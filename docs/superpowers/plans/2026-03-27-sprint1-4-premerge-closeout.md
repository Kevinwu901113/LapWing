# Sprint 1/2/3/4 预合并收尾报告（2026-03-27）

## 1. 收尾目标与结论

目标：把 Sprint 1/2/3/4 从“可运行”推进到“可合并、可交付、可追溯”。

结论：**达到预合并标准（Go）**。

- 自动化门禁通过：
  - `./venv/bin/pytest -q` -> `341 passed, 3 warnings`
  - `npm run build` -> 成功
- 关键回归子集通过：
  - `./venv/bin/pytest -q tests/core/test_brain_tools.py tests/core/test_task_runtime.py tests/api/test_server.py tests/agents/test_dispatcher.py tests/agents/test_coder.py tests/app/test_task_view.py tests/app/test_telegram_app.py`
  - 结果：`71 passed`
- 任务链路烟测通过（事件 -> 任务投影）：
  - 通过 `DesktopEventBus + TaskViewStore` 发布 `task.started/executing/completed`，任务最终状态为 `completed`，详情事件数为 `3`。

## 2. Sprint 验收矩阵（目标 -> 实现 -> 证据）

| Sprint | 目标 | 主要实现 | 验收证据 |
|---|---|---|---|
| Sprint 1 | 执行骨架重构 | `task_runtime` 抽离、`task.*` 生命周期事件、brain 瘦身 | `tests/core/test_brain_tools.py`（事件顺序、blocked/failed 分支） |
| Sprint 2 | 文件编辑与验证闭环 | `file_editor`、统一 `verifier`、`coder` 多轮修复、`file_agent` 复用 | `tests/tools/test_file_editor.py`、`tests/core/test_verifier.py`、`tests/agents/test_coder.py`、`tests/agents/test_file_agent.py` |
| Sprint 3 | 工具注册与策略分层 | `tools registry/types`、`policy`、runtime 改为 registry+policy | `tests/tools/test_registry.py`、`tests/policy/test_shell_runtime_policy.py`、`tests/core/test_task_runtime.py` |
| Sprint 4 | 入口解耦与任务视图 | `AppContainer`、`TelegramApp`、`TaskViewStore`、`/api/tasks*`、desktop 任务面板 | `tests/app/test_container.py`、`tests/app/test_telegram_app.py`、`tests/app/test_task_view.py`、`tests/api/test_server.py` |

## 3. 对外契约变化与兼容性

### 3.1 保持兼容

- 现有 API 与 SSE 事件流保持可用。
- `task.*` 事件结构保持现状，继续作为任务事实源。
- `python main.py` 启动方式保持不变（入口变薄，但行为兼容）。

### 3.2 新增契约

- 新增只读 API：
  - `GET /api/tasks?chat_id=&status=&limit=`
  - `GET /api/tasks/{task_id}`
- 新增内部应用接口：
  - `AppContainer`
  - `TelegramApp`
  - `TaskViewStore`（任务摘要/详情视图）

## 4. 最终 Delta 摘要（核心行为变化）

- 工具执行能力从“brain/task_runtime 硬编码分支”演进为“registry + policy”可扩展架构。
- `CoderAgent` 从单次修复升级为双模式多轮闭环（snippet/workspace_patch，最多 3 次）。
- 入口从单文件 `main.py` 重构为“容器装配 + Telegram 适配”双层。
- 任务视图从“仅 SSE 事件展示”升级为“事件 + 统一只读任务读模型（API/desktop 同源）”。

## 5. 未覆盖边界与风险清单

### 5.1 风险分级

| 编号 | 风险 | 等级 | 处理结论 | 后续动作 |
|---|---|---|---|---|
| R1 | `tests/core/test_llm_router.py` 存在 3 条 asyncio 标记 warning | P3 | 本次放行（非阻断） | 下个迭代清理测试标记 |
| R2 | `TaskViewStore` 为内存态，重启后任务视图清空 | P3 | 设计内默认（可接受） | 若进入生产可增加持久化投影 |
| R3 | 事件总线为单进程模型，不支持多进程共享任务视图 | P2 | 本次放行（当前部署模型匹配） | 扩展部署时引入外部消息总线 |
| R4 | Telegram 真机端到端未在本次收尾中跑外网实操 | P2 | 以 adapter + 生命周期自动化测试替代 | 合并后补一次联调记录 |

### 5.2 放行标准结论

- P0/P1：无
- P2：2 条（已记录并有后续动作）
- P3：2 条（已记录并可后置）
- 结论：满足“预合并”放行条件

## 6. 提交切分方案（4 组）

> 目标：降低评审复杂度、缩小回滚半径、保持提交语义清晰。

1. **`runtime+policy`**
   - 建议内容：`src/tools/{types,registry}.py`、`src/policy/**`、`src/core/{task_runtime,shell_policy,brain,verifier}.py`、`src/tools/__init__.py` 及对应核心测试。
   - 切分理由：先固化执行内核与策略层。
   - 回滚影响：仅影响工具执行/策略判定主链路，不影响 UI/入口层。

2. **`agents+dispatcher+file`**
   - 建议内容：`src/agents/{base,coder,file_agent}.py`、`src/core/dispatcher.py`、`prompts/agent_dispatcher.md`、`prompts/coder_workspace_*.md`、对应 agent 测试。
   - 切分理由：将上层业务能力与 runtime 内核解耦评审。
   - 回滚影响：影响 agent 路由与代码/文件执行能力，不影响 API/desktop。

3. **`app-container+telegram-entry`**
   - 建议内容：`src/app/{container,telegram_app,__init__}.py`、`main.py`、`tests/app/test_container.py`、`tests/app/test_telegram_app.py`、`tests/test_main_commands.py`。
   - 切分理由：单独审查入口重构与生命周期迁移。
   - 回滚影响：可整体回退到旧 `main.py` 启动模型。

4. **`task-view+api+desktop+closeout-docs`**
   - 建议内容：`src/app/task_view.py`、`src/api/{event_bus,server}.py`、`desktop/src/{api.ts,App.tsx}`、`tests/app/test_task_view.py`、`tests/api/test_server.py`、本收尾文档与 PR 模板。
   - 切分理由：统一任务读模型与展示链路一并评审。
   - 回滚影响：仅影响任务视图与 API/desktop 展示层，不影响核心对话执行。

## 7. 合并前检查清单（最终）

- [x] 全量 `pytest` 通过
- [x] desktop 构建通过
- [x] 关键回归子集通过（brain/runtime/api/dispatcher/coder/task-view）
- [x] 任务链路烟测通过（started -> executing -> completed）
- [x] 风险分级完成并记录后续动作
- [x] 提交切分方案完成
- [x] PR 模板完成（见同目录模板文件）
