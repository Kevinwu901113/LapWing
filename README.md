# Lapwing — Architecture Reference

> 本文档面向 AI 架构分析。目标：仅阅读此文件即可理解代码结构、模块职责、依赖关系和数据流，以便进行架构设计或重构建议。

---

## 项目概述

Lapwing 是一个 24/7 运行的自主 AI 伴侣系统——具有人格、记忆、自我进化能力的虚拟女友，不是 bot 框架。

| 维度 | 详情 |
|------|------|
| **后端** | Python 3.12+，~25,500 行代码，121 个源文件 |
| **前端** | Tauri v2 + React 19 + TypeScript，~3,400 行，53 个文件 |
| **测试** | pytest + pytest-asyncio，~15,800 行，85 个测试文件 |
| **LLM** | MiniMax M2.7（Anthropic 兼容 API）、GLM（OpenAI 兼容 API） |
| **消息通道** | QQ（NapCat WebSocket）、Desktop（本地 WebSocket） |
| **存储** | SQLite（WAL）、ChromaDB 向量库、Markdown 文件 |
| **部署** | PVE 服务器（Xeon E-2174G, 32GB），无 CI/CD |

---

## 全局架构

```
main.py  ──→  AppContainer.prepare() / start()
                 │
                 ├── LapwingBrain          ← 所有请求的唯一门面
                 │    ├── LLMRouter        ← 多 slot 模型路由
                 │    ├── TaskRuntime      ← 工具循环执行层
                 │    ├── StateViewBuilder ← prompt 组装（soul/voice/memory/rules）
                 │    ├── ConversationMemory ← 对话存储
                 │    └── [可选依赖]        ← 由 feature flag 控制
                 │
                 ├── MainLoop             ← 单消费者事件循环
                 ├── InnerTickScheduler   ← 自主思考调度
                 ├── MaintenanceTimer     ← 每日维护（语义蒸馏）
                 ├── DurableScheduler     ← 持久化提醒
                 ├── ChannelManager        ← 多通道路由
                 │    ├── QQAdapter
                 │    └── DesktopAdapter
                 └── LocalApiServer        ← FastAPI + WebSocket，供桌面端消费
```

### 核心设计原则

1. **无 agent dispatch 层**：所有能力注册为 `ToolSpec`，LLM 通过 tool_calls 自行决定调用。
2. **人格与行为分离**：`soul.md` 定义"她是谁"，`voice.md` 用 ✕/✓ 对比约束行为边界。
3. **文件即数据库**：身份、记忆、进化规则均存为 Markdown，可直接编辑、可 Git 追踪。
4. **Diff-based 进化**：人格变化以 diff 累积，ConstitutionGuard 保证不违反宪法。
5. **可选依赖注入**：Brain 的所有子系统都是可选的（默认 `None`），由 feature flag 开关。

---

## 消息完整流转

```
用户消息 (Telegram / QQ / Desktop)
  │
  ▼
Brain.think_conversational(chat_id, text, send_fn, adapter, user_id)
  ├── AuthorityGate.identify(adapter, user_id)  # IGNORE/GUEST/TRUSTED/OWNER
  ├── ConversationMemory.append()              # 写入内存缓存 + TrajectoryStore 镜像
  ├── StateViewBuilder.build(chat_id)          # 读取 soul/voice/rules/trajectory/memory/commitments
  ├── StateSerializer.serialize(state_view)    # 纯函数 → prompt 字节
  │
  ▼
Brain._complete_chat()
  └── TaskRuntime.complete_chat()              # 工具循环（最多 N 轮）
        │
        ├── LLMRouter.tool_turn(messages, tools, purpose)
        │     → 返回 ToolTurnResult { text, tool_calls, continuation_message }
        │
        ├── [for each tool_call]
        │     ├── LoopDetectionState.check()   # 循环检测（可选）
        │     ├── VitalGuard.check()           # 核心文件保护
        │     ├── AuthorityGate.authorize()    # 权限校验
        │     └── ToolRegistry.execute()       # 实际执行
        │
        └── 返回最终文本
  │
  ▼
写回 ConversationMemory → 通过 send_fn 回传用户
```

