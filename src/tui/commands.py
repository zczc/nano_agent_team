"""
Command Registry System for TUI
Inspired by OpenCode's command palette system
"""

from dataclasses import dataclass, field
from typing import Callable, Optional, List, Dict, Any


@dataclass
class Command:
    """A single command that can be triggered via command palette or keybind"""
    title: str
    value: str  # Unique identifier like "model.list"
    category: str = "General"
    keybind: Optional[str] = None  # e.g. "m" for model
    description: Optional[str] = None
    suggested: bool = False  # Show prominently
    hidden: bool = False  # Hide from command palette
    on_select: Callable[[], None] = field(default=lambda: None)
    

class CommandRegistry:
    """
    Central registry for all TUI commands.
    Supports registration, lookup, and triggering.
    """
    
    def __init__(self):
        self._commands: Dict[str, Command] = {}
        self._keybind_map: Dict[str, str] = {}  # keybind -> value
    
    def register(self, command: Command):
        """Register a command"""
        self._commands[command.value] = command
        if command.keybind:
            self._keybind_map[command.keybind] = command.value
    
    def register_many(self, commands: List[Command]):
        """Register multiple commands at once"""
        for cmd in commands:
            self.register(cmd)
    
    def get(self, value: str) -> Optional[Command]:
        """Get command by value"""
        return self._commands.get(value)
    
    def get_by_keybind(self, key: str) -> Optional[Command]:
        """Get command by keybind"""
        value = self._keybind_map.get(key)
        if value:
            return self._commands.get(value)
        return None
    
    def trigger(self, value: str) -> bool:
        """Trigger a command by value. Returns True if found."""
        cmd = self.get(value)
        if cmd:
            cmd.on_select()
            return True
        return False
    
    def trigger_keybind(self, key: str) -> bool:
        """Trigger a command by keybind. Returns True if found."""
        cmd = self.get_by_keybind(key)
        if cmd:
            cmd.on_select()
            return True
        return False
    
    def all(self) -> List[Command]:
        """Get all visible commands"""
        return [cmd for cmd in self._commands.values() if not cmd.hidden]
    
    def by_category(self) -> Dict[str, List[Command]]:
        """Get commands grouped by category"""
        result: Dict[str, List[Command]] = {}
        for cmd in self.all():
            if cmd.category not in result:
                result[cmd.category] = []
            result[cmd.category].append(cmd)
        return result
    
    def suggested(self) -> List[Command]:
        """Get suggested/prominent commands"""
        return [cmd for cmd in self.all() if cmd.suggested]


# Global command registry instance
commands = CommandRegistry()
