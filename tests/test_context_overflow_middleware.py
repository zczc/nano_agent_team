"""
Tests for ContextOverflowMiddleware.

Covers:
1. Error detection — _is_context_length_error recognizes various provider error formats
2. LLM summarization path — compress history via LLM and retry
3. Intelligent truncation fallback — when no client/model or summarization fails
4. Edge cases — history too short, non-context errors pass through, retry exhaustion
"""

import json
import sys
import os
from unittest import mock
from dataclasses import dataclass, field
from typing import List, Dict, Any

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.llm.middleware import ContextOverflowMiddleware
from backend.llm.types import AgentSession, SystemPromptConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(history: List[Dict[str, Any]], metadata: Dict[str, Any] = None) -> AgentSession:
    """Create a minimal AgentSession for testing."""
    return AgentSession(
        history=history,
        depth=1,
        system_config=SystemPromptConfig(),
        tools=[],
        metadata=metadata or {},
    )


def _make_long_history(n: int = 20) -> List[Dict[str, Any]]:
    """Generate a conversation history with n message pairs."""
    history = []
    for i in range(n):
        history.append({"role": "user", "content": f"User message {i} " + "x" * 200})
        history.append({"role": "assistant", "content": f"Assistant reply {i} " + "y" * 200})
    return history


def _make_mock_client(summary_text: str = "This is a summary."):
    """Create a mock OpenAI-compatible client that returns summary_text."""
    client = mock.MagicMock()
    mock_message = mock.MagicMock()
    mock_message.content = summary_text
    mock_choice = mock.MagicMock()
    mock_choice.message = mock_message
    mock_response = mock.MagicMock()
    mock_response.choices = [mock_choice]
    client.chat.completions.create.return_value = mock_response
    return client


# ============================================================================
# 1. Error detection tests
# ============================================================================

class TestIsContextLengthError:
    """_is_context_length_error should match various provider error messages."""

    @pytest.fixture
    def mw(self):
        return ContextOverflowMiddleware()

    @pytest.mark.parametrize("msg", [
        "This model's maximum context length is 128000 tokens.",
        "Error code: context_length_exceeded",
        "Request too large for model",
        "content_too_large: prompt is too long",
        "Too many tokens in the request",
        "token limit exceeded",
        "context window exceeded",
        "max_tokens exceeded",
        "The input is too long for this model",
    ])
    def test_detects_context_errors(self, mw, msg):
        assert mw._is_context_length_error(Exception(msg)) is True

    @pytest.mark.parametrize("msg", [
        "Connection refused",
        "Internal server error",
        "Rate limit exceeded",
        "Invalid API key",
        "Model not found",
    ])
    def test_ignores_non_context_errors(self, mw, msg):
        assert mw._is_context_length_error(Exception(msg)) is False


# ============================================================================
# 2. LLM summarization path
# ============================================================================

class TestLLMSummarizationPath:
    """When client/model are available, middleware should summarize via LLM and retry."""

    def test_summarize_and_retry_success(self):
        """After context error, compress history via LLM, then retry succeeds."""
        mw = ContextOverflowMiddleware(max_retries=2, keep_last_n=2)
        history = _make_long_history(10)  # 20 messages
        mock_client = _make_mock_client("Compressed summary of conversation.")

        session = _make_session(history, metadata={
            "llm_client": mock_client,
            "llm_model": "gpt-4",
        })

        call_count = {"n": 0}

        def next_call(s):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Exception("This model's maximum context length is 128000 tokens.")
            return "success"

        result = mw(session, next_call)

        assert result == "success"
        assert call_count["n"] == 2

        # History should be: [summary_message] + last 2 original messages
        assert len(session.history) == 3
        assert "[Context Summary]" in session.history[0]["content"]
        assert "Compressed summary of conversation." in session.history[0]["content"]
        assert session.history[0]["role"] == "user"

        # LLM was called for summarization
        mock_client.chat.completions.create.assert_called_once()
        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs.kwargs["stream"] is False

    def test_summary_preserves_last_n_messages(self):
        """The last keep_last_n messages should be preserved exactly."""
        mw = ContextOverflowMiddleware(keep_last_n=2)
        history = _make_long_history(5)
        last_two = [dict(m) for m in history[-2:]]  # deep copy for comparison
        mock_client = _make_mock_client("Summary.")

        session = _make_session(history, metadata={
            "llm_client": mock_client,
            "llm_model": "gpt-4",
        })

        call_count = {"n": 0}
        def next_call(s):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Exception("context_length_exceeded")
            return "ok"

        mw(session, next_call)

        # Last 2 messages should be identical to originals
        assert session.history[-2:] == last_two

    def test_double_compression_on_persistent_error(self):
        """If first compression isn't enough, middleware should compress again."""
        mw = ContextOverflowMiddleware(max_retries=2, keep_last_n=2)
        history = _make_long_history(10)
        mock_client = _make_mock_client("Even shorter summary.")

        session = _make_session(history, metadata={
            "llm_client": mock_client,
            "llm_model": "gpt-4",
        })

        call_count = {"n": 0}
        def next_call(s):
            call_count["n"] += 1
            if call_count["n"] <= 2:
                raise Exception("context_length_exceeded")
            return "finally"

        result = mw(session, next_call)
        assert result == "finally"
        assert call_count["n"] == 3
        # LLM summarize called twice
        assert mock_client.chat.completions.create.call_count == 2


