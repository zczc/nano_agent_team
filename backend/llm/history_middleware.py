"""
Sliding Window History Middlewares

Two-phase approach (inspired by OpenCode & Claude Code compaction):
  Phase 1 — Incremental pruning: clear old tool results in-place (every turn, zero cost)
  Phase 2 — Batch compaction: when history exceeds threshold, summarize all expired
            messages in one batch and keep only the most recent ones.

Unlike a naive sliding window that fires every turn after overflow, this implementation
fires infrequently (~every 25-30 turns) and produces a single high-quality summary.

Usage:
    # Option 1: Rule-based — zero extra LLM cost
    from backend.llm.history_middleware import RuleSlidingWindowMiddleware
    strategies = [..., RuleSlidingWindowMiddleware(), ...]

    # Option 2: LLM-summarized — small extra cost, better quality
    from backend.llm.history_middleware import LLMSlidingWindowMiddleware
    strategies = [..., LLMSlidingWindowMiddleware(), ...]
"""

from typing import Any, Callable, Dict, List, Optional, Tuple
from backend.llm.middleware import StrategyMiddleware
from backend.llm.types import AgentSession
from backend.utils.logger import Logger


_SUMMARY_MARKER = "[Conversation history summary]"
_CLEARED_MARKER = "[Cleared]"

# Tool names whose results are exempt from Phase 1 clearing.
# Skill SOPs need to stay visible across many turns.
_CLEAR_EXEMPT_TOOLS = frozenset({"activate_skill"})


