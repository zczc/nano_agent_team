import os
import json
import uuid
import fcntl
from backend.llm.middleware import StrategyMiddleware
from backend.llm.types import AgentSession
from backend.utils.logger import Logger
from typing import Callable, Any, List

from .._mock_chunk import create_mock_tool_chunk
from src.utils.file_lock import file_lock


class WorkerGuardMiddleware(StrategyMiddleware):
    """
    Worker Guard Middleware — enforces protocol for Worker (sub) agents.

    Stream interception:
      Rule A: spawn_swarm_agent is blocked (Workers cannot spawn other agents).
      Rule B: finish is blocked while the Worker still has IN_PROGRESS tasks
              assigned to it in central_plan.md.

    End-of-stream (no tool call):
      - Has IN_PROGRESS tasks → inject wait + prompt to complete tasks
      - No IN_PROGRESS tasks  → auto-finish
    """

    def __init__(self, agent_name: str = "Worker", blackboard_dir: str = ".blackboard"):
        self.agent_name = agent_name
        self.blackboard_dir = blackboard_dir

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_my_incomplete_tasks(self) -> List[dict]:
        """Return list of tasks assigned to this agent that are still IN_PROGRESS."""
        plan_path = os.path.join(self.blackboard_dir, "global_indices", "central_plan.md")
        if not os.path.exists(plan_path):
            return []
        try:
            with file_lock(plan_path, 'r', fcntl.LOCK_SH) as fd:
                if fd is None:
                    return []
                content = fd.read()
            json_end = content.rfind("```")
            if json_end == -1:
                return []
            json_start = content.rfind("```json", 0, json_end)
            if json_start == -1:
                return []
            json_str = content[json_start + 7:json_end].strip()
            data = json.loads(json_str)
            tasks = data.get("tasks", [])
            return [
                t for t in tasks
                if self.agent_name in t.get("assignees", [])
                and t.get("status") == "IN_PROGRESS"
            ]
        except Exception as e:
            Logger.debug(f"[WorkerGuard] Error reading plan: {e}")
            return []

    # ------------------------------------------------------------------
    # Pre-call / main entry
    # ------------------------------------------------------------------

    def __call__(self, session: AgentSession, next_call: Callable[[AgentSession], Any]) -> Any:
        generator = next_call(session)
        return self._guard_stream(generator)

    # ------------------------------------------------------------------
    # Stream interception
    # ------------------------------------------------------------------

    def _guard_stream(self, generator):
        has_tool_calls = False
        replace_mode = False
        replacement_tool_index = -1

        for chunk in generator:
            if not (hasattr(chunk, 'choices') and chunk.choices):
                yield chunk
                continue

            delta = chunk.choices[0].delta

            if hasattr(delta, 'tool_calls') and delta.tool_calls:
                modified_tool_calls = []

                for tc in delta.tool_calls:
                    has_tool_calls = True

                    if replace_mode and tc.index == replacement_tool_index:
                        pass  # swallow continuation chunks

                    elif tc.function and tc.function.name:
                        tool_name = tc.function.name

                        # Rule A: Workers cannot spawn agents
                        if tool_name == "spawn_swarm_agent":
                            replace_mode = True
                            replacement_tool_index = tc.index
                            tc.function.name = "wait"
                            tc.function.arguments = json.dumps({
                                "duration": 0.5, "wait_for_new_index": False,
                                "reason": "[SYSTEM WARNING] SPAWN VIOLATION: Workers cannot spawn "
                                          "other agents. Only the Architect can use spawn_swarm_agent. "
                                          "Focus on completing your assigned tasks."
                            }, ensure_ascii=False)
                            modified_tool_calls.append(tc)

                        # Rule B: Finish gate — check for incomplete tasks
                        elif tool_name == "finish":
                            incomplete = self._get_my_incomplete_tasks()
                            if incomplete:
                                task_info = ", ".join(
                                    f"Task #{t.get('id')}({t.get('status')})"
                                    for t in incomplete
                                )
                                replace_mode = True
                                replacement_tool_index = tc.index
                                tc.function.name = "wait"
                                tc.function.arguments = json.dumps({
                                    "duration": 0.5, "wait_for_new_index": False,
                                    "reason": f"[FINISH BLOCKED] You still have IN_PROGRESS tasks: "
                                              f"{task_info}. Complete your assigned tasks and update "
                                              f"their status via update_task before finishing."
                                }, ensure_ascii=False)
                                modified_tool_calls.append(tc)
                            else:
                                modified_tool_calls.append(tc)

                        else:
                            modified_tool_calls.append(tc)

                    else:
                        if replace_mode and tc.index == replacement_tool_index:
                            pass
                        else:
                            modified_tool_calls.append(tc)

                if modified_tool_calls:
                    try:
                        chunk.choices[0].delta.tool_calls = modified_tool_calls
                        yield chunk
                    except Exception:
                        yield chunk
            else:
                yield chunk

        # ------------------------------------------------------------------
        # End-of-stream phase
        # ------------------------------------------------------------------
        Logger.debug(f"[WorkerGuard] End of stream. has_tool_calls={has_tool_calls}")
        if not has_tool_calls:
            call_id = f"call_{uuid.uuid4().hex[:8]}"
            incomplete = self._get_my_incomplete_tasks()

            if incomplete:
                task_info = ", ".join(
                    f"Task #{t.get('id')}: {t.get('description', '')[:60]}"
                    for t in incomplete
                )
                Logger.info(f"[WorkerGuard] Worker '{self.agent_name}' has {len(incomplete)} incomplete tasks. Injecting wait.")
                yield create_mock_tool_chunk(call_id, "wait", json.dumps({
                    "duration": 0.5, "wait_for_new_index": False,
                    "reason": f"[TASK INCOMPLETE] You produced no action this turn but still have "
                              f"IN_PROGRESS tasks: {task_info}. "
                              f"Complete your work and update task status via update_task."
                }, ensure_ascii=False))
            else:
                Logger.debug(f"[WorkerGuard] Worker '{self.agent_name}' has no incomplete tasks. Auto-finishing.")
                yield create_mock_tool_chunk(call_id, "finish",
                    json.dumps({"reason": "Auto-finishing: Worker has no remaining IN_PROGRESS tasks."}, ensure_ascii=False))
