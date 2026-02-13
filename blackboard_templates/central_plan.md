---
name: Central Mission Plan
description: "JSON-structured Task Graph for the agent team."
usage_policy:
  - "Watchdog/Architect: Manage the JSON. Add/modify tasks, set dependencies. Resolve Blockers. When mission is DONE, write a final summary."
  - "Task Decomposition Guidelines": |
      - **Granularity**: Break down tasks into specific, actionable units. Avoid vague descriptions.
      - **Constraints**: Explicitly state any constraints or requirements in the task description.
      - **Completeness**: Ensure that the combination of all subtasks fully achieves the parent goal and satisfies all original requirements.
  - other Agents (Protocol): |
      1. **Task Types**:
         - `standard`: Deterministic task with clear deliverable. Single assignee. Once DONE, it's finished.
         - `standing`: Iterative task with unknown number of iterations. Supports multiple `assignees`. Can (and should) be marked DONE when its goal is achieved. Examples: monitoring, discussions, ongoing behaviors.
      2. **Statuses**:
         - **Mission `status`** (top-level):
           - `IN_PROGRESS`: Mission is active, agents should work on tasks.
           - `DONE`: Mission is complete. All agents should stop and Architect can finish.
         - **Task `status`**:
           - `PENDING`: Ready to be claimed. All dependencies are DONE.
           - `IN_PROGRESS`: Claimed and actively being worked on.
           - `BLOCKED`: Cannot start. Has unfulfilled dependencies (at least one dependency is not DONE).
           - `DONE`: Task is complete. `result_summary` MUST be provided.
      3. **Claiming**:
         - **Protocol**: Poll `central_plan.md` using `read_index`.
         - **Selection**: Find a PENDING task that matches your **Role Capabilities**.
         - **Condition**: ALL dependencies must be DONE.
         - **Status Rule**: Tasks with unfulfilled dependencies MUST be `BLOCKED`. A task becomes `PENDING` only when all its dependencies are `DONE`.
         - **Action**: Use `update_task(task_id=..., updates={"status": "IN_PROGRESS", "assignees": ["Me"]}, expected_checksum=...)`.
         - Do NOT use `update_index` for claiming.
      4. **Working**:
         - Perform the task. Log progress in `primary_timeline.md`.
      5. **Finishing**:
         - Set status="DONE", end_time=Now, artifact_link="..." via `update_task`.
         - **MUST** provide `result_summary`: A concise outcome of the task (e.g., "Verified X found Y issues", "Implemented Z").
      6. **Concurrency**:
         - Always provide `expected_checksum` obtained from a fresh `read_index`.
         - If CAS fails, re-read and retry.
schema: JSON Code Block
---

# Mission Plan (JSON)

```json
{
  "mission_goal": "[High-level Goal]",
  "status": "IN_PROGRESS",
  "summary": null,
  "tasks": [
    {
      "id": 1,
      "type": "standard",
      "description": "Initial Analysis",
      "status": "PENDING",
      "dependencies": [],
      "assignees": [],
      "start_time": null,
      "end_time": null,
      "artifact_link": null,
      "result_summary": null
    },
    {
      "id": 2,
      "type": "standard",
      "description": "Next Step",
      "status": "BLOCKED",
      "dependencies": [1],
      "assignees": [],
      "start_time": null,
      "end_time": null,
      "artifact_link": null,
      "result_summary": null
    }
  ]
}
```
