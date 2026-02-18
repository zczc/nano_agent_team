"""
TAP Exceptions
"""


class AbortError(Exception):
    """Raised when the TUI sends an abort signal (Ctrl+K)."""
    pass
