"""
Logger Utility Module

Provides global logging functionality, outputting all logs to file instead of console.
This is because in Native Messaging environment, stdout/stderr are used for communication with browser.
"""

import datetime
import sys
from backend.infra.config import Config


class Logger:
    """
    Simple Logger Class
    
    Outputs logs to file instead of console (since stdout is occupied by Native Messaging).
    """
    
    @staticmethod
    def log(message: str) -> None:
        """
        Log a normal message
        
        Format: {timestamp} - {message}
        """
        try:
            with open(Config.LOG_PATH, 'a', encoding='utf-8') as f:
                f.write(f"{datetime.datetime.now()} - {message}\n")
        except Exception:
            # If logging fails, we can't do much but ignore
            pass

    @staticmethod
    def info(message: str) -> None:
        """Log info message (alias for log)"""
        Logger.log(message)
            
    @staticmethod
    def error(message: str) -> None:
        """
        Log error message
        
        Adds "ERROR: " prefix for easy filtering.
        
        Args:
            message: Error info
            
        Returns:
            None
            
        Example:
            >>> Logger.error("Database connection failed")
            >>> Logger.error(f"Invalid config: {e}")
        """
        Logger.log(f"ERROR: {message}")

    @staticmethod
    def warning(message: str) -> None:
        """
        Log warning message
        
        Adds "WARNING: " prefix.
        """
        Logger.log(f"WARNING: {message}")

    @staticmethod
    def debug(message: str) -> None:
        """
        Log debug message
        
        Adds "DEBUG: " prefix.
        """
        Logger.log(f"DEBUG: {message}")

