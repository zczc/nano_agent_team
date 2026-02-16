"""
LLM Provider Abstraction Layer

Provides unified interface for LLM client creation, abstracting differences between providers.
"""

import os
import json
import uuid
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING, List

if TYPE_CHECKING:
    from openai import OpenAI

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False
    OpenAI = None  # type: ignore

# Try to import Langfuse OpenAI wrapper only if not disabled
LangfuseOpenAI = None
if os.environ.get("DISABLE_LANGFUSE", "").lower() != "true":
    try:
        from langfuse.openai import OpenAI as LangfuseOpenAI
    except ImportError:
        LangfuseOpenAI = None

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

try:
    import google.generativeai as genai
    HAS_GOOGLE = True
except ImportError:
    HAS_GOOGLE = False

# --- Mock Response Classes for Adapter Compatibility ---

@dataclass
class MockToolCall:
    id: str
    function: object

@dataclass
class MockMessage:
    content: str = ""
    tool_calls: Optional[List[MockToolCall]] = None

@dataclass
class MockChoice:
    message: MockMessage

@dataclass
class MockResponse:
    choices: List[MockChoice] = field(default_factory=list)

# --- Anthropic Adapter Classes ---

class OpenAIStyleChunk:
    """Mock OpenAI chunk for compatibility."""
    def __init__(self, content=None, tool_calls=None):
        self.choices = [self.Choice(content, tool_calls)]

    class Choice:
        def __init__(self, content, tool_calls):
            self.delta = self.Delta(content, tool_calls)

        class Delta:
            def __init__(self, content, tool_calls):
                self.content = content
                self.tool_calls = [
                    self.ToolCall(i, tc.get("id"), tc.get("function", {}))
                    for i, tc in enumerate(tool_calls)
                ] if tool_calls else None

            class ToolCall:
                def __init__(self, index, id, function):
                    self.index = index
                    self.id = id
                    self.function = self.Function(function.get("name"), function.get("arguments"))

                class Function:
                    def __init__(self, name, arguments):
                        self.name = name
                        self.arguments = arguments

class AnthropicAdapter:
    """
    Adapter to make Anthropic client compatible with OpenAI interface.
    Specifically converts chat.completions.create calls to messages.create.
    """
    def __init__(self, api_key: str, base_url: str = None, timeout: float = 60.0):
        self.client = anthropic.Anthropic(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout
        )
        self.chat = self.Chat(self.client)

    class Chat:
        def __init__(self, client):
            self.completions = self.Completions(client)

        class Completions:
            def __init__(self, client):
                self.client = client

            def create(self, model: str, messages: list, stream: bool = False, tools: list = None, **kwargs):
                # 1. Extract System Prompt & Register names for user/assistant messages if any
                system_prompt = None
                filtered_messages = []
                for m in messages:
                    if m["role"] == "system":
                        system_prompt = m["content"]
                    elif m["role"] == "tool":
                        # Convert tool result to Anthropic format
                        # Content block type 'tool_result'
                        # Anthropic expects tool results in a specific message role 'user' with content as list
                        # This adapter V2 will handle complex tool history mapping later if needed
                        # For now, simplistic mapping:
                        filtered_messages.append({
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": m.get("tool_call_id"),
                                    "content": m.get("content")
                                }
                            ]
                        })
                    else:
                        role = m["role"]
                        content = m["content"]
                        # Anthropic assistant messages with tool calls need special handling
                        if role == "assistant" and m.get("tool_calls"):
                            content_blocks = []
                            if content:
                                content_blocks.append({"type": "text", "text": content})
                            for tc in m["tool_calls"]:
                                content_blocks.append({
                                    "type": "tool_use",
                                    "id": tc["id"],
                                    "name": tc["function"]["name"],
                                    "input": json.loads(tc["function"]["arguments"]) if isinstance(tc["function"]["arguments"], str) else tc["function"]["arguments"]
                                })
                            filtered_messages.append({"role": "assistant", "content": content_blocks})
                        else:
                            # Anthropic doesn't allow 'name' in messages
                            msg = {"role": role, "content": content}
                            filtered_messages.append(msg)

                # 2. Prepare Tool Config (if any)
                anthropic_tools = []
                if tools:
                    for t in tools:
                        if t.get("type") == "function":
                            fn = t.get("function", {})
                            anthropic_tools.append({
                                "name": fn.get("name"),
                                "description": fn.get("description"),
                                "input_schema": fn.get("parameters")
                            })

                # 3. Call Anthropic API
                api_kwargs = {
                    "model": model,
                    "messages": filtered_messages,
                    "stream": stream,
                    "max_tokens": 4096,
                }
                if system_prompt:
                    api_kwargs["system"] = system_prompt
                if anthropic_tools:
                    api_kwargs["tools"] = anthropic_tools

                if stream:
                    return self._stream_response(api_kwargs)
                else:
                    response = self.client.messages.create(**api_kwargs)
                    return self._map_response(response)

            def _map_response(self, response):
                """Map Anthropic Message to OpenAI Style Completion."""
                content = ""
                tool_calls = []
                for block in response.content:
                    if block.type == "text":
                        content += block.text
                    elif block.type == "tool_use":
                        tool_calls.append({
                            "id": block.id,
                            "function": {
                                "name": block.name,
                                "arguments": json.dumps(block.input)
                            }
                        })

                # Use shared dataclasses
                mock_tool_calls = [
                    MockToolCall(
                        id=tc["id"],
                        function=type('obj', (object,), tc["function"])
                    ) for tc in tool_calls
                ] if tool_calls else None

                mock_message = MockMessage(content=content, tool_calls=mock_tool_calls)
                mock_choice = MockChoice(message=mock_message)
                return MockResponse(choices=[mock_choice])

            def _stream_response(self, api_kwargs):
                with self.client.messages.stream(**api_kwargs) as stream:
                    for event in stream:
                        if event.type == "content_block_delta":
                            if event.delta.type == "text_delta":
                                yield OpenAIStyleChunk(content=event.delta.text)
                            elif event.delta.type == "input_json_delta":
                                # Handle streaming tool call arguments
                                # Note: Anthropic gives us the ID in content_block_start
                                # We need to track current tool call index
                                yield OpenAIStyleChunk(tool_calls=[{
                                    "index": 0, # Simplified: support one tool call for now or track index
                                    "function": {"arguments": event.delta.partial_json}
                                }])
                        elif event.type == "content_block_start":
                            if event.content_block.type == "tool_use":
                                yield OpenAIStyleChunk(tool_calls=[{
                                    "index": 0,
                                    "id": event.content_block.id,
                                    "function": {"name": event.content_block.name, "arguments": ""}
                                }])

