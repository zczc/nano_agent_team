"""
LLM Agent Engine

Implements LLM-based AI Agent execution engine, supporting streaming, tool calls, and nested skills.

Core Classes:
    - AgentEngine: Executes ReAct loop (Reasoning-Acting-Observing), supports on-demand skill activation.

Core Features:
    - Streaming: Real-time response generation
    - Tool Calling: Automated Function Calling
    - Middleware: Loop detection, budget management, semantic drift guard
    - Subagents: Recursive delegation to specialized agents
    - Skill On Demand: Dynamic loading of domain knowledge

Design Philosophy:
    - Streaming First: All responses are Generators
    - Stateless: New session per call
    - Composable Middleware: Custom chains
    - Expert Collaboration: Main engine delegates complex tasks

Dependencies:
    - Used by: backend.services.chat, backend.analysis.worker
    - Uses: backend.llm.types, backend.llm.providers, backend.llm.middleware
"""

import json
import uuid
import datetime
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import List, Dict, Any, Tuple, Optional, Generator
from backend.utils.json_utils import repair_truncated_json
from backend.llm.types import AgentSession, SystemPromptConfig
from backend.llm.providers import LLMFactory
from backend.infra.config import Config
from backend.llm.middleware import StrategyMiddleware, LoopBreakerMiddleware, SemanticDriftGuard, ExecutionBudgetManager, ToolResultCacheMiddleware, ErrorRecoveryMiddleware, ContextOverflowMiddleware
from backend.tools.base import BaseTool
from backend.utils.logger import Logger
from backend.utils.langfuse_manager import observe
from backend.llm.events import AgentEvent

# IO 密集型工具：并行执行时串行化，避免共享线程池死锁
_IO_BOUND_TOOLS = {"web_search", "web_reader", "browser_use"}

# 按工具类型的超时（秒），未列出的工具使用 engine 默认 tool_timeout
_TOOL_TIMEOUTS = {
    "web_search": 30,
    "web_reader": 45,
    "browser_use": 60,
}


