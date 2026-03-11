"""
Sliding Window History Middlewares

Two-phase approach (inspired by OpenCode & Claude Code compaction):
  Phase 1 — Incremental pruning: clear old tool results in-place (every turn, zero cost)
  Phase 2 — Sliding window: when history exceeds window, summarize expired messages via LLM

Usage:
    # Option 1: Rule-based sliding window — zero extra LLM cost
    from backend.llm.history_middleware import RuleSlidingWindowMiddleware
    strategies = [..., RuleSlidingWindowMiddleware(), ...]

    # Option 2: LLM-summarized sliding window — small extra cost, better quality
    from backend.llm.history_middleware import LLMSlidingWindowMiddleware
    strategies = [..., LLMSlidingWindowMiddleware(summary_model="qwen/qwen-flash"), ...]
"""

from typing import Any, Callable, Dict, List
from backend.llm.middleware import StrategyMiddleware
from backend.llm.types import AgentSession
from backend.utils.logger import Logger


_SUMMARY_MARKER = "[Conversation history summary]"
_CLEARED_MARKER = "[Cleared]"


class _SlidingWindowBase(StrategyMiddleware):
    """
    Base class for sliding window history management.

    Phase 1 — Tool result pruning (every call, zero cost):
        After the LLM has consumed a tool result (N subsequent assistant turns),
        replace the content with a short "[Cleared]" marker.
        This is the same strategy OpenCode uses ("Old tool result content cleared").

    Phase 2 — Window sliding (when history overflows):
        Summarize expired messages and pin the user's original task at the front.
        Layout after sliding: [assistant(summary), user(pinned_task), ...window...]
    """

    def __init__(self,
                 window_size: int = 40,
                 clear_after_turns: int = 2,
                 clear_threshold: int = 500,
                 max_summary_chars: int = 8000):
        """
        Args:
            window_size: Number of recent messages to keep in full.
            clear_after_turns: Clear tool results after this many subsequent assistant turns.
            clear_threshold: Only clear tool results larger than this (chars).
            max_summary_chars: Maximum character length for the accumulated summary block.
        """
        self.window_size = window_size
        self.clear_after_turns = clear_after_turns
        self.clear_threshold = clear_threshold
        self.max_summary_chars = max_summary_chars

    def __call__(self, session: AgentSession, next_call: Callable[[AgentSession], Any]) -> Any:
        history = session.history

        # Phase 1: Clear old tool results (incremental, every call)
        self._clear_old_tool_results(history)

        # Phase 2: Slide window if history exceeds limit
        #
        # After sliding, the history is always:
        #   [assistant(summary), user(pinned_task), ...window starting with assistant...]
        #
        # This guarantees valid message alternation for all LLM providers.
        # The user's original task message is "pinned" — never expired into the summary.

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

        actual_len = len(history) - actual_start
        if actual_len > self.window_size:
            actual_history = history[actual_start:]

            # Find a safe cut point so window starts with "assistant".
            cut = len(actual_history) - self.window_size
            while cut > 0 and actual_history[cut].get("role") != "assistant":
                cut -= 1

            if cut <= 0:
                return next_call(session)

            expired = actual_history[:cut]
            window = actual_history[cut:]

            new_summary_text = self._generate_summary(expired, session)

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

            Logger.info(f"[SlidingWindow] Slid window: expired {len(expired)} msgs, "
                        f"summary {len(merged_body)} chars, window {len(window)} msgs")

        return next_call(session)

    def _clear_old_tool_results(self, history: List[Dict]):
        """
        Phase 1: Clear old tool results that have been consumed by the LLM.

        Like OpenCode's pruning: replace content with "[Cleared]" marker.
        Simple and effective — no preview, no head/tail.
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
            turns_after = sum(1 for m in history[i + 1:] if m.get("role") == "assistant")
            if turns_after >= self.clear_after_turns:
                tool_name = msg.get("name", "unknown")
                msg["content"] = f"{_CLEARED_MARKER} {tool_name} result ({len(content)} chars)"

    def _generate_summary(self, expired_messages: List[Dict], session: AgentSession) -> str:
        """Override in subclass."""
        raise NotImplementedError


class RuleSlidingWindowMiddleware(_SlidingWindowBase):
    """
    Sliding window with rule-based summary.
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


# --- Structured summary prompt (inspired by OpenCode's compaction.ts) ---

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
- Preserve agent names, task IDs, branch names, and other identifiers exactly.\
"""


class LLMSlidingWindowMiddleware(_SlidingWindowBase):
    """
    Sliding window with LLM-based structured summary.

    Uses a (optionally separate) model to generate a high-quality structured summary
    of expired messages. Falls back to rule-based extraction on failure.

    The summary prompt follows OpenCode's compaction template:
    Goal -> Decisions/Discoveries -> Accomplished -> In Progress -> Relevant Files
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
        """Build conversation text for the summarizer, preserving more context."""
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
                        parts.append(f"[Assistant] {content[:800]}\n  -> Tool calls: {tc_text}")
                    else:
                        parts.append(f"[Assistant] -> Tool calls: {tc_text}")
                elif content and isinstance(content, str):
                    parts.append(f"[Assistant] {content[:800]}")

            elif role == "user":
                if content and isinstance(content, str):
                    parts.append(f"[User] {content[:500]}")

            elif role == "tool":
                name = msg.get("name", "tool")
                if content and isinstance(content, str):
                    if content.startswith(_CLEARED_MARKER):
                        parts.append(f"[Tool:{name}] {content}")
                    else:
                        parts.append(f"[Tool:{name}] {content[:400]}")

        return "\n".join(parts)

    def _generate_summary(self, expired_messages: List[Dict], session: AgentSession) -> str:
        conversation_text = self._build_conversation_text(expired_messages)

        if not conversation_text.strip():
            return "(no messages in expired window)"

        # Cap input to keep the summary call cheap but informative
        if len(conversation_text) > 12000:
            conversation_text = conversation_text[:6000] + "\n...(truncated)...\n" + conversation_text[-6000:]

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
            Logger.warning(f"[SlidingWindow] LLM summary failed ({e}), falling back to rule-based")
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
