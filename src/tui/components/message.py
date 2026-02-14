"""
Message Components for TUI Session
"""

from textual.widget import Widget
from textual.widgets import Static, Markdown
from textual.containers import Vertical
from textual.app import ComposeResult
from dataclasses import dataclass
from typing import Optional
import time
from rich.markup import escape


@dataclass
class ChatMessage:
    """Represents a chat message"""
    role: str  # "user", "assistant", "tool", "thinking"
    content: str
    timestamp: float = 0.0
    tool_name: Optional[str] = None  # For tool messages
    is_streaming: bool = False  # True while still receiving tokens
    is_error: bool = False  # True if message represents an error
    
    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()


class UserMessageWidget(Static):
    """Widget for displaying user messages"""
    
    DEFAULT_CSS = """
    UserMessageWidget {
        background: $surface;
        color: $text;
        padding: 1 2;
        margin: 1 0 1 10;
        border: round $primary;
    }
    
    UserMessageWidget .role {
        color: $primary;
        text-style: bold;
    }
    """
    
    def __init__(self, message: ChatMessage):
        super().__init__()
        self.message = message
    
    def compose(self) -> ComposeResult:
        yield Static(f"[bold cyan]You[/bold cyan]", markup=True)
        yield Static(self.message.content)


class AssistantMessageWidget(Static):
    """Widget for displaying assistant messages with Markdown rendering"""
    
    DEFAULT_CSS = """
    AssistantMessageWidget {
        background: $surface-darken-2;
        color: $text;
        padding: 1 2;
        margin: 1 10 1 0;
        border: round $border;
    }
    
    AssistantMessageWidget.streaming {
        border: round $warning;
    }
    
    AssistantMessageWidget Markdown {
        background: transparent;
        margin: 0;
        padding: 0;
    }
    
    AssistantMessageWidget #content-static {
        /* For streaming, use plain static */
    }
    """
    
    def __init__(self, message: ChatMessage):
        super().__init__()
        self.message = message
        self._use_markdown = not message.is_streaming  # Use Static for streaming, Markdown when complete
        if message.is_streaming:
            self.add_class("streaming")
    
    def compose(self) -> ComposeResult:
        yield Static(f"[bold green]Assistant[/bold green]", markup=True)
        if self._use_markdown:
            yield Markdown(self.message.content, id="content-md")
        else:
            yield Static(self.message.content, id="content-static")
    
    def update_content(self, content: str):
        """Update the message content (for streaming - uses Static)"""
        self.message.content = content
        try:
            content_widget = self.query_one("#content-static", Static)
            content_widget.update(content)
        except Exception:
            pass
    
    def finish_streaming(self):
        """Mark streaming as complete and switch to Markdown rendering"""
        self.message.is_streaming = False
        self.remove_class("streaming")
        
        # Replace Static with Markdown for proper rendering
        try:
            old_widget = self.query_one("#content-static", Static)
            old_widget.remove()
            self.mount(Markdown(self.message.content, id="content-md"))
        except Exception:
            pass


class ErrorMessageWidget(Static):
    """Widget for displaying error messages"""
    
    DEFAULT_CSS = """
    ErrorMessageWidget {
        background: $error 15%;
        color: $text;
        padding: 1 2;
        margin: 1 10 1 0;
        border: heavy $error;
    }
    
    ErrorMessageWidget Markdown {
        background: transparent;
        margin: 0;
        padding: 0;
    }
    """
    
    def __init__(self, message: ChatMessage):
        super().__init__()
        self.message = message
    
    def compose(self) -> ComposeResult:
        yield Static("[bold red]Error[/bold red]", markup=True)
        yield Markdown(self.message.content)


class ToolMessageWidget(Static):
    """Widget for displaying tool calls/results"""
    
    DEFAULT_CSS = """
    ToolMessageWidget {
        background: $surface-darken-1;
        color: $text-muted;
        padding: 0 2;
        margin: 0 5;
        border: dashed $border;
    }
    
    ToolMessageWidget .tool-name {
        color: $secondary;
        text-style: bold;
    }
    
    ToolMessageWidget .tool-result {
        color: $text-muted;
    }
    """
    
    def __init__(self, message: ChatMessage):
        super().__init__()
        self.message = message
    
    def compose(self) -> ComposeResult:
        icon = "⚙" if self.message.role == "tool_call" else "→"
        tool_name = escape(self.message.tool_name or "tool")
        content = self.message.content
        if len(content) > 100:
            content = content[:100] + "..."
        content = escape(content)
        
        yield Static(f"[bold magenta]{icon} {tool_name}[/bold magenta]  [dim]{content}[/dim]",
                     markup=True)


class ThinkingWidget(Static):
    """Widget for displaying thinking/reasoning"""
    
    DEFAULT_CSS = """
    ThinkingWidget {
        color: $text-muted;
        text-style: italic;
        padding: 0 2;
        margin: 0 5;
    }
    """
    
    def __init__(self, content: str = "Thinking..."):
        super().__init__(f"[italic dim]💭 {escape(content)}[/italic dim]", markup=True)
        self.thinking_content = content
    
    def update_thinking(self, content: str):
        """Update thinking content"""
        self.thinking_content = content
        self.update(f"[italic dim]💭 {escape(content)}[/italic dim]")


class FinishMessageWidget(Static):
    """Widget for distinctively displaying the finish tool call"""
    
    DEFAULT_CSS = """
    FinishMessageWidget {
        background: $surface-darken-2;
        color: $text;
        padding: 1 2;
        margin: 1 10 1 0;
        border: double $success;
    }
    
    FinishMessageWidget .header {
        color: $success;
        text-style: bold;
        margin-bottom: 1;
    }
    
    FinishMessageWidget Markdown {
        background: transparent;
        margin: 0;
        padding: 0;
    }
    """
    
    def __init__(self, message: ChatMessage):
        super().__init__()
        self.message = message
        
    def compose(self) -> ComposeResult:
        content = self.message.content
        display_text = content
        
        # Remove "Agent Finished." prefix if present to avoid redundancy with the header
        # The content from FinishTool is: "Agent Finished.\n\nReason: ...\n\n===========================\n\nOutput: ..."
        if "Agent Finished." in content:
            display_text = content.replace("Agent Finished.", "").strip()
            
        yield Static("🏁 MISSION ACCOMPLISHED", classes="header")
        yield Markdown(display_text)



def create_message_widget(message: ChatMessage) -> Widget:
    """Factory function to create appropriate widget for message type"""
    if message.is_error or message.role == "error":
        return ErrorMessageWidget(message)
    if message.role == "user":
        return UserMessageWidget(message)
    elif message.role == "assistant":
        return AssistantMessageWidget(message)
    elif message.role in ("tool_call", "tool_result"):
        # Special handling for finish tool call
        if message.tool_name == "finish" and message.role == "tool_result":
             return FinishMessageWidget(message)
        return ToolMessageWidget(message)
    elif message.role == "thinking":
        return ThinkingWidget(message.content)
    else:
        return Static(f"[{message.role}] {escape(message.content)}")
