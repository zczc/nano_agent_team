"""
API Key Input Dialog for TUI
Prompts user to enter API key for a provider
"""

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Static, Input
from textual.containers import Vertical
from textual.binding import Binding
from textual import on
from typing import Optional, Callable
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.infra.auth import AuthManager


# Provider help URLs
PROVIDER_HELP = {
    "openai": "https://platform.openai.com/api-keys",
    "anthropic": "https://console.anthropic.com/settings/keys",
    "google": "https://aistudio.google.com/apikey",
    "deepseek": "https://platform.deepseek.com/api_keys",
    "openrouter": "https://openrouter.ai/keys",
    "groq": "https://console.groq.com/keys",
    "mistral": "https://console.mistral.ai/api-keys",
    "xai": "https://console.x.ai/",
    "together": "https://api.together.xyz/settings/api-keys",
}


class DialogApiKey(ModalScreen):
    """
    API Key input dialog.
    Securely saves to AuthManager.
    """
    
    BINDINGS = [
        Binding("escape", "dismiss", "Cancel"),
    ]
    
    DEFAULT_CSS = """
    DialogApiKey {
        align: center middle;
    }
    
    DialogApiKey > Vertical {
        width: 50;
        max-width: 80%;
        background: $surface;
        border: solid $primary;
        padding: 1 2;
    }
    
    DialogApiKey .title {
        text-style: bold;
        color: $text;
        padding-bottom: 1;
    }
    
    DialogApiKey .description {
        color: $text-muted;
        padding-bottom: 1;
    }
    
    DialogApiKey .link {
        color: $primary;
        padding-bottom: 1;
    }
    
    DialogApiKey Input {
        margin-bottom: 1;
    }
    
    DialogApiKey .error {
        color: $error;
    }
    
    DialogApiKey .hint {
        color: $text-muted;
        padding-top: 1;
    }
    """
    
    def __init__(
        self,
        provider_id: str,
        provider_name: str,
    ):
        super().__init__()
        self.provider_id = provider_id
        self.provider_name = provider_name
        self.help_url = PROVIDER_HELP.get(provider_id, "")
    
    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(f"Connect {self.provider_name}", classes="title")
            yield Static("Enter your API key:", classes="description")
            
            if self.help_url:
                yield Static(f"Get key: {self.help_url}", classes="link")
            
            yield Input(placeholder="sk-...", password=True, id="api_key")
            yield Static("", id="error", classes="error")
            yield Static(r"\[enter] save      \[esc] cancel", classes="hint")
    
    def on_mount(self):
        """Focus input on mount"""
        self.query_one("#api_key", Input).focus()
    
    @on(Input.Submitted, "#api_key")
    def on_key_submitted(self, event: Input.Submitted):
        """Handle API key submission"""
        key = event.value.strip()
        
        if not key:
            try:
                self.query_one("#error", Static).update("API key is required")
            except Exception:
                self.notify("API key is required", severity="error")
            return
            
        if len(key) < 10:
            try:
                self.query_one("#error", Static).update("API key seems too short")
            except Exception:
                self.notify("API key seems too short", severity="error")
            return
        
        # Save to AuthManager
        try:
            AuthManager.set(self.provider_id, {"type": "api", "key": key})
            self.dismiss(self.provider_id)
        except Exception as e:
            self.query_one("#error", Static).update(f"Error: {str(e)}")
    
    def action_dismiss(self):
        """Cancel dialog"""
        self.dismiss(None)