class AgentEngine:
    
    def __init__(self, 
                 tools: List[BaseTool],
                 agent_registry: Any = None,
                 tool_registry: Any = None, # Added tool_registry
                 strategies: List[StrategyMiddleware] = None, 
                 provider_key: str = None, 
                 depth: int = 0, 
                 skill_registry: Any = None, 
                 parallel_tools: bool = True,
                 max_parallel_workers: int = 5,
                 tool_timeout: int = 300,
                 tool_context: Optional[Dict[str, Any]] = None):
        """
        Initialize Agent Engine
        
        Args:
            tools: List of available tools (instantiated). "My Arsenal".
            agent_registry: Registry for finding subagent definitions.
            tool_registry: Tool factory. "Arsenal Factory" for creating subagent tools dynamically.
            strategies: Custom middleware list. None for defaults.
            provider_key: LLM provider identifier. Defaults to config if None.
            depth: Current recursion depth.
            skill_registry: Skill registry instance. None disables skills.
            parallel_tools: Enable parallel tool execution (default False).
            max_parallel_workers: Max parallel workers (default 5).
        """
        # 1. Store core dependencies
        self.tools = tools
        self.agent_registry = agent_registry
        self.tool_registry = tool_registry
        
        # 2. Initialize client
        if not provider_key:
            if Config.ACTIVE_PROVIDER and Config.ACTIVE_MODEL:
                provider_key = f"{Config.ACTIVE_PROVIDER}/{Config.ACTIVE_MODEL}"
        
        self.provider_key = provider_key
        self.client = LLMFactory.create_client(self.provider_key)
        self.model = LLMFactory.get_model_name(self.provider_key)
        self.strategies = strategies if strategies is not None else [
            ContextOverflowMiddleware(),     # Outermost: catch context length errors, summarize and retry
            ErrorRecoveryMiddleware(),       # Handle connection errors and other exceptions
            ToolResultCacheMiddleware(),     # Preventive compression of tool results
            LoopBreakerMiddleware(),
            SemanticDriftGuard(),
            ExecutionBudgetManager()
        ]
        self.depth = depth
        self.parallel_tools = parallel_tools
        self.max_parallel_workers = max_parallel_workers
        self.tool_timeout = tool_timeout
        self.skill_registry = skill_registry
        self.tool_context = tool_context or {}

        # Configure tools with current context (e.g. for spawn_tool inheritance)
        for tool in self.tools:
            context = dict(self.tool_context)
            context["agent_model"] = self.provider_key
            tool.configure(context)

    def _get_llm_pipeline(self):
        """
        Build LLM call pipeline (including middleware)
        """
        def base_llm_call(session: AgentSession):
            # Inject client and model to metadata (for ContextOverflowMiddleware)
            session.metadata["llm_client"] = self.client
            session.metadata["llm_model"] = self.model

            model = self.model
            messages = [{"role": "system", "content": session.system_config.build()}] + session.history

            if not self.client:
                raise RuntimeError("LLM 客户端未初始化，请检查 keys.json 或环境变量中的 API Key，并确认 openai 依赖已安装。")

            kwargs = {
                "model": model,
                "messages": messages,
                "stream": True
            }
            if session.tools:
                kwargs["tools"] = [t.to_openai_schema() for t in session.tools]

            return self.client.chat.completions.create(**kwargs)

        pipeline = base_llm_call
        for strategy in reversed(self.strategies):
            def make_wrapper(current_pipeline, current_strat):
                return lambda s: current_strat(s, current_pipeline)
            pipeline = make_wrapper(pipeline, strategy)

        return pipeline
        
    @observe(as_type="span")
    def run(self, messages: List[Dict[str, Any]], system_config: SystemPromptConfig, max_iterations: int = 10, on_step_log: callable = None, forced_skill: str = None, return_full_history: bool = True) -> Generator[AgentEvent, None, None]:
        """
        Execute structured streaming conversation
        
        Args:
            messages: History messages
            system_config: System prompt config
            max_iterations: Max ReAct loops
            on_step_log: Step log callback
            forced_skill: Force match skill name
            return_full_history: Whether to return full history on finish.
            
        Yields:
            AgentEvent: Structured event object:
                - "token": Streaming text delta
                - "message": Complete message object
                - "tool_call": Tool call request
                - "tool_result": Tool execution result
                - "finish": Task completion signal
                - "error": Error information
        """
        self.depth += 1
        search_citations = []
        
        initial_query = ""
        for m in reversed(messages):
            if m["role"] == "user":
                initial_query = m["content"]
                break
        
        # Start with self.tools
        current_tools = list(self.tools)
        
        # [Skill On Demand] Inject activate_skill tool
        if self.skill_registry:
            try:
                from backend.tools.activate_skill import ActivateSkillTool
                if not any(t.name == "activate_skill" for t in current_tools):
                    active_skill_tool = ActivateSkillTool(self.skill_registry)
                    # Dynamically update description with available skills
                    skills_meta = self.skill_registry.get_skills_metadata()
                    skills_list_str = ", ".join([f"'{s['name']}'" for s in skills_meta])
                    active_skill_tool._description = active_skill_tool.description.format(skills_list=skills_list_str)
                    current_tools.append(active_skill_tool)
            except ImportError:
                Logger.warning("ActivateSkillTool not found, skill-on-demand disabled.")
        
        # [Skill On Demand] Inject system prompt
        if self.skill_registry:
            skill_demand_prompt = (
                "\n### SKILL ON DEMAND\n"
                "You are equipped with a library of specialized skills. "
                "If you find that the user's request falls into a specific domain (e.g., citation-management, arxiv-search, etc.), "
                "you should use the `activate_skill` tool to fetch the professional SOP and instructions for that skill. "
                "Available skill names are provided in the `activate_skill` tool description. "
                "Do NOT guess the process; always rely on the official instructions returned by the tool. "
                "Once a skill is activated, all your subsequent reasoning and actions MUST strictly follow its SOP."
            )
            system_config.extra_sections.append(skill_demand_prompt)
        
        # [Forced Skill] Inject via simulated activate_skill message
        active_skill_tool = next((t for t in current_tools if t.name == "activate_skill"), None)
        if forced_skill and active_skill_tool and self.skill_registry:
            skill_names = [s.strip() for s in forced_skill.split(",") if s.strip()]
            for s_name in skill_names:
                skill = self.skill_registry.get_skill(s_name)
                if skill:
                    tool_call_id = f"call_{uuid.uuid4().hex[:12]}"
                    skill_content = active_skill_tool.execute(s_name)
                    
                    # Inject Assistant tool call message
                    messages.append({
                        "role": "assistant",
                        "content": f"Activating required skill: {s_name}",
                        "tool_calls": [{
                            "id": tool_call_id,
                            "type": "function",
                            "function": {
                                "name": "activate_skill",
                                "arguments": json.dumps({"skill_name": s_name})
                            }
                        }]
                    })
                    yield AgentEvent(type="message", data=messages[-1])
                    
                    # Inject Tool result message
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "name": "activate_skill",
                        "content": skill_content
                    })
                    yield AgentEvent(type="message", data=messages[-1])
                    
                    if on_step_log:
                        on_step_log("skill_activated", name=s_name, path=skill.path)
                    Logger.info(f"Forced skill '{s_name}' activated via mock message")
                else:
                    Logger.warning(f"Forced skill '{s_name}' not found.")

        session = AgentSession(history=list(messages), depth=self.depth, system_config=system_config, tools=current_tools)
        pipeline = self._get_llm_pipeline()
        try:
            for iteration in range(max_iterations):
                session.metadata["iteration_count"] = iteration + 1

                # Stream consumption with retry — streaming errors (e.g. read timeout)
                # happen during iteration, outside middleware scope. Retry here and
                # re-invoke pipeline (which passes through the full middleware chain).
                full_content = ""
                tool_calls = []
                for stream_attempt in range(3):  # max 3 attempts (1 initial + 2 retries)
                    try:
                        stream = pipeline(session)
                        full_content = ""
                        tool_calls = []
                        for chunk in stream:
                            delta = chunk.choices[0].delta
                            if delta.tool_calls:
                                for tc_chunk in delta.tool_calls:
                                    if len(tool_calls) <= tc_chunk.index:
                                        tool_calls.append({"id": tc_chunk.id, "function": {"name": "", "arguments": ""}})
                                    tc = tool_calls[tc_chunk.index]
                                    if tc_chunk.id: tc["id"] = tc_chunk.id
                                    if tc_chunk.function.name: tc["function"]["name"] += tc_chunk.function.name
                                    if tc_chunk.function.arguments: tc["function"]["arguments"] += tc_chunk.function.arguments
                            if delta.content:
                                full_content += delta.content
                                yield AgentEvent(type="token", data={"delta": delta.content})
                        break  # stream consumed successfully
                    except Exception as e:
                        if stream_attempt < 2:
                            Logger.warning(f"Stream error: {e}. Retrying ({stream_attempt + 1}/2)...")
                            continue
                        raise  # exhausted retries, let outer except handle
                
                if not tool_calls:
                    if search_citations:
                        citation_text = "\n\n**References:**\n"
                        for idx, item in enumerate(search_citations, 1):
                            title = item.get("title", "No Title")
                            href = item.get("href", "#")
                            citation_text += f"{idx}. [{title}]({href})\n"
                        
                        full_content += citation_text
                        yield AgentEvent(type="token", data={"delta": citation_text})

                    session.history.append({"role": "assistant", "content": full_content})
                    yield AgentEvent(type="message", data=session.history[-1])

                    if on_step_log: on_step_log("assistant_response", content=full_content)
                    break

                session.history.append({
                    "role": "assistant", 
                    "content": full_content or None, 
                    "tool_calls": [{
                        "id": tc["id"],
                        "type": "function",
                        "function": tc["function"]
                    } for tc in tool_calls]
                })
                yield AgentEvent(type="message", data=session.history[-1])

                if on_step_log: on_step_log("tool_call_request", tool_calls=tool_calls, assistant_content=full_content)
                
                # Yield tool call events
                yield AgentEvent(type="tool_call", data={"tool_calls": tool_calls})

                # Prepare tool execution task
                def execute_single_tool(tc):
                    """Execute single tool call, return (tc, result, fn_name, args)"""
                    fn_name = tc["function"]["name"]
                    args_str = tc["function"]["arguments"]
                    try:
                        args = json.loads(args_str)
                    except json.JSONDecodeError:
                        # Attempt to repair truncated JSON
                        repaired_str, args = repair_truncated_json(args_str)
                        if args is not None:
                            # Update the tool call with repaired JSON to ensure history is valid
                            tc["function"]["arguments"] = repaired_str
                            Logger.info(f"Repaired truncated JSON arguments for tool '{fn_name}'")
                        else:
                            args = {}
                            Logger.warning(f"Failed to repair JSON for tool '{fn_name}': {args_str}")

                    tool = next((t for t in current_tools if t.name == fn_name), None)
                    if tool:
                        try:
                            result = tool.execute(**args)
                        except Exception as e:
                            result = f"Error: {e}"
                    else:
                        # Mock a tool result for non-existent tool to prevent crash
                        result = f"Error: Tool '{fn_name}' not found. Please check the tool name and try again."

                    # Downgrade failed finish to wait — prevent engine from breaking on invalid finish
                    if fn_name == "finish" and str(result).startswith("Error:"):
                        error_detail = str(result)
                        tc["function"]["name"] = "wait"
                        tc["function"]["arguments"] = json.dumps({
                            "duration": 0.1,
                            "wait_for_new_index": False,
                            "reason": f"Your finish call failed: {error_detail}. Please fix the arguments and call finish again."
                        })
                        wait_tool = next((t for t in current_tools if t.name == "wait"), None)
                        if wait_tool:
                            try:
                                result = wait_tool.execute(duration=0.1, wait_for_new_index=False, reason=f"Your finish call failed: {error_detail}. Please fix the arguments and call finish again.")
                            except Exception:
                                result = f"[System] Your finish call failed: {error_detail}. Please fix the arguments and call finish again."
                        else:
                            result = f"[System] Your finish call failed: {error_detail}. Please fix the arguments and call finish again."
                        fn_name = "wait"

                    return (tc, result, fn_name, args)
                
                # Execute tool calls with timeout protection
                # IO-bound tools (web_search etc.) are serialized to avoid shared thread pool deadlock;
                # local tools run in parallel as before.
                if self.parallel_tools and len(tool_calls) > 1:
                    # Split into IO-bound and local tool calls
                    io_tool_calls = [tc for tc in tool_calls if tc["function"]["name"] in _IO_BOUND_TOOLS]
                    local_tool_calls = [tc for tc in tool_calls if tc["function"]["name"] not in _IO_BOUND_TOOLS]

                    tool_results = []

                    # 1) Local tools: parallel execution
                    if local_tool_calls:
                        max_workers = min(len(local_tool_calls), self.max_parallel_workers)
                        with ThreadPoolExecutor(max_workers=max_workers) as executor:
                            futures = [(tc, executor.submit(execute_single_tool, tc)) for tc in local_tool_calls]
                            for tc, f in futures:
                                fn_name = tc["function"]["name"]
                                timeout = _TOOL_TIMEOUTS.get(fn_name, self.tool_timeout)
                                try:
                                    tool_results.append(f.result(timeout=timeout))
                                except FuturesTimeoutError:
                                    error_result = f"Error: Tool '{fn_name}' execution timed out after {timeout}s."
                                    tool_results.append((tc, error_result, fn_name, {}))

                    # 2) IO tools: serial execution (avoid DDGS ClassVar executor contention)
                    for tc in io_tool_calls:
                        fn_name = tc["function"]["name"]
                        timeout = _TOOL_TIMEOUTS.get(fn_name, self.tool_timeout)
                        executor = ThreadPoolExecutor(max_workers=1)
                        future = executor.submit(execute_single_tool, tc)
                        try:
                            tool_results.append(future.result(timeout=timeout))
                        except FuturesTimeoutError:
                            error_result = f"Error: Tool '{fn_name}' execution timed out after {timeout}s."
                            tool_results.append((tc, error_result, fn_name, {}))
                        finally:
                            executor.shutdown(wait=False, cancel_futures=True)
                else:
                    # Serial execution with per-tool timeout
                    tool_results = []
                    for tc in tool_calls:
                        fn_name = tc["function"]["name"]
                        timeout = _TOOL_TIMEOUTS.get(fn_name, self.tool_timeout)
                        executor = ThreadPoolExecutor(max_workers=1)
                        future = executor.submit(execute_single_tool, tc)
                        try:
                            tool_results.append(future.result(timeout=timeout))
                        except FuturesTimeoutError:
                            error_result = f"Error: Tool '{fn_name}' execution timed out after {timeout}s."
                            tool_results.append((tc, error_result, fn_name, {}))
                        finally:
                            executor.shutdown(wait=False, cancel_futures=True)                
                # Process results in order
                for tc, result, fn_name, args in tool_results:
                    # Handle special logic for web_search
                    if fn_name == "web_search":
                        try:
                            search_data = json.loads(result)
                            if isinstance(search_data, list):
                                for item in search_data:
                                    if isinstance(item, dict) and item.get("href") and not any(x.get("href") == item.get("href") for x in search_citations):
                                        search_citations.append(item)
                        except json.JSONDecodeError:
                            pass
                    
                    yield AgentEvent(type="tool_result", data={
                        "tool_call_id": tc["id"],
                        "name": fn_name,
                        "result": str(result)
                    })

                    session.history.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "name": fn_name,
                        "content": str(result)
                    })
                    yield AgentEvent(type="message", data=session.history[-1])

                    if on_step_log: on_step_log("tool_result", tool_call_id=tc["id"], function_name=fn_name, arguments=args, result=str(result))

                if "finish" in ','.join([fn_name for tc, result, fn_name, args in tool_results]):
                    break
            else:
                # If the loop finished without break, it means max_iterations was reached
                yield AgentEvent(type="error", data={"error": f"Agent (PID: {os.getpid()}) has reached the maximum iteration limit ({max_iterations}), the agent is closed. You can use commend /iterations * to improve it."})
            
            # End of loop
            final_history = session.history if return_full_history else [session.history[-1]]
            yield AgentEvent(type="finish", data={"history": final_history})

        except Exception as e:
            Logger.error(f"AgentEngine execution error: {e}")
            yield AgentEvent(type="error", data={"error": str(e)})
            raise e
        finally:
            self.depth -= 1
            # Clean up cache middleware
            for strategy in self.strategies:
                if isinstance(strategy, ToolResultCacheMiddleware):
                    strategy.cleanup()

    @observe(as_type="span")
    def invoke_agent(self, agent_name: str, query: str, history: List[Dict[str, Any]] = None, on_step_log: callable = None, forced_skill: str = None, return_full_history: bool = True) -> Generator[AgentEvent, None, None]:
        """
        Invoke specific subagent
        
        Args:
            agent_name: Subagent name
            query: User query
            history: Dialogue history
            on_step_log: Step log callback
            forced_skill: Forced skill
            return_full_history: Whether to return full history
            
        Yields:
            AgentEvent: Events forwarded from AgentEngine.run. See AgentEngine.run docs for format.
        """
        if not self.agent_registry:
            yield AgentEvent(type="error", data="Error: No agent registry available.")
            return

        agent_data = self.agent_registry.get_agent(agent_name)
        if not agent_data:
            yield AgentEvent(type="error", data=f"Error: Agent '{agent_name}' not found.")
            return

        # 1. 准备子代理配置
        # 子代理继承当前的 provider_key 或者是默认的，除非 agent_data 指定了 modal
        provider_key = agent_data.get("model") or self.provider_key 
        
        # 2. 准备子代理工具
        # 使用 tool_registry 为子代理创建工具实例。
        # 如果子代理定义了 "allowed_tools"，则只实例化指定的工具。
        resolved_tools = []
        if self.tool_registry and "allowed_tools" in agent_data:
             for name in agent_data["allowed_tools"]:
                 # 注意：这里创建的工具需要具备执行环境 (Context)。
                 # 目前依赖于 ToolRegistry 在 create_tool 时可能需要的 context。
                 # 对于某些依赖环境的工具（如 BashTool），后续可能需要优化环境注入机制，
                 # 例如从当前 Engine 传递 LocalEnvironment 给子代理。
                 # 目前假设 ToolRegistry 或工具自身能处理基本的初始化。
                 t = self.tool_registry.create_tool(name, context=self.tool_context)
                 if t: resolved_tools.append(t)
        
        # TODO: 完善子代理的工具上下文注入 (Tool Context Injection)
        # 目前子代理的工具可能缺少与主代理相同的环境配置 (如 env 对象)。
        # 未来应在 Engine 初始化时接收 tool_context，并在此处传递给 create_tool，
        # 或者在 SystemPromptConfig 中携带更多环境信息。
        
        config = SystemPromptConfig(base_prompt=agent_data["instructions"])
        
        messages = history or []
        messages.append({"role": "user", "content": query})

        # 3. 创建子代理引擎
        # 子代理也需要 registries 和 tools
        sub_engine = AgentEngine(
             tools=resolved_tools,
             agent_registry=self.agent_registry,
             tool_registry=self.tool_registry,
             skill_registry=self.skill_registry,
             provider_key=provider_key,
             depth=self.depth + 1,
             tool_context=self.tool_context
        )
        
        # 使用 yield from 转发事件
        yield from sub_engine.run(
            messages=messages, 
            system_config=config, 
            on_step_log=on_step_log, 
            forced_skill=forced_skill,
            return_full_history=return_full_history
        )
