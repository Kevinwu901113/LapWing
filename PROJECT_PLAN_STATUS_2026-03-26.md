# Lapwing 计划总览与完成标记（截至 2026-03-26）

> 目的：按“计划文档”逐条汇总当前进度，明确哪些已做完、哪些进行中、哪些未完成。
>
> 状态说明：
> - `✅ 已完成`：功能与代码、测试已落地
> - `🟡 进行中/部分完成`：核心已落地，但与计划描述仍有差距或仍在打磨
> - `⬜ 未完成`：未发现明确实现

---

## 一、来源 1：`CLAUDE.md` 开发路线图

### Phase 1 - 基础搭建

| 计划项 | 状态 | 代码依据 |
|---|---|---|
| Telegram Bot 基础框架 | ✅ 已完成 | [main.py](/home/kevin/lapwing/main.py) |
| LLM 接入（OpenAI 兼容） | ✅ 已完成 | [llm_router.py](/home/kevin/lapwing/src/core/llm_router.py) |
| 人格 prompt（lapwing.md） | ✅ 已完成 | [lapwing.md](/home/kevin/lapwing/prompts/lapwing.md) |
| 基础对话（私聊回复） | ✅ 已完成 | [brain.py](/home/kevin/lapwing/src/core/brain.py), [main.py](/home/kevin/lapwing/main.py) |
| `/start` 和 `/reload` 命令 | ✅ 已完成 | [main.py](/home/kevin/lapwing/main.py) |
| 内存对话历史 | ✅ 已完成 | [conversation.py](/home/kevin/lapwing/src/memory/conversation.py) |

### Phase 1.5 - 基础完善

| 任务 | 状态 | 代码依据 |
|---|---|---|
| 任务 1：持久化记忆（SQLite） | ✅ 已完成 | [conversation.py](/home/kevin/lapwing/src/memory/conversation.py) |
| 任务 2：多模型路由 | ✅ 已完成 | [llm_router.py](/home/kevin/lapwing/src/core/llm_router.py) |
| 任务 3：用户画像提取 | ✅ 已完成 | [fact_extractor.py](/home/kevin/lapwing/src/memory/fact_extractor.py) |
| 任务 4：主动消息 | ✅ 已完成 | [proactive.py](/home/kevin/lapwing/src/heartbeat/actions/proactive.py) |

### Phase 2 - Agent 团队

| 任务 | 状态 | 代码依据 |
|---|---|---|
| 任务 5：Agent 基础框架 | ✅ 已完成 | [base.py](/home/kevin/lapwing/src/agents/base.py), [dispatcher.py](/home/kevin/lapwing/src/core/dispatcher.py) |
| 任务 6：Researcher Agent | ✅ 已完成 | [researcher.py](/home/kevin/lapwing/src/agents/researcher.py) |
| 任务 7：Coder Agent | ✅ 已完成 | [coder.py](/home/kevin/lapwing/src/agents/coder.py) |

### Phase 3 - 自主意识

| 任务 | 状态 | 代码依据 |
|---|---|---|
| 任务 8：自主浏览 | 🟡 进行中/部分完成 | [autonomous_browsing.py](/home/kevin/lapwing/src/heartbeat/actions/autonomous_browsing.py) |
| 任务 9：兴趣图谱 | ✅ 已完成 | [interest_tracker.py](/home/kevin/lapwing/src/memory/interest_tracker.py), [conversation.py](/home/kevin/lapwing/src/memory/conversation.py) |
| 任务 10：主动分享发现 | ✅ 已完成 | [proactive.py](/home/kevin/lapwing/src/heartbeat/actions/proactive.py), [interest_proactive.py](/home/kevin/lapwing/src/heartbeat/actions/interest_proactive.py) |

### 额外项

| 计划项 | 状态 | 代码依据 |
|---|---|---|
| 语音消息支持（Whisper） | ✅ 已完成 | [transcriber.py](/home/kevin/lapwing/src/tools/transcriber.py), [main.py](/home/kevin/lapwing/main.py) |

### Phase 4 - 体验优化与问题修复

| 任务 | 状态 | 代码依据 |
|---|---|---|
| 任务 11：消息合并机制（防连发） | ✅ 已完成 | [main.py](/home/kevin/lapwing/main.py) |
| 任务 12：搜索功能修复（DDG + Bing 回退） | ✅ 已完成 | [web_search.py](/home/kevin/lapwing/src/tools/web_search.py) |
| 任务 13：人格 prompt 替换优化 | ✅ 已完成 | [lapwing.md](/home/kevin/lapwing/prompts/lapwing.md), [README.md](/home/kevin/lapwing/README.md) |

