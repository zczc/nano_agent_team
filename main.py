
import os
import sys
import argparse
import shutil
from src.core.agent_wrapper import SwarmAgent
from backend.infra.config import Config
from backend.tools.web_search import SearchTool
from backend.tools.web_reader import WebReaderTool
from src.core.middlewares import RequestMonitorMiddleware, WatchdogGuardMiddleware
from backend.infra.envs.local import LocalEnvironment
from backend.tools.bash import BashTool
from backend.tools.write_file import WriteFileTool
from backend.tools.read_file import ReadFileTool
from backend.tools.edit_file import EditFileTool
from backend.tools.grep import GrepTool
from backend.tools.glob import GlobTool

# Ensure project root is in path
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

def setup_env(args):
    """
    Initialize environment.
    Default: Clean .blackboard (unless --keep-history is set).
    """
    blackboard_dir = os.path.join(project_root, ".blackboard")
    
    if args.keep_history:
        print(f"[Launcher] Keeping history at {blackboard_dir}")
        return

    if os.path.exists(blackboard_dir):
        print(f"[Launcher] Cleaning blackboard at {blackboard_dir}...")
        try:
            shutil.rmtree(blackboard_dir)
        except Exception as e:
            print(f"[Launcher] Warning: Failed to clean blackboard: {e}")

def archive_session():
    """
    Archive session.
    Copy .blackboard content to logs/session_<timestamp>.
    """
    import datetime
    
    blackboard_dir = os.path.join(project_root, ".blackboard")
    if not os.path.exists(blackboard_dir):
        return

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = os.path.join(project_root, "logs", f"session_{timestamp}")
    
    print(f"\n[Launcher] Archiving session to {log_dir}...")
    try:
        shutil.copytree(blackboard_dir, log_dir)
        print(f"[Launcher] Session archived successfully.")
    except Exception as e:
        print(f"[Launcher] Error archiving session: {e}")

def main():
    parser = argparse.ArgumentParser(description="Nano Agent Team - Watchdog Launcher")
    parser.add_argument("query", nargs="?", help="The mission or query for the swarm")
    parser.add_argument("--role", default="Architect", help="Role of the main agent (default: Architect)")
    parser.add_argument("--name", default="Watchdog", help="Name of the main agent")
    # Changed: --clean is now default behavior, added --keep-history to inverse it
    parser.add_argument("--keep-history", action="store_true", help="Keep the previous blackboard state (do not clean)")
    parser.add_argument("--model", type=str, default=None, help="Model provider key (default: Use settings.json)")
    

    # Global flags
    parser.add_argument("--keys", type=str, default="keys.json", help="Path to keys.json (default: keys.json)")
    
    args = parser.parse_args()

    print("=== Nano Agent Team Launcher ===")

    # 1. Initialize Config 
    Config.initialize(args.keys)

    # 2. Setup Environment (Clean Blackboard)
    setup_env(args)

    # 3. Load Architect/Watchdog Prompt
    # By default we use the Architect prompt which is designed to plan swarms
    prompt_path = os.path.join(project_root, "src/prompts/architect.md")
    if not os.path.exists(prompt_path):
        print(f"Error: Prompt file not found at {prompt_path}")
        return

    with open(prompt_path, "r", encoding="utf-8") as f:
        architect_role_content = f.read()


    # 4. Determine Mission
    mission = args.query
    if not mission:
        print("\nPlease enter the Swarm Mission:")
        mission = input("> ").strip()
    
    if not mission:
        print("No mission provided. Exiting.")
        return

    blackboard_dir = os.path.join(project_root, ".blackboard")

    try:
        # 5. Initialize Watchdog Agent
        # Log Watchdog start for status tracking
        watchdog_log = os.path.join(blackboard_dir, "logs", "Watchdog.log")
        os.makedirs(os.path.dirname(watchdog_log), exist_ok=True)
        with open(watchdog_log, "w", encoding="utf-8") as f:
            f.write(f"[{os.getpid()}] Watchdog Started\n")
            f.write(f"PID: {os.getpid()}\n")
            f.write(f"Mission: {mission}\n")
        
        # Initialize Middleware
        request_monitor = RequestMonitorMiddleware(blackboard_dir)
        watchdog_guard = WatchdogGuardMiddleware(
            agent_name=args.name,
            blackboard_dir=blackboard_dir,
            is_architect=True
        )

        # The Watchdog uses the Architect role to design and spawn other agents.
        watchdog = SwarmAgent(
            role=architect_role_content,
            name=args.name,
            blackboard_dir=blackboard_dir,
            model=args.model,
            max_iterations=200,  # Increased Budget for Watchdog
            extra_strategies=[request_monitor, watchdog_guard]
        )
        
        # 6. Add Research Capabilities (Requested by User)
        watchdog.add_tool(SearchTool())
        watchdog.add_tool(WebReaderTool())

        env = LocalEnvironment(
            workspace_root=project_root,
            blackboard_dir=blackboard_dir,
            agent_name=args.name
        )
        watchdog.add_tool(BashTool(env=env))
        watchdog.add_tool(WriteFileTool(env=env))
        watchdog.add_tool(ReadFileTool(env=env))
        watchdog.add_tool(EditFileTool(env=env))
        watchdog.add_tool(GrepTool())
        watchdog.add_tool(GlobTool())
        
        print(f"\n[Launcher] Starting {args.name} ({args.role})")
        print(f"[Launcher] Mission: {mission}\n")
        
        watchdog.run(
            goal=f"The User's Mission is: {mission}",
            scenario="You are the Root Architect. Analyze the mission, design the blackboard indices, and spawn agents to execute it.",
        )
    except KeyboardInterrupt:
        print("\n[Launcher] Interrupted by user.")
    finally:
        # 7. Archive Session
        archive_session()

if __name__ == "__main__":
    main()