---

## 源码结构（src/，121 文件，~25,500 行）

### src/core/（39 文件，~10,700 行）— 核心业务逻辑

**请求处理链：**

| 文件 | 行数 | 职责 |
|------|------|------|
| `brain.py` | 844 | **门面类**。`think()` / `think_conversational()` 是所有请求的唯一入口。不直接调用 LLM，只准备上下文后委托给 TaskRuntime |
| `task_runtime.py` | 1367 | **工具循环执行层**。`complete_chat()` 是核心循环：调 LLM → 执行工具 → 追加结果 → 重复。内含循环检测和断路器 |
| `llm_router.py` | 1141 | **模型路由**。按 purpose slot（chat/tool/heartbeat）路由到不同模型/API。自动检测 Anthropic vs OpenAI 兼容端点。支持 per-session 模型覆盖 |
| `llm_protocols.py` | 373 | **协议适配**。Anthropic SDK 调用封装，tool_calls 解析，prefix caching 支持 |
| `llm_types.py` | 29 | **LLM 类型定义**。`ToolCallRequest`、`ToolTurnResult` |
| `state_view_builder.py` | - | **Prompt 组装**。StateView 构建 + depth-0 voice reminder 注入 |
| `prompt_loader.py` | 45 | **Prompt 热加载**。从 `prompts/` 目录加载 Markdown |
| `task_types.py` | 88 | **任务类型**。`RuntimeDeps`、`LoopDetectionConfig/State`、`TaskLoopStep/Result` |
| `runtime_profiles.py` | 66 | **工具剖面**。按执行上下文过滤可用工具集（chat_shell / coder_snippet / coder_workspace / file_ops） |

**v2.0 核心调度：**

| 文件 | 行数 | 职责 |
|------|------|------|
| `main_loop.py` | - | 单消费者事件循环，按优先级消费 EventQueue |
| `event_queue.py` | - | 优先级队列 (OWNER > TRUSTED > SYSTEM > INNER) |
| `inner_tick_scheduler.py` | - | 自主思考调度（自适应退避 + 紧急事件推送） |
| `durable_scheduler.py` | - | 持久化提醒 (reminders_v2 表) |
| `maintenance_timer.py` | - | 每日 3AM 语义蒸馏 |

**安全与权限：**

| 文件 | 行数 | 职责 |
|------|------|------|
| `authority_gate.py` | 120 | 三级权限：OWNER(2) / TRUSTED(1) / GUEST(0)。按工具过滤 |
| `vital_guard.py` | 419 | 核心文件保护。Shell/文件写入前检查目标路径，判定 PASS / VERIFY_FIRST / BLOCK |
| `shell_policy.py` | 666 | Shell 执行策略 + ACL 白名单 |
| `verifier.py` | 362 | Shell 约束验证 |
| `credential_vault.py` | 144 | 加密凭据存储 |

**浏览器：**

| 文件 | 行数 | 职责 |
|------|------|------|
| `browser_manager.py` | 1179 | Playwright 持久化上下文。Tab 管理、DOM 提取、截图、视觉理解管线 |

**其他核心：**

| 文件 | 行数 | 职责 |
|------|------|------|
| `channel_manager.py` | 145 | 多通道路由。主动消息优先级：Desktop > last_active > 任意连接 |
| `vitals.py` | 235 | 生命体征：启动时间、uptime、重启感知、系统快照（CPU/内存/磁盘） |
| `model_config.py` | 471 | 运行时模型切换。持久化到 `data/config/model_routing.json` |
| `model_config.py` | - | 运行时模型切换 |
| `reasoning_tags.py` | 113 | 内部思考标签处理（`<think>` 标签剥离/保留） |
| `codex_oauth_client.py` | 379 | Codex OAuth 客户端 |

---

### src/tools/（23 文件，~4,700 行）— 工具系统

**核心框架：**

