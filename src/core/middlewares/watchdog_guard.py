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
                 skip_user_verification: bool = False, is_architect: bool = True):
        self.agent_name = agent_name
        self.blackboard_dir = blackboard_dir
        self.skip_user_verification = skip_user_verification
        self.is_architect = is_architect
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
        """
        Returns one of:
          "DONE"            — mission.status == DONE
          "ALL_TASKS_DONE"  — every task is DONE but mission.status is not yet DONE
          "IN_PROGRESS"     — some tasks still incomplete
          "UNKNOWN"         — no plan or parse error
        """
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
            mission_status = data.get("status", "UNKNOWN")
            if tasks:
                all_done = all(t.get("status") == "DONE" for t in tasks)
                if not all_done:
                    return "IN_PROGRESS"
                if mission_status == "DONE":
                    return "DONE"
                return "ALL_TASKS_DONE"
            return mission_status
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

        # PRE-CHECK: Detect dead agents with incomplete tasks — inject as user message
        dead_agent_tag = "[DEAD AGENT ALERT]"
        if mission_status == "IN_PROGRESS" and self.is_architect:
            dead_agents = self._get_dead_agents_with_incomplete_tasks()
            if dead_agents:
                # Avoid duplicate injection
                already_alerted = (
                    session.history and
                    session.history[-1].get("role") == "user" and
                    dead_agent_tag in session.history[-1].get("content", "")
                )
                if not already_alerted:
                    alert_parts = [f"### {dead_agent_tag}"]
                    for da in dead_agents:
                        task_info = ", ".join(
                            f"Task #{t['id']}({t['status']}): {t['desc']}" for t in da["tasks"])
                        alert_parts.append(f"- Agent **{da['name']}** is DEAD with incomplete tasks: {task_info}")
                    alert_parts.append("")
                    alert_parts.append("")
                    alert_parts.append("**You MUST perform the following cleanup immediately:**")
                    alert_parts.append("1. Call `blackboard(operation=\"read_index\", name=\"central_plan.md\")` to get the current checksum.")
                    alert_parts.append("2. For each incomplete task of the dead agent, call "
                                       "`blackboard(operation=\"update_task\", task_id=X, updates={\"status\": \"PENDING\", \"assignees\": []}, expected_checksum=\"...\")` "
                                       "to reset it.")
                    alert_parts.append("3. Call `spawn_swarm_agent(name=\"...\", role=\"...\")` to launch a replacement Worker.")
                    alert_parts.append("4. Continue monitoring until the replacement agent completes the tasks.")
                    alert_parts.append("")
                    alert_parts.append("Do NOT ignore this alert. Do NOT call `finish` until all tasks are DONE.")
                    session.history.append({"role": "user", "content": "\n".join(alert_parts)})

        if self.is_architect and mission_status == "IN_PROGRESS":
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

        # Recovery scenario: if other agents already exist in registry,
        # the plan was previously verified — skip re-verification for respawns
        if not has_verified_plan and self.is_architect:
            try:
                registry = self._registry.read()
                for name in registry:
                    if name != self.agent_name:
                        has_verified_plan = True
                        Logger.debug(f"[Watchdog] Recovery mode: agent '{name}' found in registry, skipping ask_user requirement")
                        break
            except Exception:
                pass

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
                                tc.function.arguments = json.dumps({
                                    "duration": 0.1, "wait_for_new_index": False,
                                    "reason": "[PLAN VIOLATION] `spawn_swarm_agent` blocked — `central_plan.md` does not exist yet.\n"
                                              "Required order:\n"
                                              "1. Call `blackboard(operation=\"create_index\", name=\"central_plan.md\", content=\"...\")` to create the plan.\n"
                                              "2. Call `ask_user(question=\"...\")` to get user approval.\n"
                                              "3. Then call `spawn_swarm_agent(...)` to launch Workers."
                                }, ensure_ascii=False)
                                modified_tool_calls.append(tc)

                            elif not has_verified_plan:
                                replace_mode = True
                                replacement_tool_index = tc.index
                                tc.function.name = "wait"
                                tc.function.arguments = json.dumps({
                                    "duration": 0.1, "wait_for_new_index": False,
                                    "reason": "[PLAN VIOLATION] `spawn_swarm_agent` blocked — plan exists but not yet approved by user.\n"
                                              "You must call `ask_user(question=\"...\")` to confirm your plan first, "
                                              "then call `spawn_swarm_agent(...)` to launch Workers."
                                }, ensure_ascii=False)
                                modified_tool_calls.append(tc)

                            else:
                                modified_tool_calls.append(tc)

                        # Rule C: Unverified Execution (Architect only — Workers may write freely)
                        elif self.is_architect and tool_name in self.EXECUTION_TOOLS and not has_verified_plan:
                            replace_mode = True
                            replacement_tool_index = tc.index
                            tc.function.name = "wait"
                            tc.function.arguments = json.dumps({
                                "duration": 0.1, "wait_for_new_index": False,
                                "reason": f"[EXECUTION VIOLATION] `{tool_name}` blocked — Architect must not execute work directly.\n"
                                          "Delegate to Workers instead:\n"
                                          "1. Call `spawn_swarm_agent(name=\"...\", role=\"...\")` to launch a Worker.\n"
                                          "2. The Worker will pick up tasks from `central_plan.md` and execute them."
                            }, ensure_ascii=False)
                            modified_tool_calls.append(tc)

                        # Rule B: Finish Logic (Architect only — Workers may finish freely)
                        elif tool_name == "finish":
                            if self.is_architect:
                                mission_status = self._check_mission_status()
                                if mission_status == "IN_PROGRESS":
                                    replace_mode = True
                                    replacement_tool_index = tc.index
                                    tc.function.name = "wait"
                                    tc.function.arguments = json.dumps({
                                        "duration": 0.1, "wait_for_new_index": False,
                                        "reason": "[PROTOCOL VIOLATION] Cannot call `finish` — some tasks in "
                                                  "`central_plan.md` are NOT yet DONE. "
                                                  "Use `blackboard(operation=\"read_index\", name=\"central_plan.md\")` "
                                                  "to check current task statuses, then continue monitoring."
                                    }, ensure_ascii=False)
                                    modified_tool_calls.append(tc)
                                else:
                                    # DONE or ALL_TASKS_DONE — allow finish
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
                        if hasattr(chunk, 'choices') and chunk.choices:
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

            # Workers don't need the Architect monitor loop
            if not self.is_architect:
                # Worker produced no tool call — just auto-finish
                Logger.debug(f"[Watchdog] Worker '{self.agent_name}' produced no tool call. Auto-finishing.")
                yield create_mock_tool_chunk(call_id, "finish",
                    json.dumps({"reason": "Auto-finishing: Worker produced no tool call."}, ensure_ascii=False))
                return

            mission_status = self._check_mission_status()
            Logger.debug(f"[Watchdog] Mission Status: {mission_status}")

            # 1. Mission DONE → auto-finish
            if mission_status == "DONE":
                Logger.debug("[Watchdog] Auto-finishing (DONE)")
                yield create_mock_tool_chunk(call_id, "finish",
                    json.dumps({"reason": "Auto-finishing as Mission Status is DONE."}, ensure_ascii=False))

            # 2. All tasks DONE but mission not marked → prompt closure
            elif mission_status == "ALL_TASKS_DONE":
                Logger.info("[Watchdog] All tasks DONE, prompting mission closure.")
                yield create_mock_tool_chunk(call_id, "wait", json.dumps({
                    "duration": 0.1, "wait_for_new_index": False,
                    "reason": "[ALL TASKS COMPLETED] Every task in `central_plan.md` is now DONE. "
                              "You MUST finalize the mission immediately:\n"
                              "1. Call `blackboard(operation=\"update_index\", name=\"central_plan.md\", ...)` "
                              "to set the mission `status` to `\"DONE\"`.\n"
                              "2. Then call `finish(reason=\"Mission complete.\")` to exit.\n"
                              "Do NOT spawn more agents or wait — just close the mission."
                }, ensure_ascii=False))

            # 3. Plan not verified → inject ask_user
            elif not has_verified_plan:
                Logger.info(f"[{self.agent_name}] Guard: No tool call, plan not verified. Injecting 'ask_user'.")
                prompt = captured_content.strip() if captured_content.strip() else \
                    "I have drafted a plan. Could you please review and confirm before I proceed?"
                yield create_mock_tool_chunk(call_id, "ask_user", json.dumps({"question": prompt}, ensure_ascii=False))

            # 4. Mission in progress — monitor loop
            else:
                anyone_else = self._is_anyone_else_running()
                Logger.debug(f"[Watchdog] Anyone else running: {anyone_else}")

                if anyone_else:
                    self._no_agent_strike_count = 0
                    yield create_mock_tool_chunk(call_id, "wait", json.dumps({
                        "duration": 10, "wait_for_new_index": True,
                        "reason": "Sub-agents are still working. Waiting for blackboard updates. "
                                  "After waking, call `blackboard(operation=\"read_index\", name=\"central_plan.md\")` "
                                  "to check task progress."
                    }, ensure_ascii=False))
                else:
                    self._no_agent_strike_count += 1
                    strikes = self._no_agent_strike_count
                    Logger.info(f"[Watchdog] No agent running, strike {strikes}/{self.MAX_NO_AGENT_STRIKES}")

                    if strikes >= self.MAX_NO_AGENT_STRIKES:
                        self._no_agent_strike_count = 0
                        reason = (
                            f"[DEADLOCK DETECTED] No sub-agent has been running for "
                            f"{strikes} consecutive checks, but tasks are still incomplete.\n"
                            "You MUST take recovery action NOW:\n"
                            "1. Call `blackboard(operation=\"read_index\", name=\"central_plan.md\")` to check task statuses.\n"
                            "2. For each incomplete task of a DEAD agent, call "
                            "`blackboard(operation=\"update_task\", task_id=X, updates={{\"status\": \"PENDING\", \"assignees\": []}})` to reset it.\n"
                            "3. Call `spawn_swarm_agent(...)` to launch a replacement Worker.\n"
                            "4. If all tasks are actually DONE, update mission status to DONE and call `finish(reason=\"...\")` to exit.\n"
                            "DO NOT just call `wait` again."
                        )
                    elif strikes == 1:
                        reason = (
                            f"No sub-agent is working. (Strike {strikes}/{self.MAX_NO_AGENT_STRIKES})\n"
                            "Action required:\n"
                            "1. Check the REAL-TIME SWARM STATUS for DEAD agents.\n"
                            "2. Call `blackboard(operation=\"read_index\", name=\"central_plan.md\")` to check incomplete tasks.\n"
                            "3. If a DEAD agent has incomplete tasks, call `spawn_swarm_agent(...)` to launch a replacement."
                        )
                    else:
                        reason = (
                            f"Still no sub-agent running. (Strike {strikes}/{self.MAX_NO_AGENT_STRIKES})\n"
                            "URGENT: You must act now or a forced recovery will trigger on next check.\n"
                            "Call `spawn_swarm_agent(...)` to re-spawn the dead agent, "
                            "or call `blackboard(operation=\"read_index\", name=\"central_plan.md\")` to re-assess."
                        )

                    yield create_mock_tool_chunk(call_id, "wait", json.dumps({
                        "duration": 10, "wait_for_new_index": True, "reason": reason
                    }, ensure_ascii=False))
