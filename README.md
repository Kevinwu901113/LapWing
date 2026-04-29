# Lapwing — Architecture Reference

> 本文档面向 AI 架构分析。目标：仅阅读此文件即可理解代码结构、模块职责、依赖关系和数据流。

---

## 项目概述

Lapwing 是一个 24/7 运行的自主 AI 伴侣系统——具有人格、记忆、自我进化能力的虚拟女友，不是 bot 框架。

| 维度 | 详情 |
|------|------|
| **后端** | Python 3.12+，~41,500 行代码，177 个源文件 |
| **前端** | Tauri v2 + React 19 + TypeScript，~5,100 行，69 个文件 |
| **测试** | pytest + pytest-asyncio，`asyncio_mode = auto`，无 CI |
| **LLM** | MiniMax M2.7（Anthropic 兼容 API）、GLM、NVIDIA NIM、Codex OAuth |
| **消息通道** | QQ（NapCat WebSocket）、Desktop（本地 SSE/WebSocket） |
| **存储** | SQLite（WAL：`lapwing.db` + `mutation_log.db`）、ChromaDB 向量库、Markdown 身份文件 |
| **部署** | PVE 服务器（Xeon E-2174G, 32GB），systemd，无 CI/CD |

---

## 全局架构

```
main.py  ──→  AppContainer.prepare() / start()       (src/app/container.py — DI 根)
                 │
                 ├── LapwingBrain                     ← 所有请求的唯一入口
                 │    ├── LLMRouter                   ← 多 slot 模型路由
                 │    ├── TaskRuntime                 ← 工具循环执行层
                 │    ├── StateViewBuilder/Serializer ← prompt 组装
                 │    ├── TrajectoryStore             ← 对话单一真相源（SQLite）
                 │    ├── StateMutationLog            ← LLM/工具事件日志（SQLite）
                 │    ├── AgentRegistry               ← Researcher / Coder 子智能体
                 │    └── 可选依赖                     ← skill_manager / browser_manager / vector_store / …
                 │
                 ├── MainLoop + EventQueue            ← 单消费者，优先级 OWNER>TRUSTED>SYSTEM>INNER
                 ├── InnerTickScheduler               ← 自主 inner tick（自适应退避）
                 ├── MaintenanceTimer                 ← 每日 3AM 语义蒸馏
                 ├── DurableScheduler                 ← 持久化提醒（reminders_v2 表）
                 ├── ChannelManager                   ← QQ + Desktop 适配器路由
                 └── LocalApiServer                   ← FastAPI + SSE/WebSocket，供桌面端消费
```

### 核心设计原则

1. **裸文本即用户消息**：模型裸 assistant 文本 → 用户可见；伴随 tool_call 的文本 → 内部 scratch 不发送。`send_message` 工具仅用于无对话上下文的主动消息（inner tick / 提醒）。
2. **无 agent dispatch 层**：所有能力注册为 `ToolSpec`，LLM 通过 tool_calls 自行决定调用。子 agent（researcher / coder）也是工具。
3. **人格与行为分离**：`data/identity/soul.md` 定义"她是谁"，`prompts/lapwing_voice.md` 用 ✕/✓ 对比约束行为边界。
4. **TrajectoryStore 是对话真相**：取代了过去的 `ConversationMemory` 缓存层。所有读写直达 SQLite，无独立缓存。
5. **可选依赖注入**：Brain 子系统全部可选（默认 `None`），由 `config/.env` 中的 feature flag 开关，调用前用 `getattr(brain, "...", None)`。

---

## 消息完整流转

```
用户消息 (QQ / Desktop)
  │
  ▼
通道适配器（src/adapters/qq_adapter.py 或 desktop_adapter.py）
  └── 投递事件到 EventQueue
        │
        ▼
MainLoop（按优先级消费）
  └── 调用 LapwingBrain.think_conversational(
        chat_id, user_message, send_fn,
        typing_fn=None, status_callback=None,
        adapter="", user_id="",
        metadata=None, images=None,
      )
        │
        ├── TrustTagger.tag(adapter, user_id)        # 0=GUEST / 1=TRUSTED / 2=OWNER
        ├── StateViewBuilder.build(chat_id)          # soul/voice/rules/trajectory/notes/commitments
        ├── StateSerializer.serialize(state_view)    # 纯函数 → prompt 字节
        │
        ▼
Brain._complete_chat()
  └── TaskRuntime.complete_chat()
        │
        ├── LLMRouter.complete_with_tools(messages, tools, purpose)
        │     → ToolTurnResult { text, tool_calls, continuation_message }
        │
        ├── [for each tool_call]
        │     ├── 循环检测（src/utils/loop_detection.py，observation 模式）
        │     ├── VitalGuard.check()                  # 核心文件保护
        │     ├── AuthorityGate.authorize()           # 三级权限校验
        │     └── ToolRegistry.execute()              # 实际执行
        │
        └── 裸文本通过 send_fn 实时回传，TrajectoryStore 记录每一轮
```