### Phase 5 - 动手能力与自我进化

| 任务 | 状态 | 代码依据 |
|---|---|---|
| 任务 14：Shell 执行引擎 | ✅ 已完成 | [shell_executor.py](/home/kevin/lapwing/src/tools/shell_executor.py), [brain.py](/home/kevin/lapwing/src/core/brain.py) |
| 任务 15：文件读写能力（tool + FileAgent） | ✅ 已完成 | [brain.py](/home/kevin/lapwing/src/core/brain.py), [file_agent.py](/home/kevin/lapwing/src/agents/file_agent.py) |
| 任务 16：自省与 Prompt 进化 | ✅ 已完成 | [self_reflection.py](/home/kevin/lapwing/src/core/self_reflection.py), [prompt_evolver.py](/home/kevin/lapwing/src/core/prompt_evolver.py), [main.py](/home/kevin/lapwing/main.py) |
| 任务 17：真正的持续自主浏览闭环 | 🟡 进行中/部分完成 | [autonomous_browsing.py](/home/kevin/lapwing/src/heartbeat/actions/autonomous_browsing.py), [knowledge_manager.py](/home/kevin/lapwing/src/core/knowledge_manager.py) |

**任务 17 说明**：
- 已有：周期浏览、搜索+抓取、知识沉淀、兴趣更新。
- 差距：计划中“浏览后自行决定是否即时分享”在当前实现里仍以“写 discovery，交由主动消息链路择机分享”为主。

### Phase 6 - 功能扩展

| 任务 | 状态 | 代码依据 |
|---|---|---|
| 任务 18：记忆管理界面（命令） | ✅ 已完成 | [main.py](/home/kevin/lapwing/main.py)（`/memory`、`/interests`） |
| 任务 19：RAG 长期记忆 | ✅ 已完成 | [vector_store.py](/home/kevin/lapwing/src/memory/vector_store.py), [brain.py](/home/kevin/lapwing/src/core/brain.py) |
| 任务 20：更多工具 Agent（weather/todo/file） | ✅ 已完成 | [weather_agent.py](/home/kevin/lapwing/src/agents/weather_agent.py), [todo_agent.py](/home/kevin/lapwing/src/agents/todo_agent.py), [file_agent.py](/home/kevin/lapwing/src/agents/file_agent.py) |
| 任务 21：桌面应用 | 🟡 进行中/部分完成 | [App.tsx](/home/kevin/lapwing/desktop/src/App.tsx), [server.py](/home/kevin/lapwing/src/api/server.py) |

**任务 21 说明**：
- 已有 MVP：状态看板、兴趣/记忆/学习日志查看、SSE 主动事件、桌面通知、触发进化与重载。
- 未见完整实现：任务看板（Agent 进度）、完整设置面板（模型配置/主动策略）。

---

## 二、来源 2：`TASKS.md`（A~D）

| 任务 | 状态 | 代码依据 | 测试依据 |
|---|---|---|---|
| 任务 A：`web_fetcher` 网页抓取 | ✅ 已完成 | [web_fetcher.py](/home/kevin/lapwing/src/tools/web_fetcher.py) | [test_web_fetcher.py](/home/kevin/lapwing/tests/tools/test_web_fetcher.py) |
| 任务 B：BrowserAgent | ✅ 已完成 | [browser.py](/home/kevin/lapwing/src/agents/browser.py), [main.py](/home/kevin/lapwing/main.py) | [test_browser.py](/home/kevin/lapwing/tests/agents/test_browser.py) |
| 任务 C：兴趣图谱（tracker + DB） | ✅ 已完成 | [interest_tracker.py](/home/kevin/lapwing/src/memory/interest_tracker.py), [conversation.py](/home/kevin/lapwing/src/memory/conversation.py) | [test_interest_tracker.py](/home/kevin/lapwing/tests/memory/test_interest_tracker.py), [test_conversation_interests.py](/home/kevin/lapwing/tests/memory/test_conversation_interests.py) |
| 任务 D：兴趣驱动主动分享 | ✅ 已完成 | [interest_proactive.py](/home/kevin/lapwing/src/heartbeat/actions/interest_proactive.py) | [test_interest_proactive.py](/home/kevin/lapwing/tests/heartbeat/actions/test_interest_proactive.py) |

---

## 三、来源 3：`TASKS_execution_fix.md`（紧急动手能力优化）