| 文件 | 行数 | 职责 |
|------|------|------|
| `types.py` | 73 | **类型定义**。`ToolSpec`、`ToolExecutionRequest`、`ToolExecutionContext`、`ToolExecutionResult` |
| `registry.py` | 824 | **工具注册表**。`build_default_tool_registry()` 注册所有工具，`chat_tools()` 按 RuntimeProfile 过滤 |
| `shell_executor.py` | 359 | Shell 子进程管理。`ShellResult` 封装 |

**工具处理器（每个文件 = 一个或多个工具实现）：**

| 文件 | 行数 | 工具 | capability |
|------|------|------|-----------|
| `handlers.py` | 467 | `web_search`、`web_fetch`、`execute_shell`、`run_python_code`、`verify_code_result`、`apply_workspace_patch` 等 | web, shell, code |
| `browser_tools.py` | 808 | `browser_navigate`、`browser_click`、`browser_type`、`browser_screenshot` 等 | browser |
| `file_editor.py` | 718 | `file_read_segment`、`file_write`、`file_append`、`file_list_directory` | file |
| `memory_crud.py` | 242 | `memory_create`、`memory_read`、`memory_update`、`memory_delete` | memory |
| `memory_note.py` | 46 | `memory_note`（快速记忆写入） | memory |
| `web_search.py` | 264 | Web 搜索（Tavily / DuckDuckGo 双引擎） | web |
| `web_fetcher.py` | 141 | 网页内容抓取 | web |
| `schedule_task.py` | 270 | `schedule_task`（定时任务） | schedule |
| `code_runner.py` | 70 | Python 代码执行 | code |
| `delegation_tool.py` | 94 | 任务委派 | general |
| `self_status.py` | 80 | 自我状态查询 | general |
| `skill_tools.py` | 148 | 技能列表/查看 | skill |
| `session_search.py` | 101 | 会话感知记忆搜索 | memory |
| `image_search.py` | 74 | 图片搜索 | web |
| `send_image.py` | 64 | 发送图片 | general |
| `weather.py` | 64 | 天气查询 | web |
| `transcriber.py` | 58 | 语音转文字 | general |
| `trace_mark.py` | 85 | 执行追踪标记 | general |

---

### src/memory/（13 文件，~2,300 行）— 记忆系统

四层记忆架构：

```
┌─────────────────────────────────────────────────────┐
│ Layer 4: VectorStore (ChromaDB)                     │ ← 语义检索
│   vector_store.py (148 行)                          │
├─────────────────────────────────────────────────────┤
│ Layer 3: MemoryIndex (JSON)                         │ ← 分类 + 时间衰减评分
│   memory_index.py (257 行)                          │
├─────────────────────────────────────────────────────┤
│ Layer 2: FileMemory (Markdown)                      │ ← 可直接编辑的用户/自我认知
│   file_memory.py (55 行)                            │
├─────────────────────────────────────────────────────┤
│ Layer 1: ConversationMemory (SQLite)                │ ← 对话历史 + 用户事实
│   conversation.py (653 行) + user_facts.py (81 行)  │
└─────────────────────────────────────────────────────┘
```

| 文件 | 行数 | 职责 |
|------|------|------|
| `conversation.py` | 653 | SQLite + 内存缓存。`chat_id` 分组，WAL 模式，session 感知 |
| `memory_index.py` | 257 | JSON 索引，分类存储，时间衰减评分 |
| `vector_store.py` | 148 | ChromaDB 封装，相似度检索 |
| `file_memory.py` | 55 | 读取 KEVIN.md / SELF.md |
| `user_facts.py` | 81 | SQLite user_facts 表的结构化事实 |
| `fact_extractor.py` | 231 | LLM 驱动的事实自动提取 |
| `auto_extractor.py` | 214 | 自动记忆提取管线 |
| `compactor.py` | 138 | LLM 驱动的历史压缩，输出到 `conversations/summaries/` |
| `interest_tracker.py` | 172 | 兴趣图谱追踪 |
| `reminders.py` | 326 | 提醒管理（SQLite） |
| `todos.py` | 84 | 待办追踪 |
| `discoveries.py` | 132 | 浏览器发现的知识存储 |

---

### src/heartbeat/（14 文件，~1,500 行）— 自主行为系统

