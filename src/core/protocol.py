
import yaml
from typing import Tuple, Dict

def parse_frontmatter(content: str) -> Tuple[Dict, str]:
    """
    Parse YAML frontmatter from a markdown string.
    Returns (metadata_dict, body_content).
    """
    if content.startswith("---\n"):
        try:
            # Split into: empty, frontmatter, body
            parts = content.split("---\n", 2)
            if len(parts) >= 3:
                fm = parts[1]
                body = parts[2]
                return yaml.safe_load(fm) or {}, body
        except Exception:
            pass # Fallback to empty metadata on error
            
    return {}, content