| 修复任务 | 状态 | 代码依据 | 备注 |
|---|---|---|---|
| 任务 A：强化 tool runtime instruction | ✅ 已完成 | [brain.py](/home/kevin/lapwing/src/core/brain.py) | 已加入强约束指令与禁止行为 |
| 任务 B：实时状态反馈 | 🟡 进行中/部分完成 | [brain.py](/home/kevin/lapwing/src/core/brain.py), [main.py](/home/kevin/lapwing/main.py) | 已有 `status_callback`，但目前是发送文本状态，不是仅 typing 指示 |
| 任务 C：增加 `read_file` / `write_file` 工具 | ✅ 已完成 | [brain.py](/home/kevin/lapwing/src/core/brain.py) | 工具 schema 与执行逻辑已接入 |

**与该文档策略差异（重要）**：
- 文档要求“权限问题自动换路径不问用户”；当前代码采用更安全策略：部分场景会要求用户确认替代路径（`shell_policy`）。

---

## 四、来源 4：`docs/superpowers/plans/*.md`

### 4.1 Multi-Model Routing Plan（2026-03-23）

| 计划块 | 状态 | 代码依据 |
|---|---|---|
| settings 增加 `LLM_CHAT_*`/`LLM_TOOL_*` | ✅ 已完成 | [settings.py](/home/kevin/lapwing/config/settings.py) |
| 新建 `LLMRouter` | ✅ 已完成 | [llm_router.py](/home/kevin/lapwing/src/core/llm_router.py) |
| brain 改走 router.complete | ✅ 已完成 | [brain.py](/home/kevin/lapwing/src/core/brain.py) |
| main 启动逻辑收敛 | ✅ 已完成 | [main.py](/home/kevin/lapwing/main.py) |
| `.env.example` 更新 | ✅ 已完成 | [config/.env.example](/home/kevin/lapwing/config/.env.example) |
| 测试覆盖 | ✅ 已完成 | [test_llm_router.py](/home/kevin/lapwing/tests/core/test_llm_router.py) |

### 4.2 Heartbeat Implementation Plan（2026-03-23）

| 计划块 | 状态 | 代码依据 |
|---|---|---|
| 心跳配置与依赖 | ✅ 已完成 | [settings.py](/home/kevin/lapwing/config/settings.py), [requirements.txt](/home/kevin/lapwing/requirements.txt) |
| discoveries 表与方法 | ✅ 已完成 | [conversation.py](/home/kevin/lapwing/src/memory/conversation.py) |
| `force_extraction()` | ✅ 已完成 | [fact_extractor.py](/home/kevin/lapwing/src/memory/fact_extractor.py) |
| router 增加 `heartbeat` purpose | ✅ 已完成 | [llm_router.py](/home/kevin/lapwing/src/core/llm_router.py) |
| HeartbeatEngine + ActionRegistry + SenseLayer | ✅ 已完成 | [heartbeat.py](/home/kevin/lapwing/src/core/heartbeat.py) |
| proactive/consolidation 动作 | ✅ 已完成 | [proactive.py](/home/kevin/lapwing/src/heartbeat/actions/proactive.py), [consolidation.py](/home/kevin/lapwing/src/heartbeat/actions/consolidation.py) |
| 主流程生命周期接入 | ✅ 已完成 | [main.py](/home/kevin/lapwing/main.py) |
| 测试覆盖 | ✅ 已完成 | [tests/heartbeat](/home/kevin/lapwing/tests/heartbeat), [test_conversation_discoveries.py](/home/kevin/lapwing/tests/memory/test_conversation_discoveries.py) |

### 4.3 Heartbeat 设计文档（spec）

| 文档状态字段 | 当前实际状态 | 说明 |
|---|---|---|
| 标注为“待实现” | 🟡 文档已过期 | 实现已落地，但 spec 头部状态未回写更新。见 [2026-03-23-heartbeat-design.md](/home/kevin/lapwing/docs/superpowers/specs/2026-03-23-heartbeat-design.md) |

---

## 五、总体结论（可直接用于汇报）

1. **核心计划基本完成**：Phase 1~5 主体能力已落地，Phase 6 已进入整合优化期。
2. **明确进行中项**：
   - 任务 17（持续自主浏览闭环）
   - 任务 21（桌面端从 MVP 到完整控制台）
   - execution_fix 的任务 B（状态反馈形式仍可优化）
3. **需要补齐的管理动作**：
   - 文档状态回写（spec 中“待实现”改为已实现）
   - 将当前未提交的大改动按模块拆分提交，形成稳定里程碑

---

## 六、校验快照（当前代码基线）

- 分支：`master`
- HEAD：`f2ef4b7`
- 测试：`296 passed, 3 warnings`

