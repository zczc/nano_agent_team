from typing import Dict, Any, Optional, Callable
from backend.tools.base import BaseTool
from backend.llm.decorators import schema_strict_validator

class AskUserTool(BaseTool):
    """
    Pauses execution to ask the user a question.
    """
    def __init__(self, input_callback: Optional[Callable[[str], str]] = None):
        super().__init__()
        self.input_callback = input_callback
        
    @property
    def name(self) -> str:
        return "ask_user"

    @property
    def description(self) -> str:
        return (
            "Pauses execution to ask the user a question and waits for their input from the command line. "
            "Useful for clarifying requirements or requesting confirmation."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to ask the user."
                }
            },
            "required": ["question"]
        }

    def configure(self, context: Dict[str, Any]):
        """Inject input callback from context if provided"""
        if "input_callback" in context:
             self.input_callback = context["input_callback"]

    @schema_strict_validator
    def execute(self, question: str, **kwargs) -> str:
        # 1. Use callback if available (TAP stdio or TUI dialog)
        if self.input_callback:
            return self.input_callback(question)

        # 2. Fallback to CLI input (only in direct CLI mode without TUI/TAP)
        print(f"\n[AskUser] {question}")
        try:
            user_input = input("> ")
            return user_input.strip()
        except EOFError:
            # stdin closed (piped process / TAP mode without callback)
            return ""
