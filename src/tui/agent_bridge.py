"""
Agent Bridge for TUI
Dual Mode Support: Chat (Standard AgentEngine) / Swarm (SwarmAgent)
"""

import os
import sys
import shutil
from typing import Generator, List, Dict, Any, Optional, Callable
from dataclasses import dataclass
from backend.utils.logger import Logger

# Ensure project root is in path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from backend.llm.engine import AgentEngine
from backend.llm.types import SystemPromptConfig
from backend.llm.events import AgentEvent
from backend.llm.middleware import ExecutionBudgetManager, InteractionRefinementMiddleware
from src.core.middlewares import (
    WatchdogGuardMiddleware, 
    DependencyGuardMiddleware, 
    MailboxMiddleware, 
    SwarmStateMiddleware,
    NotificationAwarenessMiddleware,
    ActivityLoggerMiddleware
)
from backend.tools.base import BaseTool
from backend.tools.web_search import SearchTool
from backend.tools.web_reader import WebReaderTool

from src.core.agent_wrapper import SwarmAgent
from .components.message import ChatMessage
from .state import state, AgentMode


@dataclass
class AgentConfig:
    """Configuration for the agent"""
    mode: AgentMode = AgentMode.CHAT
    model_key: Optional[str] = None
    max_iterations: int = 200
    system_prompt: str = "You are a helpful AI assistant."
    # Swarm-specific config
    swarm_role: str = "assistant"
    swarm_name: str = "Assistant"
    blackboard_dir: str = ".blackboard"
    session_id: Optional[str] = None
    scenario: str = "You are the Root Architect. Analyze the mission, design the blackboard indices, and spawn agents to execute it."
    # Swarm max iterations (default matches main.py)
    swarm_max_iterations: int = 200
    # Auto-load architect.md for Swarm mode
    use_architect_prompt: bool = True


