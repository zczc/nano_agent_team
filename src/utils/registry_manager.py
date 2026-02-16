"""
Centralized manager for registry.json operations.
All registry reads/writes should go through this class to ensure
consistent file locking and data handling.
"""

import os
import json
import time
import fcntl
from typing import Optional, Dict, List, Any
from src.utils.file_lock import file_lock


class RegistryManager:
    """Thread-safe, file-lock-protected manager for the agent registry."""

    def __init__(self, blackboard_dir: str):
        self.blackboard_dir = blackboard_dir
        self.registry_path = os.path.join(blackboard_dir, "registry.json")
        self._ensure_registry_exists()

    def _ensure_registry_exists(self):
        """Ensure registry.json exists, create with empty dict if not."""
        if not os.path.exists(self.registry_path):
            os.makedirs(self.blackboard_dir, exist_ok=True)
            with open(self.registry_path, 'w', encoding='utf-8') as f:
                json.dump({}, f)

    def read(self, timeout: int = 10) -> dict:
        """Read the full registry with a shared lock."""
        if not os.path.exists(self.registry_path):
            return {}

        try:
            with file_lock(self.registry_path, 'r', fcntl.LOCK_SH, timeout=timeout) as fd:
                if not fd:
                    return {}
                content = fd.read()
                if not content:
                    return {}
                return json.loads(content)
        except (json.JSONDecodeError, Exception):
            return {}

    def _read_and_write(self, mutator, timeout: int = 10) -> bool:
        """
        Internal helper: read registry, apply mutator function, write back.
        mutator(registry) -> should modify registry in place.
        Returns True on success.
        """
        try:
            with file_lock(self.registry_path, 'r+', fcntl.LOCK_EX, timeout=timeout) as fd:
                if fd is None:
                    return False

                content = fd.read()
                try:
                    registry = json.loads(content) if content else {}
                except json.JSONDecodeError:
                    registry = {}

                mutator(registry)

                fd.seek(0)
                json.dump(registry, fd, indent=2, ensure_ascii=False)
                fd.truncate()
                return True
        except Exception:
            return False

    def register_agent(self, name: str, role: str, pid: int = None) -> bool:
        """Register an agent as RUNNING. Preserves spawn_time if set by spawn_tool."""
        def _mutate(registry):
            existing = registry.get(name, {})
            registry[name] = {
                "pid": pid or os.getpid(),
                "role": role,
                "status": "RUNNING",
                "start_time": time.time(),
                # Preserve spawn_time written by spawn_tool for grace period tracking
                "spawn_time": existing.get("spawn_time"),
            }
        return self._read_and_write(_mutate)

    def deregister_agent(self, name: str, reason: str = "Self-terminated or normal exit") -> bool:
        """Mark an agent as DEAD."""
        def _mutate(registry):
            if name in registry:
                registry[name]["status"] = "DEAD"
                registry[name]["exit_time"] = time.time()
                registry[name]["exit_reason"] = reason
        return self._read_and_write(_mutate)

    def update_agent(self, name: str, **fields) -> bool:
        """Update arbitrary fields on an agent entry."""
        def _mutate(registry):
            if name in registry:
                registry[name].update(fields)
        return self._read_and_write(_mutate)

    def get_agent(self, name: str) -> Optional[dict]:
        """Get a single agent's info."""
        registry = self.read()
        return registry.get(name)

    def list_agents(self, status: str = None) -> Dict[str, dict]:
        """List agents, optionally filtered by status."""
        registry = self.read()
        if status:
            return {k: v for k, v in registry.items() if v.get("status") == status}
        return registry

    def is_agent_active(self, name: str) -> bool:
        """Check if an agent is RUNNING or IDLE."""
        info = self.get_agent(name)
        if not info:
            return False
        return info.get("status") in ("RUNNING", "IDLE", "STARTING")

    def verify_and_sync_pids(self) -> Dict[str, dict]:
        """
        Verify all agent PIDs and mark dead ones.
        Returns the verified report_data for prompt injection.

        Rules:
        - STARTING agents get a grace period (30s) before being marked DEAD.
        - Already DEAD agents are not re-checked or re-timestamped.
        - Only RUNNING/IDLE agents are actively verified via os.kill().
        """
        report_data = {}
        now = time.time()
        STARTING_GRACE_PERIOD = 30  # seconds

        def _mutate(registry):
            nonlocal report_data
            for name, info in registry.items():
                current_status = info.get("status", "")

                # Skip agents already marked DEAD — don't overwrite exit_time
                if current_status == "DEAD":
                    info_copy = info.copy()
                    info_copy["verified_status"] = "DEAD"
                    report_data[name] = info_copy
                    continue

                # STARTING agents get a grace period before PID check
                if current_status == "STARTING":
                    spawn_time = info.get("spawn_time") or info.get("start_time") or 0
                    if now - spawn_time < STARTING_GRACE_PERIOD:
                        info_copy = info.copy()
                        info_copy["verified_status"] = "STARTING"
                        report_data[name] = info_copy
                        continue
                    # Grace period expired — fall through to PID check

                # PID liveness check for RUNNING / IDLE / expired STARTING
                pid = info.get("pid")
                is_alive = False
                if pid:
                    try:
                        os.kill(pid, 0)
                        is_alive = True
                    except OSError:
                        pass

                if not is_alive:
                    info["status"] = "DEAD"
                    if "exit_time" not in info:
                        info["exit_time"] = now
                    info.setdefault("exit_reason", "PID not found (verified by SwarmStateMiddleware)")

                info_copy = info.copy()
                info_copy["verified_status"] = "ALIVE" if is_alive else "DEAD"
                report_data[name] = info_copy

        self._read_and_write(_mutate)
        return report_data
