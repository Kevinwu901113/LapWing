# Lapwing MVP 全量清理计划 (2026-04-19)

> **状态**: 待审批 · 尚未执行
> **基线**: `master` @ `f931dbd` (logging overhaul 已合入)
> **原则**: 宁可砍过头事后加回来，也不要留一堆"可能有用"的东西。

## 方法说明

本计划基于 6 个 Explore agent 的并行扫描结果 + 设计规格 (`Lapwing MVP 设计描述`) + 现状文档 (`docs/项目结构总览_20260419.md`) 交叉验证产出。每一条删除都附带证据和理由。

**分类规则**:
- 🗑 **删除**: 代码/数据不服务 MVP，且无历史参考价值
- 📦 **归档**: 曾经有用但已执行完毕 / 被取代；保留到 `docs/archive/` 供翻查
- ♻ **简化**: 保留功能但去掉死分支 / 永真永假的 feature flag
- ⚠ **观察**: 发现了偏离 MVP 的实现细节，但不在"清理"范围内（需单独立项）

## 不碰的底线

- `data/identity/soul.md` / `constitution.md`；`prompts/lapwing_soul.md` / `lapwing_voice.md`
- `src/core/authority_gate.py` / `vital_guard.py` / `shell_policy.py`；`src/guards/memory_guard.py`
- `src/tools/tell_user.py` 和它作为单出口的语义（本轮不改）
- `src/logging/state_mutation_log.py` 和 `mutation_log.db`
- `trajectory`、`reminders_v2`、`commitments` 表；`mutations` 表
- 所有活代码对应的测试

---

## P0 · 死代码（可立刻删，无依赖）

### P0.1 已消失源文件对应的 `__pycache__` 僵尸字节码

**证据**: `src/heartbeat/actions/` 里 `.py` 只剩 `__init__.py`（空），但 `__pycache__` 还有 6 个 `.pyc`：`session_reaper`、`interest_proactive`、`prompt_evolution`、`auto_memory`、`scheduled_tasks`、`system_health`、`task_notification`、`autonomous_browsing`、`compaction_check`、`consolidation`、`memory_maintenance`、`self_reflection`。

**附带**: `tests/**/__pycache__/` 还有 `test_consciousness.pyc`、`test_evolution_engine.pyc`、`test_quality_checker.pyc`、`test_tactical_rules.pyc`、`test_session_search.pyc`、`test_telegram_app.pyc` 等 10 个无源文件的 `.pyc`。

**动作**:
- 🗑 `find src tests -name '*.pyc' -delete && find src tests -name __pycache__ -type d -empty -delete`
- 🗑 删 `src/heartbeat/actions/__pycache__/` 整个目录（既然 actions/ 目录将一起归档到 `__init__.py` + proactive_filter 都是空/死的，见 P1.1）

**理由**: 死掉的源文件对应的 `.pyc` 会误导新 Claude Code 实例以为这些模块还在。

---

### P0.2 `src/core/maintenance_timer.py` 里的坏 import

**证据**: Explore agent 发现该文件 line 126/127/131/151–156 `from src.heartbeat.actions.xxx import ...`，对应源文件已不存在。若 `_run_hourly()` / `_run_daily()` 触发会 `ModuleNotFoundError`。

**动作**:
- ♻ 打开 `src/core/maintenance_timer.py`，删除/替换这 7 处坏 import 分支；如果整个 hourly/daily 架构已被 `inner_tick_scheduler` 取代，则把相关调度干脆删掉。先读代码确认再做。
- 可能牵扯 `SenseContext.user_facts_summary` 字段（永远空），一并删。

**理由**: 坏 import 是真实 bug，不是"未来可能用到"。

---

### P0.3 `src/core/phase0.py` 的处置 ← 需裁定

**证据**: `phase0.py` 只在 `PHASE0_MODE` 环境变量为非空时被使用；这是一个极简 prompt 的测试/冒烟模式。目前在 `config/.env.test:7` 设 `PHASE0_MODE=A`，5 处源文件和 5 个测试引用。

