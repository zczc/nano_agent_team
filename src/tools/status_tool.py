
import os
import time
import subprocess
import json
from typing import Dict, Any, List

from backend.tools.base import BaseTool
from backend.llm.decorators import schema_strict_validator

class SwarmStatusTool(BaseTool):
    """
    Introspection tool for the Watchdog Overseer.
    Checks process health, blackboard activity, and admin directives.
    """
    def __init__(self, blackboard_dir: str = ".blackboard"):
        super().__init__()
        self.blackboard_dir = blackboard_dir
        self.indices_dir = os.path.join(blackboard_dir, "global_indices")
        self.logs_dir = os.path.join(blackboard_dir, "logs")

    @property
    def name(self) -> str:
        return "check_swarm_status"

    @property
    def description(self) -> str:
        return """Returns the comprehensive status of the Swarm:
        1. Active/Dead Processes (based on .blackboard/logs/ PIDs)
        2. Last activity on central_plan.md (Progress)
        3. Recent global events from primary_timeline.md
        4. Pending Admin Directives from admin_directives.md
        Use this to Decide whether to Wait, Spawn (Recover), or Intervene.
        """

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "required": []
        }

    def _get_process_status(self) -> List[Dict]:
        """Scans logs dir for inferred agents and checks their PIDs."""
        agents = []
        if not os.path.exists(self.logs_dir):
            return agents

        for log_name in os.listdir(self.logs_dir):
            if log_name.endswith(".log"):
                agent_name = log_name[:-4]
                pid = self._extract_pid_from_log(log_name)
                
                status = "UNKNOWN"
                if pid:
                    is_running = self._check_pid_running(pid)
                    status = "RUNNING" if is_running else "DEAD"
                else:
                    status = "NO_PID_FOUND"
                
                agents.append({
                    "name": agent_name,
                    "pid": pid,
                    "status": status
                })
        return agents

    def _extract_pid_from_log(self, log_name: str) -> int:
        """Reads the log file to find the LAST occurrence of 'PID: <pid>'."""
        log_path = os.path.join(self.logs_dir, log_name)
        pid = None
        try:
            with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                # Read line by line, keep the last valid PID found
                for line in f:
                    if line.startswith("PID: "):
                        try:
                            pid = int(line.strip().split(": ")[1])
                        except ValueError:
                            pass
        except Exception:
            pass
        return pid

    def _check_pid_running(self, pid: int) -> bool:
        """Checks if a PID is running using os.kill(pid, 0)."""
        if not pid:
            return False
        try:
            # Signal 0 does not terminate, just checks existence
            os.kill(pid, 0)
            return True
        except OSError:
            return False



    def _get_file_info(self, filename: str) -> Dict:
        path = os.path.join(self.indices_dir, filename)
        if not os.path.exists(path):
            return {"exists": False}
        
        stat = os.stat(path)
        last_modified = time.time() - stat.st_mtime
        
        # Read tail
        content_tail = ""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                content_tail = "".join(lines[-5:])
        except:
            content_tail = "Error reading file"
            
        return {
            "exists": True,
            "last_modified_seconds_ago": int(last_modified),
            "tail": content_tail
        }

    @schema_strict_validator
    def execute(self, **kwargs) -> str:
        status_report = {}
        
        # 0. Prune Dead Agents from Registry (New Feature)
        pruned_agents = self._prune_registry()
        if pruned_agents:
            status_report["pruned_agents"] = pruned_agents
        
        # 1. Process Status (Enhanced)
        # Now we can just read the verified registry directly instead of scanning logs?
        # Or keep scanning logs as backup?
        # Let's use the registry since it's now our source of truth.
        # But for backward compatibility/robustness, let's mix both or just rely on registry.
        # Current _get_process_status scans logs. Let's redirect it to read registry?
        # No, let's keep _get_process_status scanning logs for "All visible logs" vs "Registry Active".
        # Actually, if we pruned the registry, the registry is the Active List.
        
        status_report["agents"] = self._get_process_status()
        
        # 2. Mission Plan (Central)
        status_report["central_plan"] = self._get_file_info("central_plan.md")
        
        # 3. Timeline
        status_report["timeline"] = self._get_file_info("primary_timeline.md")
        
        # 4. Admin Directives
        status_report["admin_directives"] = self._get_file_info("admin_directives.md")
        
        return json.dumps(status_report, indent=2, ensure_ascii=False)

    def _prune_registry(self) -> List[str]:
        """Checks PIDs in registry.json and marks dead agents as DEAD."""
        from src.utils.file_lock import file_lock
        import fcntl
        registry_path = os.path.join(self.blackboard_dir, "registry.json")
        if not os.path.exists(registry_path):
            return []
            
        pruned = []
        try:
            with file_lock(registry_path, 'r+', fcntl.LOCK_EX, timeout=10) as fd:
                if fd is None:
                    return []
                    
                content = fd.read()
                try:
                    registry = json.loads(content) if content else {}
                except json.JSONDecodeError:
                    return []
                
                modified = False
                for name, info in registry.items():
                    # Only check agents that are supposedly running/starting
                    if info.get("status") in ["RUNNING", "STARTING", "IDLE"]:
                        pid = info.get("pid")
                        if not (pid and self._check_pid_running(pid)):
                            info["status"] = "DEAD"
                            info["exit_time"] = time.time()
                            pruned.append(f"{name} (PID: {pid})")
                            modified = True
                
                # Save if changes made
                if modified:
                    fd.seek(0)
                    json.dump(registry, fd, indent=2, ensure_ascii=False)
                    fd.truncate()
                    
        except Exception as e:
            print(f"[SwarmStatusTool] Error pruning registry: {e}")
            return []
            
        return pruned