class AgentBridge:
    """
    Bridge between TUI and Agent Engine.
    Supports two modes:
    - CHAT: Uses standard AgentEngine
    - SWARM: Uses SwarmAgent
    """
    
    def __init__(self, config: AgentConfig = None):
        self.config = config or AgentConfig()
        self._chat_engine: Optional[AgentEngine] = None
        self._swarm_agent: Optional[SwarmAgent] = None
        self._tools: List[BaseTool] = []
        self._is_running = False
        self._confirmation_callback: Optional[Callable[[str], bool]] = None
        self._input_callback: Optional[Callable[[str], str]] = None
        self._current_generator = None  # Track current event generator for force stop
        # Track last init parameters to avoid redundant re-initialization
        self._last_init_key: Optional[tuple] = None
        # Note: messages are now stored in state.agent_messages (shared)
    
    def clean_blackboard(self):
        """Clean the current session's blackboard directory (Swarm mode only)"""
        if self.config.mode != AgentMode.SWARM:
            return
            
        from backend.infra.config import Config
        bb_dir = Config.BLACKBOARD_ROOT
        
        if os.path.exists(bb_dir):
            try:
                shutil.rmtree(bb_dir)
            except Exception as e:
                Logger.error(f"[AgentBridge] Error cleaning blackboard: {e}")
        
        # Recreate the directory to ensure it exists for agents
        os.makedirs(bb_dir, exist_ok=True)


    
    def set_confirmation_callback(self, callback: Callable[[str], bool]):
        """Set callback for user confirmation (e.g. for dangerous tool storage)"""
        self._confirmation_callback = callback

    def set_input_callback(self, callback: Callable[[str], str]):
        """Set callback for user input (e.g. for AskUserTool)"""
        self._input_callback = callback
    
    @property
    def mode(self) -> AgentMode:
        return self.config.mode
    
    def set_mode(self, mode: AgentMode):
        """切换模式"""
        if mode != self.config.mode:
            self.config.mode = mode
            # 重新初始化
            self.initialize(self.config.model_key)
    
    def initialize(self, model_key: str = None):
        """
        Initialize or reinitialize the agent.
        
        Args:
            model_key: Provider key for the model (e.g. "gpt4o", "claude")
        """
        if model_key:
            self.config.model_key = model_key
        
        if self.config.mode == AgentMode.CHAT:
            self._initialize_chat_engine()
        else:
            self._initialize_swarm_agent()
        
        # Track what we initialized with to avoid redundant re-init
        try:
            from backend.infra.config import Config
            self._last_init_key = (state.get_model_key(), Config.BLACKBOARD_ROOT)
        except Exception:
            self._last_init_key = (state.get_model_key(), None)
        
        # Note: Don't clear messages here - they are shared in state
    
    def _initialize_chat_engine(self):
        """初始化 Chat 模式的 AgentEngine"""
        from backend.tools.bash import BashTool
        from backend.tools.write_file import WriteFileTool
        from backend.tools.read_file import ReadFileTool
        from backend.tools.edit_file import EditFileTool
        from backend.tools.browser_use import BrowserUseTool
        from backend.infra.envs.local import LocalEnvironment
        
        # Initialize local environment with callback
        # Use project_root as workspace root
        env = LocalEnvironment(
            workspace_root=project_root,
            blackboard_dir=self.config.blackboard_dir,
            confirmation_callback=self._confirmation_callback
        )
        
        # Initialize base tools list
        self._tools = []
        
        # Bootstrap LLM Registry (Skills, SubAgents, Tools)
        Logger.info("[AgentBridge] Bootstrapping LLM Registry (Tools, Skills, SubAgents)...")
        
        # Define engine factory for subagents
        def engine_factory(tools=None):
            return AgentEngine(
                tools=tools or [],
                strategies=[ExecutionBudgetManager(max_iterations=100)],
                provider_key=self.config.model_key
            )
        
        from backend.llm.tool_registry import bootstrap_llm
        
        try:
            # Bootstrap with skills and subagents
            registry, agent_registry, skill_registry = bootstrap_llm(
                agents_dir=os.path.abspath(".subagents"),
                skills_dir=os.path.abspath(".skills"),
                engine_factory=engine_factory
            )
            
            # Load all tools from registry
            for tool_name in registry.get_all_tool_names():
                try:
                    tool_instance = registry.create_tool(tool_name, context={
                        "env": env,
                        "skill_registry": skill_registry
                    })
                    if tool_instance:
                        self._tools.append(tool_instance)
                        Logger.info(f"[AgentBridge] Loaded tool: {tool_instance.name}")
                except Exception as e:
                    Logger.warn(f"[AgentBridge] Failed to load tool {tool_name}: {e}")
                    
        except Exception as e:
            Logger.warn(f"[AgentBridge] Failed to bootstrap LLM registry: {e}")
            Logger.warn("[AgentBridge] Falling back to basic tools only")
            
            # Fallback: Add basic tools manually
            from backend.tools.grep import GrepTool
            from backend.tools.glob import GlobTool
            
            self._tools = [
                SearchTool(),
                WebReaderTool(),
                BashTool(env=env),
                WriteFileTool(env=env),
                ReadFileTool(env=env),
                EditFileTool(env=env),
                GrepTool(),
                GlobTool(),
                BrowserUseTool(env=env),
            ]

        # Initialize engine with strategies
        strategies = [
            InteractionRefinementMiddleware(),
            ExecutionBudgetManager(max_iterations=self.config.max_iterations)
        ]
        
        self._chat_engine = AgentEngine(
            tools=self._tools,
            strategies=strategies,
            provider_key=self.config.model_key,
            tool_context={
                "env": env,
                "skill_registry": skill_registry
            }
        )
        self._swarm_agent = None
    
    def _initialize_swarm_agent(self):
        """初始化 Swarm 模式的 SwarmAgent (与 main.py 保持一致)"""
        import os
        from src.tools.ask_user_tool import AskUserTool
        
        # Load architect.md prompt if enabled
        swarm_role = self.config.swarm_role
        if self.config.use_architect_prompt:
            prompt_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                "src", "prompts", "architect.md"
            )
            if os.path.exists(prompt_path):
                with open(prompt_path, "r", encoding="utf-8") as f:
                    swarm_role = f.read()
        
        from backend.infra.config import Config
        bb_dir = Config.BLACKBOARD_ROOT
            
        self._swarm_agent = SwarmAgent(

            role=swarm_role,
            name=self.config.swarm_name,
            blackboard_dir=bb_dir,
            model=self.config.model_key,
            max_iterations=self.config.swarm_max_iterations  # Use swarm-specific iterations
        )
        
        # Inject input callback into existing AskUserTool if present
        if hasattr(self, '_input_callback') and self._input_callback:
            for tool in self._swarm_agent.tools:
                if isinstance(tool, AskUserTool):
                    tool.input_callback = self._input_callback
        
        # Add research tools (same as main.py)
        self._swarm_agent.add_tool(SearchTool())
        self._swarm_agent.add_tool(WebReaderTool())
        
        # Add basic file tools to architect as well
        from backend.tools.bash import BashTool
        from backend.tools.write_file import WriteFileTool
        from backend.tools.read_file import ReadFileTool
        from backend.tools.edit_file import EditFileTool
        from backend.infra.envs.local import LocalEnvironment
        
        env = LocalEnvironment(
            workspace_root=project_root,
            blackboard_dir=bb_dir,
            agent_name=self.config.swarm_name
        )
        self._swarm_agent.add_tool(BashTool(env=env))
        self._swarm_agent.add_tool(WriteFileTool(env=env))
        self._swarm_agent.add_tool(ReadFileTool(env=env))
        self._swarm_agent.add_tool(EditFileTool(env=env))
        
        # Initialize RequestMonitorMiddleware for TUI
        # This enables the TUI to handle permission requests via dialogs
        from src.core.middlewares import RequestMonitorMiddleware
        request_monitor = RequestMonitorMiddleware(
            blackboard_dir=bb_dir,
            confirmation_callback=self._confirmation_callback
        )
        self._swarm_agent.add_strategy(request_monitor)
        
        # [FIX] Add WatchdogGuardMiddleware to enforce safety protocols (prevents early exit, non-tool deadlocks)
        # This aligns TUI Swarm mode with CLI Watchdog behavior.
        self._swarm_agent.add_strategy(WatchdogGuardMiddleware(
            agent_name=self.config.swarm_name,
            blackboard_dir=bb_dir,
            critical_tools=["spawn_swarm_agent"]
        ))
        
        self._chat_engine = None
    
    def send_message(
        self, 
        message: str,
        on_event: Optional[Callable[[AgentEvent], None]] = None
    ) -> Generator[ChatMessage, None, None]:
        """
        Send a message to the agent and stream responses.
        
        Args:
            message: User message to send
            on_event: Optional callback for raw AgentEvents
            
        Yields:
            ChatMessage objects for display in UI
        """
        if self.config.mode == AgentMode.CHAT:
            yield from self._send_message_chat(message, on_event)
        else:
            yield from self._send_message_swarm(message, on_event)
    
    def _process_events(
        self,
        event_generator,
        on_event: Optional[Callable[[AgentEvent], None]] = None,
        forward_to_swarm: bool = False,
        handle_finish_tool: bool = False,
    ) -> Generator[ChatMessage, None, None]:
        """
        Shared event processing loop for both Chat and Swarm modes.
        
        Args:
            event_generator: Generator from AgentEngine.run()
            on_event: Optional callback for raw AgentEvents
            forward_to_swarm: If True, forward events to SwarmAgent.handle_event()
            handle_finish_tool: If True, check tool_result for 'finish' tool
        """
        # Store current generator for force stop
        self._current_generator = event_generator
        
        current_content = ""
        current_msg: Optional[ChatMessage] = None
        
        # Track ask_user tool calls to convert them to natural messages
        pending_ask_user = {}  # tool_call_id -> question
        
        try:
            for event in event_generator:
                if not self._is_running:
                    event_generator.close()
                    yield ChatMessage(role="assistant", content="[Stopped]")
                    break
                
                # Forward to SwarmAgent for JSONL logging (Swarm mode only)
                if forward_to_swarm and self._swarm_agent:
                    self._swarm_agent.handle_event(event)
                
                # Call optional event callback
                if on_event:
                    on_event(event)
                
                if event.type == "token":
                    token = event.data.get("delta", "")
                    current_content += token
                    
                    if current_msg is None:
                        current_msg = ChatMessage(
                            role="assistant",
                            content=current_content,
                            is_streaming=True
                        )
                        yield current_msg
                    else:
                        current_msg.content = current_content
                        yield current_msg
                        
                elif event.type == "message":
                    if current_msg:
                        current_msg.is_streaming = False
                        current_msg.content = current_content
                        yield current_msg
                    
                    # Add to shared history
                    state.add_agent_message("assistant", current_content)
                    
                    current_content = ""
                    current_msg = None
                    
                elif event.type == "tool_call":
                    tool_calls = event.data.get("tool_calls", [])
                    for tc in tool_calls:
                        fn = tc.get("function", {})
                        tool_name = fn.get("name", "tool")
                        
                        # Track ask_user calls to convert them later
                        if tool_name == "ask_user":
                            try:
                                import json
                                args = json.loads(fn.get("arguments", "{}"))
                                question = args.get("question", "")
                                pending_ask_user[tc.get("id")] = question
                            except:
                                pass
                        
                        yield ChatMessage(
                            role="tool_call",
                            content=fn.get("arguments", ""),
                            tool_name=tool_name
                        )
                        
                elif event.type == "tool_result":
                    res = event.data
                    tool_name = res.get("name", "tool")
                    tool_call_id = res.get("tool_call_id")
                    result = res.get("result", "")
                    
                    yield ChatMessage(
                        role="tool_result",
                        content=result[:200],
                        tool_name=tool_name
                    )
                    
                    # Special handling for ask_user: save as assistant + user messages
                    if tool_name == "ask_user" and tool_call_id in pending_ask_user:
                        question = pending_ask_user.pop(tool_call_id)
                        # Save question as assistant message
                        state.add_agent_message("assistant", question)
                        # Save answer as user message
                        state.add_agent_message("user", result)
                    
                    # Special handling for finish: save as assistant message
                    elif tool_name == "finish":
                        # Extract reason from result if available
                        try:
                            import json
                            finish_data = json.loads(result) if result.startswith("{") else {"reason": result}
                            reason = finish_data.get("reason", result)
                        except:
                            reason = result
                        
                        state.add_agent_message("assistant", f"[Task Completed] {reason}")
                        
                        # Check for finish tool (Swarm mode only)
                        if handle_finish_tool:
                            yield ChatMessage(role="assistant", content="[Swarm Agent Finished]")
                            break
                    
                elif event.type == "error":
                    yield ChatMessage(
                        role="assistant",
                        content=f"Error: {event.data.get('error', 'Unknown error')}",
                        is_error=True
                    )
        finally:
            self._current_generator = None

    def _send_message_chat(
        self,
        message: str,
        on_event: Optional[Callable[[AgentEvent], None]] = None
    ) -> Generator[ChatMessage, None, None]:
        """Chat mode message handling"""
        if not self._chat_engine:
            self.initialize()
        
        self._is_running = True
        
        # Add user message to shared state
        state.add_agent_message("user", message)
        yield ChatMessage(role="user", content=message)
        
        system_config = SystemPromptConfig(base_prompt=self.config.system_prompt)
        
        try:
            event_generator = self._chat_engine.run(
                state.get_agent_messages_ref(),
                system_config,
                max_iterations=self.config.max_iterations
            )
            yield from self._process_events(event_generator, on_event)
                    
        except KeyboardInterrupt:
            yield ChatMessage(role="assistant", content="[Interrupted]")
        except Exception as e:
            yield ChatMessage(role="assistant", content=f"Error: {str(e)}", is_error=True)
        finally:
            self._is_running = False
    
    def _send_message_swarm(
        self,
        message: str,
        on_event: Optional[Callable[[AgentEvent], None]] = None
    ) -> Generator[ChatMessage, None, None]:
        """Swarm mode message handling"""
        # Ensure session ID is synced to Config and physical directory exists
        state.sync_blackboard_root()
        from backend.infra.config import Config
        os.makedirs(Config.BLACKBOARD_ROOT, exist_ok=True)
        
        # Only re-initialize if model or blackboard root actually changed
        current_init_key = (state.get_model_key(), Config.BLACKBOARD_ROOT)
        if current_init_key != self._last_init_key:
            self.initialize()
            self._last_init_key = current_init_key
        
        self._is_running = True
        
        # Add user message to shared state
        state.add_agent_message("user", message)
        yield ChatMessage(role="user", content=message)
        
        # Build system prompt from SwarmAgent's prompt builder
        sys_prompt_content = self._swarm_agent.prompt_builder.build(
            self._swarm_agent.role, 
            self.config.scenario
        )
        system_config = SystemPromptConfig(base_prompt=sys_prompt_content)
        
        try:
            # Lifecycle: Register in SwarmRegistry
            self._swarm_agent.register()
            
            run_limit = (self.config.swarm_max_iterations)
            event_generator = self._swarm_agent.engine.run(
                state.get_agent_messages_ref(),
                system_config,
                max_iterations=run_limit
            )
            yield from self._process_events(
                event_generator, on_event,
                forward_to_swarm=True,
                handle_finish_tool=True
            )
                    
        except KeyboardInterrupt:
            yield ChatMessage(role="assistant", content="[Interrupted]")
        except Exception as e:
            yield ChatMessage(role="assistant", content=f"Error: {str(e)}", is_error=True)
        finally:
            self._is_running = False
            # Lifecycle: Always deregister (DEAD status) to terminate child agents
            # This happens for both FinishTool and ctrl+K
            if self._swarm_agent:
                self._swarm_agent.deregister()
    
    def stop(self):
        """Stop the current agent execution immediately"""
        self._is_running = False
        
        # Force close the current event generator to stop blocking API calls
        if self._current_generator:
            try:
                self._current_generator.close()
            except Exception:
                pass  # Ignore errors during forced closure
    
    def clear_history(self):
        """Clear conversation history (shared state)"""
        state.clear_agent_messages()
    
    @property
    def is_running(self) -> bool:
        """Check if agent is currently processing"""
        return self._is_running
    
    @property
    def history(self) -> List[Dict[str, Any]]:
        """Get conversation history (from shared state)"""
        return state.agent_messages.copy()
