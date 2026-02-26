# Skill: Verify Before Complete

## Purpose
Systematic verification checklist before marking any task as DONE.

## Working Directory
All checks run inside the workspace copy:
  `{{blackboard}}/resources/workspace/`

Use this Python command pattern for ALL checks:
```bash
cd {{blackboard}}/resources/workspace && PYTHONPATH={{blackboard}}/resources/workspace {{root_path}}/.venv/bin/python ...
```

## Verification Checklist

### 0. Python File Gate (FIRST — fail fast)
Check that CHANGED_FILES contains at least one `.py` file.
If CHANGED_FILES contains ONLY non-Python files (markdown, JSON, shell, etc.):
→ **VERDICT: FAIL** immediately. Report: "No Python files changed — invalid evolution improvement."
Do NOT proceed to further checks.

### 1. Confirm Changed Files Exist
Read Developer's result_summary to get the `CHANGED_FILES` list.
For each file, verify it exists:
```bash
ls -la {{blackboard}}/resources/workspace/<file>
```

### 2. Syntax & Import Check
For EVERY `.py` file in CHANGED_FILES:
```bash
cd {{blackboard}}/resources/workspace && PYTHONPATH={{blackboard}}/resources/workspace {{root_path}}/.venv/bin/python -c "import py_compile; py_compile.compile('<file>', doraise=True); print('syntax OK')"
```

### 3. Import Smoke Test
```bash
cd {{blackboard}}/resources/workspace && PYTHONPATH={{blackboard}}/resources/workspace {{root_path}}/.venv/bin/python -c "from <module> import <Class>; print('import OK')"
```

### 4. Functional Verification
Instantiate and call the new/modified component:
```bash
cd {{blackboard}}/resources/workspace && PYTHONPATH={{blackboard}}/resources/workspace {{root_path}}/.venv/bin/python -c "
from <module> import <Class>
obj = <Class>()
result = obj.<primary_method>(<sample_input>)
assert result is not None, 'got None'
print('functional OK:', result)
"
```

### 5. Run Test Files
If Developer created test files, run them yourself — do NOT trust Developer's self-reported output:
```bash
cd {{blackboard}}/resources/workspace && PYTHONPATH={{blackboard}}/resources/workspace {{root_path}}/.venv/bin/python -m pytest tests/test_<feature>.py -v 2>&1
```

### 6. No Side-Effect Check
Verify only expected files were modified:
```bash
diff -rq --exclude='__pycache__' --exclude='*.pyc' \
  {{blackboard}}/resources/workspace/src {{root_path}}/src
diff -rq --exclude='__pycache__' --exclude='*.pyc' \
  {{blackboard}}/resources/workspace/backend {{root_path}}/backend
```
Only the CHANGED_FILES from Developer's summary should appear. Any unexpected diff is a red flag.

### 7. Report Format
Write result_summary as:
```
VERDICT: PASS|FAIL
FILES_CHECKED: [list]
COMMANDS_RUN:
  - [command 1]
  - [command 2]
TESTS_RUN: [N passed]/[N total]
ACTUAL_OUTPUT:
  [paste real command output here]
ISSUES: [none | detailed error]
```

## Rules
- NEVER mark DONE without running ALL checks
- NEVER trust Developer's self-reported test results — always re-run yourself
- If ANY check fails → VERDICT: FAIL with full error output
- Always paste REAL command output, never summarize or fabricate
- Use `.venv/bin/python` via `{{root_path}}/.venv/bin/python`, not bare `python`
- If CHANGED_FILES has no `.py` files → VERDICT: FAIL (gate 0 triggers)
