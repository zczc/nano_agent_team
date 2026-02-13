"""
Models Management Screen
Allows users to manage Providers (Base URL, API Key) and Models (Name, ID).
"""

from textual.app import ComposeResult
from textual.screen import Screen, ModalScreen
from textual.widgets import Static, ListView, ListItem, Input, Header, Footer, Button, Label
from textual.containers import Vertical, Horizontal, Container
from textual.binding import Binding
from textual import on, work
from textual.message import Message as TextualMessage
from typing import Optional, List, Dict
import sys
from rich.markup import escape
import os

# Ensure backend can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.infra.config import Config
from backend.infra.auth import AuthManager
from ..state import state, ModelInfo
from ..dialogs.api_key import DialogApiKey

class InputDialog(ModalScreen):
    """Generic input dialog for adding/editing items"""
    
    DEFAULT_CSS = """
    InputDialog {
        align: center middle;
    }
    InputDialog > Vertical {
        width: 60;
        height: auto;
        border: solid $primary;
        background: $surface;
        padding: 1 2;
    }
    InputDialog Input {
        margin: 1 0;
    }
    InputDialog Label {
        margin-top: 1;
        color: $text-muted;
    }
    """
    
    def __init__(self, title: str, fields: List[Dict[str, str]], on_submit):
        super().__init__()
        self.dialog_title = title
        self.fields = fields
        self.on_submit = on_submit
        
    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.dialog_title, classes="title")
            for field in self.fields:
                yield Label(field["label"])
                yield Input(
                    value=field.get("value", ""),
                    placeholder=field.get("placeholder", ""),
                    id=field["id"]
                )
            yield Static(r"\[Enter] Save  \[Esc] Cancel", classes="hint")
            
    def on_mount(self):
        first_input = self.query(Input).first()
        if first_input:
            first_input.focus()

    @on(Input.Submitted)
    def on_submit_action(self):
        result = {}
        for field in self.fields:
            result[field["id"]] = self.query_one(f"#{field['id']}", Input).value
        self.on_submit(result)
        self.dismiss()


