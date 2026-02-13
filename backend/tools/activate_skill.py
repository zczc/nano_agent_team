from typing import Dict, Any, Optional
from backend.tools.base import BaseTool
from backend.llm.decorators import schema_strict_validator

class ActivateSkillTool(BaseTool):
    """
    ActivateSkillTool: 获取并激活特定技能的内容。
    """
    def __init__(self, skill_registry: Any = None):
        self.skill_registry = skill_registry

    @property
    def name(self) -> str:
        return "activate_skill"

    @property
    def description(self) -> str:
        return (
            "Activate and retrieve the specific SOP (Standard Operating Procedure) and instructions for a skill. "
            "Use this when you identify that a specialized skill is required to complete the task. "
            "Available skills: {skills_list}"
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "The name of the skill to activate."
                }
            },
            "required": ["skill_name"]
        }

    def configure(self, context: Dict[str, Any]):
        """注入注册中心"""
        if "skill_registry" in context:
            self.skill_registry = context["skill_registry"]

    @schema_strict_validator
    def execute(self, skill_name: str) -> str:
        if not self.skill_registry:
            return "Error: Skill registry not initialized."
            
        skill = self.skill_registry.get_skill(skill_name)
        if not skill:
            return f"Error: Skill '{skill_name}' not found."
            
        # 返回技能内容
        result = (
            f"--- SKILL ACTIVATED: {skill.name} ---\n"
            f"Skill Base Path: {skill.path}\n"
            f"Instructions:\n{skill.instructions}\n"
            "--- END SKILL ---\n\n"
            "IMPORTANT:\n"
            "1. From now on, you MUST strictly follow the SOP and instructions provided above for the subsequent steps.\n"
            f"2. All resource paths mentioned in the instructions, if relative, are based on the 'Skill Base Path' ({skill.path}).\n"
            "3. You can (and should) use bash commands to list the directory tree under the Skill Base Path to locate resources before proceeding."
        )
        return result
