
import time
import os
from typing import Dict, Any
from backend.tools.base import BaseTool
from backend.llm.decorators import schema_strict_validator

class WaitTool(BaseTool):
    """
    Tool for pausing execution, optionally waiting for Blackboard updates.
    Monitors both global_indices (task updates) and mailboxes (messages).
    """
    def __init__(self, watch_dir: str = ".blackboard/global_indices", blackboard_root: str = ".blackboard"):
        super().__init__()
        self.watch_dir = watch_dir
        self.blackboard_root = blackboard_root
        self.mailboxes_dir = os.path.join(blackboard_root, "mailboxes")
        self._agent_name = None  # Will be set via configure()
    
    @property
    def name(self) -> str:
        return "wait"
    
    def configure(self, context: Dict[str, Any]):
        """Configure with agent context"""
        self._agent_name = context.get("agent_name")
    
    @property
    def description(self) -> str:
        return """Pause execution.
Can simply sleep for a duration, OR wait until new activity is detected in global_indices or your mailbox.
Monitors:
  - global_indices/ (task updates from other agents)
  - mailboxes/{your_name}.json (messages sent to you)
Use this when you are waiting for other agents to reply or post tasks.
"""
    
    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "duration": {
                    "type": "number",
                    "default": 15,
                    "description": "Maximum wait time in seconds (strictly ≤ 15s)."
                },
                "wait_for_new_index": {
                    "type": "boolean",
                    "default": True,
                    "description": "If true, returns early if any file in global_indices is modified."
                },
                "reason": {
                    "type": "string",
                    "description": "Optional reason for waiting (used for logging/protocol enforcement)."
                }
            }
        }

    def _get_max_mtime(self) -> float:
        """
        Get the latest modification time across monitored locations:
        1. global_indices/ directory
        2. mailboxes/{agent_name}.json file (if agent name is known)
        """
        max_mtime = 0.0
        
        # 1. Monitor global_indices directory
        if os.path.exists(self.watch_dir):
            try:
                # Check directory mtime (for added/deleted files)
                dir_mtime = os.path.getmtime(self.watch_dir)
                if dir_mtime > max_mtime:
                    max_mtime = dir_mtime
                
                # Check individual files mtime (for appends)
                for fname in os.listdir(self.watch_dir):
                    fpath = os.path.join(self.watch_dir, fname)
                    if os.path.isfile(fpath):
                        mtime = os.path.getmtime(fpath)
                        if mtime > max_mtime:
                            max_mtime = mtime
            except OSError:
                pass
        
        # 2. Monitor agent's mailbox file
        if self._agent_name:
            mailbox_path = os.path.join(self.mailboxes_dir, f"{self._agent_name}.json")
            if os.path.exists(mailbox_path):
                try:
                    mtime = os.path.getmtime(mailbox_path)
                    if mtime > max_mtime:
                        max_mtime = mtime
                except OSError:
                    pass
        
        return max_mtime

    @schema_strict_validator
    def execute(self, duration: int = 15, wait_for_new_index: bool = True, reason: str = None) -> str:
        """
        Execute the wait.
        Args:
            duration: Seconds to wait max.
            wait_for_new_index: If True, poll for blackboard changes (global_indices + mailbox).
            reason: Optional reason for waiting (used for logging/protocol enforcement).
        """
        prefix = f"[Reason: {reason}] " if reason else ""
        
        if not wait_for_new_index:
            time.sleep(duration)
            return f"{prefix}Waited for {duration} seconds."
        
        initial_mtime = self._get_max_mtime()
        start_time = time.time()
        
        while (time.time() - start_time) < duration:
            current_mtime = self._get_max_mtime()
            if current_mtime > initial_mtime:
                # Determine what changed
                change_location = "Global Indices"
                if self._agent_name:
                    mailbox_path = os.path.join(self.mailboxes_dir, f"{self._agent_name}.json")
                    if os.path.exists(mailbox_path):
                        mailbox_mtime = os.path.getmtime(mailbox_path)
                        if mailbox_mtime >= initial_mtime:
                            change_location = "Mailbox"
                
                return f"{prefix}New activity detected in {change_location}! Waking up."
            time.sleep(1) # Poll interval
            
        return f"{prefix}No new activity detected after {duration} seconds."
