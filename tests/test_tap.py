"""
Tests for TAP (TUI-Agent Protocol) module.

Covers:
1. protocol.py — serialization, event/message types, ID generation
2. agent_process.py — StdinDispatcher dispatch, abort, timeout
3. client.py — TapClient send/receive, abort flow
4. Modified files — audit_guard callback, local.py EOFError, ask_user fallback
"""

import io
import json
import sys
import os
import time
import threading
import queue
from unittest import mock
from dataclasses import dataclass

import pytest

# Ensure project root is on path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ============================================================================
# 1. protocol.py tests
# ============================================================================

class TestProtocol:
    """Tests for TAP protocol serialization and types."""

    def test_emit_event_writes_json_line(self):
        """emit_event should write a single JSON line ending with \\n."""
        from src.core.tap.protocol import emit_event

        buf = io.StringIO()
        emit_event({"type": "token", "delta": "hello"}, file=buf)
        output = buf.getvalue()

        assert output.endswith("\n")
        parsed = json.loads(output.strip())
        assert parsed["type"] == "token"
        assert parsed["delta"] == "hello"

    def test_emit_event_handles_unicode(self):
        """emit_event should handle Chinese characters without escaping."""
        from src.core.tap.protocol import emit_event

        buf = io.StringIO()
        emit_event({"type": "token", "delta": "你好世界"}, file=buf)
        output = buf.getvalue()

        assert "你好世界" in output
        parsed = json.loads(output.strip())
        assert parsed["delta"] == "你好世界"

    def test_emit_event_escapes_newlines_in_data(self):
        """Newlines in data fields should be JSON-escaped, not raw."""
        from src.core.tap.protocol import emit_event

        buf = io.StringIO()
        emit_event({"type": "tool_result", "output": "line1\nline2\nline3"}, file=buf)
        raw = buf.getvalue()

        # Should be exactly one line (the JSON line)
        lines = raw.strip().split("\n")
        assert len(lines) == 1

        parsed = json.loads(lines[0])
        assert parsed["output"] == "line1\nline2\nline3"

    def test_parse_control_message(self):
        """parse_control_message should parse a JSON line."""
        from src.core.tap.protocol import parse_control_message

        line = '{"type": "user_message", "text": "hello"}\n'
        msg = parse_control_message(line)
        assert msg["type"] == "user_message"
        assert msg["text"] == "hello"

    def test_parse_control_message_strips_whitespace(self):
        from src.core.tap.protocol import parse_control_message

        line = '  {"type": "abort"}  \n'
        msg = parse_control_message(line)
        assert msg["type"] == "abort"

    def test_make_confirm_request_has_unique_ids(self):
        """Each confirm_request should get a unique ID."""
        from src.core.tap.protocol import make_confirm_request

        r1 = make_confirm_request("Allow?")
        r2 = make_confirm_request("Allow?")
        assert r1["id"] != r2["id"]
        assert r1["type"] == "confirm_request"
        assert r1["kind"] == "confirmation"
        assert r1["message"] == "Allow?"

    def test_make_input_request_has_unique_ids(self):
        from src.core.tap.protocol import make_input_request

        r1 = make_input_request("What framework?")
        r2 = make_input_request("What framework?")
        assert r1["id"] != r2["id"]
        assert r1["type"] == "input_request"
        assert r1["question"] == "What framework?"

    def test_tap_event_to_dict(self):
        """TapEvent.to_dict() should flatten type into data."""
        from src.core.tap.protocol import TapEvent

        ev = TapEvent(type="token", data={"delta": "hi"})
        d = ev.to_dict()
        assert d == {"type": "token", "delta": "hi"}

    def test_tap_event_emit(self):
        from src.core.tap.protocol import TapEvent

        buf = io.StringIO()
        ev = TapEvent(type="finish", data={"reason": "end_turn"})
        ev.emit(file=buf)

        parsed = json.loads(buf.getvalue().strip())
        assert parsed == {"type": "finish", "reason": "end_turn"}

    def test_tap_control_message_from_dict(self):
        from src.core.tap.protocol import TapControlMessage

        d = {"type": "confirm_response", "id": "c-1", "approved": True}
        msg = TapControlMessage.from_dict(d)
        assert msg.type == "confirm_response"
        assert msg.id == "c-1"
        assert msg.approved is True
        assert msg.reason is None

    def test_tap_control_message_with_reason(self):
        from src.core.tap.protocol import TapControlMessage

        d = {"type": "confirm_response", "id": "c-2", "approved": False, "reason": "太危险了"}
        msg = TapControlMessage.from_dict(d)
        assert msg.approved is False
        assert msg.reason == "太危险了"

    def test_tap_control_message_user_message(self):
        from src.core.tap.protocol import TapControlMessage

        d = {"type": "user_message", "text": "帮我重构"}
        msg = TapControlMessage.from_dict(d)
        assert msg.text == "帮我重构"


