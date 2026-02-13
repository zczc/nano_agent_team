"""
Theme definitions for TUI
Inspired by OpenCode's theming system
"""

from dataclasses import dataclass
from typing import Dict


@dataclass
class Theme:
    """Color theme definition"""
    name: str
    
    # Base colors
    background: str
    surface: str
    
    # Text colors
    text: str
    text_muted: str
    text_highlight: str
    
    # Accent colors
    primary: str
    secondary: str
    
    # Status colors
    success: str
    warning: str
    error: str
    info: str
    
    # UI element colors
    border: str
    border_focus: str
    selection: str


# Dark theme (Nord)
DARK = Theme(
    name="dark",
    background="#2e3440",  # Polar Night
    surface="#3b4252",
    text="#ffffff",        # Pure White
    text_muted="#e5e9f0",
    text_highlight="#ffffff",
    primary="#88c0d0",     # Frost - Ice Blue
    secondary="#b48ead",   # Aurora - Purple
    success="#a3be8c",     # Aurora - Green
    warning="#ebcb8b",     # Aurora - Yellow
    error="#bf616a",       # Aurora - Red
    info="#81a1c1",        # Frost
    border="#4c566a",      # Polar Night
    border_focus="#88c0d0",
    selection="#434c5e",
)

# Light theme
LIGHT = Theme(
    name="light",
    background="#ffffff",
    surface="#f6f8fa",
    text="#000000",
    text_muted="#333333",
    text_highlight="#000000",
    primary="#0969da",
    secondary="#8250df",
    success="#1a7f37",
    warning="#9a6700",
    error="#cf222e",
    info="#0969da",
    border="#d0d7de",
    border_focus="#0969da",
    selection="#ddf4ff",
)

# Available themes
THEMES: Dict[str, Theme] = {
    "dark": DARK,
    "light": LIGHT,
}

# TODO: Integrate with Textual CSS variables for runtime theme switching.
# Currently the app CSS in app.py hardcodes Nord theme colors as CSS variables.
# To enable theme switching, these functions would need to regenerate CSS vars.
current_theme: Theme = DARK


def get_theme() -> Theme:
    """Get current theme"""
    return current_theme


def set_theme(name: str):
    """Set current theme by name"""
    global current_theme
    if name in THEMES:
        current_theme = THEMES[name]
