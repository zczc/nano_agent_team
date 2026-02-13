"""
Main TUI Application
Inspired by OpenCode's app.tsx
"""

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Static, Input, Footer, Header
from textual.containers import Vertical, Horizontal, Container
from textual.screen import Screen
from textual import on, work
from typing import Optional
from backend.utils.logger import Logger

from .themes import DARK, LIGHT
from .commands import commands, Command
from .state import state
from .constants import EXIT_KEYWORDS, get_mode_display
from .dialogs import DialogProvider, DialogModel, DialogCommand, DialogApiKey
from .screens import SessionScreen, ModelsScreen, AgentMonitorScreen
from backend.infra.config import Config



class HomeScreen(Screen):
    """
    Home screen with centered logo and input.
    Inspired by OpenCode's home route.
    """
    
    BINDINGS = [
        Binding("ctrl+p", "select_provider", "Connect", priority=True),
        Binding("ctrl+o", "select_model", "Select Model", priority=True),
        Binding("tab", "toggle_mode", "Toggle Mode"),
        Binding("ctrl+s", "cycle_model", "Quick Switch Model"),
        Binding("ctrl+x", "command_palette", "Command Palette"),
        Binding("ctrl+c", "app.force_quit", "Quit"),
    ]
    
    DEFAULT_CSS = """
    HomeScreen {
        background: $background;
    }
    
    HomeScreen .logo-container {
        align: center middle;
        height: 1fr;
    }
    
    HomeScreen .logo {
        text-align: center;
        color: $primary;
        text-style: bold;
    }
    
    HomeScreen .input-container {
        align: center middle;
        width: 100%;
        max-width: 80;
        padding: 0 4;
    }
    
    HomeScreen #prompt {
        width: 100%;
        border: solid $border;
    }
    
    HomeScreen #prompt:focus {
        border: solid $primary;
    }
    
    HomeScreen .tips {
        text-align: center;
        color: $text-muted;
        padding: 1;
    }
    
    HomeScreen .status-bar {
        dock: bottom;
        height: 1;
        background: $surface;
        padding: 0 2;
    }
    
    HomeScreen .status-left {
        width: 1fr;
        color: $text-muted;
    }
    
    HomeScreen .status-right {
        width: auto;
        color: $text;
    }
    
    HomeScreen .model-name {
        color: $primary;
    }
    """
    
    def compose(self) -> ComposeResult:
        # Main content area
        with Vertical(classes="logo-container"):
            yield Static("", classes="spacer", id="spacer-top")
            yield Static(self._logo(), classes="logo", id="logo")
            
            with Container(classes="input-container"):
                yield Input(placeholder=">>> Enter your prompt...", id="prompt")
            
            yield Static(self._tips(), classes="tips", id="tips")
            yield Static("", classes="spacer")
        
        # Status bar
        with Horizontal(classes="status-bar"):
            yield Static(self._get_status_left(), classes="status-left", id="status-left")
            yield Static(self._get_status_right(), classes="status-right", id="status-right")
    
    def _logo(self) -> str:
        """Generate ASCII logo"""
        return """
╔═════════════════════════════════════════════╗
║               NANO AGENT TEAM               ║
║                    ▚   ▞                    ║
║                ▞▒▒▚ ᵔ▄ᵔ ▞▒▒▚                ║
║                ▚▒▒▞  █  ▞▒▒▞                ║
║                 ▞▒▒▞ ▀ ▚▒▒▚                 ║
║                      ▼                      ║
╚═════════════════════════════════════════════╝
        """
    
    def _tips(self) -> str:
        """Generate tips text"""
        current_model = state.current_model
        model_text = f" ({current_model.provider_id}/{current_model.name})" if current_model else ""
        mode_display = self._get_mode_display()
        
        tips = [
            rf"\[tab] Toggle Mode {mode_display}",
            rf"\[ctrl+o] Select Model{model_text}",
            r"\[ctrl+p] Connect Provider", 
            rf"\[ctrl+s] Quick Switch Model",
            rf"\[ctrl+c] Quit",
        ]
        return "  |  ".join(tips)
    
    def _get_mode_display(self) -> str:
        """Get mode badge for display"""
        return get_mode_display(state.agent_mode)
    
    def _get_status_left(self) -> str:
        """Get left status text"""
        import os
        return os.getcwd()
    
    def _get_status_right(self) -> str:
        """Get right status text (current model)"""
        model = state.current_model
        if model:
            return f"[{model.provider_id}] {model.name or model.model_id}"
        return "[No model selected]"
    
    def on_mount(self):
        """Focus input on mount"""
        self.query_one("#prompt", Input).focus()
    
    def _update_status(self):
        """Update status bar"""
        self.query_one("#status-right", Static).update(self._get_status_right())
        self.query_one("#tips", Static).update(self._tips())
    
    def action_command_palette(self):
        """Show command palette"""
        self.app.push_screen(DialogCommand())

    def action_monitor_agents(self):
        """Show agent monitor"""
        from backend.infra.config import Config
        Logger.info(f"[App] Opening Monitor with blackboard_dir: {Config.BLACKBOARD_ROOT}")
        self.app.push_screen(AgentMonitorScreen())

    
    def action_select_provider(self):
        """Show provider selection"""
        self.app._cmd_select_provider()
    
    def action_select_model(self):
        """Show model selection dialog"""
        self.app._cmd_select_model()
    
    def action_cycle_model(self):
        """Cycle through recent models"""
        self.app._cmd_cycle_model()
        self._update_status()
    
    def action_toggle_mode(self):
        """Toggle between Chat and Swarm mode"""
        from .state import AgentMode
        new_mode = state.toggle_agent_mode()
        if new_mode == AgentMode.CHAT:
            self.notify("Switched to Chat mode", severity="information")
        else:
            self.notify("Switched to Swarm mode", severity="information")
        self._update_status()
    
    @on(Input.Submitted, "#prompt")
    def on_prompt_submitted(self, event: Input.Submitted):
        """Handle prompt submission"""
        prompt = event.value.strip()
        if not prompt:
            return
            
        # Check for exit keywords
        if prompt.lower() in EXIT_KEYWORDS:
            self.app.action_force_quit()
            return

        # Check command
        if prompt.startswith("/"):
            # Clear input immediately
            event.input.value = ""
            from .slash_commands import handle_slash_command
            handle_slash_command(self, prompt, source="home")
            return
        
        # Check if model is selected
        if not state.current_model:
            self.notify("Please select a model first [ctrl+s]", severity="warning")
            return
        
        # Clear input
        event.input.value = ""
        
        # Navigate to session screen with the prompt
        self.app.push_screen(SessionScreen(initial_prompt=prompt))