---

## 源码结构（src/，~177 个 .py，~41,500 行）

### src/core/（52 文件，~16,800 行）— 核心业务逻辑

**请求处理链：**

| 文件 | 行数 | 职责 |
|------|------|------|
| `brain.py` | 1125 | **门面类**。`think_conversational()` 是所有用户消息的唯一入口，trust tag → 构 StateView → 委托 TaskRuntime |
| `task_runtime.py` | 2136 | **工具循环执行层**。`complete_chat()` 是核心循环：调 LLM → 执行工具 → 追加结果 → 重复。内含循环检测和断路器 |
| `llm_router.py` | 1809 | **模型路由**。按 purpose slot（chat/tool/heartbeat/fallback）路由到不同模型/API。自动检测 Anthropic vs OpenAI 兼容端点 |
| `llm_protocols.py` | 371 | Anthropic SDK 调用封装、tool_calls 解析、prefix caching |
| `llm_types.py` | 31 | `ToolCallRequest`、`ToolTurnResult` |
| `state_view.py` | 219 | `StateView` 数据结构（包含 trajectory turns、note buffers、commitments） |
| `state_view_builder.py` | 598 | StateView 构建 + voice depth-0 reminder 注入 |
| `state_serializer.py` | 303 | StateView → 字符串 prompt 的纯函数序列化 |
| `prompt_loader.py` | 45 | 从 `prompts/` 热加载 Markdown |
| `runtime_profiles.py` | 158 | **工具剖面**。9 个 profile：`chat_shell`、`chat_minimal`、`chat_extended`、`task_execution`、`coder_snippet`、`coder_workspace`、`file_ops`、`agent_researcher`、`agent_coder` |
| `task_types.py` | 216 | `RuntimeDeps`、`LoopDetectionConfig/State`、`TaskLoopStep/Result` |
| `task_runtime.py` 配套 | — | 见上 |

**v2.0 调度与事件循环：**

| 文件 | 行数 | 职责 |
|------|------|------|
| `main_loop.py` | 371 | 单消费者事件循环 |
| `event_queue.py` | 72 | 优先级队列（OWNER > TRUSTED > SYSTEM > INNER） |
| `events.py` | 164 | Event 类型定义 |
| `inner_tick_scheduler.py` | 357 | 自主 inner tick 调度（替代了已删除的 HeartbeatEngine） |
| `durable_scheduler.py` | 803 | 持久化提醒（reminders_v2 表）+ 承诺（commitments 表） |
| `maintenance_timer.py` | 131 | 每日 3AM 语义蒸馏触发器 |
| `dispatcher.py` | 124 | 事件分发到 brain |

**安全与权限：**

| 文件 | 行数 | 职责 |
|------|------|------|
| `authority_gate.py` | 141 | 三级权限：OWNER(2) / TRUSTED(1) / GUEST(0)。按工具过滤 |
| `vital_guard.py` | 189 | 核心文件保护。Shell/文件写入前检查目标路径，判定 PASS / VERIFY_FIRST / BLOCK |
| `shell_policy.py` | 658 | Shell 执行策略 + ACL 白名单 |
| `verifier.py` | 362 | Shell 约束验证 |
| `credential_vault.py` | 144 | 加密凭据存储 |
| `credential_sanitizer.py` | 121 | 敏感信息脱敏 |
| `output_sanitizer.py` | 34 | 模型输出敏感词过滤 |
| `execution_sandbox.py` | 261 | 统一 sandbox 抽象（SandboxTier: STRICT/STANDARD/PRIVILEGED） |
| `trust_tagger.py` | 42 | 用户身份信任标签 |

**存储：**

| 文件 | 行数 | 职责 |
|------|------|------|
| `trajectory_store.py` | 545 | **对话真相源**。`trajectory` 表，所有用户/模型 turn 记录 |
| `commitments.py` | 341 | 承诺解析与管理 |
| `focus_manager.py` | 842 | 焦点（topic）连续性、休眠/激活、摘要 |
| `focus_archiver.py` | 129 | 焦点归档 |
| `attention.py` | 280 | 注意力分配 |
| `soul_manager.py` | 222 | soul.md 读写（VitalGuard 保护） |
| `identity_file_manager.py` | 180 | 身份 Markdown 文件统一管理 |

