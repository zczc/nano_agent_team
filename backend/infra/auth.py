
import os
import json
import stat
from typing import Dict, Any, Optional

class AuthManager:
    """
    Manages secure storage of API credentials.
    Replicates logic from opencode/packages/opencode/src/auth/index.ts
    
    Storage: .nano_agent_team/auth.json
    Permissions: 0o600 (User Read/Write only)
    """
    
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    DATA_DIR = os.path.join(os.path.expanduser("~"), ".nano_agent_team")
    AUTH_FILE = os.path.join(DATA_DIR, "auth.json")
    
    @classmethod
    def _ensure_dir(cls):
        if not os.path.exists(cls.DATA_DIR):
            os.makedirs(cls.DATA_DIR, mode=0o700, exist_ok=True)
            
    @classmethod
    def all(cls) -> Dict[str, Any]:
        """
        Retrieve all stored auth credentials.
        Returns: { provider_id: { type: 'api', key: '...' }, ... }
        """
        if not os.path.exists(cls.AUTH_FILE):
            return {}
            
        try:
            with open(cls.AUTH_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[AuthManager] Error reading auth file: {e}")
            return {}
            
    @classmethod
    def get(cls, provider_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve auth info for a specific provider."""
        data = cls.all()
        return data.get(provider_id)
        
    @classmethod
    def set(cls, provider_id: str, info: Dict[str, Any]):
        """
        Save auth info for a provider.
        Enforces 0o600 permissions on the file.
        """
        cls._ensure_dir()
        
        data = cls.all()
        data[provider_id] = info
        
        # Write securely
        try:
            # Write to temp file then rename to ensure atomicity and permission retention issues avoided
            temp_file = cls.AUTH_FILE + ".tmp"
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            
            os.replace(temp_file, cls.AUTH_FILE)
            os.chmod(cls.AUTH_FILE, stat.S_IRUSR | stat.S_IWUSR)
            
        except OSError as e:
            print(f"[AuthManager] Error saving auth file: {e}")

    @classmethod
    def remove(cls, provider_id: str):
        """Remove auth info for a provider."""
        if not os.path.exists(cls.AUTH_FILE):
            return
            
        data = cls.all()
        if provider_id in data:
            del data[provider_id]
            
            try:
                temp_file = cls.AUTH_FILE + ".tmp"
                with open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2)
                    
                os.replace(temp_file, cls.AUTH_FILE)
                os.chmod(cls.AUTH_FILE, stat.S_IRUSR | stat.S_IWUSR)
            except OSError as e:
                print(f"[AuthManager] Error saving auth file during removal: {e}")

    @classmethod
    def has_key_for_provider(cls, provider_id: str, env_keys: list = None) -> bool:
        """
        Check if auth is available for a provider.
        Checks in order:
        1. Environment variables (from env_keys list)
        2. Auth storage (~/.nano_agent_team/auth.json)
        
        Returns True if any key source is found.
        """
        # Check environment variables
        if env_keys:
            for env_key in env_keys:
                if os.environ.get(env_key):
                    return True
        
        # Check auth storage
        if cls.get(provider_id):
            return True
            
        return False
    
    @classmethod
    def get_key_for_provider(cls, provider_id: str, env_keys: list = None) -> Optional[str]:
        """
        Get the API key for a provider.
        Checks in order:
        1. Environment variables (from env_keys list)
        2. Auth storage (~/.nano_agent_team/auth.json)
        
        Returns the key string or None.
        """
        # Check auth storage first (User Preference)
        auth_info = cls.get(provider_id)
        if auth_info:
            if isinstance(auth_info, dict):
                return auth_info.get("key")
            return auth_info

        # Check environment variables
        if env_keys:
            for env_key in env_keys:
                key = os.environ.get(env_key)
                if key:
                    return key
            
        return None

