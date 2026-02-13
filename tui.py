import os
import sys
from backend.infra.config import Config
from src.tui.app import SwarmTUI

# Ensure project root is in path
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

def main():
    # Simplified Product Launcher
    # Configuration via AuthManager (~/.nano_agent_team/auth.json) and global config.
    
    Config.initialize()
    
    # Redirect logs to dedicated TUI log file
    tui_log = os.path.join(Config.LOG_DIR, "tui.log")
    Config.LOG_PATH = tui_log
    os.makedirs(Config.LOG_DIR, exist_ok=True)
    
    # Clear previous log
    if os.path.exists(tui_log):
        with open(tui_log, 'w') as f: f.write("")
        
    print(f"[TUI] Launching Nano Agent Team Console...")
    
    app = SwarmTUI(cli_model=None)
    app.run()

if __name__ == "__main__":
    main()
