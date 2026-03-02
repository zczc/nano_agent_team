# nano_agent_team — Living System Design

> This document is maintained by the self-evolution process.
> Each PASS round appends to Evolution Changelog and updates the Component Map.
> Auditor agents read this first to avoid redundant scanning.

---

## Architecture Overview

nano_agent_team is a multi-agent swarm framework where:
- **Watchdog / Architect** — orchestrates the mission, spawns worker agents, monitors via blackboard
- **Worker agents** — spawned as separate processes, claim tasks from `central_plan.md`, execute and report
- **Blackboard** — shared state at `.blackboard/`: `global_indices/` (coordination), `resources/` (artifacts)
- **LLM Engine** — `backend/llm/engine.py`: streaming, middleware chain, tool dispatch, skill injection
- **Middlewares** — `src/core/middlewares/`: intercept LLM stream before/after each turn
- **Tools** — `backend/tools/`: registered in `tool_registry.py`, injected per agent role
- **Skills** — `.skills/<name>/SKILL.md`: activated on demand via `activate_skill` tool

### Request Flow
```
User mission
  → main.py (Watchdog + middleware chain)
    → AgentEngine.run() loop
      → LLM API (streaming)
        → StrategyMiddleware chain (WatchdogGuard, RequestMonitor, ...)
      → Tool dispatch (spawn_swarm_agent, blackboard, bash, ...)
        → Worker agent subprocess
          → AgentEngine.run() loop (sub-engine)
```

---

## Component Map

### Entry Points
| File | Purpose |
|------|---------|
| `main.py` | CLI entry: parses args, initializes Config, builds Watchdog + middleware chain |
| `evolve.sh` | Evolution loop: calls `main.py --evolution` repeatedly |
| `src/core/agent_wrapper.py` | SwarmAgent: wraps AgentEngine with blackboard, registry, tool setup |

### LLM Layer (`backend/llm/`)
| File | Purpose |
|------|---------|
| `engine.py` | Core LLM loop: streaming, tool call parsing, skill injection — **protected** |
| `providers.py` | LLMFactory: creates OpenAI/Anthropic/Gemini clients |
| `middleware.py` | StrategyMiddleware base class |
| `skill_registry.py` | Loads `.skills/*/SKILL.md`, exposes `get_skill(name)` |
| `tool_registry.py` | Registers all tools, creates per-agent tool instances |

### Middlewares (`src/core/middlewares/`)
| File | Wired Into | Purpose |
|------|-----------|---------|
| `watchdog_guard.py` | `main.py` (Watchdog only) | Enforces spawn/edit rules; injects persistence reminders |
| `request_monitor.py` | `main.py` (Watchdog only) | Tracks API request counts |
| `context_overflow.py` | `backend/llm/engine.py` | Handles context length overflow |
| `cost_tracker.py` | `main.py` (Watchdog only) | Monitors and reports token usage and estimated costs for LLM API calls |

### Tools (`backend/tools/`)
| File | Registered In | Used By |
|------|--------------|---------|
| `subagent.py` | `tool_registry.py` | Architect (legacy in-process subagent) |
| `activate_skill.py` | `tool_registry.py` | All agents |
| `evolution_workspace.py` | injected in `main.py` evolution block | Watchdog (evolution mode only) |

### Source Tools (`src/tools/`)
| File | Purpose |
|------|---------|
| `spawn_tool.py` | `spawn_swarm_agent`: spawns agent subprocess, waits for RUNNING handshake |
| `blackboard_tool.py` | `blackboard`: create/read/update/append indices |
| `check_swarm_status_tool.py` | `check_swarm_status`: reads registry.json + process liveness |
| `wait_tool.py` | `wait`: sleep + optional new-message trigger |
| `finish_tool.py` | `finish`: terminates agent loop |

### Infrastructure (`backend/infra/`)
| File | Purpose |
|------|---------|
| `envs/local.py` | LocalEnvironment: bash, file ops, safety checks, auto_approve_patterns |
| `config.py` | Config: loads `llm_config.json`, `keys.json`, `tui_state.json` |
| `provider_registry.py` | Known providers and their API base URLs |

### Skills (`.skills/`)
| Skill | Trigger | Used By |
|-------|---------|---------|
| `test-driven-development` | When implementing any feature/bugfix | Developer agent |
| `verification-before-completion` | Before claiming work is done | Tester agent |
| `systematic-debugging` | When encountering a bug or test failure | Developer agent |
| `dispatching-parallel-agents` | When multiple independent tasks exist | Architect |
| `executing-plans` | When executing a written plan | Worker agents |
| `brainstorming` | Before creative/feature work | Architect |
| `using-superpowers` | Start of any session | All agents |

---

## Known Gaps & Opportunities

*(Updated by Auditor each round. Remove entries once addressed.)*

- No persistent agent memory across rounds (each agent starts fresh)
- `evolution_workspace.py` commit message format is minimal
- No structured logging of tool call latencies

---

## Evolution Changelog

*(Each PASS round appends here. Newest at top.)*

### Round 1 — Cost Tracking Middleware (FEATURE)
**Changed**: src/core/middlewares/cost_tracker.py, src/core/middlewares/__init__.py, main.py, docs/system_design.md, tests/test_cost_tracker.py
**What it does**: Adds a middleware that monitors and reports token usage and estimated costs for LLM API calls
**Wired into**: main.py (Watchdog middleware chain) and documented in system_design.md

<!-- rounds appended below by evolution process -->
