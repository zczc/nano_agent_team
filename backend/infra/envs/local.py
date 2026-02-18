import os
import subprocess
import shutil
import shlex
import sys
from typing import Optional, Dict, List, Callable
from backend.infra.environment import Environment, FileNotFoundError as EnvFileNotFoundError, PermissionError as EnvPermissionError, EnvironmentError as EnvError, CommandError

class LocalEnvironment(Environment):
    """
    Local environment implementation using subprocess and os.
    Includes security checks (audit guard and dangerous token detection).
    """
    def __init__(self, workspace_root: str, blackboard_dir: str, allowed_write_paths: Optional[List[str]] = None, confirmation_callback: Optional[Callable[[str], bool]] = None, non_interactive: bool = False, agent_name: str = "UnknownAgent"):
        """
        Args:
            workspace_root: The root directory for this environment (sandbox root).
            allowed_write_paths: Optional list of absolute paths where writing is allowed. 
                                 If None, defaults to sandbox_root (process-level sandbox).
            confirmation_callback: Optional callback for user confirmation. 
                                   Should accept a message string and return bool (True=Allow, False=Deny).
                                   If None, defaults to built-in input().
            non_interactive: If True, uses IPC RequestManager to ask for permission instead of blocking on stdin.
            agent_name: Name of the agent (used for IPC requests).
            blackboard_dir: Directory for IPC requests (used only if non_interactive=True).
        """
        self._workdir = os.path.abspath(workspace_root)
        self.sandbox_root = self._workdir
        self.blackboard_dir = os.path.abspath(blackboard_dir) if blackboard_dir else None
        self.allowed_write_paths = [os.path.abspath(p) for p in allowed_write_paths] if allowed_write_paths else None
        self.confirmation_callback = confirmation_callback
        self.non_interactive = non_interactive
        self.agent_name = agent_name
        
        if self.non_interactive:
            # Import here to avoid circular dependencies if any (though RequestManager is standalone)
            from src.core.ipc.request_manager import RequestManager
            self.request_manager = RequestManager(blackboard_dir)
        else:
            self.request_manager = None

    @property
    def workdir(self) -> str:
        return self._workdir

    def _request_confirmation(self, message: str) -> bool:
        """Helper to request confirmation via callback, IPC, or fallback to input.

        Priority:
        1. confirmation_callback (TUI dialog or TAP stdio callback)
        2. IPC RequestManager (non-interactive sub-agent mode)
        3. CLI input() fallback (only for direct CLI usage without TUI)
        """
        if self.confirmation_callback:
            return self.confirmation_callback(message)

        if self.non_interactive and self.request_manager:
            # Create IPC Request
            print(f"[{self.agent_name}] Permission required. Sending request to Watchdog...")
            req_id = self.request_manager.create_request(
                agent_name=self.agent_name,
                req_type="permission_request",
                content=message,
                reason="High-risk operation detected by LocalEnvironment"
            )

            # Wait for response
            print(f"[{self.agent_name}] Waiting for approval (Req ID: {req_id})...")
            status = self.request_manager.wait_for_response(req_id, timeout=120)

            if status == "APPROVED":
                print(f"[{self.agent_name}] Request APPROVED.")
                return True
            elif status == "TIMEOUT":
                 print(f"[{self.agent_name}] Request Timed Out. Automatically DENIED.")
                 return False
            else:
                print(f"[{self.agent_name}] Request DENIED (Status: {status}).")
                return False

        # Fallback to CLI input (only when no callback and not non-interactive)
        # In TAP mode, confirmation_callback is always set, so this branch
        # is only reached in direct CLI usage (e.g. main.py without TUI).
        try:
            user_input = input(f"{message} [y/N]: ").strip().lower()
            return user_input == 'y'
        except EOFError:
            # stdin is closed (e.g. piped process) — deny by default
            return False

    def run_command(self, command: str, cwd: Optional[str] = None, env_vars: Optional[Dict[str, str]] = None, timeout: int = 60, background: bool = False) -> str:
        target_cwd = cwd or self.workdir
        
        if not os.path.exists(target_cwd):
            return f"Error: The provided cwd '{target_cwd}' does not exist."

        # Security Check
        if not self._check_safety(command, target_cwd):
            return "Error: Command execution denied by user (Security Policy)."

        # Prepare Environment
        env = os.environ.copy()
        if env_vars:
            env.update(env_vars)
            
        # Inject Audit Hook for Python
        self._inject_audit_hook(command, env)

        try:
            if background:
                # Background Execution
                # Use start_new_session to detach properly
                # Redirect output to DEVNULL or log file? 
                # For simplicity, detach and return PID.
                proc = subprocess.Popen(
                    command,
                    shell=True,
                    cwd=target_cwd,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True
                )
                return f"Background process started with PID: {proc.pid}"
            
            # Synchronous Execution
            result = subprocess.run(
                command,
                shell=True,
                cwd=target_cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            
            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR:\n{result.stderr}"
            
            if result.returncode != 0:
                output = f"Command failed with exit code {result.returncode}\n{output}"
                
            return output if output else "Command executed successfully with no output."
            
        except subprocess.TimeoutExpired:
            return f"Error: Command timed out after {timeout} seconds."
        except Exception as e:
            return f"Error executing command: {str(e)}"

    def read_file(self, path: str) -> str:
        if not os.path.exists(path):
            raise EnvFileNotFoundError(f"File '{path}' not found.")
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return f.read()
        except UnicodeDecodeError:
            raise EnvFileNotFoundError(f"File '{path}' is binary or not valid UTF-8.")
        except PermissionError as e:
            raise EnvPermissionError(f"Permission denied reading '{path}': {e}")
        except Exception as e:
            from backend.infra.environment import EnvironmentError
            raise EnvironmentError(f"Error reading file '{path}': {e}")

    def write_file(self, path: str, content: str) -> str:
        """
        Write content to a file with security checks.
        
        Security Policy:
        1. Access outside the sandbox triggers a user confirmation prompt.
        2. If 'allowed_write_paths' is configured (e.g., for restricted subagents), 
           writes outside these paths are automatically denied without prompt.
        """
        # Security Check for Write
        abs_path = os.path.abspath(path)
        if not abs_path.startswith(self.sandbox_root):
            # Auto-approve blackboard paths
            if self.blackboard_dir and abs_path.startswith(self.blackboard_dir):
                pass  # Blackboard path, auto-approved
            else:
                msg = (
                    f"### ⚠️ [SECURITY ALERT] Write Outside Sandbox\n\n"
                    f"**Target**: `{abs_path}`\n\n"
                    f"**Sandbox**: `{self.sandbox_root}`\n\n"
                    f"**Allow this write operation?**"
                )
                if not self._request_confirmation(msg):
                    raise EnvPermissionError(f"Write operation to '{path}' denied by user.")
        
        # Explicit Path Restriction (e.g., for Subagents restricted to Blackboard)
        if self.allowed_write_paths:
            is_allowed = False
            for allowed in self.allowed_write_paths:
                if abs_path.startswith(allowed):
                    is_allowed = True
                    break
            if not is_allowed:
                 raise EnvPermissionError(f"Write denied. This agent is restricted to writing only in: {self.allowed_write_paths}")

        try:
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return f"Successfully wrote to file '{path}'."
        except Exception as e:
            raise EnvError(f"Error writing file '{path}': {e}")

    def file_exists(self, path: str) -> bool:
        return os.path.exists(path)

    def upload_file(self, local_path: str, remote_path: str) -> bool:
        # For LocalEnv, upload is just copy
        # We assume local_path is from 'outside' and remote_path is 'inside'
        try:
            shutil.copy2(local_path, remote_path)
            return True
        except Exception as e:
            print(f"Error copying file: {e}")
            return False

    def download_file(self, remote_path: str, local_path: str) -> bool:
        # For LocalEnv, download is just copy
        try:
            shutil.copy2(remote_path, local_path)
            return True
        except Exception as e:
            print(f"Error copying file: {e}")
            return False

    def _check_safety(self, command: str, cwd: str) -> bool:
        """
        Perform security checks on the command to be executed.
        """
        is_safe = True
        reason = ""
        
        dangerous_tokens = ["rm ", "mv ", "cp ", "chmod ", "chown ", "dd ", ">", ">>", "sudo ", "su ", "curl ", "wget ", "git "]
        has_dangerous_token = any(token in command for token in dangerous_tokens)
        
        try:
            parts = shlex.split(command)
        except Exception:
            parts = command.split()
            
        sandbox_abs = self.sandbox_root
        
        if has_dangerous_token:
            for part in parts:
                if part.startswith("/"):
                    part_abs = os.path.abspath(part)
                    if not part_abs.startswith(sandbox_abs):
                        # Auto-approve blackboard paths
                        if self.blackboard_dir and part_abs.startswith(self.blackboard_dir):
                            continue
                        is_safe = False
                        reason = f"Dangerous command targets outside sandbox: {part}"
                        break
            
            if ".." in command:
                is_safe = False
                reason = "Dangerous command contains path traversal ('..')"

        safe_read_commands = ["ls", "cat", "grep", "find", "pwd", "whoami", "tail", "head", "wc", "file", "du", "echo"]
        
        if is_safe and parts and parts[0] in safe_read_commands:
            pass # Allow safe read commands
        elif is_safe and not has_dangerous_token:
            pass # Allow unknown commands without dangerous tokens
        elif not is_safe:
             msg = (
                 f"### ⚠️ [SECURITY ALERT] Potentially Unsafe Command\n\n"
                 f"**Command**: `{command}`\n\n"
                 f"**Reason**: *{reason}*\n\n"
                 f"**CWD**: `{cwd}`\n\n"
                 f"**Allow execution?**"
             )
             if not self._request_confirmation(msg):
                 return False
             
        return True

    def _inject_audit_hook(self, command: str, env: Dict[str, str]):
        """Inject python audit hook if command is running python"""
        cmd_parts = command.strip().split()
        if not cmd_parts: return

        prog = cmd_parts[0]
        if prog.startswith("python") or prog.endswith("/python") or prog.endswith("/python3"):
             env["SANDBOX_ROOT"] = self.sandbox_root
             # Path to audit_guard.py relative to this file
             # backend/infra/envs/local.py -> backend/utils/audit_guard.py
             guard_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../utils/audit_guard.py"))
             
             if os.path.exists(guard_path):
                 env["PYTHONSTARTUP"] = guard_path
