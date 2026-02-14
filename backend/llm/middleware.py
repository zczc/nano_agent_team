"""
LLM Middleware Module

Provides middleware pattern for intercepting and enhancing LLM calls.
"""

import os
import time
import json
import re
import uuid
import shutil
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Generator
from backend.llm.types import AgentSession
from backend.utils.logger import Logger


class StrategyMiddleware(ABC):
    """
    LLM Middleware Abstract Base Class
    
    Defines standard interface for middleware. All concrete middleware must inherit from this.
    
    Middleware Pattern:
        Middleware sits in the LLM call chain, executing logic before/after calls:
        - Before call: Check and modify session state
        - After call: Process and transform response results
    
    Design Philosophy:
        - Single Responsibility: Each middleware focuses on one task
        - Composability: Multiple middlewares can be chained
        - Transparency: Transparent to LLM call logic
    
    Implementation Requirements:
        Subclasses must implement __call__ method, which:
        1. Receives session and next_call
        2. Can check/modify session
        3. Calls next_call(session) to continue chain
        4. Can process return value of next_call
        5. Returns final result
    
    Typical Implementation Pattern:
        ```python
        class MyMiddleware(StrategyMiddleware):
            def __call__(self, session, next_call):
                # Pre-processing
                print("Before LLM call")
                session.metadata["start_time"] = time.time()
                
                # Call next middleware or LLM
                result = next_call(session)
                
                # Post-processing
                print("After LLM call")
                session.metadata["end_time"] = time.time()
                
                return result
        ```
    """
    
    @abstractmethod
    def __call__(self, session: AgentSession, next_call: Callable[[AgentSession], Any]) -> Any:
        """
        Middleware call interface
        
        Args:
            session: Current Agent session state
            next_call: Next middleware or LLM call function
                      Signature: (AgentSession) -> Any
                      Returns: LLM response (possibly generator)
        
        Returns:
            Any: LLM response or processed result
        
        Implementation Suggestions:
            - Always call next_call(session) to continue chain
            - Be careful when modifying session to avoid breaking state
            - Raise exception to interrupt chain if needed
            - Return value type should match next_call
        """
        pass

class LoopBreakerMiddleware(StrategyMiddleware):
    """
    Loop Detection Middleware
    
    Prevents Agent from entering infinite loops, especially repeating failed tool calls.
    
    How it works:
        1. Analyzes assistant messages in session.history
        2. Extracts recent tool calls (tool_calls)
        3. Detects if there are N consecutive identical calls
        4. Injects warning into system_config if loop detected
    """
    
    def __init__(self, max_repeats: int = 3):
        """
        Initialize Loop Detection Middleware
        
        Args:
            max_repeats: Max allowed repeats, default 3.
                        Trigger warning when same call appears 3 times.
        """
        self.max_repeats = max_repeats

    def __call__(self, session: AgentSession, next_call: Callable[[AgentSession], Any]) -> Any:
        # We look at the history to find consecutive identical tool calls
        tool_calls_history = []
        for msg in reversed(session.history):
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                # Extract simplified tool call signature: name + args
                for tc in msg["tool_calls"]:
                    sig = (tc["function"]["name"], tc["function"]["arguments"])
                    tool_calls_history.append(sig)
            elif msg.get("role") == "user":
                # Reset history on new user message if this was a long-running session
                break
        
        # Check for repeats
        if len(tool_calls_history) >= self.max_repeats:
            last_n = tool_calls_history[:self.max_repeats]
            if all(x == last_n[0] for x in last_n):
                Logger.error(f"Loop detected for tool: {last_n[0][0]}")
                # Intervene by adding a hidden system message to warn the LLM
                session.system_config.extra_sections.append(
                    f"WARNING: You have attempted to call '{last_n[0][0]}' with the same arguments {self.max_repeats} times consecutively. "
                    "This action is failing to produce a new result. PLEASE CHANGE YOUR STRATEGY or stop this action."
                )
        
        return next_call(session)

