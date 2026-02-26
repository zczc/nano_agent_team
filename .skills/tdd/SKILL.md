# Skill: Test-Driven Development

## Purpose
Enforce RED-GREEN-REFACTOR workflow for code changes.

## Working Directory
You work inside the workspace copy of the project:
  `{{blackboard}}/resources/workspace/`

This is a **full copy** of the real project. Use normal relative paths.
Do NOT write to `{{root_path}}/` directly.

Run Python with:
```bash
cd {{blackboard}}/resources/workspace && PYTHONPATH={{blackboard}}/resources/workspace {{root_path}}/.venv/bin/python ...
```

## Protocol
1. **RED**: Write a failing test FIRST to `{{blackboard}}/resources/workspace/tests/test_<feature>.py`
   - Use normal project imports: `from backend.tools.foo import FooTool`
   - Run the test to confirm it fails:
     ```bash
     cd {{blackboard}}/resources/workspace && PYTHONPATH={{blackboard}}/resources/workspace {{root_path}}/.venv/bin/python -m pytest tests/test_<feature>.py -v
     ```
2. **GREEN**: Write the MINIMUM implementation to `{{blackboard}}/resources/workspace/<target_path>.py`
   - Re-run the test to confirm it passes
3. **REFACTOR**: Clean up while keeping tests green. Re-run tests.

## Test Categories
- **Import test**: `from backend.tools.foo import FooTool; print('OK')` — no syntax/import errors
- **Smoke test**: `t = FooTool(); print(t.name)` — basic instantiation works
- **Functional test**: Assert on real input/output
- **Integration test**: Use alongside existing framework components

## result_summary CHANGED_FILES (REQUIRED)
Always include this in your task result_summary:
```
CHANGED_FILES:
- backend/tools/foo.py
- tests/test_foo.py
DESCRIPTION: [what was implemented]
TEST_OUTPUT: [paste actual pytest output]
```

## Rules
- NEVER write to `{{root_path}}/` — only to `{{blackboard}}/resources/workspace/`
- NEVER commit — the Watchdog handles git
- If a test cannot be written for something, don't implement it
- Always paste real test output in result_summary, never fabricate it
