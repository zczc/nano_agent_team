
from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field

class BlackboardIndex(BaseModel):
    name: str = Field(..., description="Filename of the index, e.g. 'primary_timeline.md'")
    description: str = Field(..., description="Description of this index's purpose")
    usage_policy: str = Field(..., description="Strict rules on how agents should read/write to this index")
    initial_content: str = Field(default="", description="Initial content template, e.g. headers")

class BlackboardStructure(BaseModel):
    indices: List[BlackboardIndex] = Field(default_factory=list)
    resources_dir: str = Field(default="resources", description="Directory for heavy content")

class AgentProfile(BaseModel):
    name: str = Field(..., description="Agent name, e.g. 'Coordinator'")
    role: str = Field(..., description="Agent role, e.g. 'Project Manager'")
    goal: str = Field(..., description="Primary goal or instruction")
    model: Optional[str] = Field(None, description="Model provider key (e.g., 'gpt-4o', 'deepseek-v3')")
    tools: List[str] = Field(default_factory=lambda: ["blackboard_tool", "wait_tool"], description="List of tool names")
    responsibilities: List[str] = Field(default_factory=list, description="Specific duties")

class SwarmConfig(BaseModel):
    mission: str = Field(..., description="Overall mission description")
    blackboard_structure: BlackboardStructure
    agents: List[AgentProfile]
