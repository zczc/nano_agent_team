from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Static, Button, Markdown
from textual.containers import Vertical, Horizontal
from textual.binding import Binding

class ConfirmationDialog(ModalScreen[bool]):
    """
    A simple Yes/No confirmation dialog.
    Returns True if confirmed (Yes), False otherwise (No/Cancel).
    """
    
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("y", "confirm", "Yes"),
        Binding("n", "cancel", "No"),
    ]
    
    DEFAULT_CSS = """
    ConfirmationDialog {
        align: center middle;
    }
    
    ConfirmationDialog > Vertical {
        width: 80;
        max-width: 90%;
        height: auto;
        background: $surface;
        border: solid $warning;
        padding: 1 2;
    }
    
    ConfirmationDialog .title {
        text-style: bold;
        color: $warning;
        padding-bottom: 1;
        text-align: center;
    }
    
    ConfirmationDialog .message {
        color: $text;
        padding-bottom: 1;
        height: auto;
        max-height: 20;
    }
    
    ConfirmationDialog Markdown {
        background: transparent;
        padding: 0;
        margin: 0;
    }

    ConfirmationDialog Horizontal {
        align: center middle;
        height: auto;
        margin-top: 1;
    }
    
    ConfirmationDialog Button {
        margin: 0 2;
    }
    """
    
    def __init__(self, title: str, message: str):
        super().__init__()
        self.dialog_title = title
        self.message = message
    
    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.dialog_title, classes="title")
            yield Markdown(self.message, classes="message")
            
            with Horizontal():
                yield Button("Yes", variant="success", id="btn-yes")
                yield Button("No", variant="error", id="btn-no")
    
    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "btn-yes":
            self.dismiss(True)
        else:
            self.dismiss(False)
    
    def action_confirm(self):
        self.dismiss(True)
        
    def action_cancel(self):
        self.dismiss(False)