三种节拍，12 个 Action：

| Action 文件 | selection_mode | 节拍 | 职责 |
|------------|----------------|------|------|
| `proactive.py` | decide | fast | 主动消息生成 |
| `interest_proactive.py` | decide | fast | 兴趣驱动分享 |
| `auto_memory.py` | decide | fast | 自动记忆提取 |
| `autonomous_browsing.py` | decide | fast | 自主浏览 |
| `consolidation.py` | always | slow | 记忆整理 |
| `self_reflection.py` | decide | slow | 自省 |
| `prompt_evolution.py` | decide | slow | 人格进化 |
| `memory_maintenance.py` | always | slow | 索引优化 |
| `compaction_check.py` | always | slow | 压缩触发 |
| `system_health.py` | always | minute | 系统健康检查 |
| `session_reaper.py` | always | minute | 过期会话清理 |
| `task_notification.py` | always | minute | 任务通知 |

`SenseContext` 是心跳的环境快照：`beat_type`、当前时间、上次交互时间、沉默小时数、用户画像、对话摘要、兴趣。

---

### src/app/（5 文件，~1,900 行）— 应用容器与启动

| 文件 | 行数 | 职责 |
|------|------|------|
| `container.py` | 340 | **DI 根**。`prepare()` → 初始化 DB、装配 Brain 依赖；`start()` → 启动心跳/通道/API；`shutdown()` → 逆序清理 |
| `telegram_app.py` | 690 | Telegram Bot API 适配。消息处理、命令路由 |
| `telegram_delivery.py` | 599 | Telegram 消息投递。分段发送、Markdown 渲染、媒体支持 |
| `task_view.py` | 221 | TaskViewStore。工具执行遥测 |

---

### src/adapters/（6 文件，~850 行）— 消息通道适配器

| 文件 | 行数 | 职责 |
|------|------|------|
| `base.py` | 43 | `BaseAdapter` 抽象接口 + `ChannelType` 枚举（TELEGRAM / QQ / DESKTOP） |
| `desktop_adapter.py` | 83 | SSE 推送的桌面端适配器 |
| `qq_adapter.py` | 530 | OneBot v11 WebSocket 适配（QQ） |
| `qq_group_context.py` | 55 | QQ 群上下文管理 |
| `qq_group_filter.py` | 133 | QQ 群消息过滤（关键词/@ 触发） |

---

### src/api/（14 文件，~2,200 行）— Desktop API 服务

| 文件 | 行数 | 职责 |
|------|------|------|
| `server.py` | 215 | FastAPI 启动 + SSE 端点 + 路由挂载 |
| `event_bus.py` | 62 | 事件发布（桌面端 SSE 推送） |
| `model_routing.py` | 110 | 运行时模型选择 API |
| `routes/auth.py` | - | 认证路由（桌面端 token 认证） |
| `routes/chat_ws.py` | - | WebSocket 对话路由 |
| `routes/browser.py` | - | 浏览器控制路由 |
| `routes/agents.py` | - | Agent 管理路由（v2） |
| `routes/identity.py` | - | 身份管理路由（v2） |
| `routes/events_v2.py` | - | 事件查询路由（v2） |
| `routes/life_v2.py` | - | 人生图景路由（v2） |
| `routes/models_v2.py` | - | 模型管理路由（v2） |
| `routes/notes_v2.py` | - | 笔记管理路由（v2） |
| `routes/permissions_v2.py` | - | 权限管理路由（v2） |
| `routes/status_v2.py` | - | 状态查询路由（v2） |
| `routes/system_v2.py` | - | 系统信息路由（v2） |
| `routes/tasks_v2.py` | - | 任务管理路由（v2） |

---

### src/auth/（5 文件，~1,670 行）— 认证系统

| 文件 | 行数 | 职责 |
|------|------|------|
| `service.py` | 1023 | AuthManager。多策略认证（API key、OAuth、桌面 token）、用户身份解析 |
| `storage.py` | 314 | 认证配置文件持久化 |
| `openai_codex.py` | 222 | OpenAI Codex OAuth 流程 |
| `resolver.py` | 38 | 用户 ID 解析 |
| `models.py` | 64 | 认证数据模型 |

