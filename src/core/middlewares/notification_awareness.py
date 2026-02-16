import os
import fcntl
from backend.llm.middleware import StrategyMiddleware
from backend.llm.types import AgentSession
from backend.utils.logger import Logger
from typing import Callable, Any

from src.utils.file_lock import file_lock


class NotificationAwarenessMiddleware(StrategyMiddleware):
    """
    Notification Awareness Middleware

    Responsibilities:
    1. Reads the last N lines of `global_indices/notifications.md`.
    2. Injects them into the System Prompt (`extra_sections`) so the Agent is aware of Swarm Activity.
    """
    def __init__(self, blackboard_dir: str = ".blackboard", context_lines: int = 20):
        self.blackboard_dir = blackboard_dir
        self.context_lines = context_lines
        self.notification_path = os.path.join(blackboard_dir, "global_indices", "notifications.md")

    def __call__(self, session: AgentSession, next_call: Callable[[AgentSession], Any]) -> Any:
        self._inject_notifications(session)
        return next_call(session)

    def _inject_notifications(self, session: AgentSession):
        if not os.path.exists(self.notification_path):
            return

        try:
            with file_lock(self.notification_path, 'r', fcntl.LOCK_SH) as fd:
                if fd is None:
                    return
                lines = fd.readlines()

            if not lines:
                return

            tail = lines[-self.context_lines:]
            content = "".join(tail)

            max_chars = 5000
            if len(content) > max_chars:
                content = content[-max_chars:]
                content = "...[Older notifications truncated]\n" + content

            header = "## RECENT NOTIFICATIONS (SWARM HEARTBEAT)"
            full_section = f"{header}\nThese are the latest actions performed by other agents. Check if you are mentioned (@Role) or if a topic regarding you is updated.\n\n```text\n{content}\n```"

            idx = -1
            for i, section in enumerate(session.system_config.extra_sections):
                if section.startswith(header):
                    idx = i
                    break

            if idx != -1:
                session.system_config.extra_sections[idx] = full_section
            else:
                session.system_config.extra_sections.append(full_section)

        except Exception as e:
            Logger.error(f"[NotificationMiddleware] Failed to read notifications: {e}")