class _SlidingWindowBase(StrategyMiddleware):
    """
    Base class for sliding window history management (batch compaction strategy).

    Phase 1 — Tool result pruning (every call, zero cost):
        After the LLM has consumed a tool result (N subsequent assistant turns),
        replace the content with a short "[Cleared]" marker.
        activate_skill results are exempt (skill SOPs need multi-turn visibility).

    Phase 2 — Batch compaction (only when threshold reached):
        When message count or estimated token usage exceeds the threshold,
        summarize ALL expired messages in one batch and keep only the most recent ones.
        Layout after compaction: [assistant(summary), user(pinned_task), ...recent...]
    """

    def __init__(self,
                 # Phase 1: incremental pruning
                 clear_after_turns: int = 2,
                 clear_threshold: int = 500,
                 # Phase 2: batch compaction
                 max_messages: int = 80,
                 keep_recent: int = 20,
                 max_token_ratio: float = 0.7,
                 context_window: int = 128000,
                 chars_per_token: float = 3.5,
                 max_summary_chars: int = 8000):
        """
        Args:
            clear_after_turns: Clear tool results after this many subsequent assistant turns.
            clear_threshold: Only clear tool results larger than this (chars).
            max_messages: Trigger compaction when actual message count exceeds this.
            keep_recent: Number of recent messages to keep after compaction.
            max_token_ratio: Trigger compaction when estimated tokens exceed this
                             fraction of context_window.
            context_window: Model's context window size in tokens.
            chars_per_token: Characters-per-token estimation factor.
            max_summary_chars: Maximum character length for the accumulated summary block.
        """
        self.clear_after_turns = clear_after_turns
        self.clear_threshold = clear_threshold
        self.max_messages = max_messages
        self.keep_recent = keep_recent
        self.max_token_ratio = max_token_ratio
        self.context_window = context_window
        self.chars_per_token = chars_per_token
        self.max_summary_chars = max_summary_chars

    def __call__(self, session: AgentSession, next_call: Callable[[AgentSession], Any]) -> Any:
        history = session.history

        # Phase 1: Clear old tool results (incremental, every call)
        self._clear_old_tool_results(history)

        # Phase 2: Batch compaction (only when threshold reached)
        if self._should_compact(history):
            self._do_compaction(session)

        return next_call(session)

    # ── Phase 1 ──────────────────────────────────────────────────────────

    def _clear_old_tool_results(self, history: List[Dict]):
        """
        Phase 1: Clear old tool results that have been consumed by the LLM.

        Like OpenCode's pruning: replace content with "[Cleared]" marker.
        activate_skill results are exempt — skill SOPs need to stay visible.
        """
        for i, msg in enumerate(history):
            if msg.get("role") != "tool":
                continue
            content = msg.get("content", "")
            if not isinstance(content, str):
                continue
            if content.startswith(_CLEARED_MARKER):
                continue
            if len(content) < self.clear_threshold:
                continue
            tool_name = msg.get("name", "unknown")
            if tool_name in _CLEAR_EXEMPT_TOOLS:
                continue
            turns_after = sum(1 for m in history[i + 1:] if m.get("role") == "assistant")
            if turns_after >= self.clear_after_turns:
                msg["content"] = f"{_CLEARED_MARKER} {tool_name} result ({len(content)} chars)"

    # ── Phase 2 ──────────────────────────────────────────────────────────

    def _parse_history_structure(self, history: List[Dict]) -> Tuple[int, Optional[str], Optional[Dict]]:
        """
        Parse history to find: actual_start index, existing_summary, pinned user_task.

        Returns:
            (actual_start, existing_summary_content_or_None, user_task_msg_or_None)
        """
        existing_summary = None
        user_task = None
        actual_start = 0

        if (history
                and history[0].get("role") == "assistant"
                and isinstance(history[0].get("content"), str)
                and history[0]["content"].startswith(_SUMMARY_MARKER)):
            existing_summary = history[0]["content"]
            if len(history) > 1 and history[1].get("role") == "user":
                user_task = history[1]
                actual_start = 2
            else:
                actual_start = 1
        else:
            for i, msg in enumerate(history):
                if msg.get("role") == "user":
                    user_task = msg
                    actual_start = i + 1
                    break

        return actual_start, existing_summary, user_task

    def _estimate_tokens(self, history: List[Dict]) -> float:
        """Estimate total token count from character lengths (no tokenizer needed)."""
        total_chars = 0
        for msg in history:
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            for tc in msg.get("tool_calls", []):
                total_chars += len(tc.get("function", {}).get("arguments", ""))
        return total_chars / self.chars_per_token

    def _should_compact(self, history: List[Dict]) -> bool:
        """Check if compaction should be triggered."""
        actual_start, _, _ = self._parse_history_structure(history)
        actual_len = len(history) - actual_start

        # Condition 1: message count exceeds threshold
        if actual_len > self.max_messages:
            return True

        # Condition 2: estimated token usage exceeds ratio
        if self.max_token_ratio < 1.0:
            estimated_tokens = self._estimate_tokens(history)
            if estimated_tokens > self.context_window * self.max_token_ratio:
                return True

        return False

    def _do_compaction(self, session: AgentSession):
        """Execute batch compaction: summarize expired messages, keep recent ones."""
        history = session.history
        actual_start, existing_summary, user_task = self._parse_history_structure(history)
        actual_history = history[actual_start:]

        # Find a safe cut point so the kept window starts with "assistant"
        cut = len(actual_history) - self.keep_recent
        while cut > 0 and actual_history[cut].get("role") != "assistant":
            cut -= 1

        if cut <= 0:
            return  # Can't cut safely

        expired = actual_history[:cut]
        window = actual_history[cut:]

        new_summary_text = self._generate_summary(expired, session)

        # Merge with existing summary
        if existing_summary:
            body = existing_summary[len(_SUMMARY_MARKER):].lstrip("\n")
            merged_body = body + "\n\n" + new_summary_text
        else:
            merged_body = new_summary_text

        if len(merged_body) > self.max_summary_chars:
            merged_body = "...(earlier history truncated)...\n" + merged_body[-(self.max_summary_chars - 40):]

        summary_msg = {"role": "assistant", "content": _SUMMARY_MARKER + "\n" + merged_body}

        new_history = [summary_msg]
        if user_task:
            new_history.append(user_task)
        new_history.extend(window)
        session.history = new_history

        Logger.info(f"[BatchCompaction] Compacted: expired {len(expired)} msgs, "
                    f"summary {len(merged_body)} chars, kept {len(window)} msgs")

    def _generate_summary(self, expired_messages: List[Dict], session: AgentSession) -> str:
        """Override in subclass."""
        raise NotImplementedError


# ── Rule-based ────────────────────────────────────────────────────────────

class RuleSlidingWindowMiddleware(_SlidingWindowBase):
    """
    Sliding window with rule-based summary (batch compaction strategy).
    Extracts assistant message content from expired messages.
    Zero additional LLM cost.
    """

    def _generate_summary(self, expired_messages: List[Dict], session: AgentSession) -> str:
        lines = []
        for msg in expired_messages:
            if msg.get("role") == "assistant":
                content = msg.get("content")
                if content and isinstance(content, str) and content.strip():
                    text = content.strip()
                    if len(text) > 200:
                        text = text[:200] + "..."
                    lines.append(f"- {text}")
        if not lines:
            return "(no assistant messages in expired window)"
        return "\n".join(lines)


# ── LLM-based ─────────────────────────────────────────────────────────────

