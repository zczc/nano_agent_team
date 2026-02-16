from backend.llm.middleware import StrategyMiddleware
from backend.llm.types import AgentSession
from backend.utils.logger import Logger
from typing import Callable, Any, Optional


class RequestMonitorMiddleware(StrategyMiddleware):
    """
    Request Monitor Middleware

    Monitors for pending permission requests from agents.
    When a request is found, it either:
    1. Uses a provided callback (TUI/GUI) to display and handle the request.
    2. Falls back to CLI input for approval/denial.

    This middleware runs in the Watchdog's loop (pre-call in session.history),
    ensuring 0 token cost and no pollution.
    """
    def __init__(self, blackboard_dir: str, confirmation_callback: Optional[Callable[[str], bool]] = None):
        self.blackboard_dir = blackboard_dir
        self.confirmation_callback = confirmation_callback
        from src.core.ipc.request_manager import RequestManager
        self.request_manager = RequestManager(blackboard_dir)

    def __call__(self, session: AgentSession, next_call: Callable[[AgentSession], Any]) -> Any:
        self._check_and_handle_requests()
        return next_call(session)

    def _check_and_handle_requests(self):
        """Check for pending requests and handle them interactively."""
        try:
            pending_requests = self.request_manager.list_pending_requests()
            if not pending_requests:
                return

            if not self.confirmation_callback:
                Logger.info(f"[RequestMonitor] Found {len(pending_requests)} pending requests.")

            for req in pending_requests:
                req_id = req["id"]
                agent = req["agent_name"]
                action = req["type"]
                content = req["content"]
                reason = req.get("reason", "No reason provided")

                message_body = (
                    f"**Agent**: `{agent}`\n\n"
                    f"**Action**: {action}\n\n"
                    f"**Command/Content**:\n```\n{content}\n```\n"
                    f"**Reason**: *{reason}*"
                )

                if self.confirmation_callback:
                    approved = self.confirmation_callback(
                        f"### 🛡️ PENDING PERMISSION REQUEST\n\n{message_body}\n\n**Approve this action?**"
                    )

                    if approved:
                        if self.request_manager.update_request_status(req_id, "APPROVED"):
                            pass
                    else:
                        self.request_manager.update_request_status(req_id, "DENIED")

                else:
                    print("\n" + "=" * 60)
                    print("🚨  PENDING PERMISSION REQUESTS DETECTED  🚨")
                    print("=" * 60 + "\n")
                    print(f"REQUEST [{req_id[:8]}...]")
                    print(f"  {message_body}")
                    print("-" * 40)

                    while True:
                        choice = input("  >> Approve this action? (y/n): ").strip().lower()
                        if choice in ['y', 'yes']:
                            if self.request_manager.update_request_status(req_id, "APPROVED"):
                                print("  ✅ APPROVED.")
                            else:
                                print("  ❌ Update failed (File Lock Error?).")
                            break
                        elif choice in ['n', 'no']:
                            if self.request_manager.update_request_status(req_id, "DENIED"):
                                print("  🚫 DENIED.")
                            else:
                                print("  ❌ Update failed.")
                            break
                        else:
                            print("  Please enter 'y' or 'n'.")
                    print("\n")

        except Exception as e:
            Logger.error(f"[RequestMonitor] Error checking requests: {e}")
