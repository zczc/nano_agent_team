"""
Session Screen for TUI
Main chat interface with dual-mode agent support
"""

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, Input, Select, Markdown
from textual.containers import Vertical, Horizontal, ScrollableContainer
from textual.binding import Binding
from textual import on, work
from typing import Optional
import threading
import traceback
from rich.markup import escape

from backend.utils.logger import Logger
from ..state import state, AgentMode
from ..constants import EXIT_KEYWORDS, get_mode_display
from ..commands import commands, Command
from ..dialogs import DialogModel, DialogCommand, ConfirmationDialog
from ..components.message import ChatMessage, create_message_widget, AssistantMessageWidget
from ..agent_bridge import AgentBridge, AgentConfig
from backend.infra.config import Config
from .monitor import AgentMonitorScreen

# Timeout (seconds) for blocking confirmation/input dialogs in worker threads
_DIALOG_TIMEOUT = 120

# Max number of message widgets in the chat area before old ones are removed
MAX_VISIBLE_MESSAGES = 20



class SessionScreen(Screen):
    """
    Chat session screen.
    Supports Chat and Swarm modes.
    """
    
    BINDINGS = [
        Binding("ctrl+c", "app.force_quit", "Quit Application"),
        Binding("ctrl+k", "stop_agent", "Stop Agent", priority=True),
        Binding("ctrl+n", "new_session", "New Session"),
        Binding("ctrl+p", "select_provider", "Select Provider"),
        Binding("ctrl+o", "select_model", "Select Model"),
        Binding("tab", "toggle_mode", "Toggle Mode"),
        Binding("ctrl+s", "cycle_model", "Switch Model"),
        Binding("ctrl+j", "monitor_agents", "Monitor Agents"),
    ]
    
    DEFAULT_CSS = """
    SessionScreen {
        background: $background;
    }
    
    SessionScreen .logo {
        dock: top;
        height: 10;
        background: $surface;
        content-align: center middle;
        text-align: center;
        color: $primary;
        text-style: bold;
    }
    
    SessionScreen .header {
        dock: top;
        height: 1;
        background: $surface;
        padding: 0 2;
    }
    
    SessionScreen .header-left {
        width: 1fr;
        color: $text;
    }
    
    SessionScreen .header-right {
        width: auto;
        color: $text-muted;
    }
    
    SessionScreen .model-name {
        color: $primary;
        text-style: bold;
    }
    
    SessionScreen #chat-area {
        height: 1fr;
        padding: 1 2;
    }
    
    SessionScreen #bottom-container {
        dock: bottom;
        height: auto;
        background: $surface;
    }
    
    SessionScreen .input-bar {
        height: auto;
        max-height: 5;
        padding: 1 2;
    }
    
    SessionScreen #message-input {
        width: 100%;
    }
    
    SessionScreen .hint-bar {
        color: $text-muted;
        overflow-y: auto;
    }
    
    SessionScreen .mode-badge {
        color: $warning;
        text-style: bold;
    }
    
    SessionScreen .question-container {
        background: $surface-lighten-1;
        border-left: solid $warning;
        margin: 1 2;
        padding: 1 2;
        height: auto;
    }
    
    SessionScreen .question-title {
        color: $warning;
        text-style: bold;
        padding-bottom: 1;
    }
    """
    
    def __init__(self, initial_prompt: str = "", mode: AgentMode = AgentMode.CHAT):
        super().__init__()
        self.initial_prompt = initial_prompt
        self.initial_mode = mode
        self.agent: Optional[AgentBridge] = None
        self.current_assistant_widget: Optional[AssistantMessageWidget] = None

        # Input handling state
        self._is_waiting_for_input = False
        self._input_event = threading.Event()
        self._input_response: Optional[str] = None
    
    def compose(self) -> ComposeResult:
        # Logo
        yield Static(self._get_logo(), classes="logo", id="logo")
        
        # Header
        with Horizontal(classes="header"):
            model_name = self._get_model_display()
            mode_display = self._get_mode_display()
            yield Static(f"[bold]Session[/bold]  {mode_display}  {model_name}", classes="header-left", markup=True, id="header-left")
            yield Static("", classes="header-right", id="header-right")
        
        # Chat area
        yield ScrollableContainer(id="chat-area")
        
        # Bottom area (Input + Hints)
        with Vertical(id="bottom-container"):
            # Input bar
            with Horizontal(classes="input-bar"):
                yield Input(placeholder=">>> Type your message...", id="message-input")
            
            # Hint bar
            yield Static(self._get_hints(), classes="hint-bar", id="hint-bar")
    
    def _get_logo(self) -> str:
        """Get ASCII logo for display"""
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
    
    def _get_model_display(self) -> str:
        """Get model name for display"""
        model = state.current_model
        if model:
            return f"[cyan]{escape(model.name or model.model_id)}[/cyan]"
        return "[dim]No model[/dim]"
    
    def _get_mode_display(self) -> str:
        """Get mode badge for display (uses shared state)"""
        return get_mode_display(state.agent_mode)
    
    def on_mount(self):
        """Initialize session on mount"""
        try:
            # Sync blackboard root if in Swarm mode to avoid polluting .blackboard
            if state.agent_mode == AgentMode.SWARM:
                state.sync_blackboard_root()

            # Initialize agent bridge (uses shared state mode)
            model_key = state.get_model_key()
            config = AgentConfig(
                mode=state.agent_mode,
                model_key=model_key,
                system_prompt=self._get_system_prompt(),
                swarm_max_iterations=state.swarm_max_iterations
            )
            self.agent = AgentBridge(config)
            self.agent.set_confirmation_callback(self._request_confirmation)
            self.agent.set_input_callback(self._request_input)
            self.agent.initialize(model_key)

            # Restore history messages to UI
            self._restore_history()
            
            # Focus input
            self.query_one("#message-input", Input).focus()
            
            # If initial prompt provided, send it
            if self.initial_prompt:
                self._send_message(self.initial_prompt)
        except Exception as e:
            self.notify(f"Error initializing session: {e}", severity="error", timeout=10)
            Logger.error(f"[Session] Error initializing session: {e}\n{traceback.format_exc()}")
    
    def _restore_history(self):
        """Restore history messages from shared state to UI"""
        chat_area = self.query_one("#chat-area", ScrollableContainer)
        
        for msg in state.agent_messages:
            chat_msg = ChatMessage(role=msg["role"], content=msg["content"])
            widget = create_message_widget(chat_msg)
            chat_area.mount(widget)
        
        # Scroll to bottom if there are messages
        if state.agent_messages:
            chat_area.scroll_end(animate=False)
    
    def _get_system_prompt(self) -> str:
        """Get system prompt for agent"""
        return """You are a helpful AI assistant. You have access to various tools to help answer questions.
Be concise and helpful. Format your responses using markdown when appropriate."""
    
    def _request_confirmation(self, message: str) -> bool:
        """
        Callback for agent to request confirmation (runs in worker thread).
        Uses threading.Event to wait for user input from TUI.
        Returns False on timeout.
        """
        # Container for result (nonlocal)
        result_container = {"confirmed": False}
        event = threading.Event()
        
        def on_result(result: bool):
            result_container["confirmed"] = result
            event.set()
            
        def show_dialog():
            from ..dialogs.confirmation import ConfirmationDialog
            self.app.push_screen(
                ConfirmationDialog(title="Confirmation Required", message=message),
                on_result
            )
            
        # Schedule dialog on main thread
        self.app.call_from_thread(show_dialog)
        
        # Wait for user interaction (with timeout to prevent permanent hang)
        event.wait(timeout=_DIALOG_TIMEOUT)
        
        if not event.is_set():
            Logger.warning("[Session] Confirmation dialog timed out, returning False (conservative choice)")
            # Add visual feedback for timeout
            def show_timeout_warning():
                self.notify("Confirmation request timed out. Proceeding conservatively (False).", severity="warning")
            self.app.call_from_thread(show_timeout_warning)
        
        return result_container["confirmed"]

    def _request_input(self, question: str) -> str:
        """
        Callback for agent to request input (runs in worker thread).
        Renders question in chat area and waits for user input via input bar.
        """
        # 1. Render Question
        def show_question():
             chat_area = self.query_one("#chat-area", ScrollableContainer)
             
             q_container = Vertical(
                 Static("❓ Agent Question:", classes="question-title"),
                 Markdown(question),
                 classes="question-container"
             )
             
             chat_area.mount(q_container)
             chat_area.scroll_end(animate=False)
             
             # Highlight input bar
             inp = self.query_one("#message-input", Input)
             inp.placeholder = "Type your answer here..."
             inp.focus()
             
        self.app.call_from_thread(show_question)
        
        # 2. Wait for Input (with timeout to prevent permanent hang)
        self._input_event.clear()
        self._input_response = None
        self._is_waiting_for_input = True
        
        self._input_event.wait(timeout=_DIALOG_TIMEOUT)
        
        if not self._input_event.is_set():
            mock_response = "Please use your own discretion and proceed with execution along a safe, conservative, and straightforward path."
            Logger.warning(f"[Session] Input request timed out, returning mock response: {mock_response}")
            
            # Add visual feedback and show the mock response in UI
            def show_timeout_and_mock():
                self.notify("Input request timed out. Using mock safety response.", severity="warning")
                chat_area = self.query_one("#chat-area", ScrollableContainer)
                
                # Show the timeout + mock response in the chat area
                timeout_msg = Vertical(
                    Static("⏰ Timeout Warning:", classes="question-title"),
                    Markdown(f"User interaction timed out. Returning mock response:\n\n> {mock_response}"),
                    classes="question-container"
                )
                chat_area.mount(timeout_msg)
                chat_area.scroll_end(animate=False)
                
            self.app.call_from_thread(show_timeout_and_mock)
            self._input_response = mock_response
        
        # 3. Cleanup
        self._is_waiting_for_input = False
        def reset_input():
            try:
                inp = self.query_one("#message-input", Input)
                inp.placeholder = ">>> Type your message..."
            except Exception:
                pass
            
        self.app.call_from_thread(reset_input)
        
        return self._input_response if self._input_response is not None else ""

    @on(Input.Submitted, "#message-input")
    def on_message_submitted(self, event: Input.Submitted):
        """Handle message submission"""
        message = event.value.strip()
        
        # Handle Agent Input Request (response to ask_user)
        if self._is_waiting_for_input:
            if not message: return
            
            event.input.value = ""
            self._input_response = message
            
            # Display Answer in Chat as User message
            chat_area = self.query_one("#chat-area", ScrollableContainer)
            msg = ChatMessage(role="user", content=message)
            chat_area.mount(create_message_widget(msg))
            chat_area.scroll_end(animate=False)
            
            self._input_event.set()
            return

        if not message:
            return

        # Check for exit keywords
        if message.lower() in EXIT_KEYWORDS:
            self.app.action_force_quit()
            return
            
        # [NEW LOGIC] Active User Message -> Refresh Session ID if in Swarm mode
        if state.agent_mode == AgentMode.SWARM:
            state.refresh_session_id()
        
        # Clear input immediately
        event.input.value = ""
        
        # Check command
        if message.startswith("/"):
            from ..slash_commands import handle_slash_command
            handle_slash_command(self.app, message, source="session", context=self)
            return
        
        # Check if agent is already running
        if self.agent and self.agent.is_running:
            self.notify("Agent is processing, please wait... or ctrl+K to stop it", severity="warning")
            # Restore input if we didn't send it?
            # Actually, better to just clear it and ignore or queue it.
            # But here we just return as per original logic.
            return
        
        # Send message
        self._send_message(message)


    
    @work(exclusive=True, thread=True)
    def _send_message(self, message: str):
        """Send message to agent (runs in worker thread)"""
        if not self.agent:
            self.notify("Agent not initialized", severity="error")
            return

        chat_area = self.query_one("#chat-area", ScrollableContainer)
        generator = None

        try:
            # Stream messages from agent
            generator = self.agent.send_message(message)
            for chat_msg in generator:
                # Use call_from_thread to update UI from worker thread
                self.app.call_from_thread(self._add_or_update_message, chat_msg)
        except Exception as e:
            self.app.call_from_thread(self.notify, f"Agent error: {e}", severity="error")
        finally:
            # Ensure generator is closed to trigger finally block in _send_message_swarm
            if generator:
                try:
                    generator.close()
                except Exception:
                    pass
    
    def _add_or_update_message(self, chat_msg: ChatMessage):
        """Add or update a message in the chat area"""
        # Check for error notification
        if chat_msg.is_error and not getattr(chat_msg, "_notified", False):
            self.notify(chat_msg.content, severity="error", timeout=3.0)
            chat_msg._notified = True

        chat_area = self.query_one("#chat-area", ScrollableContainer)
        
        if chat_msg.is_error or chat_msg.role == "error":
            widget = create_message_widget(chat_msg)
            chat_area.mount(widget)
            chat_area.scroll_end(animate=False)
            
            children = chat_area.children
            if len(children) > MAX_VISIBLE_MESSAGES:
                children[0].remove()
            
            self.current_assistant_widget = None
            
        elif chat_msg.role == "assistant" and chat_msg.is_streaming:
            # Streaming assistant message
            if self.current_assistant_widget is None:
                # Create new widget
                widget = create_message_widget(chat_msg)
                self.current_assistant_widget = widget
                chat_area.mount(widget)
            else:
                # Update existing widget
                if isinstance(self.current_assistant_widget, AssistantMessageWidget):
                    self.current_assistant_widget.update_content(chat_msg.content)
            
            # Scroll to bottom
            chat_area.scroll_end(animate=False)
            
        elif chat_msg.role == "assistant" and not chat_msg.is_streaming:
            # Finished streaming
            if self.current_assistant_widget and isinstance(self.current_assistant_widget, AssistantMessageWidget):
                self.current_assistant_widget.finish_streaming()
            self.current_assistant_widget = None
            
        else:
            # Other message types (user, tool, etc.)
            widget = create_message_widget(chat_msg)
            chat_area.mount(widget)
            chat_area.scroll_end(animate=False)
            
            # Cap visible messages to prevent unbounded memory growth
            children = chat_area.children
            if len(children) > MAX_VISIBLE_MESSAGES:
                children[0].remove()
            
            # If it's a user message, reset assistant widget tracker
            if chat_msg.role == "user":
                self.current_assistant_widget = None
    
    def action_toggle_mode(self):
        """Toggle between Chat and Swarm mode"""
        if not self.agent:
            return
        
        if self.agent.is_running:
            self.notify("Agent is processing, please wait... or ctrl+K to stop it", severity="warning")
            return
        
        # Toggle mode in shared state
        new_mode = state.toggle_agent_mode()
        
        # Sync agent bridge mode
        self.agent.set_mode(new_mode)
        
        if new_mode == AgentMode.CHAT:
            self.notify("Switched to Chat mode", severity="information")
        else:
            state.sync_blackboard_root()
            self.notify("Switched to Swarm mode", severity="information")
        
        # Update header and hints
        self._update_status()
    
    def _update_hints(self):
        """Update hint bar text"""
        hints = self._get_hints()
        self.query_one("#hint-bar", Static).update(hints)

    def _get_hints(self) -> str:
        """Get hint text based on mode"""
        base_hints = r"\[Tab] Toggle Mode \[Ctrl+K] Stop Agent \[Ctrl+N] New Session \[Ctrl+P] Select Provider \[Ctrl+O] Select Model \[Ctrl+S] Switch Model"
        if state.agent_mode == AgentMode.SWARM:
            base_hints += r" \[Ctrl+J] Monitor Agents"
        base_hints += r" \[Ctrl+C] Quit"
        return base_hints
    
    def action_command_palette(self):
        """Show command palette"""
        self.app.push_screen(DialogCommand())
    
    def action_select_model(self):
        """Show model selector using global app logic"""
        if self.agent and self.agent.is_running:
            self.notify("Agent is processing, please wait... or ctrl+K to stop it", severity="warning")
            return
        if hasattr(self.app, '_cmd_select_model'):
            self.app._cmd_select_model()
    
    def action_select_provider(self):
        """Show provider selector using global app logic"""
        if self.agent and self.agent.is_running:
            self.notify("Agent is processing, please wait... or ctrl+K to stop it", severity="warning")
            return
        if hasattr(self.app, '_cmd_select_provider'):
            self.app._cmd_select_provider()
    
    def action_new_session(self):
        """Start a new session (Clears history only)"""
        if self.agent:
            if self.agent.is_running:
                self.agent.stop()
            
            self.agent.clear_history()
            # Note: We don't refresh ID or clean blackboard here.
            # It will happen lazily on the next active message.

        
        # Clear chat area
        chat_area = self.query_one("#chat-area", ScrollableContainer)
        chat_area.remove_children()
        
        # Focus input
        self.query_one("#message-input", Input).focus()
        
        self.notify("New session started")
    
    def action_monitor_agents(self):
        """Show agent monitor (Swarm mode only)"""
        if state.agent_mode != AgentMode.SWARM:
            self.notify("Monitor is only available in Swarm Mode", severity="warning")
            return
        self.app.push_screen(AgentMonitorScreen())

    
    def action_stop_agent(self):
        """Stop agent if running, otherwise quit"""
        if self.agent and self.agent.is_running:
            self.agent.stop()
            self.notify("Agent stopped")
        else:
            self.notify("Agent is not running", severity="information")

    def action_cycle_model(self):
        """Cycle through recent models using global app logic"""
        if self.agent and self.agent.is_running:
            self.notify("Agent is processing, please wait... or ctrl+K to stop it", severity="warning")
            return
        if hasattr(self.app, '_cmd_cycle_model'):
            self.app._cmd_cycle_model()
        self._update_status()
    
    def _update_status(self):
        """Update screen status (header, hints) and re-sync agent if needed"""
        # 1. Update Header
        model_name = self._get_model_display()
        mode_display = self._get_mode_display()
        header = self.query_one("#header-left", Static)
        header.update(f"[bold]Session[/bold]  {mode_display}  {model_name}")

        # 2. Update Hints
        self._update_hints()

        # 3. Handle Model Change for Agent
        if self.agent:
            model_key = state.get_model_key()
            # AgentBridge.initialize check internal state to avoid redundant re-init
            self.agent.initialize(model_key)
