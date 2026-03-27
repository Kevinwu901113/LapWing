# PR 模板：Sprint 1/2/3/4 汇总合并

## 背景

- 本 PR 汇总 Sprint 1/2/3/4 的重构与能力增强，目标是将系统推进到“可合并、可交付、可追溯”状态。

## 范围

- 包含：
  - 执行骨架重构（task runtime + 生命周期事件）
  - 文件编辑/统一验证/coder 多轮修复
  - 工具注册与策略分层
  - 入口解耦（container + telegram adapter）
  - 统一 task 视图（task projection + 只读任务 API + desktop 面板）
- 不包含：
  - 任务控制写接口（cancel/retry）
  - 版本号升级与正式发布动作

## 关键变更

1. Runtime/Policy/Tools：
   - `registry + policy` 替代 runtime 工具硬编码分支
2. Agent 层：
   - `CoderAgent` 双模式 + 多轮修复闭环
   - `FileAgent` 复用 `file_editor`
3. 入口层：
   - `main.py` 薄入口化
   - 新增 `AppContainer` 与 `TelegramApp`
4. 任务视图：
   - 新增 `TaskViewStore`
   - 新增 `GET /api/tasks` 与 `GET /api/tasks/{task_id}`
   - desktop 接入统一任务读模型

## 对外契约变化

- 新增只读 API：
  - `GET /api/tasks?chat_id=&status=&limit=`
  - `GET /api/tasks/{task_id}`
- 兼容承诺：
  - 现有 API/SSE 保持兼容
  - `task.*` 事件结构保持兼容
  - `python main.py` 启动方式保持兼容

## 验证证据

- 全量测试：
  - `./venv/bin/pytest -q`
  - 结果：`341 passed, 3 warnings`
- 关键回归子集：
  - `./venv/bin/pytest -q tests/core/test_brain_tools.py tests/core/test_task_runtime.py tests/api/test_server.py tests/agents/test_dispatcher.py tests/agents/test_coder.py tests/app/test_task_view.py tests/app/test_telegram_app.py`
  - 结果：`71 passed`
- desktop 构建：
  - `npm run build`
  - 结果：成功

## 风险与处理

- 已知非阻断：
  - `llm_router` 3 条 asyncio 测试 warning（后续清理）
  - `TaskViewStore` 内存态（重启后清空）
  - 单进程事件总线（扩展部署时需外部总线）
- 阻断级别：
  - P0/P1：无
  - P2/P3：已记录并有后续动作

## 回滚方案

- 若出现入口层问题：回滚 `app/container + telegram adapter + main.py` 相关提交。
- 若出现任务视图问题：回滚 `task_view + api/tasks + desktop` 相关提交，不影响核心执行链。
- 若出现 runtime 行为问题：回滚 `runtime+policy+registry` 提交并恢复硬编码分支（历史版本）。
