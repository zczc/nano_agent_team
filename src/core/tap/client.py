"""
TAP Client — TUI-side interface for communicating with an Agent subprocess.

Spawns the Agent as a child process, reads its event stream from stdout,
and sends control messages via stdin.

Usage:
    client = TapClient(mode="chat", model_key="gpt-4o")
    client.start()

    # Send a message (non-blocking, events arrive via callback)
    client.send_message("帮我重构这个函数")

    # Or iterate events
    for event in client.iter_events():
        ...

    # Abort
    client.abort()

    # Shutdown
    client.stop()
"""

import json
import subprocess
import sys
import os
import threading
from typing import Optional, Callable, Dict, Any, Generator


class TapClient:
    """
    TUI-side TAP client. Manages the Agent subprocess lifecycle.
    """

    def __init__(
        self,
        mode: str = "chat",
        model_key: Optional[str] = None,
        workspace: Optional[str] = None,
        blackboard_dir: str = ".blackboard",
        max_iterations: int = 200,
        python_executable: Optional[str] = None,
        on_event: Optional[Callable[[dict], None]] = None,
    ):
        self.mode = mode
        self.model_key = model_key
        self.workspace = workspace or os.getcwd()
        self.blackboard_dir = blackboard_dir
        self.max_iterations = max_iterations
        self.python_executable = python_executable or sys.executable
        self.on_event = on_event

        self._proc: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._event_queue: "queue.Queue[Optional[dict]]" = None
        self._alive = False
        self._abort_timer: Optional[threading.Timer] = None

    def start(self) -> None:
        """Spawn the Agent subprocess."""
        import queue
        self._event_queue = queue.Queue()

        cmd = [
            self.python_executable, "-m", "src.core.tap.agent_process",
            "--mode", self.mode,
            "--blackboard", self.blackboard_dir,
            "--max-iterations", str(self.max_iterations),
        ]
        if self.model_key:
            cmd.extend(["--model", self.model_key])
        if self.workspace:
            cmd.extend(["--workspace", self.workspace])

        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=self.workspace,
        )
        self._alive = True

        # Background thread to read agent stdout
        self._reader_thread = threading.Thread(
            target=self._read_events, daemon=True
        )
        self._reader_thread.start()

    def _read_events(self) -> None:
        """Background thread: read agent stdout, parse JSON events, enqueue."""
        try:
            for line in self._proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    self._event_queue.put(event)
                    if self.on_event:
                        self.on_event(event)
                except json.JSONDecodeError:
                    pass
        except Exception:
            pass
        finally:
            self._alive = False
            self._event_queue.put(None)  # Sentinel

    # -- Sending control messages -------------------------------------------

    def _send(self, msg: dict) -> None:
        """Write a JSON control message to agent stdin."""
        if self._proc and self._proc.stdin and self._proc.poll() is None:
            try:
                self._proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError):
                pass

    def send_message(self, text: str, attachments: list = None) -> None:
        """Send a user_message to start a new turn."""
        self._send({
            "type": "user_message",
            "text": text,
            "attachments": attachments or [],
        })

    def send_confirm_response(self, req_id: str, approved: bool, reason: str = None) -> None:
        """Reply to a confirm_request."""
        msg = {"type": "confirm_response", "id": req_id, "approved": approved}
        if reason:
            msg["reason"] = reason
        self._send(msg)

    def send_input_response(self, req_id: str, text: str) -> None:
        """Reply to an input_request."""
        self._send({"type": "input_response", "id": req_id, "text": text})

    def abort(self, kill_timeout: float = 10.0) -> None:
        """
        Send abort signal. If agent doesn't finish within kill_timeout, SIGKILL.
        """
        self._send({"type": "abort"})

        # Safety net: force kill if agent doesn't respond
        if self._abort_timer:
            self._abort_timer.cancel()
        self._abort_timer = threading.Timer(
            kill_timeout, self._force_kill
        )
        self._abort_timer.start()

    def _force_kill(self) -> None:
        """Force kill the agent process."""
        if self._proc and self._proc.poll() is None:
            self._proc.kill()

    # -- Event consumption --------------------------------------------------

    def iter_events(self, timeout: float = None) -> Generator[dict, None, None]:
        """
        Iterate over events from the agent. Blocks until an event is available.
        Yields None sentinel when agent process exits.
        """
        import queue
        while self._alive or not self._event_queue.empty():
            try:
                event = self._event_queue.get(timeout=timeout)
                if event is None:
                    return
                yield event

                # Cancel abort timer if we got a finish event
                if event.get("type") == "finish" and self._abort_timer:
                    self._abort_timer.cancel()
                    self._abort_timer = None

            except queue.Empty:
                continue

    def next_event(self, timeout: float = 30.0) -> Optional[dict]:
        """Get the next event, or None on timeout."""
        import queue
        try:
            return self._event_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    # -- Lifecycle ----------------------------------------------------------

    def stop(self) -> None:
        """Gracefully stop the agent process."""
        if self._abort_timer:
            self._abort_timer.cancel()

        if self._proc and self._proc.poll() is None:
            # Close stdin to signal EOF
            try:
                self._proc.stdin.close()
            except Exception:
                pass

            # Wait briefly, then kill
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()

        self._alive = False

    @property
    def is_alive(self) -> bool:
        return self._alive and self._proc is not None and self._proc.poll() is None

    def __del__(self):
        self.stop()
