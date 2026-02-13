"""
LLM Platform Type Definitions

Defines core data types and configuration structures for LLM platform.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Union, Callable


@dataclass
class SystemPromptConfig:
    """
    System Prompt Configuration Class
    
    Manages LLM system-level prompt (System Prompt), supports dynamic appending of extra instructions.
    
    System prompt is the first message in LLM conversation, defining AI role, behavior norms, and constraints.
    
    Attributes:
        base_prompt: Base system prompt, defining core AI role and behavior
        extra_sections: List of extra prompt sections for dynamic instruction appending
    
    Design Features:
        - Separates base prompt and dynamic instructions, facilitating middleware injection
        - Uses '\n\n' to join sections, ensuring readability
        - Supports runtime modification (by appending to extra_sections)
    
    Typical Usage:
        >>> config = SystemPromptConfig(
        ...     base_prompt="You are a helpful coding assistant.",
        ...     extra_sections=["Always use Python 3.10+ syntax."]
        ... )
        >>> prompt = config.build()
        >>> print(prompt)
        You are a helpful coding assistant.
        
        Always use Python 3.10+ syntax.
        
        >>> # Dynamic instruction appending (usually by middleware)
        >>> config.extra_sections.append("WARNING: Time limit exceeded.")
        >>> prompt = config.build()
    
    Example Scenarios:
        - Base Prompt: Define AI core capabilities
        - Dynamic Instructions: Loop detection, budget limits, semantic drift warnings
    """
    
    base_prompt: str = "You are a helpful assistant."
    extra_sections: List[str] = field(default_factory=list)
    
    def build(self) -> str:
        """
        Build complete system prompt
        
        Combines base_prompt and all extra_sections into a complete prompt string.
        Sections are separated by double newlines to ensure clear paragraph structure.
        
        Returns:
            str: Complete system prompt string
        
        Example:
            >>> config = SystemPromptConfig(
            ...     base_prompt="You are an expert.",
            ...     extra_sections=["Rule 1: Be concise.", "Rule 2: Be accurate."]
            ... )
            >>> config.build()
            'You are an expert.\\n\\nRule 1: Be concise.\\n\\nRule 2: Be accurate.'
        """
        parts = [self.base_prompt] + self.extra_sections
        return "\n\n".join(parts)


@dataclass
class AgentSession:
    """
    AI Agent Session State Class
    
    Encapsulates complete state of an Agent execution, including dialogue history, tool list, config, and metadata.
    
    Session is the basic unit of Agent execution, spanning the entire ReAct loop (Think-Act-Observe).
    Middleware can read and modify session state to influence Agent behavior.
    
    Attributes:
        history: Dialogue history message list
                 Format: [{"role": "user", "content": "..."}, 
                        {"role": "assistant", "content": "...", "tool_calls": [...]},
                        {"role": "tool", "content": "...", "tool_call_id": "..."}]
        depth: Recursion depth (for subagent delegation nested calls)
               Top level is 1, increases by 1 for each subagent delegation
        system_config: System prompt configuration
        tools: List of tools available in current session
        metadata: Custom metadata dict (for middleware information passing)
                  Example: {"iteration_count": 5, "user_id": "123"}
    
    Design Features:
        - Mutable State: history and metadata can be modified during execution
        - Tool Isolation: Independent tool list per session (subagent constraint)
        - Metadata Extension: Supports arbitrary custom data
    
    Lifecycle:
        1. Create Session: Initialize history and tools
        2. Middleware Processing: Read/Modify session state
        3. LLM Call: Generate response based on history and system_config
        4. Tool Execution: Execute function calls based on tools
        5. Update History: Append assistant and tool messages
        6. Loop Continues: Until LLM returns final answer
    
    Typical Usage:
        >>> from backend.tools.base import BaseTool
        >>> session = AgentSession(
        ...     history=[{"role": "user", "content": "What's 2+2?"}],
        ...     depth=1,
        ...     system_config=SystemPromptConfig(),
        ...     tools=[],
        ...     metadata={"user_id": "user_123"}
        ... )
        >>> session.history.append({
        ...     "role": "assistant",
        ...     "content": "The answer is 4."
        ... })
    
    Interaction with Middleware:
        - LoopBreakerMiddleware: Checks history for repeated tool calls
        - SemanticDriftGuard: Reads metadata["iteration_count"]
        - ExecutionBudgetManager: Counts assistant messages in history
    """
    
    history: List[Dict[str, Any]]
    depth: int
    system_config: SystemPromptConfig
    tools: List['BaseTool']
    metadata: Dict[str, Any] = field(default_factory=dict)

