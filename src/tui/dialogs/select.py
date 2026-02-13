"""
Generic Select Dialog for TUI
Inspired by OpenCode's DialogSelect component
"""

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Static, ListView, ListItem, Input
from textual.containers import Vertical, Horizontal
from textual.binding import Binding
from textual import on
from dataclasses import dataclass
from typing import Callable, Optional, List, Any
from thefuzz import fuzz
from rich.markup import escape


@dataclass
class SelectOption:
    """A selectable option in the dialog"""
    title: str
    value: Any
    category: str = ""
    description: str = ""
    footer: str = ""  # e.g. "Connected", "Free"
    disabled: bool = False
    on_select: Optional[Callable[[], None]] = None


class DialogSelect(ModalScreen):
    """
    Generic selection dialog with fuzzy search.
    Shows options grouped by category with keyboard navigation.
    """
    
    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("enter", "select", "Select"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
    ]
    
    DEFAULT_CSS = """
    DialogSelect {
        align: center middle;
    }
    
    DialogSelect > Vertical {
        width: 60;
        max-width: 80%;
        max-height: 80%;
        background: $surface;
        border: solid $primary;
        padding: 1 2;
    }
    
    DialogSelect .title {
        text-style: bold;
        color: $text;
        padding-bottom: 1;
    }
    
    DialogSelect Input {
        margin-bottom: 1;
    }
    
    DialogSelect ListView {
        height: auto;
        max-height: 20;
        background: transparent;
    }
    
    DialogSelect ListItem {
        padding: 0 1;
    }
    
    DialogSelect ListItem:hover {
        background: $primary 20%;
    }
    
    DialogSelect .option-title {
        color: $text;
    }
    
    DialogSelect .option-desc {
        color: $text-muted;
        padding-left: 1;
    }
    
    DialogSelect .option-footer {
        color: $success;
        padding-left: 1;
    }
    
    DialogSelect .category {
        color: $text-muted;
        text-style: bold;
        padding: 1 0 0 0;
    }
    
    DialogSelect .hint {
        color: $text-muted;
        padding-top: 1;
    }
    
    DialogSelect .disabled {
        color: $text-muted;
        text-style: dim;
    }
    """
    
    def __init__(
        self,
        title: str,
        options: List[SelectOption],
        keybind_hints: Optional[List[tuple]] = None,  # [(key, label), ...]
    ):
        super().__init__()
        self.dialog_title = title
        self.options = options
        self.filtered_options = options.copy()
        self.keybind_hints = keybind_hints or []
        self._selected_index = 0
    
    def compose(self) -> ComposeResult:
        with Vertical():
            # Title
            yield Static(self.dialog_title, classes="title")
            
            # Search input
            yield Input(placeholder="Search...", id="search")
            
            # Options list
            yield ListView(id="options")
            
            # Keybind hints
            if self.keybind_hints:
                hints = "  ".join(f"[{k}] {l}" for k, l in self.keybind_hints)
                yield Static(hints, classes="hint")
    
    def on_mount(self):
        """Initialize list on mount"""
        self._refresh_list()
        self.query_one("#search", Input).focus()
    
    def _refresh_list(self):
        """Refresh the options list"""
        listview = self.query_one("#options", ListView)
        listview.clear()
        
        current_category = ""
        
        for i, opt in enumerate(self.filtered_options):
            # Category header
            if opt.category and opt.category != current_category:
                current_category = opt.category
                listview.append(ListItem(
                    Static(opt.category, classes="category"),
                    disabled=True
                ))
            
            # Option item
            content = self._render_option(opt)
            classes = "disabled" if opt.disabled else ""
            listview.append(ListItem(
                Static(content, markup=True),
                classes=classes,
                id=f"opt_{i}"
            ))
    
    def _render_option(self, opt: SelectOption) -> str:
        """Render option as markup string"""
        parts = [f"[bold]{escape(opt.title)}[/bold]"]
        if opt.description:
            parts.append(f"[dim]{escape(opt.description)}[/dim]")
        if opt.footer:
            parts.append(f"[green]{escape(opt.footer)}[/green]")
        return "  ".join(parts)
    
    @on(Input.Changed, "#search")
    def on_search_changed(self, event: Input.Changed):
        """Filter options based on search"""
        query = event.value.strip().lower()
        
        if not query:
            self.filtered_options = self.options.copy()
        else:
            # Fuzzy search
            scored = []
            for opt in self.options:
                score = max(
                    fuzz.partial_ratio(query, opt.title.lower()),
                    fuzz.partial_ratio(query, opt.category.lower()) if opt.category else 0
                )
                if score > 50:
                    scored.append((score, opt))
            
            scored.sort(key=lambda x: -x[0])
            self.filtered_options = [opt for _, opt in scored]
        
        self._refresh_list()
    
    @on(ListView.Selected, "#options")
    def on_option_selected(self, event: ListView.Selected):
        """Handle option selection via click"""
        self._select_current()
    
    def action_select(self):
        """Handle enter key"""
        self._select_current()
    
    def _select_current(self):
        """Select the currently highlighted option"""
        listview = self.query_one("#options", ListView)
        if listview.highlighted_child is None:
            return
        
        # Find the option index from the item id
        item_id = listview.highlighted_child.id
        if item_id and item_id.startswith("opt_"):
            idx = int(item_id[4:])
            if 0 <= idx < len(self.filtered_options):
                opt = self.filtered_options[idx]
                if not opt.disabled:
                    if opt.on_select:
                        opt.on_select()
                    self.dismiss(opt.value)
    
    def action_dismiss(self):
        """Close dialog"""
        self.dismiss(None)
