import os
import json
import fcntl
from backend.llm.middleware import StrategyMiddleware
from backend.llm.types import AgentSession
from backend.utils.json_utils import repair_truncated_json
from backend.utils.logger import Logger
from typing import Callable, Any, Tuple, Dict, Optional

from .._mock_chunk import create_mock_tool_chunk
from src.utils.file_lock import file_lock


class DependencyGuardMiddleware(StrategyMiddleware):
    """
    Dependency Guard Middleware (Intercepts tool calls)

    Responsibilities:
    1. Intercept `update_task` or `update_index` to enforce Dependency Rules.
        - Prevent claiming tasks (status="IN_PROGRESS") if dependencies are not "DONE".
    2. Auto-Fix on Read/Write:
        - If task is BLOCKED but dependencies are DONE -> Set to PENDING.
        - If standard task has multiple assignees -> Truncate to first assignee.
    """

    def __init__(self, blackboard_dir: str = ".blackboard"):
        self.blackboard_dir = blackboard_dir
        self.plan_path = os.path.join(blackboard_dir, "global_indices", "central_plan.md")

    def _load_plan(self) -> Tuple[Optional[Dict], Optional[str], Optional[str]]:
        """Helpers to load plan. Returns (json_data, raw_content, error)"""
        if not os.path.exists(self.plan_path):
            return None, None, "Plan not found"

        try:
            with file_lock(self.plan_path, 'r', fcntl.LOCK_SH) as fd:
                if fd is None:
                    return None, None, "Plan not found"
                content = fd.read()

            json_start = content.find("```json")
            if json_start == -1:
                return None, content, "No JSON block"
            json_end = content.rfind("```")
            json_str = content[json_start + 7:json_end].strip()

            data = json.loads(json_str)
            return data, content, None
        except Exception as e:
            return None, None, str(e)

    def _save_plan(self, data: Dict, original_content: str, fd=None):
        """Helper to save plan back. If fd is provided, writes to it directly (for locked writes)."""
        try:
            new_json = json.dumps(data, indent=2, ensure_ascii=False)
            json_start = original_content.find("```json")
            json_end = original_content.rfind("```")

            new_content = original_content[:json_start + 7] + "\n" + new_json + "\n" + original_content[json_end:]

            if fd is not None:
                fd.seek(0)
                fd.write(new_content)
                fd.truncate()
            else:
                with open(self.plan_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
        except Exception as e:
            Logger.error(f"DependencyGuard failed to auto-fix plan: {e}")

    def _auto_fix_plan(self):
        """Rule 1: Auto-Unblock & Rule 2: Single Assignee Enforcement.
        Uses exclusive file lock for atomic read-modify-write."""
        if not os.path.exists(self.plan_path):
            return

        try:
            with file_lock(self.plan_path, 'r+', fcntl.LOCK_EX) as fd:
                if fd is None:
                    return
                content = fd.read()

                json_start = content.find("```json")
                if json_start == -1:
                    return
                json_end = content.rfind("```")
                json_str = content[json_start + 7:json_end].strip()

                try:
                    data = json.loads(json_str)
                except Exception:
                    return

                tasks = data.get("tasks", [])
                modified = False

                task_status_map = {t["id"]: t.get("status", "PENDING") for t in tasks}

                for t in tasks:
                    # Rule 1: Auto-Unblock
                    if t.get("status") == "BLOCKED":
                        deps = t.get("dependencies", [])
                        all_done = all(task_status_map.get(d) == "DONE" for d in deps)
                        if all_done:
                            t["status"] = "PENDING"
                            modified = True
                            Logger.info(f"[DependencyGuard] Auto-Unblocked Task {t['id']}")

                    # Rule 2: Single Assignee for Standard Tasks
                    is_standing = t.get("type", "standard") == "standing"
                    assignees = t.get("assignees", [])
                    if not is_standing and len(assignees) > 1:
                        t["assignees"] = [assignees[0]]
                        modified = True
                        Logger.warning(f"[DependencyGuard] Enforced single assignee for Task {t['id']}")

                if modified:
                    self._save_plan(data, content, fd)
        except FileNotFoundError:
            return
        except Exception as e:
            Logger.error(f"DependencyGuard auto-fix failed: {e}")

    def _check_dependencies(self, task_id: int) -> Tuple[bool, str]:
        """Check if dependencies are met for a task"""
        data, _, err = self._load_plan()
        if err or not data:
            return False, f"Could not load plan: {err}"

        tasks = data.get("tasks", [])
        task = next((t for t in tasks if t["id"] == task_id), None)
        if not task:
            return False, f"Task {task_id} not found"

        deps = task.get("dependencies", [])
        if not deps:
            return True, ""

        for dep_id in deps:
            dep_task = next((t for t in tasks if t["id"] == dep_id), None)
            if not dep_task:
                continue
            if dep_task.get("status") != "DONE":
                return False, f"Dependency Task {dep_id} ('{dep_task.get('description')}') is not DONE (Status: {dep_task.get('status')})"

        return True, ""

    def __call__(self, session: AgentSession, next_call: Callable[[AgentSession], Any]) -> Any:
        # Pre-check: Auto-Fix (Passive)
        self._auto_fix_plan()

        generator = next_call(session)
        return self._guard_stream(generator)

    def _guard_stream(self, generator):
        has_tool_calls = False
        tool_call_buffer = {}  # index -> {name: "", args: ""}

        for chunk in generator:
            if not (hasattr(chunk, 'choices') and chunk.choices):
                yield chunk
                continue

            delta = chunk.choices[0].delta

            if delta.tool_calls:
                has_tool_calls = True

                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_call_buffer:
                        tool_call_buffer[idx] = {"name": "", "args": "", "id": tc.id}

                    if tc.function and tc.function.name:
                        tool_call_buffer[idx]["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        tool_call_buffer[idx]["args"] += tc.function.arguments

                # We do NOT yield tool_call chunks yet. Buffering...
                pass
            else:
                if tool_call_buffer:
                    yield from self._process_and_flush_tools(tool_call_buffer)
                    tool_call_buffer = {}

                yield chunk

        # End of stream flush
        if tool_call_buffer:
            yield from self._process_and_flush_tools(tool_call_buffer)

    # PLACEHOLDER_PROCESS_FLUSH

    def _process_and_flush_tools(self, buffer):
        for idx in sorted(buffer.keys()):
            tc_data = buffer[idx]
            name = tc_data["name"]
            args_str = tc_data["args"]

            is_violation = False
            violation_reason = ""

            if name == "blackboard" or name == "update_task":
                try:
                    repaired_str, args = repair_truncated_json(args_str)
                    if args is not None:
                        args_str = repaired_str

                        if name == "blackboard":
                            op = args.get("operation")
                            if op == "update_task":
                                tid = args.get("task_id")
                                updates = args.get("updates", {})

                                if updates.get("status") == "IN_PROGRESS":
                                    allowed, reason = self._check_dependencies(tid)
                                    if not allowed:
                                        is_violation = True
                                        violation_reason = reason

                                if "assignees" in updates:
                                    data, _, _ = self._load_plan()
                                    if data:
                                        task = next((t for t in data.get("tasks", []) if t["id"] == tid), None)
                                        if task and task.get("type", "standard") != "standing":
                                            if len(updates["assignees"]) > 1:
                                                is_violation = True
                                                violation_reason = "Cannot assign multiple agents to a standard task."
                    else:
                        Logger.warning(f"[DependencyGuard] Malformed JSON in buffer could not be repaired: {args_str}")

                except Exception as e:
                    Logger.debug(f"[DependencyGuard] Error during tool validation: {e}")

            if is_violation:
                Logger.warning(f"[DependencyGuard] Blocked task update: {violation_reason}")

                new_name = "wait"
                new_args = json.dumps({
                    "duration": 5,
                    "wait_for_new_index": False,
                    "reason": f"BLOCKED BY GUARD: {violation_reason}. Please check dependencies."
                })

                yield create_mock_tool_chunk(tc_data["id"], new_name, new_args, idx)

            else:
                yield create_mock_tool_chunk(tc_data["id"], name, args_str, idx)
