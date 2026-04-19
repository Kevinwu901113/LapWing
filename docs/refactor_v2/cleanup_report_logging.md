# Cleanup Report — Logging System Overhaul

> Branch: `refactor/logging-overhaul`
> Base: `b1c260e` (Step 7 complete)
> Date: 2026-04-19
> Backup: `~/lapwing-backups/pre_logging_overhaul_20260419_170430/`

## §1 审计结果摘要

| 维度 | 数量 | 备注 |
|---|---|---|
| `logging.getLogger(...)` 调用点 | 79 | 各一个于 79 个源文件 |
| `logger.{level}(...)` 调用点 | 337 | 贯穿 60 个文件 |
| StateMutationLog 相关调用 | 156 | 20 个文件 |
| EventLogger 源码存在 | **0** | 已在 Step 1 重铸时整体撤除 |
| "已撤除" 历史注释 | 6 处 | 3 prod + 3 test |
| 死 logger 配置 | 4 处 | `main.py:setup_logging()` 里引用已删模块 |
| 命名不一致 logger | 6 处 | 含 1 个逃逸 `lapwing.*` 命名空间 |
| 重复 INFO（与 MutationLog 对齐） | 6 处 | 分布在 brain + task_runtime |
| `event_log` 孤儿表 | 4,145 行 | 无 writer，最后一行 2026-04-14 |

核心结论：**原计划预设的"EventLogger vs MutationLog 职责重叠"问题已不存在**——EventLogger 本体早已撤除，本次重做实际上是清理它留下的配置残渣 + 孤儿数据 + 历史注释，再把与 MutationLog 业务事件重叠的 Python `logger.info` 降到 DEBUG。

## §2 设计决策

### 两层架构（EventLogger 层不再存在）

- **Layer 1 — StateMutationLog**：业务事件持久层。独立 SQLite (`data/mutation_log.db`) + 每日 JSONL 镜像 (`data/logs/mutations_YYYY-MM-DD.log`)。**本次一字不动**。
- **Layer 2 — Python `logging`**：开发诊断层。`data/logs/lapwing.log` (RotatingFileHandler 5MB × 2) + stdout。第三方库写 `libraries.log`。
- **中间业务日志层不存在**：Step 1 已证明其职责可被 StateMutationLog 吸收。在 design 里显式记录"不要再补这一层"。

### 级别边界（强约束）

- `ERROR`：意料之外的失败
- `WARNING`：降级路径
- `INFO`：状态转换（启动/关闭、路由热重载、罕见 recovery）
- `DEBUG`：高频诊断；**业务事件的 INFO 文本版都降到这一级**（MutationLog 已持久化过）

详见 `docs/refactor_v2/logging_design.md`。

### EventLogger 处置

**保持已撤除状态。** 已删除所有配置残渣和历史注释，DROP 了孤儿数据表。

### `data/tool_results/*.txt` 处置

**不纳入本次范围**（显式记入遗留债务，见 §8）。它不是日志，是工具大结果的 spillover archive，需要独立 retention policy。

## §3 删除清单

### §3.1 `main.py:setup_logging()` 中的死 logger 配置

```diff
-    for module_name in (
-        "lapwing.core.brain",
-        "lapwing.core.task_runtime",
-        "lapwing.core.llm_router",
-        "lapwing.core.llm_protocols",
-        "lapwing.core.prompt_builder",     # 模块已删（Step 3）
-        "lapwing.core.heartbeat",          # 模块已删（Step 1 重铸）
-        "lapwing.core.consciousness",      # 模块已删（Step 4 重铸）
-        "lapwing.memory",
-        "lapwing.tools",
-        "lapwing.core.channel_manager",
-    ):
-        logging.getLogger(module_name).setLevel(logging.WARNING)
-
-    logging.getLogger("lapwing.app.container").setLevel(logging.INFO)
-    logging.getLogger("lapwing.event_logger").setLevel(logging.INFO)  # 模块已删
```

commit: `c85d14b chore(logging): drop dead logger configs in setup_logging`

### §3.2 重写 6 处"已撤除"历史注释

删除"EventLogger 已撤除"、"events_v2.db 已撤除"之类的过渡期叙事，改写为对当前行为的直接描述。涉及：

- `src/app/container.py`（2 处 docstring/注释）
- `src/api/routes/events_v2.py`（模块 docstring）
- `src/api/routes/system_v2.py`（模块 docstring）
- `src/api/routes/tasks_v2.py`（模块 docstring）
- `tests/api/test_events_v2.py`（模块 docstring）
- `tests/api/test_system_v2.py`（模块 docstring）
- `tests/api/test_tasks_v2.py`（模块 docstring）

