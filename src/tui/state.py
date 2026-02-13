"""
State Manager for TUI
Manages models, providers, messages, favorites, and recents
"""

from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from enum import Enum
import json
import os
import threading
from datetime import datetime
from backend.utils.logger import Logger



class AgentMode(Enum):
    """Agent Run Mode"""
    CHAT = "chat"      # Standard Chat Mode
    SWARM = "swarm"    # Swarm Multi-Agent Mode


@dataclass
class ModelInfo:
    """Model information"""
    provider_id: str
    model_id: str
    name: str = ""
    description: str = ""
    
    def __eq__(self, other):
        if not isinstance(other, ModelInfo):
            return False
        return self.provider_id == other.provider_id and self.model_id == other.model_id
    
    def __hash__(self):
        return hash((self.provider_id, self.model_id))
    
    def to_dict(self) -> dict:
        return {"provider_id": self.provider_id, "model_id": self.model_id, "name": self.name}
    
    @classmethod
    def from_dict(cls, data: dict) -> "ModelInfo":
        return cls(
            provider_id=data.get("provider_id", ""),
            model_id=data.get("model_id", ""),
            name=data.get("name", ""),
        )


@dataclass  
class ProviderInfo:
    """Provider information"""
    id: str
    name: str
    connected: bool = False
    description: str = ""
    recommended: bool = False


