# Skill: Verify Before Complete

## Purpose
Systematic verification checklist before marking any task as DONE.

## Verification Checklist

### 1. Syntax & Import Check
```bash
python -c "import py_compile; py_compile.compile('<file>', doraise=True)"
```
Run for EVERY modified .py file.

### 2. Dependency Check
Verify no new dependencies are needed, or they're in requirements.txt.

### 3. Functional Verification
- Instantiate the new/modified component.
- Call its primary method with sample input.
- Verify output is reasonable.

### 4. Integration Smoke Test
```bash
python -c "
from backend.infra.config import Config
Config.initialize('keys.json')
# Try to import and basic-use the changed module
"
```

### 5. Side Effect Check
- Verify no existing tests are broken.
- Verify no import cycles introduced.
- Check `git diff --stat` to confirm only expected files changed.

### 6. Report Format
Write result_summary as:
```
VERDICT: PASS|FAIL
FILES_CHECKED: [list]
TESTS_RUN: [count passed]/[count total]
ISSUES: [none or description]
```

## Rules
- NEVER mark a task DONE without running ALL checks.
- If ANY check fails, mark as FAIL with detailed error.
- Include full command output in report.
