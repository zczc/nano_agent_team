"""
TAP Protocol — Event and Control Message types, serialization helpers.

Transport: newline-delimited JSON over stdio.
- Agent stdout → TUI: event stream
- TUI stdin → Agent: control messages

Event data fields may contain newlines (e.g. code snippets) — they are JSON-escaped,
so bare \\n as message delimiter is safe.
"""

import json
import sys
import itertools
from dataclasses import dataclass, field, asdict
from typing import Any, Optional, Dict, Literal

# ---------------------------------------------------------------------------
# ID generator
# ---------------------------------------------------------------------------
_counter = itertools.count(1)


def _next_id(prefix: str = "r") -> str:
    return f"{prefix}-{next(_counter)}"


# ---------------------------------------------------------------------------
# Agent → TUI: Event types
# ---------------------------------------------------------------------------

# Event type literals matching existing AgentEvent.type + new TAP types
EventType = Literal[
    "token",            # LLM streaming token
    "message",          # LLM complete message
    "tool_call",        # LLM decided to call a tool
    "tool_result",      # Tool execution result
    "error",            # Error
    "finish",           # Turn finished
    "confirm_request",  # Agent needs user confirmation (bool)
    "input_request",    # Agent needs user text input (str)
]


def emit_event(event: dict, file=None) -> None:
    """Write a JSON event line to stdout (or given file)."""
    out = file or sys.stdout
    out.write(json.dumps(event, ensure_ascii=False) + "\n")
    out.flush()


# ---------------------------------------------------------------------------
# TUI → Agent: Control message types
# ---------------------------------------------------------------------------

ControlType = Literal[
    "user_message",      # Start a new turn
    "confirm_response",  # Reply to confirm_request
    "input_response",    # Reply to input_request
    "abort",             # Ctrl+K — cancel current operation
]


def parse_control_message(line: str) -> dict:
    """Parse a single JSON line from stdin into a control message dict."""
    return json.loads(line.strip())


# ---------------------------------------------------------------------------
# Convenience dataclasses (optional typed wrappers)
# ---------------------------------------------------------------------------

@dataclass
class TapEvent:
    """Typed wrapper for an outgoing event."""
    type: EventType
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Flatten to a single dict for serialization."""
        d = dict(self.data)
        d["type"] = self.type
        return d

    def emit(self, file=None) -> None:
        emit_event(self.to_dict(), file=file)


@dataclass
class TapControlMessage:
    """Typed wrapper for an incoming control message."""
    type: ControlType
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "TapControlMessage":
        return cls(type=d["type"], raw=d)

    # Convenience accessors
    @property
    def text(self) -> str:
        return self.raw.get("text", "")

    @property
    def id(self) -> str:
        return self.raw.get("id", "")

    @property
    def approved(self) -> bool:
        return self.raw.get("approved", False)

    @property
    def reason(self) -> Optional[str]:
        return self.raw.get("reason")


# ---------------------------------------------------------------------------
# Helper: build specific events
# ---------------------------------------------------------------------------

def make_confirm_request(message: str, kind: str = "confirmation") -> dict:
    """Build a confirm_request event dict."""
    return {
        "type": "confirm_request",
        "id": _next_id("c"),
        "kind": kind,
        "message": message,
    }


def make_input_request(question: str) -> dict:
    """Build an input_request event dict."""
    return {
        "type": "input_request",
        "id": _next_id("i"),
        "question": question,
    }