class SemanticDriftGuard(StrategyMiddleware):
    """
    Semantic Drift Guard Middleware
    
    Prevents Agent from deviating from original goal in long conversations.
    
    Problem Scenario:
        In long ReAct loops, AI might:
        - Forget initial task goal
        - Be distracted by intermediate results
        - Get stuck in details and miss big picture
        - Fall into irrelevant exploration
    """
    
    def __init__(self, drift_threshold: int = 5):
        """
        Initialize Semantic Drift Guard
        
        Args:
            drift_threshold: Iteration threshold to trigger reminder, default 5.
                           i.e., start reminding on the 5th iteration.
        """
        self.drift_threshold = drift_threshold

    def __call__(self, session: AgentSession, next_call: Callable[[AgentSession], Any]) -> Any:
        # Check iteration depth (session.depth is for recursion, we need loop iterations)
        # We can store iteration count in metadata
        iteration = session.metadata.get("iteration_count", 0)
        
        if iteration >= self.drift_threshold:
            # Re-emphasize the original goal or instructions
            original_prompt = session.system_config.base_prompt
            drift_reminder = (
                f"\n--- REMINDER ---\n"
                f"You are in a long reasoning chain (Step {iteration}). "
                f"Ensure your current actions still align with your original goal: {original_prompt[:200]}..."
                f"\nIf the goal is achieved, provide the final answer immediately."
            )
            session.system_config.extra_sections.append(drift_reminder)
        
        return next_call(session)

class ExecutionBudgetManager(StrategyMiddleware):
    """
    Execution Budget Manager Middleware
    
    Limits total iterations and tool calls to control cost and time.
    
    Budget Management Goals:
        - Token Cost Control: Reduce unnecessary LLM calls
        - Response Time Control: Avoid user waiting too long
        - Resource Protection: Prevent malicious or accidental infinite loops
        - Service Quality: Ensure system stability
    """
    
    def __init__(self, max_iterations: int):
        """
        Initialize Execution Budget Manager
        
        Args:
            max_iterations: Max allowed iterations, default 50.
                           Suggest adjusting based on task complexity.
        """
        self.max_iterations = max_iterations

    def __call__(self, session: AgentSession, next_call: Callable[[AgentSession], Any]) -> Any:
        # In this architecture, AgentEngine's run handles the loop.
        # However, middleware can still monitor the history length.
        
        turns = sum(1 for msg in session.history if msg.get("role") == "assistant")
        
        if turns >= self.max_iterations:
            # Get last injection turn from metadata
            last_injection_turn = session.metadata.get("budget_manager_last_injection", -1)
            
            # Check if turns since last injection >= 5 (or first injection)
            if last_injection_turn == -1 or (turns - last_injection_turn >= 5):
                Logger.error(f"Execution budget exceeded: {turns} turns.")
                # We can't easily 'stop' the loop from here without raising an exception
                # or modifying the session in a way that the engine stops.
                # For now, we inject a mandatory termination instruction.
                session.system_config.extra_sections.append(
                    f"CRITICAL: You have exceeded your execution budget ({turns} turns). "
                    "You MUST provide your final best answer NOW and stop calling tools."
                )
                session.metadata["budget_manager_last_injection"] = turns
            
        return next_call(session)