class StateManager:
    """
    Central state management for TUI.
    Handles persistence of favorites, recents, and preferences.
    Implemented as a Singleton.
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(StateManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if getattr(self, '_initialized', False):
            return
            
        self._current_model: Optional[ModelInfo] = None
        self._recents: List[ModelInfo] = []
        self._providers: Dict[str, ProviderInfo] = {}
        self._selected_provider_id: Optional[str] = None
        self._session_id: Optional[str] = None
        
        # Agent mode (Shared State)

        self._agent_mode: AgentMode = AgentMode.CHAT
        
        # Shared agent messages (History for AgentBridge)
        self._agent_messages: List[Dict[str, Any]] = []
        self._agent_lock = threading.Lock()
        
        # Swarm configuration
        self._swarm_max_iterations: int = 200  # Default matches main.py
        
        # Persistence path
        self._data_dir = os.path.expanduser("~/.nano_agent_team")
        self._state_file = os.path.join(self._data_dir, "tui_state.json")
        
        # Load saved state
        self._load()
        self._initialized = True
    
    # === Model Management ===
    
    @property
    def current_model(self) -> Optional[ModelInfo]:
        """Get current selected model"""
        return self._current_model
    
    def set_model(self, model: ModelInfo, add_to_recent: bool = True):
        """Set current model"""
        self._current_model = model
        if add_to_recent:
            self._update_recents(model)
        self._save()  # Single save for the whole operation
        
        # Sync to global Config so child processes can inherit
        try:
            from backend.infra.config import Config
            # Use name as the identifier for Config mapping
            model_key = f"{model.provider_id}/{model.name}"
            Config.set_active_model(model_key)
        except Exception as e:
            Logger.debug(f"[State] Failed to sync model to Config: {e}")
    
    # === Recents ===
    
    @property
    def recents(self) -> List[ModelInfo]:
        """Get recent models (max 10)"""
        return self._recents[:10]
    
    def _update_recents(self, model: ModelInfo):
        """Internal: update recents list without saving (caller is responsible for _save)."""
        if model in self._recents:
            self._recents.remove(model)
        self._recents.insert(0, model)
        self._recents = self._recents[:10]
    
    def add_recent(self, model: ModelInfo):
        """Add model to recents (public API, triggers save)."""
        self._update_recents(model)
        self._save()
    
    def cycle_recent(self):
        """
        Toggle between the two most recent models.
        Behaves like Alt-Tab.
        """
        if not self._recents:
            return
            
        # If we have less than 2 models, just stay on the first one
        if len(self._recents) < 2:
            self._current_model = self._recents[0]
            return

        # Toggle between first and second recent
        # recents[0] is always the *most* recent (current)
        # recents[1] is the *previous* model
        
        # However, due to how set_model(add_to_recent=True) works:
        # When we select a model, it moves to recents[0].
        # So recents[0] is basically 'current'.
        # We want to switch to recents[1].
        
        target_model = self._recents[1]
        
        # Set model but DON'T add to recent recursively here to avoid reordering list yet
        # Actually, to make 'set_model' effectively swap them in the list, 
        # we strictly want to select recents[1]. 
        # When set_model is called with it, it will move to recents[0].
        
        self.set_model(target_model, add_to_recent=True)
    
    # === Providers ===
    
    @property
    def selected_provider_id(self) -> Optional[str]:
        """Get currently selected provider context"""
        return self._selected_provider_id
        
    def set_selected_provider(self, provider_id: str):
        """Set current provider context"""
        self._selected_provider_id = provider_id
    
    @property
    def providers(self) -> Dict[str, ProviderInfo]:
        """Get all providers"""
        return self._providers.copy()
    
    def set_providers(self, providers: Dict[str, ProviderInfo]):
        """Set providers"""
        self._providers = providers
    
    def get_provider(self, provider_id: str) -> Optional[ProviderInfo]:
        """Get provider by ID"""
        return self._providers.get(provider_id)
    
    def set_provider_connected(self, provider_id: str, connected: bool):
        """Update provider connection status"""
        if provider_id in self._providers:
            self._providers[provider_id].connected = connected
    
    # === Agent Mode ===
    
    @property
    def agent_mode(self) -> AgentMode:
        """Get current agent mode"""
        return self._agent_mode
    
    def set_agent_mode(self, mode: AgentMode):
        """Set agent mode"""
        self._agent_mode = mode
    
    def toggle_agent_mode(self) -> AgentMode:
        """Toggle between Chat and Swarm mode, returns new mode"""
        if self._agent_mode == AgentMode.CHAT:
            self._agent_mode = AgentMode.SWARM
        else:
            self._agent_mode = AgentMode.CHAT
        return self._agent_mode
    
    # === Shared Agent Messages (for AgentBridge) ===
    
    @property
    def agent_messages(self) -> List[Dict[str, Any]]:
        """Get a snapshot copy of shared agent messages (thread-safe)."""
        with self._agent_lock:
            return list(self._agent_messages)
    
    def get_agent_messages_ref(self) -> List[Dict[str, Any]]:
        """Get the raw mutable reference to agent messages.
        
        WARNING: Not thread-safe. Only use where AgentEngine.run() needs
        the mutable list for in-place updates. Caller must coordinate access.
        """
        return self._agent_messages
    
    def add_agent_message(self, role: str, content: str):
        """Add a message to shared agent history (thread-safe)."""
        with self._agent_lock:
            self._agent_messages.append({"role": role, "content": content})
    
    def clear_agent_messages(self):
        """Clear shared agent messages (thread-safe)."""
        with self._agent_lock:
            self._agent_messages.clear()
    
    # === Swarm Configuration ===
    
    @property
    def swarm_max_iterations(self) -> int:
        """Get max iterations for Swarm mode"""
        return self._swarm_max_iterations
    
    @swarm_max_iterations.setter
    def swarm_max_iterations(self, value: int):
        """Set max iterations for Swarm mode"""
        self._swarm_max_iterations = max(10, min(500, value))  # Clamp 10-500
        self._save()
    
    # === Session ID Management ===
    
    @property
    def session_id(self) -> str:
        """Get or initialize the current session ID"""
        if not self._session_id:
            self.refresh_session_id()
        return self._session_id

    def refresh_session_id(self) -> str:
        """Generate a new timestamp-based session ID. Does NOT sync config yet."""
        self._session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        return self._session_id

    def sync_blackboard_root(self):
        """
        Sync the isolated blackboard path to the global Config.
        Should be called just before starting a Swarm task.
        """
        if not self._session_id:
            self.refresh_session_id()
            
        try:
            from backend.infra.config import Config
            # We base it on the original ROOT_PATH/ .blackboard
            base_bb = os.path.join(Config.ROOT_PATH, ".blackboard")
            isolated_bb = os.path.join(base_bb, f"session_{self._session_id}")
            Config.BLACKBOARD_ROOT = isolated_bb
        except Exception as e:
            Logger.warning(f"[State] Failed to sync blackboard root: {e}")

    
    # === Persistence ===
    
    def _load(self):
        """Load state from file"""
        if not os.path.exists(self._state_file):
            return
        
        try:
            with open(self._state_file, "r") as f:
                data = json.load(f)
            
            # Load recents
            self._recents = [
                ModelInfo.from_dict(m) for m in data.get("recents", [])
            ]
            
            # Load current model
            if data.get("current_model"):
                self._current_model = ModelInfo.from_dict(data["current_model"])
                
        except Exception as e:
            Logger.debug(f"[State] Failed to load state: {e}")
    
    def _save(self):
        """Save state to file"""
        os.makedirs(self._data_dir, exist_ok=True)
        
        data = {
            "recents": [m.to_dict() for m in self._recents],
            "current_model": self._current_model.to_dict() if self._current_model else None,
        }
        
        try:
            with open(self._state_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            Logger.debug(f"[State] Failed to save state: {e}")
    
    # === Model Key Support ===
    
    def set_model_from_key(self, key: str):
        """
        Set model from a provider key string.
        
        Formats:
        - "provider_key" (e.g. "gpt4o") -> lookup in settings.json
        - "provider/model_id" (e.g. "openai/gpt-4o")
        """
        if "/" in key:
            # Format: provider/model_id
            parts = key.split("/", 1)
            provider_id = parts[0]
            model_id = parts[1]
            model = ModelInfo(provider_id=provider_id, model_id=model_id, name=model_id)
            self.set_model(model)
        else:
            # Lookup in config
            try:
                from backend.infra.config import Config
                Config.initialize()
                provider_config = Config.get_provider_config(key)
                if provider_config:
                    # provider_config is already the provider dict
                    # Try to extract provider_id and model_id
                    provider_id = provider_config.get("provider", key)
                    model_id = provider_config.get("model", key)
                    name = provider_config.get("name", model_id)
                    model = ModelInfo(provider_id=provider_id, model_id=model_id, name=name)
                    self.set_model(model)
            except Exception:
                # Fallback: use key as both provider and model
                model = ModelInfo(provider_id=key, model_id=key, name=key)
                self.set_model(model)
    
    def get_model_key(self) -> Optional[str]:
        """Get current model as a key string for use with Config/Agent (provider/name)"""
        if not self._current_model:
            return None
        return f"{self._current_model.provider_id}/{self._current_model.name}"


# Global state instance
state = StateManager()