# ============================================================================
# 2. StdinDispatcher tests
# ============================================================================

class TestStdinDispatcher:
    """Tests for the background stdin reader and message dispatch."""

    def _make_dispatcher_with_fake_stdin(self, lines: list[str]):
        """Create a StdinDispatcher with a fake stdin feeding given lines."""
        from src.core.tap.agent_process import StdinDispatcher

        dispatcher = StdinDispatcher()
        fake_stdin = io.StringIO("\n".join(lines) + "\n")
        return dispatcher, fake_stdin

    def test_user_message_dispatched_to_queue(self):
        from src.core.tap.agent_process import StdinDispatcher

        dispatcher = StdinDispatcher()
        fake_stdin = io.StringIO(
            json.dumps({"type": "user_message", "text": "hello"}) + "\n"
        )

        # Monkey-patch sys.stdin for the reader thread
        with mock.patch.object(sys, "stdin", fake_stdin):
            dispatcher._reader_loop()

        msg = dispatcher.message_queue.get(timeout=1)
        assert msg["type"] == "user_message"
        assert msg["text"] == "hello"

    def test_confirm_response_dispatched_to_pending(self):
        from src.core.tap.agent_process import StdinDispatcher

        dispatcher = StdinDispatcher()

        # Pre-register a pending request
        q = queue.Queue()
        with dispatcher._lock:
            dispatcher._pending["c-99"] = q

        fake_stdin = io.StringIO(
            json.dumps({"type": "confirm_response", "id": "c-99", "approved": True}) + "\n"
        )

        with mock.patch.object(sys, "stdin", fake_stdin):
            dispatcher._reader_loop()

        result = q.get(timeout=1)
        assert result["approved"] is True

    def test_input_response_dispatched_to_pending(self):
        from src.core.tap.agent_process import StdinDispatcher

        dispatcher = StdinDispatcher()

        q = queue.Queue()
        with dispatcher._lock:
            dispatcher._pending["i-42"] = q

        fake_stdin = io.StringIO(
            json.dumps({"type": "input_response", "id": "i-42", "text": "pytest"}) + "\n"
        )

        with mock.patch.object(sys, "stdin", fake_stdin):
            dispatcher._reader_loop()

        result = q.get(timeout=1)
        assert result["text"] == "pytest"

    def test_abort_sets_event_and_wakes_pending(self):
        from src.core.tap.agent_process import StdinDispatcher

        dispatcher = StdinDispatcher()

        # Register a pending callback
        q = queue.Queue()
        with dispatcher._lock:
            dispatcher._pending["c-100"] = q

        fake_stdin = io.StringIO(
            json.dumps({"type": "abort"}) + "\n"
        )

        with mock.patch.object(sys, "stdin", fake_stdin):
            dispatcher._reader_loop()

        assert dispatcher.abort_event.is_set()
        # Pending queue should have received None (wake-up sentinel)
        result = q.get(timeout=1)
        assert result is None

    def test_invalid_json_lines_are_skipped(self):
        from src.core.tap.agent_process import StdinDispatcher

        dispatcher = StdinDispatcher()

        fake_stdin = io.StringIO(
            "not json\n"
            + json.dumps({"type": "user_message", "text": "valid"}) + "\n"
            + "also {broken\n"
        )

        with mock.patch.object(sys, "stdin", fake_stdin):
            dispatcher._reader_loop()

        msg = dispatcher.message_queue.get(timeout=1)
        assert msg["text"] == "valid"
        assert dispatcher.message_queue.empty()

    def test_empty_lines_are_skipped(self):
        from src.core.tap.agent_process import StdinDispatcher

        dispatcher = StdinDispatcher()

        fake_stdin = io.StringIO(
            "\n\n"
            + json.dumps({"type": "user_message", "text": "ok"}) + "\n"
            + "\n"
        )

        with mock.patch.object(sys, "stdin", fake_stdin):
            dispatcher._reader_loop()

        msg = dispatcher.message_queue.get(timeout=1)
        assert msg["text"] == "ok"

    def test_wait_for_response_returns_on_match(self):
        from src.core.tap.agent_process import StdinDispatcher

        dispatcher = StdinDispatcher()

        def feed_response():
            time.sleep(0.05)
            with dispatcher._lock:
                q = dispatcher._pending.get("c-1")
            if q:
                q.put({"type": "confirm_response", "id": "c-1", "approved": True})

        t = threading.Thread(target=feed_response)
        t.start()

        result = dispatcher.wait_for_response("c-1", timeout=2)
        t.join()

        assert result["approved"] is True

    def test_wait_for_response_raises_abort(self):
        from src.core.tap.agent_process import StdinDispatcher
        from src.core.tap.exceptions import AbortError

        dispatcher = StdinDispatcher()

        def trigger_abort():
            time.sleep(0.05)
            dispatcher.abort_event.set()
            with dispatcher._lock:
                for q in dispatcher._pending.values():
                    q.put(None)

        t = threading.Thread(target=trigger_abort)
        t.start()

        with pytest.raises(AbortError):
            dispatcher.wait_for_response("c-2", timeout=2)

        t.join()

    def test_wait_for_response_timeout_returns_default_deny(self):
        from src.core.tap.agent_process import StdinDispatcher

        dispatcher = StdinDispatcher()
        result = dispatcher.wait_for_response("c-never", timeout=0.1)
        assert result == {"approved": False}

    def test_clear_abort_resets_state(self):
        from src.core.tap.agent_process import StdinDispatcher

        dispatcher = StdinDispatcher()
        dispatcher.abort_event.set()
        assert dispatcher.abort_event.is_set()

        dispatcher.clear_abort()
        assert not dispatcher.abort_event.is_set()

    def test_response_for_unknown_id_is_ignored(self):
        """A response for an ID not in _pending should not crash."""
        from src.core.tap.agent_process import StdinDispatcher

        dispatcher = StdinDispatcher()

        fake_stdin = io.StringIO(
            json.dumps({"type": "confirm_response", "id": "unknown-id", "approved": True}) + "\n"
        )

        with mock.patch.object(sys, "stdin", fake_stdin):
            dispatcher._reader_loop()  # Should not raise

    def test_multiple_messages_dispatched_in_order(self):
        from src.core.tap.agent_process import StdinDispatcher

        dispatcher = StdinDispatcher()

        lines = [
            json.dumps({"type": "user_message", "text": "first"}),
            json.dumps({"type": "user_message", "text": "second"}),
            json.dumps({"type": "user_message", "text": "third"}),
        ]
        fake_stdin = io.StringIO("\n".join(lines) + "\n")

        with mock.patch.object(sys, "stdin", fake_stdin):
            dispatcher._reader_loop()

        assert dispatcher.message_queue.get(timeout=1)["text"] == "first"
        assert dispatcher.message_queue.get(timeout=1)["text"] == "second"
        assert dispatcher.message_queue.get(timeout=1)["text"] == "third"


