"""
LLM Tool and Subagent Registry

Provides dynamic loading, registration, and management of tools and subagents.

Main Classes:
    - ToolRegistry: Tool registry, manages tool creation and configuration
    - AgentRegistry: Subagent registry, loads agent definitions from Markdown (with YAML Frontmatter)

Main Functions:
    - bootstrap_llm: Initialize LLM platform, register atomic tools, scan/load subagents and skills

Design Philosophy:
    - Subagent Pattern: Each agent is an expert with independent prompt, tools, and model config
    - Factory Pattern: Create tool instances via factory functions
    - Auto Discovery: Scan agents directory to auto-load subagents
    - Context Injection: Support runtime tool configuration
    - Skill Integration: Auto-register Skill On Demand activation tools
"""

import os
import yaml
from typing import Dict, Any, List, Callable, Optional, Type
from backend.llm.skill_registry import SkillRegistry
from backend.tools.base import BaseTool
from backend.tools.subagent import AgentTool
from backend.tools.web_search import SearchTool
from backend.tools.web_reader import WebReaderTool
from backend.tools.read_file import ReadFileTool
from backend.tools.write_file import WriteFileTool
from backend.tools.bash import BashTool
from backend.tools.edit_file import EditFileTool
from backend.tools.activate_skill import ActivateSkillTool
from backend.tools.grep import GrepTool
from backend.tools.glob import GlobTool
from backend.utils.logger import Logger


class ToolRegistry:
    """
    Tool Registry
    
    Manages creation, configuration, and query of all available tools in the system.
    """
    
    def __init__(self):
        """Initialize Tool Registry"""
        self._factories: Dict[str, Callable[[], BaseTool]] = {}
    
    def register_factory(self, name: str, factory: Callable[[], BaseTool]):
        """
        Register tool factory function
        """
        self._factories[name] = factory
    
    def register_tool_class(self, name: str, tool_cls: Type[BaseTool]):
        """
        Register tool class (convenience method)
        
        Automatically creates factory function calling tool class no-arg constructor.
        """
        self.register_factory(name, lambda: tool_cls())
    
    def create_tool(self, name: str, context: Optional[Dict] = None) -> Optional[BaseTool]:
        """
        Create tool instance
        
        Finds and calls factory function by tool name, returns new tool instance.
        If context provided, calls tool's configure method.
        """
        factory = self._factories.get(name)
        if not factory:
            return None
        tool = factory()
        if tool and context:
            tool.configure(context)
        return tool
    
    def get_all_tool_names(self) -> List[str]:
        """
        Get all registered tool names
        
        Returns:
            List of tool names
        """
        return list(self._factories.keys())

class AgentRegistry:
    """
    Subagent Registry
    
    Dynamically loads and manages AI subagent definitions from filesystem.
    
    Subagents are specialized AI assistants with specific system prompts, allowed tools, and model configs.
    Each agent is defined via a Markdown file with YAML Frontmatter.
    
    File Format:
        ---
        name: goal_tracker
        description: Analyze user long-term goals
        tools: [query_intent, query_behavior]
        model: sonnet
        ---
        
        # Your Role
        You are a long-term goal tracking expert...
    """
    
    def __init__(self, agents_dir: str):
        """
        Initialize Agent Registry
        
        Args:
            agents_dir: Directory containing agent definition files
        """
        self.agents_dir = agents_dir
        self.agents: Dict[str, Dict[str, Any]] = {}
        self._load_agents()
    
    def _load_agents(self):
        """
        Load all subagents from filesystem
        
        Scans all .md files in agents_dir.
        Parses YAML Frontmatter and Markdown content.
        """
        if not os.path.exists(self.agents_dir):
            return
        
        for filename in os.listdir(self.agents_dir):
            if not filename.endswith('.md'):
                continue
                
            file_path = os.path.join(self.agents_dir, filename)
            agent_id = filename[:-3] # Remove .md suffix
            
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    
                    # Parse YAML Frontmatter
                    if content.startswith('---'):
                        parts = content.split('---', 2)
                        if len(parts) >= 3:
                            meta = yaml.safe_load(parts[1]) or {}
                            
                            # Parse tools (supports string and list format)
                            # Compatible with old allowed-tools and new tools
                            raw_tools = meta.get("tools") or meta.get("allowed-tools") or []
                            if isinstance(raw_tools, str):
                                tools = [t.strip() for t in raw_tools.split(',')]
                            elif isinstance(raw_tools, list):
                                tools = raw_tools
                            else:
                                tools = []
                            
                            self.agents[agent_id] = {
                                "name": meta.get("name", agent_id),
                                "description": meta.get("description", ""),
                                "instructions": parts[2].strip(),
                                "allowed_tools": tools,
                                "model": meta.get("model"),
                                "path": file_path
                            }
            except Exception as e:
                print(f"Error loading agent from '{filename}': {e}")
    
    def get_agent(self, name: str) -> Optional[Dict[str, Any]]:
        """Get data for specific agent"""
        return self.agents.get(name)
    
    def get_all_agents(self) -> List[Dict[str, Any]]:
        """Get all loaded agents"""
        return list(self.agents.values())

