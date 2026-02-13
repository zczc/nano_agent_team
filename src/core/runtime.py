
import os
import json
import time
import datetime
import fcntl
from typing import Any
from src.utils.file_lock import file_lock

class RuntimeManager:
    """
    Helper for Agent Lifecycle and Blackboard Side-Effects.
    Allows reusable cleanup and logging across normal exits and middleware-triggered terminations.
    """

    @staticmethod
    def cleanup_agent(name: str, blackboard_dir: str, reason: str = "Self-terminated or normal exit"):
        """Performs full blackboard cleanup for a closing agent."""
        # 1. Update Registry to DEAD
        registry_path = os.path.join(blackboard_dir, "registry.json")
        try:
            with file_lock(registry_path, 'r+', fcntl.LOCK_EX, timeout=10) as fd:
                if fd:
                    content = fd.read()
                    try:
                        registry = json.loads(content) if content else {}
                    except json.JSONDecodeError:
                        registry = {}
                    
                    if name in registry:
                        registry[name]["status"] = "DEAD"
                        registry[name]["exit_time"] = time.time()
                        registry[name]["exit_reason"] = reason
                        
                        fd.seek(0)
                        json.dump(registry, fd, indent=2, ensure_ascii=False)
                        fd.truncate()
            print(f"[{name}] Status updated to DEAD in registry.")
        except Exception as e:
            print(f"[{name}] Failed to update registry status during cleanup: {e}")

        # 2. Log Termination Event
        RuntimeManager.log_event(name, blackboard_dir, "lifecycle", {"event": "terminated", "reason": reason})

        # 3. Broadcast Notification
        RuntimeManager.broadcast_notification(blackboard_dir, f"Agent [{name}] has left the swarm. Reason: {reason}")

    @staticmethod
    def log_event(name: str, blackboard_dir: str, event_type: str, data: Any):
        """Logs an event to the agent's specific JSONL file."""
        log_dir = os.path.join(blackboard_dir, "logs")
        if not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
            
        jsonl_path = os.path.join(log_dir, f"{name}.jsonl")
        log_entry = {
            "timestamp": time.time(),
            "type": event_type,
            "data": data
        }
        
        try:
            with open(jsonl_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
                f.flush()
        except Exception as e:
            print(f"[{name}] Failed to write to JSONL log: {e}")

    @staticmethod
    def broadcast_notification(blackboard_dir: str, message: str):
        """Appends a notification to the global notifications.md index."""
        notification_path = os.path.join(blackboard_dir, "global_indices", "notifications.md")
        os.makedirs(os.path.dirname(notification_path), exist_ok=True)
        
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"\n- [{timestamp}] {message}"
        
        try:
            with file_lock(notification_path, 'a', fcntl.LOCK_EX, timeout=10) as fd:
                if fd:
                    fd.write(entry)
        except Exception as e:
            print(f"[RuntimeManager] Failed to broadcast notification: {e}")