# ============================================================================
# 3. AgentProcess callback tests (unit-level, no real engine)
# ============================================================================

class TestAgentProcessCallbacks:
    """Test the stdio-based confirmation/input callbacks in isolation."""

    def test_confirmation_callback_emits_and_waits(self):
        """_confirmation_callback should emit confirm_request and return approved."""
        from src.core.tap.agent_process import AgentProcess

        proc = AgentProcess(mode="chat")
        buf = io.StringIO()

        # Feed a confirm_response into the dispatcher
        def feed_response():
            time.sleep(0.05)
            with proc._dispatcher._lock:
                for req_id, q in proc._dispatcher._pending.items():
                    q.put({"type": "confirm_response", "id": req_id, "approved": True})

        t = threading.Thread(target=feed_response)
        t.start()

        with mock.patch("src.core.tap.protocol.sys.stdout", buf):
            result = proc._confirmation_callback("Allow dangerous operation?")

        t.join()

        assert result is True
        # Verify the confirm_request was emitted
        output = buf.getvalue()
        event = json.loads(output.strip())
        assert event["type"] == "confirm_request"
        assert "Allow dangerous operation?" in event["message"]

    def test_confirmation_callback_denied(self):
        from src.core.tap.agent_process import AgentProcess

        proc = AgentProcess(mode="chat")
        buf = io.StringIO()

        def feed_deny():
            time.sleep(0.05)
            with proc._dispatcher._lock:
                for req_id, q in proc._dispatcher._pending.items():
                    q.put({"type": "confirm_response", "id": req_id, "approved": False})

        t = threading.Thread(target=feed_deny)
        t.start()

        with mock.patch("src.core.tap.protocol.sys.stdout", buf):
            result = proc._confirmation_callback("Delete everything?")

        t.join()
        assert result is False

    def test_confirmation_callback_returns_false_on_abort(self):
        from src.core.tap.agent_process import AgentProcess

        proc = AgentProcess(mode="chat")
        buf = io.StringIO()

        def trigger_abort():
            time.sleep(0.05)
            proc._dispatcher.abort_event.set()
            with proc._dispatcher._lock:
                for q in proc._dispatcher._pending.values():
                    q.put(None)

        t = threading.Thread(target=trigger_abort)
        t.start()

        with mock.patch("src.core.tap.protocol.sys.stdout", buf):
            result = proc._confirmation_callback("Allow?")

        t.join()
        assert result is False

    def test_input_callback_emits_and_waits(self):
        from src.core.tap.agent_process import AgentProcess

        proc = AgentProcess(mode="chat")
        buf = io.StringIO()

        def feed_input():
            time.sleep(0.05)
            with proc._dispatcher._lock:
                for req_id, q in proc._dispatcher._pending.items():
                    q.put({"type": "input_response", "id": req_id, "text": "用 pytest"})

        t = threading.Thread(target=feed_input)
        t.start()

        with mock.patch("src.core.tap.protocol.sys.stdout", buf):
            result = proc._input_callback("用什么测试框架？")

        t.join()

        assert result == "用 pytest"
        output = buf.getvalue()
        event = json.loads(output.strip())
        assert event["type"] == "input_request"
        assert event["question"] == "用什么测试框架？"

    def test_input_callback_returns_empty_on_abort(self):
        from src.core.tap.agent_process import AgentProcess

        proc = AgentProcess(mode="chat")
        buf = io.StringIO()

        def trigger_abort():
            time.sleep(0.05)
            proc._dispatcher.abort_event.set()
            with proc._dispatcher._lock:
                for q in proc._dispatcher._pending.values():
                    q.put(None)

        t = threading.Thread(target=trigger_abort)
        t.start()

        with mock.patch("src.core.tap.protocol.sys.stdout", buf):
            result = proc._input_callback("Question?")

        t.join()
        assert result == ""

    def test_emit_agent_event_dict_data(self):
        """_emit_agent_event should flatten dict data into the event."""
        from src.core.tap.agent_process import AgentProcess

        proc = AgentProcess(mode="chat")
        buf = io.StringIO()

        @dataclass
        class FakeEvent:
            type: str
            data: dict

        event = FakeEvent(type="token", data={"delta": "hi"})

        with mock.patch("src.core.tap.protocol.sys.stdout", buf):
            proc._emit_agent_event(event)

        parsed = json.loads(buf.getvalue().strip())
        assert parsed == {"type": "token", "delta": "hi"}

    def test_emit_agent_event_non_dict_data(self):
        """Non-dict data should be wrapped in {"value": ...}."""
        from src.core.tap.agent_process import AgentProcess

        proc = AgentProcess(mode="chat")
        buf = io.StringIO()

        @dataclass
        class FakeEvent:
            type: str
            data: str

        event = FakeEvent(type="error", data="something broke")

        with mock.patch("src.core.tap.protocol.sys.stdout", buf):
            proc._emit_agent_event(event)

        parsed = json.loads(buf.getvalue().strip())
        assert parsed == {"type": "error", "value": "something broke"}


