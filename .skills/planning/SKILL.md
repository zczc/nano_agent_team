---
name: planning
description: "Use when you have requirements for a multi-step task and need to generate a structured plan conforming to central_plan.md format. [Scenario: General]"
---

# Planning

> **Scenario:** General — Use this skill for any multi-step task that requires coordination, whether it's coding, research, discussion, or simulation.

## Overview

Write comprehensive implementation plans assuming the executor has zero context. Document everything they need: which files to touch, exact code, verification steps. Break work into bite-sized tasks. Follow DRY, YAGNI, TDD (for coding tasks).

## When to Use

- Multi-step task requiring coordination
- Tasks that need to be delegated across multiple agents
- Complexity beyond a single `standard` task
- User asks to "plan", "design", or "brainstorm"

## Bite-Sized Task Granularity

**Each step is one action (2-5 minutes):**
- "Write the failing test" — one step
- "Run test to verify it fails" — one step
- "Implement minimal code to pass" — one step
- "Run tests to verify pass" — one step

## Output Format

Generated plans **MUST** strictly follow the `central_plan.md` template JSON format:

```json
{
  "mission_goal": "[One sentence goal]",
  "status": "IN_PROGRESS",
  "summary": null,
  "tasks": [
    {
      "id": 1,
      "type": "standard",
      "description": "[Specific task with file paths and expected outcome]",
      "status": "PENDING",
      "dependencies": [],
      "assignees": [],
      "start_time": null,
      "end_time": null,
      "artifact_link": null,
      "result_summary": null
    }
  ]
}
```

## Task Type Selection

| Scenario | `type` | Reason |
|----------|--------|--------|
| Implement a feature | `standard` | Clear deliverable, done once |
| Write tests | `standard` | Deterministic task |
| Monitor a system | `standing` | Continuous, unknown iterations |
| Discussion / review | `standing` | Multi-round interaction, DONE when consensus reached |
| Ongoing behavior (e.g. simulation) | `standing` | Unknown iterations, DONE when goal achieved |

**Remember: `standing` ≠ never ends.** When the goal is achieved, it MUST be marked `DONE`.

## Dependency Rules

- Tasks with unfulfilled dependencies MUST be `BLOCKED`
- Tasks become `PENDING` only when all dependencies are `DONE`
- Do NOT create circular dependencies

## Writing Good Descriptions

Good task descriptions include:
1. **What** — The specific action
2. **Where** — File paths
3. **How to verify** — Expected outcome

**Good:**
```
"Write test_rejects_empty_email in tests/test_login.py. Expect submit_form({'email': ''}) to return an error."
```

**Bad:**
```
"Add validation"
```

## Brainstorming Phase

If requirements are unclear, before generating a plan:

1. Discuss via `topic_brainstorm.md` on the Blackboard
2. Ask clarifying questions
3. Explore alternatives
4. Generate formal plan only after consensus

## Plan Review

Before writing to `central_plan.md`:
- Confirm each task can be executed independently
- Confirm dependency relationships are correct
- Confirm no steps are missing
- Confirm task granularity is small enough (2-5 minutes)
