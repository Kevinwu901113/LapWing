# Logging Audit — 2026-04-19

> Phase 1 (A1 + A2 + A3) of the logging-system overhaul.
> Branch: `refactor/logging-overhaul`. Baseline HEAD: `b1c260e` (Step 7 complete).

## §1 扫描结果摘要

| 维度 | 数量 | 备注 |
|---|---|---|
| `logging.getLogger(...)` 模块 | 79 | 每个 `src/**/*.py` 下的日志模块命名 |
| `logger.{level}(...)` 调用点 | 337 | 贯穿 60 个文件 |
| StateMutationLog 相关调用点 | 156 | 贯穿 20 个文件（`mutation_log.record(...)` + `iteration_context` + 等） |
| EventLogger 相关代码 | **0** | 源码中没有任何 `EventLogger` / `event_logger.py` 的定义或调用 |
| EventLogger "已撤除" 注释 | 6 | `src/app/container.py` ×2、`src/api/routes/events_v2.py`、`src/api/routes/system_v2.py`、`src/api/routes/tasks_v2.py`、以及 3 个测试文件 |
| `event_log` 表（lapwing.db） | 4,145 行 | 已无 writer，latest row `2026-04-14` |
| `events_v2.db` 文件 | 不存在 | 已删 |
| 日志文件 | 3 | `data/logs/lapwing.log` (228 KB)、`data/logs/mutations_2026-04-18.log`、`data/logs/mutations_2026-04-19.log` |

**关键发现**：原计划的核心疑虑"EventLogger vs MutationLog 职责重叠"在本次扫描时已不成立——EventLogger 本体已在 Step 1 重铸时撤除，仅剩数据库残表（`event_log`）和历史注释。Phase 3 的 B3 因此退化为验证 + 收尾（确认没有任何残余写入路径），B5 变成纯数据清理。

## §2 三机制现状盘点

### §2.1 StateMutationLog（保留）

- 位置：`src/logging/state_mutation_log.py`
- 存储：独立 SQLite 文件 `data/mutation_log.db`（不是 `lapwing.db`）+ 每日 JSONL 镜像 `data/logs/mutations_YYYY-MM-DD.log`
- 消费者：
  - Desktop v2 SSE（`src/api/routes/events_v2.py` 通过 `mutation_log.subscribe(...)` 接收 live fan-out）
  - 审计回放（`query_by_iteration` / `query_by_window` / `query_by_type` / `query_llm_request`）
  - `api/routes/system_v2.py`、`api/routes/tasks_v2.py` 的历史查询
- 词表：`MutationType` 封闭枚举，18 个成员（见 state_mutation_log.py §85-132）。非 `MutationType` 枚举值调用 `record(...)` 直接 `TypeError`，防止词表膨胀。
- **判断**：重铸的核心产物，**一字不动**。

### §2.2 Python `logging`（保留但需整形）

- 命名空间根：`lapwing.*`
- 配置入口：`main.py:setup_logging()`（现行实现已使用 RotatingFileHandler 5MB×2 写到 `data/logs/lapwing.log`，root logger 写到 `libraries.log`）
- 消费者：开发调试 + 异常排查 + 滚动日志文件
- 级别控制：`config/settings.py:LOG_LEVEL`（默认 INFO），由 `LOG_LEVEL` 环境变量覆盖
- 现状问题：见 §4、§5、§6

### §2.3 EventLogger（已撤除，仅需清理残留）

- 代码：无
- 数据：`lapwing.db:event_log` 表 4,145 行（无 writer，最后一行 2026-04-14）
- 代码注释："已撤除" × 6 条分布在 container / 3 个 routes / 3 个 tests

## §3 死日志清单（引用已删模块/概念）

### §3.1 `main.py:setup_logging` — 配置 ghost logger

`main.py:58-74` 在 `setup_logging()` 中对以下 logger name 显式设置级别：

