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
    Rule A: If 'spawn_swarm_agent' but NO 'ask_user' in history ->
            Rename to 'wait' with warning (must verify plan first).
    Rule C: If 'write_file'/'edit_file' but NO 'ask_user' in history ->
            Rename to 'wait' with warning (Architect must not do execution work).
    Rule B: If 'finish' but mission status is 'IN_PROGRESS' ->
            Rename to 'wait' with warning (must complete mission first).
            'UNKNOWN' status (no plan/tasks) is allowed to finish.
    End-of-stream: If NO tool calls -> inject ask_user / wait / finish as appropriate.
    """
    EXECUTION_TOOLS = {"write_file", "edit_file"}

    def __init__(self, agent_name: str = "Assistant", blackboard_dir: str = ".blackboard", critical_tools: List[str] = None, skip_user_verification: bool = False):
        self.agent_name = agent_name
        self.blackboard_dir = blackboard_dir
        self.critical_tools = critical_tools or []
        self.skip_user_verification = skip_user_verification
        self._registry = RegistryManager(blackboard_dir)

    def _is_anyone_else_running(self) -> bool:
        """
        Checks registry.json to see if any other agent is active.
        An agent is active if its status is 'RUNNING' or 'IDLE'.
        """
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
        Reads central_plan.md and returns the status.
        Returns: "DONE", "IN_PROGRESS", or "UNKNOWN" (if file missing or error)
        """
        plan_path = os.path.join(self.blackboard_dir, "global_indices", "central_plan.md")
        if not os.path.exists(plan_path):
            return "UNKNOWN"

        try:
            with file_lock(plan_path, 'r', fcntl.LOCK_SH) as fd:
                if fd is None:
                    return "UNKNOWN"
                content = fd.read()

            # Simple heuristic extraction of JSON status
            json_end = content.rfind("```")
            if json_end == -1:
                return "UNKNOWN"
            json_start = content.rfind("```json", 0, json_end)
            if json_start == -1:
                return "UNKNOWN"

            json_str = content[json_start + 7:json_end].strip()
            data = json.loads(json_str)

            # Robust Check: If any task is NOT "DONE", the mission is NOT "DONE"
            tasks = data.get("tasks", [])
            if tasks:
                all_done = all(t.get("status") == "DONE" for t in tasks)
                if not all_done:
                    return "IN_PROGRESS"

            return data.get("status", "UNKNOWN")
        except Exception:
            return "UNKNOWN"

    def __call__(self, session: AgentSession, next_call: Callable[[AgentSession], Any]) -> Any:
        # Detect if mission is truly finished
        mission_status = self._check_mission_status()

        if mission_status != "DONE" and mission_status != "UNKNOWN":
            # Detect current turn count (Assistant messages)
            current_turn = sum(1 for msg in session.history if msg["role"] == "assistant")

            # Find the last turn where we injected the PERSISTENCE GUARD message
            last_injection_turn = -1
            persistence_tag = "[SYSTEM INTERVENTION: PERSISTENCE GUARD]"

            # Temporary counter during scan
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
                intervention_msg = f"### {persistence_tag} (Turn {current_turn})\nThe mission in `central_plan.md` is NOT yet complete (some tasks are still PENDING or IN_PROGRESS). You MUST continue to monitor the agents and coordinate the swarm until ALL tasks are marked as 'DONE'. Please take immediate action based on the current plan."

                # Double check to avoid immediate duplicate if the history just updated
                is_duplicate = (
                    session.history and
                    session.history[-1].get("role") == "user" and
                    persistence_tag in session.history[-1].get("content", "")
                )

                if not is_duplicate:
                    session.history.append({
                        "role": "user",
                        "content": intervention_msg
                    })

        # 1. Execute the LLM call to get the generator
        generator = next_call(session)

        # 2. Return a wrapper generator that inspects/modifies the stream
        return self._guard_stream(generator, session)

    # PLACEHOLDER_GUARD_STREAM

    def _guard_stream(self, generator, session):
        # 1. PRE-CALCULATE STATE from session

        # Check plan verification status
        has_verified_plan = self.skip_user_verification  # Evolution mode skips ask_user gate
        # Check critical tool usage (scan entire history)
        used_tools = set()

        for msg in session.history:
            if msg.get("role") == "tool":
                used_tools.add(msg.get("name"))
                if msg.get("name") == "ask_user":
                    has_verified_plan = True
            # Also check for rewritten history (by InteractionRefinementMiddleware)
            elif msg.get("role") == "user":
                if msg.get("metadata", {}).get("from_tool_call") == "ask_user":
                    has_verified_plan = True

        # 2. STREAM INTERCEPTION
        has_tool_calls = False
        replace_mode = False
        replacement_tool_index = -1
        captured_content = ""

        for chunk in generator:
            # Pass through non-choice chunks
            if not (hasattr(chunk, 'choices') and chunk.choices):
                yield chunk
                continue

            delta = chunk.choices[0].delta

            # Capture content for potential ask_user usage
            if hasattr(delta, 'content') and delta.content:
                captured_content += delta.content

            if hasattr(delta, 'tool_calls') and delta.tool_calls:
                modified_tool_calls = []

                for tc in delta.tool_calls:
                    has_tool_calls = True

                    # If already replacing this index, continue suppression
                    if replace_mode and tc.index == replacement_tool_index:
                        pass  # Suppress (don't yield original)

                    elif tc.function and tc.function.name:
                        tool_name = tc.function.name

                        # Rule A: Unverified Spawn -> AskUser (Warning)
                        if tool_name == "spawn_swarm_agent" and not has_verified_plan:
                            replace_mode = True
                            replacement_tool_index = tc.index

                            tc.function.name = "wait"
                            warning_msg = "[SYSTEM WARNING] PLAN VIOLATION: You attempted to spawn agents without user verification. Please create a plan and ask me (the user) for approval first."
                            args = {
                                "duration": 0.1,
                                "wait_for_new_index": False,
                                "reason": warning_msg
                            }
                            tc.function.arguments = json.dumps(args)
                            modified_tool_calls.append(tc)

                        # Rule C: Unverified Execution -> Warning (Architect must not do execution work)
                        elif tool_name in self.EXECUTION_TOOLS and not has_verified_plan:
                            replace_mode = True
                            replacement_tool_index = tc.index

                            tc.function.name = "wait"
                            warning_msg = "[SYSTEM WARNING] EXECUTION VIOLATION: You are the Architect and attempted to execute work directly via '{}'. You must NOT do execution work yourself. First call 'ask_user' to verify your plan, then use 'spawn_swarm_agent' to delegate execution to worker agents.".format(tool_name)
                            args = {
                                "duration": 0.1,
                                "wait_for_new_index": False,
                                "reason": warning_msg
                            }
                            tc.function.arguments = json.dumps(args)
                            modified_tool_calls.append(tc)

                        # Rule B: Finish Logic
                        elif tool_name == "finish":
                            mission_status = self._check_mission_status()

                            if mission_status == "IN_PROGRESS":
                                replace_mode = True
                                replacement_tool_index = tc.index

                                tc.function.name = "wait"
                                warning_msg = "PROTOCOL VIOLATION: The Mission is NOT marked as DONE in `central_plan.md`. You cannot finish yet. Please CHECK `central_plan.md`. If you find that the mission is indeed completed, THEN update its status to 'DONE' first."
                                args = {
                                    "duration": 0.1,
                                    "wait_for_new_index": False,
                                    "reason": warning_msg
                                }
                                tc.function.arguments = json.dumps(args)
                                modified_tool_calls.append(tc)
                            else:
                                # DONE or UNKNOWN -> allow finish
                                modified_tool_calls.append(tc)

                        else:
                            modified_tool_calls.append(tc)

                    else:
                        # Continuation chunk (args only)
                        if replace_mode and tc.index == replacement_tool_index:
                            pass  # Suppress
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
                yield create_mock_tool_chunk(call_id, "finish", json.dumps({"reason": "Auto-finishing as Mission Status is DONE and no other tool was called."}))
            elif not has_verified_plan:
                # Priority: if user hasn't verified the plan yet, ask first
                Logger.info(f"[{self.agent_name}] Guard triggered: No tool call and plan not verified. Injecting 'ask_user'.")
                prompt = captured_content.strip() if captured_content.strip() else "I have drafted a plan. Could you please review and confirm before I proceed?"
                args = {"question": prompt}
                yield create_mock_tool_chunk(call_id, "ask_user", json.dumps(args))
            else:
                anyone_else = self._is_anyone_else_running()
                Logger.debug(f"[Watchdog] Anyone else running: {anyone_else}")

                if anyone_else:
                    reason = "MISSION IN PROGRESS: Sub-agents are still working. Waiting for updates."
                else:
                    reason = "MISSION IN PROGRESS: But no sub-agent is working. Please check the mission status. If the mission is not completed, please create new sub-agent to finish it. If the mission is completed, please update the mission status to DONE."

                args = {
                    "duration": 10,
                    "wait_for_new_index": True,
                    "reason": reason
                }
                yield create_mock_tool_chunk(call_id, "wait", json.dumps(args))
