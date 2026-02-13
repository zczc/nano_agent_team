import os
from typing import Dict, Any, Optional
from backend.tools.base import BaseTool
from backend.infra.environment import Environment
from backend.llm.decorators import schema_strict_validator

class BashTool(BaseTool):
    """
    执行 Bash 命令的工具。
    现在是环境感知的 (Environment-Aware)，通过注入的 Environment 实例执行命令。
    """
    def __init__(self, env: Optional[Environment] = None):
        """
        Args:
            env: Optional environment instance. Can be injected later via configure.
        """
        super().__init__()
        self.env = env
    
    @property
    def name(self) -> str:
        return "bash"

    @property
    def description(self) -> str:
        return """Run commands in a bash shell\n
* When invoking this tool, the contents of the \"command\" parameter does NOT need to be XML-escaped.\n
* You don't have access to the internet via this tool.\n
* You do have access to a mirror of common linux and python packages via apt and pip.\n
* State is persistent across command calls and discussions with the user.\n
* To inspect a particular line range of a file, e.g. lines 10-25, try 'sed -n 10,25p /path/to/the/file'.\n
* Please avoid commands that may produce a very large amount of output.\n
* Please run long lived commands in the background, e.g. 'sleep 10 &' or start a server in the background."""

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The full bash command to execute."
                },
                "cwd": {
                    "type": "string",
                    "description": "Optional. The working directory to execute the command in. Defaults to the environment's current working directory."
                },
                "wait": {
                    "type": "boolean",
                    "description": "Whether to wait for command completion (default True). Set to False to run in background.",
                    "default": True
                }
            },
            "required": ["command"]
        }

    def configure(self, context: Dict[str, Any]):
        """Inject environment from context"""
        # If 'env' is directly provided in context (new style)
        if "env" in context and isinstance(context["env"], Environment):
            self.env = context["env"]
        # Fallback for backward compatibility (during migration) is handled by the Environment wrappers if needed,
        # but here we strictly expect an Environment object.
        elif not self.env:
             # If we are in transition, we might throw or log warning.
             # For now, we assume the Runner is updated to pass 'env'.
             pass

    @schema_strict_validator
    def execute(self, command: str = None, cmd: str = None, cwd: Optional[str] = None, wait: bool = True) -> str:
        # Support legacy 'cmd' parameter
        final_command = command or cmd
        if not final_command:
            return "Error: Command is required."

        if not self.env:
            return "Error: No execution environment configured for BashTool."

        # Delegate execution to the environment
        # The environment implementation handles safety checks (Local) or API calls (E2B/Docker)
        return self.env.run_command(final_command, cwd=cwd, background=(not wait))
