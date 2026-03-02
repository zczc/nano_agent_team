---
name: architecture-decision
description: Use when facing a significant design decision - choosing between approaches, evaluating technologies, or designing system structure - before committing to an implementation path
---

# Architecture Decision

## Overview

Architectural decisions made under time pressure without structured analysis become technical debt. The cost of the wrong architecture compounds over time.

**Core principle:** Explore at least 3 options before choosing. Document why you rejected the others.

**Violating the letter of this process is violating the spirit of good design.**

## The Iron Law

```
NO ARCHITECTURAL COMMITMENT WITHOUT EXPLICIT TRADEOFF ANALYSIS
```

"We'll use X" is not a decision. "We chose X over Y and Z because [criteria] — the tradeoffs are [A, B] which we accept because [reasoning]" is a decision.

## When to Use

**Always when:**
- Choosing between implementation approaches
- Selecting a technology, library, or framework
- Designing a new system component
- Deciding on data models or API shapes
- Making choices that are hard to reverse

**Trigger questions:**
- "We could do this with X or Y"
- "What's the best way to structure this?"
- "Should we use A or build our own?"

## The Decision Process

### Phase 1: Define the Decision Context

Before evaluating options:

**1. State the problem clearly:**
```
"We need to choose how to handle distributed rate limiting
across multiple API instances."
```

**2. Identify the constraints:**
- Hard constraints (must satisfy): scale requirements, team skills, existing infrastructure
- Quality attributes (prioritize): consistency, simplicity, operability, performance
- Anti-requirements (must not): excessive complexity, new infrastructure dependencies

**3. Define success criteria:**
```
Success:
- Handles 10k req/s across 5 instances
- <5ms overhead per request
- Survives single instance failure
- Operable without Redis expertise
```

### Phase 2: Generate at Least 3 Options

**Force yourself to generate 3 distinct approaches.** If you can only think of 2, you're not thinking hard enough.

**Option generation heuristics:**
- **Simplest possible** — What's the minimum viable solution?
- **Industry standard** — What do established systems use?
- **Novel approach** — What would you build if you weren't constrained?

For each option, write:
```
## Option N: [Name]

### Description
[What it is, how it works]

### Pros
- [Specific advantage, not vague]

### Cons
- [Specific disadvantage, not vague]

### Effort
[Rough implementation effort]

### Reversibility
[How hard to undo if wrong]
```

### Phase 3: Evaluate Against Criteria

Build a decision matrix:

```
| Criterion (weight) | Option A | Option B | Option C |
|--------------------|----------|----------|----------|
| Performance (30%)  | 4/5      | 5/5      | 3/5      |
| Simplicity (25%)   | 5/5      | 2/5      | 4/5      |
| Ops burden (25%)   | 5/5      | 2/5      | 4/5      |
| Scalability (20%)  | 3/5      | 5/5      | 4/5      |
| **Weighted score** | **4.1**  | **3.4**  | **3.75** |
```

**But scores aren't the decision.** The matrix surfaces tradeoffs. Humans decide.

### Phase 4: Make and Document the Decision

**Decision record format (ADR):**

```markdown
## Decision: [Short Title]

**Date:** YYYY-MM-DD
**Status:** Decided

### Context
[Why this decision is needed. What problem it solves.]

### Options Considered

#### Option A: [Name]
[Description, pros, cons]

#### Option B: [Name]
[Description, pros, cons]

#### Option C: [Name]
[Description, pros, cons]

### Decision
**We choose Option [X].**

Reasoning:
- [Primary reason: how it best satisfies the key criteria]
- [Secondary reason]

### Tradeoffs Accepted
- [What we're giving up by choosing this option]
- [Why we accept that tradeoff]

### Consequences
- [What becomes easier because of this decision]
- [What becomes harder]
- [What follow-on decisions this forces]

### Rejected Alternatives
- **Option Y rejected because:** [Specific reason, not just "worse"]
- **Option Z rejected because:** [Specific reason]
```

Document this even when the decision seems obvious. Future agents/developers will thank you.

## Common Architectural Tradeoffs

Understanding these helps frame analysis:

| Dimension | Extreme A | Extreme B |
|-----------|-----------|-----------|
| **Consistency** | Strong consistency (slower) | Eventual consistency (faster) |
| **Coupling** | Monolith (simple ops, harder to scale) | Microservices (complex ops, easy to scale) |
| **Build vs Buy** | Custom (fits perfectly, high effort) | Library (quick, less control) |
| **Abstraction** | Thin layer (transparent) | Thick layer (convenient, opaque) |
| **Config vs Code** | Config-driven (flexible, complex) | Hardcoded (simple, rigid) |

## Red Flags - STOP and Analyze

- "Obviously we should use X" (without comparing alternatives)
- Only two options considered
- Decision made but rejected alternatives not documented
- Key constraint not stated ("assumes we have Redis")
- Reversibility not assessed ("this would be painful to undo")
- Decision made under extreme time pressure without any analysis

## Common Rationalizations

| Excuse | Reality |
|--------|---------|
| "Only one option makes sense" | You haven't looked hard enough for alternatives |
| "We don't have time to analyze" | Wrong architecture costs 10x more to fix later |
| "We can change it later" | Most architectural decisions are sticky. Be honest about reversibility. |
| "Team prefers X" | Preference isn't analysis. State it as a constraint, not a reason. |
| "Industry standard choice" | Industry standard for your scale/context? Verify. |

## Final Rule

```
State problem → Generate 3 options → Compare against criteria →
Choose with explicit reasoning → Document rejected alternatives
```

Undocumented decisions become mystery constraints. Document the why, not just the what.
