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
    # Only create directories for write modes
    if any(m in mode for m in ['w', 'a']):
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

    # For 'r+' mode, raise FileNotFoundError if file doesn't exist
    if not os.path.exists(full_path) and 'r' in mode and '+' in mode:
        raise FileNotFoundError(f"File does not exist: {full_path}")

    try:
        fd = open(full_path, mode, encoding='utf-8')
    except FileNotFoundError:
         # Fallback for read modes if file doesn't exist
         yield None
         return

    use_signal = False
    old_handler = None
    start = time.monotonic()

    def timeout_handler(signum, frame):
        raise LockTimeoutError(f"Lock timeout ({timeout}s): {full_path}")

    if threading.current_thread() is threading.main_thread():
        try:
            old_handler = signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(timeout)
            use_signal = True
        except (ValueError, OSError):
            # signal.signal() fails in sub-interpreters or Textual worker threads
            # even when threading reports "main thread". Fall back to polling.
            use_signal = False
    
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