**浏览器与多模态：**

| 文件 | 行数 | 职责 |
|------|------|------|
| `browser_manager.py` | 1424 | Playwright 持久化上下文。Tab 管理、DOM 提取、截图、视觉理解管线 |
| `minimax_vlm.py` | 90 | MiniMax 视觉模型客户端 |

**其他核心：**

| 文件 | 行数 | 职责 |
|------|------|------|
| `channel_manager.py` | 154 | 多通道路由。主动消息优先级：active session > last_active > 任意连接 |
| `vitals.py` | 270 | 启动时间、uptime、重启感知、系统快照（CPU/内存/磁盘） |
| `model_config.py` | 768 | 运行时模型切换。持久化到 `data/config/model_routing.json` |
| `codex_oauth_client.py` | 380 | Codex OAuth 客户端 |
| `proxy_router.py` | 422 | HTTP 代理路由（按域名/服务） |
| `system_send.py` | 134 | 系统主动发送（inner tick → channel） |
| `intent_router.py` | 78 | 意图分类（轻量级路由） |
| `group_filter.py` | 192 | QQ 群消息过滤逻辑 |
| `task_model.py` / `plan_state.py` | 178 / 124 | 任务/计划数据模型 |
| `reasoning_tags.py` | 114 | `<think>` 标签处理（剥离/保留） |

---

### src/tools/（22 文件，~7,000 行）— 工具系统

**核心框架：**

| 文件 | 行数 | 职责 |
|------|------|------|
| `types.py` | 81 | `ToolSpec`、`ToolExecutionRequest`、`ToolExecutionContext`、`ToolExecutionResult` |
| `registry.py` | 500 | `build_default_tool_registry()` 注册所有工具，`chat_tools()` 按 RuntimeProfile 过滤 |
| `shell_executor.py` | 298 | Shell 子进程管理 |

**工具实现：**

| 文件 | 行数 | 主要工具 |
|------|------|---------|
| `handlers.py` | 297 | `execute_shell`、`run_python_code`、`verify_code_result` 等 |
| `browser_tools.py` | 808 | `browser_navigate`、`browser_click`、`browser_type`、`browser_screenshot` 等 |
| `file_editor.py` | 893 | `file_read_segment`、`file_write`、`file_append`、`file_list_directory` |
| `memory_tools_v2.py` | 362 | `recall`、`write_note`、`edit_note`、`list_notes`、`forget_note`（接 NoteStore） |
| `personal_tools.py` | 525 | 个人化工具（用户偏好、提醒等） |
| `skill_tools.py` | 688 | `search_skill` / `install_skill` / 技能列表 |
| `commitments.py` | 294 | 承诺创建/查询工具 |
| `agent_tools.py` | 196 | `dispatch_researcher` / `dispatch_coder` 子智能体调用 |
| `ambient_tools.py` | 489 | 环境感知（时间、农历、节假日） |
| `focus_tools.py` | 129 | 焦点查询工具 |
| `plan_tools.py` | 190 | 计划写入工具 |
| `research_tool.py` | 93 | 包装 `src/research/engine.py` |
| `sports_tool.py` | 345 | 体育赛程查询（NBA/MLB） |
| `timezone_tools.py` | 135 | 时区相关 |
| `soul_tools.py` | 99 | soul.md 读写（受 VitalGuard 限制） |
| `correction_tools.py` | 59 | 纠错回写 |
| `workspace_tools.py` | 96 | 工作区元信息 |
| `code_runner.py` | 55 | Python 代码执行 |

---

### src/memory/（10 文件，~2,100 行）— 记忆系统（v2.0 重写）

```
┌─────────────────────────────────────────────────────┐
│ Layer 4: VectorStore (ChromaDB)                     │ ← 语义检索
│   vector_store.py（含 MemoryVectorStore 单例）       │
├─────────────────────────────────────────────────────┤
│ Layer 3: Episodic / Semantic Stores (SQLite)        │ ← 长期记忆
│   episodic_store.py / semantic_store.py             │
├─────────────────────────────────────────────────────┤
│ Layer 2: NoteStore (Markdown + SQLite 索引)          │ ← LLM 自管笔记
│   note_store.py                                     │
├─────────────────────────────────────────────────────┤
│ Layer 1: TrajectoryStore (SQLite, in src/core/)     │ ← 对话真相源
└─────────────────────────────────────────────────────┘
```

