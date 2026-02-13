"""
Shared constants and utilities for TUI
"""

EXIT_KEYWORDS = frozenset({
    "quit", "q", "exit", "quit(", "exit(", "quit()", "exit()",
    "退出", "结束", "关闭", "退出程序"
})


def get_mode_display(mode) -> str:
    """Get Rich markup badge for agent mode display."""
    from .state import AgentMode
    if mode == AgentMode.CHAT:
        return "[green]◉ Chat[/green]"
    return "[yellow]◉ Swarm[/yellow]"