| 行号 | Logger 名 | 目标模块存在？ | 行动 |
|---|---|---|---|
| 63 | `lapwing.core.prompt_builder` | ✕ 已删（Step 3） | DELETE |
| 64 | `lapwing.core.heartbeat` | ✕ 已删（Step 1 重铸） | DELETE |
| 65 | `lapwing.core.consciousness` | ✕ 已删（Step 4 重铸） | DELETE |
| 74 | `lapwing.event_logger` | ✕ 已删 | DELETE |

> `lapwing.core.brain` / `lapwing.core.task_runtime` / `lapwing.core.llm_router` / `lapwing.core.llm_protocols` / `lapwing.memory` / `lapwing.tools` / `lapwing.core.channel_manager` / `lapwing.app.container` — 均实际存在 → **保留**。

### §3.2 "已撤除" 注释 — 历史包袱

以下注释对消费者（LLM / 开发者）没有价值，只在阅读老代码时增加噪音。清理方式：**删除注释，改写为当前行为描述**。

| 文件 | 行 | 当前内容（摘要） | 处理 |
|---|---|---|---|
| `src/app/container.py` | 140 | `# (v2.0 Step 1: EventLogger/events_v2.db 持久化职责已移交给 StateMutationLog)` | DELETE comment |
| `src/app/container.py` | 191 | `# mutations, independent from the legacy events_v2.db (which is scheduled…)` | REWRITE to current-only language |
| `src/api/routes/events_v2.py` | 9 | `断线重连仍未实现（EventLogger 已撤除）。…` | 保留"断线重连仍未实现"，删除"EventLogger 已撤除" |
| `src/api/routes/system_v2.py` | 4 | `而不是已撤除的 events_v2.db。…` | REWRITE to current-only language |
| `src/api/routes/tasks_v2.py` | 4 | `EventLogger 对 agent.* 历史事件的查询已随 EventLogger 撤除而失效。…` | REWRITE |
| `tests/api/test_events_v2.py` | 3 | `v2.0 Step 1: EventLogger + events_v2.db have been removed…` | REWRITE |
| `tests/api/test_system_v2.py` | 4 | `(mutation_log.db) rather than events_v2.db…` | REWRITE |
| `tests/api/test_tasks_v2.py` | 4 | `(EventLogger-backed agent-history lookup was removed)…` | REWRITE |

### §3.3 Logger 命名不一致

`lapwing.*` 命名空间下以下 logger 名与 Python 模块路径不对齐。后果：`setup_logging` 在 §3.1 那种"按 logger name 分级配置"的代码里会 miss、开发者阅读时找不到对应源文件。

| 文件 | 声明的 logger 名 | 应改为 | 影响 |
|---|---|---|---|
| `src/core/main_loop.py:41` | `lapwing.main_loop` | `lapwing.core.main_loop` | 配置 miss |
| `src/core/maintenance_timer.py:32` | `lapwing.maintenance_timer` | `lapwing.core.maintenance_timer` | 配置 miss |
| `src/core/inner_tick_scheduler.py:45` | `lapwing.inner_tick_scheduler` | `lapwing.core.inner_tick_scheduler` | 配置 miss |
| `src/adapters/desktop_adapter.py:10` | `lapwing.adapters.desktop` | `lapwing.adapters.desktop_adapter` | 命名不一致 |
| `src/tools/workspace_tools.py:10` | `lapwing.tools.workspace` | `lapwing.tools.workspace_tools` | 命名不一致 |
| `src/core/codex_oauth_client.py:27` | `logging.getLogger(__name__)` | `lapwing.core.codex_oauth_client` | **逃逸 `lapwing.*` 命名空间 → 走 root logger，污染 `libraries.log`** |

### §3.4 重复/冗余 INFO 日志（与 MutationLog 重叠）

`logger.info` 调用中有一类"业务事件"——它们与 MutationLog 的 `ITERATION_STARTED` / `TOOL_CALLED` / `TOOL_RESULT` / `LLM_REQUEST` / `LLM_RESPONSE` 字面重复。这些应 **降级到 DEBUG**（保留用于调试，但不进默认 INFO 日志）。