# ============================================================================
# 4. TapClient tests (unit-level, mock subprocess)
# ============================================================================

class TestTapClient:
    """Tests for the TUI-side TAP client."""

    def test_send_message_format(self):
        """send_message should write correct JSON to stdin."""
        from src.core.tap.client import TapClient

        client = TapClient()
        # Mock the subprocess
        client._proc = mock.MagicMock()
        client._proc.poll.return_value = None
        stdin_buf = io.StringIO()
        client._proc.stdin = stdin_buf

        client.send_message("帮我重构这个函数")

        output = stdin_buf.getvalue()
        parsed = json.loads(output.strip())
        assert parsed["type"] == "user_message"
        assert parsed["text"] == "帮我重构这个函数"
        assert parsed["attachments"] == []

    def test_send_confirm_response_approved(self):
        from src.core.tap.client import TapClient

        client = TapClient()
        client._proc = mock.MagicMock()
        client._proc.poll.return_value = None
        stdin_buf = io.StringIO()
        client._proc.stdin = stdin_buf

        client.send_confirm_response("c-1", approved=True)

        parsed = json.loads(stdin_buf.getvalue().strip())
        assert parsed == {"type": "confirm_response", "id": "c-1", "approved": True}

    def test_send_confirm_response_denied_with_reason(self):
        from src.core.tap.client import TapClient

        client = TapClient()
        client._proc = mock.MagicMock()
        client._proc.poll.return_value = None
        stdin_buf = io.StringIO()
        client._proc.stdin = stdin_buf

        client.send_confirm_response("c-2", approved=False, reason="不要删这个")

        parsed = json.loads(stdin_buf.getvalue().strip())
        assert parsed["approved"] is False
        assert parsed["reason"] == "不要删这个"

    def test_send_input_response(self):
        from src.core.tap.client import TapClient

        client = TapClient()
        client._proc = mock.MagicMock()
        client._proc.poll.return_value = None
        stdin_buf = io.StringIO()
        client._proc.stdin = stdin_buf

        client.send_input_response("i-1", "用 pytest 框架")

        parsed = json.loads(stdin_buf.getvalue().strip())
        assert parsed == {"type": "input_response", "id": "i-1", "text": "用 pytest 框架"}

    def test_abort_sends_abort_message(self):
        from src.core.tap.client import TapClient

        client = TapClient()
        client._proc = mock.MagicMock()
        client._proc.poll.return_value = None
        stdin_buf = io.StringIO()
        client._proc.stdin = stdin_buf

        client.abort(kill_timeout=999)  # Large timeout so timer doesn't fire

        parsed = json.loads(stdin_buf.getvalue().strip())
        assert parsed == {"type": "abort"}

        # Clean up timer
        if client._abort_timer:
            client._abort_timer.cancel()

    def test_send_does_nothing_when_process_dead(self):
        """_send should silently do nothing if process has exited."""
        from src.core.tap.client import TapClient

        client = TapClient()
        client._proc = mock.MagicMock()
        client._proc.poll.return_value = 1  # Process exited
        stdin_buf = io.StringIO()
        client._proc.stdin = stdin_buf

        client.send_message("should not be sent")
        assert stdin_buf.getvalue() == ""

    def test_send_handles_broken_pipe(self):
        """_send should not raise on BrokenPipeError."""
        from src.core.tap.client import TapClient

        client = TapClient()
        client._proc = mock.MagicMock()
        client._proc.poll.return_value = None
        client._proc.stdin.write.side_effect = BrokenPipeError()

        # Should not raise
        client.send_message("test")

    def test_is_alive_property(self):
        from src.core.tap.client import TapClient

        client = TapClient()
        assert client.is_alive is False

        client._proc = mock.MagicMock()
        client._proc.poll.return_value = None
        client._alive = True
        assert client.is_alive is True

        client._proc.poll.return_value = 0
        assert client.is_alive is False

    def test_on_event_callback_called(self):
        """on_event callback should be called for each parsed event."""
        from src.core.tap.client import TapClient
        import queue as queue_mod

        received = []
        client = TapClient(on_event=lambda e: received.append(e))
        client._event_queue = queue_mod.Queue()
        client._alive = True

        # Simulate _read_events with a fake stdout
        fake_stdout = io.StringIO(
            json.dumps({"type": "token", "delta": "hi"}) + "\n"
            + json.dumps({"type": "finish", "reason": "end_turn"}) + "\n"
        )
        client._proc = mock.MagicMock()
        client._proc.stdout = fake_stdout

        client._read_events()

        assert len(received) == 2
        assert received[0]["type"] == "token"
        assert received[1]["type"] == "finish"


