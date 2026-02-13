
import os
import signal
import uuid
from backend.llm.middleware import StrategyMiddleware
from backend.llm.types import AgentSession
from backend.utils.json_utils import repair_truncated_json
from backend.utils.logger import Logger
from typing import Callable, Any, List, Optional

from .runtime import RuntimeManager

class ParentProcessMonitorMiddleware(StrategyMiddleware):
    """
    Middleware that monitors the parent process and terminates the current process
    if the parent process ceases to exist OR if the parent agent has finished its task.
    
    Enhanced to check both:
    1. Process existence (PID check)
    2. Agent status in registry.json (DEAD status)
    
    This prevents orphaned child agents from running indefinitely after parent completes.
    """
    def __init__(self, parent_pid: int, agent_name: str = "Agent", blackboard_dir: str = ".blackboard", parent_agent_name: str = "Assistant"):
        """
        Initialize the monitor with the parent PID to watch.
        
        Args:
            parent_pid: The PID of the parent process (Watchdog/Assistant).
                       If None or 0, monitoring is disabled.
            agent_name: Name of the current agent (for cleanup logic).
            blackboard_dir: Blackboard directory (for cleanup logic).
            parent_agent_name: Name of the parent agent in registry (for status check).
        """
        self.parent_pid = parent_pid
        self.agent_name = agent_name
        self.blackboard_dir = blackboard_dir
        self.parent_agent_name = parent_agent_name
        if self.parent_pid and self.parent_pid > 0:
            Logger.info(f"Initialized ParentProcessMonitorMiddleware for {self.agent_name}: watching parent PID {self.parent_pid} (agent: {self.parent_agent_name})")

    def _is_pid_running(self, pid: int) -> bool:
        """Check if a PID exists explicitly."""
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    
    def _is_parent_agent_active(self) -> bool:
        """
        Check if parent agent is still active in registry.json.
        Returns False if parent status is DEAD (either by FinishTool or ctrl+K).
        
        Child agents will terminate when parent is DEAD, regardless of the reason.
        """
        import json
        registry_path = os.path.join(self.blackboard_dir, "registry.json")
        
        if not os.path.exists(registry_path):
            return True  # Assume active if registry doesn't exist yet
        
        try:
            from src.utils.file_lock import file_lock
            import fcntl
            
            # Use shared lock for reading
            with file_lock(registry_path, 'r', fcntl.LOCK_SH, timeout=2) as fd:
                if not fd:
                    return True  # Assume active if can't read
                
                content = fd.read()
                if not content:
                    return True
                
                registry = json.loads(content)
                
                # Check parent agent status
                if self.parent_agent_name in registry:
                    parent_info = registry[self.parent_agent_name]
                    status = parent_info.get("status", "UNKNOWN")
                    
                    # Only terminate if parent is DEAD (task completed)
                    # IDLE means paused by user (ctrl+K), children should continue
                    if status == "DEAD":
                        return False
                
                return True
                
        except Exception as e:
            Logger.debug(f"Failed to check parent agent status: {e}")
            return True  # Assume active on error

    def __call__(self, session: AgentSession, next_call: Callable[[AgentSession], Any]) -> Any:
        """
        Check parent process and agent status before proceeding with the pipeline.
        Terminates child agent if:
        1. Parent process PID no longer exists, OR
        2. Parent agent status is DEAD in registry
        """
        if self.parent_pid and self.parent_pid > 0:
            # Check 1: Process existence
            if not self._is_pid_running(self.parent_pid):
                Logger.warning(f"[{self.agent_name}] Parent process {self.parent_pid} died. Terminating self.")
                self._terminate_self("Parent process died")
            
            # Check 2: Agent status in registry
            if not self._is_parent_agent_active():
                Logger.warning(f"[{self.agent_name}] Parent agent '{self.parent_agent_name}' is DEAD. Terminating self.")
                self._terminate_self(f"Parent agent '{self.parent_agent_name}' finished")
        
        return next_call(session)
    
    def _terminate_self(self, reason: str):
        """Perform cleanup and terminate the current process."""
        try:
            RuntimeManager.cleanup_agent(
                name=self.agent_name, 
                blackboard_dir=self.blackboard_dir, 
                reason=reason
            )
        except Exception as e:
            Logger.error(f"Cleanup failed during termination: {e}")
        
        # Kill self immediately
        Logger.info(f"[{self.agent_name}] Self-terminating: {reason}")
        os.kill(os.getpid(), signal.SIGKILL)