| 文件:行 | 内容摘要 | 对应 MutationType |
|---|---|---|
| `src/core/brain.py:911` | `[%s] incoming: %s` | ITERATION_STARTED (chat_id 已在 iteration_context) |
| `src/core/task_runtime.py:560` | runtime 入口摘要 | ITERATION_STARTED |
| `src/core/task_runtime.py:737, 759, 812, 1070, 1149` | 工具调用进度 | TOOL_CALLED / TOOL_RESULT |
| `src/core/task_runtime.py:1526, 1556` | Reactive compact | 非重复但低价值 |
| `src/core/llm_router.py:330, 402, 979, 1027, 1189` | LLM routing/response | LLM_REQUEST / LLM_RESPONSE |

> 保留的 INFO：启动/关闭、模型配置热重载成功（`llm_router.py:380 "Model routing reloaded"`）、prompt reload（`brain.py:177`）——这些是**状态转换**，MutationLog 不收录。

### §3.5 `data/tool_results/*.txt` — 澄清

不是日志，是 `src/core/task_runtime.py:86 TOOL_RESULT_DIR` 为"超大工具结果"写入的 spillover archive（为避免内存里保留巨型字符串）。当前 146 个文件 1.1MB 总大小，无任何轮转策略。

**判断**：**不属于本次日志重做的范围**（它是工具执行产物，不是日志）。记录为**遗留债务**：需要独立的 retention policy（建议 7 天 TTL 或 1000 文件 LRU），留给后续。

## §4 EventLogger 与 MutationType 对比（决策证据）

因 EventLogger 源码已不存在，此处基于 `event_log` 表的历史数据（`SELECT DISTINCT category, event_type FROM event_log`）回推其词表。

样本（最新一行）：
```json
{"category": "llm_call", "event_type": "complete",
 "data": {"slot": "memory_processing", "model": "MiniMax-M2.7", "duration": 14.24, ...}}
```

这与 `MutationType.LLM_RESPONSE` 的 payload 高度重叠。MutationType 是 EventLogger 词表的**严格超集**：
- `llm_call.*` ⊆ `LLM_REQUEST` / `LLM_RESPONSE`
- `tool_call.*` ⊆ `TOOL_CALLED` / `TOOL_RESULT`
- `agent.*` ⊆ `AGENT_STARTED` / `AGENT_COMPLETED` / `AGENT_FAILED` / `AGENT_TOOL_CALL`
- iteration 边界 ⊆ `ITERATION_STARTED` / `ITERATION_ENDED`
- 系统生命周期 ⊆ `SYSTEM_STARTED` / `SYSTEM_STOPPED`

结论：没有任何 EventLogger 类别是 MutationLog 未覆盖的。`event_log` 表可以安全 DROP，**无需迁移**。

## §5 决策汇总（供 logging_design.md 引用）

1. **EventLogger**：保持已撤除状态。删除残留注释（§3.2），DROP `event_log` 表（§5.B）。
2. **death row**：§3.1 四个 ghost logger、§3.3 六处 logger 命名修正、§3.4 的 INFO→DEBUG 降级。
3. **StateMutationLog**：不动。
4. **日志文件策略**：`main.py` 现行 `RotatingFileHandler(5MB, 2 backups)` **符合新架构**，不改。`LOG_LEVEL` 已通过 env var 可控，不改。
5. **root logger 写 `libraries.log`**：保留，但配置 `lapwing.*` 的 logger `.propagate = False`（已是）以防交叉。
6. **`data/tool_results/`**：记为遗留债务，不纳入本次范围。

## §6 下一步

1. 写 `docs/refactor_v2/logging_design.md`（§5 决策的正式化）
2. B1：按 §3.1 / §3.2 / §3.3 / §3.4 分别 commit
3. B5：DROP `event_log` 表
4. B6：全测通过
5. cleanup_report_logging.md + merge
