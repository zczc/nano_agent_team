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


class ArchitectGuardMiddleware(StrategyMiddleware):
    """
    Architect Guard Middleware — enforces protocol for the main coordinating agent.

    Pre-call:
      - Dead Agent Detection: alert Architect about DEAD agents with incomplete tasks.
      - Persistence Guard: remind Architect to keep monitoring every N turns.

    Stream interception:
      Rule A: spawn_swarm_agent requires central_plan.md + ask_user verification.
      Rule B: finish blocked while mission_status == IN_PROGRESS.
      Rule C: write_file/edit_file blocked until Architect has spawned Workers.

    End-of-stream (no tool call):
      - DONE → inject finish
      - No ask_user yet → inject wait + protocol reminder
      - Agents running → inject wait
      - No agents running → strike counting with escalating warnings
    """
    EXECUTION_TOOLS = {"write_file", "edit_file"}
    MAX_NO_AGENT_STRIKES = 3

    def __init__(self, agent_name: str = "Architect", blackboard_dir: str = ".blackboard",
                 skip_user_verification: bool = False):
        self.agent_name = agent_name
        self.blackboard_dir = blackboard_dir
        self.skip_user_verification = skip_user_verification
        self._registry = RegistryManager(blackboard_dir)
        self._no_agent_strike_count = 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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
            Logger.debug(f"[ArchitectGuard] Error reading registry: {e}")
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
                if not all(t.get("status") == "DONE" for t in tasks):
                    return "IN_PROGRESS"
            return data.get("status", "UNKNOWN")
        except Exception:
            return "UNKNOWN"

    def _get_dead_agents_with_incomplete_tasks(self) -> List[dict]:
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
            Logger.debug(f"[ArchitectGuard] Error checking dead agents: {e}")
        return results

    # ------------------------------------------------------------------
    # Pre-call phase
    # ------------------------------------------------------------------

    def __call__(self, session: AgentSession, next_call: Callable[[AgentSession], Any]) -> Any:
        mission_status = self._check_mission_status()

        # Dead Agent Detection
        if mission_status == "IN_PROGRESS":
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

        # Persistence Guard
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

    # ------------------------------------------------------------------
    # Stream interception phase
    # ------------------------------------------------------------------

    def _guard_stream(self, generator, session):
        has_verified_plan = self.skip_user_verification
        has_spawned = False

        for msg in session.history:
            if msg.get("role") == "tool":
                if msg.get("name") == "ask_user":
                    has_verified_plan = True
                if msg.get("name") == "spawn_swarm_agent":
                    has_spawned = True
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
                                tc.function.arguments = json.dumps({
                                    "duration": 0.5, "wait_for_new_index": False,
                                    "reason": "[SYSTEM WARNING] PLAN VIOLATION: You attempted to spawn agents "
                                              "but central_plan.md does not exist yet. Required order: "
                                              "create_index(central_plan.md) -> ask_user -> spawn_swarm_agent."
                                })
                                modified_tool_calls.append(tc)

                            elif not has_verified_plan:
                                replace_mode = True
                                replacement_tool_index = tc.index
                                tc.function.name = "wait"
                                tc.function.arguments = json.dumps({
                                    "duration": 0.5, "wait_for_new_index": False,
                                    "reason": "[SYSTEM WARNING] PLAN VIOLATION: central_plan.md exists but "
                                              "you must call ask_user for approval first. Required order: "
                                              "create_index(central_plan.md) -> ask_user -> spawn_swarm_agent."
                                })
                                modified_tool_calls.append(tc)

                            else:
                                has_spawned = True
                                modified_tool_calls.append(tc)

                        # Rule C: Execution interception (must spawn Workers first)
                        elif tool_name in self.EXECUTION_TOOLS and not has_spawned:
                            replace_mode = True
                            replacement_tool_index = tc.index
                            tc.function.name = "wait"
                            tc.function.arguments = json.dumps({
                                "duration": 0.5, "wait_for_new_index": False,
                                "reason": f"[SYSTEM WARNING] EXECUTION VIOLATION: You are the Architect and "
                                          f"attempted to execute work directly via '{tool_name}'. "
                                          "Architect should delegate work to Workers via spawn_swarm_agent, "
                                          "not execute it directly."
                            })
                            modified_tool_calls.append(tc)

                        # Rule B: Finish blocked while mission IN_PROGRESS
                        elif tool_name == "finish":
                            mission_status = self._check_mission_status()
                            if mission_status == "IN_PROGRESS":
                                replace_mode = True
                                replacement_tool_index = tc.index
                                tc.function.name = "wait"
                                tc.function.arguments = json.dumps({
                                    "duration": 0.5, "wait_for_new_index": False,
                                    "reason": "PROTOCOL VIOLATION: The Mission is NOT marked as DONE in "
                                              "`central_plan.md`. You cannot finish yet."
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

        # ------------------------------------------------------------------
        # End-of-stream phase
        # ------------------------------------------------------------------
        Logger.debug(f"[ArchitectGuard] End of stream. has_tool_calls={has_tool_calls}")
        if not has_tool_calls:
            call_id = f"call_{uuid.uuid4().hex[:8]}"
            mission_status = self._check_mission_status()
            Logger.debug(f"[ArchitectGuard] Mission Status: {mission_status}")

            # 1. Mission DONE → auto-finish
            if mission_status == "DONE":
                Logger.debug("[ArchitectGuard] Auto-finishing (DONE)")
                yield create_mock_tool_chunk(call_id, "finish",
                    json.dumps({"reason": "Auto-finishing as Mission Status is DONE."}))

            # 2. Plan not verified → remind protocol
            elif not has_verified_plan:
                Logger.info(f"[{self.agent_name}] Guard: No tool call, plan not verified. Injecting wait.")
                yield create_mock_tool_chunk(call_id, "wait", json.dumps({
                    "duration": 0.5, "wait_for_new_index": False,
                    "reason": "[PROTOCOL REMINDER] You produced no action this turn. "
                              "Please follow the protocol: create central_plan.md -> "
                              "call ask_user to confirm your plan -> spawn Workers to execute."
                }))

            # 3/4. Mission in progress — monitor loop
            else:
                anyone_else = self._is_anyone_else_running()
                Logger.debug(f"[ArchitectGuard] Anyone else running: {anyone_else}")

                if anyone_else:
                    self._no_agent_strike_count = 0
                    yield create_mock_tool_chunk(call_id, "wait", json.dumps({
                        "duration": 30, "wait_for_new_index": True,
                        "reason": "MISSION IN PROGRESS: Sub-agents are still working. Waiting for updates."
                    }))
                else:
                    self._no_agent_strike_count += 1
                    strikes = self._no_agent_strike_count
                    Logger.info(f"[ArchitectGuard] No agent running, strike {strikes}/{self.MAX_NO_AGENT_STRIKES}")

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
                        "duration": 30, "wait_for_new_index": True, "reason": reason
                    }))