**判断**: Phase 0 目前仍是"测试模式"，但对 MVP 运行时不产生任何用户可见行为。`soul_test.md` / `constitution_test.md` 是专用测试身份。

**动作**: **保留**（不算 MVP 的一部分，但是合法的测试基座；删了会掉 5 个测试）。将其状态在 CLAUDE.md 中显式说明即可。

---

## P1 · 补偿工程残留（这是"不服务于 MVP"的核心）

### P1.1 `src/heartbeat/proactive_filter.py` — 规则过滤 LLM 产出

**证据**: 这个文件用 5 条硬规则判断主动消息是否"够好"：密度、客服腔、口头语堆叠、报表格式、>4 句。**grep 全项目：零调用方**（包括测试）。

**动作**:
- 🗑 删 `src/heartbeat/proactive_filter.py`
- 🗑 删 `src/heartbeat/actions/` 整个目录（`__init__.py` + `__pycache__` 僵尸）
- 🗑 删 `src/heartbeat/__init__.py`（随目录一起）→ 即：**删整个 `src/heartbeat/` 目录**
- 🗑 检查并删除 `tests/heartbeat/` 如果还有残留测试文件

**理由**: (a) MVP 原则"代码不判断她该不该说话"；(b) 已经没有调用方；(c) `src/heartbeat/actions/` 就是心跳 action 旧架构的遗址，新架构是 `inner_tick_scheduler + maintenance_timer`。

---

### P1.2 `tests/heartbeat/actions/test_evolution_trigger.pyc` 等孤儿字节码

见 P0.1，随 P1.1 一起删除。

---

### P1.3 根 `Lapwing_角色设定书.md` 与 MVP 规格并不冲突但需核对

**证据**: `Lapwing_角色设定书.md` (11K, 2026-04-03) 是她的人格参考稿。

**动作**: ⚠ 暂时保留，但在清理完后对照 `soul.md` / `voice.md` 核对是否有过期条款。本次不动。

---

## P2 · Feature flag 简化

### P2.1 settings.py 中定义但全局零引用的 flag（全部删除）

| Flag | 行号 | 默认 | 引用点 |
|---|---:|---|---|
| `QQ_ENABLED` | 45 | false | **0** |
| `HEARTBEAT_ENABLED` | 101 | true | **0** |
| `CONSCIOUSNESS_ENABLED` | 106 | true | **0** |
| `MEMORY_GUARD_ENABLED` | 133 | true | **0** |
| `SELF_SCHEDULE_ENABLED` | 144 | true | **0** |
| `MESSAGE_SPLIT_ENABLED` | 145 | true | **0** |

**动作**:
- ♻ 从 `config/settings.py` 删除这 6 个定义
- ♻ 从 `config/.env.example` / `.env` 移除同名条目（如有）

**理由**: 零引用 = 永远是默认值，if-check 压根不存在 —— 纯视觉噪声。

### P2.2 `.env` 中设置但 `settings.py` 不读取的 flag

| Flag | 状态 |
|---|---|
| `SKILLS_ENABLED=true` | `.env` 设置；`settings.py` 未定义；零引用 |
| `DELEGATION_ENABLED=true` | `.env` 设置；`settings.py` 未定义；零引用 |

**动作**:
- ♻ 从 `config/.env` 删除这 2 行（CLAUDE.md 错标它们存在）

### P2.3 `.env.example` 中已注释掉的遗留 flag

`MEMORY_CRUD_ENABLED`、`AUTO_MEMORY_EXTRACT_ENABLED`、`SESSION_ENABLED`、`INCIDENT_ENABLED`、`EXPERIENCE_SKILLS_ENABLED`

**动作**:
- ♻ 从 `config/.env.example` 删除这些注释行

### P2.4 正常工作、保留不动的 flag

`BROWSE_ENABLED`、`EPISODIC_EXTRACT_ENABLED`、`SEMANTIC_DISTILL_ENABLED`、`AGENT_TEAM_ENABLED`、`SHELL_ENABLED`、`LOOP_DETECTION_ENABLED`、`BROWSER_ENABLED`、`BROWSER_VISION_ENABLED`、`MINIMAX_VLM_ENABLED`、`CHAT_WEB_TOOLS_ENABLED`

