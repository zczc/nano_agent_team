"""
Sliding Window History Middlewares

Two alternative strategies for managing conversation history to reduce token consumption.
Both replace ToolResultCacheMiddleware in the middleware chain.

Usage:
    # Option 1 (default): ToolResultCacheMiddleware — only compresses large tool results
    from backend.llm.middleware import ToolResultCacheMiddleware
    strategies = [..., ToolResultCacheMiddleware(), ...]

    # Option 2: Rule-based sliding window — zero extra LLM cost
    from backend.llm.history_middleware import RuleSlidingWindowMiddleware
    strategies = [..., RuleSlidingWindowMiddleware(), ...]

    # Option 3: LLM-summarized sliding window — small extra cost, better quality
    from backend.llm.history_middleware import LLMSlidingWindowMiddleware
    strategies = [..., LLMSlidingWindowMiddleware(summary_model="qwen/qwen-flash"), ...]
"""

from typing import Any, Callable, Dict, List
from backend.llm.middleware import StrategyMiddleware
from backend.llm.types import AgentSession
from backend.utils.logger import Logger

_SUMMARY_MARKER = "[Conversation history summary]"


class _SlidingWindowBase(StrategyMiddleware):
    """
    Base class for sliding window history management.

    Maintains a fixed-size window of recent messages. When history exceeds
    the window, older messages are summarized and kept as a single message
    at the front. Tool results within the window are compressed after
    the LLM has consumed them.
    """

    def __init__(self,
                 window_size: int = 15,
                 compress_after_turns: int = 1,
                 compress_threshold: int = 1500,
                 preview_head: int = 300,
                 preview_tail: int = 150,
                 max_summary_chars: int = 4000):
        """
        Args:
            window_size: Number of recent messages to keep in full.
            compress_after_turns: Compress tool results after this many assistant turns.
            compress_threshold: Only compress tool results larger than this (chars).
            preview_head: Characters to keep from start of compressed content.
            preview_tail: Characters to keep from end of compressed content.
            max_summary_chars: Maximum character length for the accumulated summary block.
        """
        self.window_size = window_size
        self.compress_after_turns = compress_after_turns
        self.compress_threshold = compress_threshold
        self.preview_head = preview_head
        self.preview_tail = preview_tail
        self.max_summary_chars = max_summary_chars

    def __call__(self, session: AgentSession, next_call: Callable[[AgentSession], Any]) -> Any:
        history = session.history

        # Step 1: Compress tool results that the LLM has already consumed
        self._compress_tool_results(history)

        # Step 2: Slide window if history exceeds limit
        #
        # After sliding, the history is always:
        #   [assistant(summary), user(pinned_task), ...window starting with assistant...]
        #
        # This guarantees valid message alternation for all LLM providers:
        #   assistant → user → assistant → tool → assistant → ...
        #
        # The user's original task message is "pinned" — never expired into the summary.
        # This follows the same pattern as Claude API compaction and OpenCode.

        existing_summary = None
        user_task = None
        actual_start = 0

        if (history
                and history[0].get("role") == "assistant"
                and isinstance(history[0].get("content"), str)
                and history[0]["content"].startswith(_SUMMARY_MARKER)):
            # Subsequent sliding: [assistant(summary), user(task), ...actual...]
            existing_summary = history[0]["content"]
            if len(history) > 1 and history[1].get("role") == "user":
                user_task = history[1]
                actual_start = 2
            else:
                actual_start = 1
        else:
            # First sliding: find and pin the user's task message
            for i, msg in enumerate(history):
                if msg.get("role") == "user":
                    user_task = msg
                    actual_start = i + 1
                    break

        actual_len = len(history) - actual_start
        if actual_len > self.window_size:
            actual_history = history[actual_start:]

            # Find a safe cut point so window starts with "assistant".
            # This single rule prevents:
            #   - Orphaned tool results (tool without preceding assistant+tool_calls)
            #   - Consecutive assistant messages (assistant summary + assistant window start)
            #   - Broken tool_call/result pairs (assistant's tool results always follow it)
            cut = len(actual_history) - self.window_size
            while cut > 0 and actual_history[cut].get("role") != "assistant":
                cut -= 1

            if cut <= 0:
                # No safe cut point found — skip sliding this round
                return next_call(session)

            expired = actual_history[:cut]
            window = actual_history[cut:]

            new_summary_text = self._generate_summary(expired, session)

            # Merge with existing summary
            if existing_summary:
                body = existing_summary[len(_SUMMARY_MARKER):].lstrip("\n")
                merged_body = body + "\n\n" + new_summary_text
            else:
                merged_body = new_summary_text

            # Enforce max length — keep the most recent part
            if len(merged_body) > self.max_summary_chars:
                merged_body = "...(earlier history truncated)...\n" + merged_body[-(self.max_summary_chars - 40):]

            summary_msg = {"role": "assistant", "content": _SUMMARY_MARKER + "\n" + merged_body}

            # Build: [assistant(summary), user(task), ...window...]
            new_history = [summary_msg]
            if user_task:
                new_history.append(user_task)
            new_history.extend(window)
            session.history = new_history

            Logger.info(f"[SlidingWindow] Slid window: expired {len(expired)} msgs, "
                        f"summary {len(merged_body)} chars, window {len(window)} msgs")

        return next_call(session)

    def _compress_tool_results(self, history: List[Dict]):
        """Compress old tool results that have been consumed by the LLM."""
        for i, msg in enumerate(history):
            if msg.get("role") != "tool":
                continue
            content = msg.get("content", "")
            if not isinstance(content, str):
                continue
            if content.startswith("[Compressed"):
                continue
            if len(content) < self.compress_threshold:
                continue
            turns_after = sum(1 for m in history[i + 1:] if m.get("role") == "assistant")
            if turns_after >= self.compress_after_turns:
                tool_name = msg.get("name", "unknown")
                preview = self._make_preview(content)
                msg["content"] = f"[Compressed: {tool_name}, {len(content)} chars]\n{preview}"

    def _make_preview(self, content: str) -> str:
        total = self.preview_head + self.preview_tail
        if len(content) <= total + 20:
            return content
        head = content[:self.preview_head]
        tail = content[-self.preview_tail:]
        omitted = len(content) - self.preview_head - self.preview_tail
        return f"{head}\n...[{omitted} chars omitted]...\n{tail}"

    def _generate_summary(self, expired_messages: List[Dict], session: AgentSession) -> str:
        """Override in subclass."""
        raise NotImplementedError