| 文件 | 行数 | 职责 |
|------|------|------|
| `vector_store.py` | 528 | ChromaDB 封装，`MemoryVectorStore` 全局单例 |
| `note_store.py` | 302 | Markdown 笔记 + SQLite 索引 |
| `episodic_store.py` | 251 | 情景记忆条目（来自焦点 dormant） |
| `semantic_store.py` | 252 | 语义记忆（蒸馏后的长期事实） |
| `episodic_extractor.py` | 206 | LLM 抽取情景记忆 |
| `semantic_distiller.py` | 174 | 每日 3AM 语义蒸馏（episodic → semantic） |
| `incident_store.py` | 165 | 事件/事故记录 |
| `working_set.py` | 160 | 工作记忆缓存 |
| `embedding_worker.py` | 45 | 异步 embedding 后台任务 |

> 注：`ConversationMemory`、`MemoryIndex`、`FileMemory`、`UserFacts`、`InterestTracker`、`reminders.py`、`todos.py`、`discoveries.py` 等已在 v2.0 MVP cleanup 中删除（commit b6e3cbd）。

---

### src/identity/（9 文件，~3,200 行）— 身份基底（identity-substrate, ticket A 进行中）

| 文件 | 行数 | 职责 |
|------|------|------|
| `store.py` | 1104 | IdentityStore：事件源 SQLite + ChromaDB |
| `parser.py` | 790 | LLM claim 解析器 |
| `__main__.py` | 385 | CLI 入口（rebuild、查询） |
| `models.py` | 316 | IdentityClaim / IdentityBlock 数据模型 |
| `retriever.py` | 289 | 嵌入检索（按上下文相关度评分） |
| `vector_index.py` | 165 | ChromaDB 索引 |
| `auth.py` | 114 | 身份相关认证检查 |
| `flags.py` | 46 | feature flag |
| `migrations/` | — | 迁移脚本 |

---

### src/agents/（6 文件，~590 行）— 子智能体

| 文件 | 行数 | 职责 |
|------|------|------|
| `base.py` | 359 | `BaseAgent` 抽象 |
| `researcher.py` | 73 | Researcher agent（深度查询） |
| `coder.py` | 69 | Coder agent（代码任务） |
| `registry.py` | 34 | Agent 注册表 |
| `types.py` | 56 | AgentRequest / AgentResult |

调用入口：`src/tools/agent_tools.py` 的 `dispatch_researcher` / `dispatch_coder` 工具。

---

### src/research/（7 文件，~730 行）— 研究子系统

| 文件 | 行数 | 职责 |
|------|------|------|
| `engine.py` | 152 | 研究主流程 |
| `fetcher.py` | 242 | HTTP 抓取（含 proxy 路由） |
| `refiner.py` | 175 | 结果精炼 |
| `scope_router.py` | 76 | 查询范围路由 |
| `prompts.py` | 40 | 内嵌 prompt |
| `types.py` | 48 | 数据类型 |
| `backends/` | — | 搜索后端（Tavily / DuckDuckGo） |

---

### src/skills/（4 文件，~770 行）— 技能系统

| 文件 | 行数 | 职责 |
|------|------|------|
| `skill_store.py` | 310 | 技能存储（含 search_skill / install_skill 实现） |
| `skill_capturer.py` | 279 | 从执行轨迹捕获技能候选 |
| `skill_executor.py` | 183 | 技能执行（沙箱化） |

---

### src/feedback/（3 文件，~270 行）— 纠错系统

| 文件 | 行数 | 职责 |
|------|------|------|
| `correction_store.py` | 185 | SQLite 持久化纠错 |
| `correction_manager.py` | 83 | 运行时纠错管理 |

---

### src/ambient/（4 文件，~750 行）— 环境感知

| 文件 | 行数 | 职责 |
|------|------|------|
| `ambient_knowledge.py` | 198 | 环境知识聚合（aiosqlite） |
| `preparation_engine.py` | 283 | 上下文准备 |
| `time_context.py` | 193 | 时间/农历/节假日上下文 |
| `models.py` | 72 | Ambient 数据模型 |

---

### src/app/（3 文件，~1,300 行）— 应用容器

| 文件 | 行数 | 职责 |
|------|------|------|
| `container.py` | 1057 | **DI 根**。`prepare()` → 初始化 DB、装配 Brain 依赖；`start()` → 启动调度/通道/API；`shutdown()` → 逆序清理 |
| `task_view.py` | 219 | TaskViewStore，桌面端任务执行遥测 |

> 注：原 `telegram_app.py` / `telegram_delivery.py` 已删除，Telegram 通道下线。

---

### src/adapters/（6 文件，~900 行）— 消息通道适配器

