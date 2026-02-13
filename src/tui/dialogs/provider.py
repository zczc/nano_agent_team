"""
Provider Connection Dialog for TUI
Shows providers from llm_config.json with connection status
"""

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Static, ListView, ListItem, Input
from textual.containers import Vertical
from textual.binding import Binding
from textual import on
from dataclasses import dataclass
from typing import Optional, List, Callable
import sys
from rich.markup import escape
import os

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.infra.auth import AuthManager
from backend.infra.config import Config


@dataclass
class ProviderOption:
    """Provider display option"""
    id: str
    name: str
    base_url: str = ""
    connected: bool = False
    has_key: bool = False
    model_count: int = 0


class DialogProvider(ModalScreen):
    """
    Provider connection dialog.
    Shows providers from llm_config.json with connection status.
    - ● Connected (key exists and valid)
    - ○ Not connected (no key or invalid)
    """
    
    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("enter", "select", "Connect"),
        Binding("ctrl+r", "reconfigure", "Reconfigure Key"),
    ]
    
    # ... (Keep existing CSS) ...

    # ... (Keep methods) ...

    def action_reconfigure(self):
        """Handle reconfigure action"""
        listview = self.query_one("#providers", ListView)
        if listview.highlighted_child is None:
            return
        
        item_id = listview.highlighted_child.id
        if item_id and item_id.startswith("prov_"):
            provider_id = item_id[5:]
            
            # Signal reconfigure
            if self.on_select_callback:
                # We need a way to signal "reconfigure". 
                # Since callback accepts str, we could use a prefix or change contract.
                # But to avoid breaking, let's assume the callback handles a special tuple or we add a new callback?
                # Simpler: The callback in app.py parses the ID.
                # OR: return a magic string like "RECONFIGURE:{id}"
                self.on_select_callback(f"RECONFIGURE:{provider_id}")
            self.dismiss(f"RECONFIGURE:{provider_id}")
    
    DEFAULT_CSS = """
    DialogProvider {
        align: center middle;
    }
    
    DialogProvider > Vertical {
        width: 60;
        max-width: 85%;
        max-height: 80%;
        background: $surface;
        border: solid $primary;
        padding: 1 2;
    }
    
    DialogProvider .title {
        text-style: bold;
        color: $text;
        padding-bottom: 1;
    }
    
    DialogProvider ListView {
        height: auto;
        max-height: 14;
        background: transparent;
    }
    
    DialogProvider ListItem {
        padding: 0 1;
    }
    
    DialogProvider ListItem:hover {
        background: $primary 20%;
    }
    
    DialogProvider ListItem.-highlight {
        background: $primary 40%;
    }
    
    DialogProvider ListView:focus > ListItem.-highlight {
        background: $primary;
        color: $background;
        text-style: bold;
    }
    
    DialogProvider .connected {
        color: $success;
    }
    
    DialogProvider .not-connected {
        color: $text-muted;
    }
    
    DialogProvider .hint {
        color: $text-muted;
        padding-top: 1;
    }
    
    DialogProvider .category {
        color: $text-muted;
        text-style: bold;
        padding: 1 0 0 0;
    }
    """
    
    def __init__(self, on_select: Optional[Callable[[str], None]] = None):
        super().__init__()
        self.on_select_callback = on_select
        self.providers: List[ProviderOption] = []
        self._load_providers()
    
    def _load_providers(self):
        """Load providers from llm_config.json"""
        try:
            Config.initialize()
            all_providers_config = Config.load_llm_config().get("providers", {})
        except Exception:
            all_providers_config = {}
        
        # Determine active provider
        from ..state import state
        # Primary source: selected_provider_id
        active_provider_id = state.selected_provider_id
        # Fallback source: current_model's provider
        if not active_provider_id and state.current_model:
            active_provider_id = state.current_model.provider_id
        
        self.providers = []
        for provider_id, provider_config in all_providers_config.items():
            base_url = provider_config.get("base_url", "")
            models = provider_config.get("models", [])
            env_keys = provider_config.get("env", [])
            
            # Check if key exists via env or auth storage
            has_key = AuthManager.has_key_for_provider(provider_id, env_keys)
            
            # Connected = Currently Selected/Active
            connected = (provider_id == active_provider_id)
            
            self.providers.append(ProviderOption(
                id=provider_id,
                name=provider_id.title().replace("-", " ").replace("_", " "),
                base_url=base_url,
                connected=connected,
                has_key=has_key,
                model_count=len(models)
            ))
        
        # Sort active first, then by name
        self.providers.sort(key=lambda p: (not p.connected, p.name.lower()))
    
    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Connect Provider", classes="title")
            yield ListView(id="providers")
            yield Static(r"\[Enter] Connect  \[Ctrl+R] Reconfigure  \[Esc] Close", classes="hint")
    
    def on_mount(self):
        """Populate list on mount"""
        self._refresh_list()
    
    def _refresh_list(self):
        """Refresh provider list"""
        listview = self.query_one("#providers", ListView)
        listview.clear()
        
        if not self.providers:
            listview.append(ListItem(
                Static("[dim]No providers configured. Add via [ctrl+e] menu.[/dim]", markup=True),
                disabled=True
            ))
            return
            
        for p in self.providers:
            listview.append(self._make_item(p))

    def _make_item(self, p: ProviderOption) -> ListItem:
        """Create a list item for a provider"""
        # Status logic:
        # 1. Active (Connected) -> Green ●
        # 2. Key Configured -> Blue ○
        # 3. No Key -> Dim ○
        
        if p.connected:
            indicator = "●"
            indicator_class = "connected"
            status_text = " [green]Active[/green]"
        elif p.has_key:
            indicator = "○"
            indicator_class = "configured"  # Define in CSS if needed, or use a color
            status_text = " [blue]Key Configured[/blue]"
        else:
            indicator = "○"
            indicator_class = "not-connected"
            status_text = " [dim]No Key[/dim]"
        
        content = f"[{indicator_class}]{indicator}[/{indicator_class}] [bold]{escape(p.name)}[/bold]"
        if p.model_count:
            content += f" [dim]({p.model_count} models)[/dim]"
        
        content += status_text
        
        return ListItem(Static(content, markup=True), id=f"prov_{p.id}")
    
    @on(ListView.Selected, "#providers")
    def on_provider_selected(self, event: ListView.Selected):
        """Handle provider selection"""
        self._select_current()
    
    def action_select(self):
        """Handle enter key"""
        self._select_current()
    
    def _select_current(self):
        """Select highlighted provider"""
        listview = self.query_one("#providers", ListView)
        if listview.highlighted_child is None:
            return
        
        item_id = listview.highlighted_child.id
        if item_id and item_id.startswith("prov_"):
            provider_id = item_id[5:]
            
            # Find provider
            provider = next((p for p in self.providers if p.id == provider_id), None)
            if provider:
                if self.on_select_callback:
                    self.on_select_callback(provider_id)
                self.dismiss(provider_id)
    
    def action_dismiss(self):
        """Close dialog"""
        self.dismiss(None)