class WatchdogGuardMiddleware(StrategyMiddleware):
    """
    Watchdog Guard Middleware (StrategyMiddleware Pattern)
    
    Intercepts the LLM stream to enforce protocol:
    1. If Response has NO tool calls -> Inject a fake 'protocol_enforcement_alert' tool call.
    2. If Response has 'spawn_swarm_agent' but NO 'ask_user' in history -> 
       Intercept stream, RENAME tool to 'protocol_enforcement_alert', and replace args.
    3. If Response has 'finish_tool' but NOT ALL critical_tools used ->
       Intercept stream, RENAME tool to 'protocol_enforcement_alert', and replace args.
    """
    def __init__(self, agent_name: str = "Assistant", blackboard_dir: str = ".blackboard", critical_tools: List[str] = None):
        self.agent_name = agent_name
        self.blackboard_dir = blackboard_dir
        self.critical_tools = critical_tools or []

    def _is_anyone_else_running(self) -> bool:
        """
        Checks registry.json to see if any other agent is active.
        An agent is active if its status is 'RUNNING' or 'IDLE'.
        """
        registry_path = os.path.join(self.blackboard_dir, "registry.json")
        if not os.path.exists(registry_path):
            return False
            
        try:
            from src.utils.file_lock import file_lock
            import fcntl
            
            # Use shared lock for reading
            with file_lock(registry_path, 'r', fcntl.LOCK_SH, timeout=5) as fd:
                if not fd:
                    return False
                
                content = fd.read()
                if not content:
                    return False
                    
                registry = json.loads(content)
            
            for name, info in registry.items():
                if name == self.agent_name:
                    continue
                
                status = info.get("status")
                if status in ["RUNNING", "IDLE", "STARTING"]: # Also include STARTING
                    # Optional: Verify PID is actually alive
                    pid = info.get("pid")
                    if pid:
                        try:
                            # Check if process exists
                            os.kill(pid, 0)
                            return True
                        except OSError:
                            pass
                    else:
                        return True # Trust status if no PID
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
            with open(plan_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Simple heuristic extraction of JSON status
            import json
            # Find last JSON block
            json_end = content.rfind("```")
            if json_end == -1: return "UNKNOWN"
            json_start = content.rfind("```json", 0, json_end)
            if json_start == -1: return "UNKNOWN"
            
            json_str = content[json_start+7:json_end].strip()
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
            
            # Injection logic: only every 5 turns
            # We also allow injection if it's the very first time (last_injection_turn == -1)
            # and current_turn >= 5 (to avoid nagging too early) or 1? 
            # The user said: "if current_turn - last_albert_turn >= 5"
            # If never injected, current_turn - (-1) is always > 5 eventually.
            # Let's use a logic that ensures we don't spam.
            
            should_inject = False
            if last_injection_turn == -1:
                 # First time: inject if we've reached at least a few turns to give it a chance
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

    def _guard_stream(self, generator, session):
        # 1. PRE-CALCULATE STATE from session
        
        # Check plan verification status
        has_verified_plan = False
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
        
        import uuid
        
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
                    # Logic to identify if we need to replace THIS tool call
                    
                    # If already replacing this index, continue suppression
                    if replace_mode and tc.index == replacement_tool_index:
                        pass # Suppress (don't yield original)

                    elif tc.function and tc.function.name:
                        # New tool call (or name packet). Check rules.
                        tool_name = tc.function.name
                        
                        # Rule A: Unverified Spawn -> AskUser (Warning)
                        if tool_name == "spawn_swarm_agent" and not has_verified_plan:
                            replace_mode = True
                            replacement_tool_index = tc.index
                            
                            # Rename to ask_user
                            tc.function.name = "wait"
                            warning_msg = "[SYSTEM WARNING] PLAN VIOLATION: You attempted to spawn agents without user verification. Please create a plan and ask me (the user) for approval first."
                            # We fake the arguments to be a valid JSON for wait
                            args = {
                                    "duration": 0.1, 
                                    "wait_for_new_index": False, 
                                    "reason": warning_msg
                                }
                            import json
                            tc.function.arguments = json.dumps(args)
                            modified_tool_calls.append(tc)
                            
                        # Rule B: Finish Logic
                        elif tool_name == "finish":
                            # Check Mission Status
                            mission_status = self._check_mission_status()
                            
                            if mission_status == "DONE":
                                # Pass-through
                                modified_tool_calls.append(tc)
                            else:
                                # BLOCK -> Wait
                                replace_mode = True
                                replacement_tool_index = tc.index
                                
                                # Rename to wait
                                tc.function.name = "wait"
                                warning_msg = "PROTOCOL VIOLATION: The Mission is NOT marked as DONE in `central_plan.md`. You cannot finish yet. Please CHECK `central_plan.md`. If you find that the mission is indeed completed, THEN update its status to 'DONE' first."
                                
                                # We fake the arguments to be a valid JSON for wait
                                args = {
                                    "duration": 0.1, 
                                    "wait_for_new_index": False, 
                                    "reason": warning_msg
                                }
                                import json
                                tc.function.arguments = json.dumps(args)
                                modified_tool_calls.append(tc)
                                
                        else:
                            # Allowed
                            modified_tool_calls.append(tc)
                            
                    else:
                        # Continuation chunk (args only)
                        if replace_mode and tc.index == replacement_tool_index:
                            pass # Suppress
                        else:
                            modified_tool_calls.append(tc)
                            
                if modified_tool_calls:
                    try:
                        chunk.choices[0].delta.tool_calls = modified_tool_calls
                        yield chunk
                    except:
                        yield chunk
            else:
                yield chunk
        
        # 3. END OF STREAM CHECK (No Tool)
        Logger.debug(f"[Watchdog] End of stream. has_tool_calls={has_tool_calls}")
        if not has_tool_calls:
            import json
            call_id = f"call_{uuid.uuid4().hex[:8]}"
            mission_status = self._check_mission_status()
            Logger.debug(f"[Watchdog] Mission Status: {mission_status}")
            
            if mission_status == "DONE":
                # Auto-Finish
                Logger.debug("[Watchdog] Auto-finishing (DONE)")
                yield self._create_mock_tool_chunk(call_id, "finish", json.dumps({"reason": "Auto-finishing as Mission Status is DONE and no other tool was called."}))
            else:
                # Mission NOT DONE. Decide: Wait or AskUser?
                anyone_else = self._is_anyone_else_running()
                Logger.debug(f"[Watchdog] Anyone else running: {anyone_else}")
                
                if anyone_else:
                    # Force Wait
                    reason = "MISSION IN PROGRESS: Sub-agents are still working. Waiting for updates."
                    args = {
                        "duration": 10, 
                        "wait_for_new_index": True, 
                        "reason": reason
                    }
                    yield self._create_mock_tool_chunk(call_id, "wait", json.dumps(args))
                else:
                    # Force Wait
                    reason = "MISSION IN PROGRESS: But no sub-agent is working. Please check the mission status. If the mission is not completed, please create new sub-agent to finish it. If the mission is completed, please update the mission status to DONE."
                    args = {
                        "duration": 10, 
                        "wait_for_new_index": True, 
                        "reason": reason
                    }
                    yield self._create_mock_tool_chunk(call_id, "wait", json.dumps(args))
                    # # No sub-agents. Ask User.
                    # question = captured_content.strip() if captured_content.strip() else "Mission is not yet DONE and no sub-agents are active. How should I proceed?"
                    # Logger.debug(f"[Watchdog] Injecting ask_user: {question[:50]}...")
                    # yield self._create_mock_tool_chunk(call_id, "ask_user", json.dumps({"question": question}))

    def _create_mock_tool_chunk(self, id, name, args):
        from types import SimpleNamespace
        import time
        
        # Construct a realistic OpenAI-compatible ChatCompletionChunk
        # 1. Delta
        delta_kwargs = {"content": None}
        if id or name or args:
            tc = SimpleNamespace(index=0)
            if id: tc.id = id
            if name: 
                tc.type = 'function'
                tc.function = SimpleNamespace(name=name, arguments="")
            if args:
                if not hasattr(tc, 'function'): tc.function = SimpleNamespace(arguments="")
                tc.function.arguments = args
            
            delta_kwargs["tool_calls"] = [tc]
            
        # 2. Choice
        choice = SimpleNamespace(
            index=0,
            delta=SimpleNamespace(**delta_kwargs),
            finish_reason=None
        )
        
        # 3. Top-level Chunk
        chunk = SimpleNamespace(
            id=f"chatcmpl-mock-{int(time.time())}",
            object="chat.completion.chunk",
            created=int(time.time()),
            model="mock-guardian-model",
            choices=[choice]
        )
        
        return chunk


import json
from typing import Tuple, Dict

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
            with open(self.plan_path, 'r', encoding='utf-8') as f:
                content = f.read()
                
            # Parse JSON from markdown
            json_start = content.find("```json")
            if json_start == -1: return None, content, "No JSON block"
            json_end = content.rfind("```")
            json_str = content[json_start+7:json_end].strip()
            
            data = json.loads(json_str)
            return data, content, None
        except Exception as e:
            return None, None, str(e)

    def _save_plan(self, data: Dict, original_content: str):
        """Helper to save plan back atomically"""
        try:
            # Reconstruct
            new_json = json.dumps(data, indent=2, ensure_ascii=False)
            json_start = original_content.find("```json")
            json_end = original_content.rfind("```")
            
            new_content = original_content[:json_start+7] + "\n" + new_json + "\n" + original_content[json_end:]
            
            with open(self.plan_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
        except Exception as e:
            Logger.error(f"DependencyGuard failed to auto-fix plan: {e}")

    def _auto_fix_plan(self):
        """Rule 1: Auto-Unblock & Rule 2: Single Assignee Enforcement"""
        data, content, err = self._load_plan()
        if err or not data: return
        
        tasks = data.get("tasks", [])
        modified = False
        
        # Build dependency map
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
            self._save_plan(data, content)

    def _check_dependencies(self, task_id: int) -> Tuple[bool, str]:
        """Check if dependencies are met for a task"""
        data, _, err = self._load_plan()
        if err or not data: return False, f"Could not load plan: {err}"
        
        tasks = data.get("tasks", [])
        task = next((t for t in tasks if t["id"] == task_id), None)
        if not task: return False, f"Task {task_id} not found"
        
        deps = task.get("dependencies", [])
        if not deps: return True, ""
        
        # Check all deps are DONE
        for dep_id in deps:
            dep_task = next((t for t in tasks if t["id"] == dep_id), None)
            if not dep_task: continue
            if dep_task.get("status") != "DONE":
                return False, f"Dependency Task {dep_id} ('{dep_task.get('description')}') is not DONE (Status: {dep_task.get('status')})"
                
        return True, ""

    def __call__(self, session: AgentSession, next_call: Callable[[AgentSession], Any]) -> Any:
        # Pre-check: Auto-Fix (Passive)
        self._auto_fix_plan()
        
        generator = next_call(session)
        return self._guard_stream(generator)

    def _guard_stream(self, generator):
        import json
        
        has_tool_calls = False
        tool_call_buffer = {} # index -> {name: "", args: ""}
        
        for chunk in generator:
            if not (hasattr(chunk, 'choices') and chunk.choices):
                yield chunk
                continue
            
            delta = chunk.choices[0].delta
            
            if delta.tool_calls:
                has_tool_calls = True
                
                # Accumulate args
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
                # Content or other chunk.
                # If we have buffered tool calls, we must now flush them (processed) before yielding content.
                if tool_call_buffer:
                    yield from self._process_and_flush_tools(tool_call_buffer)
                    tool_call_buffer = {}
                
                yield chunk
        
        # End of stream flush
        if tool_call_buffer:
            yield from self._process_and_flush_tools(tool_call_buffer)

    def _process_and_flush_tools(self, buffer):
        # Check constraints
        for idx in sorted(buffer.keys()):
            tc_data = buffer[idx]
            name = tc_data["name"]
            args_str = tc_data["args"]
            
            is_violation = False
            violation_reason = ""
            
            if name == "blackboard" or name == "update_task": 
                # Parse args
                try:
                    import json
                    repaired_str, args = repair_truncated_json(args_str)
                    if args is not None:
                        # Use repaired args for validation and output
                        args_str = repaired_str
                        
                        if name == "blackboard":
                            op = args.get("operation")
                            if op == "update_task":
                                tid = args.get("task_id")
                                updates = args.get("updates", {})
                                
                                # Check Claiming (Setting IN_PROGRESS)
                                if updates.get("status") == "IN_PROGRESS":
                                    allowed, reason = self._check_dependencies(tid)
                                    if not allowed:
                                        is_violation = True
                                        violation_reason = reason
                                        
                                # Check Multi-Assignee on Standard
                                if "assignees" in updates:
                                    data, _, _ = self._load_plan()
                                    if data:
                                        task = next((t for t in data.get("tasks",[]) if t["id"] == tid), None)
                                        if task and task.get("type", "standard") != "standing":
                                            if len(updates["assignees"]) > 1:
                                                is_violation = True
                                                violation_reason = "Cannot assign multiple agents to a standard task."
                    else:
                        Logger.warning(f"[DependencyGuard] Malformed JSON in buffer could not be repaired: {args_str}")

                except Exception as e:
                    Logger.debug(f"[DependencyGuard] Error during tool validation: {e}")
                    pass 
            
            # Construct Chunks to yield
            if is_violation:
                Logger.warning(f"[DependencyGuard] Blocked task update: {violation_reason}")
                
                # REPLACEMENT: wait tool
                new_name = "wait"
                new_args = json.dumps({
                    "duration": 5,
                    "wait_for_new_index": False,
                    "reason": f"BLOCKED BY GUARD: {violation_reason}. Please check dependencies."
                })
                
                yield self._create_mock_chunk(tc_data["id"], new_name, new_args, idx)
                
            else:
                yield self._create_mock_chunk(tc_data["id"], name, args_str, idx)

    def _create_mock_chunk(self, id, name, args, idx):
        from types import SimpleNamespace
        import time
        
        tc = SimpleNamespace(index=idx, id=id, type='function', function=SimpleNamespace(name=name, arguments=args))
        choice = SimpleNamespace(index=0, delta=SimpleNamespace(tool_calls=[tc], content=None), finish_reason=None)
        chunk = SimpleNamespace(
            id=f"chatcmpl-guard-{idx}",
            object="chat.completion.chunk",
            created=int(time.time()),
            model="guard",
            choices=[choice]
        )
        return chunk


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
        # Pre-check: Mailbox
        # We process mailbox BEFORE yielding the generator
        
        self._check_mailbox(session)
        
        return next_call(session)

    def _check_mailbox(self, session: AgentSession):
        """
        Check mailbox for new messages and inject them into session history.
        Supports both legacy single-message format and new queue format.
        """
        import json
        import datetime
        import time
        
        if not os.path.exists(self.mailbox_path):
            return
            
        try:
            from src.utils.file_lock import file_lock
            import fcntl
            
            processed_messages = []  # Track messages for logging
            
            # Use exclusive lock for read-modify-write
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
                
                # Support both formats: single message (dict) or queue (list)
                messages = []
                if isinstance(data, dict):
                    # Legacy single-message format
                    messages = [data]
                elif isinstance(data, list):
                    # Queue format
                    messages = data
                else:
                    return
                
                # Process all unread messages
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
                
                # Write back updated messages if any were marked as read
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


class SwarmStateMiddleware(StrategyMiddleware):
    """
    Swarm State Middleware (StrategyMiddleware Pattern)
    
    Injects the REAL-TIME SWARM STATUS into the System Prompt before every LLM call.
    1. Reads registry.json
    2. Verifies PIDs (Dead/Alive)
    3. Injects full registry content into session.system_config.extra_sections
    4. Ensures no duplicate history (Updates in-place if possible or appends unique section)
    """
    def __init__(self, blackboard_dir: str = ".blackboard"):
        self.blackboard_dir = blackboard_dir

    def __call__(self, session: AgentSession, next_call: Callable[[AgentSession], Any]) -> Any:
        # Pre-check: Inject Status
        self._inject_status(session)
        return next_call(session)

    def _inject_status(self, session: AgentSession):
        import json
        import os
        from src.utils.file_lock import file_lock
        import fcntl
        import time
        
        registry_path = os.path.join(self.blackboard_dir, "registry.json")
        if not os.path.exists(registry_path):
            return

        try:
            # 1. Read & Update Registry with File Lock
            with file_lock(registry_path, 'r+', fcntl.LOCK_EX, timeout=10) as fd:
                if fd is None:
                    return
                
                content = fd.read()
                try:
                    registry = json.loads(content) if content else {}
                except json.JSONDecodeError:
                    return

                modified = False
                report_data = {}
                
                # 2. Verify PIDs and Sync Status
                for name, info in registry.items():
                    pid = info.get("pid")
                    is_alive = False
                    if pid:
                        try:
                            os.kill(pid, 0)
                            is_alive = True
                        except OSError:
                            pass
                    
                    # Sync verified status back to registry if it's dead but was marked active
                    if not is_alive:
                        info["status"] = "DEAD"
                        info["exit_time"] = time.time()
                        modified = True
                    
                    # Annotate our in-memory report for the prompt
                    info_copy = info.copy()
                    info_copy["verified_status"] = "ALIVE" if is_alive else "DEAD"
                    report_data[name] = info_copy

                # 3. Write Back if modified
                if modified:
                    fd.seek(0)
                    json.dump(registry, fd, indent=2, ensure_ascii=False)
                    fd.truncate()

            # 4. Format Output for System Prompt
            # Limit the number of agents reported to avoid context overflow
            # max_agents_in_prompt = 15
            # if len(report_data) > max_agents_in_prompt:
            #     # Keep active agents, truncate the rest
            #     sorted_agents = sorted(report_data.items(), key=lambda x: x[1].get("status") != "RUNNING")
            #     report_data = dict(sorted_agents[:max_agents_in_prompt])
            #     report_data["..."] = "Other agents truncated for context length."

            status_text = json.dumps(report_data, indent=2, ensure_ascii=False)
            
            header = "## REAL-TIME SWARM STATUS (REGISTRY)"
            full_section = f"{header}\nThis is the current state of all agents in the swarm, synced from the registry.\nVerified by Middleware (PID Check).\n\n```json\n{status_text}\n```"

            # 5. Inject into System Config
            idx = -1
            for i, section in enumerate(session.system_config.extra_sections):
                if section.startswith(header):
                    idx = i
                    break
            
            if idx != -1:
                session.system_config.extra_sections[idx] = full_section
            else:
                # Place at the very top of extra sections for visibility
                session.system_config.extra_sections.insert(0, full_section)
                
        except Exception as e:
            from backend.utils.logger import Logger
            Logger.error(f"[SwarmStateMiddleware] Failed to inject/update status: {e}")

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
            # Efficiently read last N lines
            # For simplicity, we read all and slice, assuming file isn't massive yet.
            # In production, use `tail` logic.
            with open(self.notification_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            if not lines:
                return

            tail = lines[-self.context_lines:]
            content = "".join(tail)
            
            # Limit total characters (approx 5000 chars max)
            max_chars = 5000
            if len(content) > max_chars:
                content = content[-max_chars:]
                content = "...[Older notifications truncated]\n" + content

            header = "## RECENT NOTIFICATIONS (SWARM HEARTBEAT)"
            full_section = f"{header}\nThese are the latest actions performed by other agents. Check if you are mentioned (@Role) or if a topic regarding you is updated.\n\n```text\n{content}\n```"
            
            # Inject or Replace
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


class ActivityLoggerMiddleware(StrategyMiddleware):
    """
    Activity Logger Middleware
    
    Responsibilities:
    1. Intercepts `tool_result` events.
    2. If the tool call was a "State Changing" action (update_task, create_resource, create_index),
       automatically appends a Work Report to `global_indices/notifications.md`.
    """
    def __init__(self, agent_name: str, blackboard_dir: str = ".blackboard"):
        self.agent_name = agent_name
        self.blackboard_dir = blackboard_dir
        self.notification_path = os.path.join(blackboard_dir, "global_indices", "notifications.md")
        # Tools that are worth logging
        self.significant_tools = {"update_task", "create_resource", "create_index", "update_index"}

    def __call__(self, session: AgentSession, next_call: Callable[[AgentSession], Any]) -> Any:
        generator = next_call(session)
        return self._intercept_stream(generator)

    def _intercept_stream(self, generator):
        # We need to capture tool calls AND their results (which implies waiting for the next turn loop in a real engine)
        # However, StrategyMiddleware wraps the `generate` call. It sees the `tool_call` emitted by the model.
        # It DOES NOT see the `tool_result` because that happens in the Engine loop, outside this generator.
        #
        # WAIT: The `AgentEngine` structure in `agent_wrapper.py` executes tools *after* the generator yields.
        # This Middleware runs *inside* `engine.run`'s generator pipeline.
        # So we can only see the Model *requesting* a tool, not the result.
        #
        # But we can log "Agent X IS ATTEMPTING to update task...".
        # Better yet, let's log *after* the tool call is generated.
        #
        # Actually, `blackboard_tool` itself handles the writing. 
        # Middleware here is tricky for *auto-logging results* unless we hook into the Tool execution itself.
        #
        # Alternative: We log the *Intention* (Tool Call).
        # "Agent X calling update_task(...)".
        
        # Let's keep it simple: We log the tool call *intention*. 
        # If it fails, the error log will appear in the next turn eventually (not in notification).
        # For a "Notification Stream", intent ("I am updating the plan") is seemingly good enough.
        
        for chunk in generator:
            yield chunk
            
            # Inspect for tool calls
            if hasattr(chunk, 'choices') and chunk.choices:
                delta = chunk.choices[0].delta
                if delta.tool_calls:
                   for tc in delta.tool_calls:
                       if tc.function and tc.function.name:
                           if tc.function.name in self.significant_tools:
                               self._log_activity(tc.function.name, tc.function.arguments)

    def _log_activity(self, tool_name: str, args_str: str):
        import datetime
        import json
        import fcntl
        from src.utils.file_lock import file_lock
        
        try:
            # Parse args specific to tool to make a nice message
            summary = ""
            details = ""
            
            try:
                args = json.loads(args_str) if args_str else {}
            except:
                args = {}

            if tool_name == "update_task":
                updates = args.get('updates', {})
                status_change = f"Status->{updates.get('status')}" if 'status' in updates else ""
                comments = updates.get('comments', "")
                if comments:
                     # Truncate content
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
                # Often update_index replaces whole content, so snippet might be just the start
                snippet = content[:150].replace("\n", " ") + "... [truncated]" if len(content) > 150 else content.replace("\n", " ")
                summary = f"Posted to '{args.get('name')}': \"{snippet}\""
            
            if not summary:
                return

            timestamp = datetime.datetime.now().strftime("%H:%M:%S")
            # Work Report Style: focus on what was done (summary), not the tool name
            log_entry = f"[{timestamp}] [{self.agent_name}] {summary}\n"
            
            # Shared File Append
            # We use the file_lock utility we created earlier!
            if os.path.exists(self.blackboard_dir): # Ensure dir exists
                 path = self.notification_path
                 with file_lock(path, 'a', fcntl.LOCK_EX, timeout=5) as fd:
                    if fd:
                        fd.write(log_entry)
                        
        except Exception as e:
            Logger.error(f"[ActivityLogger] Failed to log: {e}")


class RequestMonitorMiddleware(StrategyMiddleware):
    """
    Request Monitor Middleware
    
    Responsibilities:
    1. Checks for pending permission requests from sub-agents via RequestManager.
    2. Intercepts execution BEFORE LLM call to ask user for approval directly in TUI/CLI.
    3. Updates request status, allowing sub-agents to proceed.
    4. Does NOT modify LLM context (session.history), ensuring 0 token cost and no pollution.
    """
    def __init__(self, blackboard_dir: str, confirmation_callback: Optional[Callable[[str], bool]] = None):
        self.blackboard_dir = blackboard_dir
        self.confirmation_callback = confirmation_callback
        # Import lazily to avoid circular imports
        from src.core.ipc.request_manager import RequestManager
        self.request_manager = RequestManager(blackboard_dir)

    def __call__(self, session: AgentSession, next_call: Callable[[AgentSession], Any]) -> Any:
        # Pre-check: Handle Requests
        self._check_and_handle_requests()
        return next_call(session)

    def _check_and_handle_requests(self):
        """
        Check for pending requests and handle them interactively.
        """
        try:
            pending_requests = self.request_manager.list_pending_requests()
            if not pending_requests:
                return
            
            # Simple logging if we are about to interrupt
            if not self.confirmation_callback:
                 Logger.info(f"[RequestMonitor] Found {len(pending_requests)} pending requests.")
                 
            for req in pending_requests:
                req_id = req["id"]
                agent = req["agent_name"]
                action = req["type"]
                content = req["content"]
                reason = req.get("reason", "No reason provided")
                
                # Format the message for the user using Markdown for better visual structure
                message_body = (
                    f"**Agent**: `{agent}`\n\n"
                    f"**Action**: {action}\n\n"
                    f"**Command/Content**:\n```\n{content}\n```\n"
                    f"**Reason**: *{reason}*"
                )
                
                if self.confirmation_callback:
                    # Use provided callback (TUI/GUI)
                    # This will handle the UI display and waiting
                    approved = self.confirmation_callback(
                        f"### 🛡️ PENDING PERMISSION REQUEST\n\n{message_body}\n\n**Approve this action?**"
                    )
                    
                    if approved:
                         if self.request_manager.update_request_status(req_id, "APPROVED"):
                             pass # Logging handled by manager or caller
                    else:
                         self.request_manager.update_request_status(req_id, "DENIED")
                         
                else:
                    # Fallback to CLI
                    print("\n" + "="*60)
                    print("🚨  PENDING PERMISSION REQUESTS DETECTED  🚨")
                    print("="*60 + "\n")
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

class SwarmAgentGuardMiddleware(StrategyMiddleware):
    """
    Swarm Agent Guard Middleware (StrategyMiddleware Pattern)
    
    Ensures that a Swarm Worker Agent does not exit or idle without intent:
    1. If the LLM response contains NO tool calls, it automatically injects 
       a 'wait' tool call to force the agent to continue its task or finish.
    """
    def __init__(self, agent_name: str = "Agent", blackboard_dir: str = ".blackboard"):
        self.agent_name = agent_name
        self.blackboard_dir = blackboard_dir

    def __call__(self, session: AgentSession, next_call: Callable[[AgentSession], Any]) -> Any:
        # 1. Execute the LLM call
        generator = next_call(session)
        
        # 2. Return a wrapper generator that inspects the stream
        return self._guard_stream(generator, session)

    def _guard_stream(self, generator, session):
        has_tool_calls = False
        
        for chunk in generator:
            if hasattr(chunk, 'choices') and chunk.choices:
                delta = chunk.choices[0].delta
                if hasattr(delta, 'tool_calls') and delta.tool_calls:
                    has_tool_calls = True
            yield chunk
        
        # End of stream check
        if not has_tool_calls:
            import json
            call_id = f"call_{uuid.uuid4().hex[:8]}"
            Logger.info(f"[{self.agent_name}] Guard triggered: No tool call detected. Injecting 'wait'.")
            
            reason = "### [SYSTEM GUARD]\nYou did not call any tools. If your task is complete, you MUST call the `finish` tool. Otherwise, use appropriate tools to move forward. If you are waiting for something, use the `wait` tool explicitly."
            args = {
                "duration": 0.5, 
                "wait_for_new_index": True, 
                "reason": reason
            }
            
            yield self._create_mock_tool_chunk(call_id, "wait", json.dumps(args))

    def _create_mock_tool_chunk(self, id, name, args):
        from types import SimpleNamespace
        import time
        
        tc = SimpleNamespace(index=0)
        if id: tc.id = id
        if name: 
            tc.type = 'function'
            tc.function = SimpleNamespace(name=name, arguments="")
        if args:
            if not hasattr(tc, 'function'): tc.function = SimpleNamespace(arguments="")
            tc.function.arguments = args
        
        choice = SimpleNamespace(
            index=0,
            delta=SimpleNamespace(content=None, tool_calls=[tc]),
            finish_reason=None
        )
        
        return SimpleNamespace(
            id=f"chatcmpl-mock-{int(time.time())}",
            object="chat.completion.chunk",
            created=int(time.time()),
            model="mock-guardian-model",
            choices=[choice]
        )