这 10 个都有真实的 `if flag:` 开关作用，保留。

---

## P3 · 数据与数据库清理

### P3.1 SQLite `lapwing.db` 废弃表

通过 `sqlite_master` 和 grep 双重核对确认的废弃表：

| 表 | 行数 | 现状 | 理由 |
|---|---:|---|---|
| `user_facts` | 107 | 零 reader | 被 `SemanticStore` (data/memory/semantic/kevin.md) 取代；CLAUDE.md 已标废 |
| `interest_topics` | 98 | 零 reader | 并入 `SemanticStore.world`；CLAUDE.md 已标废 |
| `discoveries` | 113 | **write-only 孤儿** | 由 `src/memory/conversation.py:68-80` 建表，全项目无 SELECT；写入者也需要删 |
| `todos` | 0 | 空表 | 旧 reminder 模型；`reminders_v2` + `DurableScheduler` 已代替 |
| `reminders` | 3 | 零 reader | 被 `reminders_v2` 取代 |

**动作**:
- 📦 先备份：`mkdir -p ~/lapwing-backups/pre_mvp_cleanup_$(date +%Y%m%d_%H%M%S)` 然后导出这 5 张表为 JSONL
- 🗑 脚本：`scripts/migrations/mvp_drop_legacy_tables.py`（一次性）执行 `DROP TABLE IF EXISTS` 对这 5 张
- 🗑 同步删代码：`src/memory/conversation.py` 中对应的 `CREATE TABLE IF NOT EXISTS` DDL 和所有写入函数（主要是 `user_facts` / `interest_topics` / `discoveries` / `todos` / `reminders`）
- ♻ `src/memory/conversation.py` 清理后应只保留仍在用的 API（看起来几乎整个文件都变成 facade；具体保留什么要在执行阶段结合 grep 结果裁定）

**理由**: 有写入、无读取 = 持续产生垃圾，直接违反 MVP 原则"不做没人看的事"。

### P3.2 数据目录中的孤儿 / 空占位

| 路径 | 现状 | 动作 |
|---|---|---|
| `data/memory/notes/` | 已空 (只有 `.gitkeep`) | 🗑 删除目录（v2.0 起笔记走 episodic/） |
| `data/workspace/` | 只有 `.gitkeep`，0 reader | 🗑 删除（`data/agent_workspace/` 才是实际沙箱） |
| `data/evolution/` | **磁盘上不存在**（CLAUDE.md 虚报） | — 仅需在 CLAUDE.md 删除虚假描述 |
| `data/tool_results/` | write-only 缓存（8.2 MB） | 📦 暂不删，保留但在 P4 阶段增加 30 天保留窗口（非必需，先不做） |
| `data/chroma_memory/` 与 `data/chroma/` | **不是重复**：分别服务 `VectorStore` 和 `MemoryVectorStore` | ♻ 保留；附录 A 会在 CLAUDE.md 说明 |

### P3.3 `data/memory/conversations/summaries/` 的 91 个旧摘要

**证据**: `src/api/routes/life_v2.py:154` 真的在读这个目录 → 是"她的生活 timeline"的数据源。

**动作**: ♻ 保留。这是活数据。

### P3.4 根目录 `skills/` 与 `skill_traces/`

**证据**:
- `skills/general/*.md` 有 16 份 INC-* 文档，`_index.json` 说 0 active，无自动调用
- `skill_traces/` 有 1000+ JSON trace，`rg 'skill_traces'` 在 src/ 零匹配

**动作**:
- 📦 归档到 `docs/archive/skills-experiment/` 或直接删 → **推荐删**（`git` 有历史，真要找回来能翻）
- 🗑 删 `skill_traces/`（write-only 审计存档，没人看）
- 🗑 删 `skills/`（实验期产物，MVP 里 Lapwing 靠记忆 + 人格驱动，不需要 incident-based skill）

**理由**: 不服务于 MVP，且无自动反馈闭环。

---

