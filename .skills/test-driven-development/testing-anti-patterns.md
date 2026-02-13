# Testing Anti-Patterns

**Load this reference when:** writing or changing tests, adding mocks, or tempted to add test-only methods to production code.

> **Scenario:** Coding — This is a companion reference for the `test-driven-development` skill.

## Overview

Tests must verify real behavior, not mock behavior. Mocks are a means to isolate, not the thing being tested.

**Core principle:** Test what the code does, not what the mocks do.

## The Iron Laws

```
1. NEVER test mock behavior
2. NEVER add test-only methods to production classes
3. NEVER mock without understanding dependencies
```

## Anti-Pattern 1: Testing Mock Behavior

**Wrong:**
```python
# ❌ BAD: Testing that the mock was called
def test_calls_service(mocker):
    mock_service = mocker.patch("app.service.call")
    process_request({"data": "test"})
    mock_service.assert_called_once()  # Only verifies mock was called
```

**Right:**
```python
# ✅ GOOD: Test real behavior
def test_processes_request_correctly():
    result = process_request({"data": "test"})
    assert result["status"] == "success"
    assert result["processed_data"] is not None
```

## Anti-Pattern 2: Test-Only Methods in Production

**Wrong:**
```python
# ❌ BAD: destroy() only used in tests
class Session:
    def destroy(self):  # Looks like production API!
        self.cleanup_workspace()
```

**Right:**
```python
# ✅ GOOD: Test utilities handle cleanup
# Session has no destroy() — stateless in production

# In test_utils.py
def cleanup_session(session):
    workspace = session.get_workspace_info()
    if workspace:
        workspace_manager.destroy(workspace["id"])
```

## Anti-Pattern 3: Mocking Without Understanding

### Gate Function

```
BEFORE mocking any method:
  STOP — Don't mock yet

  1. Ask: "What side effects does the real method have?"
  2. Ask: "Does this test depend on any of those side effects?"
  3. Ask: "Do I fully understand what this test needs?"

  IF depends on side effects:
    Mock at lower level (the actual slow/external operation)
    NOT the high-level method the test depends on

  IF unsure what test depends on:
    Run test with real implementation FIRST
    Observe what actually needs to happen
    THEN add minimal mocking at the right level
```

## Anti-Pattern 4: Incomplete Mocks

```
BEFORE creating mock responses:
  Check: "What fields does the real API response contain?"

  Actions:
    1. Examine actual API response from docs/examples
    2. Include ALL fields system might consume downstream
    3. Verify mock matches real response schema completely
```

## Quick Reference

| Anti-Pattern | Fix |
|--------------|-----|
| Assert on mock elements | Test real component or unmock it |
| Test-only methods in production | Move to test utilities |
| Mock without understanding | Understand dependencies first, mock minimally |
| Incomplete mocks | Mirror real API completely |
| Tests as afterthought | TDD — tests first |

## Red Flags

- Mock setup is >50% of test code
- Test fails when you remove mock
- Can't explain why mock is needed
- Mocking "just to be safe"

## The Bottom Line

**Mocks are tools to isolate, not things to test.**

If TDD reveals you're testing mock behavior, you've gone wrong. Fix: Test real behavior or question why you're mocking at all.