class ToolResultCacheMiddleware(StrategyMiddleware):
    """
    Tool Result Cache Middleware
    
    When tool message content in history is too long and exceeds specified LLM call turns,
    cache content to file and replace with brief reference to prevent context overflow.
    """
    
    def __init__(self, 
                 delay_turns: int = 5,
                 size_threshold: int = 5000,
                 preview_head: int = 500,
                 preview_tail: int = 200):
        """
        Initialize Tool Result Cache Middleware
        """
        self.delay_turns = delay_turns
        self.size_threshold = size_threshold
        self.preview_head = preview_head
        self.preview_tail = preview_tail
        
        # Generate unique session ID
        self.session_id = uuid.uuid4().hex[:8]
        self.cache_dir = os.path.join(os.getcwd(), ".agent_cache", self.session_id)
        self._cached_files = set()  # Track cached files
    
    def __call__(self, session: AgentSession, next_call: Callable[[AgentSession], Any]) -> Any:
        # 1. Count current LLM call turns (assistant messages)
        current_turn = sum(1 for m in session.history if m.get("role") == "assistant")
        
        # 2. Iterate history messages, process tool messages needing compression
        for i, msg in enumerate(session.history):
            if msg.get("role") != "tool":
                continue
            
            content = msg.get("content", "")
            
            # Skip already compressed messages (check marker)
            if content.startswith("[Cached to file:"):
                continue
            
            # Check length threshold
            if len(content) < self.size_threshold:
                continue
            
            # Calculate turns after this message
            turns_after = sum(1 for m in session.history[i+1:] if m.get("role") == "assistant")
            
            # Compress only if delay turns exceeded
            if turns_after >= self.delay_turns:
                file_path = self._cache_to_file(msg, content)
                preview = self._generate_preview(content)
                
                # Replace message content
                msg["content"] = (
                    f"[Cached to file: {file_path}]\n"
                    f"Original length: {len(content)} chars\n\n"
                    f"Preview:\n{preview}\n\n"
                    f"To read full content, use: cat {file_path}"
                )
                
                Logger.info(f"Cached tool result to {file_path} ({len(content)} chars)")
        
        return next_call(session)
    
    def _generate_preview(self, content: str) -> str:
        """Generate preview content (head + tail)"""
        if len(content) <= self.preview_head + self.preview_tail + 20:
            return content
        
        head = content[:self.preview_head]
        tail = content[-self.preview_tail:]
        return f"{head}\n...[TRUNCATED {len(content) - self.preview_head - self.preview_tail} chars]...\n{tail}"
    
    def _cache_to_file(self, msg: dict, content: str) -> str:
        """Cache content to file"""
        # Ensure cache directory exists
        os.makedirs(self.cache_dir, exist_ok=True)
        
        tool_name = msg.get("name", "unknown")
        tool_call_id = msg.get("tool_call_id", "")[:8]
        filename = f"{tool_name}_{tool_call_id}.txt"
        file_path = os.path.join(self.cache_dir, filename)
        
        # Avoid duplicate writes
        if file_path not in self._cached_files:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            self._cached_files.add(file_path)
        
        return file_path
    
    def cleanup(self):
        """Clean up cache directory"""
        if os.path.exists(self.cache_dir):
            try:
                shutil.rmtree(self.cache_dir)
                Logger.info(f"Cleaned up cache directory: {self.cache_dir}")
            except Exception as e:
                Logger.error(f"Failed to cleanup cache: {e}")
        
        # Try to clean parent directory (if empty)
        parent_dir = os.path.dirname(self.cache_dir)
        if os.path.exists(parent_dir) and not os.listdir(parent_dir):
            try:
                os.rmdir(parent_dir)
            except:
                pass

class InteractionRefinementMiddleware(StrategyMiddleware):
    """
    Interaction Refinement Middleware
    
    Rewrites `ask_user` tool call history into natural User-Assistant conversation.
           
    Purpose:
        Makes LLM feel direct conversation with user, maintaining natural flow.
    """
    
    def __call__(self, session: AgentSession, next_call: Callable[[AgentSession], Any]) -> Any:
        if len(session.history) >= 2:
            last_msg = session.history[-1]
            prev_msg = session.history[-2]
            
            # Check for the pattern
            # Prev msg must be assistant role and have tool_calls for 'ask_user'
            if (prev_msg.get("role") == "assistant" and 
                prev_msg.get("tool_calls") and 
                last_msg.get("role") == "tool" and 
                last_msg.get("name") == "ask_user"):
                
                # Verify consistency (same tool call ID)
                tool_call = prev_msg["tool_calls"][0]
                if tool_call["id"] == last_msg.get("tool_call_id"):
                    
                    # 1. Extract the question from arguments
                    try:
                        args = json.loads(tool_call["function"]["arguments"])
                        question = args.get("question", "")
                    except:
                        question = tool_call["function"]["arguments"]
                        
                    # 2. Extract the answer from tool result
                    answer = last_msg.get("content", "")
                    
                    # 3. Rewrite [-2] (Assistant)
                    # Turn it into a pure content message
                    prev_msg["content"] = question
                    if "tool_calls" in prev_msg:
                        del prev_msg["tool_calls"]
                        
                    # 4. Rewrite [-1] (User)
                    # Turn it into a user message
                    last_msg["role"] = "user"
                    last_msg["content"] = answer
                    if "tool_call_id" in last_msg:
                        del last_msg["tool_call_id"]
                    if "name" in last_msg:
                        del last_msg["name"]
                    
                    # Add metadata tag for Watchdog (or other guards) to recognize this was an AskUser call
                    last_msg["metadata"] = {"from_tool_call": "ask_user"}
                        
                    Logger.info("Refined interaction history for ask_user.")
                    
        return next_call(session)

