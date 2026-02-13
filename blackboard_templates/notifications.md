---
name: "SWARM NOTIFICATION STREAM"
description: "Real-time activity log and work report stream for the swarm."
usage_policy:
  - "Frequency: Append a report whenever a sub-task/milestone is reached."
  - "Content Style: Focus on ACHIEVEMENTS and PROGRESS (Work Report style) rather than low-level tool calls."
  - "Output Rule: If you created or modified a file, you MUST include the absolute path and a brief description of what was changed."
  - "Format: '[Time] [Role] [Action] Summary @Target'"
  - "Field Definitions:"
    - "[Time]: Current timestamp (HH:MM:SS)."
    - "[Role]: Your defined role (e.g., Coder, Architect)."
    - "[Action]: Short state verb (e.g., Finished, Blocked, Analysis, Thinking)."
    - "Summary: Achievement-oriented work report. Focus on progress and outputs."
    - "@Target: Optional. Use '@Role' to communicate directly with another agent or request their attention."
  - "Communication: Use '@AgentName' in the summary to suggest next steps or request specific actions from peers."
---
## SWARM NOTIFICATION STREAM
<!-- Example: [14:05:23] [Coder] [Finished] Refactored auth logic in /path/to/auth.py. Added login validation. -->