class ModelsScreen(Screen):
    """
    Screen for managing LLM Configurations.
    """
    
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("a", "add_item", "Add"),
        Binding("e", "edit_item", "Edit"),
        Binding("d", "delete_item", "Delete"),
        Binding("k", "set_key", "Set API Key"),
        Binding("enter", "select_active", "Select Active"),
        Binding("left", "focus_providers", "Providers"),
        Binding("right", "focus_models", "Models"),
    ]
    
    CSS = """
    ModelsScreen {
        background: $background;
    }
    
    ModelsScreen .main-container {
        height: 1fr;
        layout: horizontal;
    }
    
    ModelsScreen .panel {
        width: 1fr;
        height: 100%;
        border: solid $border;
        margin: 1;
        background: $surface;
        display: block;
    }
    
    ModelsScreen .panel:focus-within {
        border: solid $primary;
    }
    
    ModelsScreen .panel-header {
        background: $primary 20%;
        color: $text;
        text-align: center;
        text-style: bold;
        padding: 1;
    }
    
    ModelsScreen ListView {
        height: 1fr;
    }
    
    ModelsScreen ListItem {
        padding: 1 2;
        content-align: left middle;
    }
    
    ModelsScreen ListItem:hover {
        background: $primary 10%;
    }
    
    ModelsScreen ListItem.--highlighted {
        background: $primary 20%;
    }
    
    ModelsScreen .active-indicator {
        color: $success;
        text-style: bold;
    }
    """
    
    def compose(self) -> ComposeResult:
        yield Header()
        
        with Container(classes="main-container"):
            # Left Panel: Providers
            with Vertical(classes="panel", id="panel-providers"):
                yield Static("Providers", classes="panel-header")
                yield ListView(id="list-providers")
            
            # Right Panel: Models
            with Vertical(classes="panel", id="panel-models"):
                yield Static("Models", classes="panel-header")
                yield ListView(id="list-models")
                
        yield Footer()

    def on_mount(self):
        self._refresh_providers()
        self.query_one("#list-providers").focus()

    def _refresh_providers(self):
        Config.initialize()
        providers = Config.get_all_providers()
        
        list_view = self.query_one("#list-providers", ListView)
        current_idx = list_view.index
        list_view.clear()
        
        for pid, data in providers.items():
            base_url = data.get("base_url", "")
            item = ListItem(
                Static(f"{escape(pid)}\n[dim]{escape(base_url)}[/dim]", markup=True),
                id=f"provider_{pid}"
            )
            list_view.append(item)
            
        if current_idx is not None and len(list_view.children) > current_idx:
            list_view.index = current_idx
            
        self._refresh_models()

    def _refresh_models(self):
        list_view = self.query_one("#list-models", ListView)
        list_view.clear()
        
        provider_id = self._get_selected_provider_id()
        if not provider_id:
            list_view.append(ListItem(Static("[dim]Select a provider[/dim]", markup=True), disabled=True))
            return

        Config.initialize()
        provider_config = Config.get_all_providers().get(provider_id, {})
        models = provider_config.get("models", [])
        
        current_model = state.current_model
        
        for m in models:
            mid = m["id"]
            name = m["name"]
            
            # Indicators
            active_mark = "● " if (current_model and mid == current_model.model_id and provider_id == current_model.provider_id) else "  "
            
            display = f"{active_mark}{escape(name)}\n[dim]{escape(mid)}[/dim]"
            
            item = ListItem(
                Static(display, markup=True),
                id=f"model_{mid}"
            )
            if active_mark.strip():
                item.add_class("active-indicator")
                
            list_view.append(item)

    def _get_selected_provider_id(self) -> Optional[str]:
        list_view = self.query_one("#list-providers", ListView)
        if list_view.highlighted_child is None:
            return None
        return list_view.highlighted_child.id.replace("provider_", "")

    def _get_selected_model_id(self) -> Optional[str]:
        list_view = self.query_one("#list-models", ListView)
        if list_view.highlighted_child is None:
            return None
        return list_view.highlighted_child.id.replace("model_", "")

    @on(ListView.Highlighted, "#list-providers")
    def on_provider_highlighted(self, event: ListView.Highlighted):
        self._refresh_models()

    # --- Actions ---

    def action_focus_providers(self):
        self.query_one("#list-providers").focus()

    def action_focus_models(self):
        self.query_one("#list-models").focus()

    def action_add_item(self):
        if self.query_one("#panel-providers").has_focus:
            # Add Provider
            def on_add_provider(data):
                pid = data["id"].strip()
                url = data["url"].strip()
                if pid:
                    Config.update_provider(pid, url)
                    self._refresh_providers()
                    
            self.app.push_screen(InputDialog(
                "Add Provider",
                [
                    {"id": "id", "label": "Provider ID (e.g. 'openai')", "placeholder": "openai"},
                    {"id": "url", "label": "Base URL", "placeholder": "https://api.openai.com/v1"}
                ],
                on_add_provider
            ))
            
        elif self.query_one("#panel-models").has_focus:
            # Add Model to selected provider
            pid = self._get_selected_provider_id()
            if not pid:
                self.notify("Select a provider first", severity="warning")
                return
                
            def on_add_model(data):
                mid = data["id"].strip()
                name = data["name"].strip() or mid
                if mid:
                    Config.add_model(pid, name, mid)
                    self._refresh_models()

            self.app.push_screen(InputDialog(
                f"Add Model to {pid}",
                [
                    {"id": "id", "label": "Model ID (as sent to API)", "placeholder": "gpt-4o"},
                    {"id": "name", "label": "Display Name", "placeholder": "GPT-4o"}
                ],
                on_add_model
            ))

    def action_edit_item(self):
        if self.query_one("#panel-providers").has_focus:
            pid = self._get_selected_provider_id()
            if not pid: return
            
            # Get current URL
            current_config = Config.get_all_providers().get(pid, {})
            current_url = current_config.get("base_url", "")
            
            def on_edit_provider(data):
                url = data["url"].strip()
                Config.update_provider(pid, url)
                self._refresh_providers()

            self.app.push_screen(InputDialog(
                f"Edit Provider {pid}",
                [
                    {"id": "url", "label": "Base URL", "value": current_url}
                ],
                on_edit_provider
            ))
            
        elif self.query_one("#panel-models").has_focus:
            pid = self._get_selected_provider_id()
            mid = self._get_selected_model_id()
            if not pid or not mid: return
            
            # Find current details
            current_config = Config.get_all_providers().get(pid, {})
            models = current_config.get("models", [])
            model_data = next((m for m in models if m["id"] == mid), {})
            
            def on_edit_model(data):
                new_name = data["name"].strip()
                Config.add_model(pid, new_name, mid) # add_model updates if exists
                self._refresh_models()

            self.app.push_screen(InputDialog(
                f"Edit Model {mid}",
                [
                    {"id": "name", "label": "Display Name", "value": model_data.get("name", "")}
                ],
                on_edit_model
            ))

    def action_delete_item(self):
        if self.query_one("#panel-providers").has_focus:
            pid = self._get_selected_provider_id()
            if pid:
                Config.delete_provider(pid)
                self._refresh_providers()
                
        elif self.query_one("#panel-models").has_focus:
            pid = self._get_selected_provider_id()
            mid = self._get_selected_model_id()
            if pid and mid:
                Config.delete_model(pid, mid)
                self._refresh_models()

    def action_set_key(self):
        # Always sets key for selected provider
        pid = self._get_selected_provider_id()
        if not pid:
            self.notify("Select a provider first", severity="warning")
            return
            
        self.app.push_screen(DialogApiKey(
            provider_id=pid,
            provider_name=pid,
            on_success=lambda _: self.notify(f"API Key saved for {pid}")
        ))

    def action_select_active(self):
        # Select currently focused model as active
        if self.query_one("#panel-models").has_focus:
            pid = self._get_selected_provider_id()
            mid = self._get_selected_model_id()
            if pid and mid:
                # Store active model as provider/model in new config if needed, 
                # OR Config.set_active_model expects just the model ID if it's unique?
                # The config logic for set_active_model calls Config.save, putting it in "active_model" field.
                # State logic expects ModelInfo object.
                
                # Update Config
                # We'll use "provider/model" format for active_model to be precise
                full_model_id = f"{pid}/{mid}"  
                Config.set_active_model(full_model_id)
                self.notify(f"Set active model: {mid}")
                
                # Update Runtime State (which updates TUI status bar etc)
                Config.initialize() # Reload config to be safe
                provider_cfg = Config.get_all_providers().get(pid, {})
                model_data = next((m for m in provider_cfg.get("models", []) if m["id"] == mid), {})
                
                model_info = ModelInfo(
                    provider_id=pid, 
                    model_id=mid, 
                    name=model_data.get("name", mid)
                )
                state.set_model(model_info)
                
                self._refresh_models()
