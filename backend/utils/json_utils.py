import json
import re
from typing import Any, Tuple

def repair_truncated_json(json_str: str) -> Tuple[str, Any]:
    """
    尝试修复截断的 JSON 字符串。
    返回 (修复后的字符串, 解析后的对象)。如果解析失败，返回 (json_str, None)。
    """
    if not json_str:
        return json_str, None
    
    # 尝试直接解析
    try:
        data = json.loads(json_str)
        return json_str, data
    except json.JSONDecodeError:
        pass

    # 1. 处理未闭合的字符串引号
    # 如果字符串末尾有奇数个未转义的引号，且不是以引号结束，或者报错信息提示 Expecting ',' delimiter
    working_str = json_str.strip()
    
    # 简单的启发式逻辑：补全引号和括号
    stack = []
    in_string = False
    escaped = False
    
    for char in working_str:
        if escaped:
            escaped = False
            continue
        if char == '\\':
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if not in_string:
            if char == '{':
                stack.append('}')
            elif char == '[':
                stack.append(']')
            elif char == '}':
                if stack and stack[-1] == '}':
                    stack.pop()
            elif char == ']':
                if stack and stack[-1] == ']':
                    stack.pop()

    repaired_str = working_str
    if in_string:
        repaired_str += '"'
    
    while stack:
        repaired_str += stack.pop()
        
    try:
        data = json.loads(repaired_str)
        return repaired_str, data
    except json.JSONDecodeError:
        # 如果还是失败，尝试更激进的修复（如移除末尾多余逗号后补齐）
        aggressive_str = re.sub(r',\s*$', '', working_str.strip())
        # 重新应用 stack 逻辑
        # (这里为了简洁先返回原样，后续根据需要增强)
        return json_str, None
