import os
import signal
from backend.llm.middleware import StrategyMiddleware
from backend.llm.types import AgentSession
from backend.utils.logger import Logger
from typing import Callable, Any

from ..runtime import RuntimeManager
from src.utils.registry_manager import RegistryManager


class ParentProcessMonitorMiddleware(StrategyMiddleware):
    """
    Middleware that monitors the parent process and terminates the current process
    if the parent process ceases to exist OR if the parent agent has finished its task.

    Enhanced to check both:
    1. Process existence (PID check)
    2. Agent status in registry.json (DEAD status)

    This prevents orphaned child agents from running indefinitely after parent completes.
    """
    def __init__(self, parent_pid: int, agent_name: str = "Agent", blackboard_dir: str = ".blackboard", parent_agent_name: str = "Assistant"):
        """
        Initialize the monitor with the parent PID to watch.

        Args:
            parent_pid: The PID of the parent process (Watchdog/Assistant).
                       If None or 0, monitoring is disabled.
            agent_name: Name of the current agent (for cleanup logic).
            blackboard_dir: Blackboard directory (for cleanup logic).
            parent_agent_name: Name of the parent agent in registry (for status check).
        """
        self.parent_pid = parent_pid
        self.agent_name = agent_name
        self.blackboard_dir = blackboard_dir
        self.parent_agent_name = parent_agent_name
        self._registry = RegistryManager(blackboard_dir)
        self._terminating = False
        if self.parent_pid and self.parent_pid > 0:
            Logger.info(f"Initialized ParentProcessMonitorMiddleware for {self.agent_name}: watching parent PID {self.parent_pid} (agent: {self.parent_agent_name})")

    def _is_pid_running(self, pid: int) -> bool:
        """Check if a PID exists explicitly."""
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _is_parent_agent_active(self) -> bool:
        """
        Check if parent agent is still active in registry.json.
        Returns False if parent status is DEAD (either by FinishTool or ctrl+K).

        Child agents will terminate when parent is DEAD, regardless of the reason.
        """
        try:
            info = self._registry.get_agent(self.parent_agent_name)
            if info is None:
                return True  # Assume active if not found

            status = info.get("status", "UNKNOWN")
            if status == "DEAD":
                return False

            return True

        except Exception as e:
            Logger.debug(f"Failed to check parent agent status: {e}")
            return True  # Assume active on error

    def __call__(self, session: AgentSession, next_call: Callable[[AgentSession], Any]) -> Any:
        """
        Check parent process and agent status before proceeding with the pipeline.
        Terminates child agent if:
        1. Parent process PID no longer exists, OR
        2. Parent agent status is DEAD in registry
        """
        if self.parent_pid and self.parent_pid > 0:
            # Check 1: Process existence
            if not self._is_pid_running(self.parent_pid):
                Logger.warning(f"[{self.agent_name}] Parent process {self.parent_pid} died. Terminating self.")
                self._terminate_self("Parent process died")

            # Check 2: Agent status in registry
            if not self._is_parent_agent_active():
                Logger.warning(f"[{self.agent_name}] Parent agent '{self.parent_agent_name}' is DEAD. Terminating self.")
                self._terminate_self(f"Parent agent '{self.parent_agent_name}' finished")

        return next_call(session)

    def _terminate_self(self, reason: str):
        """Perform cleanup and terminate the current process."""
        if self._terminating:
            return
        self._terminating = True
        try:
            RuntimeManager.cleanup_agent(
                name=self.agent_name,
                blackboard_dir=self.blackboard_dir,
                reason=reason
            )
        except Exception as e:
            Logger.error(f"Cleanup failed during termination: {e}")

        # Terminate self gracefully (SIGTERM allows cleanup via atexit/finally/signal handlers)
        Logger.info(f"[{self.agent_name}] Self-terminating: {reason}")
        os.kill(os.getpid(), signal.SIGTERM)
