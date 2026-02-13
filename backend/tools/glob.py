"""
Glob Tool - Find files matching patterns
"""

import os
from typing import Dict, Any, Optional, List
from pathlib import Path

from backend.tools.base import BaseTool
from backend.infra.config import Config
from backend.llm.decorators import schema_strict_validator

class GlobTool(BaseTool):
    """
    Find files and directories matching glob patterns.
    Similar to Unix find with glob patterns.
    """

    @property
    def name(self) -> str:
        return "glob"

    @property
    def description(self) -> str:
        return """Find files and directories matching glob patterns.
Useful for locating files by name, extension, or pattern.
Supports wildcards: * (any chars), ** (recursive), ? (single char).
Examples: '*.py', 'src/**/*.js', 'test_*.py'"""

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to match (e.g., '*.py', 'src/**/*.js', 'test_*')",
                },
                "path": {
                    "type": "string",
                    "description": "Base directory to search from (must be absolute path)",
                },
                "type": {
                    "type": "string",
                    "description": "Filter by type: 'file', 'dir', or 'all' (default: 'all')",
                    "enum": ["file", "dir", "all"],
                    "default": "all",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default: 200)",
                    "default": 200,
                },
                "show_hidden": {
                    "type": "boolean",
                    "description": "Include hidden files/dirs (starting with .)",
                    "default": False,
                },
            },
            "required": ["pattern", "path"],
        }

    def get_status_message(self, **kwargs) -> str:
        pattern = kwargs.get("pattern", "")
        return f"\n\n🔍 正在查找文件: '{pattern}'...\n"

    @schema_strict_validator
    def execute(
        self,
        pattern: str,
        path: Optional[str] = None,
        type: str = "all",
        max_results: int = 200,
        show_hidden: bool = False,
    ) -> str:
        """
        Find files matching glob pattern.

        Args:
            pattern: Glob pattern to match
            path: Base directory to search from
            type: Filter by 'file', 'dir', or 'all'
            max_results: Maximum results to return
            show_hidden: Include hidden files/dirs

        Returns:
            Formatted string with matching paths
        """
        try:
            # Determine base path
            if path:
                base_path = Path(path)
                if not base_path.is_absolute():
                    base_path = Path(Config.ROOT_PATH) / path
            else:
                base_path = Path(Config.ROOT_PATH)

            if not base_path.exists():
                return f"Error: Path '{base_path}' does not exist."

            if not base_path.is_dir():
                return f"Error: Path '{base_path}' is not a directory."

            # Determine if recursive pattern
            if "**" in pattern:
                matches = base_path.glob(pattern)
            else:
                # For non-recursive, search from base_path
                matches = base_path.glob(pattern)

            # Filter and collect results
            results: List[Path] = []
            for match in matches:
                # Skip hidden files unless requested
                if not show_hidden and any(part.startswith(".") for part in match.parts):
                    continue

                # Filter by type
                if type == "file" and not match.is_file():
                    continue
                if type == "dir" and not match.is_dir():
                    continue

                results.append(match)

                # Respect max results
                if len(results) >= max_results:
                    break

            # Format output
            if not results:
                return f"No matches found for pattern '{pattern}' in {base_path}"

            # Sort results for consistent output
            results.sort()

            output = [f"Found {len(results)} match(es) for '{pattern}':\n"]
            
            for result in results:
                try:
                    # Try to show relative path
                    rel_path = result.relative_to(Config.ROOT_PATH)
                    display_path = str(rel_path)
                except ValueError:
                    # If not relative to workspace, show full path
                    display_path = str(result)

                # Add type indicator
                if result.is_dir():
                    display_path += "/"
                
                output.append(f"  {display_path}")

            if len(output) - 1 >= max_results:
                output.append(f"\n... (showing first {max_results} results)")

            return "\n".join(output)

        except Exception as e:
            return f"Error during glob: {e}"
