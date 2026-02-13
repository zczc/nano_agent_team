"""
Grep Tool - Search for patterns in files
"""

import os
import re
from typing import Dict, Any, Optional, List
from pathlib import Path

from backend.tools.base import BaseTool
from backend.infra.config import Config
from backend.llm.decorators import schema_strict_validator


class GrepTool(BaseTool):
    """
    Search for text patterns in files (similar to Unix grep).
    Supports regex patterns and recursive directory search.
    """

    @property
    def name(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        return """Search for text patterns in files recursively.
Useful for finding specific code, configuration, or text across multiple files.
Supports regular expressions and case-insensitive search.
Returns file paths and matching line numbers."""

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "The text pattern or regex to search for",
                },
                "path": {
                    "type": "string",
                    "description": "Directory or file path to search in (must be absolute path)",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Search recursively in subdirectories (default: True)",
                    "default": True,
                },
                "case_sensitive": {
                    "type": "boolean",
                    "description": "Case-sensitive search (default: True)",
                    "default": True,
                },
                "file_pattern": {
                    "type": "string",
                    "description": "Filter files by glob pattern (e.g., '*.py', '*.js')",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default: 100)",
                    "default": 100,
                },
            },
            "required": ["pattern", "path"],
        }

    def get_status_message(self, **kwargs) -> str:
        pattern = kwargs.get("pattern", "")
        path = kwargs.get("path", Config.ROOT_PATH)
        return f"\n\n🔍 正在搜索: '{pattern}' in {path}...\n"

    @schema_strict_validator
    def execute(
        self,
        pattern: str,
        path: Optional[str] = None,
        recursive: bool = True,
        case_sensitive: bool = True,
        file_pattern: Optional[str] = None,
        max_results: int = 100,
    ) -> str:
        """
        Search for pattern in files.

        Args:
            pattern: Text pattern or regex to search for
            path: Directory or file to search in
            recursive: Search recursively
            case_sensitive: Case-sensitive search
            file_pattern: Filter files by glob pattern
            max_results: Maximum results to return

        Returns:
            Formatted string with matching files and line numbers
        """
        try:
            # Determine search path
            if path:
                search_path = Path(path)
                if not search_path.is_absolute():
                    search_path = Path(Config.ROOT_PATH) / path
            else:
                search_path = Path(Config.ROOT_PATH)

            if not search_path.exists():
                return f"Error: Path '{search_path}' does not exist."

            # Compile regex pattern
            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                regex = re.compile(pattern, flags)
            except re.error as e:
                return f"Error: Invalid regex pattern: {e}"

            results: List[Dict[str, Any]] = []
            files_searched = 0

            # Search in files
            if search_path.is_file():
                # Single file search
                matches = self._search_file(search_path, regex)
                if matches:
                    results.append({"file": str(search_path), "matches": matches})
                files_searched = 1
            else:
                # Directory search
                pattern_obj = Path(file_pattern) if file_pattern else None
                
                if recursive:
                    file_iter = search_path.rglob("*")
                else:
                    file_iter = search_path.glob("*")

                for file_path in file_iter:
                    if not file_path.is_file():
                        continue

                    # Filter by file pattern if provided
                    if pattern_obj and not file_path.match(file_pattern):
                        continue

                    # Skip binary files and common ignore patterns
                    if self._should_skip(file_path):
                        continue

                    files_searched += 1
                    matches = self._search_file(file_path, regex)
                    
                    if matches:
                        results.append({
                            "file": str(file_path.relative_to(Config.ROOT_PATH)),
                            "matches": matches,
                        })

                    # Respect max results limit
                    if len(results) >= max_results:
                        break

            # Format results
            if not results:
                return f"No matches found for '{pattern}' (searched {files_searched} files)"

            output = [f"Found {len(results)} file(s) with matches (searched {files_searched} files):\n"]
            
            for result in results[:max_results]:
                file_path = result["file"]
                matches = result["matches"]
                output.append(f"\n{file_path}:")
                for line_num, line_content in matches[:10]:  # Show first 10 matches per file
                    # Truncate long lines
                    if len(line_content) > 200:
                        line_content = line_content[:197] + "..."
                    output.append(f"  {line_num}: {line_content}")
                
                if len(matches) > 10:
                    output.append(f"  ... and {len(matches) - 10} more matches")

            if len(results) > max_results:
                output.append(f"\n... and {len(results) - max_results} more files (increase max_results to see more)")

            return "\n".join(output)

        except Exception as e:
            return f"Error during grep: {e}"

    def _search_file(self, file_path: Path, regex: re.Pattern) -> List[tuple]:
        """
        Search for pattern in a single file.

        Returns:
            List of (line_number, line_content) tuples
        """
        matches = []
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                for line_num, line in enumerate(f, start=1):
                    line = line.rstrip("\n\r")
                    if regex.search(line):
                        matches.append((line_num, line))
        except (UnicodeDecodeError, PermissionError):
            # Skip files that can't be read
            pass
        return matches

    def _should_skip(self, file_path: Path) -> bool:
        """Check if file should be skipped."""
        # Skip common binary and generated files
        skip_patterns = [
            ".git/", "__pycache__/", "node_modules/", ".venv/", "venv/",
            ".pyc", ".so", ".dylib", ".dll", ".exe", ".bin", ".class",
            ".jpg", ".jpeg", ".png", ".gif", ".pdf", ".zip", ".tar", ".gz",
        ]
        
        path_str = str(file_path)
        return any(pattern in path_str for pattern in skip_patterns)
