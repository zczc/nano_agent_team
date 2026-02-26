# Self-Evolution Loop Implementation Plan

## Context

为 nano_agent_team 添加自进化模式：系统使用自身的 Swarm 机制分析自己、搜索优化方向、开发改进、测试验证、自动回滚，循环往复。每轮输出总结报告供人查看。

核心约束：尽量复用现有框架，最小化新增代码。唯一的"框架外"代码是一个 shell 重启循环。

**调研结论**：参考了 Superpowers (62K stars) 的 TDD + verification-before-completion 模式，以及 OpenSpec 的 spec-driven 开发流程。从中提取两个关键 skill 集成到框架中，让进化过程更稳定。

---

## 需要新增/修改的文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `evolve.sh` | 新增 | Shell 重启循环（~20行） |
| `src/prompts/evolution_architect.md` | 新增 | 进化专用 Watchdog prompt（核心） |
| `.skills/tdd/SKILL.md` | 新增 | TDD skill（借鉴 Superpowers） |
| `.skills/verify-before-complete/SKILL.md` | 新增 | 完成前验证 skill（借鉴 Superpowers） |
| `main.py` | 修改 | 添加 `--evolution` flag |
| `src/core/middlewares/watchdog_guard.py` | 修改 | 进化模式跳过 ask_user 门控 |
| `backend/infra/envs/local.py` | 修改 | 进化模式自动批准 git 命令 |

总计：4 个新文件 + 3 个小改动

---

## Step 1: `backend/infra/envs/local.py` — 自动批准模式

**文件**: `backend/infra/envs/local.py`

**问题**：`git ` 在 `dangerous_tokens` 列表中，每次 git 操作都弹确认，进化模式无人值守会卡住。

**改动**：`__init__` 新增 `auto_approve_patterns` 参数，`_check_safety` 开头增加白名单检查。

```python
# __init__ 新增参数
def __init__(self, ..., auto_approve_patterns=None):
    self.auto_approve_patterns = auto_approve_patterns or []

# _check_safety 开头增加
def _check_safety(self, command, cwd):
    for pattern in self.auto_approve_patterns:
        if command.strip().startswith(pattern):
            return True
    # ... 原有逻辑不变
```

只在进化模式下传入 `auto_approve_patterns=["git "]`，普通模式完全不受影响。

---

## Step 2: `src/core/middlewares/watchdog_guard.py` — 跳过人工审批

**文件**: `src/core/middlewares/watchdog_guard.py`

**问题**：Rule A 要求 spawn 前必须先 ask_user 验证 plan，进化模式需跳过。

**改动**：构造函数增加 `skip_user_verification=False`。

```python
def __init__(self, agent_name, blackboard_dir, critical_tools,
             skip_user_verification=False):
    self.skip_user_verification = skip_user_verification
    # ... 原有初始化

# 在 _guard_stream 中：
has_verified_plan = self.skip_user_verification  # 预设为 True 则跳过 ask_user 门控
```

---

## Step 3: `main.py` — `--evolution` 模式

**文件**: `main.py`

**改动点**（约 40 行新增）：

### 3a. 新增参数
```python
parser.add_argument("--evolution", action="store_true",
                    help="Run in self-evolution mode")
```

### 3b. 进化模式分支（在 main 函数中，加载 prompt 之前）
```python
if args.evolution:
    prompt_path = os.path.join(project_root, "src/prompts/evolution_architect.md")

    # 读取进化状态
    state_path = os.path.join(project_root, "evolution_state.json")
    if os.path.exists(state_path):
        with open(state_path) as f:
            evo_state = json.load(f)
        round_num = evo_state.get("round", 0) + 1
    else:
        round_num = 1
        evo_state = {"round": 0, "history": [], "failures": []}

    mission = (
        f"Self-Evolution Round {round_num}.\n\n"
        f"Evolution History (last 5 rounds):\n"
        f"{json.dumps(evo_state.get('history', [])[-5:], indent=2, ensure_ascii=False)}\n\n"
        f"Past Failures to Avoid:\n"
        f"{json.dumps(evo_state.get('failures', [])[-10:], indent=2, ensure_ascii=False)}\n\n"
        f"Analyze the framework, find an improvement, implement it, test it, "
        f"and write a report to evolution_reports/."
    )
else:
    prompt_path = os.path.join(project_root, "src/prompts/architect.md")
    # ... 原有逻辑
```