class RuleSlidingWindowMiddleware(_SlidingWindowBase):
    """
    Sliding window with rule-based summary.

    Extracts assistant message content from expired messages to build the summary.
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


class LLMSlidingWindowMiddleware(_SlidingWindowBase):
    """
    Sliding window with LLM-based summary.

    Uses a (optionally cheaper) model to generate a high-quality summary
    of expired messages. Falls back to rule-based extraction on failure.
    """

    def __init__(self, summary_model: str = None, summary_max_tokens: int = 800, **kwargs):
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

    def _generate_summary(self, expired_messages: List[Dict], session: AgentSession) -> str:
        # Build conversation text
        parts = []
        for msg in expired_messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if not content or not isinstance(content, str):
                continue
            if role == "assistant":
                parts.append(f"[Assistant] {content[:500]}")
            elif role == "user":
                parts.append(f"[User] {content[:300]}")
            elif role == "tool":
                name = msg.get("name", "tool")
                parts.append(f"[Tool:{name}] {content[:150]}")

        if not parts:
            return "(no messages in expired window)"

        conversation_text = "\n".join(parts)
        # Cap input to keep the summary call itself cheap
        if len(conversation_text) > 6000:
            conversation_text = conversation_text[:3000] + "\n...(truncated)...\n" + conversation_text[-3000:]

        try:
            client, model = self._get_summary_client(session)
            if not client or not model:
                raise RuntimeError("No LLM client available for summarization")

            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": (
                        "You are a conversation summarizer for an AI agent. "
                        "Compress the following conversation into a concise summary. "
                        "Focus on: key decisions made, important findings, current progress, "
                        "and unresolved issues. Be factual and dense. Use bullet points. Max 300 words."
                    )},
                    {"role": "user", "content": conversation_text}
                ],
                stream=False,
                max_tokens=self.summary_max_tokens
            )
            return resp.choices[0].message.content

        except Exception as e:
            Logger.warning(f"[SlidingWindow] LLM summary failed ({e}), falling back to rule-based")
            # Fallback to rule-based
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