## P4 · 脚本归档与清理

| 脚本 | 现状 | 动作 | 理由 |
|---|---|---|---|
| `scripts/deploy.sh` | 活跃部署入口 | ✅ 保留 | CLAUDE.md 已记录 |
| `scripts/setup_browser.sh` | 活跃 | ✅ 保留 | Playwright 初始化 |
| `scripts/diagnose_schedule.py` | 活跃排障工具 | ✅ 保留 | — |
| `scripts/qq_export.py` | 活跃 | ✅ 保留 | 定期导出 QQ 记录 |
| `scripts/test_codex_oauth.py` | 用的 `oauth_codex`（已不在 requirements） | 🗑 删 | 旧的集成测试，外部 SDK 已不用 |
| `scripts/smoke_test_step5.py` | Step 5 手动冒烟 | 🗑 删 | Step 5 已完成；测试套件有对应单元/集成测试 |
| `scripts/migrate_to_trajectory.py` | Step 2e 已执行 | 📦 归档 → `docs/refactor_v2/migrations/` | 迁移已完成；保留说明文档 |
| `scripts/verify_dual_write.py` | Step 2f 已执行 | 📦 归档同上 | — |
| `scripts/drop_sessions_table.py` | Step 2j 已执行 | 📦 归档同上 | — |
| `scripts/migrations/step3_verify_drop_safety.py` | Step 3 M2.e 已执行 | 📦 归档同上 | — |
| `scripts/migrations/step3_drop_legacy_tables.py` | Step 3 M2.e 已执行 | 📦 归档同上 | — |
| **新增**: `scripts/migrations/mvp_drop_legacy_tables.py` | 本次清理用 | 执行后也归档 | 随 P3.1 创建 |

**归档方式**: 不是把 `.py` 挪到 docs/ 下，而是把脚本**删除**同时在 `docs/refactor_v2/migrations-archive.md` 里留一条"此迁移已在 XX step 执行完毕"的登记条目，git 历史保留源码可翻。

---

## P5 · 文档整理

### P5.1 根目录过期 `.md` → 归档到 `docs/archive/`

| 文件 | 大小 | 动作 | 理由 |
|---|---:|---|---|
| `HEALTH_CHECK_REPORT.md` | 25K | 📦 mv → `docs/archive/HEALTH_CHECK_REPORT_2026-04-14.md` | 反映 Step 1 之前状态；已过期 |
| `Lapwing_项目状况_20260416.md` | 31K | 📦 mv → `docs/archive/Lapwing_项目状况_20260416.md` | Step 1 之前的快照 |
| `cleanup_report_step1.md` | 17K | 📦 mv → `docs/refactor_v2/cleanup_report_step1.md` | Step 4–7 已归档在那；保持一致 |
| `cleanup_report_step2.md` | 33K | 📦 mv → `docs/refactor_v2/cleanup_report_step2.md` | 同上 |
| `cleanup_report_step3.md` | 15K | 📦 mv → `docs/refactor_v2/cleanup_report_step3.md` | 同上 |

### P5.2 `docs/` 下过期蓝图

| 文件 | 动作 | 理由 |
|---|---|---|
| `docs/lapwing-restructure-final.md` (63K) | 📦 mv → `docs/archive/2026-04-01-restructure-blueprint-superseded.md` | Step 1–7 已超越该蓝图；标注 superseded |
| `docs/lapwing-frontend-blueprint.md` (26K) | 📦 mv → `docs/archive/2026-04-01-frontend-blueprint-v1.md` | Tauri v1 时代的前端蓝图；desktop-v2 已取代 |

### P5.3 `docs/` 保留不动

- `docs/项目结构总览_20260419.md` ← 本次清理的最权威对照
- `docs/diagnosis_dodgers_2026041[67].md` ← 排障记录
- `docs/refactor_v2/*.md` ← 各 step 实施日志
- `docs/archive/*.md` ← 已归档的设计稿
- `docs/superpowers/plans/*.md` ← 任务计划归档（本计划也在这里）

### P5.4 `prompts/README.md`

