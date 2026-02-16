
import yaml
from typing import Tuple, Dict

def parse_frontmatter(content: str) -> Tuple[Dict, str]:
    """
    Parse YAML frontmatter from a markdown string.
    Handles both \r\n and \n line endings.
    Returns (metadata_dict, body_content).
    """
    if content.startswith("---"):
        # Handle both \r\n and \n
        try:
            # Find the end of the first marker line
            first_newline = content.find("\n")
            if first_newline == -1:
                return {}, content

            # Find the start of the closing marker
            # We search for \n---\n or \n--- (at end of file or followed by newline)
            end_marker_pos = content.find("\n---", first_newline)
            if end_marker_pos == -1:
                return {}, content

            fm_section = content[first_newline+1:end_marker_pos].strip()
            body_start = content.find("\n", end_marker_pos + 1)
            if body_start == -1:
                body = ""  # No body after closing marker
            else:
                body = content[body_start+1:]

            try:
                meta = yaml.safe_load(fm_section)
                return meta if isinstance(meta, dict) else {}, body
            except yaml.YAMLError:
                return {}, content
        except Exception:
            return {}, content
    return {}, content
