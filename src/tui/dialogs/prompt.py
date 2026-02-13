"""
Prompt Dialog for TUI
Used for text input (API keys, etc.)
"""

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Static, Input
from textual.containers import Vertical
from textual.binding import Binding
from textual import on
from typing import Callable, Optional


class DialogPrompt(ModalScreen):
    """
    Text input dialog.
    Used for entering API keys, rename, etc.
    """
    
    BINDINGS = [
        Binding("escape", "dismiss", "Cancel"),
    ]
    
    DEFAULT_CSS = """
    DialogPrompt {
        align: center middle;
    }
    
    DialogPrompt > Vertical {
        width: 50;
        max-width: 80%;
        background: $surface;
        border: solid $primary;
        padding: 1 2;
    }
    
    DialogPrompt .title {
        text-style: bold;
        color: $text;
        padding-bottom: 1;
    }
    
    DialogPrompt .description {
        color: $text-muted;
        padding-bottom: 1;
    }
    
    DialogPrompt Input {
        margin-bottom: 1;
    }
    
    DialogPrompt .error {
        color: $error;
    }
    
    DialogPrompt .hint {
        color: $text-muted;
        padding-top: 1;
    }
    """
    
    def __init__(
        self,
        title: str,
        placeholder: str = "",
        description: str = "",
        password: bool = False,
        validate: Optional[Callable[[str], Optional[str]]] = None,
        on_confirm: Optional[Callable[[str], None]] = None,
    ):
        super().__init__()
        self.dialog_title = title
        self.placeholder = placeholder
        self.description = description
        self.password = password
        self.validate = validate
        self.on_confirm_callback = on_confirm
        self._error: Optional[str] = None
    
    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.dialog_title, classes="title")
            
            if self.description:
                yield Static(self.description, classes="description")
            
            yield Input(
                placeholder=self.placeholder,
                password=self.password,
                id="input"
            )
            
            yield Static("", id="error", classes="error")
            yield Static(r"\[Enter] Confirm  \[Esc] Cancel", classes="hint")
    
    def on_mount(self):
        """Focus input on mount"""
        self.query_one("#input", Input).focus()
    
    @on(Input.Submitted, "#input")
    def on_input_submitted(self, event: Input.Submitted):
        """Handle enter key"""
        value = event.value.strip()
        
        # Validate
        if self.validate:
            error = self.validate(value)
            if error:
                self.query_one("#error", Static).update(error)
                return
        
        # Callback
        if self.on_confirm_callback:
            self.on_confirm_callback(value)
        
        self.dismiss(value)
    
    def action_dismiss(self):
        """Cancel dialog"""
        self.dismiss(None)
