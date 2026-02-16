import os
import json
import datetime
import time
from backend.llm.middleware import StrategyMiddleware
from backend.llm.types import AgentSession
from backend.utils.logger import Logger
from typing import Callable, Any


class MailboxMiddleware(StrategyMiddleware):
    """
    Mailbox Middleware (StrategyMiddleware Pattern)

    Intercepts the Agent Loop to check for external messages (Intervention).
    If a message is found in .blackboard/mailboxes/<agent_name>.json:
    1. Injects an Assistant thought to acknowledge the interruption.
    2. Injects the User's message.
    3. Marks the message as 'read'.
    """
    def __init__(self, agent_name: str, blackboard_dir: str = ".blackboard"):
        self.agent_name = agent_name
        self.blackboard_dir = blackboard_dir
        self.mailbox_path = os.path.join(blackboard_dir, "mailboxes", f"{agent_name}.json")

    def __call__(self, session: AgentSession, next_call: Callable[[AgentSession], Any]) -> Any:
        self._check_mailbox(session)
        return next_call(session)

    def _check_mailbox(self, session: AgentSession):
        """
        Check mailbox for new messages and inject them into session history.
        Supports both legacy single-message format and new queue format.
        """
        if not os.path.exists(self.mailbox_path):
            return

        try:
            from src.utils.file_lock import file_lock
            import fcntl

            processed_messages = []

            with file_lock(self.mailbox_path, 'r+', fcntl.LOCK_EX, timeout=5) as fd:
                if fd is None:
                    return

                content = fd.read()
                if not content.strip():
                    return

                try:
                    data = json.loads(content)
                except json.JSONDecodeError:
                    return

                messages = []
                if isinstance(data, dict):
                    messages = [data]
                elif isinstance(data, list):
                    messages = data
                else:
                    return

                has_new_messages = False
                for msg in messages:
                    if msg.get("status") == "unread":
                        user_content = msg.get("content", "")
                        if not user_content:
                            continue

                        Logger.info(f"[Mailbox] Received intervention: {user_content[:50]}...")

                        # 1. Inject Assistant Thought (Self-Reflection)
                        thought_msg = {
                            "role": "assistant",
                            "content": "等等，用户似乎对我的行为有建议，让我看看用户的建议，并在后续遵循用户的建议并继续执行任务。"
                        }
                        session.history.append(thought_msg)

                        # 2. Inject User Message
                        user_msg = {
                            "role": "user",
                            "content": user_content,
                            "metadata": {"source": "mailbox"}
                        }
                        session.history.append(user_msg)

                        # 3. Mark this message as read
                        msg["status"] = "read"
                        msg["read_time"] = datetime.datetime.now().timestamp()

                        has_new_messages = True
                        processed_messages.append(user_content)

                if has_new_messages:
                    fd.seek(0)
                    json.dump(messages, fd, indent=2, ensure_ascii=False)
                    fd.truncate()

            # Log intervention events to JSONL for Monitor UI (outside file lock)
            if processed_messages:
                try:
                    log_dir = os.path.join(self.blackboard_dir, "logs")
                    os.makedirs(log_dir, exist_ok=True)
                    jsonl_path = os.path.join(log_dir, f"{self.agent_name}.jsonl")

                    with open(jsonl_path, 'a', encoding='utf-8') as f:
                        for content in processed_messages:
                            log_entry = {
                                "timestamp": time.time(),
                                "type": "intervention",
                                "data": {
                                    "role": "user",
                                    "content": content
                                }
                            }
                            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
                except Exception as e:
                    Logger.debug(f"[Mailbox] Failed to log intervention: {e}")

        except Exception as e:
            Logger.error(f"[Mailbox] Error processing mailbox: {e}")
