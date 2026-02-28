
import os
import sys
import signal
from typing import List, Generator, Any
import datetime
import time
from src.utils.file_lock import file_lock
from src.utils.registry_manager import RegistryManager

# Backend imports should now be available from local directory
# project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../"))
# if project_root not in sys.path:
#     sys.path.insert(0, project_root)

from backend.llm.engine import AgentEngine
from backend.infra.config import Config
from backend.llm.types import SystemPromptConfig
from backend.llm.middleware import (
    ExecutionBudgetManager, 
    InteractionRefinementMiddleware,
    ErrorRecoveryMiddleware,
    LoopBreakerMiddleware,
    ToolResultCacheMiddleware
)
from backend.tools.base import BaseTool

from src.tools.ask_user_tool import AskUserTool
from src.tools.blackboard_tool import BlackboardTool
from src.tools.wait_tool import WaitTool
from src.tools.finish_tool import FinishTool
from src.tools.spawn_tool import SpawnSwarmAgentTool
from src.core.prompt_builder import PromptBuilder
from src.core.runtime import RuntimeManager
from src.core.middlewares import (
    WatchdogGuardMiddleware, 
    DependencyGuardMiddleware, 
    MailboxMiddleware, 
    SwarmStateMiddleware,
    NotificationAwarenessMiddleware,
    ActivityLoggerMiddleware,
    SwarmAgentGuardMiddleware
)