| 文件 | 行数 | 职责 |
|------|------|------|
| `base.py` | 42 | `BaseAdapter` + `ChannelType` 枚举（QQ / DESKTOP） |
| `desktop_adapter.py` | 83 | SSE 推送的桌面端适配器 |
| `qq_adapter.py` | 589 | OneBot v11 WebSocket 适配 |
| `qq_group_context.py` | 55 | QQ 群上下文 |
| `qq_group_filter.py` | 133 | QQ 群消息过滤 |

---

### src/api/（21 文件，~2,700 行）— Desktop API

| 文件 | 行数 | 职责 |
|------|------|------|
| `server.py` | 319 | FastAPI 启动 + SSE 端点 + 路由挂载 |
| `event_bus.py` | 62 | 事件发布（桌面端 SSE 推送） |
| `model_routing.py` | 124 | 运行时模型选择 API |
| `desktop_auth.py` | 29 | 桌面端 token 认证 |
| `routes/auth.py` | 110 | 认证路由 |
| `routes/chat_ws.py` | 212 | WebSocket 对话路由 |
| `routes/browser.py` | 84 | 浏览器控制路由 |
| `routes/agents.py` | 44 | Agent 管理 |
| `routes/identity.py` | 108 | 身份管理（v2） |
| `routes/identity_claims.py` | 363 | 身份 claim 路由（identity-substrate） |
| `routes/events_v2.py` | 111 | 事件查询 |
| `routes/life_v2.py` | 432 | 人生图景（"她的生活"） |
| `routes/models_v2.py` | 155 | 模型管理 |
| `routes/notes_v2.py` | 89 | 笔记管理 |
| `routes/permissions_v2.py` | 120 | 权限管理 |
| `routes/skills_v2.py` | 41 | 技能管理 |
| `routes/status_v2.py` | 70 | 状态查询 |
| `routes/system_v2.py` | 149 | 系统信息 |
| `routes/tasks_v2.py` | 56 | 任务管理 |

---

### src/auth/（6 文件，~1,600 行）— 认证

| 文件 | 行数 | 职责 |
|------|------|------|
| `service.py` | 1010 | AuthManager。多策略认证（API key、OAuth、桌面 token） |
| `storage.py` | 296 | 认证配置文件持久化 |
| `openai_codex.py` | 222 | Codex OAuth 流程 |
| `models.py` | 64 | 认证数据模型 |
| `resolver.py` | 39 | 用户 ID 解析 |

---

### src/logging/（1 文件，~420 行）

| 文件 | 行数 | 职责 |
|------|------|------|
| `state_mutation_log.py` | 422 | **StateMutationLog**：LLM 调用、工具调用、工具结果的权威事件日志（`data/mutation_log.db`） |

---

### src/utils/（8 文件，~500 行）

| 文件 | 行数 | 职责 |
|------|------|------|
| `loop_detection.py` | 181 | 工具循环检测（observation 模式，默认不阻断） |
| `circuit_breaker.py` | 80 | LLM 断路器 |
| `retry.py` | 53 | 重试包装 |
| `path_resolver.py` | 42 | 路径解析 |
| `conversation.py` | 41 | 对话工具 |
| `text.py` | 28 | 文本处理 |
| `url_safety.py` | 73 | URL 安全检查 |

### 其他 src/ 子包

| 包 | 用途 |
|------|------|
| `src/guards/` | `memory_guard.py`（记忆写入扫描） |
| `src/models/` | `message.py`（RichMessage 跨通道富媒体消息） |
| `src/config/` | `settings.py`（环境变量 + config.toml 加载） |

---

## 关键数据类型

```python
# --- 工具系统 (src/tools/types.py) ---

@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str                   # 展示给 LLM
    json_schema: dict                  # OpenAI function calling 格式
    executor: ToolExecutor             # async (req, ctx) -> result
    capability: str                    # 主标签 (shell/web/file/memory/schedule/skill/code/general/browser)
    capabilities: tuple[str, ...]      # 附加标签
    visibility: "model" | "internal"   # internal 不展示给 LLM
    risk_level: "low" | "medium" | "high"

@dataclass(frozen=True)
class ToolExecutionContext:
    execute_shell: Callable
    shell_default_cwd: str
    workspace_root: str
    services: dict[str, Any]           # 注入 skill_manager / note_store / vector_store / …
    adapter: str                       # "qq" / "desktop"
    user_id: str
    auth_level: int                    # 0=GUEST, 1=TRUSTED, 2=OWNER
    chat_id: str

# --- 任务类型 (src/core/runtime_profiles.py) ---

@dataclass(frozen=True)
class RuntimeProfile:
    name: str                          # 9 个 profile（见 runtime_profiles.py）
    capabilities: frozenset[str]
    tool_names: frozenset[str]
    include_internal: bool
    shell_policy_enabled: bool
```