---

### src/guards/（1 文件）— 安全守卫

| 文件 | 行数 | 职责 |
|------|------|------|
| `memory_guard.py` | 128 | 记忆写入扫描（prompt injection、凭据泄露检测） |

---

### src/models/（2 文件）— 共享数据模型

| 文件 | 行数 | 职责 |
|------|------|------|
| `message.py` | 88 | `RichMessage`：跨通道富媒体消息（text + images，支持 URL/Base64/路径） |

---

## 关键数据类型

```python
# --- 工具系统 (src/tools/types.py) ---

@dataclass(frozen=True)
class ToolSpec:                        # 工具描述符（不可变）
    name: str
    description: str                   # 展示给 LLM
    json_schema: dict                  # OpenAI function calling 格式
    executor: ToolExecutor             # async (req, ctx) -> result
    capability: str                    # 主标签 (shell/web/file/memory/schedule/skill/code/verify/general/browser)
    capabilities: tuple[str, ...]      # 附加标签
    visibility: "model" | "internal"   # internal 不展示给 LLM
    risk_level: "low" | "medium" | "high"

@dataclass(frozen=True)
class ToolExecutionContext:            # 工具执行环境
    execute_shell: Callable
    shell_default_cwd: str
    workspace_root: str
    services: dict[str, Any]           # 注入 skill_manager、reminder_scheduler 等
    adapter: str                       # "telegram" / "qq" / "desktop"
    user_id: str
    auth_level: int                    # 0=GUEST, 1=TRUSTED, 2=OWNER
    chat_id: str
    memory: ConversationMemory | None
    memory_index: MemoryIndex | None

@dataclass(frozen=True)
class ToolExecutionRequest:            # 工具执行请求
    name: str
    arguments: dict[str, Any]

@dataclass
class ToolExecutionResult:             # 工具执行结果
    success: bool
    payload: dict[str, Any]
    reason: str
    shell_result: ShellResult | None

# --- LLM 类型 (src/core/llm_types.py) ---

@dataclass
class ToolCallRequest:                 # LLM 返回的工具调用请求
    id: str
    name: str
    arguments: dict[str, Any]

@dataclass
class ToolTurnResult:                  # 一轮 LLM 响应
    text: str
    tool_calls: list[ToolCallRequest]
    continuation_message: dict | None

# --- 任务类型 (src/core/task_types.py) ---

@dataclass(frozen=True)
class RuntimeDeps:                     # 工具循环的底层依赖
    execute_shell: Callable
    policy: ShellRuntimePolicy
    shell_default_cwd: str
    shell_allow_sudo: bool

@dataclass(frozen=True)
class RuntimeProfile:                  # 工具剖面 (src/core/runtime_profiles.py)
    name: str                          # chat_shell / coder_snippet / coder_workspace / file_ops
    capabilities: frozenset[str]       # 允许的 capability 集合
    tool_names: frozenset[str]         # 额外允许的工具名
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
                          │AppContainer │ ← DI 根（src/app/container.py）
                          └──────┬──────┘
                    ┌────────────┼────────────────────────┐
                    │            │                        │
             ┌──────▼──────┐  ┌─▼──────────┐  ┌─────────▼─────────┐
             │ LapwingBrain│  │HeartbeatEng │  │ ChannelManager    │
             │ (门面)       │  │ (定时触发)   │  │ ├─TelegramApp     │
             └──────┬──────┘  └─────┬──────┘  │ ├─QQAdapter       │
                    │               │          │ └─DesktopAdapter  │
         ┌──────────┼───────┐       │          └───────────────────┘
         │          │       │       │
  ┌──────▼───┐ ┌───▼────┐ ┌▼──────────────┐
  │PromptBld │ │TaskRtm │ │ConversationMem│
  │ (8 层)    │ │(工具循环)│ │ (SQLite)      │
  └──────────┘ └───┬────┘ └───────────────┘
                   │
            ┌──────┼──────┐
            │      │      │
      ┌─────▼──┐ ┌▼────┐ ┌▼──────────┐
      │LLMRoutr│ │Tools│ │ Guards    │
      │(多 slot)│ │Regis│ │├VitalGuard│
      └────────┘ │(824L)│ │├AuthGate  │
                 └─────┘ │└BrowserGd │
                         └───────────┘
```