class GeminiAdapter:
    """
    Adapter for Google Gemini API via google-generativeai SDK.
    """
    def __init__(self, api_key: str, base_url: str = None, timeout: float = 60.0):
        genai.configure(api_key=api_key)
        self.chat = self.Chat()

    class Chat:
        def __init__(self):
            self.completions = self.Completions()

        class Completions:
            def create(self, model: str, messages: list, stream: bool = False, tools: list = None, **kwargs):
                # 1. Separate System Prompt and History
                system_instruction = None
                history = []
                last_user_message = None

                # Gemini roles: "user", "model"
                for m in messages:
                    role = m["role"]
                    content = m["content"]
                    if role == "system":
                        system_instruction = content
                    elif role == "user":
                        history.append({"role": "user", "parts": [content]})
                    elif role == "assistant":
                        if m.get("tool_calls"):
                            # Map tool calls to Gemini parts
                            parts = []
                            if content:
                                parts.append(content)
                            for tc in m["tool_calls"]:
                                parts.append(genai.protos.Part(
                                    tool_call=genai.protos.ToolCall(
                                        function_call=genai.protos.FunctionCall(
                                            name=tc["function"]["name"],
                                            args=json.loads(tc["function"]["arguments"]) if isinstance(tc["function"]["arguments"], str) else tc["function"]["arguments"]
                                        )
                                    )
                                ))
                            history.append({"role": "model", "parts": parts})
                        else:
                            history.append({"role": "model", "parts": [content or " "]})
                    elif role == "tool":
                        # Map tool result to Gemini parts
                        # Tool results must follow the model message with tool calls
                        history.append({
                            "role": "user", # Gemini expects tool results as 'user' role with function_response
                            "parts": [
                                genai.protos.Part(
                                    function_response=genai.protos.FunctionResponse(
                                        name=m.get("name"),
                                        response={"result": m.get("content")}
                                    )
                                )
                            ]
                        })

                # The last message should be extracted if it's user
                if history and history[-1]["role"] == "user" and not any(isinstance(p, genai.protos.Part) and (p.function_response) for p in history[-1]["parts"]):
                    last_user_message_dict = history.pop()
                    last_user_message = last_user_message_dict["parts"][0]
                
                # 2. Initialize Model
                gemini_tools = []
                if tools:
                    functions = []
                    for t in tools:
                        if t.get("type") == "function":
                            fn = t.get("function", {})
                            functions.append(fn)
                    if functions:
                        gemini_tools = [functions] # Gemini SDK expects a list of list of functions or a Tool object

                gen_model = genai.GenerativeModel(
                    model_name=model,
                    system_instruction=system_instruction,
                    tools=gemini_tools
                )

                # 3. Start Chat
                chat_session = gen_model.start_chat(history=history)

                # 4. Generate Response
                if stream:
                    return self._stream_response(chat_session, last_user_message)
                else:
                    response = chat_session.send_message(last_user_message or " ")
                    return self._map_response(response)

            def _map_response(self, response):
                content = ""
                tool_calls = []
                for part in response.candidates[0].content.parts:
                    if part.text:
                        content += part.text
                    if part.function_call:
                        tool_calls.append({
                            "id": f"call_{uuid.uuid4().hex[:12]}",
                            "function": {
                                "name": part.function_call.name,
                                "arguments": json.dumps(dict(part.function_call.args))
                            }
                        })

                # Use shared dataclasses
                mock_tool_calls = [
                    MockToolCall(
                        id=tc["id"],
                        function=type('obj', (object,), tc["function"])
                    ) for tc in tool_calls
                ] if tool_calls else None

                mock_message = MockMessage(content=content, tool_calls=mock_tool_calls)
                mock_choice = MockChoice(message=mock_message)
                return MockResponse(choices=[mock_choice])

            def _stream_response(self, chat_session, message):
                response = chat_session.send_message(message or " ", stream=True)
                for chunk in response:
                    # Gemini chunk.text raises ValueError if content is blocked or empty
                    try:
                        # Check for tool calls
                        for part in chunk.candidates[0].content.parts:
                            if part.text:
                                yield OpenAIStyleChunk(content=part.text)
                            if part.function_call:
                                yield OpenAIStyleChunk(tool_calls=[{
                                    "index": 0,
                                    "id": f"call_{uuid.uuid4().hex[:12]}",
                                    "function": {
                                        "name": part.function_call.name,
                                        "arguments": json.dumps(dict(part.function_call.args))
                                    }
                                }])
                    except (ValueError, IndexError, AttributeError):
                        pass # Blocked or empty chunk