---

## 模块依赖图

```
                          ┌─────────────┐
                          │   main.py   │
                          └──────┬──────┘
                                 │
                          ┌──────▼──────┐
                          │AppContainer │
                          └──────┬──────┘
                ┌────────────────┼────────────────────────┐
                │                │                        │
         ┌──────▼──────┐  ┌──────▼─────────┐   ┌─────────▼─────────┐
         │ LapwingBrain│  │InnerTickSched. │   │ ChannelManager    │
         │ (门面)       │  │MaintenanceTimer│   │ ├─QQAdapter       │
         └──────┬──────┘  │DurableSched.   │   │ └─DesktopAdapter  │
                │         └────────────────┘   └───────────────────┘
   ┌────────────┼─────────────┐
   │            │             │
┌──▼──────┐ ┌──▼─────┐ ┌──────▼────────┐
│StateView│ │TaskRtm │ │TrajectoryStore│
│ Builder │ │(工具循环)│ │  (单一真相)    │
└─────────┘ └──┬─────┘ └───────────────┘
               │
   ┌───────────┼─────────────┐
   │           │             │
┌──▼─────┐ ┌──▼────┐ ┌──────▼──────┐
│LLMRoutr│ │Tools  │ │ Guards      │
│(多 slot)│ │Regis. │ │ ├VitalGuard │
└────────┘ └───────┘ │ └AuthGate   │
                     └─────────────┘
```

**依赖方向规则：**
- `core/` 模块之间可互相引用
- `tools/` 依赖 `core/`（通过 `ToolExecutionContext.services` 注入）
- `memory/` / `identity/` / `skills/` 等数据子包被 `core/` 和 `tools/` 引用
- `guards/` 被 `core/task_runtime.py` 在工具执行前调用
- `adapters/` 和 `api/` 依赖 `core/brain.py` 和 `models/`
- 所有模块依赖 `config/settings.py`

---

## Brain 可选依赖注入表

`AppContainer._configure_brain_dependencies()` 注入到 `brain.<attr>`，未启用时为 `None`。调用方用 `getattr(brain, "...", None)`：

| 属性 | Feature Flag | 说明 |
|------|-------------|------|
| `vector_store` | 始终（chromadb 必装） | `MemoryVectorStore` 单例 |
| `note_store` | 始终 | `NoteStore`（v2 笔记） |
| `episodic_store` / `semantic_store` | 始终 | 情景/语义记忆 |
| `embedding_worker` | 始终 | 异步 embedding |
| `skill_manager` | `SKILLS_ENABLED` | 技能存储 + 执行 |
| `browser_manager` | `BROWSER_ENABLED` | Playwright 浏览器 |
| `focus_manager` | `FOCUS_ENABLED` | 焦点连续性 |
| `intent_router` | `INTENT_ROUTER_ENABLED` | 意图分类 |
| `correction_manager` | 始终 | 纠错 |
| `identity_store` | （identity-substrate flag） | 身份基底 |
| `commitments_store` | 始终 | 承诺 |

---

## LLM 路由系统

按 purpose slot 路由到不同模型：

```
┌─────────────┬───────────────────────────────────────┬──────────────────────┐
│ Slot        │ Purpose 枚举值                         │ 环境变量              │
├─────────────┼───────────────────────────────────────┼──────────────────────┤
│ chat        │ main_conversation, persona_expression │ LLM_CHAT_BASE_URL    │
│             │ self_reflection                       │ LLM_CHAT_MODEL       │
├─────────────┼───────────────────────────────────────┼──────────────────────┤
│ tool        │ lightweight_judgment, memory_processing│ LLM_TOOL_BASE_URL   │
│             │ agent_execution                       │ LLM_TOOL_MODEL       │
├─────────────┼───────────────────────────────────────┼──────────────────────┤
│ heartbeat   │ inner_tick_proactive                  │ NIM_BASE_URL         │
│             │                                       │ NIM_MODEL            │
├─────────────┼───────────────────────────────────────┼──────────────────────┤
│ fallback    │ 未匹配时                               │ LLM_BASE_URL         │
│             │                                       │ LLM_MODEL            │
└─────────────┴───────────────────────────────────────┴──────────────────────┘
```

自动检测：base_url 含 `/anthropic` → `AsyncAnthropic`，否则 → `AsyncOpenAI`。

运行时模型切换通过 `ModelConfigManager` 持久化到 `data/config/model_routing.json`。

---

## 桌面前端（desktop-v2/，Tauri v2 + React 19，~5,100 行，69 个文件）