class SwarmTUI(App):
    """
    Main TUI Application.
    Manages screens, commands, and global state.
    """
    
    CSS = """
    /* Global styles - Nord Theme */
    $background: #2e3440;
    $surface: #3b4252;
    $text: #eceff4;
    $text-muted: #d8dee9;
    $primary: #88c0d0;
    $secondary: #b48ead;
    $accent: #88c0d0;
    $success: #a3be8c;
    $warning: #ebcb8b;
    $error: #bf616a;
    $border: #4c566a;
    $surface-darken-1: #343b49;
    $surface-darken-2: #2b303b;
    $warning-lighten-2: #f2d7a8;
    
    Screen {
        background: $background;
    }
    
    Input {
        background: $surface;
        color: $text;
        border: solid $border;
    }
    
    Input:focus {
        border: solid $primary;
    }
    
    ListView {
        background: transparent;
    }
    
    ListItem {
        background: transparent;
    }
    
    ListItem:hover {
        background: $primary 15%;
    }
    
    Static {
        background: transparent;
    }

    ToastRack {
        align: right bottom;
        margin: 1 2;
    }
    
    Toast {
        width: auto;
        max-width: 50%;
    }
    """
    
    # Disable Textual's built-in command palette (ctrl+p) to use our own
    ENABLE_COMMAND_PALETTE = False
    
    # Shorter notification timeout (default is 5 seconds)
    NOTIFICATION_TIMEOUT = 2.5
    
    SCREENS = {
        "session": SessionScreen,
        "models_mgmt": ModelsScreen,
    }

    
    def __init__(self, cli_model: str = None):
        """Initialize app with optional CLI model"""
        super().__init__()
        self.cli_model = cli_model
        
        # Set model from CLI if provided
        if cli_model:
            state.set_model_from_key(cli_model)
        
        # Register global commands
        self._register_commands()
    
    def _register_commands(self):
        """Register all app-level commands"""
        commands.register_many([
            Command(
                title="Select Model",
                value="model.list",
                category="Model",
                keybind="ctrl+o",
                suggested=True,
                on_select=self._cmd_select_model
            ),
            Command(
                title="Connect Provider",
                value="provider.connect",
                category="Provider",
                keybind="ctrl+p",
                suggested=True,
                on_select=self._cmd_select_provider
            ),
            Command(
                title="Cycle Model",
                value="model.cycle",
                category="Model",
                keybind="tab",
                hidden=True,
                on_select=self._cmd_cycle_model
            ),
            Command(
                title="New Session",
                value="session.new",
                category="Session",
                keybind="n",
                on_select=self._cmd_new_session
            ),
            Command(
                title="Manage Models",
                value="model.manage",
                category="Models",
                keybind="ctrl+t",
                on_select=lambda: self.push_screen("models_mgmt")
            ),
        ])
    
    def _refresh_ui(self):
        """Force refresh of the current screen if applicable"""
        if hasattr(self.screen, "_update_status"):
            self.screen._update_status()

    def _cmd_select_model(self):
        """Command: Select model (filtered by provider if selected)"""
        # Primary filter: Explicitly selected provider
        provider_filter = state.selected_provider_id
        
        # Fallback filter: Current model's provider
        if not provider_filter and state.current_model:
            provider_filter = state.current_model.provider_id
            
        if provider_filter:
            self.notify(f"Showing models for {provider_filter}", severity="information")
            
        # Use on_dismiss mechanism (callback arg to push_screen)
        # The dialog returns the selected model_info (or None)
        self.push_screen(
            DialogModel(provider_filter=provider_filter),
            self._on_model_selected_dialog
        )

    def _on_model_selected_dialog(self, model_info):
        """Callback when model is selected from dialog (runs after dialog closes)"""
        if model_info:
             self._refresh_ui()

    def _cmd_select_provider(self):
        """Command: Connect provider - check keys and prompt if needed"""
        def on_provider_selected(provider_id: str):
            if not provider_id:
                return

            # Check for reconfiguration signal
            force_reconfigure = False
            if provider_id.startswith("RECONFIGURE:"):
                provider_id = provider_id.replace("RECONFIGURE:", "")
                force_reconfigure = True
            
            from backend.infra.auth import AuthManager
            from backend.infra.config import Config
            
            auth_info = AuthManager.get(provider_id)
            
            if auth_info and not force_reconfigure:
                # Key exists and not reconfiguring - set as selected
                state.set_selected_provider(provider_id)
                
                # Auto-select first model if available
                providers = Config.get_all_providers()
                provider_cfg = providers.get(provider_id, {})
                models = provider_cfg.get("models", [])
                
                if models:
                    first_model = models[0]
                    from .state import ModelInfo
                    model_info = ModelInfo(
                        provider_id=provider_id,
                        model_id=first_model["id"],
                        name=first_model["name"]
                    )
                    state.set_model(model_info)
                    self.notify(f"Connected to {provider_id}. Selected {first_model['name']}.", severity="information")
                    # Refresh UI
                    self._refresh_ui()
                else:
                    self.notify(f"Connected to {provider_id}. Press [ctrl+s] to select a model.", severity="information")
                    self._refresh_ui()
            else:
                # No key or forced reconfigure - prompt for API key
                provider_name = provider_id.title().replace("-", " ").replace("_", " ")
                self.push_screen(
                    DialogApiKey(provider_id=provider_id, provider_name=provider_name),
                    self._on_key_entered
                )
        
        # Pass callback to push_screen, NOT to DialogProvider constructor
        self.push_screen(DialogProvider(), on_provider_selected)
    
    def _on_key_entered(self, provider_id: Optional[str]):
        """Called after API key dialog is dismissed"""
        if not provider_id:
            return

        state.set_selected_provider(provider_id)
        
        # Auto-select first model
        from backend.infra.config import Config
        Config.initialize()
        providers = Config.get_all_providers()
        provider_cfg = providers.get(provider_id, {})
        models = provider_cfg.get("models", [])
        
        if models:
            first_model = models[0]
            from .state import ModelInfo
            model_info = ModelInfo(
                provider_id=provider_id,
                model_id=first_model["id"],
                name=first_model["name"]
            )
            state.set_model(model_info)
            self.notify(f"API Key saved. Selected {first_model['name']}.", severity="information")
            self._refresh_ui()
        else:
            self.notify(f"API Key saved for {provider_id}. Press [ctrl+s] to select a model.", severity="information")
            self._refresh_ui()
    
    def _cmd_cycle_model(self):
        """Command: Cycle through recent models"""
        state.cycle_recent()
        self.notify(f"Model: {state.current_model.name if state.current_model else 'None'}")
        self._refresh_ui()
    
    def _cmd_new_session(self):
        """Command: New session"""
        state.clear_agent_messages()
        # If on session screen, clear it
        if hasattr(self.screen, 'action_new_session'):
            self.screen.action_new_session()
    
    def on_mount(self):
        """Initialize app"""
        self.push_screen(SessionScreen())
    
    def action_force_quit(self):
        """Quit the application immediately"""
        self.exit()


def run():
    """Run the TUI application"""
    app = SwarmTUI()
    app.run()


if __name__ == "__main__":
    run()
