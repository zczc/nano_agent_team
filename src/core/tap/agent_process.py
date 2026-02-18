"""
TAP Agent Process — the Agent side of the TUI-Agent Protocol.

This module is the entry point for the Agent subprocess. It:
1. Reads control messages from stdin via a background thread
2. Runs the AgentEngine loop, yielding events to stdout
3. Provides stdio-based confirmation_callback / input_callback
   that replace the old threading.Event + TUI dialog mechanism

Usage:
    python -m src.core.tap.agent_process [--mode chat|swarm] [--model KEY] [--workspace DIR]

Or programmatically:
    proc = AgentProcess(config)
    proc.run()   # blocks, reads stdin, writes stdout
"""

import json
import sys
import os
import threading
import signal
import traceback
from queue import Queue, Empty
from typing import Optional, Dict, Any, List

from .protocol import (
    emit_event,
    parse_control_message,
    make_confirm_request,
    make_input_request,
)
from .exceptions import AbortError


# ---------------------------------------------------------------------------
# Stdin reader thread + dispatch queues
# ---------------------------------------------------------------------------

class StdinDispatcher:
    """
    Background thread that reads stdin line-by-line and dispatches messages
    to the appropriate queue based on type.

    - user_message → message_queue (consumed by main loop)
    - confirm_response / input_response → pending[id] (consumed by callbacks)
    - abort → sets abort_event, wakes all pending callbacks
    """

    def __init__(self):
        self.message_queue: Queue = Queue()
        self._pending: Dict[str, Queue] = {}
        self.abort_event = threading.Event()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Start the background stdin reader thread."""
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()

    def _reader_loop(self):
        """Continuously read stdin, dispatch by message type."""
        try:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type")

                if msg_type == "abort":
                    self.abort_event.set()
                    # Wake all pending callbacks so they raise AbortError
                    with self._lock:
                        for q in self._pending.values():
                            q.put(None)

                elif msg_type in ("confirm_response", "input_response"):
                    req_id = msg.get("id", "")
                    with self._lock:
                        q = self._pending.get(req_id)
                    if q is not None:
                        q.put(msg)

                elif msg_type == "user_message":
                    self.message_queue.put(msg)

        except Exception:
            # stdin closed or broken pipe — agent should exit
            pass

    def wait_for_response(self, req_id: str, timeout: float = 120) -> dict:
        """
        Block until a response with the given id arrives, or abort/timeout.

        Returns the response dict.
        Raises AbortError if abort was signaled.
        Raises TimeoutError if timeout expires.
        """
        q: Queue = Queue()
        with self._lock:
            self._pending[req_id] = q
        try:
            result = q.get(timeout=timeout)
            if result is None or self.abort_event.is_set():
                raise AbortError()
            return result
        except Empty:
            # Timeout — default deny
            return {"approved": False}
        finally:
            with self._lock:
                self._pending.pop(req_id, None)

    def clear_abort(self):
        """Reset abort state for a new turn."""
        self.abort_event.clear()


# ---------------------------------------------------------------------------
# AgentProcess — main entry point
# ---------------------------------------------------------------------------

class AgentProcess:
    """
    Runs the Agent engine in a loop, communicating with TUI via TAP protocol.

    Lifecycle:
        1. __init__: configure mode, model, workspace
        2. run(): start stdin dispatcher, enter main loop
        3. Main loop: wait for user_message → run engine → emit events
        4. Callbacks: confirmation_callback / input_callback block on stdin
        5. Abort: abort_event interrupts engine loop
    """

    def __init__(
        self,
        mode: str = "chat",
        model_key: Optional[str] = None,
        workspace: Optional[str] = None,
        blackboard_dir: str = ".blackboard",
        max_iterations: int = 200,
    ):
        self.mode = mode
        self.model_key = model_key
        self.workspace = workspace or os.getcwd()
        self.blackboard_dir = blackboard_dir
        self.max_iterations = max_iterations

        self._dispatcher = StdinDispatcher()
        self._engine = None
        self._swarm_agent = None

    # -- stdio-based callbacks (injected into LocalEnvironment / tools) -----

    def _confirmation_callback(self, message: str) -> bool:
        """
        Replaces the old threading.Event + TUI dialog callback.
        Emits confirm_request to stdout, blocks on stdin for response.
        """
        event = make_confirm_request(message)
        emit_event(event)
        try:
            response = self._dispatcher.wait_for_response(event["id"], timeout=120)
            return response.get("approved", False)
        except AbortError:
            return False

    def _input_callback(self, question: str) -> str:
        """
        Replaces the old threading.Event + TUI input callback.
        Emits input_request to stdout, blocks on stdin for response.
        """
        event = make_input_request(question)
        emit_event(event)
        try:
            response = self._dispatcher.wait_for_response(event["id"], timeout=120)
            return response.get("text", "")
        except AbortError:
            return ""

    # -- Agent event → stdout -----------------------------------------------

    def _emit_agent_event(self, agent_event) -> None:
        """Convert an AgentEvent from the engine into a TAP event on stdout."""
        # AgentEvent has .type and .data (dict or Any)
        data = agent_event.data if isinstance(agent_event.data, dict) else {"value": agent_event.data}
        tap = {"type": agent_event.type}
        tap.update(data)
        emit_event(tap)

    # -- Engine initialization -----------------------------------------------

    def _init_engine(self) -> None:
        """Initialize the AgentEngine (chat or swarm) with stdio callbacks."""
        # Ensure project root is on sys.path
        project_root = os.path.abspath(self.workspace)
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

        # Inject confirmation callback into audit_guard (for Python subprocess sandboxing)
        try:
            from backend.utils.audit_guard import set_confirmation_callback
            set_confirmation_callback(self._confirmation_callback)
        except ImportError:
            pass

        if self.mode == "swarm":
            self._init_swarm_engine()
        else:
            self._init_chat_engine()

    def _init_chat_engine(self) -> None:
        """Initialize standard chat AgentEngine."""
        from backend.llm.engine import AgentEngine
        from backend.llm.types import SystemPromptConfig
        from backend.llm.middleware import ExecutionBudgetManager, InteractionRefinementMiddleware
        from backend.infra.envs.local import LocalEnvironment
        from backend.tools.bash import BashTool
        from backend.tools.write_file import WriteFileTool
        from backend.tools.read_file import ReadFileTool
        from backend.tools.edit_file import EditFileTool
        from backend.tools.web_search import SearchTool
        from backend.tools.web_reader import WebReaderTool

        env = LocalEnvironment(
            workspace_root=self.workspace,
            blackboard_dir=self.blackboard_dir,
            confirmation_callback=self._confirmation_callback,
        )

        tools = [
            BashTool(env=env),
            WriteFileTool(env=env),
            ReadFileTool(env=env),
            EditFileTool(env=env),
            SearchTool(),
            WebReaderTool(),
        ]

        # Try loading tools from registry
        try:
            from backend.tools.grep import GrepTool
            from backend.tools.glob import GlobTool
            tools.append(GrepTool())
            tools.append(GlobTool())
        except ImportError:
            pass

        # Inject ask_user tool with stdio callback
        from src.tools.ask_user_tool import AskUserTool
        tools.append(AskUserTool(input_callback=self._input_callback))

        strategies = [
            InteractionRefinementMiddleware(),
            ExecutionBudgetManager(max_iterations=self.max_iterations),
        ]

        self._engine = AgentEngine(
            tools=tools,
            strategies=strategies,
            provider_key=self.model_key,
        )
        self._messages: List[Dict[str, Any]] = []
        self._system_prompt = (
            "You are a helpful AI assistant. You have access to various tools "
            "to help answer questions. Be concise and helpful. "
            "Format your responses using markdown when appropriate."
        )

    def _init_swarm_engine(self) -> None:
        """Initialize SwarmAgent engine."""
        from src.core.agent_wrapper import SwarmAgent
        from backend.infra.config import Config
        from src.tools.ask_user_tool import AskUserTool

        Config.BLACKBOARD_ROOT = os.path.abspath(self.blackboard_dir)
        os.makedirs(Config.BLACKBOARD_ROOT, exist_ok=True)

        # Load architect prompt
        prompt_path = os.path.join(self.workspace, "src", "prompts", "architect.md")
        role = "You are the Root Architect."
        if os.path.exists(prompt_path):
            with open(prompt_path, "r", encoding="utf-8") as f:
                role = f.read()

        self._swarm_agent = SwarmAgent(
            role=role,
            name="Assistant",
            blackboard_dir=self.blackboard_dir,
            model=self.model_key,
            max_iterations=self.max_iterations,
        )

        # Inject stdio callbacks into tools
        for tool in self._swarm_agent.tools:
            if isinstance(tool, AskUserTool):
                tool.input_callback = self._input_callback

        # Inject confirmation callback into environment
        if hasattr(self._swarm_agent, 'engine'):
            for tool in self._swarm_agent.engine.tools:
                if hasattr(tool, 'env') and hasattr(tool.env, 'confirmation_callback'):
                    tool.env.confirmation_callback = self._confirmation_callback

        # Add RequestMonitor with stdio callback
        from src.core.middlewares import RequestMonitorMiddleware
        request_monitor = RequestMonitorMiddleware(
            blackboard_dir=self.blackboard_dir,
            confirmation_callback=self._confirmation_callback,
        )
        self._swarm_agent.add_strategy(request_monitor)

        self._engine = self._swarm_agent.engine
        self._messages: List[Dict[str, Any]] = []
        self._system_prompt = None  # Built dynamically by SwarmAgent

    # -- Main loop -----------------------------------------------------------

    def run(self) -> None:
        """
        Main blocking loop. Call this from __main__.

        1. Initialize engine
        2. Start stdin dispatcher
        3. Loop: read user_message → run engine → emit events
        """
        self._init_engine()
        self._dispatcher.start()

        while True:
            try:
                # Block until TUI sends a user_message
                msg = self._dispatcher.message_queue.get()
            except KeyboardInterrupt:
                break

            user_text = msg.get("text", "")
            if not user_text:
                continue

            self._dispatcher.clear_abort()

            try:
                self._run_turn(user_text)
            except AbortError:
                self._cleanup_on_abort()
                emit_event({"type": "finish", "reason": "aborted"})
            except Exception as e:
                emit_event({
                    "type": "error",
                    "code": "engine_error",
                    "message": str(e),
                    "recoverable": True,
                })
                emit_event({"type": "finish", "reason": "error"})

    def _run_turn(self, user_text: str) -> None:
        """Execute a single conversation turn, emitting events to stdout."""
        from backend.llm.types import SystemPromptConfig

        self._messages.append({"role": "user", "content": user_text})

        # Build system prompt
        if self.mode == "swarm" and self._swarm_agent:
            sys_content = self._swarm_agent.prompt_builder.build(
                self._swarm_agent.role, ""
            )
            system_config = SystemPromptConfig(base_prompt=sys_content)
            if self._swarm_agent:
                self._swarm_agent.register()
        else:
            system_config = SystemPromptConfig(base_prompt=self._system_prompt)

        event_generator = self._engine.run(
            self._messages,
            system_config,
            max_iterations=self.max_iterations,
        )

        for event in event_generator:
            # Check abort between events
            if self._dispatcher.abort_event.is_set():
                event_generator.close()
                raise AbortError()

            self._emit_agent_event(event)

            # Forward to swarm logging if applicable
            if self.mode == "swarm" and self._swarm_agent:
                self._swarm_agent.handle_event(event)

        # Emit finish if engine didn't
        emit_event({"type": "finish", "reason": "end_turn"})

    def _cleanup_on_abort(self) -> None:
        """Clean up after an abort signal."""
        # Kill any running tool subprocesses, cancel LLM requests, etc.
        # The engine's generator.close() already handles most cleanup.
        # For swarm mode, mark agent as IDLE
        if self._swarm_agent:
            try:
                self._swarm_agent.registry.update_agent(
                    self._swarm_agent.name, status="IDLE"
                )
            except Exception:
                pass


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    """Entry point when run as `python -m src.core.tap.agent_process`."""
    import argparse

    parser = argparse.ArgumentParser(description="TAP Agent Process")
    parser.add_argument("--mode", choices=["chat", "swarm"], default="chat")
    parser.add_argument("--model", type=str, default=None, help="LLM provider key")
    parser.add_argument("--workspace", type=str, default=None, help="Workspace root")
    parser.add_argument("--blackboard", type=str, default=".blackboard")
    parser.add_argument("--max-iterations", type=int, default=200)
    args = parser.parse_args()

    proc = AgentProcess(
        mode=args.mode,
        model_key=args.model,
        workspace=args.workspace,
        blackboard_dir=args.blackboard,
        max_iterations=args.max_iterations,
    )
    proc.run()


if __name__ == "__main__":
    main()