```
desktop-v2/src/
├── main.tsx, App.tsx, router.tsx       # 入口与路由
│
├── pages/                              # 页面路由
│   ├── ChatPage.tsx                    # 主对话 UI（含 TaskSidebar）
│   ├── DashboardPage.tsx               # 仪表盘
│   ├── MemoryPage.tsx                  # 记忆浏览器
│   ├── ModelRoutingPage.tsx            # 模型路由 UI
│   ├── PersonaPage.tsx                 # 人格编辑
│   ├── SettingsPage.tsx                # 配置
│   └── …
│
├── components/
│   ├── chat/                           # MessageBubble, MessageList, AgentActivityCard, AgentPanel
│   ├── tasks/                          # TaskSidebar, ToolCallDetail
│   ├── layout/                         # AppShell, Sidebar, StatusBar
│   ├── memory/ status/ model-routing/ settings/
│   └── ui/                             # shadcn/ui 基础组件
│
├── hooks/                              # useSSE, useWebSocket
├── stores/                             # Zustand
├── lib/                                # api 客户端、工具
└── types/                              # TypeScript 类型定义
```

**与后端通信**：SSE（`/events` 端点）+ WebSocket（`/ws/chat`）+ REST API。

---

## 配置系统

`config/settings.py` 加载顺序（高 → 低）：**`.env` 环境变量 > `config.toml` > 代码默认值**。

### Feature Flags（`*_ENABLED` 模式）

```
SKILLS_ENABLED                — 技能系统
SHELL_ENABLED                 — Shell 执行
BROWSER_ENABLED               — 浏览器子系统
QQ_ENABLED                    — QQ 通道
LOOP_DETECTION_ENABLED        — 工具循环检测（observation 模式）
FOCUS_ENABLED                 — 焦点连续性
INTENT_ROUTER_ENABLED         — 意图路由
CHAT_WEB_TOOLS_ENABLED        — 聊天中的 web 工具
CORRECTION_ENABLED            — 纠错系统
SEMANTIC_DISTILL_ENABLED      — 每日语义蒸馏
```

具体参数和默认值见 `config/settings.py` 与 `config.toml`。

---

## 数据目录

```
data/
├── identity/
│   ├── soul.md                 # 核心人格（VitalGuard 保护）
│   ├── constitution.md         # 进化宪法（ConstitutionGuard 保护）
│   ├── kevin_interests.md      # 兴趣
│   └── role_card.md            # 角色设定参考
├── lapwing.db                  # SQLite 主库（trajectory / reminders_v2 / commitments / focuses）
├── mutation_log.db             # SQLite 事件日志（llm.request / tool.called / tool.result）
├── chroma/                     # ChromaDB 向量索引
├── notes/                      # NoteStore 笔记 Markdown
├── credentials/vault.enc       # 加密凭据
├── browser/profile/            # Playwright 持久化上下文
├── browser/screenshots/        # 页面截图（按天清理）
├── config/model_routing.json   # 运行时模型路由
├── tasks/                      # 任务检查点
├── lapwing.pid                 # 进程锁
└── vitals.json                 # 启停状态（重启感知）
```

---

## 测试结构

测试目录镜像 `src/`：

```
tests/
├── core/         brain / task_runtime / llm_router / authority / shell_policy / focus_manager 等
├── tools/        registry / shell / file / memory / browser 等
├── memory/       note / vector / episodic / semantic 等
├── identity/     identity-substrate 套件
├── agents/       researcher / coder
├── adapters/     qq / desktop
├── api/          server / 路由
├── auth/         oauth / storage
├── guards/       memory_guard
├── baseline_v2/  REPORT.md（M2.7 模型能力基线）
└── 根级别         import_smoke / main_commands
```

**测试模式**：mock `LLMRouter`、`TrajectoryStore`、adapters、工具结果。`pytest-asyncio` `asyncio_mode = auto`，无 CI。

---

## Prompt 模板（`prompts/`）

`prompts/` 含 14 个运行时加载的 Markdown：

```
lapwing_soul.md                  # 核心人格（注入 system prompt）
lapwing_voice.md                 # 行为约束（✕/✓ 对比，depth-0 注入）
lapwing_voice_details.md         # 行为细则（孤儿，未接入加载链路）
agent_coder.md / agent_researcher.md   # 子智能体 system prompt（model_config slot）
browser_vision_describe.md       # 视觉理解
episodic_extract.md              # 情景记忆抽取
focus_continuity.md / focus_match_dormant.md / focus_summarize.md   # 焦点 prompts
group_engage_decision.md         # QQ 群参与决策
life_today_tone.md               # 今日心情摘要
semantic_distill.md              # 语义蒸馏
sop/体育赛程查询.md              # SOP（孤儿，未接入加载链路）
README.md                        # prompts 元索引
```