class ErrorRecoveryMiddleware(StrategyMiddleware):
    """
    Error Recovery Middleware
    
    Handles exceptions during LLM calls (e.g., 400 Context Length Exceeded, 500 Server Error, Connection Error).
    Provides automatic retry and fallback strategies.
    """
    
    def __init__(self, max_retries: int = 2, max_connection_retries: int = 5, backoff_factor: float = 1.0):
        self.max_retries = max_retries
        self.max_connection_retries = max_connection_retries
        self.backoff_factor = backoff_factor
    
    def _is_connection_error(self, error: Exception) -> bool:
        """Determine if connection error"""
        error_msg = str(error).lower()
        connection_keywords = [
            'connection', 'timeout', 'network', 'refused',
            'unreachable', 'timed out', 'temporary failure',
            'connection error', 'connect timeout', 'read timeout'
        ]
        return any(keyword in error_msg for keyword in connection_keywords)

    def __call__(self, session: AgentSession, next_call: Callable[[AgentSession], Any]) -> Any:
        attempts = 0
        last_error = None
        
        # 1. Determine max retries
        max_retries_to_use = self.max_retries
        
        # 2. Retry loop
        while attempts <= max_retries_to_use:
            try:
                # Attempt call
                return next_call(session)
            except Exception as e:
                last_error = e
                attempts += 1
                
                # Dynamically adjust max retries (if connection error detected)
                if self._is_connection_error(e) and max_retries_to_use < self.max_connection_retries:
                    max_retries_to_use = self.max_connection_retries
                    Logger.info(f"Connection error detected, increasing max retries to {self.max_connection_retries}")
                
                if attempts <= max_retries_to_use:
                    sleep_time = self.backoff_factor * (2 ** (attempts - 1))
                    error_type = "Connection error" if self._is_connection_error(e) else "API error"
                    Logger.warning(f"{error_type}: {e}. Retrying ({attempts}/{max_retries_to_use}) in {sleep_time}s...")
                    time.sleep(sleep_time)
                else:
                    error_type = "Connection error" if self._is_connection_error(e) else "API error"
                    Logger.error(f"{error_type} after {max_retries_to_use} retries: {e}")

        # 2. Fallback strategy
        # If retries exhausted and last message is Tool, likely due to content length or format
        if session.history and session.history[-1]["role"] == "tool":
            Logger.info("Attempting error recovery by modifying last tool result...")
            
            # Modify last message content
            original_content = session.history[-1]["content"]
            error_hint = (
                f"Error: The previous tool execution resulted in an API error "
                f"(likely payload too large or invalid). "
                f"Original length: {len(str(original_content))}. "
                f"Please try a different approach or arguments."
            )
            
            # Modification is in-place, affecting session object
            session.history[-1]["content"] = error_hint
            
            try:
                # Last attempt
                return next_call(session)
            except Exception as e:
                Logger.error(f"Recovery attempt failed: {e}")
                # Recovery failed, raise original (or last) error
                raise last_error

        # If cannot fallback, raise error
        raise last_error
