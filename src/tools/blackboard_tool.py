import os
import json
import yaml
import fcntl
import signal
import hashlib
import time
import threading
import uuid
from typing import Dict, Any, List, Optional, Tuple
from contextlib import contextmanager
from backend.tools.base import BaseTool
from src.utils.file_lock import file_lock, LockTimeoutError
from backend.llm.decorators import schema_strict_validator
from src.core.protocol import parse_frontmatter

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class BlackboardTool(BaseTool):
    """
    Enhanced Blackboard Tool with Semantic Index Directory Support.

    Structure:
    - .blackboard/global_indices/: Index files (Markdown + YAML Frontmatter)
    - .blackboard/resources/: Raw content files
    - .blackboard/registry.json: Agent registry
    """

    # Valid status transitions for tasks
    VALID_STATUS_TRANSITIONS = {
        "PENDING": {"IN_PROGRESS"},
        "IN_PROGRESS": {"DONE", "PENDING"},  # PENDING allows un-claiming
        "BLOCKED": {"PENDING"},               # Only DependencyGuard or Architect can unblock
        "DONE": set(),                         # DONE is terminal (Architect can override)
    }

    def __init__(self, blackboard_dir: str = ".blackboard", lock_timeout: int = 30):
        super().__init__()
        self.blackboard_dir = blackboard_dir
        self.lock_timeout = lock_timeout
        self.indices_dir = os.path.join(blackboard_dir, "global_indices")
        self.resources_dir = os.path.join(blackboard_dir, "resources")
        self._agent_name = None
        self._is_architect = False

        os.makedirs(blackboard_dir, exist_ok=True)
        os.makedirs(self.indices_dir, exist_ok=True)
        os.makedirs(self.resources_dir, exist_ok=True)

    def configure(self, context: Dict[str, Any]):
        """Inject agent identity for access control."""
        self._agent_name = context.get("agent_name")
        self._is_architect = context.get("is_architect", False)
        if not self._is_architect and self._agent_name:
            self._is_architect = "architect" in self._agent_name.lower()

    @property
    def name(self) -> str:
        return "blackboard"
    
    @property
    def description(self) -> str:
        return """The Primary Collaboration Interface for the Swarm.
        
    **Directory Semantics**:
    - `global_indices/`: **Coordination Layer**. Shared state, plans, and coordination signals. (Metadata)
      - **Tool Usage**: MUST use `blackboard` tool for all operations here.
    - `resources/`: **Working Directory (Storage Layer)**. Raw artifacts, code files, data, and reports. (Data)
      - **Tool Usage**: Use `bash`, `write_file`, or `read_file` for direct file manipulation in this directory. 
      - Use `{{blackboard}}/resources` as the base path.
    - **Protocol**: "Indices point to Resources". Metadata lives in indices; heavy data lives in resources.

Operations:
1. `list_indices()`: Discover available index files.
2. `read_index(filename)`: Read an index (e.g. 'central_plan.md'). Returns content and `checksum`.
3. `update_task(filename, task_id, updates, expected_checksum)`: Atomic task update. **(CAS protected, mandatory checksum)**.
4. `append_to_index(filename, content)`: Append a log entry to a timeline file. **(No CAS required, append-only)**.
5. `update_index(filename, content, expected_checksum)`: Full-file update. **(CAS protected, mandatory checksum)**.
6. `create_index(filename, content)`: Create a new global communication channel (index file).
   - **MANDATORY**: `content` MUST start with a YAML frontmatter containing:
     - `name`: "The title of the index file."
     - `description`: "A description of the file's purpose."
     - `usage_policy`: "Usage protocol (how to interact, format, etc.)."
   - **SECURITY TIP**: Always wrap YAML values in double quotes `""` to avoid parsing errors with special characters like `:`, `[`, `]`.
   - **Example**:
     ```
     ---
     name: "My_Channel"
     description: "Discussing the progress of task X."
     usage_policy: "Architect creates tasks; executors update status."
     ---
     # Body starts here...
     ```
7. `list_templates()`: List all available `.md` templates.
8. `read_template(filename)`: Read the content of a specific template file.
"""
    
    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["list_indices", "read_index", "update_index", "append_to_index", "update_task", "create_index", "list_templates", "read_template"],
                    "description": "Operation name"
                },
                "filename": {
                    "type": "string",
                    "description": "Target filename for index or template operations."
                },
                "task_id": {
                    "type": "integer",
                    "description": "ID of the task to update (for update_task)"
                },
                "updates": {
                    "type": "object",
                    "description": "Dictionary of fields to update (for update_task)"
                },
                "content": {
                    "type": "string",
                    "description": "Content to write or append"
                },
                "expected_checksum": {
                    "type": "string",
                    "description": "Expected SHA256 checksum for CAS updates (mandatory for update_index/update_task)"
                }
            },
            "required": ["operation"]
        }

    # Internal _file_lock removed, using src.utils.file_lock instead

    def _validate_central_plan(self, content: str) -> Optional[str]:
        """
        Validate the central_plan.md content if it is a central plan.
        Returns an error message if invalid, else None.
        """
        _, body = parse_frontmatter(content)
        
        # 1. Extract JSON block
        json_start = body.find("```json")
        if json_start == -1: return "Invalid central_plan: No JSON block found."
        json_end = body.rfind("```")
        if json_end == -1 or json_end <= json_start: return "Invalid central_plan: Malformed JSON block."
        
        json_str = body[json_start+7:json_end].strip()
        try:
            plan = json.loads(json_str)
        except json.JSONDecodeError as e:
            return f"Invalid central_plan: JSON Decode Error: {str(e)}"
        
        tasks = plan.get("tasks", [])
        if not isinstance(tasks, list):
            return "Invalid central_plan: 'tasks' must be a list."
            
        task_ids = {t.get("id") for t in tasks if isinstance(t, dict) and "id" in t}
        
        # 2. Check dependency existence and self-dependency
        for task in tasks:
            if not isinstance(task, dict): continue
            tid = task.get("id")
            deps = task.get("dependencies", [])
            if not isinstance(deps, list):
                return f"Invalid central_plan: Task {tid} 'dependencies' must be a list."
                
            for dep_id in deps:
                if dep_id not in task_ids:
                    return f"Invalid central_plan: Task {tid} depends on non-existent task {dep_id}."
                if dep_id == tid:
                    return f"Invalid central_plan: Task {tid} depends on itself (ID: {tid})."

        # 3. Check for circular dependencies
        def has_cycle(curr_id, visited, stack):
            visited.add(curr_id)
            stack.add(curr_id)
            
            task_obj = next((t for t in tasks if isinstance(t, dict) and t.get("id") == curr_id), None)
            if task_obj:
                deps = task_obj.get("dependencies", [])
                for dep_id in deps:
                    if dep_id not in visited:
                        if has_cycle(dep_id, visited, stack):
                            return True
                    elif dep_id in stack:
                        return True
            
            stack.remove(curr_id)
            return False

        visited = set()
        for task in tasks:
            if not isinstance(task, dict): continue
            tid = task.get("id")
            if tid not in visited:
                if has_cycle(tid, visited, set()):
                    return f"Invalid central_plan: Circular dependency detected involving task {tid}."

        # 4. Check status correctness: tasks with unfulfilled deps should not be PENDING
        for task in tasks:
            if not isinstance(task, dict): continue
            tid = task.get("id")
            status = task.get("status")
            deps = task.get("dependencies", [])
            
            if deps:
                unfulfilled_deps = []
                for dep_id in deps:
                    dep_task = next((t for t in tasks if isinstance(t, dict) and t.get("id") == dep_id), None)
                    if dep_task and dep_task.get("status") != "DONE":
                        unfulfilled_deps.append(dep_id)
                
                if unfulfilled_deps and status == "PENDING":
                    return f"Invalid central_plan: Task {tid} is PENDING but has unfulfilled dependencies: {unfulfilled_deps}. Status should be BLOCKED."

        return None

    def _sanitize_index_name(self, name: str) -> str:
        """Strip 'global_indices/' prefix if agent included it by mistake"""
        if name.startswith("global_indices/"):
            return name.replace("global_indices/", "", 1)
        elif name.startswith("/global_indices/"):
            return name.replace("/global_indices/", "", 1)
        return name

    def _list_indices(self) -> str:
        indices = []
        if not os.path.exists(self.indices_dir):
            return "No indices found."

        for fname in os.listdir(self.indices_dir):
            if fname.endswith(".md"):
                fpath = os.path.join(self.indices_dir, fname)
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        # Read enough to cover frontmatter. 8KB should be plenty for metadata.
                        content = f.read(8192)
                        meta, _ = parse_frontmatter(content)
                        # Default values
                        item = {}
                        # Update with all meta fields
                        if isinstance(meta, dict):
                            item.update(meta)
                        # Ensure filename is present and correct
                        item["filename"] = fname
                        indices.append(item)
                except Exception as e:
                    indices.append({"filename": fname, "error": str(e)})
        
        return json.dumps(indices, indent=2, ensure_ascii=False)

    def _read_index(self, filename: str) -> str:
        filename = self._sanitize_index_name(filename)
        fpath = os.path.join(self.indices_dir, filename)
        if not os.path.exists(fpath):
            return f"Error: Index '{filename}' not found."
            
        with file_lock(fpath, 'r', fcntl.LOCK_SH, timeout=self.lock_timeout) as fd:
            if fd is None: return f"Error: Could not open {filename}"
            content = fd.read()

        meta, body = parse_frontmatter(content)
        
        return json.dumps({
            "metadata": meta,
            "content": body,
            "checksum": hashlib.sha256(content.encode('utf-8')).hexdigest()
        }, indent=2, ensure_ascii=False)

    def _append_to_index(self, filename: str, content: str) -> str:
        filename = self._sanitize_index_name(filename)
        fpath = os.path.join(self.indices_dir, filename)
        
        # Ensure newline at start if needed
        if not content.startswith("\n"):
            content = "\n" + content
            
        with file_lock(fpath, 'a', fcntl.LOCK_EX, timeout=self.lock_timeout) as fd:
            fd.write(content)
            
        return "Success: Appended to index."

    def _update_index(self, filename: str, content: str, expected_checksum: str) -> str:
        """
        Update index content with CAS property.
        """
        filename = self._sanitize_index_name(filename)
        fpath = os.path.join(self.indices_dir, filename)
        
        if not os.path.exists(fpath):
            return f"Error: Index '{filename}' not found."
            
        if not expected_checksum:
            return "Error: expected_checksum is required for update_index."

        with file_lock(fpath, 'r+', fcntl.LOCK_EX, timeout=self.lock_timeout) as fd:
            if fd is None: return f"Error: Could not open {filename}"
            
            # Read current content
            current_content = fd.read()
            current_checksum = hashlib.sha256(current_content.encode('utf-8')).hexdigest()
            
            # CAS Check
            if current_checksum != expected_checksum:
                return f"Error: CAS Failed. Content has changed. Current checksum: {current_checksum}"
            
            # Verify YAML frontmatter integrity in new content
            if not content.startswith("---"):
                return "Error: Metadata Missing. content MUST start with '---' followed by YAML frontmatter."

            try:
                # Validate the new content has a parseable frontmatter
                test_meta, _ = parse_frontmatter(content)
                if not test_meta:
                    return "Error: Failed to parse YAML frontmatter in the provided content."
            except Exception as e:
                return f"Error: YAML validation failed: {str(e)}"

            # Additional Validation for central_plan.md
            if filename == "central_plan.md" or filename.endswith("/central_plan.md"):
                val_error = self._validate_central_plan(content)
                if val_error:
                    return f"Error: {val_error}"

            # Update
            fd.seek(0)
            fd.write(content)
            fd.truncate()
            
        return "Success: Index updated."

    def _validate_status_transition(self, current_status: str, new_status: str, task: Dict, all_tasks: List[Dict]) -> Optional[str]:
        """Validate that a status transition is legal. Returns error message if invalid, None if OK."""
        if current_status == new_status:
            return None
        if self._is_architect:
            return None  # Architect can force any transition
        allowed = self.VALID_STATUS_TRANSITIONS.get(current_status, set())
        if new_status not in allowed:
            return (
                f"Error: Illegal status transition '{current_status}' -> '{new_status}' for Task #{task.get('id')}. "
                f"Allowed transitions from '{current_status}': {sorted(allowed) if allowed else 'none (terminal state)'}. "
                f"Only the Architect can override this restriction."
            )
        if new_status == "IN_PROGRESS":
            deps = task.get("dependencies", [])
            for dep_id in deps:
                dep_task = next((t for t in all_tasks if t.get("id") == dep_id), None)
                if dep_task and dep_task.get("status") != "DONE":
                    return (
                        f"Error: Cannot claim Task #{task.get('id')} (set IN_PROGRESS) because "
                        f"dependency Task #{dep_id} is '{dep_task.get('status')}', not DONE."
                    )
        return None

    def _validate_assignee_access(self, task: Dict, updates: Dict) -> Optional[str]:
        """Validate that a non-Architect agent can only update tasks assigned to itself."""
        if self._is_architect:
            return None
        if not self._agent_name:
            return None
        current_assignees = task.get("assignees", [])
        new_assignees = updates.get("assignees")
        if new_assignees is not None and self._agent_name in new_assignees:
            return None  # Agent is claiming this task
        if not current_assignees:
            return None  # Unassigned task
        if self._agent_name in current_assignees:
            return None  # Agent owns this task
        return (
            f"Error: Agent '{self._agent_name}' cannot update Task #{task.get('id')} "
            f"which is assigned to {current_assignees}. "
            f"Only the assigned agent or the Architect can modify this task."
        )

    def _update_task(self, filename: str, task_id: int, updates: Dict[str, Any], expected_checksum: str) -> str:
        """Partial update of a task with CAS. Enforces status transitions and assignee access."""
        filename = self._sanitize_index_name(filename)
        fpath = os.path.join(self.indices_dir, filename)

        if not os.path.exists(fpath):
            return f"Error: Index '{filename}' not found."
        if not expected_checksum:
            return "Error: expected_checksum is required for update_task."

        with file_lock(fpath, 'r+', fcntl.LOCK_EX, timeout=self.lock_timeout) as fd:
            if fd is None: return f"Error: Could not open {filename}"

            content = fd.read()
            current_checksum = hashlib.sha256(content.encode('utf-8')).hexdigest()
            if current_checksum != expected_checksum:
                return f"Error: CAS Failed. Plan has changed. Current checksum: {current_checksum}"

            meta, body = parse_frontmatter(content)
            try:
                json_start = body.find("```json")
                if json_start == -1: return "Error: No JSON block found in plan."
                json_end = body.rfind("```")
                if json_end == -1 or json_end <= json_start: return "Error: Malformed JSON block."

                json_str = body[json_start+7:json_end].strip()
                plan = json.loads(json_str)
                tasks = plan.get("tasks", [])
                target_task = next((t for t in tasks if t.get("id") == task_id), None)
                if not target_task:
                    return f"Error: Task ID {task_id} not found."

                # Validate assignee access
                access_err = self._validate_assignee_access(target_task, updates)
                if access_err:
                    return access_err

                # Validate status transition
                if "status" in updates:
                    transition_err = self._validate_status_transition(
                        target_task.get("status", "PENDING"), updates["status"], target_task, tasks
                    )
                    if transition_err:
                        return transition_err

                for k, v in updates.items():
                    target_task[k] = v

                new_json_str = json.dumps(plan, indent=2, ensure_ascii=False)
                new_body = body[:json_start+7] + "\n" + new_json_str + "\n" + body[json_end:]
                if meta:
                    new_content = "---\n" + yaml.dump(meta, sort_keys=False, width=1000) + "---\n" + new_body
                else:
                    new_content = new_body

                try:
                    verify_meta, _ = parse_frontmatter(new_content)
                    if not verify_meta and meta:
                        return "Error: Reconstructed content has invalid YAML frontmatter."
                except Exception as ve:
                    return f"Error: YAML Verification failed before write: {ve}"

                fd.seek(0)
                fd.write(new_content)
                fd.truncate()
                return "Success: Task updated."

            except json.JSONDecodeError:
                return "Error: Failed to parse Central Plan JSON."
            except Exception as e:
                return f"Error updating task: {e}"

    def _create_index(self, filename: str, content: str) -> str:
        filename = self._sanitize_index_name(filename)
        fpath = os.path.join(self.indices_dir, filename)
        
        if os.path.exists(fpath):
            return f"Index '{filename}' already exists."

        # 1. Parse Frontmatter and Validate Requirements
        meta, _ = parse_frontmatter(content)
        if not content.startswith("---"):
             return "Error: Metadata Missing. content MUST start with '---' followed by YAML frontmatter."
        
        required_fields = ["name", "description", "usage_policy"]
        missing = [f for f in required_fields if f not in meta]
        if missing:
            return f"Error: YAML Metadata incomplete. Missing fields: {', '.join(missing)}. Please refer to BlackboardTool description for format."
        
        # Additional Validation for central_plan.md
        if filename == "central_plan.md" or filename.endswith("/central_plan.md"):
            val_error = self._validate_central_plan(content)
            if val_error:
                return f"Error: {val_error}"

        # 2. Proceed with creation
        os.makedirs(os.path.dirname(fpath), exist_ok=True)

        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(content)
            
        return f"Success: Created index '{filename}'"

    # def _create_resource(self, filename: str, content: str, overwrite: bool) -> str:
    #     # Prevent traversal
    #     if ".." in filename or filename.startswith("/"):
    #         return "Error: Invalid filename."
            
    #     # Robustness: Strip dir if the agent mistakenly included it
    #     filename = filename.split('/')[-1]

    #     fpath = os.path.join(self.resources_dir, filename)
        
    #     if os.path.exists(fpath) and not overwrite:
    #         return f"Error: File '{filename}' already exists. Set overwrite=True to replace."
            
    #     os.makedirs(os.path.dirname(fpath), exist_ok=True)
    #     with open(fpath, 'w', encoding='utf-8') as f:
    #         f.write(content)
            
    #     return f"Success: Resource '{filename}' created at {fpath}"

    def _list_resources(self) -> str:
        """List all files in the resources directory."""
        if not os.path.exists(self.resources_dir):
            return "No resources directory found."
        
        resources = []
        for root, _, files in os.walk(self.resources_dir):
            for f in files:
                rel_path = os.path.relpath(os.path.join(root, f), self.resources_dir)
                resources.append(rel_path)
        
        return json.dumps(resources, indent=2, ensure_ascii=False)

    def _list_templates(self) -> str:
        """List all available templates."""
        # Use project root to find templates
        templates_dir = os.path.join(project_root, "blackboard_templates")
        if not os.path.exists(templates_dir):
            return "No templates directory found."
        templates = [f for f in os.listdir(templates_dir) if f.endswith(".md")]
        return json.dumps(templates, indent=2, ensure_ascii=False)

    def _read_template(self, filename: str) -> str:
        """Read a template by name."""
        if not filename:
            return "Error: Template filename is required."
        # Use project root to find templates
        templates_dir = os.path.join(project_root, "blackboard_templates")
        fpath = os.path.abspath(os.path.join(templates_dir, filename))
        
        # Verify it is still inside templates_dir
        if not fpath.startswith(templates_dir):
            return "Error: Access denied (Invalid template path)."
        
        if not os.path.exists(fpath):
            return f"Error: Template '{filename}' not found."
            
        with open(fpath, 'r', encoding='utf-8') as f:
            return f.read()

    # def _read_resource(self, filename: str) -> str:
    #     # Security: robust filename handling
    #     safe_name = filename.split('/')[-1]
    #     abs_path = os.path.abspath(os.path.join(self.resources_dir, safe_name))
    #     if not abs_path.startswith(os.path.abspath(self.resources_dir)):
    #         return "Error: Access denied (Outside resources)."
    #     if os.path.exists(abs_path):
    #         with open(abs_path, 'r', encoding='utf-8') as f:
    #             return f.read()
    #     return f"Error: Resource '{filename}' not found."

    @schema_strict_validator
    def execute(self, operation: str, **kwargs) -> str:
        operation = operation.lower()
        if not operation:
            return "Error: Operation is required."
        
        filename = kwargs.get("filename")
        
        try:
            if operation == "list_indices" or "list_indices" in operation:
                return self._list_indices()
            
            elif operation == "read_index" or "read_index" in operation:
                if not filename: return "Error: filename is required for read_index."
                return self._read_index(filename)
            
            elif operation == "append_to_index" or "append_to_index" in operation:
                if not filename: return "Error: filename is required for append_to_index."
                return self._append_to_index(filename, kwargs.get("content"))
            
            elif operation == "update_index" or "update_index" in operation:
                if not filename: return "Error: filename is required for update_index."
                return self._update_index(filename, kwargs.get("content"), kwargs.get("expected_checksum"))
            
            elif operation == "update_task" or "update_task" in operation:
                # Default to central_plan.md if filename not provided
                fname = filename or "central_plan.md"
                return self._update_task(
                    fname,
                    kwargs.get("task_id"),
                    kwargs.get("updates"),
                    kwargs.get("expected_checksum"),
                )
            
            elif operation == "create_index" or "create_index" in operation:
                if not filename: return "Error: filename is required for create_index."
                return self._create_index(filename, kwargs.get("content"))
            
            # elif operation == "create_resource" or "create_resource" in operation:
            #     if not filename: return "Error: filename is required for create_resource."
            #     return self._create_resource(
            #         filename, 
            #         kwargs.get("content"), 
            #         kwargs.get("overwrite", False)
            #     )
            
            # elif operation == "read_resource" or "read_resource" in operation:
            #     if not filename: return "Error: filename is required for read_resource."
            #     return self._read_resource(filename)
            
            elif operation == "list_templates" or "list_templates" in operation:
                return self._list_templates()
            
            elif operation == "read_template" or "read_template" in operation:
                if not filename: return "Error: filename is required for read_template."
                return self._read_template(filename)
            
            elif operation == "list_resources" or "list_resources" in operation:
                return self._list_resources()

            else:
                return f"Error: Unknown operation {operation}"
        except Exception as e:
            return f"Error: {str(e)}"
