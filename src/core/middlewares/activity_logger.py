import os
import json
import datetime
from backend.llm.middleware import StrategyMiddleware
from backend.llm.types import AgentSession
from backend.utils.logger import Logger
from typing import Callable, Any

from .._mock_chunk import create_mock_tool_chunk


class ActivityLoggerMiddleware(StrategyMiddleware):
    """
    Activity Logger Middleware (Generator Pattern)

    Intercepts the LLM output stream to detect significant tool calls
    and logs them to a shared notification file.
    """
    def __init__(self, agent_name: str, blackboard_dir: str = ".blackboard"):
        self.agent_name = agent_name
        self.blackboard_dir = blackboard_dir
        self.notification_path = os.path.join(blackboard_dir, "global_indices", "notifications.md")
        self.significant_tools = {"update_task", "create_index", "create_resource", "update_index"}

    def __call__(self, session: AgentSession, next_call: Callable[[AgentSession], Any]) -> Any:
        generator = next_call(session)
        return self._intercept_stream(generator)

    def _intercept_stream(self, generator):
        for chunk in generator:
            yield chunk

            if hasattr(chunk, 'choices') and chunk.choices:
                delta = chunk.choices[0].delta
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        if tc.function and tc.function.name:
                            if tc.function.name in self.significant_tools:
                                self._log_activity(tc.function.name, tc.function.arguments)

    def _log_activity(self, tool_name: str, args_str: str):
        import fcntl
        from src.utils.file_lock import file_lock

        try:
            summary = ""
            details = ""

            try:
                args = json.loads(args_str) if args_str else {}
            except (json.JSONDecodeError, TypeError):
                args = {}

            if tool_name == "update_task":
                updates = args.get('updates', {})
                status_change = f"Status->{updates.get('status')}" if 'status' in updates else ""
                comments = updates.get('comments', "")
                if comments:
                    snippet = comments[:100] + "... [truncated]" if len(comments) > 100 else comments
                    details = f" | Comment: '{snippet}'"
                summary = f"Updated Task #{args.get('task_id')}. {status_change}{details}"

            elif tool_name == "create_index":
                content = args.get('content', "")
                snippet = content[:150].replace("\n", " ") + "... [truncated]" if len(content) > 150 else content.replace("\n", " ")
                summary = f"Created Topic '{args.get('name')}': \"{snippet}\""

            elif tool_name == "create_resource":
                content = args.get('content', "")
                snippet = content[:50].replace("\n", " ") + "... [truncated]" if len(content) > 50 else content.replace("\n", " ")
                summary = f"Created Resource '{args.get('filename')}'. Preview: \"{snippet}\""

            elif tool_name == "update_index":
                content = args.get('content', "")
                snippet = content[:150].replace("\n", " ") + "... [truncated]" if len(content) > 150 else content.replace("\n", " ")
                summary = f"Posted to '{args.get('name')}': \"{snippet}\""

            if not summary:
                return

            timestamp = datetime.datetime.now().strftime("%H:%M:%S")
            log_entry = f"[{timestamp}] [{self.agent_name}] {summary}\n"

            if os.path.exists(self.blackboard_dir):
                path = self.notification_path
                with file_lock(path, 'a', fcntl.LOCK_EX, timeout=5) as fd:
                    if fd:
                        fd.write(log_entry)

        except Exception as e:
            Logger.error(f"[ActivityLogger] Failed to log: {e}")