from backend.infra.config import Config
from backend.utils.logger import Logger


class LLMFactory:
    """
    LLM Client Factory
    
    Provides static methods to create LLM clients and get model configuration.
    """
    
    @staticmethod
    def create_client(provider_key: Optional[str] = None) -> Optional[OpenAI]:
        """
        Create LLM Client
        """
        key = provider_key
        if not key:
            if Config.ACTIVE_PROVIDER and Config.ACTIVE_MODEL:
                key = f"{Config.ACTIVE_PROVIDER}/{Config.ACTIVE_MODEL}"
        
        llm_config = Config.get_provider_config(key)
        
        api_key = llm_config.get("api_key")
        base_url = llm_config.get("base_url")
        
        if not api_key:
            Logger.error(f"LLM API Key missing for provider '{key}'")
            return None
        
        # Debug log: Masked key
        masked_key = f"{api_key[:6]}...{api_key[-4:]}" if len(api_key) > 10 else "***"
        print(f"[LLM] Creating client for provider: {key}, model: {llm_config.get('model')}, base_url: {base_url}, key: {masked_key}")

        # Langfuse keys (from Config first, fallback env). Only enable when both exist and not disabled.
        lf_public = Config.LANGFUSE_PUBLIC_KEY or os.environ.get("LANGFUSE_PUBLIC_KEY", "")
        lf_secret = Config.LANGFUSE_SECRET_KEY or os.environ.get("LANGFUSE_SECRET_KEY", "")
        disable_lf = os.environ.get("DISABLE_LANGFUSE", "").lower() == "true"
        
        # Set reasonable timeout (60s) to prevent network fluctuation timeout
        if key == "anthropic":
            if not HAS_ANTHROPIC:
                Logger.error("Anthropic package not installed. Please run `pip install anthropic`.")
                return None
            return AnthropicAdapter(api_key=api_key, base_url=base_url, timeout=60.0)

        if key == "google":
            if not HAS_GOOGLE:
                Logger.error("Google Generative AI package not installed. Please run `pip install google-generativeai`.")
                return None
            return GeminiAdapter(api_key=api_key, base_url=base_url, timeout=60.0)

        if not disable_lf and lf_public and lf_secret and LangfuseOpenAI:
            return LangfuseOpenAI(api_key=api_key, base_url=base_url, timeout=60.0)
        
        return OpenAI(api_key=api_key, base_url=base_url, timeout=60.0)

    @staticmethod
    def get_model_name(provider_key: Optional[str] = None) -> str:
        """
        Get model name
        """
        key = provider_key
        if not key:
            if Config.ACTIVE_PROVIDER and Config.ACTIVE_MODEL:
                key = f"{Config.ACTIVE_PROVIDER}/{Config.ACTIVE_MODEL}"
        
        llm_config = Config.get_provider_config(key)
        return llm_config.get("model", "none")