class SwarmAgent:
    """
    Nano Agent Team Wrapper.
    Integrates AgentEngine with Swarm tools and dynamic prompt building.
    """
    
    def __init__(
        self, 
        role: str, 
        name: str = "Assistant",
        blackboard_dir: str = ".blackboard",
        model: str = None,
        max_iterations: int = 200,
        extra_strategies: List[Any] = None
    ):
        self.role = role
        self.name = name
        self.blackboard_dir = blackboard_dir
        self.registry = RegistryManager(blackboard_dir)
        
        # Update Global Config for Path Substitution
        from backend.infra.config import Config
        Config.BLACKBOARD_ROOT = os.path.abspath(blackboard_dir)
        
        # Initialize Core Components
        self.prompt_builder = PromptBuilder(blackboard_dir)
        
        # Initialize Tools
        self.tools: List[BaseTool] = [
            BlackboardTool(blackboard_dir),
            WaitTool(
                watch_dir=os.path.join(blackboard_dir, "global_indices"),
                blackboard_root=blackboard_dir
            ),
            FinishTool(agent_name=name, agent_role=role, blackboard_dir=blackboard_dir),
            AskUserTool(),
            SpawnSwarmAgentTool(blackboard_dir, max_iterations=max_iterations)
        ]
        
        # Initialize Engine with specific strategies
        strategies = [
            ErrorRecoveryMiddleware(),
            ToolResultCacheMiddleware(),
            LoopBreakerMiddleware(),
            InteractionRefinementMiddleware(),
            DependencyGuardMiddleware(blackboard_dir),
            MailboxMiddleware(name, blackboard_dir),
            SwarmStateMiddleware(blackboard_dir),
            NotificationAwarenessMiddleware(blackboard_dir),
            ActivityLoggerMiddleware(name, blackboard_dir),
            SwarmAgentGuardMiddleware(name, blackboard_dir),
            ExecutionBudgetManager(max_iterations=max_iterations)
        ]
        
        # Note: We explicitly add middlewares here because AgentEngine 
        # only adds defaults if no strategies list is provided.
        self.max_iterations = max_iterations
        if extra_strategies:
            strategies.extend(extra_strategies)
            
        self.engine = AgentEngine(tools=self.tools, strategies=strategies, provider_key=model)
        
        # Configure tools with agent name for context propagation (e.g., for spawn_tool)
        for tool in self.tools:
            if hasattr(tool, 'configure'):
                tool.configure({
                    "agent_model": model,
                    "agent_name": name,
                    "is_architect": False  # Workers are not architects; overridden by agent_bridge for Architect
                })

        # Register SIGTERM handler for graceful shutdown (cleanup registry before exit)
        # This may fail in TUI worker threads — that's OK, TUI has its own shutdown path.
        _sigterm_in_progress = False
        def _sigterm_handler(signum, frame):
            nonlocal _sigterm_in_progress
            if _sigterm_in_progress:
                return
            _sigterm_in_progress = True
            try:
                self.deregister()
            except Exception:
                pass
            # 杀掉自己所在的整个进程组（含 browser-use 等子进程），避免孤儿进程
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            try:
                os.killpg(os.getpgid(os.getpid()), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            sys.exit(0)

        try:
            signal.signal(signal.SIGTERM, _sigterm_handler)
        except (ValueError, OSError):
            pass  # Not in main thread/interpreter (e.g. Textual worker), skip
        
    def add_tool(self, tool: BaseTool):
        self.tools.append(tool)
        # Verify if engine tools list needs valid reference or manual update
        if self.engine and hasattr(self.engine, 'tools') and tool not in self.engine.tools:
             self.engine.tools.append(tool)

    def add_strategy(self, strategy: Any):
        """Adds a strategy/middleware to the agent engine."""
        if self.engine and hasattr(self.engine, 'strategies'):
            self.engine.strategies.append(strategy)
        
    def run(self, goal: str = "", scenario: str = "", critical_tools: List[str] = None):
        """
        Starts the Agent Loop.
        """
        print(f"[{self.name}] Booting up with role: {self.role}")
        print(f"[{self.name}] Blackboard: {self.blackboard_dir}")
        
        # Dynamic System Prompt
        sys_prompt_content = self.prompt_builder.build(self.role, scenario)
        
        # Resolve path variables in system prompt so LLM knows the actual paths
        sys_prompt_content = sys_prompt_content.replace("{{blackboard}}", os.path.abspath(self.blackboard_dir))
        sys_prompt_content = sys_prompt_content.replace("{{root_path}}", Config.ROOT_PATH)
        
        system_config = SystemPromptConfig(base_prompt=sys_prompt_content)
        
        # Log System Prompt for TUI
        from collections import namedtuple
        Event = namedtuple("Event", ["type", "data"])
        self.handle_event(Event(type="system_prompt", data={"content": sys_prompt_content}))
        
        initial_message = goal if goal else f"Hello {self.role}, please check the Blackboard Indicies and begin your work."
        messages = [{"role": "user", "content": initial_message}]
        
        print(f"[{self.name}] Starting loop...")
        
        # Determine strict strategies if this is Watchdog
        if critical_tools:
             # Check if already added to avoid duplicates
             already_has = any(isinstance(s, WatchdogGuardMiddleware) for s in self.engine.strategies)
             if not already_has:
                 # Pass critical_tools to middleware
                 self.engine.strategies.append(WatchdogGuardMiddleware(agent_name=self.name, blackboard_dir=self.blackboard_dir, critical_tools=critical_tools))
        
        max_engine_retries = 3  # Maximum times to restart the engine on connection errors
        engine_retry_count = 0
        
        try:
            self.register()  # Register once before entering retry loop

            while engine_retry_count < max_engine_retries:
                try:
                    # We use a large max_iterations because the Agent is expected to run long-term
                    # controlled by WaitTool and external events.
                    # We add a buffer to engine.run so ExecutionBudgetManager (soft limit) triggers first.
                    run_limit = self.max_iterations + 20
                    event_generator = self.engine.run(messages, system_config, max_iterations=run_limit)

                    iteration_count = 0
                    while True:
                        try:
                            event = next(event_generator)
                            self.handle_event(event)
                            if event.type == "tool_call":
                                iteration_count += 1

                            # Check for termination signal
                            if event.type == "tool_result" and event.data.get("name") == "finish":
                                 print(f"[{self.name}] Detected 'finish' tool call after {iteration_count} tool calls. Stopping loop.")
                                 return  # Normal exit, don't retry

                        except StopIteration:
                            print(f"[{self.name}] Agent loop completed after {iteration_count} tool calls (max_iterations={self.max_iterations}).")
                            if iteration_count >= self.max_iterations:
                                print(f"[{self.name}] WARNING: Agent terminated because max_iterations ({self.max_iterations}) was reached. Tasks may be incomplete.")
                            self._cleanup_on_max_iterations()
                            return  # Normal completion, don't retry

                except KeyboardInterrupt:
                    print(f"\n[{self.name}] Interrupted by user.")
                    return  # User interrupt, don't retry

                except Exception as e:
                    error_msg = str(e).lower()
                    print(f"[{self.name}] Exception in agent loop: {e}")

                    # Check if this is a recoverable connection error
                    is_connection_error = any(keyword in error_msg for keyword in [
                        'connection', 'timeout', 'network', 'refused',
                        'unreachable', 'timed out', 'temporary failure'
                    ])

                    if is_connection_error and engine_retry_count < max_engine_retries - 1:
                        engine_retry_count += 1
                        retry_delay = 5 * engine_retry_count  # Exponential backoff: 5s, 10s, 15s
                        print(f"\n[SwarmAgent] Connection error detected: {e}")
                        print(f"[SwarmAgent] Retrying engine in {retry_delay}s... (Attempt {engine_retry_count}/{max_engine_retries})")

                        # Log the retry attempt
                        from collections import namedtuple
                        Event = namedtuple("Event", ["type", "data"])
                        self.handle_event(Event(
                            type="lifecycle",
                            data={
                                "event": "connection_retry",
                                "reason": f"Retrying due to connection error: {e}",
                                "attempt": engine_retry_count,
                                "max_retries": max_engine_retries
                            }
                        ))

                        import time
                        time.sleep(retry_delay)
                        continue  # Retry the entire engine.run()
                    else:
                        # Non-recoverable error or max retries reached
                        print(f"\n[SwarmAgent] Critical Error: {e}")
                        if is_connection_error:
                            print(f"[SwarmAgent] Max retries ({max_engine_retries}) reached for connection errors.")

                        # Log the error
                        from collections import namedtuple
                        Event = namedtuple("Event", ["type", "data"])
                        self.handle_event(Event(type="error", data={"error": str(e)}))

                        # Log lifecycle termination
                        self.handle_event(Event(
                            type="lifecycle",
                            data={"event": "terminated", "reason": "Self-terminated or normal exit"}
                        ))
                        return  # Exit without retry
        finally:
            # Always deregister when exiting (only called once at the end)
            self.deregister()

    def register(self):
        """Registers the agent in registry.json at startup."""
        if self.registry.register_agent(self.name, self.role):
            print(f"[{self.name}] Registered in blackboard registry.")
        else:
            print(f"[{self.name}] Failed to register.")

    def deregister(self):
        """Updates the agent's status to DEAD in registry.json on exit. Idempotent."""
        if getattr(self, '_deregistered', False):
            return
        self._deregistered = True
        RuntimeManager.cleanup_agent(self.name, self.blackboard_dir)

    def _cleanup_on_max_iterations(self):
        """
        Cleanup logic when agent reaches max_iterations.
        Notifies parent agent that worker was interrupted.
        Does NOT mark tasks as DONE (parent should decide next steps).
        """
        try:
            # Find my tasks for reporting purposes
            blackboard_tool = None
            for tool in self.tools:
                if isinstance(tool, BlackboardTool):
                    blackboard_tool = tool
                    break

            my_tasks = []
            if blackboard_tool:
                try:
                    result = blackboard_tool.execute(operation="read_index", filename="central_plan.md")
                    if "error" not in result.lower() and "not found" not in result.lower():
                        import json
                        plan_data = json.loads(result)
                        plan_content = plan_data.get("content", "")

                        # Extract JSON from markdown
                        import re
                        json_match = re.search(r'```json\s*(\{.*?\})\s*```', plan_content, re.DOTALL)
                        if json_match:
                            plan_json = json.loads(json_match.group(1))
                            my_tasks = [
                                t for t in plan_json.get("tasks", [])
                                if self.name in t.get("assignees", [])
                            ]
                except Exception as e:
                    print(f"[{self.name}] Failed to read central_plan.md: {e}")

            # Notify parent agent via mailbox
            parent_pid = None
            parent_agent_name = None

            # Find parent info from middlewares
            for strategy in self.engine.strategies:
                if hasattr(strategy, 'parent_pid'):
                    parent_pid = strategy.parent_pid
                if hasattr(strategy, 'parent_agent_name'):
                    parent_agent_name = strategy.parent_agent_name

            if parent_agent_name:
                try:
                    import json
                    import datetime

                    mailbox_dir = os.path.join(self.blackboard_dir, "mailboxes")
                    os.makedirs(mailbox_dir, exist_ok=True)
                    mailbox_path = os.path.join(mailbox_dir, f"{parent_agent_name}.json")

                    # Collect task info
                    in_progress_tasks = [t for t in my_tasks if t.get("status") == "IN_PROGRESS"]

                    # Format task details for content
                    task_details = []
                    for t in in_progress_tasks:
                        task_details.append(f"  - Task #{t['id']}: {t.get('description', 'N/A')}")
                    task_details_str = "\n".join(task_details) if task_details else "  (none)"

                    message = {
                        "timestamp": datetime.datetime.now().isoformat(),
                        "from": self.name,
                        "type": "max_iterations_reached",
                        "status": "unread",  # Required by mailbox middleware
                        "content": f"⚠️ Agent {self.name} reached max iterations ({self.max_iterations}) and was terminated. Tasks may be incomplete.\n\nIN_PROGRESS tasks ({len(in_progress_tasks)}):\n{task_details_str}\n\nPlease review these tasks and decide next steps:\n- Check if tasks are actually complete (check artifacts)\n- Re-spawn worker with higher max_iterations if needed\n- Break down into smaller subtasks if needed",
                        "tasks": [{"id": t["id"], "status": t.get("status"), "description": t.get("description")} for t in my_tasks],
                        "in_progress_count": len(in_progress_tasks)
                    }

                    # Append to mailbox
                    messages = []
                    if os.path.exists(mailbox_path):
                        with open(mailbox_path, 'r') as f:
                            messages = json.load(f)
                    messages.append(message)

                    with open(mailbox_path, 'w') as f:
                        json.dump(messages, f, indent=2)

                    print(f"[{self.name}] ⚠️ Notified {parent_agent_name}: reached max_iterations with {len(in_progress_tasks)} IN_PROGRESS tasks")
                except Exception as e:
                    print(f"[{self.name}] Failed to notify parent agent: {e}")

        except Exception as e:
            print(f"[{self.name}] Error in _cleanup_on_max_iterations: {e}")

    def handle_event(self, event):
        """Simple CLI Visualization of events + JSONL Logging"""
        import datetime
        import json
        import time
        
        # 1. JSONL Logging
        if event.type != "token":
            RuntimeManager.log_event(self.name, self.blackboard_dir, event.type, event.data)

        # 2. Text Logging (Parity with CLI output)
        log_dir = os.path.join(self.blackboard_dir, "logs")
        log_file_path = os.path.join(log_dir, f"{self.name}.log")
        timestamp = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S]") # Full date for log file
        
        log_msg = ""
        if event.type == "token":
            # Don't log individual tokens to text file to avoid massive I/O
            # We only log full messages
            pass
        elif event.type == "message":
            # Log the full message content that was just completed
            # Data usually contains the full message if it's a non-streaming event, 
            # but for streaming we might need to rely on the accumulator in the bridge/CLI.
            # However, handle_event acts on *events*. 
            # The 'message' event usually comes at the end of a turn with full content?
            # Let's check agent_bridge.py: it serves as a signal. 
            # For Swarm, enable_logging=True in engine might emit differently.
            # Actually, standard AgentEngine emits 'message' with full content at end of turn.
            msg_content = event.data.get("content", "")
            role = event.data.get("role", "assistant")
            log_msg = f"\n{timestamp} [{role}] {msg_content}\n"
            
        elif event.type == "tool_call":
            calls = event.data["tool_calls"]
            for call in calls:
                fn = call["function"]
                log_msg += f"\n{timestamp} [Tool Call] {fn['name']}({fn['arguments']})\n"
                
        elif event.type == "tool_result":
            res = event.data
            result_preview = res['result'] # Log full result in file
            log_msg = f"{timestamp} [Tool Result] {res['name']} -> {result_preview}\n"
            
        elif event.type == "finish":
            log_msg = f"\n{timestamp} [SwarmAgent] Session Finished.\n"
            
        elif event.type == "error":
            error_msg = event.data.get('error') or event.data.get('message') or "Unknown Error"
            log_msg = f"\n{timestamp} [SwarmAgent] Error: {error_msg}\n"

        if log_msg:
            try:
                with open(log_file_path, 'a', encoding='utf-8') as f:
                    f.write(log_msg)
            except Exception as e:
                print(f"[SwarmAgent] Failed to write to text log: {e}")

        # 3. CLI Visualization (Original logic)
        timestamp_cli = datetime.datetime.now().strftime("[%H:%M:%S]")
        separator = "-" * 80

        if event.type == "token":
            print(event.data["delta"], end="", flush=True)
        elif event.type == "message":
            # New message block finished
            print("\n") 
            print(separator)
        elif event.type == "tool_call":
            calls = event.data["tool_calls"]
            for call in calls:
                fn = call["function"]
                print(f"\n{timestamp_cli} [Tool Call] {fn['name']}({fn['arguments']})")
            print(separator)
        elif event.type == "tool_result":
            res = event.data
            result_preview = res['result'][:100] + "..." if len(res['result']) > 100 else res['result']
            print(f"{timestamp_cli} [Tool Result] {res['name']} -> {result_preview}")
            print(separator)
        elif event.type == "error":
            error_msg = event.data.get('error') or event.data.get('message') or "Unknown Error"
            print(f"\n{timestamp_cli} [SwarmAgent] Error: {error_msg}")
            print(separator)

