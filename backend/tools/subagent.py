"""
子代理工具包装器模块

本模块实现了 AgentTool 类，它可以将一个独立的子代理定义（Markdown）
包装成一个标准工具，从而实现 Agent 的递归调用和能力委派。

主要类：
    - AgentTool: 子代理工具包装器，实现了 BaseTool 接口。

设计理念：
    - 任务委派：允许 LLM 将复杂的子任务委派给专门的子代理。
    - 隔离上下文：子代理在独立的会话中运行，拥有自己的提示词、工具集和模型。
    - 结果聚合：子代理的执行结果被聚合后作为工具输出返回。
    - [NEW] 跨环境委派：支持 Main Agent (Local) 调用 Sub Agent (Remote E2B/Docker)。
"""

import json
from typing import Dict, Any, List, Callable, Optional
from backend.tools.base import BaseTool
from backend.infra.environment import Environment
from backend.infra.envs import E2BEnvironment, DockerEnvironment
from backend.llm.decorators import schema_strict_validator

class AgentTool(BaseTool):
    """
    子代理工具包装器
    
    将 Agent Registry 中加载的代理定义转化为可调用的工具。
    当 LLM 调用此工具时，它实际上是在启动一个新的子引擎执行该代理的任务。
    
    支持 'target_environment' 参数，用于实现 Main Agent(Local) -> Sub Agent(Remote) 模式。
    """
    def __init__(self, agent_data: Dict[str, Any], engine_factory: Callable, tool_registry: Any, 
                 agent_registry: Any = None, skill_registry: Any = None, 
                 current_env: Optional[Environment] = None):
        """
        初始化子代理工具
        
        Args:
            agent_data: 代理元数据（instructions, allowed_tools, model, etc.）
            engine_factory: 用于创建新 AgentEngine 实例的工厂函数或者 Engine 类本身
            tool_registry: 工具注册中心（工厂，用于为子代理创建工具实例）
            agent_registry: 代理注册中心（用于子代理查找嵌套代理定义）
            skill_registry: 技能注册中心（用于子代理的技能匹配）
            current_env: 当前工具所处的环境（作为默认环境）
        """
        self.agent_data = agent_data
        self.engine_factory = engine_factory
        self.tool_registry = tool_registry
        self.agent_registry = agent_registry
        self.skill_registry = skill_registry
        self.current_env = current_env

    @property
    def name(self) -> str:
        """返回代理名称"""
        return self.agent_data["name"]

    @property
    def description(self) -> str:
        """返回代理描述，附带 [AGENT] 前缀以便 LLM 区分"""
        base_desc = self.agent_data.get('description', '')
        # 如果这是一个通用任务代理，明确说明它可以在隔离环境中运行
        if "general" in self.name or "task" in self.name:
            base_desc += " Can execute tasks in isolated environments (e2b/docker)."
        return f"[AGENT] {base_desc}"

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        """
        所有子代理工具接受 'query' 和可选的 'environment'、'files_to_transfer' 参数
        """
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The specific task or request to delegate to this subagent."
                },
                "environment": {
                    "type": "string",
                    "enum": ["default", "inherit", "local", "e2b", "docker"],
                    "description": "The target environment to run this subagent in. 'default' implies 'inherit'.",
                    "default": "default"
                },
                "files_to_transfer": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of absolute file paths in the current environment to transfer to the target environment before execution."
                }
            },
            "required": ["query"]
        }
    
    def configure(self, context: Dict[str, Any]):
        """Inject current environment from execution context"""
        if "env" in context and isinstance(context["env"], Environment):
            self.current_env = context["env"]

    def get_status_message(self, **kwargs) -> str:
        env = kwargs.get('environment', 'inherit')
        return f"\n\n🤖 正在委派任务给子代理: {self.name} (Env: {env})...\n"
    
    @schema_strict_validator
    def execute(self, query: str, environment: str = "default", files_to_transfer: List[str] = None) -> str:
        """
        执行子代理任务，支持动态环境切换和文件传输。
        
        Args:
            query: 任务描述
            environment: 目标运行环境 (default/inherit/local/e2b/docker)
            files_to_transfer: 需要传输到目标环境的文件列表（绝对路径）
            
        Returns:
            子代理执行结果摘要
        """
        target_env = None
        created_new_env = False
        
        # 0. 解析默认环境配置 (省略复杂的 Config 依赖，使用简单逻辑)
        if environment == "default":
            environment = "inherit"

        # 1. 决定目标环境
        try:
            if environment == "inherit":
                target_env = self.current_env
            elif environment == "local":
                from backend.infra.envs import LocalEnvironment
                if isinstance(self.current_env, LocalEnvironment):
                    target_env = self.current_env
                else:
                    target_env = self.current_env # Fallback
            elif environment == "e2b":
                from backend.infra.envs import E2BEnvironment
                from backend.infra.config import Config
                api_key = Config.get_provider_config("e2b").get("api_key")
                if not api_key:
                    return "Error: E2B API Key not configured."
                target_env = E2BEnvironment(api_key=api_key)
                created_new_env = True
            elif environment == "docker":
                from backend.infra.envs import DockerEnvironment
                target_env = DockerEnvironment(image="python:3.9-slim")
                created_new_env = True
            
            if not target_env and environment == "inherit":
                 # 如果 inherit 也是 None，尝试初始化 Local
                 from backend.infra.envs import LocalEnvironment
                 from backend.infra.config import Config
                 import os
                 target_env = LocalEnvironment(
                     workspace_root=Config.ROOT_PATH,
                     blackboard_dir=Config.BLACKBOARD_ROOT
                 )

            if not target_env:
                return "Error: Could not determine target environment."

            # 2. 文件传输 (跨环境文件同步)
            if created_new_env and files_to_transfer and self.current_env:
                import tempfile
                import os
                for file_path in files_to_transfer:
                    file_name = os.path.basename(file_path)
                    try:
                        with tempfile.TemporaryDirectory() as tmpdir:
                            local_tmp_path = os.path.join(tmpdir, file_name)
                            # Step A: 从源环境下载
                            if self.current_env.file_exists(file_path):
                                if not self.current_env.download_file(file_path, local_tmp_path):
                                    return f"Error: Failed to download file '{file_path}'."
                            else:
                                return f"Error: File '{file_path}' not found."
                            # Step B: 上传到目标环境
                            target_remote_path = f"{target_env.workdir}/{file_name}"
                            if not target_env.upload_file(local_tmp_path, target_remote_path):
                                return f"Error: Failed to upload file '{file_path}'."
                    except Exception as e:
                        return f"Error during file transfer: {e}"

            # 3. 创建并配置子引擎
            # 从 tool_registry 为子代理创建工具，注入 target_env
            resolved_tools = []
            if self.tool_registry:
                 allowed = self.agent_data.get("allowed_tools", [])
                 for t_name in allowed:
                     t = self.tool_registry.create_tool(t_name, context={"env": target_env})
                     if t:
                         resolved_tools.append(t)
            
            from backend.llm.engine import AgentEngine
            from backend.llm.types import SystemPromptConfig
            
            # 准备 SystemPrompt
            system_config = SystemPromptConfig(base_prompt=self.agent_data["instructions"])
            
            # 注入环境上下文到 Prompt
            prompt_cwd = target_env.workdir
            env_context_prompt = (
                f"\n\n[ENVIRONMENT CONTEXT]\n"
                f"You are running in an isolated execution environment.\n"
                f"CWD: {prompt_cwd}\n"
                "ALWAYS use absolute paths based on this context.\n"
            )
            
            # 构造子引擎（使用传入的 registry，不再 hack）
            sub_engine = AgentEngine(
                 tools=resolved_tools,
                 agent_registry=self.agent_registry,
                 tool_registry=self.tool_registry,
                 skill_registry=self.skill_registry,
                 provider_key=self.agent_data.get("model")
            )
            
            # 4. 运行子 Agent 会话并收集结果
            result_chunks = []
            
            # 使用 run 而不是 invoke_agent，因为我们已经手动 setup 了
            # 我们构造历史：
            current_messages = [{"role": "user", "content": f"{env_context_prompt}\n\n[TASK]\n{query}"}]
            
            # iterate stream
            final_history = []
            for event in sub_engine.run(messages=current_messages, system_config=system_config):
                if event.type == "finish":
                    final_history = event.data["history"]
            
            # Prefix successful results with marker so history middleware
            # can identify subagent outputs (dynamic tool names) and exempt
            # them from Phase 1 clearing.
            _MARKER = "[AGENT_RESULT]"

            if final_history:
                last_msg = final_history[-1]
                if last_msg["role"] == "assistant":
                    return f"{_MARKER}\n{str(last_msg['content'])}"
                elif last_msg["role"] == "tool":
                    return f"{_MARKER}\n{str(last_msg['content'])}"

            return "Task completed (no output)."
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            return f"Subagent execution failed: {str(e)}"
            
        finally:
            # 5. 清理环境
            if created_new_env and target_env:
                target_env.close()
