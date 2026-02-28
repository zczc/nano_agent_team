import os
import json
import uuid
import fcntl
from backend.llm.middleware import StrategyMiddleware
from backend.llm.types import AgentSession
from backend.utils.logger import Logger
from typing import Callable, Any, List

from .._mock_chunk import create_mock_tool_chunk
from src.utils.registry_manager import RegistryManager
from src.utils.file_lock import file_lock


class WatchdogGuardMiddleware(StrategyMiddleware):
    """
    Watchdog Guard Middleware (StrategyMiddleware Pattern)

    Intercepts the LLM stream to enforce protocol:
    Rule A: If 'spawn_swarm_agent' but NO central_plan.md or NO 'ask_user' in history ->
            Rename to 'wait' with warning (must create plan + verify first).
    Rule C: If 'write_file'/'edit_file' but NO 'ask_user' in history ->
            Rename to 'wait' with warning (Architect must not do execution work).
    Rule B: If 'finish' but mission status is 'IN_PROGRESS' ->
            Rename to 'wait' with warning (must complete mission first).
            'UNKNOWN' status (no plan/tasks) is allowed to finish.
    End-of-stream: If NO tool calls -> inject ask_user / wait / finish as appropriate.
    Pre-check: Detect dead agents with incomplete tasks and alert Architect.
    """
    EXECUTION_TOOLS = {"write_file", "edit_file"}

    MAX_NO_AGENT_STRIKES = 3

    def __init__(self, agent_name: str = "Assistant", blackboard_dir: str = ".blackboard",
                 critical_tools: List[str] = None, skip_user_verification: bool = False):
        self.agent_name = agent_name
        self.blackboard_dir = blackboard_dir
        self.critical_tools = critical_tools or []
        self.skip_user_verification = skip_user_verification
        self._registry = RegistryManager(blackboard_dir)
        self._no_agent_strike_count = 0

    def _is_anyone_else_running(self) -> bool:
        try:
            registry = self._registry.read()
            for name, info in registry.items():
                if name == self.agent_name:
                    continue
                status = info.get("status")
                if status in ["RUNNING", "IDLE", "STARTING"]:
                    pid = info.get("pid")
                    if pid:
                        try:
                            os.kill(pid, 0)
                            return True
                        except OSError:
                            pass
                    else:
                        return True
            return False
        except Exception as e:
            Logger.debug(f"[Watchdog] Error reading registry: {e}")
            return False

    def _check_mission_status(self) -> str:
        plan_path = os.path.join(self.blackboard_dir, "global_indices", "central_plan.md")
        if not os.path.exists(plan_path):
            return "UNKNOWN"
        try:
            with file_lock(plan_path, 'r', fcntl.LOCK_SH) as fd:
                if fd is None:
                    return "UNKNOWN"
                content = fd.read()
            json_end = content.rfind("```")
            if json_end == -1:
                return "UNKNOWN"
            json_start = content.rfind("```json", 0, json_end)
            if json_start == -1:
                return "UNKNOWN"
            json_str = content[json_start + 7:json_end].strip()
            data = json.loads(json_str)
            tasks = data.get("tasks", [])
            if tasks:
                all_done = all(t.get("status") == "DONE" for t in tasks)
                if not all_done:
                    return "IN_PROGRESS"
            return data.get("status", "UNKNOWN")
        except Exception:
            return "UNKNOWN"

    def _get_dead_agents_with_incomplete_tasks(self) -> List[dict]:
        """Check registry for DEAD agents with incomplete tasks in the plan."""
        results = []
        try:
            registry = self._registry.read()
            plan_path = os.path.join(self.blackboard_dir, "global_indices", "central_plan.md")
            if not os.path.exists(plan_path):
                return results
            with file_lock(plan_path, 'r', fcntl.LOCK_SH) as fd:
                if fd is None:
                    return results
                content = fd.read()
            json_end = content.rfind("```")
            json_start = content.rfind("```json", 0, json_end)
            if json_start == -1 or json_end == -1:
                return results
            plan = json.loads(content[json_start + 7:json_end].strip())
            tasks = plan.get("tasks", [])
            for name, info in registry.items():
                if name == self.agent_name:
                    continue
                if info.get("status") == "DEAD":
                    agent_tasks = [
                        t for t in tasks
                        if name in t.get("assignees", []) and t.get("status") in ("IN_PROGRESS", "PENDING")
                    ]
                    if agent_tasks:
                        results.append({
                            "name": name,
                            "tasks": [{"id": t["id"], "status": t["status"],
                                       "desc": t.get("description", "")[:80]} for t in agent_tasks]
                        })
        except Exception as e:
            Logger.debug(f"[Watchdog] Error checking dead agents: {e}")
        return results

    def __call__(self, session: AgentSession, next_call: Callable[[AgentSession], Any]) -> Any:
        mission_status = self._check_mission_status()

        # PRE-CHECK: Detect dead agents with incomplete tasks and alert Architect
        if mission_status == "IN_PROGRESS" and not self.skip_user_verification:
            dead_agents = self._get_dead_agents_with_incomplete_tasks()
            if dead_agents:
                alert_parts = ["[SYSTEM ALERT: DEAD AGENT DETECTED]"]
                for da in dead_agents:
                    task_info = ", ".join(
                        f"Task #{t['id']}({t['status']}): {t['desc']}" for t in da["tasks"])
                    alert_parts.append(f"  - Agent '{da['name']}' is DEAD with incomplete tasks: {task_info}")
                alert_parts.append(
                    "ACTION REQUIRED: Spawn a replacement agent for these tasks or reassign them.")
                session.system_config.extra_sections.append("\n".join(alert_parts))

        if mission_status != "DONE" and mission_status != "UNKNOWN":
            current_turn = sum(1 for msg in session.history if msg["role"] == "assistant")
            last_injection_turn = -1
            persistence_tag = "[SYSTEM INTERVENTION: PERSISTENCE GUARD]"
            temp_turn_count = 0
            for msg in session.history:
                if msg["role"] == "assistant":
                    temp_turn_count += 1
                if msg["role"] == "user" and persistence_tag in msg.get("content", ""):
                    last_injection_turn = temp_turn_count

            should_inject = False
            if last_injection_turn == -1:
                if current_turn >= 5:
                    should_inject = True
            elif (current_turn - last_injection_turn) >= 5:
                should_inject = True

            if should_inject:
                intervention_msg = (
                    f"### {persistence_tag} (Turn {current_turn})\n"
                    "The mission in `central_plan.md` is NOT yet complete. "
                    "You MUST continue to monitor the agents and coordinate the swarm "
                    "until ALL tasks are marked as 'DONE'. Please take immediate action."
                )
                is_duplicate = (
                    session.history and
                    session.history[-1].get("role") == "user" and
                    persistence_tag in session.history[-1].get("content", "")
                )
                if not is_duplicate:
                    session.history.append({"role": "user", "content": intervention_msg})

        generator = next_call(session)
        return self._guard_stream(generator, session)

    def _guard_stream(self, generator, session):
        has_verified_plan = self.skip_user_verification
        used_tools = set()

        for msg in session.history:
            if msg.get("role") == "tool":
                used_tools.add(msg.get("name"))
                if msg.get("name") == "ask_user":
                    has_verified_plan = True
            elif msg.get("role") == "user":
                if msg.get("metadata", {}).get("from_tool_call") == "ask_user":
                    has_verified_plan = True

        has_tool_calls = False
        replace_mode = False
        replacement_tool_index = -1
        captured_content = ""

        for chunk in generator:
            if not (hasattr(chunk, 'choices') and chunk.choices):
                yield chunk
                continue

            delta = chunk.choices[0].delta

            if hasattr(delta, 'content') and delta.content:
                captured_content += delta.content

            if hasattr(delta, 'tool_calls') and delta.tool_calls:
                modified_tool_calls = []

                for tc in delta.tool_calls:
                    has_tool_calls = True

                    if replace_mode and tc.index == replacement_tool_index:
                        pass

                    elif tc.function and tc.function.name:
                        tool_name = tc.function.name

                        # Rule A: Spawn requires central_plan.md + ask_user
                        if tool_name == "spawn_swarm_agent":
                            plan_path = os.path.join(self.blackboard_dir, "global_indices", "central_plan.md")
                            has_plan = os.path.exists(plan_path)

                            if not has_plan:
                                replace_mode = True
                                replacement_tool_index = tc.index
                                tc.function.name = "wait"
                                warning_msg = (
                                    "[SYSTEM WARNING] PLAN VIOLATION: You attempted to spawn agents "
                                    "but central_plan.md does not exist yet. Required order: "
                                    "create_index(central_plan.md) -> ask_user -> spawn_swarm_agent."
                                )
                                tc.function.arguments = json.dumps({
                                    "duration": 0.1, "wait_for_new_index": False, "reason": warning_msg
                                })
                                modified_tool_calls.append(tc)

                            elif not has_verified_plan:
                                replace_mode = True
                                replacement_tool_index = tc.index
                                tc.function.name = "wait"
                                warning_msg = (
                                    "[SYSTEM WARNING] PLAN VIOLATION: central_plan.md exists but "
                                    "you must call ask_user for approval first. Required order: "
                                    "create_index(central_plan.md) -> ask_user -> spawn_swarm_agent."
                                )
                                tc.function.arguments = json.dumps({
                                    "duration": 0.1, "wait_for_new_index": False, "reason": warning_msg
                                })
                                modified_tool_calls.append(tc)

                            else:
                                modified_tool_calls.append(tc)

                        # Rule C: Unverified Execution
                        elif tool_name in self.EXECUTION_TOOLS and not has_verified_plan:
                            replace_mode = True
                            replacement_tool_index = tc.index
                            tc.function.name = "wait"
                            warning_msg = (
                                f"[SYSTEM WARNING] EXECUTION VIOLATION: You are the Architect and "
                                f"attempted to execute work directly via '{tool_name}'. "
                                "First call 'ask_user' to verify your plan, then use 'spawn_swarm_agent'."
                            )
                            tc.function.arguments = json.dumps({
                                "duration": 0.1, "wait_for_new_index": False, "reason": warning_msg
                            })
                            modified_tool_calls.append(tc)

                        # Rule B: Finish Logic
                        elif tool_name == "finish":
                            mission_status = self._check_mission_status()
                            if mission_status == "IN_PROGRESS":
                                replace_mode = True
                                replacement_tool_index = tc.index
                                tc.function.name = "wait"
                                warning_msg = (
                                    "PROTOCOL VIOLATION: The Mission is NOT marked as DONE in "
                                    "`central_plan.md`. You cannot finish yet."
                                )
                                tc.function.arguments = json.dumps({
                                    "duration": 0.1, "wait_for_new_index": False, "reason": warning_msg
                                })
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

        # 3. END OF STREAM CHECK (No Tool)
        Logger.debug(f"[Watchdog] End of stream. has_tool_calls={has_tool_calls}")
        if not has_tool_calls:
            call_id = f"call_{uuid.uuid4().hex[:8]}"
            mission_status = self._check_mission_status()
            Logger.debug(f"[Watchdog] Mission Status: {mission_status}")

            if mission_status == "DONE":
                Logger.debug("[Watchdog] Auto-finishing (DONE)")
                yield create_mock_tool_chunk(call_id, "finish",
                    json.dumps({"reason": "Auto-finishing as Mission Status is DONE."}))
            elif not has_verified_plan:
                Logger.info(f"[{self.agent_name}] Guard: No tool call, plan not verified. Injecting 'ask_user'.")
                prompt = captured_content.strip() if captured_content.strip() else \
                    "I have drafted a plan. Could you please review and confirm before I proceed?"
                yield create_mock_tool_chunk(call_id, "ask_user", json.dumps({"question": prompt}))
            else:
                anyone_else = self._is_anyone_else_running()
                Logger.debug(f"[Watchdog] Anyone else running: {anyone_else}")

                if anyone_else:
                    self._no_agent_strike_count = 0
                    reason = "MISSION IN PROGRESS: Sub-agents are still working. Waiting for updates."
                    yield create_mock_tool_chunk(call_id, "wait", json.dumps({
                        "duration": 10, "wait_for_new_index": True, "reason": reason
                    }))
                else:
                    self._no_agent_strike_count += 1
                    strikes = self._no_agent_strike_count
                    Logger.info(f"[Watchdog] No agent running, strike {strikes}/{self.MAX_NO_AGENT_STRIKES}")

                    if strikes >= self.MAX_NO_AGENT_STRIKES:
                        self._no_agent_strike_count = 0
                        reason = (
                            f"[DEADLOCK DETECTED] No sub-agent has been running for "
                            f"{strikes} consecutive checks, but the mission is still IN_PROGRESS. "
                            "You MUST now take recovery action:\n"
                            "1. Check which agents are DEAD with incomplete tasks\n"
                            "2. Either spawn replacements or update central_plan.md status to DONE\n"
                            "3. Call finish when done\n"
                            "DO NOT just wait again."
                        )
                    elif strikes == 1:
                        reason = (
                            "MISSION IN PROGRESS: But no sub-agent is working. "
                            f"(Strike {strikes}/{self.MAX_NO_AGENT_STRIKES}) "
                            "Check REAL-TIME SWARM STATUS — if an agent is DEAD with incomplete tasks, "
                            "spawn a REPLACEMENT agent immediately."
                        )
                    else:
                        reason = (
                            "MISSION IN PROGRESS: Still no sub-agent running. "
                            f"(Strike {strikes}/{self.MAX_NO_AGENT_STRIKES}) "
                            "URGENT: Re-spawn the dead agent NOW. "
                            "Next check will trigger forced recovery."
                        )

                    yield create_mock_tool_chunk(call_id, "wait", json.dumps({
                        "duration": 10, "wait_for_new_index": True, "reason": reason
                    }))