**动作**: ♻ 核对 11 个 prompt 文件是否和实际目录一致（可能遗漏 Step 4-7 新增的 prompt）。

---

## P6 · CLAUDE.md 重写（清理最后一步）

执行完 P0–P5 后，依据 Explore agent "CLAUDE.md vs 现状" 报告的 30 项偏差全部重写。重点修正：

1. **架构流程**: `SessionManager → PromptBuilder → 8 层 prompt` → 改写为 `Attention → TrajectoryStore → StateViewBuilder → StateSerializer`
2. **目录结构**: 删除 `src/core/evolution.py` / `delegation.py` / `session.py` / `heartbeat.py` / `src/heartbeat/actions/` / `data/evolution/` 等已不存在条目；新增 `main_loop.py` / `event_queue.py` / `events.py` / `inner_tick_scheduler.py` / `attention.py` / `trajectory_store.py` / `commitments.py` / `durable_scheduler.py` / `state_view*.py` / `trust_tagger.py` / `minimax_vlm.py` / `src/logging/state_mutation_log.py` / `src/memory/episodic_*` / `src/memory/semantic_*` / `src/memory/working_set.py` / `src/agents/*` / `src/research/*`
3. **权限**: 三级 (OWNER/TRUSTED/GUEST) → 四级 (IGNORE/GUEST/TRUSTED/OWNER)
4. **Feature flag 列表**: 按 P2.4 的白名单重写
5. **通道**: 去掉 Telegram 和 TelegramApp
6. **桌面页面**: `DashboardPage/MemoryPage/ModelRoutingPage/PersonaPage/SensingPage/TaskCenterPage` → `ChatPage/IdentityPage/NotesPage/SystemPage/SettingsPage/StatusDetailPage`
7. **Guards**: 只留 `memory_guard.py`；`browser_guard` 说明它实际在 `browser_manager` 内部
8. **心跳**: 删除"fast/slow/minute 三种节拍 + HeartbeatAction ABC"的描述，改为 `InnerTickScheduler + MaintenanceTimer`
9. **Tools**: 从 "20 handler files" 改为准确数量 (16)
10. **MVP 核心约束**: 增加一段说明 `tell_user` 作为单出口、`think-then-speak` 循环、OWNER 可打断 inner tick 的不变量

新版 CLAUDE.md 的结构仍按当前大纲（Overview / Setup / Commands / Architecture / Directory Structure / Development Conventions / Key Subsystems / Extension Patterns），只是每一节内容替换。

---

## ⚠ 观察（超出清理范围，不在本轮动作中）

这些是 MVP 规格和当前实现之间的**实质偏离**，需要单独立项修复，不走"清理"：

### O1. `tell_user` 单出口被旁路

Tool-audit agent 发现 5 个直接 `await send_fn(...)` 的调用点，绕开了 `tell_user`：
- `src/core/brain.py:679` —— 立即回复路径
- `src/core/brain.py:1017` —— LLM 错误回传
- `src/core/durable_scheduler.py:396` —— 提醒通知触发
- `src/core/durable_scheduler.py:423` —— 提醒 agent 结果
- `src/core/durable_scheduler.py:432` —— 提醒 agent 降级消息

**观察**: 这是 MVP 规格里的关键不变量（"tell_user 单出口"）但**当前代码没实现**。不是死代码可以删，而是缺一层抽象。建议单开 `Step 8: tell_user 归并` 计划。

### O2. `src/memory/conversation.py` 作为"ConversationMemory facade"仍在

Step 3 报告第 8 节 Debt D 已登记，内存里还保留 todos/reminders/user_facts/discoveries/interests 的 facade，本次 P3.1 会拆掉表，但 `ConversationMemory` 类本身的简化需要跟着来。执行阶段再定细节。

### O3. `src/core/main_loop.py` 是骨架

Blueprint Step 4 的 MainLoop 只搭了壳子，M2/M3/M4 handler 仍是 TODO。不是"死"的，但也不是 MVP 的完整实现。超出清理范围。

### O4. `tests/core/test_consciousness*.pyc` 等无源测试的字节码

