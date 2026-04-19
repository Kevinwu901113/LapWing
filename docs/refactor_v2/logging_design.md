# Logging Architecture — Post-Recast Design

> Phase 2 of the logging overhaul.
> Depends on: `docs/refactor_v2/logging_audit.md`.

## §1 Why 重做

重铸后日志系统的问题不是"结构不合理"，而是**重铸过程只换了引擎（Step 1 的 StateMutationLog 替换了 EventLogger），没有清理仪表盘**：main.py 里还在配置已删模块的 logger，业务事件在 `logger.info` 和 `MutationLog` 两处双写，并存留一张 4,145 行的孤儿表。目标是让三层（实际只剩两层）职责干净、消费者显式、死代码归零。

## §2 架构（两层）

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 1: StateMutationLog (durable business events)        │
│  ───────────────────────────────────────────────────────    │
│  File:      src/logging/state_mutation_log.py                │
│  Storage:   data/mutation_log.db (独立 SQLite) +             │
│             data/logs/mutations_YYYY-MM-DD.log (JSONL 镜像) │
│  Vocabulary: MutationType enum (封闭, 18 成员)               │
│  Consumers: Desktop SSE / 审计回放 / 未来 observability      │
│  Writers:   核心业务路径 — task_runtime, llm_router, brain,  │
│             tell_user, commitments, agents, attention        │
│  Policy:    永不禁用, 永不降级, 不做 level 过滤               │
│                                                              │
│  每条记录都是"发生过的结构化事件"——不做噪音控制。              │
│  下游消费者通过 `query_by_*` 或 `subscribe` 挑需要的。         │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  Layer 2: Python logging (diagnostics)                      │
│  ───────────────────────────────────────────────────────    │
│  File:      main.py:setup_logging()                          │
│  Storage:   data/logs/lapwing.log (RotatingFileHandler,     │
│             5MB × 2 backups) + stdout (开发时)               │
│             root (third-party) → data/logs/libraries.log    │
│  Namespace: lapwing.*  (strict, no escapes)                  │
│  Level:     LOG_LEVEL env var (default INFO)                 │
│  Consumers: 开发者 / 排障 / 事故取证                          │
│  Writers:   全 src/** 代码，通过 `logging.getLogger(...)`     │
│  Policy:    严格三档 (WARNING/INFO/DEBUG),                   │
│             **业务事件 → DEBUG** (已由 MutationLog 兜底)     │
│             **状态转换 → INFO**                              │
│             **异常降级 → WARNING/ERROR**                     │
└─────────────────────────────────────────────────────────────┘
```

**EventLogger 层不存在**——这一层的职责已在 Step 1 重铸时被 MutationLog 吸收。保留这一事实在设计里记录，防止未来有人"想补一个中间业务日志层"。

## §3 级别边界（强约束）

| 级别 | 语义 | 例子 | 禁止 |
|---|---|---|---|
| `ERROR` | 需要人看的失败：意料之外的异常 | LLM 调用失败无法恢复 | 用于可预期的业务失败（如权限不够拒绝工具） |
| `WARNING` | 降级路径：失败但有兜底 | LLM 调用失败后重试成功、VLM 回退到 LLMRouter | 用于正常路径 |
| `INFO` | 状态转换：少频事件 | 启动/关闭、模型路由热重载、prompt reload | 用于每请求/每工具调用的进度 |
| `DEBUG` | 诊断细节：高频事件、调用链 trace | 每次 tool 调用入参出参、LLM 调用详细元数据 | — |

**裁决原则**：如果一条 `logger.info/warning` 的内容**已经**作为 `MutationLog.record(...)` 的 payload 出现过一次，该 Python log 应降级到 DEBUG。理由：它对开发者排障还有用（本地 `LOG_LEVEL=DEBUG` 时可见），但不应出现在默认 INFO 日志里重复占用带宽。

具体降级清单见 `logging_audit.md` §3.4。

## §4 命名规则

所有 `getLogger(...)` 必须满足：
1. 形如 `lapwing.<module_path>`——module_path 与 `src/` 下的相对路径按 `/` → `.` 对应，保留 `.py` 的 stem。
2. 不使用 `__name__`（因为源码在 `src/` 下，`__name__` 会是 `src.xxx` 而不是 `lapwing.xxx`）。
3. 第三方库日志走 root logger 自动归入 `libraries.log`，不需要在 Lapwing 代码里手动获取。

不合规清单见 `logging_audit.md` §3.3。

## §5 配置（保留现行）

审计发现 `main.py:setup_logging()` 的**轮转策略**（5MB × 2 backups）、**分离的 root handler**（`libraries.log`）、**propagate=False**、**LOG_LEVEL 可配置**已全部符合新架构。本次重做**不修改** setup_logging 的结构，只修改它引用的 logger 名清单（§3.1 的四个 ghost + §3.3 的命名修正）。

`config/.env.example` 的 `LOG_LEVEL=INFO` 保持不变。生产可设 `LOG_LEVEL=WARNING` 以进一步降噪，但默认 INFO 在修复 §3.4 降级后已经是足够干净的级别——不强制用户改。

## §6 数据清理

`event_log` 表是 EventLogger 残留：
1. 备份：`~/lapwing-backups/pre_logging_overhaul_<ts>/event_log.csv`（CSV 便于肉眼检查）
2. `DROP TABLE event_log;` on `data/lapwing.db`
3. 验证：`sqlite_master` 中不再含 `event_log`

## §7 测试策略

- `src/logging/state_mutation_log.py` 测试（`tests/logging/test_state_mutation_log.py`）：**不动**
- `tests/api/test_events_v2.py` / `test_system_v2.py` / `test_tasks_v2.py`：只改注释（§3.2）
- 新的单元测试：无必要——`setup_logging()` 是声明式配置，改动通过启动 smoke test 验证即可
- 全测命令：`python -m pytest tests/ -x -q`

## §8 遗留债务（显式不纳入本次）

- `data/tool_results/*.txt`（146 个文件、1.1 MB）需要独立的 retention policy。见 `logging_audit.md` §3.5。
- 本次仅清理日志层，不触碰 `trajectory_store.py`、`discoveries`、`notes`、`commitments` 这些"独立持久化轨道"的记录机制——它们各自有自己的消费者和 schema，与日志无关。

## §9 实施顺序（Phase 3）

| 步骤 | 动作 | 文件触及 | Commit |
|---|---|---|---|
| B1a | 删 `main.py` 四个 ghost logger 行 | `main.py` | "chore(logging): drop dead logger configs" |
| B1b | 重写 6 条"已撤除" 注释 → 现行语义 | 3 prod + 3 test | "chore(logging): rewrite legacy removal comments" |
| B1c | 修 logger 命名不一致 | 6 个源文件 | "refactor(logging): align logger names with module paths" |
| B2 | INFO → DEBUG 降级（§3.4 清单） | 3 个核心文件 | "refactor(logging): demote duplicate INFO to DEBUG" |
| B3 | 验证 EventLogger 无残余写路径（grep） | — | （无代码改动，记入报告） |
| B4 | 删旧日志文件 + 验证轮转 | `data/logs/*` | （无代码改动） |
| B5 | 备份 + DROP `event_log` 表 | `data/lapwing.db` | （数据变更，记入报告） |
| B6 | `pytest -x -q` 全绿 | — | （无代码改动） |
| — | 写 `cleanup_report_logging.md` + merge master | — | "docs(logging): overhaul cleanup report" |

开始 Phase 3。