身份 Markdown 在 `data/identity/`，不在 `prompts/`。

---

## 扩展模式

### 新增工具

```python
# 1. 实现处理器 (src/tools/<name>.py 或 handlers.py)
async def my_tool(req: ToolExecutionRequest, ctx: ToolExecutionContext) -> ToolExecutionResult:
    return ToolExecutionResult(success=True, payload={"result": "..."})

# 2. 注册到 src/tools/registry.py 的 build_default_tool_registry()
registry.register(ToolSpec(
    name="my_tool", description="...", json_schema={...},
    executor=my_tool, capability="my_cap", risk_level="low",
))

# 3. （可选）src/core/authority_gate.py 添加权限条目
```

### 新增 inner tick action

`InnerTickScheduler`（`src/core/inner_tick_scheduler.py`）替代了已删除的 HeartbeatEngine。新增内驱行为通常应作为工具 + 模型决策，而非硬编码 action。

### 新增消息通道

```python
# 1. 继承 BaseAdapter (src/adapters/base.py)，实现 start/stop/send_message/is_connected
# 2. main.py 中 container.channel_manager.register(ChannelType.XXX, adapter)
# 3. 消息进入: brain.think_conversational(chat_id, text, send_fn, adapter="xxx", user_id="...")
```

---

## 开发环境

### 前置要求

- Python 3.12+
- 推荐使用 venv 隔离依赖

### 创建 venv

```bash
python3.12 -m venv venv
source venv/bin/activate   # macOS / Linux
```

### 安装依赖

```bash
pip install -r requirements.txt
```

### 运行测试

**快速安全套件（~1s，无外部依赖）：**

```bash
PYTHONPATH=. python -m pytest \
  tests/core/test_runtime_profiles_exclusion.py \
  tests/core/test_brain_zero_tools_path.py \
  tests/core/test_tool_dispatcher.py \
  tests/core/test_tool_boundary.py \
  tests/core/test_intent_router.py \
  tests/agents/test_agent_tool_dispatcher.py \
  tests/agents/test_dynamic_agent.py \
  tests/agents/test_registry.py \
  tests/agents/test_factory.py \
  tests/tools/test_agent_tools_v2.py \
  -v
```

**排除需要外部依赖的测试（适合本地快速验证）：**

```bash
PYTHONPATH=. python -m pytest tests/ -x -q \
  -m "not integration and not e2e and not requires_llm and not requires_browser and not requires_network"
```

**完整测试（需要 LLM key、ChromaDB、Playwright）：**

```bash
PYTHONPATH=. python -m pytest tests/ -x -q
```

### CI 当前覆盖范围

GitHub Actions (`.github/workflows/tests.yml`) 在 push/PR to `master` 时自动运行快速安全套件。

| 类别 | pytest marker | CI 状态 | 说明 |
|------|-------------|---------|------|
| 核心工具派发 + 意图路由 + agent | （无 marker） | ✅ 运行 | mock 驱动，无外部依赖 |
| 集成测试 (ChromaDB / 多子系统) | `integration` | ❌ 不跑 | 需要 ChromaDB + SQLite |
| 端到端 (全链路) | `e2e` | ❌ 不跑 | 需要多子系统 |
| 需要 LLM API key | `requires_llm` | ❌ 不跑 | 需要真实 LLM key |
| 需要浏览器 (Playwright) | `requires_browser` | ❌ 不跑 | 需要浏览器进程 |
| 需要外部网络 | `requires_network` | ❌ 不跑 | 需要外部 HTTP 连接 |

---

## 开发约定

- **语言**：代码注释中文；commits、PR、维护者文档（CLAUDE.md / AGENTS.md / CODEX.md）英文。Conventional Commits（`feat(scope): …`、`fix(...): …`）。
- **导入**：绝对导入 `from src.core.brain import LapwingBrain`。
- **配置**：所有配置通过 `config/.env` + `config.toml` + `config/settings.py`。
- **日志**：`logging.getLogger("lapwing.<module>")`，`lapwing` logger 不向 root 传播。
- **类型**：核心类型放独立模块（`task_types.py`、`llm_types.py`、`shell_types.py`、`tools/types.py`）。
- **测试**：`PYTHONPATH=. python -m pytest tests/ -x -q`（`src/` 不是已安装包），`asyncio_mode = auto`。
- **Prompt**：`prompts/` 目录 Markdown 热加载，改 prompt 不需改代码。
- **部署**：`bash scripts/deploy.sh` → systemd `lapwing.service`，不要直接 `nohup python main.py &`。
