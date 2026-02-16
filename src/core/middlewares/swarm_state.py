import os
import json
from backend.llm.middleware import StrategyMiddleware
from backend.llm.types import AgentSession
from backend.utils.logger import Logger
from typing import Callable, Any

from src.utils.registry_manager import RegistryManager


class SwarmStateMiddleware(StrategyMiddleware):
    """
    Swarm State Middleware (StrategyMiddleware Pattern)

    Injects the REAL-TIME SWARM STATUS into the System Prompt before every LLM call.
    1. Reads registry.json
    2. Verifies PIDs (Dead/Alive)
    3. Injects full registry content into session.system_config.extra_sections
    4. Ensures no duplicate history (Updates in-place if possible or appends unique section)
    """
    def __init__(self, blackboard_dir: str = ".blackboard"):
        self.blackboard_dir = blackboard_dir
        self.registry = RegistryManager(blackboard_dir)

    def __call__(self, session: AgentSession, next_call: Callable[[AgentSession], Any]) -> Any:
        self._inject_status(session)
        return next_call(session)

    def _inject_status(self, session: AgentSession):
        registry_path = os.path.join(self.blackboard_dir, "registry.json")
        if not os.path.exists(registry_path):
            return

        try:
            report_data = self.registry.verify_and_sync_pids()

            status_text = json.dumps(report_data, indent=2, ensure_ascii=False)

            header = "## REAL-TIME SWARM STATUS (REGISTRY)"
            full_section = f"{header}\nThis is the current state of all agents in the swarm, synced from the registry.\nVerified by Middleware (PID Check).\n\n```json\n{status_text}\n```"

            idx = -1
            for i, section in enumerate(session.system_config.extra_sections):
                if section.startswith(header):
                    idx = i
                    break

            if idx != -1:
                session.system_config.extra_sections[idx] = full_section
            else:
                session.system_config.extra_sections.insert(0, full_section)

        except Exception as e:
            Logger.error(f"[SwarmStateMiddleware] Failed to inject/update status: {e}")
