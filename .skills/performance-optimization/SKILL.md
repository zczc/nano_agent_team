---
name: performance-optimization
description: Use when tasked with improving performance, encountering slowness complaints, or before declaring an implementation complete for performance-critical paths
---

# Performance Optimization

## Overview

Guessing at bottlenecks wastes time and creates fragile code. Measurement reveals what intuition gets wrong.

**Core principle:** ALWAYS measure before optimizing. Unverified optimization is just code churn.

**Violating the letter of this process is violating the spirit of optimization.**

## The Iron Law

```
NO OPTIMIZATION WITHOUT PROFILING FIRST
```

You cannot optimize what you haven't measured. Intuition about bottlenecks is wrong 80% of the time.

## When to Use

**Always:**
- "This is too slow" complaints
- Performance-critical feature implementation
- Before declaring latency-sensitive code complete

**Especially when:**
- Under pressure to "just make it faster"
- "Obviously" the bottleneck is X (verify first)
- After previous optimization didn't help (re-measure)

## The Four Phases

### Phase 1: Establish Baseline

Before touching any code:

**1. Define the metric:**
- What exactly is slow? (latency? throughput? memory?)
- What's the current measured value?
- What's the target?

**2. Create a reproducible benchmark:**
```bash
# Python - measure current state
python -m timeit -n 100 "your_function()"

# Or use time for scripts
time python script.py

# For web: use k6, ab, or wrk
ab -n 1000 -c 10 http://localhost:8000/endpoint
```

**3. Record the baseline:**
```
Baseline: 450ms p95 latency on /api/search with 100 concurrent users
Target: < 100ms p95
```

Never skip this. You need numbers to know if optimization worked.

### Phase 2: Profile to Find Actual Bottleneck

```bash
# Python CPU profiling
python -m cProfile -s cumulative script.py | head -30

# Python with snakeviz (visual)
python -m cProfile -o profile.prof script.py
snakeviz profile.prof

# Line-level profiling
pip install line_profiler
@profile  # decorator
kernprof -l -v script.py

# Memory profiling
pip install memory_profiler
python -m memory_profiler script.py
```

**Read the profile output:**
- Sort by `cumtime` (cumulative time) — what functions spend the most total time?
- Sort by `tottime` (total time excluding callees) — where is time actually spent?
- `ncalls` — called too many times? (N+1 problem indicator)

**What you're looking for:**
- Functions with disproportionate `tottime`
- Unexpectedly high `ncalls` (N+1 queries)
- Large memory allocations
- Repeated identical computations

### Phase 3: Identify Root Cause

Common bottleneck patterns:

**N+1 Query Problem:**
```python
# SLOW: N+1
users = User.all()
for user in users:
    profile = Profile.get(user_id=user.id)  # N extra queries

# FAST: eager load
users = User.all().prefetch_related('profile')
```

**Repeated Computation:**
```python
# SLOW: recomputes every call
def get_config():
    return json.loads(open('config.json').read())

# FAST: cache it
@lru_cache(maxsize=1)
def get_config():
    return json.loads(open('config.json').read())
```

**Missing Index:**
```sql
-- SLOW: full table scan
SELECT * FROM orders WHERE customer_email = 'user@example.com';

-- FAST: after adding index
CREATE INDEX idx_orders_email ON orders(customer_email);
EXPLAIN SELECT * FROM orders WHERE customer_email = 'user@example.com';
-- verify: should show "Index Scan" not "Seq Scan"
```

**Unnecessary Data Loading:**
```python
# SLOW: loads all fields
users = User.objects.all()
names = [u.name for u in users]

# FAST: only load what's needed
names = User.objects.values_list('name', flat=True)
```

**Synchronous I/O:**
```python
# SLOW: sequential
result1 = fetch_api_1()
result2 = fetch_api_2()

# FAST: parallel
import asyncio
result1, result2 = await asyncio.gather(fetch_api_1(), fetch_api_2())
```

### Phase 4: Optimize One Thing at a Time

**Step 1:** Fix the single biggest bottleneck identified by profiler
**Step 2:** Re-run benchmark
**Step 3:** Compare to baseline
**Step 4:** If target not met, go back to Phase 2 (re-profile with change in place)

```
Optimize ONE thing → Measure → Compare → Decide → Repeat
```

**Never:**
- Optimize multiple things simultaneously
- Optimize without re-measuring after each change
- Skip profiling after a change (the bottleneck may have shifted)

## Common Optimization Patterns

| Category | Pattern | Typical Gain |
|----------|---------|-------------|
| **Database** | Add missing index | 10-100x |
| **Database** | Fix N+1 queries (eager load) | 5-50x |
| **Database** | Select only needed columns | 2-5x |
| **Compute** | Cache repeated computation | 2-100x |
| **I/O** | Parallelize independent requests | ~Nx (N = parallelism) |
| **Memory** | Use generators instead of lists | Less memory, sometimes faster |
| **Algorithm** | Replace O(n²) with O(n log n) | n/log n factor |

## Verification Checklist

Before claiming optimization is complete:

- [ ] Baseline was measured before any changes
- [ ] Profile was run to identify actual bottleneck (not guessed)
- [ ] Only ONE thing was changed per measurement
- [ ] Post-optimization benchmark run matches methodology of baseline
- [ ] Target metric achieved (or documented why not)
- [ ] No regression in correctness (tests still pass)
- [ ] No significant increase in memory usage (check with profiler)

## Red Flags - STOP and Follow Process

- "I know the bottleneck is X" (without profiling) → Profile first
- "Just add a cache everywhere" → Cache what specifically? Based on what data?
- "This looks slow" → Measure it. Looks are wrong.
- Making multiple changes before re-measuring → Undo. Change one thing.
- Optimization made code faster in dev but not prod → Use production data/load for profiling

## Common Rationalizations

| Excuse | Reality |
|--------|---------|
| "Obvious bottleneck, no need to profile" | Profiler disagrees 80% of the time |
| "Just add caching" | Caching wrong things wastes memory and introduces bugs |
| "Multiple fixes at once saves time" | Can't tell what worked. Introduces regressions. |
| "Faster machine solves it" | That's cost scaling, not optimization |
| "Only matters at scale" | Measure at scale then. Optimize based on that data. |

## Final Rule

```
Measure baseline → Profile → Fix ONE bottleneck → Measure again → Repeat
```

No optimization without measurement. No exceptions.