**依赖方向规则：**
- `core/` 模块之间可互相引用（Brain → TaskRuntime → LLMRouter → ToolRegistry）
- `tools/` 依赖 `core/`（通过 `ToolExecutionContext.services` 注入）
- `memory/` 被 `core/` 和 `tools/` 引用
- `guards/` 被 `core/task_runtime.py` 在工具执行前调用
- `adapters/` 和 `api/` 依赖 `core/brain.py` 和 `models/`
- `heartbeat/actions/` 依赖 `core/brain.py`（每个 action 接收 brain 实例）
- 所有模块依赖 `config/settings.py`

---

## Brain 可选依赖注入表

`AppContainer._configure_brain_dependencies()` 注入到 `brain.xxx`：

| 属性 | 类型 | Feature Flag | 职责 |
|------|------|-------------|------|
| `knowledge_manager` | `KnowledgeManager` | 始终 | 外部知识文件 |
| `vector_store` | `VectorStore` | 始终 | 语义检索 |
| `skill_manager` | `SkillManager` | `SKILLS_ENABLED` | 技能系统 |
| `interest_tracker` | `InterestTracker` | 始终 | 兴趣图谱 |
| `self_reflection` | `SelfReflection` | 始终 | 自省 |
| `constitution_guard` | `ConstitutionGuard` | 始终 | 宪法保护 |
| `tactical_rules` | `TacticalRules` | 始终 | 纠错规则 |
| `evolution_engine` | `EvolutionEngine` | 始终 | 人格进化 |
| `experience_skill_manager` | `ExperienceSkillManager` | `EXPERIENCE_SKILLS_ENABLED` | 经验检索 |
| `session_manager` | `SessionManager` | `SESSION_ENABLED` | 会话分段 |
| `memory_index` | `MemoryIndex` | 始终 | 记忆索引 |
| `auto_memory_extractor` | `AutoMemoryExtractor` | `AUTO_MEMORY_EXTRACT_ENABLED` | 自动记忆提取 |
| `task_flow_manager` | `TaskFlowManager` | 始终 | 任务流 |
| `browser_manager` | `BrowserManager` | `BROWSER_ENABLED` | 浏览器控制 |

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
│ heartbeat   │ heartbeat_proactive                   │ NIM_BASE_URL         │
│             │                                       │ NIM_MODEL            │
├─────────────┼───────────────────────────────────────┼──────────────────────┤
│ fallback    │ 未匹配时                               │ LLM_BASE_URL         │
│             │                                       │ LLM_MODEL            │
└─────────────┴───────────────────────────────────────┴──────────────────────┘
```

自动检测：base_url 含 `/anthropic` → `AsyncAnthropic`，否则 → `AsyncOpenAI`。

运行时模型切换通过 `ModelConfigManager` 持久化到 `data/config/model_routing.json`。

---

## 桌面前端（desktop-v2/，Tauri v2 + React 19，~3,400 行）

```
desktop-v2/src/
├── main.tsx, App.tsx, router.tsx       # 入口与路由
│
├── pages/                              # 8 个页面
│   ├── ChatPage.tsx                    # 主对话 UI
│   ├── DashboardPage.tsx               # 仪表盘（指标、资源环、通道状态）
│   ├── MemoryPage.tsx                  # 记忆浏览器
│   ├── TaskCenterPage.tsx              # 任务执行视图
│   ├── ModelRoutingPage.tsx            # 模型选择 UI
│   ├── PersonaPage.tsx                 # 人格编辑（CodeMirror Markdown 编辑器）
│   ├── SensingPage.tsx                 # 环境感知展示
│   └── SettingsPage.tsx                # 配置页
│
├── components/
│   ├── chat/                           # MessageBubble, MessageInput, MessageList, ToolCallIndicator
│   ├── dashboard/                      # MetricCard, HeartbeatCard, ReminderList, ResourceRing, ChannelStatus
│   ├── layout/                         # AppShell, Sidebar, StatusBar
│   ├── tasks/                          # TaskFlowCard, ToolCallDetail
│   └── ui/                             # 17 个 shadcn/ui 基础组件
│
├── hooks/
│   ├── useSSE.ts                       # Server-Sent Events 连接
│   └── useWebSocket.ts                 # WebSocket 连接
│
├── stores/                             # Zustand 状态管理
│   ├── chat.ts                         # 消息历史、会话状态
│   └── server.ts                       # 服务器配置、连接状态
│
├── lib/
│   ├── api.ts                          # HTTP API 客户端
│   └── utils.ts                        # 工具函数（cn() 等）
│
└── types/                              # TypeScript 类型定义
    ├── api.ts                          # API 响应类型
    ├── chat.ts                         # 消息与对话类型
    ├── sensing.ts                      # 感知数据类型
    └── tasks.ts                        # 任务与执行类型
