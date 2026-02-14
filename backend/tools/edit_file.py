from typing import Dict, Any, Optional
import os
from backend.tools.base import BaseTool
from backend.llm.decorators import schema_strict_validator
from backend.infra.environment import Environment

class EditFileTool(BaseTool):
    """
    EditFileTool: Replace a unique string in a file with new content.
    """
    def __init__(self, env: Optional[Environment] = None):
        super().__init__()
        self.env = env
    
    @property
    def name(self) -> str:
        return "edit_file"
    
    @property
    def description(self) -> str:
        return (
            "Replace a UNIQUE string segment in an existing file with new content. "
            "Use for: modifying configs, fixing bugs, updating functions, partial edits. "
            "Supports: .txt, .md, .yaml, .yml, .json, .csv, .tsv. "
            "Do NOT use for creating new files or complete rewrites."
        )
    
    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "The path to the file to edit."
                },
                "old_str": {
                    "type": "string",
                    "description": "The exact string segment to be replaced."
                },
                "new_str": {
                    "type": "string",
                    "description": "The new string content to insert."
                }
            },
            "required": ["file_path", "old_str", "new_str"]
        }
    
    def configure(self, context: Dict[str, Any]):
        """Inject environment"""
        if "env" in context and isinstance(context["env"], Environment):
            self.env = context["env"]

    @schema_strict_validator
    def execute(self, file_path: str, old_str: str, new_str: str) -> str:
        if not self.env:
            return "Error: No execution environment configured."

        if not old_str:
            return "Error: old_str cannot be empty."

        try:
            if not self.env.file_exists(file_path):
                return f"Error: File '{file_path}' does not exist."

            content = self.env.read_file(file_path)
            if content.startswith("Error"):
                return content

            occurrences = content.count(old_str)
            
            if occurrences == 0:
                return f"fail: old_str not found in '{file_path}'."
            
            if occurrences > 1:
                return f"fail: old_str found multiple times ({occurrences}) in '{file_path}'. Please provide a more specific segment to ensure a unique replacement."

            # Perform unique replacement
            new_content = content.replace(old_str, new_str)
            
            # Write back
            result = self.env.write_file(file_path, new_content)
            if result.startswith("Success") or "Success" in result:
                return "success"
            else:
                return result
                
        except Exception as e:
            return f"Error editing file '{file_path}': {str(e)}"

    def get_status_message(self, **kwargs) -> str:
        file_path = kwargs.get('file_path', 'file')
        return f"\n\n编辑文件: {os.path.basename(file_path)}...\n"
