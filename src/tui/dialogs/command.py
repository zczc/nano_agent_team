"""
Command Dialog for TUI
Command palette (ctrl+x) for quick actions
"""

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Static, ListView, ListItem, Input
from textual.containers import Vertical
from textual.binding import Binding
from textual import on
from typing import Optional, List
from thefuzz import fuzz

from ..commands import commands, Command


class DialogCommand(ModalScreen):
    """
    Command palette dialog.
    Shows all registered commands with fuzzy search.
    """
    
    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("enter", "select", "Execute"),
    ]
    
    DEFAULT_CSS = """
    DialogCommand {
        align: center middle;
    }
    
    DialogCommand > Vertical {
        width: 55;
        max-width: 80%;
        max-height: 70%;
        background: $surface;
        border: solid $primary;
        padding: 1 2;
    }
    
    DialogCommand .title {
        text-style: bold;
        color: $text;
        padding-bottom: 1;
    }
    
    DialogCommand Input {
        margin-bottom: 1;
    }
    
    DialogCommand ListView {
        height: auto;
        max-height: 15;
        background: transparent;
    }
    
    DialogCommand ListItem {
        padding: 0 1;
    }
    
    DialogCommand ListItem:hover {
        background: $primary 20%;
    }
    
    DialogCommand .category {
        color: $text-muted;
        text-style: bold;
        padding: 1 0 0 0;
    }
    
    DialogCommand .keybind {
        color: $primary;
    }
    """
    
    def __init__(self):
        super().__init__()
        self.all_commands = []
        self.filtered_commands = []
    
    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Command Palette", classes="title")
            yield Input(placeholder="> Type a command...", id="search")
            yield ListView(id="commands")
    
    def on_mount(self):
        """Initialize on mount - get commands here since they may be registered after dialog creation"""
        self.all_commands = commands.all()
        self.filtered_commands = self.all_commands.copy()
        self._refresh_list()
        self.query_one("#search", Input).focus()
    
    def _refresh_list(self):
        """Refresh command list"""
        listview = self.query_one("#commands", ListView)
        listview.clear()
        
        query = self.query_one("#search", Input).value.strip().lower()
        
        if not query:
            # Group by category
            by_category = commands.by_category()
            for category in sorted(by_category.keys()):
                cmds = by_category[category]
                listview.append(ListItem(Static(category, classes="category"), disabled=True))
                for cmd in cmds:
                    listview.append(self._make_item(cmd))
        else:
            # Fuzzy search
            scored = []
            for cmd in self.all_commands:
                score = max(
                    fuzz.partial_ratio(query, cmd.title.lower()),
                    fuzz.partial_ratio(query, cmd.value.lower()),
                    fuzz.partial_ratio(query, cmd.category.lower()),
                )
                if score > 40:
                    scored.append((score, cmd))
            
            scored.sort(key=lambda x: -x[0])
            for _, cmd in scored[:10]:
                listview.append(self._make_item(cmd))
    
    def _make_item(self, cmd: Command) -> ListItem:
        """Create list item for command"""
        content = f"[bold]{cmd.title}[/bold]"
        if cmd.keybind:
            content += r" [dim]\[{cmd.keybind}][/dim]"
        if cmd.description:
            content += r"  [dim]{cmd.description}[/dim]"
        
        return ListItem(Static(content, markup=True), id=f"cmd_{cmd.value}")
    
    @on(Input.Changed, "#search")
    def on_search_changed(self, event: Input.Changed):
        """Handle search"""
        self._refresh_list()
    
    @on(ListView.Selected, "#commands")
    def on_command_selected(self, event: ListView.Selected):
        """Handle command selection"""
        self._select_current()
    
    def action_select(self):
        """Handle enter"""
        self._select_current()
    
    def _select_current(self):
        """Execute selected command"""
        listview = self.query_one("#commands", ListView)
        if listview.highlighted_child is None:
            return
        
        item_id = listview.highlighted_child.id
        if item_id and item_id.startswith("cmd_"):
            cmd_value = item_id[4:]
            self.dismiss(cmd_value)
            commands.trigger(cmd_value)
    
    def action_dismiss(self):
        """Close dialog"""
        self.dismiss(None)