### 3c. 进化模式的中间件和环境配置
```python
watchdog_guard = WatchdogGuardMiddleware(
    agent_name=args.name,
    blackboard_dir=blackboard_dir,
    critical_tools=["spawn_swarm_agent"],
    skip_user_verification=args.evolution  # 进化模式跳过
)

env = LocalEnvironment(
    workspace_root=project_root,
    blackboard_dir=blackboard_dir,
    agent_name=args.name,
    auto_approve_patterns=["git "] if args.evolution else []  # 进化模式自动批准 git
)
```

### 3d. 进化模式 Watchdog 的 run 参数
```python
if args.evolution:
    watchdog.run(
        goal=f"The Evolution Mission is:\n{mission}",
        scenario="You are the Evolution Architect. Follow the evolution protocol strictly.",
        critical_tools=["spawn_swarm_agent"]
    )
else:
    # 原有逻辑
```

---

## Step 4: `.skills/tdd/SKILL.md` — TDD Skill（借鉴 Superpowers）

**文件**: `.skills/tdd/SKILL.md`（新增）

为 Developer agent 提供测试驱动开发指导。当进化 Watchdog spawn Developer 时，可通过 `activate_skill` 注入此 skill。

核心内容：RED-GREEN-REFACTOR 工作流，包含 Import test、Smoke test、Functional test、Integration test 四个层级。

---

## Step 5: `.skills/verify-before-complete/SKILL.md` — 完成前验证 Skill

**文件**: `.skills/verify-before-complete/SKILL.md`（新增）

为 Tester agent 提供系统化验证流程，包含：
1. Syntax & Import Check
2. Dependency Check
3. Functional Verification
4. Integration Smoke Test
5. Side Effect Check
6. Report Format (VERDICT: PASS|FAIL)

---

## Step 6: `src/prompts/evolution_architect.md` — 进化 Watchdog 核心 Prompt

**文件**: `src/prompts/evolution_architect.md`（新增，核心文件）

定义完整进化行为：
- Phase 1: Research & Decide（分析代码库，web_search 寻找灵感，选择一个改进方向）
- Phase 2: Plan & Execute（创建 central_plan，git branch 隔离，spawn Developer + Tester）
- Phase 3: Judge & Report（根据 Tester 的 VERDICT 决定 merge 或 rollback）
- Phase 3.5: Recovery Protocol（任何错误都回退到 main，记录失败）

包含：
- Protected Files 列表
- Per-Round Limits
- Agent Role Templates（Developer + Tester）
- Evolution State Protocol
- Evolution Report Template

---

## Step 7: `evolve.sh` — 重启循环

**文件**: `evolve.sh`（新增）

Shell 脚本实现：
- 接受 max_rounds 参数（默认 20）
- while 循环执行 `python main.py --evolution`
- 检查 `.evolution_stop` 文件实现优雅停止
- 每轮间 5 秒 cooldown
- 输出时间戳和状态信息

---

## 实施顺序

1. `backend/infra/envs/local.py` — +auto_approve_patterns
2. `src/core/middlewares/watchdog_guard.py` — +skip_user_verification
3. `main.py` — +--evolution 模式
4. `.skills/tdd/SKILL.md` — TDD skill
5. `.skills/verify-before-complete/SKILL.md` — 验证 skill
6. `src/prompts/evolution_architect.md` — 进化 prompt
7. `evolve.sh` — 启动脚本

## 验证

1. `python main.py --evolution` 能正常启动
2. 手动跑一轮，观察 evolution_state.json 和 evolution_reports/ 产出
3. `bash evolve.sh 3` 跑 3 轮看完整循环
4. `touch .evolution_stop` 验证能停止
