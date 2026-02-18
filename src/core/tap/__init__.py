"""
TAP (TUI-Agent Protocol) — stdio-based communication between TUI and Agent process.

Agent → TUI (stdout): newline-delimited JSON event stream
TUI → Agent (stdin): control messages (user_message, confirm_response, input_response, abort)
"""

from .protocol import (
    TapEvent,
    TapControlMessage,
    emit_event,
    parse_control_message,
)
from .exceptions import AbortError
from .agent_process import AgentProcess
from .client import TapClient
