"""
Model Selection Dialog for TUI
Shows models grouped by provider with favorites/recents
"""

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Static, ListView, ListItem
from textual.containers import Vertical
from textual.binding import Binding
from textual import on
from dataclasses import dataclass
from typing import Optional, List, Callable
import sys
from rich.markup import escape
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.infra.config import Config
from ..state import state, ModelInfo


@dataclass
class ModelOption:
    """Model display option"""
    provider_id: str
    model_id: str
    name: str
    provider_name: str = ""
    is_free: bool = False
    
    def to_model_info(self) -> ModelInfo:
        return ModelInfo(
            provider_id=self.provider_id,
            model_id=self.model_id,
            name=self.name
        )


class DialogModel(ModalScreen):
    """
    Model selection dialog.
    Shows models grouped by provider.
    """
    
    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("enter", "select", "Select"),
    ]
    
    DEFAULT_CSS = """
    DialogModel {
        align: center middle;
    }
    
    DialogModel > Vertical {
        width: 60;
        max-width: 85%;
        max-height: 85%;
        background: $surface;
        border: solid $primary;
        padding: 1 2;
    }
    
    DialogModel .title-row {
        layout: horizontal;
        height: auto;
        padding-bottom: 1;
    }
    
    DialogModel .title {
        text-style: bold;
        color: $text;
    }
    
    DialogModel ListView {
        height: auto;
        max-height: 20;
        background: transparent;
    }
    
    DialogModel ListItem {
        padding: 0 1;
    }
    
    DialogModel ListItem:hover {
        background: $primary 20%;
    }
    
    DialogModel ListItem.-highlight {
        background: $primary 40%;
    }
    
    DialogModel ListView:focus > ListItem.-highlight {
        background: $primary;
        color: $background;
        text-style: bold;
    }
    
    DialogModel .category {
        color: $text-muted;
        text-style: bold;
        padding: 1 0 0 0;
    }
    
    DialogModel .hint {
        color: $text;
        padding-top: 1;
    }
    """
    
    def __init__(
        self,
        on_select: Optional[Callable[[ModelInfo], None]] = None,
        on_providers: Optional[Callable[[], None]] = None,
        provider_filter: Optional[str] = None,
    ):
        super().__init__()
        self.on_select_callback = on_select
        self.on_providers_callback = on_providers
        self.provider_filter = provider_filter
        self.all_options: List[ModelOption] = []
        self._load_models()
    
    def _load_models(self):
        """Load models from config"""
        try:
            Config.initialize()
            providers = Config.get_all_providers()
        except Exception:
            providers = {}
        
        # Build model list
        self.all_options = []
        
        # Filter if requested
        if self.provider_filter:
            if self.provider_filter in providers:
                # Keep only filtered provider
                providers = {self.provider_filter: providers[self.provider_filter]}
            else:
                # Provider not found or no models
                providers = {}
        
        for provider_id, provider_config in providers.items():
            provider_name = provider_config.get("name", provider_id.title())
            models = provider_config.get("models", [])
            
            # models is a list of {name, id} dicts
            for model_config in models:
                model_id = model_config.get("id", "")
                model_name = model_config.get("name", model_id)
                if not model_id:
                    continue
                
                self.all_options.append(ModelOption(
                    provider_id=provider_id,
                    model_id=model_id,
                    name=model_name,
                    provider_name=provider_name,
                    is_free=model_config.get("free", False),
                ))
    
    def compose(self) -> ComposeResult:
        with Vertical():
            with Vertical(classes="title-row"):
                title = f"Select Model ({self.provider_filter})" if self.provider_filter else "Select Model"
                yield Static(title, classes="title")
            
            yield ListView(id="models")
            yield Static(r"\[Enter] Select  \[Esc] Close", classes="hint")
    
    def on_mount(self):
        """Initialize on mount"""
        self._refresh_list()
        self.query_one("#models", ListView).focus()
    
    def _refresh_list(self):
        """Refresh model list"""
        listview = self.query_one("#models", ListView)
        listview.clear()
        
        current_model = state.current_model
        
        # All models grouped by provider
        # Sort options by provider name then model name
        sorted_options = sorted(self.all_options, key=lambda x: (x.provider_name, x.name))
        
        providers = {}
        for opt in sorted_options:
            if opt.provider_name not in providers:
                providers[opt.provider_name] = []
            providers[opt.provider_name].append(opt)
        
        for provider_name in sorted(providers.keys()):
            listview.append(ListItem(Static(provider_name, classes="category"), disabled=True))
            for opt in providers[provider_name]:
                listview.append(self._make_item(opt, current_model))
    
    def _make_item(self, opt: ModelOption, current_model: Optional[ModelInfo] = None) -> ListItem:
        """Create list item for a model"""
        # Active indicator
        is_active = False
        if current_model and opt.provider_id == current_model.provider_id and opt.model_id == current_model.model_id:
            is_active = True
            
        if is_active:
            indicator = "●"
            prefix = f"[green]{indicator}[/green] "
        else:
            indicator = "○"
            prefix = f"[dim]{indicator}[/dim] "

        content = f"{prefix}[bold]{escape(opt.name)}[/bold] [dim]{escape(opt.provider_name)}[/dim]"
        if opt.is_free:
            content += " [green]Free[/green]"
        
        return ListItem(
            Static(content, markup=True),
            id=f"model_{opt.provider_id}_{opt.model_id}"
        )
    
    @on(ListView.Selected, "#models")
    def on_model_selected(self, event: ListView.Selected):
        """Handle model selection"""
        self._select_current()
    
    def action_select(self):
        """Handle enter key"""
        self._select_current()
    
    def _select_current(self):
        """Select highlighted model"""
        listview = self.query_one("#models", ListView)
        if listview.highlighted_child is None:
            return
        
        item_id = listview.highlighted_child.id
        if item_id and item_id.startswith("model_"):
            parts = item_id[6:].split("_", 1)
            if len(parts) == 2:
                provider_id, model_id = parts
                
                # Find model option
                opt = next((o for o in self.all_options 
                           if o.provider_id == provider_id and o.model_id == model_id), None)
                if opt:
                    model_info = opt.to_model_info()
                    state.set_model(model_info)
                    
                    if self.on_select_callback:
                        self.on_select_callback(model_info)
                    
                    self.dismiss(model_info)
    
    def action_show_providers(self):
        """Show providers dialog"""
        if self.on_providers_callback:
            self.on_providers_callback()
        self.dismiss("providers")
    
    def action_toggle_favorite(self):
        """Toggle favorite status of selected model"""
        listview = self.query_one("#models", ListView)
        if listview.highlighted_child is None:
            return
        
        item_id = listview.highlighted_child.id
        if item_id and item_id.startswith("model_"):
            parts = item_id[6:].split("_", 1)
            if len(parts) == 2:
                provider_id, model_id = parts
                
                opt = next((o for o in self.all_options 
                           if o.provider_id == provider_id and o.model_id == model_id), None)
                if opt:
                    model_info = opt.to_model_info()
                    is_fav = state.toggle_favorite(model_info)
                    opt.is_favorite = is_fav
                    self._refresh_list()
    
    def action_dismiss(self):
        """Close dialog"""
        self.dismiss(None)
