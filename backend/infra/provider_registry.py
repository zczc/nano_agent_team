
import re
from typing import Dict, Any, Optional, List

class ProviderRegistry:
    """
    Registry of supported LLM Providers and Models.
    Replicates logic from opencode/packages/opencode/src/provider/{models.ts, provider.ts}
    """
    
    # Bundled Model Definitions (Simplified for Python)
    # Source: opencode/packages/opencode/src/provider/models.ts
    BUNDLED_PROVIDERS = {
        "openai": {
            "name": "OpenAI",
            "api_base": "https://api.openai.com/v1",
            "models": {
                "gpt-4o": "gpt-4o",
                "gpt-4-turbo": "gpt-4-turbo",
                "gpt-3.5-turbo": "gpt-3.5-turbo",
                "o1-preview": "o1-preview",
                "o1-mini": "o1-mini"
            }
        },
        "anthropic": {
            "name": "Anthropic",
            "api_base": "https://api.anthropic.com/v1",
            "models": {
                "claude-3-5-sonnet-20241022": "claude-3-5-sonnet-20241022",
                "claude-3-5-haiku-20241022": "claude-3-5-haiku-20241022",
                "claude-3-opus-20240229": "claude-3-opus-20240229"
            }
        },
        "deepseek": {
            "name": "DeepSeek",
            "api_base": "https://api.deepseek.com",
            "models": {
                "deepseek-chat": "deepseek-chat",
                "deepseek-reasoner": "deepseek-reasoner"
            }
        },
        "qwen": {
            "name": "Qwen (Aliyun)",
             # Note: Users often need to override base_url for specific deployments, 
             # but this sets a reasonable default or template.
            "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "models": {
                "qwen-turbo": "qwen-turbo",
                "qwen-plus": "qwen-plus",
                "qwen-max": "qwen-max",
                "qwen2.5-72b-instruct": "qwen2.5-72b-instruct"
            }
        },
        "google": {
            "name": "Google Gemini",
            "api_base": "https://generativelanguage.googleapis.com/v1beta/openai",
            "models": {
                "gemini-1.5-pro": "gemini-1.5-pro",
                "gemini-1.5-flash": "gemini-1.5-flash",
                "gemini-2.0-flash-exp": "gemini-2.0-flash-exp"
            }
        },
        "openrouter": {
            "name": "OpenRouter",
            "api_base": "https://openrouter.ai/api/v1",
            "models": {
                # OpenRouter has too many models, we list prominent ones or handle dynamic
                "anthropic/claude-3.5-sonnet": "anthropic/claude-3.5-sonnet",
                "openai/gpt-4o": "openai/gpt-4o",
                "google/gemini-pro-1.5": "google/gemini-pro-1.5"
            }
        },
        "groq": {
            "name": "Groq",
            "api_base": "https://api.groq.com/openai/v1",
            "models": {
                "llama3-70b-8192": "llama3-70b-8192",
                "mixtral-8x7b-32768": "mixtral-8x7b-32768"
            }
        }
    }

    @classmethod
    def list_providers(cls) -> Dict[str, Any]:
        """List all supported providers and their models."""
        return cls.BUNDLED_PROVIDERS

    @classmethod
    def get_provider(cls, provider_id: str) -> Optional[Dict[str, Any]]:
        return cls.BUNDLED_PROVIDERS.get(provider_id)

    @classmethod
    def resolve_model(cls, model_id: str) -> Dict[str, Any]:
        """
        Resolve a model ID string (e.g. 'openai/gpt-4o') to configuration.
        Returns: { 'provider': str, 'model': str, 'base_url': str }
        """
        parts = model_id.split('/', 1)
        if len(parts) == 2:
            provider_id, model_name = parts
        else:
            # Fallback or concise format? 
            # If input is just 'gpt-4o', we might scan providers, but opencode usually uses provider/model
            # For now assume provider is required if default not handled here.
            return {}

        provider = cls.get_provider(provider_id)
        if not provider:
            return {}

        return {
            "provider": provider_id,
            "model": model_name,
            "base_url": provider.get("api_base")
        }
