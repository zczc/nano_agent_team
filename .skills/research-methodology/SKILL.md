---
name: research-methodology
description: Use when tasked with gathering information, investigating a topic, searching the web, or producing research outputs - before starting any research task
---

# Research Methodology

## Overview

Unstructured searching produces incomplete, biased results. Methodology ensures coverage.

**Core principle:** Define what "done" looks like before you start. Otherwise you'll search forever or stop too early.

**Violating the letter of this process is violating the spirit of research.**

## The Iron Law

```
NO RESEARCH WITHOUT A DEFINED QUESTION AND DONE CRITERIA FIRST
```

"Research X" is not a research question. "Find the top 5 Python async frameworks and compare them on performance, maturity, and ecosystem" is.

## When to Use

**Always when:**
- Searching the web for information
- Investigating a technical topic
- Comparing technologies or approaches
- Gathering evidence to support a decision
- Surveying the state of a field

## The Research Process

### Phase 1: Define the Question

Before any searching:

**1. Restate the question precisely:**
```
Vague:    "Research async frameworks"
Precise:  "What are the 3 most production-ready Python async HTTP frameworks
           as of 2024, compared on: performance benchmarks, community size,
           documentation quality, and compatibility with our existing stack?"
```

**2. Define done criteria:**
- How many sources are needed for confidence?
- What specific data points are required?
- What would make the research complete?

```
Done when:
- [ ] At least 3 independent sources agree on each major finding
- [ ] Performance benchmarks from at least 2 independent tests
- [ ] Recency: all primary sources within last 2 years
- [ ] Answered all sub-questions listed below
```

**3. List sub-questions:**
Break the main question into specific questions that can be independently answered.

### Phase 2: Search Strategy

**Layer 1: Primary sources (authoritative)**
- Official documentation
- Original papers / specification documents
- Benchmarks from the project itself

**Layer 2: Independent verification**
- Third-party benchmarks
- Community discussion (GitHub issues, Reddit, Hacker News)
- Blog posts from practitioners

**Layer 3: Synthesis**
- Comparison articles
- Stack Overflow answers with high votes

**Search query patterns:**
```
Specific: "[technology] benchmark 2024"
Comparison: "[A] vs [B] production"
Critical: "[technology] limitations problems"
Recency: "[technology] site:github.com after:2023"
```

**ALWAYS search for:**
- The positive case ("X is good because...")
- The negative case ("X limitations", "X problems", "X failed")
- Recent updates (last 12-24 months)

Missing the negative case is confirmation bias.

### Phase 3: Evaluate Source Quality

For each source found:

```
Credibility check:
- Who wrote this? (expert? vendor? anonymous?)
- When? (outdated information is worse than no information)
- What's their incentive? (vendor comparison = biased)
- Is it verifiable? (can you reproduce the benchmark?)
- Do multiple independent sources agree?
```

| Source Type | Trust Level | Notes |
|-------------|-------------|-------|
| Official docs | High for features, Low for comparisons | Vendors oversell |
| Independent benchmarks (published methodology) | High | Verify methodology |
| Practitioner blog post | Medium | Check their context |
| HN/Reddit discussion | Low-Medium | Look for expert comments |
| Stack Overflow | Medium | Check votes + date |
| Vendor comparison | Low | Assume bias |

### Phase 4: Track and Cross-Reference

As you research, maintain a running record:

```markdown
## Research Log: [Question]

### Sources Reviewed
1. [URL] - [Finding] - [Quality: H/M/L] - [Date]
2. ...

### Key Findings
- Finding 1: [Source A, Source B agree]
- Finding 2: [Contested: Source A says X, Source B says Y]

### Gaps (still unanswered)
- [Sub-question not yet answered]
```

**Cross-reference rule:** Any finding that affects the conclusion needs at least 2 independent sources.

### Phase 5: Synthesize and Report

Structure the output by **insight, not by source**:

```
BAD (source-organized):
"According to Source A, ... According to Source B, ..."

GOOD (insight-organized):
"FastAPI outperforms Flask by 3-4x on throughput (Sources: TechEmpower Round 22, Pydantic benchmark 2023)"
```

**Report format:**
```markdown
## Research: [Question]

### Key Findings
1. [Most important finding] — [evidence]
2. [Second finding] — [evidence]
3. ...

### Detailed Analysis
[Evidence for each finding]

### Contradictions / Uncertainty
[Where sources disagree and why]

### Gaps
[What wasn't found, what remains uncertain]

### Sources
[Full list with dates]
```

## Common Research Traps

| Trap | Prevention |
|------|-----------|
| **Confirmation bias** | Actively search for the negative case |
| **Availability bias** | Don't stop at first results. Search multiple angles. |
| **Recency bias** | Check if old but foundational sources still apply |
| **Authority fallacy** | Vendor says it's best? Verify independently. |
| **Shallow research** | Define done criteria before starting |
| **Lost in breadth** | Sub-questions keep research focused |

## Red Flags - STOP and Restructure Research

- Starting to search without defining done criteria
- Only finding positive information about any option
- All sources from the same vendor/community
- Key sub-questions still unanswered but marking research "done"
- Findings from sources older than 2 years for fast-moving tech
- A single source for a critical finding

## Common Rationalizations

| Excuse | Reality |
|--------|---------|
| "This seems comprehensive" | Seems ≠ is. Check against done criteria. |
| "I've been researching long enough" | Time spent ≠ questions answered. |
| "Found no criticism so it must be good" | You didn't search hard enough for criticism. |
| "One source is definitive" | One source is a starting point. |

## Final Rule

```
Define question → Define done criteria → Search both sides → Cross-reference → Synthesize
```

Research without defined done criteria loops forever or stops too soon. Define the end before the beginning.