P0.1 处理。但更深层：tests/ 下是否还有**文件存在但测试已和删掉的模块挂钩**的僵尸？执行时 grep 一下 `from src.core.evolution|delegation|session|heartbeat` 以兜底。

---

## 执行顺序（审批后按此进行）

1. **Pre-flight**: `git checkout -b cleanup/mvp-alignment-2026-04-19`，跑一次全测试 `python -m pytest tests/ -x -q` 记录当前通过数作为基线。
2. **DB 备份**: `python scripts/migrations/mvp_drop_legacy_tables.py --dry-run` 先出导出预览；然后 `--execute` 做实删前的 JSONL 备份到 `~/lapwing-backups/pre_mvp_cleanup_{ts}/`。
3. **P1 补偿工程**: 删 `src/heartbeat/` 整个目录（含 proactive_filter 和 actions）。跑测试。
4. **P0.2 坏 import**: 修 `src/core/maintenance_timer.py`，去掉坏 import 分支。跑测试。
5. **P3.1 废弃表代码**: 删 `src/memory/conversation.py` 里废弃表的 DDL 和写入函数 + 相关代码路径。跑测试。
6. **P3.2 数据目录**: 删 `data/workspace/`、`data/memory/notes/`。
7. **P3.1 DROP 表**: 执行 `mvp_drop_legacy_tables.py --execute`。
8. **P2 Feature flag 简化**: 删 6 个零引用 flag + 2 个 `.env` 残留 + 清理 `.env.example` 注释。跑测试。
9. **P4 脚本归档**: 删 6 个已执行的 migration/smoke 脚本；在 `docs/refactor_v2/migrations-archive.md` 留登记。
10. **P3.4 skills / skill_traces**: 删 `skills/` 和 `skill_traces/`。
11. **P0.1 字节码清扫**: `find ... -name '*.pyc' -delete`。
12. **P5 文档整理**: `git mv` 过期 `.md` 到 `docs/archive/` 或 `docs/refactor_v2/`。
13. **全量测试**: `python -m pytest tests/ -x -q` 必须全过。
14. **P6 CLAUDE.md 重写**: 依据 §P6 清单重写。重写后再跑一次测试（自然不受影响，但留做 checksum）。
15. **diff review**: `git diff --stat master..HEAD` 汇总改动，贴到 PR 描述。
16. **合入**: `git checkout master && git merge --no-ff cleanup/mvp-alignment-2026-04-19`（由你决定是否保留 merge commit）。

---

## 风险与回滚

- **SQLite DROP TABLE 不可逆**: 依赖 P3.1 的 JSONL 备份。备份路径放在 `~/lapwing-backups/` 而非仓内。
- **测试回归**: 在每个大步骤后跑 `pytest -x -q`，定位到某一步破了就立即停。
- **git 历史保底**: 所有源文件删除都在 feature branch，合入用 `--no-ff` 保留 merge boundary。任何一次删除都能 `git revert`。
- **CLAUDE.md 重写**: 最后一步，不影响运行时。若审查后要调，直接再提交。

## 预计改动规模（粗估）

- 源文件删除: ~15 files (`src/heartbeat/*`, scripts/migration/*, phase0 留着)
- 源文件修改: ~4 files (`settings.py`, `conversation.py`, `maintenance_timer.py`, `.env*`)
- 数据库表 drop: 5 tables
- 文档归档/移动: 7 files
- 新增: `scripts/migrations/mvp_drop_legacy_tables.py` (一次性)，`docs/refactor_v2/migrations-archive.md` (登记簿)
- 字节码 / 空目录清理: ~数十个 `.pyc`

**预估最终 diff**: ~2000–3000 行净减。

---

**审批问**:
1. P0–P6 全部同意？还是有你想先保留/跳过的条目？
2. O1（tell_user 单出口漏洞）要不要作为 MVP 清理的一部分一并修掉？（我默认划出范围，但这是你 MVP 规格里的"底线"。）
3. P3.4（删 `skills/` + `skill_traces/`）要不要换成归档？
4. 完成后是否要打 tag 或发 PR？
