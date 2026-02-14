"""Common file utilities."""
import re


def sanitize_filename(name: str) -> str:
    """
    Sanitize a string for safe use as a file name.

    Replaces characters that are unsafe in file names (<>:"/\\|?* and control characters)
    with underscores, and limits the length to avoid issues with very long paths.
    """
    # Replace unsafe characters with underscore
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f\s]', '_', name)
    # Limit length to avoid too long file names
    return sanitized[:100]
