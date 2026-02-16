"""
Tool Decorators Module

Provides a set of decorators for enhancing tool capabilities, including parameter validation, security sandbox, and output optimization.
Using the decorator pattern, we decouple generic defensive logic from specific tool business logic.
"""

import functools
import json
import time
from typing import Any, Dict, List, Callable, Optional
from backend.utils.logger import Logger
from backend.infra.config import Config


def resolve_path_variables(func: Callable):
    """
    Decorator to resolve {{root_path}} and {{blackboard}} in string arguments.
    """
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        def _resolve(val):
            if isinstance(val, str):
                val = val.replace("{{root_path}}", Config.ROOT_PATH)
                if Config.BLACKBOARD_ROOT:
                    val = val.replace("{{blackboard}}", Config.BLACKBOARD_ROOT)
            return val

        # Resolve kwargs
        new_kwargs = {k: _resolve(v) for k, v in kwargs.items()}
        
        # Resolve args (less common for tools but good for completeness)
        new_args = tuple(_resolve(arg) for arg in args)
        
        return func(self, *new_args, **new_kwargs)
    return wrapper


def schema_strict_validator(func: Callable):
    """
    Input Parameter Validation Decorator
    
    Validates input parameters against `parameters_schema` defined in the tool class before execution.
    Supports required field checks and basic type validation.
    
    If validation fails, returns an error message string directly without executing the decorated method.
    """
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        schema = getattr(self, "parameters_schema", {})
        properties = schema.get("properties", {})
        required = schema.get("required", [])

        # 1. Check required fields
        for field in required:
            if field not in kwargs:
                return f"Error: Missing required parameter '{field}'."

        # 2. Strict validation: Check for unexpected parameters
        for key in kwargs:
            if key not in properties:
                return f"Error: Unexpected parameter '{key}'. This tool only accepts: {list(properties.keys())}."

        # 3. Basic type validation
        for key, value in kwargs.items():
            expected_type = properties[key].get("type")
            if expected_type == "string" and not isinstance(value, str):
                return f"Error: Parameter '{key}' must be a string."
            elif expected_type == "integer" and not isinstance(value, int):
                return f"Error: Parameter '{key}' must be an integer."
            elif expected_type == "boolean" and not isinstance(value, bool):
                return f"Error: Parameter '{key}' must be a boolean."
            elif expected_type == "array" and not isinstance(value, list):
                return f"Error: Parameter '{key}' must be an array."
            elif expected_type == "object" and not isinstance(value, dict):
                return f"Error: Parameter '{key}' must be an object."

        return func(self, **kwargs)
    return wrapper


def environment_guard(func: Callable):
    """
    Environment Security Guard Decorator
    """
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        # 1. Path security check
        path_keys = ['path', 'directory', 'filename', 'filepath', 'content_path', 'agent_path']
        for key in path_keys:
            if key in kwargs and isinstance(kwargs[key], str):
                p = kwargs[key]
                # Block sensitive system directories
                if p.startswith(('/etc', '/var', '/root', '/proc', '/sys')):
                    return f"Error: Access to system path '{p}' is prohibited for security reasons."
                # Block path traversal
                if '..' in p:
                    return f"Error: Relative paths with '..' are not allowed."

        # 2. Execution time monitoring
        start_time = time.time()
        result = func(self, *args, **kwargs)
        duration = time.time() - start_time
        
        if duration > 10.0:  # 10s timeout warning
            Logger.warning(f"Tool {self.name} took {duration:.2f}s to execute.")
            
        return result
    return wrapper


def output_sanitizer(max_length: int = 2000):
    """
    Output Sanitizer and Truncation Decorator
    
    Converts tool execution results into format suitable for LLM consumption:
    1. Formatting: Automatically converts dict/list to JSON string.
    2. Error Handling: Captures unhandled exceptions within tool and logs them.
    3. Truncation: Automatically truncates output if too long and appends explanation, preventing excessive Token usage.
    
    Args:
        max_length: Max allowed output character length, default 2000.
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            try:
                result = func(self, *args, **kwargs)
                
                # Automatically convert to string
                if isinstance(result, (dict, list)):
                    result_str = json.dumps(result, ensure_ascii=False, indent=2)
                else:
                    result_str = str(result)
                
                # Truncate if too long
                original_length = len(result_str)
                if original_length > max_length:
                    result_str = result_str[:max_length] + f"\n\n[Output truncated due to length... original size: {original_length} characters]"
                
                return result_str
            except Exception as e:
                Logger.error(f"Error in tool {self.name}: {e}")
                return f"Error during execution: {str(e)}"
        return wrapper
    return decorator
