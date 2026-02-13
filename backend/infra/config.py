"""
Global Configuration Management

Simplified Nano Agent Team configuration.
Includes core LLM, path, and external service configurations.
"""

import os
import json
from typing import Dict, Any, Optional
from backend.infra.provider_registry import ProviderRegistry
from backend.infra.auth import AuthManager

class Config:
    """
    Global Configuration Class
    """
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    # settings.json path
    _settings_path = os.path.join(BASE_DIR, 'backend', 'settings.json')
    _llm_config_path = os.path.join(BASE_DIR, 'backend', 'llm_config.json')
    
    _data = {}
    _llm_config = {
        "providers": {} 
    }
    
    # Default values
    _llm_access = {} # Deprecated, kept for compat during migration
    _external_services = {}
    
    # Path configuration
    LOG_DIR = os.path.join(BASE_DIR, "logs")
    AGENTS_DIR = os.path.join(BASE_DIR, "backend", "agents")
    SKILLS_DIR = os.path.join(BASE_DIR, ".skills")
    
    # Log File Path
    LOG_PATH = os.path.join(LOG_DIR, "app.log")
    
    # Path Variables for Substitution
    ROOT_PATH = BASE_DIR
    BLACKBOARD_ROOT = os.path.join(BASE_DIR, ".blackboard") # Default
    
    # External Service Keys
    LANGFUSE_PUBLIC_KEY = ""
    LANGFUSE_SECRET_KEY = ""
    LANGFUSE_HOST = "https://cloud.langfuse.com"
    
    # Default Provider and Model (loaded from tui_state.json)
    ACTIVE_PROVIDER = None
    ACTIVE_MODEL = None
    SEARCH_PROVIDER = "duckduckgo"
    JINA_READER_KEY = ""

    @classmethod
    def load_settings(cls):
        """Load settings.json"""
        try:
            if os.path.exists(cls._settings_path):
                with open(cls._settings_path, 'r', encoding='utf-8') as f:
                    cls._data = json.load(f)
            
            # Load General Settings
            cls._external_services = cls._data.get('external_services', {})
            
            # Langfuse
            lf_config = cls._external_services.get('langfuse', {})
            cls.LANGFUSE_HOST = lf_config.get('host', cls.LANGFUSE_HOST)
            
            # Search
            cls.SEARCH_PROVIDER = cls._data.get('search', {}).get('provider', 'duckduckgo')
            
            # Jina Reader
            jina_config = cls._external_services.get('jina', {})
            cls.JINA_READER_KEY = jina_config.get('api_key', '')

            # --- LLM Config Loading & Migration ---
            cls.load_llm_config()

        except Exception as e:
            print(f"[Config] Error loading settings: {e}")

    @classmethod
    def load_llm_config(cls):
        """
        Load LLM configuration from llm_config.json.
        Migrates from settings.json if llm_config.json does not exist.
        Returns the loaded config dict.
        """
        if os.path.exists(cls._llm_config_path):
            try:
                with open(cls._llm_config_path, 'r', encoding='utf-8') as f:
                    cls._llm_config = json.load(f)
                
                # active_model is now handled by StateManager (tui_state.json)
                return cls._llm_config
            except Exception as e:
                print(f"[Config] Error loading llm_config.json: {e}")
        
        # --- Migration Logic ---
        # If llm_config.json doesn't exist, try to migrate from settings.json
        print("[Config] llm_config.json not found. Attempting migration from settings.json...")
        legacy_llm = cls._data.get('llm_access', {})
        default_model = cls._data.get('default_provider')
        
        if legacy_llm:
            new_providers = {}
            
            for key, val in legacy_llm.items():
                # Heuristic to determine provider ID. 
                # Old keys were like "qwen3-max", "openai". 
                # We need to group them or treat them as separate providers for now.
                # To be safe, we'll treat each legacy entry as a "provider" configuration 
                # with one model, ensuring backward compatibility.
                
                # If the key looks like a known provider in ProviderRegistry, map it.
                # But here we simply create a provider entry for it.
                
                provider_id = key
                model_name = val.get("model")
                base_url = val.get("base_url")
                
                # In the new schema:
                # providers: {
                #   "provider_id": {
                #       "base_url": "...",
                #       "models": [ { "name": "...", "id": "..." } ]
                #    }
                # }
                
                new_providers[provider_id] = {
                    "base_url": base_url,
                    "models": [
                        { "name": model_name, "id": model_name } # Use model name as ID for simplicity
                    ]
                }

            cls._llm_config = {
                "providers": new_providers
            }
            
            if default_model:
                if "/" in default_model:
                    cls.ACTIVE_PROVIDER, cls.ACTIVE_MODEL = default_model.split("/", 1)
                else:
                    cls.ACTIVE_PROVIDER = default_model
                    cls.ACTIVE_MODEL = None
            cls.save_llm_config()
            print(f"[Config] Migration complete. Saved to {cls._llm_config_path}")
        
        return cls._llm_config

    @classmethod
    def save_llm_config(cls):
        """Save current LLM config to file."""
        try:
            with open(cls._llm_config_path, 'w', encoding='utf-8') as f:
                json.dump(cls._llm_config, f, indent=2)
        except Exception as e:
            print(f"[Config] Error saving llm_config.json: {e}")

    @classmethod
    def load_keys(cls, keys_path: str):
        """Load keys.json and inject configuration"""
        if not keys_path or not os.path.exists(keys_path):
            return 
            
        try:
            with open(keys_path, 'r', encoding='utf-8') as f:
                keys_data = json.load(f)
                
            for k, v in keys_data.items():
                if k == "langfuse_public_key":
                    cls.LANGFUSE_PUBLIC_KEY = v
                elif k == "langfuse_secret_key":
                    cls.LANGFUSE_SECRET_KEY = v
                else:
                    # Inject into AuthManager
                    # Support both simple key string and full auth dict object
                    if isinstance(v, dict):
                         AuthManager.set(k, v)
                    else:
                         # Default to API key type for string values
                         # Check if already exists to avoid overwriting with same key potentially?
                         # AuthManager.set overwrites.
                         AuthManager.set(k, {"type": "api", "key": v})
                        
        except Exception as e:
            print(f"[Config] Error loading keys: {e}")

    @classmethod
    def _load_active_model_state(cls):
        """Load the last active model from tui_state.json (Interaction State)"""
        try:
            state_file = os.path.expanduser("~/.nano_agent_team/tui_state.json")
            if os.path.exists(state_file):
                with open(state_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    current = data.get("current_model")
                    if current:
                        cls.ACTIVE_PROVIDER = current.get("provider_id")
                        cls.ACTIVE_MODEL = current.get("name") # Use 'name' as identifier as requested
        except Exception:
            pass # Silent failure for state loading

    @classmethod
    def initialize(cls, keys_path: str = None):
        """Initialize configuration"""
        cls.load_settings()
        
        # Load interaction state (active model)
        cls._load_active_model_state()
        
        # Auto-discover keys.json
        if not keys_path:
            possible_keys = os.path.join(cls.BASE_DIR, "keys.json")
            if os.path.exists(possible_keys):
                keys_path = possible_keys
        
        if keys_path:
            cls.load_keys(keys_path)
            
        cls._apply_env_overrides()
        cls.ensure_dirs()

    @classmethod
    def _apply_env_overrides(cls):
        """Environment variable overrides"""
        if os.environ.get("LANGFUSE_PUBLIC_KEY"):
            cls.LANGFUSE_PUBLIC_KEY = os.environ["LANGFUSE_PUBLIC_KEY"]
        if os.environ.get("LANGFUSE_SECRET_KEY"):
            cls.LANGFUSE_SECRET_KEY = os.environ["LANGFUSE_SECRET_KEY"]
            
        # Keys for providers are handled by AuthManager + Env lookup in get_provider_config

    @classmethod
    def get_provider_config(cls, model_id: str) -> Dict[str, Any]:
        """
        Core method: Get LLM configuration
        
        Args:
            model_id: Can be a provider ID (e.g. "openai") or "provider/model" (e.g. "openai/gpt-4o")
                      or simply a model ID reference from llm_config settings.
        """
        
        
        # 1. Parse Input
        if not model_id:
            if cls.ACTIVE_PROVIDER and cls.ACTIVE_MODEL:
                model_id = f"{cls.ACTIVE_PROVIDER}/{cls.ACTIVE_MODEL}"
            
        if not model_id:
            return {}

        target_provider = model_id
        target_model_name = None
        
        if "/" in model_id:
            target_provider, target_model_name = model_id.split("/", 1)
        
        # 2. Look up in llm_config.json
        # The schema is: providers -> [provider_id] -> base_url, models list
        
        providers = cls._llm_config.get("providers", {})
        provider_config = providers.get(target_provider)
        
        if not provider_config:
            # Fallback: maybe model_id was actually a direct key in the old system (e.g. "qwen3-max")
            # In our migration, we turned those keys into provider IDs.
            # So if it's not found, return empty or try to resolve via Registry defaults?
            
            # Try Registry defaults if not in our config
            meta = ProviderRegistry.resolve_model(model_id)
            if meta:
                 return {
                    "api_key": cls._get_api_key(meta["provider"]),
                    "base_url": meta["base_url"],
                    "model": meta["model"]
                }
            return {}

        # 3. Construct Config
        base_url = provider_config.get("base_url")
        
        final_model_name = target_model_name
        model_api_id = target_model_name
        
        if not final_model_name:
            # Default to first model
            models = provider_config.get("models", [])
            if models:
                first_model = models[0]
                final_model_name = first_model.get("id")
                model_api_id = first_model.get("name", final_model_name)
        else:
            # Look up specific model to get name as api_id
            models = provider_config.get("models", [])
            found_model = next((m for m in models if m["id"] == target_model_name), None)
            if found_model:
                model_api_id = found_model.get("name", target_model_name)
        
        # 4. Get API Key from AuthManager or Env
        env_vars = provider_config.get("env", [])
        api_key = cls._get_api_key(target_provider, env_vars)

        return {
            "api_key": api_key,
            "base_url": base_url,
            "model": model_api_id
        }

    @classmethod
    def _get_api_key(cls, provider_id: str, env_vars: list = None) -> Optional[str]:
        """Helper to get API key with Env fallback"""
        # 1. AuthManager (Explicitly set keys take precedence)
        auth_info = AuthManager.get(provider_id)
        if auth_info and auth_info.get("type") == "api":
             return auth_info.get("key")
        
        # 2. Configured Env Vars (from llm_config.json)
        if env_vars:
            for var_name in env_vars:
                val = os.environ.get(var_name)
                if val: return val

        # 3. Default Env Var (PROVIDER_API_KEY)
        env_var = f"{provider_id.upper()}_API_KEY"
        return os.environ.get(env_var)
        
    @classmethod
    def ensure_dirs(cls):
        if not os.path.exists(cls.LOG_DIR):
            os.makedirs(cls.LOG_DIR)
            
    # --- Management API for TUI ---
    
    @classmethod
    def get_all_providers(cls) -> Dict[str, Any]:
        return cls._llm_config.get("providers", {})
        
    @classmethod
    def set_active_model(cls, model_id: str):
        # model_id is expected as "provider/model_name"
        if "/" in model_id:
            cls.ACTIVE_PROVIDER, cls.ACTIVE_MODEL = model_id.split("/", 1)
        # Note: Persistence is handled by StateManager in TUI mode.
        # We no longer save active_model to llm_config.json.
        
    @classmethod
    def update_provider(cls, provider_id: str, base_url: str):
        if provider_id not in cls._llm_config["providers"]:
            cls._llm_config["providers"][provider_id] = {"models": []}
        
        cls._llm_config["providers"][provider_id]["base_url"] = base_url
        cls.save_llm_config()
        
    @classmethod
    def add_model(cls, provider_id: str, name: str, model_id: str):
        if provider_id not in cls._llm_config["providers"]:
            return # Should create provider first
            
        # Check if exists
        models = cls._llm_config["providers"][provider_id].get("models", [])
        for m in models:
            if m["id"] == model_id:
                m["name"] = name
                cls.save_llm_config()
                return
                
        # Add new
        models.append({"name": name, "id": model_id})
        cls._llm_config["providers"][provider_id]["models"] = models
        cls.save_llm_config()

    @classmethod
    def delete_provider(cls, provider_id: str):
        if provider_id in cls._llm_config["providers"]:
            del cls._llm_config["providers"][provider_id]
            cls.save_llm_config()
            
            # Also remove auth key
            AuthManager.remove(provider_id)
            
    @classmethod
    def delete_model(cls, provider_id: str, model_id: str):
        if provider_id in cls._llm_config["providers"]:
            models = cls._llm_config["providers"][provider_id].get("models", [])
            cls._llm_config["providers"][provider_id]["models"] = [
                m for m in models if m["id"] != model_id
            ]
            cls.save_llm_config()


# 默认初始化
Config.initialize()
