import os
import json
import uuid
from backend.llm.middleware import StrategyMiddleware
from backend.llm.types import AgentSession
from backend.utils.logger import Logger
from typing import Callable, Any

from .._mock_chunk import create_mock_tool_chunk


class SwarmAgentGuardMiddleware(StrategyMiddleware):
    """
    Swarm Agent Guard Middleware (StrategyMiddleware Pattern)

    Ensures that a Swarm Worker Agent does not exit or idle without intent:
    1. If the LLM response contains NO tool calls, it automatically injects
       a 'wait' tool call to force the agent to continue its task or finish.
    """
    def __init__(self, agent_name: str = "Agent", blackboard_dir: str = ".blackboard"):
        self.agent_name = agent_name
        self.blackboard_dir = blackboard_dir

    def __call__(self, session: AgentSession, next_call: Callable[[AgentSession], Any]) -> Any:
        generator = next_call(session)
        return self._guard_stream(generator, session)

    def _guard_stream(self, generator, session):
        has_tool_calls = False

        for chunk in generator:
            if hasattr(chunk, 'choices') and chunk.choices:
                delta = chunk.choices[0].delta
                if hasattr(delta, 'tool_calls') and delta.tool_calls:
                    has_tool_calls = True
            yield chunk

        # End of stream check
        if not has_tool_calls:
            call_id = f"call_{uuid.uuid4().hex[:8]}"
            Logger.info(f"[{self.agent_name}] Guard triggered: No tool call detected. Injecting 'wait'.")

            reason = "### [SYSTEM GUARD]\nYou did not call any tools. If your task is complete, you MUST call the `finish` tool. Otherwise, use appropriate tools to move forward. If you are waiting for something, use the `wait` tool explicitly."
            args = {
                "duration": 0.5,
                "wait_for_new_index": True,
                "reason": reason
            }

            yield create_mock_tool_chunk(call_id, "wait", json.dumps(args))