# ============================================================================
# 3. Intelligent truncation fallback
# ============================================================================

class TestIntelligentTruncationFallback:
    """When LLM summarization is unavailable or fails, fall back to truncation."""

    def test_fallback_when_no_client(self):
        """Without client/model in metadata, use intelligent truncation."""
        mw = ContextOverflowMiddleware(max_retries=1, keep_last_n=2)
        history = _make_long_history(10)
        original_len = len(history)

        session = _make_session(history, metadata={})  # No client/model

        call_count = {"n": 0}
        def next_call(s):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Exception("context_length_exceeded")
            return "ok"

        result = mw(session, next_call)
        assert result == "ok"
        # History should be shorter than original
        assert len(session.history) < original_len

    def test_fallback_when_summarization_fails(self):
        """If LLM summarization raises, fall back to truncation."""
        mw = ContextOverflowMiddleware(max_retries=1, keep_last_n=2)
        history = _make_long_history(10)
        original_len = len(history)

        mock_client = mock.MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("LLM is down")

        session = _make_session(history, metadata={
            "llm_client": mock_client,
            "llm_model": "gpt-4",
        })

        call_count = {"n": 0}
        def next_call(s):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Exception("context_length_exceeded")
            return "recovered"

        result = mw(session, next_call)
        assert result == "recovered"
        assert len(session.history) < original_len

    def test_intelligent_truncate_keeps_recent_messages(self):
        """_intelligent_truncate should keep the most recent messages."""
        mw = ContextOverflowMiddleware()
        history = _make_long_history(20)  # 40 messages, each ~200+ chars

        truncated = mw._intelligent_truncate(history)

        # Should be significantly shorter
        assert len(truncated) < len(history)
        # Last message of truncated should be last message of original
        assert truncated[-1] == history[-1]

    def test_intelligent_truncate_minimum_length(self):
        """Even with very short messages, truncation should keep at least some."""
        mw = ContextOverflowMiddleware()
        history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]

        truncated = mw._intelligent_truncate(history)
        assert len(truncated) >= 1


# ============================================================================
# 4. Edge cases
# ============================================================================

class TestEdgeCases:
    """Edge cases and error propagation."""

    def test_non_context_error_passes_through(self):
        """Non-context-length errors should be re-raised immediately."""
        mw = ContextOverflowMiddleware()
        session = _make_session([{"role": "user", "content": "hi"}])

        def next_call(s):
            raise ConnectionError("Network unreachable")

        with pytest.raises(ConnectionError, match="Network unreachable"):
            mw(session, next_call)

    def test_history_too_short_to_compress(self):
        """If history <= keep_last_n, should raise without attempting compression."""
        mw = ContextOverflowMiddleware(keep_last_n=2)
        session = _make_session([
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ])

        def next_call(s):
            raise Exception("context_length_exceeded")

        with pytest.raises(Exception, match="context_length_exceeded"):
            mw(session, next_call)

    def test_retry_exhaustion_raises(self):
        """After max_retries, the original error should be raised."""
        mw = ContextOverflowMiddleware(max_retries=1, keep_last_n=2)
        history = _make_long_history(10)
        mock_client = _make_mock_client("Summary.")

        session = _make_session(history, metadata={
            "llm_client": mock_client,
            "llm_model": "gpt-4",
        })

        def next_call(s):
            raise Exception("context_length_exceeded")

        with pytest.raises(Exception, match="context_length_exceeded"):
            mw(session, next_call)

    def test_no_error_passes_through(self):
        """When no error occurs, middleware should be transparent."""
        mw = ContextOverflowMiddleware()
        session = _make_session([{"role": "user", "content": "hi"}])

        def next_call(s):
            return "normal response"

        result = mw(session, next_call)
        assert result == "normal response"

    def test_history_not_mutated_on_success(self):
        """On successful call, history should remain unchanged."""
        mw = ContextOverflowMiddleware()
        history = [{"role": "user", "content": "hi"}]
        session = _make_session(history)

        def next_call(s):
            return "ok"

        mw(session, next_call)
        assert session.history == [{"role": "user", "content": "hi"}]


# ============================================================================
# 5. Build summary prompt
# ============================================================================