```

**与后端通信**：SSE（`/events` 端点，实时推送）+ WebSocket（`/ws/chat`，对话）+ REST API。

---

## 配置系统

所有配置通过 `config/settings.py` 的 `os.getenv()` 加载，源文件 `config/.env`。

### Feature Flags（`FEATURE_ENABLED` 模式）

```
SKILLS_ENABLED                — 技能系统
MEMORY_CRUD_ENABLED           — 记忆 CRUD 工具
AUTO_MEMORY_EXTRACT_ENABLED   — 自动记忆提取
SELF_SCHEDULE_ENABLED         — 自调度工具
SESSION_ENABLED               — 会话分段
EXPERIENCE_SKILLS_ENABLED     — 经验技能系统
QQ_ENABLED                    — QQ 通道
BROWSER_ENABLED               — 浏览器子系统（25+ BROWSER_* 子配置）
BROWSE_ENABLED                — 自主浏览（心跳动作）
LOOP_DETECTION_ENABLED        — 工具循环检测
SHELL_ENABLED                 — Shell 执行
DELEGATION_ENABLED            — 任务委派
MESSAGE_SPLIT_ENABLED         — 消息分段
CHAT_WEB_TOOLS_ENABLED        — 聊天中的 web 工具
```

---

## 数据目录

```
data/
├── identity/
│   ├── soul.md                 # 核心人格（VitalGuard 保护，Lapwing 不可自改）
│   └── constitution.md         # 进化宪法（ConstitutionGuard 保护）
├── memory/
│   ├── KEVIN.md                # 对用户的了解（FileMemory）
│   ├── SELF.md                 # 自我认知（FileMemory）
│   ├── _index.json             # MemoryIndex（分类 + 时间衰减）
│   ├── conversations/summaries/# LLM 压缩摘要（Markdown）
│   ├── sessions/               # SessionManager 的 session 元数据
│   └── journal/                # 结构化记忆条目
├── evolution/
│   ├── rules.md                # 行为规则（从纠错学习积累）
│   ├── interests.md            # 兴趣图谱
│   └── changelog.md            # 人格变化日志（diff 格式）
├── browser/
│   ├── profile/                # Playwright 持久化上下文（Cookie / LocalStorage）
│   └── screenshots/            # 页面截图（按天自动清理）
├── config/
│   └── model_routing.json      # 运行时模型路由配置（ModelConfigManager）
├── credentials/vault.enc       # 加密凭据存储
├── chroma/                     # ChromaDB 向量索引
├── tasks/                      # TaskFlow 检查点
├── knowledge/                  # 外部知识文件
├── backups/                    # soul.md / constitution.md 自动备份
├── lapwing.db                  # SQLite 主库（conversations, user_facts, reminders, sessions）
├── lapwing.pid                 # 进程锁文件
└── vitals.json                 # 启动/关闭状态（重启感知）
```

---

## 测试结构（85 文件，~15,800 行）

测试目录镜像 src/ 结构：

```
tests/
├── core/          (27 文件) — brain、task_runtime、llm_router、evolution、authority、shell_policy 等
├── tools/         (14 文件) — registry、shell、file、web、memory、browser 等
├── memory/        (10 文件) — conversation、fact、interest、compactor 等
├── heartbeat/     (11 文件) — engine、registry、各 action
├── app/           (5 文件)  — container、telegram、task_view
├── auth/          (4 文件)  — oauth、storage、routing
├── adapters/      (2 文件)  — telegram、qq
├── api/           (2 文件)  — server、auth
├── guards/        (2 文件)  — browser_guard、memory_guard
└── 根级别         (2 文件)  — import_smoke、main_commands
```

**测试模式**：mock LLMRouter / ConversationMemory，定义 mock 工具结果，断言状态变更。`pytest-asyncio`（asyncio_mode=auto），无 CI。

---

## Prompt 模板（prompts/，18 个 Markdown 文件）

```
prompts/
├── lapwing_soul.md              # 核心人格定义（注入 system prompt Layer 0）
├── lapwing_voice.md             # 行为约束（✕/✓ 对比，depth-0 注入）
├── lapwing_capabilities.md      # 能力概览
├── lapwing_examples.md          # 使用示例
├── self_reflection.md           # 自省 prompt
├── constitution_check.md        # 宪法检查 prompt
├── compaction.md                # 历史压缩指令
├── evolution_diff.md            # Diff 应用模板
├── heartbeat_*.md (5 个)        # 心跳 action prompt
├── memory_extract.md            # 事实提取模板
├── interest_extract.md          # 兴趣检测 prompt
├── browser_vision_describe.md   # 视觉理解 prompt
├── correction_analysis.md       # 纠错分析 prompt
└── group_engage_decision.md     # QQ 群参与决策 prompt
```

所有 prompt 通过 `prompt_loader.py` 热加载，改 prompt 不需改代码。

---

## 扩展模式

### 新增工具

```python
# 1. 实现处理器 (src/tools/my_tool.py 或 handlers.py)
async def my_tool(req: ToolExecutionRequest, ctx: ToolExecutionContext) -> ToolExecutionResult:
    return ToolExecutionResult(success=True, payload={"result": "..."})

