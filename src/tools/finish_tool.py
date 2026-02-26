
from typing import Dict, Any
from backend.tools.base import BaseTool
from backend.llm.decorators import schema_strict_validator

class FinishTool(BaseTool):
    """
    Tool for an Agent to signal that its task is complete.
    """
    @property
    def name(self) -> str:
        return "finish"
    
    @property
    def description(self) -> str:
        return """Call this function to signal that you have completed your task or objective. 
        Provide a comprehensive paragraph describing the reason and output. 
        If your work resulted in new or modified files, you MUST explicitly mention them and provide their absolute paths within this description.
        """
    
    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "A detailed paragraph summarizing the reason for finishing and the process."
                },
                "output": {
                    "type": "string",
                    "description": "A detailed paragraph summarizing the work and any produced artifacts/file paths."
                }
            },
            "required": ["output"]
        }

    @schema_strict_validator
    def execute(self, output: str, reason: str = None) -> str:
        # Guard: block finish if an evolution workspace worktree still exists.
        # This forces the Watchdog to call evolution_workspace() first.
        import os
        from backend.infra.config import Config
        workspace = os.path.join(Config.BLACKBOARD_ROOT, "resources", "workspace")
        if os.path.isfile(os.path.join(workspace, ".git")):
            return (
                "BLOCKED: Evolution workspace worktree still exists.\n\n"
                f"  {workspace}\n\n"
                "You MUST call the 'evolution_workspace' tool first:\n"
                "  - PASS: evolution_workspace(verdict='PASS', round_num=N, description='...', changed_files=[...])\n"
                "  - FAIL: evolution_workspace(verdict='FAIL', round_num=N)\n\n"
                "Do NOT call finish until the worktree is removed."
            )

        # In the AgentEngine loop, this will be detected to break the loop.
        reason_str = f"Reason: {reason}\n\n" if reason else ""
        return f"Agent Finished.\n\n{reason_str}===========================\n\nOutput: {output}"


