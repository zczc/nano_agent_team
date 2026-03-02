
from typing import Dict, Any, Optional
from backend.tools.base import BaseTool
from backend.llm.decorators import schema_strict_validator

class FinishTool(BaseTool):
    """
    Tool for an Agent to signal that its task is complete.
    """
    def __init__(self, agent_name: str = None, agent_role: str = None, blackboard_dir: str = ".blackboard"):
        self.agent_name = agent_name
        self.agent_role = agent_role
        self.blackboard_dir = blackboard_dir
    @property
    def name(self) -> str:
        return "finish"
    
    @property
    def description(self) -> str:
        return """Call this function to signal that you have completed your task or objective. 
        Provide a comprehensive paragraph describing the reason and output. 
        If your work resulted in new or modified files, you MUST explicitly mention them and provide their absolute paths within this description.
        """
    
    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "A detailed paragraph summarizing the reason for finishing and the process."
                },
                "output": {
                    "type": "string",
                    "description": "A detailed paragraph summarizing the work and any produced artifacts/file paths."
                }
            },
            "required": ["output"]
        }

    @schema_strict_validator
    def execute(self, output: str, reason: str = None) -> str:
        # Guard: block finish if an evolution workspace worktree still exists.
        # This forces the Watchdog to call evolution_workspace() first.
        import os
        from backend.infra.config import Config
        workspace = os.path.join(Config.BLACKBOARD_ROOT, "resources", "workspace")
        if os.path.isfile(os.path.join(workspace, ".git")):
            return (
                "BLOCKED: Evolution workspace worktree still exists.\n\n"
                f"  {workspace}\n\n"
                "You MUST call the 'evolution_workspace' tool first:\n"
                "  - PASS: evolution_workspace(verdict='PASS', round_num=N, description='...', changed_files=[...])\n"
                "  - FAIL: evolution_workspace(verdict='FAIL', round_num=N)\n\n"
                "Do NOT call finish until the worktree is removed."
            )

        # Check for incomplete tasks based on agent role
        task_check_result = self._check_incomplete_tasks()
        if task_check_result:
            return task_check_result

        # In the AgentEngine loop, this will be detected to break the loop.
        reason_str = f"Reason: {reason}\n\n" if reason else ""
        return f"Agent Finished.\n\n{reason_str}===========================\n\nOutput: {output}"

    def _check_incomplete_tasks(self) -> Optional[str]:
        """Check for incomplete tasks before allowing finish."""
        import os
        import json
        import re

        central_plan_path = os.path.join(self.blackboard_dir, "global_indices", "central_plan.md")

        if not os.path.exists(central_plan_path):
            # No central plan, allow finish
            return None

        try:
            with open(central_plan_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # Extract JSON block from central_plan.md
            json_match = re.search(r'```json\s*(\{.*?\})\s*```', content, re.DOTALL)
            if not json_match:
                return None

            plan_json = json.loads(json_match.group(1))
            tasks = plan_json.get("tasks", [])

            if not tasks:
                return None

            # Check based on agent role
            if self.agent_role and "architect" in self.agent_role.lower():
                # Architect: check ALL tasks
                incomplete = [t for t in tasks if t.get("status") in ["PENDING", "IN_PROGRESS", "BLOCKED"]]

                if incomplete:
                    task_list = "\n".join([
                        f"  - Task #{t['id']}: {t.get('description', 'N/A')[:80]} [{t.get('status')}]"
                        for t in incomplete[:5]  # Show first 5
                    ])
                    more = f"\n  ... and {len(incomplete) - 5} more" if len(incomplete) > 5 else ""

                    return (
                        f"BLOCKED: There are {len(incomplete)} incomplete task(s) in the central plan.\n\n"
                        f"{task_list}{more}\n\n"
                        "As the Architect, you MUST ensure all tasks are DONE before calling finish.\n\n"
                        "Options:\n"
                        "- Wait for Workers to complete tasks (use check_swarm_status or wait tool)\n"
                        "- Spawn new Workers for PENDING tasks (use spawn_swarm_agent)\n"
                        "- Mark tasks as DONE if they are actually complete (use blackboard update_task)\n"
                        "- Update task status to reflect reality if needed\n\n"
                        "Do NOT call finish until all tasks are resolved."
                    )

            else:
                # Worker: check only MY tasks (assigned to me)
                if not self.agent_name:
                    return None

                my_tasks = [t for t in tasks if self.agent_name in t.get("assignees", [])]
                in_progress = [t for t in my_tasks if t.get("status") == "IN_PROGRESS"]

                if in_progress:
                    task_list = "\n".join([
                        f"  - Task #{t['id']}: {t.get('description', 'N/A')[:80]}"
                        for t in in_progress
                    ])

                    return (
                        f"BLOCKED: You have {len(in_progress)} IN_PROGRESS task(s) that are not marked as DONE.\n\n"
                        f"{task_list}\n\n"
                        "You MUST call blackboard update_task to mark them as DONE before calling finish.\n\n"
                        "Example:\n"
                        f"  blackboard(operation='update_task', filename='central_plan.md', task_id={in_progress[0]['id']}, "
                        "updates={'status': 'DONE', 'result_summary': '...', 'artifact_link': '...'}, expected_checksum='...')\n\n"
                        "Do NOT call finish until all your assigned tasks are marked as DONE."
                    )

        except Exception as e:
            # If we can't read/parse the plan, allow finish (don't block on errors)
            print(f"[FinishTool] Warning: Could not check tasks: {e}")
            return None

        return None


