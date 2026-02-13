import os
import json
import time
import uuid
from typing import Optional, Dict, List
from src.utils.file_lock import file_lock
import fcntl

class RequestManager:
    """
    Manages inter-process communication for permission requests using file system.
    Handles creation, monitoring, and updating of request files in .blackboard/requests/
    """
    
    def __init__(self, blackboard_dir: str):
        self.blackboard_dir = os.path.abspath(blackboard_dir)
        self.requests_dir = os.path.join(self.blackboard_dir, "requests")
        os.makedirs(self.requests_dir, exist_ok=True)

    def create_request(self, agent_name: str, req_type: str, content: str, reason: str = "") -> str:
        """
        Creates a new request file.
        Returns: request_id (str)
        """
        req_id = str(uuid.uuid4())
        request_data = {
            "id": req_id,
            "agent_name": agent_name,
            "type": req_type,
            "content": content,
            "reason": reason,
            "status": "PENDING",
            "timestamp": time.time(),
            "response_time": None
        }
        
        file_path = os.path.join(self.requests_dir, f"{req_id}.json")
        
        # Write initial request
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(request_data, f, indent=2, ensure_ascii=False)
            
        return req_id

    def wait_for_response(self, req_id: str, timeout: int = 600, poll_interval: float = 1.0) -> str:
        """
        Blocks and polls for the request status to change from PENDING.
        Returns: Final status (APPROVED/DENIED) or TIMEOUT/ERROR
        """
        file_path = os.path.join(self.requests_dir, f"{req_id}.json")
        start_time = time.time()
        
        while (time.time() - start_time) < timeout:
            if not os.path.exists(file_path):
                return "ERROR_FILE_MISSING"
            
            try:
                # Read with shared lock to allow others to read, but we just need quick read
                # Actually, simple read is fine as updates are atomic-ish or standardized
                # But let's be safe and use lock if possible, though 'r' lock might block writer.
                # Ideally, we just read. The writer (Watchdog) will use write lock.
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                status = data.get("status", "PENDING")
                if status != "PENDING":
                    return status
                    
            except Exception:
                # File might be mid-write, ignore and retry
                pass
            
            time.sleep(poll_interval)
            
        return "TIMEOUT"

    def list_pending_requests(self) -> List[Dict]:
        """
        Lists all requests with status PENDING.
        """
        pending = []
        if not os.path.exists(self.requests_dir):
            return []
            
        for filename in os.listdir(self.requests_dir):
            if not filename.endswith(".json"):
                continue
                
            file_path = os.path.join(self.requests_dir, filename)
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if data.get("status") == "PENDING":
                        pending.append(data)
            except Exception:
                continue
                
        # Sort by timestamp
        pending.sort(key=lambda x: x.get("timestamp", 0))
        return pending

    def update_request_status(self, req_id: str, status: str) -> bool:
        """
        Updates the status of a request (APPROVED/DENIED).
        Uses file lock to ensure safety.
        """
        file_path = os.path.join(self.requests_dir, f"{req_id}.json")
        if not os.path.exists(file_path):
            return False
            
        try:
            # Use exclusive lock for update
            with file_lock(file_path, 'r+', fcntl.LOCK_EX, timeout=5) as fd:
                if fd is None:
                    return False
                    
                content = fd.read()
                data = json.loads(content)
                
                data["status"] = status
                data["response_time"] = time.time()
                
                fd.seek(0)
                json.dump(data, fd, indent=2, ensure_ascii=False)
                fd.truncate()
                return True
        except Exception:
            return False