# ============================================================================
# 5. Modified files compatibility tests
# ============================================================================

class TestAuditGuardCallback:
    """Tests for audit_guard.py callback injection."""

    def test_set_confirmation_callback(self):
        from backend.utils.audit_guard import set_confirmation_callback, _confirmation_callback

        original = _confirmation_callback
        try:
            cb = mock.MagicMock(return_value=True)
            set_confirmation_callback(cb)

            from backend.utils import audit_guard
            assert audit_guard._confirmation_callback is cb
        finally:
            set_confirmation_callback(original)

    def test_prompt_user_uses_callback_when_set(self):
        from backend.utils import audit_guard

        original = audit_guard._confirmation_callback
        try:
            cb = mock.MagicMock(return_value=True)
            audit_guard._confirmation_callback = cb

            result = audit_guard._prompt_user("WRITE to", "/etc/passwd")
            assert result is True
            cb.assert_called_once()
            assert "WRITE to" in cb.call_args[0][0]
        finally:
            audit_guard._confirmation_callback = original

    def test_prompt_user_callback_returns_false_on_exception(self):
        from backend.utils import audit_guard

        original = audit_guard._confirmation_callback
        try:
            cb = mock.MagicMock(side_effect=RuntimeError("broken"))
            audit_guard._confirmation_callback = cb

            result = audit_guard._prompt_user("DELETE", "/important")
            assert result is False
        finally:
            audit_guard._confirmation_callback = original