commit: `2c4ee95 chore(logging): rewrite legacy EventLogger removal comments`

### §3.3 修复 6 处 logger 命名

| 文件 | 旧名 | 新名 |
|---|---|---|
| `src/core/main_loop.py` | `lapwing.main_loop` | `lapwing.core.main_loop` |
| `src/core/maintenance_timer.py` | `lapwing.maintenance_timer` | `lapwing.core.maintenance_timer` |
| `src/core/inner_tick_scheduler.py` | `lapwing.inner_tick_scheduler` | `lapwing.core.inner_tick_scheduler` |
| `src/adapters/desktop_adapter.py` | `lapwing.adapters.desktop` | `lapwing.adapters.desktop_adapter` |
| `src/tools/workspace_tools.py` | `lapwing.tools.workspace` | `lapwing.tools.workspace_tools` |
| `src/core/codex_oauth_client.py` | `__name__` (`src.core.codex_oauth_client`) | `lapwing.core.codex_oauth_client`（并把变量 `log` 改为 `logger` 以对齐全项目惯例） |

最后一条最关键：`__name__` 解析到 `src.core.codex_oauth_client`，**逃逸 `lapwing.*` 命名空间**，被 root logger 捕获并写入 `libraries.log`，混在第三方库日志里。修正后走 `lapwing.*` handler。

commit: `d0b22fa refactor(logging): align logger names with module paths`

### §3.4 INFO → DEBUG 降级（6 处）

同一业务事件若已被 StateMutationLog 以结构化方式记录，Python logging 的文本版降到 DEBUG：

- `src/core/brain.py:911` incoming (↔ ITERATION_STARTED)
- `src/core/task_runtime.py:560` loop completed summary (↔ ITERATION_ENDED)
- `src/core/task_runtime.py:812` multi-tool-call hint (↔ TOOL_CALLED ×N)
- `src/core/task_runtime.py:1070` per tool_call progress (↔ TOOL_RESULT)
- `src/core/task_runtime.py:1149` loop turn summary (↔ ITERATION_ENDED)
- `src/core/task_runtime.py:1556` large tool result budgeted (↔ TOOL_RESULT)

**保留为 INFO 的状态转换（对照审计时 §3.4 的清单修正后）**：
- `task_runtime.py:737/759`（罕见错误恢复路径）
- `task_runtime.py:1526`（罕见基础设施事件 reactive compact）
- `llm_router.py:330/402`（启动期模型路由注册）
- `llm_router.py:979/1027/1189`（罕见 thinking-only 重试）

commit: `972310c refactor(logging): demote duplicate hot-path INFO to DEBUG`

## §4 grep 验证（已删符号 = 0）

```bash
# EventLogger / event_logger / events_v2.db / 已撤除 / log_event
$ grep -rn 'EventLogger\|event_logger\|events_v2\.db\|已撤除' src/ tests/ --include='*.py'
(no matches)

# 死 logger 名
$ grep -rn 'lapwing\.core\.prompt_builder\|lapwing\.core\.heartbeat\|lapwing\.core\.consciousness\|lapwing\.event_logger' src/ tests/ main.py
(no matches)

# 不合规 logger 名
$ grep -rn 'logging\.getLogger("lapwing\.main_loop"\|lapwing\.maintenance_timer"\|lapwing\.inner_tick_scheduler"\|lapwing\.adapters\.desktop"\|lapwing\.tools\.workspace"' src/
(no matches)

$ grep -rn 'logging\.getLogger(__name__)' src/
(no matches)
```

保留的 `events_v2` 提及全部是对**当前 SSE 路由文件名**的引用（`src/api/routes/events_v2.py` 本身，以及 `server.py` 的 import/mount）——这是文件名，不是消亡的系统。

## §5 数据变更

### `lapwing.db:event_log` 表

1. **备份**（CSV）：`~/lapwing-backups/pre_logging_overhaul_20260419_170430/event_log.csv` （1,046,277 字节 / 4,145 行）
2. **DROP TABLE + VACUUM** 在 `data/lapwing.db`
3. **验证**：
   ```
   剩余 tables = [commitments, discoveries, interest_topics, reminders,
                  reminders_v2, sqlite_sequence, todos, trajectory, user_facts]
   lapwing.db size: 6460 KB (收缩前 6615 KB)
   ```
4. **历史数据**：category 分布 = `llm_call:1478, tool_call:995, tool_loop:894, system:264, consciousness:197, memory:156, conversation:132, thinking:22, evolution:7`——全部被 MutationLog 的 MutationType 枚举覆盖（consciousness/thinking 所属系统已在 Step 4 重铸移除）。
5. **最新一行时间**：`2026-04-14 23:14:25`——证明 Step 1 重铸后表即无 writer。

