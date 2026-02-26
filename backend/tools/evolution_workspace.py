import os
import subprocess
from typing import Dict, Any, List, Optional

from backend.tools.base import BaseTool
from backend.llm.decorators import schema_strict_validator
from backend.infra.config import Config


class EvolutionWorkspaceTool(BaseTool):
    """
    Apply or discard evolution workspace changes via git worktree.

    MUST be called after reading the Tester's VERDICT, before writing the
    evolution report or calling finish.

    - PASS: stages changed_files, commits to the evolution branch, removes worktree.
    - FAIL: force-removes the worktree without committing (branch is kept).
    """

    @property
    def name(self) -> str:
        return "evolution_workspace"

    @property
    def description(self) -> str:
        return (
            "Apply (PASS) or discard (FAIL) the evolution workspace. "
            "Call this immediately after reading the Tester's VERDICT — "
            "before writing the report or calling finish. "
            "PASS commits changed files to the evolution branch then removes the worktree. "
            "FAIL force-removes the worktree without committing."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": ["PASS", "FAIL"],
                    "description": "The Tester's verdict."
                },
                "round_num": {
                    "type": "integer",
                    "description": "Current evolution round number (used in commit message and branch name)."
                },
                "description": {
                    "type": "string",
                    "description": "Short description for the commit message (required for PASS)."
                },
                "changed_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "File paths relative to workspace root that the Developer changed "
                        "(required for PASS). E.g. ['backend/tools/foo.py', 'tests/test_foo.py']"
                    )
                }
            },
            "required": ["verdict", "round_num"]
        }

    @schema_strict_validator
    def execute(
        self,
        verdict: str,
        round_num: int,
        description: str = "",
        changed_files: Optional[List[str]] = None,
    ) -> str:
        root = Config.ROOT_PATH
        workspace = os.path.join(Config.BLACKBOARD_ROOT, "resources", "workspace")

        if not os.path.exists(workspace):
            return "Workspace does not exist — nothing to clean up."

        # A worktree has a .git FILE (not directory) at its root
        git_marker = os.path.join(workspace, ".git")
        if not os.path.isfile(git_marker):
            return "Workspace exists but is not a git worktree — skipping."

        _log = lambda msg: print(f"[evo_workspace] {msg}", flush=True)
        _log(f"verdict={verdict} round={round_num} workspace={workspace}")

        if verdict == "PASS":
            if not changed_files:
                return "Error: changed_files is required for PASS verdict."

            # 0. Read branch name from worktree BEFORE removing it
            branch_result = subprocess.run(
                ["git", "-C", workspace, "branch", "--show-current"],
                capture_output=True, text=True
            )
            evolution_branch = branch_result.stdout.strip() or f"evolution/r{round_num}"
            _log(f"PASS — branch={evolution_branch} files={changed_files}")

            # 1. Stage the changed files
            _log(f"git add {changed_files}")
            add_result = subprocess.run(
                ["git", "-C", workspace, "add"] + changed_files,
                capture_output=True, text=True
            )
            _log(f"git add rc={add_result.returncode} stderr={add_result.stderr.strip()!r}")
            if add_result.returncode != 0:
                return f"Error staging files: {add_result.stderr.strip()}"

            # 2. Commit
            commit_msg = f"evolution({evolution_branch}): {description}"
            _log(f"git commit -m {commit_msg!r}")
            commit_result = subprocess.run(
                ["git", "-C", workspace, "commit", "-m", commit_msg],
                capture_output=True, text=True
            )
            _log(f"git commit rc={commit_result.returncode} out={commit_result.stdout.strip()!r}")
            if commit_result.returncode != 0:
                _log(f"git commit stderr={commit_result.stderr.strip()!r}")
                return f"Error committing: {commit_result.stderr.strip()}"

            # 3. Remove worktree (use --force as fallback if untracked files remain)
            _log(f"git worktree remove {workspace}")
            remove_result = subprocess.run(
                ["git", "-C", root, "worktree", "remove", workspace],
                capture_output=True, text=True
            )
            if remove_result.returncode != 0:
                _log(f"worktree remove failed (rc={remove_result.returncode}), retrying --force")
                remove_result = subprocess.run(
                    ["git", "-C", root, "worktree", "remove", workspace, "--force"],
                    capture_output=True, text=True
                )
                if remove_result.returncode != 0:
                    _log(f"worktree remove --force failed: {remove_result.stderr.strip()!r}")
                    return f"Error removing worktree: {remove_result.stderr.strip()}"
            _log(f"worktree removed rc={remove_result.returncode}")

            first_line = commit_result.stdout.strip().split("\n")[0]
            result = (
                f"PASS: committed {len(changed_files)} file(s) to branch "
                f"{evolution_branch} ({first_line}). Worktree removed."
            )
            _log(result)
            return result

        else:  # FAIL
            # Read branch name for the message
            branch_result = subprocess.run(
                ["git", "-C", workspace, "branch", "--show-current"],
                capture_output=True, text=True
            )
            evolution_branch = branch_result.stdout.strip() or f"evolution/r{round_num}"
            _log(f"FAIL — branch={evolution_branch}, force-removing worktree")

            remove_result = subprocess.run(
                ["git", "-C", root, "worktree", "remove", workspace, "--force"],
                capture_output=True, text=True
            )
            _log(f"worktree remove --force rc={remove_result.returncode}")
            if remove_result.returncode != 0:
                _log(f"stderr={remove_result.stderr.strip()!r}")
                return f"Error removing worktree: {remove_result.stderr.strip()}"
            result = (
                f"FAIL: workspace discarded. "
                f"Branch {evolution_branch} kept for post-mortem."
            )
            _log(result)
            return result