_SUMMARY_SYSTEM_PROMPT = """\
You are a conversation summarizer for an AI agent.
Summarize the expired conversation history so that a continuing agent can pick up seamlessly.

Use this template:

## Goal
[What is the agent trying to accomplish?]

## Key Decisions & Discoveries
[Important findings, decisions made, constraints learned — bullet points]

## Accomplished
[What work has been completed so far — bullet points]

## In Progress / Next Steps
[What was the agent working on when this history was cut? What remains?]

## Relevant Files
[List files that were read, created, or modified — paths only, one per line]

Rules:
- Be factual and dense. No filler.
- Max 400 words.
- If tool results were cleared, note what the tool was called for, not the cleared content.
- Preserve agent names, task IDs, branch names, and other identifiers exactly.
- If any skill was activated (via activate_skill), note the skill name and the agent's \
current phase/step within that skill's workflow.\
"""


class LLMSlidingWindowMiddleware(_SlidingWindowBase):
    """
    Sliding window with LLM-based structured summary (batch compaction strategy).

    Uses a (optionally separate) model to generate a high-quality structured summary
    of expired messages. Falls back to rule-based extraction on failure.

    Compaction fires infrequently (every ~25-30 turns) and summarizes a large batch
    in one LLM call, producing much better context than per-turn sliding.
    """

    def __init__(self, summary_model: str = None, summary_max_tokens: int = 1200, **kwargs):
        """
        Args:
            summary_model: Model key for summarization (e.g. "qwen/qwen-flash").
                           If None, uses the agent's own model.
            summary_max_tokens: Max tokens for the summary response.
            **kwargs: Passed to _SlidingWindowBase.
        """
        super().__init__(**kwargs)
        self.summary_model = summary_model
        self.summary_max_tokens = summary_max_tokens
        self._summary_client = None
        self._summary_model_name = None

    def _get_summary_client(self, session: AgentSession):
        """Get or create the LLM client for summarization."""
        if self.summary_model:
            if not self._summary_client:
                from backend.llm.providers import LLMFactory
                self._summary_client = LLMFactory.create_client(self.summary_model)
                self._summary_model_name = LLMFactory.get_model_name(self.summary_model)
            return self._summary_client, self._summary_model_name
        return session.metadata.get("llm_client"), session.metadata.get("llm_model")

    def _build_conversation_text(self, expired_messages: List[Dict]) -> str:
        """Build conversation text for the summarizer."""
        parts = []
        for msg in expired_messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "assistant":
                if msg.get("tool_calls"):
                    tc_summaries = []
                    for tc in msg["tool_calls"]:
                        fn = tc.get("function", {})
                        name = fn.get("name", "?")
                        args = fn.get("arguments", "")
                        if len(args) > 200:
                            args = args[:200] + "..."
                        tc_summaries.append(f"{name}({args})")
                    tc_text = "; ".join(tc_summaries)
                    if content and isinstance(content, str) and content.strip():
                        parts.append(f"[Assistant] {content[:300]}\n  -> Tool calls: {tc_text}")
                    else:
                        parts.append(f"[Assistant] -> Tool calls: {tc_text}")
                elif content and isinstance(content, str):
                    parts.append(f"[Assistant] {content[:300]}")

            elif role == "user":
                if content and isinstance(content, str):
                    parts.append(f"[User] {content[:300]}")

            elif role == "tool":
                name = msg.get("name", "tool")
                if content and isinstance(content, str):
                    if content.startswith(_CLEARED_MARKER):
                        parts.append(f"[Tool:{name}] {content}")
                    elif name in _CLEAR_EXEMPT_TOOLS:
                        # Skill SOPs: give summarizer more context
                        parts.append(f"[Tool:{name}] {content[:2000]}")
                    else:
                        parts.append(f"[Tool:{name}] {content[:300]}")

        text = "\n".join(parts)
        # Batch compaction handles 60+ messages — allow more room
        if len(text) > 20000:
            text = text[:10000] + "\n...(truncated)...\n" + text[-10000:]
        return text

    def _generate_summary(self, expired_messages: List[Dict], session: AgentSession) -> str:
        conversation_text = self._build_conversation_text(expired_messages)

        if not conversation_text.strip():
            return "(no messages in expired window)"

        try:
            client, model = self._get_summary_client(session)
            if not client or not model:
                raise RuntimeError("No LLM client available for summarization")

            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": conversation_text}
                ],
                stream=False,
                max_tokens=self.summary_max_tokens
            )
            return resp.choices[0].message.content

        except Exception as e:
            Logger.warning(f"[BatchCompaction] LLM summary failed ({e}), falling back to rule-based")
            lines = []
            for msg in expired_messages:
                if msg.get("role") == "assistant":
                    content = msg.get("content")
                    if content and isinstance(content, str) and content.strip():
                        text = content.strip()
                        if len(text) > 200:
                            text = text[:200] + "..."
                        lines.append(f"- {text}")
            return "\n".join(lines) if lines else "(summary generation failed)"