### `data/logs/` 目录

**无文件删除**。三个文件全部保留：
- `lapwing.log` (~228 KB) — 活跃 Python logging 输出，RotatingFileHandler 会自行轮转
- `mutations_2026-04-18.log`、`mutations_2026-04-19.log` — MutationLog JSONL 业务数据，不是"日志"意义上的 log，不应删

`RotatingFileHandler(5MB × 2 backups)` 配置在审计时已验证符合新架构，本次未修改。

## §6 日志架构图（最终两层）

```
 ┌───────────────────────────────────────────────────┐
 │  StateMutationLog (durable business events)        │
 │                                                    │
 │  writes  → data/mutation_log.db                    │
 │           + data/logs/mutations_YYYY-MM-DD.log    │
 │  reads   ← Desktop SSE via subscribe()             │
 │           ← audit/replay via query_by_*            │
 │           ← /api/v2/system/events (historical)     │
 │  vocab   = MutationType (closed enum, 18 members)  │
 │  policy  = 不降级、不过滤；每条都是 durable 事件    │
 └───────────────────────────────────────────────────┘
                         ─── separate tracks ───
 ┌───────────────────────────────────────────────────┐
 │  Python logging (diagnostics)                      │
 │                                                    │
 │  writes  → data/logs/lapwing.log (5MB × 2 rolling) │
 │           + stdout (dev)                           │
 │  reads   ← 开发者 / 排障 / 事故取证                 │
 │  level   = LOG_LEVEL env (默认 INFO)               │
 │  rules   = ERROR/WARNING/INFO/DEBUG 严格语义       │
 │           = 业务事件 → DEBUG（MutationLog 兜底）   │
 │  root    → data/logs/libraries.log (第三方库)       │
 └───────────────────────────────────────────────────┘
```

**不存在第三层**。

## §7 测试

- 无测试新增（声明式配置 + 注释重写不需要单测）
- 无测试删除（EventLogger 测试在 Step 1 已清理完）
- **全测结果**：`1282 passed, 2 warnings in 327.82s`（warnings 是 pre-existing，与本次无关）
- 基线对比：master HEAD 也是 1282 测试——**零净变化**，符合"配置清理 + 注释重写 + 级别调整"的期望

```
$ python -m pytest tests/ -x -q
........................................................................ [  5%]
...
..........................................................               [100%]
1282 passed, 2 warnings in 327.82s
```

## §8 遗留债务

1. **`data/tool_results/*.txt`（146 文件 / ~1.1 MB）**
   - 来源：`src/core/task_runtime.py:86 TOOL_RESULT_DIR`——将超大工具结果从上下文 spill 到磁盘
   - 当前：无任何 retention policy
   - 建议：7 天 TTL 或 1000 文件 LRU，或纳入 `maintenance_timer.py` 每日清理任务
   - 不纳入本次：它不是日志，它是工具输出归档

2. **`RotatingFileHandler` 的轮转证据未实测**
   - 配置是 5MB × 2，当前 `lapwing.log` 只有 228 KB，还没有触发过轮转
   - 风险：配置是声明式，实际生产达到 5MB 时是否正确滚动需要监测一次
   - 建议：下次生产运行超过一周后人工 `ls -la data/logs/lapwing.log*` 抽查

3. **生产级别默认值问题**
   - `LOG_LEVEL=INFO` 是默认值（`config/.env.example`）
   - 本次 B2 降级后 INFO 级别的噪音已大幅减少，但如果生产想进一步降噪可设 `LOG_LEVEL=WARNING`
   - 不强制：INFO 现在已是干净的状态转换级别

## §9 Commit 清单

| SHA | 说明 |
|---|---|
| `759d7e6` | docs(logging): Phase 1+2 audit and design for logging overhaul |
| `c85d14b` | chore(logging): drop dead logger configs in setup_logging |
| `2c4ee95` | chore(logging): rewrite legacy EventLogger removal comments |
| `d0b22fa` | refactor(logging): align logger names with module paths |
| `972310c` | refactor(logging): demote duplicate hot-path INFO to DEBUG |
| *(待合入)* | docs(logging): overhaul cleanup report |

## §10 最终状态

- Python `logging`：两层架构清晰，命名空间严格 `lapwing.*`，业务事件不再在 INFO 重复
- StateMutationLog：不动，完整职责
- EventLogger：源码、配置、孤儿数据、历史注释全部清理完毕
- `lapwing.db`：9 张表（去掉了 `event_log`），收缩 ~155 KB
- 测试：1282 → 1282（零净变化）
- Git：5 个逻辑 commit + 1 个 cleanup report commit，全部在 `refactor/logging-overhaul` 分支
