import os
import fcntl
import signal
import threading
import time
from contextlib import contextmanager

class LockTimeoutError(Exception):
    """Acquiring lock timed out"""
    pass

@contextmanager
def file_lock(full_path: str, mode: str = 'r+', lock_type = fcntl.LOCK_EX, timeout: int = 30):
    """
    File locking context manager with timeout.
    
    Args:
        full_path: Absolute path to the file.
        mode: File open mode (default 'r+').
        lock_type: fcntl.LOCK_EX (Exclusive) or fcntl.LOCK_SH (Shared).
        timeout: Timeout in seconds.
    
    Yields:
        File object (or None if file not found and mode is read-only).
    """
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    
    # Ensure file exists for 'r+' or 'w' modes if it doesn't logic is handled by caller usually,
    # but for 'r+' we need file to exist.
    if not os.path.exists(full_path) and 'r' in mode and '+' in mode:
         with open(full_path, 'w') as f:
            f.write('')

    try:
        fd = open(full_path, mode, encoding='utf-8')
    except FileNotFoundError:
         # Fallback for read modes if file doesn't exist
         yield None
         return

    use_signal = (threading.current_thread() is threading.main_thread())
    old_handler = None
    start = time.monotonic()
    
    def timeout_handler(signum, frame):
        raise LockTimeoutError(f"Lock timeout ({timeout}s): {full_path}")
    
    if use_signal:
        old_handler = signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(timeout)
    
    try:
        if use_signal:
            fcntl.flock(fd.fileno(), lock_type)
        else:
            while True:
                try:
                    fcntl.flock(fd.fileno(), lock_type | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() - start >= timeout:
                        raise LockTimeoutError(f"Lock timeout ({timeout}s): {full_path}")
                    time.sleep(0.05)
        if use_signal:
            signal.alarm(0)
        yield fd
    finally:
        if use_signal:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler or signal.SIG_DFL)
        try:
            # Unlock and close
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            fd.close()
        except Exception:
            pass