class TestLocalEnvironmentConfirmation:
    """Tests for local.py _request_confirmation changes."""

    def test_request_confirmation_uses_callback(self):
        from backend.infra.envs.local import LocalEnvironment

        cb = mock.MagicMock(return_value=True)
        env = LocalEnvironment(
            workspace_root="/tmp",
            blackboard_dir="/tmp/.blackboard",
            confirmation_callback=cb,
        )

        result = env._request_confirmation("Allow?")
        assert result is True
        cb.assert_called_once_with("Allow?")

    def test_request_confirmation_handles_eof_on_fallback(self):
        """When no callback and input() raises EOFError, should return False."""
        from backend.infra.envs.local import LocalEnvironment

        env = LocalEnvironment(
            workspace_root="/tmp",
            blackboard_dir="/tmp/.blackboard",
            confirmation_callback=None,
            non_interactive=False,
        )

        with mock.patch("builtins.input", side_effect=EOFError):
            result = env._request_confirmation("Allow?")
            assert result is False


class TestAskUserToolFallback:
    """Tests for ask_user_tool.py changes."""

    def test_execute_uses_callback(self):
        from src.tools.ask_user_tool import AskUserTool

        cb = mock.MagicMock(return_value="use pytest")
        tool = AskUserTool(input_callback=cb)

        result = tool.execute(question="What framework?")
        assert result == "use pytest"
        cb.assert_called_once_with("What framework?")

    def test_execute_handles_eof_without_callback(self):
        from src.tools.ask_user_tool import AskUserTool

        tool = AskUserTool(input_callback=None)

        with mock.patch("builtins.input", side_effect=EOFError):
            result = tool.execute(question="What?")
            assert result == ""