def bootstrap_llm(agents_dir: str, skills_dir: str, engine_factory: Callable) -> tuple:
    """
    Initialize LLM Platform
    
    Registers atomic tools, and loads all subagents and skills.
    """
    # Create tool registry
    registry = ToolRegistry()
    
    # Register all atomic tools
    registry.register_tool_class("web_search", SearchTool)
    registry.register_tool_class("web_reader", WebReaderTool)
    registry.register_tool_class("read_file", ReadFileTool)
    registry.register_tool_class("write_file", WriteFileTool)
    registry.register_tool_class("edit_file", EditFileTool)
    registry.register_tool_class("grep", GrepTool)
    registry.register_tool_class("glob", GlobTool)
    
    # Register browser_use tool (check if browser installed first)
    try:
        from backend.tools.browser_use import BrowserUseTool, check_browser_installed
        
        if check_browser_installed():
            registry.register_tool_class("browser_use", BrowserUseTool)
        else:
            Logger.warning("[ToolRegistry] Playwright browser not installed. Run 'playwright install chromium' to enable browser_use tool.")
    except (ImportError, ModuleNotFoundError) as e:
        Logger.warning(f"[ToolRegistry] browser_use dependency not found: {e}. BrowserUseTool will not be available.")
    
    registry.register_tool_class("bash", BashTool)
    registry.register_tool_class("activate_skill", ActivateSkillTool)
    # registry.register_tool_class("read_skill_resource", SkillResourceTool)
    # registry.register_tool_class("query_goal", QueryGoalTool)
    # registry.register_tool_class("query_intent", QueryIntentTool)
    # registry.register_tool_class("query_behavior", QueryBehaviorTool)
    # registry.register_tool_class("query_content", QueryContentTool)
    # registry.register_tool_class("query_goal_full_chain", QueryGoalFullChainTool)
    
    # Load skills
    skill_registry = SkillRegistry(skills_dir)
    
    # Load subagents
    agent_registry = AgentRegistry(agents_dir)
    
    # Wrap subagents as tools and register, maintain backward compatibility (some agents might need to call others)
    for agent_data in agent_registry.get_all_agents():
        def create_agent_tool(a_data=agent_data):
            # Reuse AgentTool logic, as it essentially executes a subtask with Prompt and Tools
            return AgentTool(
                agent_data=a_data,
                engine_factory=engine_factory,
                tool_registry=registry,
                agent_registry=agent_registry,
                skill_registry=skill_registry
            )
        registry.register_factory(agent_data["name"], create_agent_tool)
    
    # Save registry references
    registry._agent_registry = agent_registry
    registry._skill_registry = skill_registry
    
    return registry, agent_registry, skill_registry
