import sys
import os

# Get Sandbox Root from environment
SANDBOX_ROOT = os.environ.get("SANDBOX_ROOT")
if SANDBOX_ROOT:
    SANDBOX_ROOT = os.path.abspath(SANDBOX_ROOT)

# Optional confirmation callback — set by TAP agent process or TUI.
# When set, _prompt_user uses this instead of /dev/tty or input().
_confirmation_callback = None


def set_confirmation_callback(callback):
    """
    Set a callback for audit guard confirmations.
    Signature: callback(message: str) -> bool
    Called by TAP AgentProcess to inject stdio-based confirmation.
    """
    global _confirmation_callback
    _confirmation_callback = callback


def _prompt_user(action, path):
    """
    Prompt user for confirmation.
    Priority:
    1. _confirmation_callback (TAP stdio or TUI dialog)
    2. /dev/tty (Unix interactive terminal, non-TAP mode)
    3. stderr + input() fallback
    """
    msg = f"\n\n⚠️  [PYTHON SECURITY ALERT] Script attempting to {action} OUTSIDE sandbox!\n   Target: {path}\n   Sandbox: {SANDBOX_ROOT}\n   >>> Allow this operation? [y/N]: "

    # 1. Use callback if available (TAP mode or TUI mode)
    if _confirmation_callback is not None:
        try:
            return _confirmation_callback(
                f"⚠️ [PYTHON SECURITY ALERT] Script attempting to {action} OUTSIDE sandbox!\n"
                f"Target: {path}\nSandbox: {SANDBOX_ROOT}\nAllow this operation?"
            )
        except Exception:
            return False

    # 2. Try /dev/tty for interactive terminal (Unix only, non-TAP mode)
    if sys.platform != "win32":
        try:
            with open("/dev/tty", "r+") as tty:
                tty.write(msg)
                response = tty.readline().strip().lower()
                if response == 'y':
                    tty.write("   [Allowed by user]\n")
                    return True
                else:
                    tty.write("   [Denied by user]\n")
                    return False
        except Exception:
            pass

    # 3. Fallback to stderr + input
    print(msg, end="", file=sys.stderr)
    try:
        response = input().strip().lower()
    except EOFError:
        response = 'n'

    if response == 'y':
        print("   [Allowed by user]", file=sys.stderr)
        return True
    return False

def audit_hook(event, args):
    if not SANDBOX_ROOT:
        return

    # 1. Monitor File Opens (Write Mode)
    if event == "open":
        path, mode, flags = args
        if isinstance(path, int): return # Ignore file descriptors
        
        # Check for write modes
        if any(m in mode for m in ['w', 'a', 'x', '+']):
            try:
                abs_path = os.path.abspath(path)
                # Ignore special device files
                if abs_path.startswith("/dev/"): return
                
                if not abs_path.startswith(SANDBOX_ROOT):
                    if not _prompt_user("WRITE to", abs_path):
                        raise PermissionError(f"Sandbox violation: Write to {abs_path} denied by user.")
            except Exception as e:
                if isinstance(e, PermissionError): raise
                # Ignore path resolution errors
                pass

    # 2. Monitor File Deletions
    elif event in ["os.remove", "os.unlink", "os.rmdir", "shutil.rmtree"]:
        path = args[0]
        try:
            abs_path = os.path.abspath(path)
            if not abs_path.startswith(SANDBOX_ROOT):
                if not _prompt_user("DELETE", abs_path):
                    raise PermissionError(f"Sandbox violation: Delete {abs_path} denied by user.")
        except Exception as e:
            if isinstance(e, PermissionError): raise
            pass
            
    # 3. Monitor File Moves/Renames
    elif event == "os.rename":
        src, dst = args[0], args[1]
        try:
            # Check destination only
            abs_dst = os.path.abspath(dst)
            if not abs_dst.startswith(SANDBOX_ROOT):
                if not _prompt_user("MOVE/RENAME to", abs_dst):
                    raise PermissionError(f"Sandbox violation: Move to {abs_dst} denied by user.")
        except Exception as e:
            if isinstance(e, PermissionError): raise
            pass

# Register the hook
if SANDBOX_ROOT:
    sys.addaudithook(audit_hook)
