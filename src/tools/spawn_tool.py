
import os
import sys
import subprocess
import time
from typing import Dict, Any, Optional

from backend.tools.base import BaseTool
from backend.infra.config import Config
from backend.llm.decorators import schema_strict_validator
import fcntl
from src.utils.file_lock import file_lock

class SpawnSwarmAgentTool(BaseTool):
    """
    Spawns a new Swarm Agent process (detached).
    Handles logging redirection and python environment consistency.
    """
    def __init__(self, root_dir: str = ".blackboard", max_iterations: int = 200):
        super().__init__()
        self.root_dir = root_dir
        self.log_dir = os.path.join(root_dir, "logs")
        self.max_iterations = max_iterations
        self.agent_model = None  # Injected via configure
        os.makedirs(self.log_dir, exist_ok=True)
        os.makedirs(os.path.join(root_dir, "mailboxes"), exist_ok=True)

    def configure(self, context: Dict[str, Any]):
        """Inject runtime context including parent agent's model and name."""
        self.agent_model = context.get("agent_model")
        self._parent_agent_name = context.get("agent_name", "Assistant")

    @property
    def name(self) -> str:
        return "spawn_swarm_agent"

    @property
    def description(self) -> str:
        return """Spawns a new Swarm Agent process in the background.
        Automatically redirects stdout/stderr to .blackboard/logs/<name>.log.
        Returns the PID of the new process.
        """

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Unique name of the agent (e.g. 'Coordinator')"
                },
                "role": {
                    "type": "string",
                    "description": "Role description (e.g. 'Project Manager')"
                },
                "goal": {
                    "type": "string",
                    "description": "Initial goal or instruction"
                },
                "model": {
                    "type": "string",
                    "description": "Optional model provider"
                },
                # "blackboard_dir": {
                #     "type": "string",
                #     "description": f"Path to blackboard",
                # },
                "excluded_tools": {
                    "type": "array",
                    "items": { "type": "string" },
                    "description": "List of tool names to exclude from the agent. Defaults to ['ask_user'].",
                    "default": ["ask_user"]
                }
            },
            "required": ["name", "role", "goal"]
        }

    @schema_strict_validator
    def execute(self, name: str, role: str, goal: str, blackboard_dir: str = None, model: Optional[str] = None, excluded_tools: list = ["ask_user"]) -> str:
        proc = None
        try:
            # 1. Resolve CLI Path
            # This tool is in src/tools/spawn_tool.py -> src/cli.py is ../cli.py
            current_dir = os.path.dirname(os.path.abspath(__file__))
            cli_path = os.path.abspath(os.path.join(current_dir, "../cli.py"))

            if blackboard_dir is None:
                blackboard_dir = Config.BLACKBOARD_ROOT

            if not os.path.exists(cli_path):
                return f"Error: Could not find CLI script at {cli_path}"

            # 2. Prepare Log File
            self.log_dir = os.path.join(blackboard_dir, "logs")
            os.makedirs(self.log_dir, exist_ok=True)
            log_file_path = os.path.join(self.log_dir, f"{name}.log")

            # 3. Construct Command
            # Priority: Tool Arg > state.get_model_key() (Last Active) > Parent Agent Model (Context) > System Default
            from src.tui.state import state
            active_model = model or state.get_model_key() or self.agent_model

            # Determine parent agent name (use configured name or default to "Assistant")
            parent_agent_name = getattr(self, '_parent_agent_name', 'Assistant')

            cmd = [
                sys.executable,
                cli_path,
                "--name", name,
                "--role", role,
                "--goal", goal,
                "--blackboard", blackboard_dir,
                "--parent-pid", str(os.getpid()),  # Pass current PID as parent
                "--parent-agent-name", parent_agent_name,  # Pass parent agent name for registry monitoring
                "--max-iterations", str(self.max_iterations)
            ]

            # Use active model if available (either passed in tool or from TUI config)
            if active_model:
                cmd.extend(["--model", str(active_model)])

            # Pass excluded tools (default excludes ask_user for worker agents)
            if excluded_tools:
                cmd.extend(["--exclude-tools", ",".join(excluded_tools)])

            # 4. Launch Process
            # Open log file for appending
            with open(log_file_path, "a") as log_f:
                log_f.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Spawning agent...\n")
                log_f.write(f"Command: {' '.join(cmd)}\n")
                log_f.flush()

                proc = subprocess.Popen(
                    cmd,
                    stdout=log_f,
                    stderr=log_f,
                    cwd=os.getcwd(), # Ensure we run from project root
                    env=os.environ.copy() # Pass current env
                )

                # IMPORTANT: Write PID to log for status_tool tracking
                log_f.write(f"PID: {proc.pid}\n")
                log_f.flush()

            # 5. Update Registry as STARTING
            self._update_registry(blackboard_dir, name, role, proc.pid, goal)

            # 6. Wait for agent to reach RUNNING status (handshake)
            ready = self._wait_for_agent_ready(blackboard_dir, name)
            if not ready:
                self._cleanup_process(proc, blackboard_dir, name)
                return f"Error: Agent '{name}' failed to start within timeout. Process cleaned up."

            return f"Success: Spawned agent '{name}' (PID: {proc.pid}) and verified RUNNING status. Log: {log_file_path}"

        except Exception as e:
            if proc is not None:
                self._cleanup_process(proc, blackboard_dir, name)
            return f"Error spawning agent: {str(e)}"

    def _cleanup_process(self, proc, blackboard_dir: str, name: str):
        """Terminate a spawned process and mark it DEAD in registry."""
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        except Exception:
            pass

        # Mark agent as DEAD in registry
        try:
            from src.utils.registry_manager import RegistryManager
            registry_mgr = RegistryManager(blackboard_dir)

            def _mark_dead(registry):
                if name in registry:
                    registry[name]["status"] = "DEAD"

            registry_mgr._read_and_write(_mark_dead)
        except Exception:
            pass

    def _get_agent_status(self, blackboard_dir: str, name: str) -> Optional[str]:
        """Fetch the current status of an agent from the registry."""
        import json
        registry_path = os.path.join(blackboard_dir, "registry.json")
        if not os.path.exists(registry_path):
            return None
        
        try:
            with file_lock(registry_path, 'r', fcntl.LOCK_SH, timeout=10) as fd:
                if fd:
                    content = fd.read()
                    registry = json.loads(content) if content else {}
                    return registry.get(name, {}).get("status")
        except Exception:
            pass
        return None

    def _wait_for_agent_ready(self, blackboard_dir: str, name: str, timeout: float = 15.0) -> bool:
        """Poll the registry until the agent status is RUNNING."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            status = self._get_agent_status(blackboard_dir, name)
            if status == "RUNNING":
                return True
            time.sleep(0.5)
        return False

    def _update_registry(self, blackboard_dir: str, name: str, role: str, pid: int, goal: str):
        """Updates the registry.json with the new agent info (Initial status: STARTING)."""
        from src.utils.registry_manager import RegistryManager
        registry_mgr = RegistryManager(blackboard_dir)

        def _mutate(registry):
            registry[name] = {
                "role": role,
                "pid": pid,
                "goal": goal,
                "spawn_time": time.time(),
                "status": "STARTING"
            }

        registry_mgr._read_and_write(_mutate)
