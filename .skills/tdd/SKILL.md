# Skill: Test-Driven Development

## Purpose
Enforce RED-GREEN-REFACTOR workflow for code changes.

## Protocol
1. **RED**: Write a failing test FIRST that defines the expected behavior.
   - Place tests in `tests/` directory matching source structure.
   - Use `python -m pytest` or `python -c "..."` for simple validations.
2. **GREEN**: Write the MINIMUM code to make the test pass.
   - Run the test to confirm it passes.
3. **REFACTOR**: Clean up while keeping tests green.
   - Re-run tests after any refactoring.

## Test Categories for Evolution
- **Import test**: `python -c "from module import Class"` — verifies no syntax/import errors
- **Smoke test**: `python -c "tool = NewTool(); print(tool.name)"` — verifies basic instantiation
- **Functional test**: Write a test script that exercises the core feature
- **Integration test**: Run a mini-task through the agent engine

## Rules
- Never commit code without at least an import test passing.
- If a test fails, fix the code, don't delete the test.
- All test files must be runnable via `python <test_file.py>`.
