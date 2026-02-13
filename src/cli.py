
import argparse
import sys
import os

# Ensure we can import the package (project root is ../ from src/)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))

from src.core.agent_wrapper import SwarmAgent
from src.core.middlewares import ParentProcessMonitorMiddleware

from backend.infra.config import Config

def main():
    parser = argparse.ArgumentParser(description="Nano Agent Team CLI")
    parser.add_argument("--role", type=str, required=True, help="Agent Role Description")
    parser.add_argument("--name", type=str, default="Agent", help="Agent Name")
    parser.add_argument("--blackboard", type=str, default=".blackboard", help="Blackboard Directory")
    parser.add_argument("--goal", type=str, default="", help="Initial Goal/Instruction")
    parser.add_argument("--scenario", type=str, default="", help="Scenario Context")
    parser.add_argument("--model", type=str, default=None, help="Model Provider Key")
    parser.add_argument("--parent-pid", type=int, default=None, help="Parent PID to monitor for auto-termination")
    parser.add_argument("--parent-agent-name", type=str, default="Assistant", help="Parent agent name in registry (for status monitoring)")
    parser.add_argument("--keys", type=str, default=None, help="Path to keys.json")
    parser.add_argument("--exclude-tools", type=str, default="", help="Comma-separated list of tools to exclude")
    parser.add_argument("--max-iterations", type=int, default=50, help="Max iterations for the agent (default: 50)")
    
    args = parser.parse_args()
    
    # 0. Initialize Config / Keys
    if args.keys:
        keys_path = os.path.abspath(args.keys)
        if os.path.exists(keys_path):
            print(f"[Worker] Loading keys from: {keys_path}")
            Config.initialize(keys_path)
        else:
            print(f"[Worker] Warning: Keys file not found at {keys_path}, using default config.")
            Config.initialize()
    else:
        # Use default discovery (same as TUI)
        Config.initialize()
    
    print(f"Starting Swarm Agent '{args.name}'...")
    print(f"  Role: {args.role}")
    print(f"  Blackboard: {os.path.abspath(args.blackboard)}")
    print(f"  Max Iterations: {args.max_iterations}")
    
    strategies = []
    if args.parent_pid:
        print(f"  Monitoring Parent PID: {args.parent_pid} (agent: {args.parent_agent_name})")
        strategies.append(ParentProcessMonitorMiddleware(
            parent_pid=args.parent_pid,
            agent_name=args.name,
            blackboard_dir=args.blackboard,
            parent_agent_name=args.parent_agent_name
        ))
    
    agent = SwarmAgent(
        role=args.role,
        name=args.name,
        blackboard_dir=args.blackboard,
        model=args.model,
        max_iterations=args.max_iterations,
        extra_strategies=strategies
    )

    # Initialize Environment for Tools
    from backend.infra.envs.local import LocalEnvironment
    # Use current working directory (project root) as workspace for workers too, 
    # but restrict write access to the Blackboard only.
    blackboard_abs = os.path.abspath(args.blackboard)
    env = LocalEnvironment(
        workspace_root=os.getcwd(),
        allowed_write_paths=[blackboard_abs],
        non_interactive=True,
        agent_name=args.name,
        blackboard_dir=blackboard_abs
    )

    print("  [Init] Bootstrapping LLM Registry (Tools, Skills, Agents)...")
    
    # Define Factory for Sub-Agents
    from backend.llm.engine import AgentEngine
    from backend.llm.middleware import ExecutionBudgetManager
    
    def engine_factory(tools=None):
        # Simple factory for SubAgents
        return AgentEngine(
            tools=tools or [],
            strategies=[ExecutionBudgetManager(max_iterations=100)],
            provider_key=args.model # Inherit model provider
        )
        
    from backend.llm.tool_registry import bootstrap_llm
    
    # Use hidden directories for now as discovered, or relative paths if user ensures existence
    registry, _, skill_registry = bootstrap_llm(
        agents_dir=os.path.abspath(".subagents"), # User mentioned ./subagent, let's try mapping to .subagents or assume user meant hidden
        skills_dir=os.path.abspath(".skills"),
        engine_factory=engine_factory
    )
    
    # Register all tools from registry to the Agent
    print("  [Auto-Load] Loading tools from Registry...")
    excluded_tool_names = [t.strip() for t in args.exclude_tools.split(",")] if args.exclude_tools else []
    
    for tool_name in registry.get_all_tool_names():
        # Generic exclusion based on CLI arg
        if tool_name in excluded_tool_names:
            continue
            
        try:
            # Create tool with context
            tool_instance = registry.create_tool(tool_name, context={
                "env": env,
                "skill_registry": skill_registry
            })
            if tool_instance:
                agent.add_tool(tool_instance)
                print(f"    + Loaded {tool_instance.name}")
        except Exception as e:
            print(f"    ! Failed to load {tool_name}: {e}")
            
    try:
        agent.run(goal=args.goal, scenario=args.scenario)
    except KeyboardInterrupt:
        print("\nExiting...")

if __name__ == "__main__":
    main()