class TestRequestMonitorFallback:
    """Tests for request_monitor.py changes."""

    def test_uses_callback_when_provided(self):
        """When confirmation_callback is set, it should be used."""
        from src.core.middlewares.request_monitor import RequestMonitorMiddleware

        cb = mock.MagicMock(return_value=True)

        with mock.patch("src.core.ipc.request_manager.RequestManager") as MockRM:
            rm_instance = MockRM.return_value
            rm_instance.list_pending_requests.return_value = [{
                "id": "req-1",
                "agent_name": "Worker-1",
                "type": "permission_request",
                "content": "rm -rf /",
                "reason": "cleanup",
            }]
            rm_instance.update_request_status.return_value = True

            middleware = RequestMonitorMiddleware(
                blackboard_dir="/tmp/.blackboard",
                confirmation_callback=cb,
            )
            middleware._check_and_handle_requests()

            cb.assert_called_once()
            rm_instance.update_request_status.assert_called_once_with("req-1", "APPROVED")


# ============================================================================
# 6. End-to-end protocol round-trip test
# ============================================================================

class TestProtocolRoundTrip:
    """Test that events survive serialize → deserialize round-trip."""

    @pytest.mark.parametrize("event", [
        {"type": "token", "delta": "好的，我来看一下"},
        {"type": "tool_call", "tool": "bash", "args": {"command": "ls -la"}},
        {"type": "tool_result", "tool": "bash", "status": "success", "output": "total 42\ndrwxr-xr-x  5 user  staff  160 Feb 18 10:00 ."},
        {"type": "finish", "reason": "end_turn"},
        {"type": "confirm_request", "id": "c-1", "kind": "confirmation", "message": "⚠️ 删除文件？"},
        {"type": "input_request", "id": "i-1", "question": "用什么框架？"},
        {"type": "error", "code": "timeout", "message": "LLM 超时", "recoverable": True},
    ])
    def test_event_round_trip(self, event):
        """Each event type should survive JSON serialization round-trip."""
        from src.core.tap.protocol import emit_event, parse_control_message

        buf = io.StringIO()
        emit_event(event, file=buf)
        raw_line = buf.getvalue()

        # Should be exactly one line
        assert raw_line.count("\n") == 1

        # Round-trip
        restored = json.loads(raw_line.strip())
        assert restored == event

    @pytest.mark.parametrize("msg", [
        {"type": "user_message", "text": "帮我重构这个函数", "attachments": []},
        {"type": "confirm_response", "id": "c-1", "approved": True},
        {"type": "confirm_response", "id": "c-1", "approved": False, "reason": "不要删这个"},
        {"type": "input_response", "id": "i-1", "text": "用 pytest 框架"},
        {"type": "abort"},
    ])
    def test_control_message_round_trip(self, msg):
        """Each control message type should survive round-trip."""
        from src.core.tap.protocol import parse_control_message

        line = json.dumps(msg) + "\n"
        restored = parse_control_message(line)
        assert restored == msg