# 2. 注册到 src/tools/registry.py → build_default_tool_registry()
registry.register(ToolSpec(
    name="my_tool", description="...", json_schema={...},
    executor=my_tool, capability="my_cap", risk_level="low",
))

# 3. （可选）src/core/authority_gate.py 添加权限条目
```

### 新增心跳 Action

```python
# 1. 创建 src/heartbeat/actions/my_action.py
class MyAction(HeartbeatAction):
    name = "my_action"
    description = "描述给 LLM"
    beat_types = ["fast"]
    selection_mode = "decide"  # "decide" = LLM 选择，"always" = 无条件

    async def execute(self, ctx: SenseContext, brain, send_fn) -> None: ...

# 2. AppContainer._build_heartbeat() 中注册
```

### 新增消息通道

```python
# 1. 继承 BaseAdapter (src/adapters/base.py)，实现 start/stop/send_message/is_connected
# 2. main.py 中 container.channel_manager.register(ChannelType.XXX, adapter)
# 3. 消息进入: brain.think_conversational(chat_id, text, send_fn, adapter="xxx", user_id="...")
```

---

## 开发约定

- **语言**：代码注释中文，CLAUDE.md 和 commit 英文
- **导入**：绝对导入 `from src.core.brain import ...`
- **配置**：全部通过 `config/.env` + `config/settings.py` 的 `os.getenv()`
- **日志**：`logging.getLogger("lapwing.module_name")`
- **类型提取**：核心类型放独立模块（`task_types.py`、`llm_types.py`、`tools/types.py`）
- **测试**：`pytest` + `pytest-asyncio`（asyncio_mode=auto），无 CI，测试是唯一质量门
- **Prompt**：`prompts/` 目录 Markdown 文件，热加载，改 prompt 不需要改代码
- **部署**：`bash scripts/deploy.sh`，不要直接 `nohup python main.py &`
