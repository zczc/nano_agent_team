"""Shared utility for creating mock OpenAI-compatible tool call chunks."""

import time
from types import SimpleNamespace


def create_mock_tool_chunk(call_id: str, name: str, args: str, index: int = 0):
    """
    Construct a realistic OpenAI-compatible ChatCompletionChunk with a tool call.

    Args:
        call_id: The tool call ID (e.g. "call_abc123").
        name: The function name.
        args: JSON string of function arguments.
        index: The tool call index (default 0).

    Returns:
        A SimpleNamespace mimicking a ChatCompletionChunk.
    """
    tc = SimpleNamespace(index=index)
    if call_id:
        tc.id = call_id
    if name:
        tc.type = 'function'
        tc.function = SimpleNamespace(name=name, arguments="")
    if args:
        if not hasattr(tc, 'function'):
            tc.function = SimpleNamespace(arguments="")
        tc.function.arguments = args

    choice = SimpleNamespace(
        index=0,
        delta=SimpleNamespace(content=None, tool_calls=[tc]),
        finish_reason=None
    )

    chunk = SimpleNamespace(
        id=f"chatcmpl-mock-{int(time.time())}",
        object="chat.completion.chunk",
        created=int(time.time()),
        model="mock-guardian-model",
        choices=[choice]
    )

    return chunk
