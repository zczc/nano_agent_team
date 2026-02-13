
import os
from typing import List, Dict
from .protocol import parse_frontmatter
import platform
import sys
import datetime

class PromptBuilder:
    """
    Builds the System Prompt dynamically based on:
    1. Core Swarm Capabilities
    2. Active Global Indices (Self-describing protocols)
    3. Agent Role & Scenario
    """
    
    def __init__(self, blackboard_dir: str):
        self.blackboard_dir = blackboard_dir
        self.indices_dir = os.path.join(blackboard_dir, "global_indices")

    def build(self, role_definition: str, scenario_context: str = "") -> str:
        """
        Constructs the full system prompt.
        """
        sections = [
            self._get_core_prompt(),
            self._get_system_context(),
            self._get_indices_prompt(),
            self._get_templates_prompt(),
            self._get_role_prompt(role_definition),
            self._get_scenario_prompt(scenario_context)
        ]
        return "\n\n".join([s for s in sections if s])

    def _get_core_prompt(self) -> str:
        return """
# CORE CAPABILITIES
You are an autonomous AI Agent operating within a Swarm.
Your primary environment is the local file system, specifically the `{{blackboard}}` directory.
You interact with other agents and the system by reading and writing files.

## CRITICAL BEHAVIORAL GUIDELINES
1. **STRICT ROLE ADHERENCE**:
   - You MUST ONLY perform tasks assigned to your specific Role.
   - Do NOT try to do everything (e.g., if you are a Planner, do not write code; if you are a Coder, do not update the high-level plan).
   - **Finishing**: When a measure is DONE, update status to "DONE".
     - **MUST** provide `result_summary` in the update: A short string describing the outcome.
     - **MUST** provide `artifact_link` if you produced a file (pointing to `resources/`).
   - If you have no active tasks in your subscribed indices: **CALL `WaitTool(duration≤15)`**. Do not hallucinate tasks.

2. **Blackboard Usage & Directory Semantics**:
   - All communication MUST happen via the Blackboard.
   - **`global_indices/` (Communication Layer)**: 
     - Use this for Shared State, Plans, and Coordination Signals. produces **Metadata**.
     - **PROTOCOL**: Every file here MUST start with a YAML frontmatter containing:
     -- `name`: "The title of the index file, used for display in `list_indices`."
     -- `description`: "A description of the file's purpose, helping other agents understand the intent of this channel."
     -- `usage_policy`: "Usage protocol. Defines how agents should interact with this file (e.g., who writes, when to read, what format to follow)."
     - **CRITICAL TIP**: If any YAML value contains special characters (like `:`, `[`, `]`, `-`, or `#`), you **MUST** wrap the entire value in double quotes to ensure correct parsing.
     - **Example**:
     ```
     ---
     name: "My_Channel"
     description: "Discussing the progress of task X."
     usage_policy: "Architect creates tasks; executors update status."
     ---
     # Body starts here...
     ```
     - If you want other agents to SEE your message, you **MUST** write to a file here using this format.
   - **`resources/` (Storage Layer)**: 
     - Use this for Heavy Artifacts, Code Files, Data Dumps, and Reports. produces **Data**.
     - **Protocol ("Indices point to Resources")**: Never put large content directly in an index. Instead, verify create a file in `resources/` and then **link** to it in an index (e.g., in `result_summary`).
     - Agents will NOT see files in `resources` unless pointed to them from an active index.

3. **Concurrency & Safe Updates**:
   - The file system is shared. **NEVER** overwrite shared indices (like `central_plan.md`) blindly.
   - **Protocol**:
     1. Read the index using `operation="read_index"` to get the current content and `checksum`.
     2. Modify the content locally.
     3. Write back using `operation="update_index"`, providing `content` and `expected_checksum`.
     4. If you receive a CAS Error, you **MUST** repeat the loop (Read -> Merge -> Update).

## COMMUNICATION PROTOCOL (MANDATORY)

1. **THE NOTIFICATION RULE**:
   - **Context**: You will see `## RECENT NOTIFICATIONS` in your context. This is the live heartbeat of the swarm.
   - **Action**: Check it every turn. If you see your name (`@MyRole`) or a topic you are working on, respond with priority.
   - **Logging**: After finishing a task or reaching a milestone, append a **Work Report** to `global_indices/notifications.md` using the `append_to_index` operation.
     - **No CAS Needed**: Notifications is an append-only stream. Do NOT use `update_index` for this.
     - **Quality**: Focus on **Task Achievements**, not tool names. 
     - **Outputs**: If you created/modified files, MUST include the **Absolute Path** and **Content Description**.
     - **Format**: `"[Time] [Role] [Action] Summary @Target"`
     - **Fields**:
       - `[Time]`: HH:MM:SS
       - `[Role]`: Your name/role
       - `[Action]`: Progress state (e.g., Finished, Request, Blocked)
       - `Summary`: Achievement-oriented report (include file paths!).
       - `@Target`: Optional. Mention other agents (e.g., `@Coder`) to request action or coordinate steps.
     - **Coordination**: Use the notification stream to tell other agents what you need from them or what they should do next using `@mention`.

2. **THE STORAGE RULE (Hot vs. Cold)**:
   - **Hot (Discussion/Plans)**: Write to `global_indices/`. Use `topic_{name}.md` for discussions.
   - **Cold (Artifacts/Data)**: Long content (Code, Reports, Data) MUST go to `resources/`. Then post a Link in the Hot zone.
   - **Never** dump huge raw data into `notifications.md` or `central_plan.md`.

3. **THE DISCUSSION RULE**:
   - When discussing, use the `global_indices/topic_*.md` files.
   - Always append your thoughts with your Role/Name prefix: `**[Role]**: My opinion is...`

4. **Loop & Wait**:
   - You exist in a continuous loop. If blocked, wait.

5. **File Path Consistency**:
   - Extremely Important: All file addresses, names, and paths MUST be absolutely consistent and correct.
   - Use absolute paths.
   - Do NOT arbitrarily change file names or paths.
   - When referencing a file, ensure it actually exists at that path.

6. **Agent Spawning Restriction**:
   - **ONLY** the Architect (Watchdog) can spawn new agents.
   - Ordinary agents (Coder, Reviewer, etc.) are **STRICTLY PROHIBITED** from calling `spawn_swarm_agent` or attempting to expand the swarm.
   - If you need more help, report a Block or Request on the Blackboard for the Architect to handle.
""".strip()

    def _get_system_context(self) -> str:
        """Injects dynamic system information."""
        now = datetime.datetime.now()
        
        info = [
            "## SYSTEM CONTEXT",
            f"- **Current Time**: {now.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- **Day of Week**: {now.strftime('%A')}",
            f"- **Operating System**: {platform.system()} {platform.release()} ({sys.platform})",
            f"- **Python Version**: {sys.version.split()[0]}",
            f"- **Working Directory**: {os.getcwd()}"
        ]

    def _get_indices_prompt(self) -> str:
        if not os.path.exists(self.indices_dir):
            return "## GLOBAL INDICES\nNo active global indices found."

        descriptions = []
        
        for fname in os.listdir(self.indices_dir):
            if fname.endswith(".md"):
                fpath = os.path.join(self.indices_dir, fname)
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        content = f.read(2048) # Read header only
                        meta, _ = parse_frontmatter(content)
                        
                        # Only include relevant indices
                        # (Implicitly all valid indices in this dir are relevant)
                        name = meta.get("name", fname)
                        desc = meta.get("description", "No description provided.")
                        policy = meta.get("usage_policy", "Read usage in file.")
                        schema = meta.get("schema", "Standard Markdown")
                        
                        entry = (
                            f"### {name} ({fname})\n"
                            f"- Description: {desc}\n"
                            f"- Usage Policy: {policy}\n"
                            f"- Schema: {schema}"
                        )
                        descriptions.append(entry)
                except Exception:
                    continue
        
        if not descriptions:
            return "## GLOBAL INDICES\nNo valid indices found."
            
        return "## GLOBAL INDICES & PROTOCOLS\nThe following channels are active in `{{blackboard}}/global_indices/`. You MUST follow their usage policies:\n\n" + "\n\n".join(descriptions)

    def _get_templates_prompt(self) -> str:
        templates_dir = os.path.join(self.blackboard_dir, "../blackboard_templates")
        if not os.path.exists(templates_dir):
            return ""
        
        templates = [f for f in os.listdir(templates_dir) if f.endswith(".md")]
        if not templates:
            return ""
            
        return "## AVAILABLE TEMPLATES\nThe following templates are available in `{{root_path}}/blackboard_templates/`. Use `blackboard_tool.read_template(name)` to read them:\n" + "\n".join([f"- {t}" for t in templates])

    def _get_role_prompt(self, role: str) -> str:
        return f"# YOUR ROLE\n{role}"

    def _get_scenario_prompt(self, scenario: str) -> str:
        if not scenario:
            return ""
        return f"# CURRENT SCENARIO\n{scenario}"
