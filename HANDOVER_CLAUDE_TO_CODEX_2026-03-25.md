# Lapwing 项目交接记录（Claude → Codex）

更新时间：2026-03-25
仓库路径：`/home/kevin/lapwing`
当前分支：`master`（工作区未清理，存在大量未提交变更）

---

## 1. 项目当前内容（代码层）

### 1.1 核心运行形态
- Telegram Bot 主入口：`main.py`
- 架构主链路：`main.py -> LapwingBrain -> AgentDispatcher / Tool Loop / HeartbeatEngine`
- 异步技术栈：Python 3.11 + asyncio + aiosqlite + python-telegram-bot

### 1.2 关键能力模块
- 对话与路由
  - `src/core/brain.py`：主对话流程、tool loop（function calling）、记忆注入、agent dispatch 兜底
  - `src/core/llm_router.py`：按 `purpose` 路由模型（`chat/tool/heartbeat`），支持 OpenAI/Anthropic 兼容协议
- Agent 体系
  - 已有 Agent：`researcher`、`coder`、`browser`、`file`、`weather`、`todo`
  - 文件：`src/agents/*.py`
  - 分发器：`src/core/dispatcher.py`
- 记忆体系
  - `src/memory/conversation.py`：SQLite 表含 conversations / user_facts / discoveries / interest_topics / todos
  - `src/memory/fact_extractor.py`：用户画像提取
  - `src/memory/interest_tracker.py`：兴趣提取与权重更新
  - `src/memory/vector_store.py`：Chroma 向量检索
- 工具层
  - `src/tools/shell_executor.py`：受限 shell 执行 + 日志
  - `src/tools/web_search.py`：DDG 主搜索 + Bing 回退
  - `src/tools/web_fetcher.py`：HTML 抓取与正文提取
  - `src/tools/code_runner.py`、`src/tools/transcriber.py`
- 心跳与自主行为
  - 引擎：`src/core/heartbeat.py`
  - Action：`proactive` / `interest_proactive` / `consolidation` / `self_reflection` / `prompt_evolution`
- 桌面侧（MVP）
  - 本地 API：`src/api/server.py` + `src/api/event_bus.py`
  - 前端：`desktop/`（React + Vite + Tauri）

---

## 2. 需求进度评估

### 2.1 对照 `TASKS.md`（A~D）
结论：**A/B/C/D 均已落地，并有对应测试文件**。

- 任务 A（web_fetcher）
  - 实现：`src/tools/web_fetcher.py`
  - 测试：`tests/tools/test_web_fetcher.py`
- 任务 B（BrowserAgent）
  - 实现：`src/agents/browser.py`，并在 `main.py` 注册
  - 测试：`tests/agents/test_browser.py`
- 任务 C（InterestTracker + interest_topics）
  - 实现：`src/memory/interest_tracker.py` + `ConversationMemory` 兴趣表与接口
  - 测试：`tests/memory/test_interest_tracker.py`、`tests/memory/test_conversation_interests.py`
- 任务 D（InterestProactiveAction）
  - 实现：`src/heartbeat/actions/interest_proactive.py`
  - 测试：`tests/heartbeat/actions/test_interest_proactive.py`

### 2.2 对照 `TASKS_execution_fix.md`
结论：**主目标已实现，存在策略差异**。

- 已完成
  - 强化 tool runtime instruction（`brain.py`）
  - tool loop 增加状态回调参数（`status_callback`）
  - 增加 `read_file` / `write_file` 工具
- 与文档不完全一致点
  - 状态反馈当前是发送消息（`_bot.send_message`），不是纯 `typing` 指示
  - 引入了更强安全策略：路径约束 + 替代路径需用户确认（`src/core/shell_policy.py`）

### 2.3 对照 `CLAUDE.md` 阶段路线
- Phase 1~3：已完成（与代码一致）
- Phase 4：核心项已可用（消息合并、搜索增强、人格替换）
- Phase 5：14/15/16 基本已实现（shell、文件能力、自省与 prompt 进化）
- 任务 17（“真正自主浏览”）：**未完全落地**
  - 当前有 BrowserAgent 与兴趣驱动主动搜索
  - 但未看到独立的持续自主浏览模块（如 `autonomous_browsing.py` 周期探索管线）
- Phase 6 中 18/19/20/21 已出现实装痕迹
  - 18：`/memory`、`/interests` 命令 + API
  - 19：向量记忆（Chroma）接入
  - 20：weather/todo/file agent 已有
  - 21：桌面端与本地 API 已有 MVP

---

## 3. 测试与可运行性

### 3.1 全量测试
执行命令：
```bash
source venv/bin/activate && pytest -q
```
结果：
- `266 passed`
- `2 warnings`
- 总耗时约 `11.63s`

### 3.2 警告（非阻塞）
- 文件：`tests/core/test_llm_router.py`
- 现象：两个同步测试函数被加了 `@pytest.mark.asyncio`
- 影响：不影响通过，但建议清理标注噪音

---

## 4. 当前工作区状态（接手必读）

### 4.1 Git 状态
- 分支：`master`
- 状态：dirty（大量 `M` + `??`）
- 关键变化范围：
  - 核心：`main.py`、`src/core/brain.py`、`src/core/llm_router.py`
  - 新增：`src/api/`、`src/core/shell_policy.py`、`src/agents/todo_agent.py`、`src/agents/weather_agent.py`、`src/memory/vector_store.py`、`src/tools/shell_executor.py`
  - 测试：多模块新增/扩展
  - 前端：`desktop/` 目录为未跟踪（含源码与构建产物）

### 4.2 文档一致性问题
- `README.md` 仍写“当前阶段 Phase 1”，与代码现实不符
- `TASKS.md` 中测试基线仍是 `145`，实际已到 `266`
- `CLAUDE.md` 阶段描述与当前代码进度存在时间差

---

## 5. 已识别风险与缺口

1. Shell 行为策略存在“执行果断”与“安全确认”的张力
- 代码目前偏安全优先（路径约束 + consent），与“不要问用户直接做”的目标存在冲突。

2. 状态回调可能带来消息噪音
- 多步任务时当前通过发消息提示进度，可能影响聊天体验。

3. README / 任务文档明显过期
- 接手人员若只看 README 会误判进度。

4. 桌面端产物管理尚未收敛
- `desktop/` 未跟踪，后续需要明确是否提交源码、是否忽略 `dist/`。

5. 自主浏览能力仍偏“半自动”
- 已有搜索与主动分享，但缺少完整的自主探索-沉淀-分享闭环调度模块。

---

## 6. 建议的下一步（交接后优先级）

1. 先做文档对齐（README/TASKS/CLAUDE 的统一状态基线）。
2. 明确 Shell 策略取舍：
   - 路径偏移是否必须确认
   - sudo 策略与提示词是否一致
3. 收敛 `desktop/` 的版本管理策略（源码、构建产物、发布流程）。
4. 若继续推进任务 17，补全“真正自主浏览”独立模块与测试。

---

## 7. 快速复现指令

```bash
cd /home/kevin/lapwing
source venv/bin/activate
pytest -q
python main.py
```