class TestBuildSummaryPrompt:
    """Verify the summary prompt construction."""

    def test_prompt_contains_history(self):
        mw = ContextOverflowMiddleware()
        history = [
            {"role": "user", "content": "Find all Python files"},
            {"role": "assistant", "content": "I'll search for .py files"},
        ]

        prompt = mw._build_summary_prompt(history)

        assert "Find all Python files" in prompt
        assert "search for .py files" in prompt
        assert "summary" in prompt.lower()

    def test_prompt_includes_max_tokens(self):
        mw = ContextOverflowMiddleware(summary_max_tokens=3000)
        prompt = mw._build_summary_prompt([{"role": "user", "content": "test"}])
        assert "3000" in prompt


# ============================================================================
# 6. Stream retry in engine
# ============================================================================

class TestStreamRetryInEngine:
    """Verify that streaming-phase errors (e.g. read timeout) are retried
    inside AgentEngine.run() before bubbling up as fatal errors."""

    @staticmethod
    def _make_chunk(content=None, tool_calls=None):
        """Build a minimal mock chunk matching OpenAI streaming format."""
        delta = mock.MagicMock()
        delta.content = content
        delta.tool_calls = tool_calls
        choice = mock.MagicMock()
        choice.delta = delta
        chunk = mock.MagicMock()
        chunk.choices = [choice]
        return chunk

    def _build_engine(self):
        """Create a minimal AgentEngine with mocked internals."""
        from backend.llm.engine import AgentEngine
        from backend.llm.types import SystemPromptConfig

        engine = AgentEngine.__new__(AgentEngine)
        engine.tools = []
        engine.agent_registry = None
        engine.tool_registry = None
        engine.strategies = []
        engine.depth = 0
        engine.parallel_tools = False
        engine.max_parallel_workers = 5
        engine.tool_timeout = 300
        engine.skill_registry = None
        engine.tool_context = {}
        engine.client = mock.MagicMock()
        engine.model = "test-model"
        engine.provider_key = "test/test-model"
        return engine

    def test_stream_retry_succeeds_after_transient_error(self):
        """First stream attempt raises read-timeout; second succeeds."""
        from backend.llm.engine import AgentEngine
        from backend.llm.types import SystemPromptConfig
        from backend.llm.events import AgentEvent

        engine = self._build_engine()

        call_count = {"n": 0}

        def fake_pipeline(session):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Simulate: yield one chunk then raise mid-stream
                def failing_gen():
                    yield self._make_chunk(content="partial ")
                    raise Exception("The read operation timed out")
                return failing_gen()
            else:
                def ok_gen():
                    yield self._make_chunk(content="Hello ")
                    yield self._make_chunk(content="world")
                return ok_gen()

        engine._get_llm_pipeline = lambda: fake_pipeline

        events = list(engine.run(
            messages=[{"role": "user", "content": "hi"}],
            system_config=SystemPromptConfig(),
            max_iterations=1,
        ))

        # Pipeline was called twice (1 failure + 1 success)
        assert call_count["n"] == 2

        token_events = [e for e in events if e.type == "token"]
        combined = "".join(e.data["delta"] for e in token_events)
        # Only the successful attempt's content should appear after retry
        # (partial tokens from attempt 1 may have been yielded before error,
        #  but attempt 2 content must be present)
        assert "Hello " in combined
        assert "world" in combined

    def test_stream_retry_exhausted_raises(self):
        """All 3 attempts fail — error propagates after retries exhausted."""
        from backend.llm.engine import AgentEngine
        from backend.llm.types import SystemPromptConfig
        from backend.llm.events import AgentEvent

        engine = self._build_engine()

        call_count = {"n": 0}

        def always_fail_pipeline(session):
            call_count["n"] += 1
            def failing_gen():
                raise Exception("The read operation timed out")
                yield  # make it a generator
            return failing_gen()

        engine._get_llm_pipeline = lambda: always_fail_pipeline

        with pytest.raises(Exception, match="timed out"):
            list(engine.run(
                messages=[{"role": "user", "content": "hi"}],
                system_config=SystemPromptConfig(),
                max_iterations=1,
            ))

        # All 3 attempts were made before giving up
        assert call_count["n"] == 3

    def test_stream_no_error_no_retry(self):
        """When stream succeeds on first try, no retry happens."""
        from backend.llm.engine import AgentEngine
        from backend.llm.types import SystemPromptConfig

        engine = self._build_engine()

        call_count = {"n": 0}

        def fake_pipeline(session):
            call_count["n"] += 1
            def ok_gen():
                yield self._make_chunk(content="All good")
            return ok_gen()

        engine._get_llm_pipeline = lambda: fake_pipeline

        events = list(engine.run(
            messages=[{"role": "user", "content": "hi"}],
            system_config=SystemPromptConfig(),
            max_iterations=1,
        ))

        assert call_count["n"] == 1
        token_events = [e for e in events if e.type == "token"]
        assert any("All good" in e.data["delta"] for e in token_events)
