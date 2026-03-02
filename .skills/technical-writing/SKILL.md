---
name: technical-writing
description: Use when producing any deliverable for human consumption - reports, documentation, analysis summaries, READMEs, or research outputs - before marking the writing task complete
---

# Technical Writing

## Overview

Technical writing that experts can't act on is wasted compute. Clarity beats completeness.

**Core principle:** Write for the reader, not the writer. If they can't use it, it failed.

**Violating the letter of this process is violating the spirit of communication.**

## The Iron Law

```
NO DELIVERABLE COMPLETE WITHOUT THE "SO WHAT" TEST
```

Every paragraph must answer: "What should the reader do or know because of this?" If it can't, cut it.

## When to Use

**Always when producing:**
- Task completion reports
- Research summaries
- Technical specifications
- API documentation
- README files
- Analysis outputs
- Decision documents

## The Writing Process

### Phase 1: Identify Reader and Purpose

Before writing a single word:

**1. Who reads this?**
- Developer implementing based on it?
- Manager making a decision?
- User learning to use something?
- Agent picking up context?

**2. What do they need to DO after reading?**
- Implement a feature? → They need exact specs, file paths, code examples
- Make a decision? → They need options, tradeoffs, a recommendation
- Use a tool? → They need quick start + reference
- Continue the work? → They need current state + next steps

**3. What's the key takeaway in one sentence?**
Write this first. If you can't write it, you don't understand the content yet.

### Phase 2: Structure Before Writing

```
Good structure:
1. What this is (1-2 sentences)
2. The key finding/result/recommendation (upfront, not buried)
3. Supporting details
4. How to use this / next steps

Bad structure:
1. Background
2. More background
3. What we did
4. What we found (buried on page 5)
5. Conclusion buried at the end
```

**The Inverted Pyramid:** Put the most important information first. Details later.

```
┌─────────────────────────────┐
│ KEY FINDING / RESULT (top)  │  ← Most important, always first
├─────────────────────────────┤
│ Supporting evidence          │  ← Why it's true
├─────────────────────────────┤
│ Details / methodology        │  ← For those who need depth
└─────────────────────────────┘
```

### Phase 3: Write Clearly

**Precision over eloquence:**
```
Vague:   "The performance was significantly improved"
Precise: "Latency dropped from 450ms to 85ms (81% reduction)"

Vague:   "Some files need to be updated"
Precise: "Modify src/auth.py:45 to add the permission check"

Vague:   "There may be issues with this approach"
Precise: "This approach fails when input exceeds 1MB; tested at 1.1MB"
```

**Active voice:**
```
Passive: "The test was failed by the module"
Active:  "The auth module fails the test when token is expired"
```

**Specific numbers:**
```
Weak:  "Much faster"
Strong: "3.2x faster (baseline: 450ms, after: 140ms)"
```

**Concrete examples over abstract descriptions:**
```
Abstract: "Handle the error case appropriately"
Concrete: "If the API returns 429, wait 60 seconds and retry once.
           If it fails again, log the error and mark the task FAILED."
```

### Phase 4: Format for Scannability

Technical readers scan before reading. Format accordingly:

**Use headers** to let readers jump to relevant sections:
```markdown
## Problem
## Solution
## Implementation
## Test Results
```

**Use lists** for steps, options, or items (not paragraphs):
```markdown
The system does three things:
- Validates input
- Calls the API
- Writes the result to disk
```

**Use code blocks** for all code, commands, file paths, and exact values:
```
The config lives at `~/.config/app/settings.json`
Run: `python main.py --mode=production`
```

**Use tables** for comparisons:
```markdown
| Approach | Pros | Cons | Recommended? |
|----------|------|------|-------------|
| A        | ...  | ...  | Yes         |
```

### Phase 5: The "So What" Test

Read each paragraph and ask: "So what?"

```
"We implemented the authentication module using JWT tokens." → So what?
"The JWT tokens expire after 24 hours." → So what?
"Short expiry limits the window for stolen token abuse." → OK, this answers "so what"
```

If a paragraph can't answer "so what?" — either add the implication or delete the paragraph.

## Document Type Patterns

### Research/Analysis Report
```markdown
## Summary
[1-3 bullet points of key findings — FIRST]

## Findings
[Detailed results with evidence]

## Implications
[What this means for the project/decision]

## Recommended Next Steps
[Specific, actionable]
```

### Technical Specification
```markdown
## Goal
[One sentence: what this builds and why]

## Requirements
[Explicit list: must / should / won't]

## Design
[How it works, with diagrams/code where needed]

## Edge Cases
[Explicit list of known edge cases and handling]

## Open Questions
[What's unresolved, who decides]
```

### README / Documentation
```markdown
## What This Is
[One sentence]

## Quick Start
[Minimal working example — runnable immediately]

## Usage
[Common use cases with examples]

## Reference
[Complete API / config / options]
```

### Task Completion Report
```markdown
## What Was Done
[Bullet list of completed items]

## Artifacts Produced
[File paths, links to outputs]

## Test Results
[Pass/fail counts, commands run]

## Known Issues / Limitations
[Honest list, don't hide problems]

## Next Steps
[What should happen next]
```

## Quality Checklist

Before marking writing task complete:

- [ ] Key finding/result appears in the first 3 sentences
- [ ] Every claim has supporting evidence or a specific example
- [ ] All numbers are specific (not "significantly", "much faster", "some")
- [ ] File paths, commands, and code are in code blocks
- [ ] Each section has a clear purpose (the "so what" test)
- [ ] No unexplained jargon for the target audience
- [ ] Actionable next steps are explicit

## Red Flags - STOP and Revise

- Key finding buried after 3+ paragraphs of context
- "This could potentially maybe have issues" (no specifics)
- Numbers like "much", "significantly", "greatly" without values
- Passive voice hiding who does what
- "See above" or "as mentioned" (force reader to hunt)
- Section exists but reader can't act on it

## Common Rationalizations

| Excuse | Reality |
|--------|---------|
| "Reader can figure it out" | Your job is to make figuring out unnecessary |
| "More detail = more thorough" | Padding buries the signal. Cut mercilessly. |
| "Technical audience doesn't need examples" | Technical audiences are busiest. Examples save their time. |
| "I'll clean it up later" | Documents don't get cleaned up later. Do it now. |

## Final Rule

```
Identify reader → Lead with key insight → Evidence → Action items
```

If the reader can't act on it, the document failed. Revise until they can.
