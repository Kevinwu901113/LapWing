# Lapwing — 技术文档

> 面向接手者的架构与模块说明。了解"系统如何运转"，而非"它能做什么"。

---

## 目录

1. [整体架构](#整体架构)
2. [启动与生命周期](#启动与生命周期)
3. [核心模块详解](#核心模块详解)
   - [Brain（大脑）](#brainbrainy)
   - [LLMRouter（模型路由）](#llmrouter模型路由)
   - [TaskRuntime（工具执行层）](#taskruntime工具执行层)
   - [PromptBuilder（Prompt 组装）](#promptbuilderprompt-组装)
   - [HeartbeatEngine（心跳引擎）](#heartbeatengine心跳引擎)
   - [Memory（记忆系统）](#memory记忆系统)
   - [Tool System（工具系统）](#tool-system工具系统)
   - [BrowserManager（浏览器子系统）](#browsermanager浏览器子系统)
   - [Vitals（生命体征）](#vitals生命体征)
   - [Channel / Adapter（消息通道）](#channel--adapter消息通道)
   - [Skills（技能系统）](#skills技能系统)
   - [权限与存活保护](#权限与存活保护)
4. [桌面端 (Desktop v2)](#桌面端-desktop-v2)
5. [关键数据结构](#关键数据结构)
6. [配置与环境变量](#配置与环境变量)
7. [数据目录结构](#数据目录结构)
8. [扩展指南](#扩展指南)

---

## 整体架构

```
main.py  ──→  AppContainer.prepare() / start()
                 │
                 ├── LapwingBrain          ← 所有请求的唯一入口
                 ├── HeartbeatEngine       ← 后台定时触发（fast/slow/minute）
                 ├── ReminderScheduler     ← 分钟级 always-run 调度
                 ├── ChannelManager        ← 多通道路由（Telegram/QQ/Desktop）
                 └── LocalApiServer        ← FastAPI + SSE，供桌面端消费
```

**消息的完整流转路径：**

```
用户消息 (任意通道)
  → Brain._prepare_think()
      ├── SessionManager.resolve_session()     # 会话分段
      ├── ConversationMemory.append()          # 写入历史
      ├── TacticalRules.process_correction()  # 异步纠错规则提取
      ├── ConversationCompactor.try_compact()  # 必要时压缩历史
      ├── PromptBuilder.build_system_prompt()  # 分层组装 system prompt
      └── ExperienceSkillManager.retrieve()   # 检索相关经验技能注入
  → Brain._complete_chat()
      └── TaskRuntime.complete_chat()          # 工具循环（最多 N 轮）
            ├── LLMRouter.tool_turn()          # 调用 LLM，获取 text + tool_calls
            ├── [for each tool_call]
            │     ├── VitalGuard.check()       # 核心文件保护
            │     ├── AuthorityGate.authorize()# 权限校验
            │     └── ToolRegistry.execute()  # 实际执行工具
            └── 返回最终文本
  → 写回 ConversationMemory
  → 通过 send_fn 或 on_interim_text 回传给用户
```

**设计原则：**

- **无 agent dispatch 层**。所有能力（搜索、Shell、记忆、调度）均注册为 `ToolSpec`，LLM 自行决定调用哪个工具。
- **人格与行为分离**：`data/identity/soul.md` 定义"她是谁"，`prompts/lapwing_voice.md` 用 ✕/✓ 对比约束行为边界。
- **文件即数据库**：身份、记忆、进化规则均存为 Markdown，可直接编辑、可 Git 追踪。

---

## 启动与生命周期

### `main.py`

薄适配层。职责仅限于：

1. 初始化日志（lapwing logger 与 root logger 分离）
2. PID 文件锁（`data/lapwing.pid`），防止多实例
3. 生成 vital manifest（供 Sentinel 哨兵使用）
4. 构造 `AppContainer` + `TelegramApp`，按需注册 QQ 适配器
5. 启动 `app.run_polling()`

**永远不要直接跑 `nohup python main.py &`。使用 `scripts/deploy.sh`。**

### `AppContainer`（`src/app/container.py`）

依赖注入根。所有核心对象都在这里实例化并连接。

```python
# 生命周期接口
await container.prepare()   # 初始化 DB、装配 Brain 的所有可选依赖
await container.start(send_fn=...)  # 启动心跳、调度器、通道、API Server
await container.shutdown()  # 逆序清理所有资源
```

`prepare()` 内部调用 `_configure_brain_dependencies()`，在这里创建并注入：

| 注入到 `brain.xxx` | 类型 | 说明 |
|---|---|---|
| `knowledge_manager` | `KnowledgeManager` | 外部知识文件读取 |
| `vector_store` | `VectorStore` | Chroma 向量检索 |
| `skill_manager` | `SkillManager` | Skills 注册表 |
| `interest_tracker` | `InterestTracker` | 兴趣图谱 |
| `self_reflection` | `SelfReflection` | 自省能力 |
| `constitution_guard` | `ConstitutionGuard` | 宪法保护 |
| `tactical_rules` | `TacticalRules` | 纠错规则提取 |
| `evolution_engine` | `EvolutionEngine` | 人格进化 |
| `experience_skill_manager` | `ExperienceSkillManager` | 经验技能检索 |
| `session_manager` | `SessionManager` | 会话分段 |
| `memory_index` | `MemoryIndex` | 记忆索引 |
| `auto_memory_extractor` | `AutoMemoryExtractor` | 自动记忆提取 |
| `task_flow_manager` | `TaskFlowManager` | 任务流编排 |
| `quality_checker` | `ReplyQualityChecker` | 回复质量检查 |
| `browser_manager` | `BrowserManager` | Playwright 浏览器子系统 |

所有这些都是可选依赖（`brain.xxx` 默认为 `None`），由 feature flag 控制是否启用。

---

## 核心模块详解

### Brain（`src/core/brain.py`）

`LapwingBrain` 是系统的门面类。外部只需调用两个方法：

```python
# 等待完整回复（heartbeat / headless 场景）
reply: str = await brain.think(chat_id, user_message)

# 流式回复（对话场景，中间结果通过 send_fn 推送）
reply: str = await brain.think_conversational(
    chat_id, user_message, send_fn, typing_fn, adapter="telegram", user_id="..."
)
```

两者共享前置逻辑 `_prepare_think()` → 返回 `_ThinkCtx`（包含组装好的 messages、session_id 等），差异仅在是否开启流式推送。

**Brain 不做任何 LLM 调用**——它只负责准备上下文，实际调用委托给 `TaskRuntime.complete_chat()`。

---

### LLMRouter（`src/core/llm_router.py`）

按 *purpose slot* 路由到不同模型 / API：

| Purpose | 对应 Slot | 环境变量 |
|---|---|---|
| `chat` | `main_conversation`, `persona_expression`, `self_reflection` | `LLM_CHAT_BASE_URL` / `LLM_CHAT_MODEL` |
| `tool` | `lightweight_judgment`, `memory_processing`, `agent_execution` | `LLM_TOOL_BASE_URL` / `LLM_TOOL_MODEL` |
| `heartbeat` | `heartbeat_proactive` | `NIM_BASE_URL` / `NIM_MODEL` |

若 slot 专用变量未配置，则 fallback 到通用 `LLM_BASE_URL` / `LLM_MODEL`。

路由器会自动检测 base_url 是否含 `/anthropic` 来决定使用 `AsyncAnthropic` 还是 `AsyncOpenAI` 客户端。支持 per-session 覆盖模型（`brain.switch_model(chat_id, selector)`）。

**关键数据类型：**

```python
@dataclass
class ToolCallRequest:
    id: str
    name: str
    arguments: dict[str, Any]

@dataclass
class ToolTurnResult:
    text: str
    tool_calls: list[ToolCallRequest]
    continuation_message: dict[str, Any] | None
```

---

### TaskRuntime（`src/core/task_runtime.py`）

工具循环的实现主体。`complete_chat()` 是核心循环：

```
while rounds < MAX_TOOL_ROUNDS:
    result = await router.tool_turn(messages, tools, purpose="chat")
    if not result.tool_calls:
        break   # 模型决定停止，返回文本
    for call in result.tool_calls:
        # VitalGuard 检查 → AuthorityGate 检查 → 执行工具
        tool_result = await execute_tool(call, ...)
        messages.append(tool_result_as_message)
```

内置循环检测（`LoopDetectionState`）：generic_repeat、ping_pong、known_poll_no_progress 三种检测器，可通过环境变量独立开关。

**`RuntimeDeps`**（注入到工具执行层的底层能力）：

```python
@dataclass(frozen=True)
class RuntimeDeps:
    execute_shell: Callable       # Shell 执行函数
    policy: ShellRuntimePolicy    # Shell 安全策略
    shell_default_cwd: str
    shell_allow_sudo: bool
```

---

### PromptBuilder（`src/core/prompt_builder.py`）

将所有上下文来源组装为 system prompt。分层顺序：

```
Layer 0    — soul.md（核心人格，depth-0 注入防漂移）
Layer 1    — evolution/rules.md（从经验中学到的行为规则）
Layer 0.5  — 当前时间（台北时区）
Layer 2    — data/memory/KEVIN.md（对用户的了解，文件记忆）
Layer 2.5  — SQLite user_facts（结构化事实补充）
Layer 3    — MemoryIndex 近期条目（semantic recall）
Layer 3.5  — VectorStore 相关记忆（向量检索）
Layer 4    — 对话摘要（recent_summaries）
Layer 5    — 技能概览（skill_manager.overview_text()）
Layer 6    — voice.md 注入（✕/✓ 行为约束，depth-0）
```

**Depth-0 注入**：voice reminder 和 persona anchor 写在 `messages` 数组的最后一条 `user` 消息之前，确保不被长对话历史稀释。

---

### HeartbeatEngine（`src/core/heartbeat.py`）

自主感知与行动的定时循环。三种节拍：

| 节拍 | 触发条件 | 典型用途 |
|---|---|---|
| `fast` | 每 N 分钟（`HEARTBEAT_FAST_INTERVAL_MINUTES`，默认 60） | 主动消息、兴趣驱动分享 |
| `slow` | 每日一次（`HEARTBEAT_SLOW_HOUR`，默认 3 AM） | 记忆整理、自省、人格进化 |
| `minute` | 每分钟 | `always` 模式 action（如 ReminderScheduler） |

**扩展心跳 Action：**

```python
class MyAction(HeartbeatAction):
    name = "my_action"
    description = "描述给 LLM 看的一句话"
    beat_types = ["fast"]          # 在哪个节拍执行
    selection_mode = "decide"      # "decide" = LLM 选择；"always" = 无条件执行

    async def execute(self, ctx: SenseContext, brain, send_fn) -> None:
        ...
```

注册到 `AppContainer._build_heartbeat()` 中的 `heartbeat.registry.register(MyAction())`。

`SenseContext` 包含：`beat_type`、当前时间、上次交互时间、沉默小时数、用户画像摘要、对话摘要、兴趣摘要。

---

### Memory（记忆系统）

记忆分四层，各司其职：

| 层 | 模块 | 存储 | 特点 |
|---|---|---|---|
| 对话历史 | `src/memory/conversation.py` | SQLite + 内存缓存 | `chat_id` 分组，WAL 模式 |
| 用户事实 | `ConversationMemory.user_facts` | SQLite `user_facts` 表 | 结构化 key-value |
| 文件记忆 | `src/memory/file_memory.py` | Markdown 文件 | 可直接编辑，KEVIN.md / SELF.md |
| 记忆索引 | `src/memory/memory_index.py` | `data/memory/_index.json` | 分类 + 时间衰减评分 |
| 向量记忆 | `src/memory/vector_store.py` | ChromaDB | 语义检索 |

**会话（Session）**：`SessionManager` 将长对话切分为带 topic 的 Session，每个 Session 有独立的消息序列，通过 `memory.get_session_messages(session_id)` 获取。

**压缩（Compaction）**：`ConversationCompactor` 当历史超过阈值时，调用 LLM 生成摘要，写入 `data/memory/conversations/summaries/`，然后清空 SQLite 历史。

---

### Tool System（工具系统）

**`ToolSpec`**（`src/tools/types.py`）是工具的不可变描述符：

```python
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str           # 展示给 LLM 的描述
    json_schema: dict          # 参数 schema（OpenAI function calling 格式）
    executor: ToolExecutor     # 异步执行函数
    capability: str            # 主标签（shell/web/file/memory/schedule/...）
    capabilities: tuple[str]   # 附加标签
    visibility: "model" | "internal"  # internal 不展示给 LLM
    risk_level: "low" | "medium" | "high"
```

**`ToolRegistry`** 按名称和 capability 过滤工具列表，`TaskRuntime` 在每次调用前通过 `chat_tools()` 获取当前上下文适用的工具集。

**`ToolExecutionContext`** 是每次工具调用时传入执行函数的上下文：

```python
@dataclass(frozen=True)
class ToolExecutionContext:
    execute_shell: Callable
    shell_default_cwd: str
    workspace_root: str
    services: dict[str, Any]   # 注入 skill_manager、reminder_scheduler 等
    adapter: str               # 消息来源（"telegram"/"qq"/"desktop"）
    user_id: str
    auth_level: int            # AuthLevel 枚举值
    chat_id: str
    memory: ConversationMemory | None
    memory_index: MemoryIndex | None
```

**新增工具**：在 `src/tools/handlers.py` 实现执行函数，在 `src/tools/registry.py` 的 `build_default_tool_registry()` 注册 `ToolSpec`。

**RuntimeProfile**（`src/core/runtime_profiles.py`）按执行上下文过滤可用工具集：

| Profile | 用途 | 包含的 capability |
|---|---|---|
| `chat_shell` | 主对话 | shell, web, skill, memory, schedule, general, browser |
| `coder_snippet` | 代码片段执行 | code, verify |
| `coder_workspace` | 工作区级代码操作 | code, file, verify |
| `file_ops` | 文件读写 | file |

---

### BrowserManager（浏览器子系统）

`BrowserManager`（`src/core/browser_manager.py`）基于 Playwright 持久化上下文控制 Chromium：

- **持久化上下文**：用户数据目录 `data/browser/profile/`，保留 Cookie / LocalStorage
- **Tab 管理**：最多 `BROWSER_MAX_TABS`（默认 8）个标签页
- **DOM 提取**：结构化元素状态输出，供 LLM 消费（`BROWSER_MAX_ELEMENT_COUNT` 控制数量上限）
- **截图**：保存到 `data/browser/screenshots/`，按 `BROWSER_SCREENSHOT_RETAIN_DAYS` 自动清理
- **视觉理解**：当页面图片占比高（超过 `BROWSER_VISION_IMG_THRESHOLD`），截图发送到独立 LLM slot（`BROWSER_VISION_SLOT`）生成视觉描述，带 TTL 缓存

**安全层：`BrowserGuard`**（`src/guards/browser_guard.py`）

- URL 黑白名单（`BROWSER_URL_BLACKLIST` / `BROWSER_URL_WHITELIST`）
- 内网访问阻断（`BROWSER_BLOCK_INTERNAL_NETWORK`）
- 敏感操作检测（删除、支付、购买等，`BROWSER_SENSITIVE_ACTION_WORDS`）

启用：`BROWSER_ENABLED=true`。25+ 个 `BROWSER_*` 环境变量在 `config/settings.py` 中定义。

**自主浏览**（`src/heartbeat/actions/autonomous_browsing.py`）：心跳驱动的后台浏览行为，发现的知识写入 `src/memory/discoveries.py`。由 `BROWSE_ENABLED` 控制。

---

### Vitals（生命体征）

`src/core/vitals.py` — 轻量级生命周期追踪模块：

- **启动/运行时间**：`boot_time()`、`uptime_seconds()`、`uptime_human()`
- **重启感知**：持久化到 `data/vitals.json`（boot_time + last_active + pid）。重启后能知道"睡了多久"
- **系统快照**：`system_snapshot()` 采集 CPU/内存/磁盘（依赖 psutil）
- **桌面环境感知**：`update_desktop_sensing()` 接收桌面端推送的用户状态（当前应用、活动状态），10 分钟 TTL

---

### Channel / Adapter（消息通道）

`BaseAdapter`（`src/adapters/base.py`）定义通道接口：

```python
class BaseAdapter(ABC):
    channel_type: ChannelType

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def send_message(self, chat_id: str, message: RichMessage) -> None: ...
    async def is_connected(self) -> bool: ...
```

现有实现：

| 适配器 | 协议 | 文件 |
|---|---|---|
| `TelegramApp` | Bot API polling | `src/app/telegram_app.py` |
| `QQAdapter` | OneBot v11 WebSocket | `src/adapters/qq_adapter.py` |
| `DesktopChannelAdapter` | 本地内存（SSE 推送） | `src/adapters/desktop_adapter.py` |

`ChannelManager`（`src/core/channel_manager.py`）管理所有已注册的 Adapter，主动消息路由优先级：Desktop > last_active > 任意已连接通道。

`RichMessage`（`src/models/message.py`）是跨通道的富媒体消息容器，支持文本、图片（URL/Base64/路径）混合组合。

---

### Skills（技能系统）

Skills 是可被 LLM 工具调用（或用户 `/command` 触发）的结构化任务模板，存为 YAML/Markdown 文件。

目录扫描顺序（`SkillManager`）：`skills/bundled/` → `skills/managed/` → `SKILLS_EXTRA_DIRS`。

**触发方式两种：**

- `command_dispatch = "dialogue"`：将技能内容注入 system prompt，走正常对话 → LLM 生成回复
- `command_dispatch = "tool"`：直接派发到白名单工具（`SKILLS_DISPATCH_TOOL_WHITELIST`），跳过对话

**经验技能**（`ExperienceSkillManager`，`src/core/experience_skills.py`）：Lapwing 自动从过往对话中提炼的操作经验，语义检索后注入 prompt 中的"参考经验"区块。

---

### 权限与存活保护

**`AuthorityGate`**（`src/core/authority_gate.py`）：

```
OWNER   — Kevin（config 中的 OWNER_IDS），可执行所有工具
TRUSTED — 信任用户（TRUSTED_IDS），可用普通功能
GUEST   — 其他人，仅聊天
```

Desktop 本地连接默认 OWNER（`DESKTOP_DEFAULT_OWNER=true`）。

**`VitalGuard`**（`src/core/vital_guard.py`）：

在 `TaskRuntime` 执行 Shell 或文件写入工具前，检查目标路径是否命中 vital manifest。核心文件（soul.md、constitution.md、lapwing.db 等）被保护，未授权修改请求直接拒绝。

**`ConstitutionGuard`**（`src/core/constitution_guard.py`）：

进化引擎修改人格文件时，验证 diff 不违反宪法条款。

**Watchdog Sentinel**（`watchdog/`）：独立进程，对 vital files 做周期性哈希校验，异常时自动从备份恢复。

---

## 桌面端 (Desktop v2)

`desktop-v2/` 是活跃开发的桌面端前端，替代旧版 `desktop/`（Tauri v1 + React 18）。

**技术栈**：Tauri v2, React 19, TypeScript, Zustand（状态管理）, Tailwind CSS 4, shadcn/ui, CodeMirror（Markdown 编辑）, Recharts（仪表盘图表）, react-router-dom, Lucide 图标。

**页面**：ChatPage, DashboardPage, MemoryPage, ModelRoutingPage, PersonaPage, SensingPage, SettingsPage, TaskCenterPage。

**状态管理**：Zustand stores 在 `desktop-v2/src/stores/`（chat.ts, server.ts）。类型定义在 `desktop-v2/src/types/`。

**组件**：按领域组织在 `desktop-v2/src/components/` 下（chat, dashboard, layout, memory, model-routing, persona, sensing, shared, tasks, ui）。

```bash
cd desktop-v2 && npm install
cd desktop-v2 && npm run dev         # Vite dev server (localhost:1420)
cd desktop-v2 && npm run tauri dev   # 完整 Tauri v2 应用
cd desktop-v2 && npm run build       # 生产构建
```

---

## 关键数据结构

```
RichMessage           — 跨通道富媒体消息（text + image list）
ToolSpec              — 工具描述符（不可变）
ToolExecutionRequest  — {name, arguments}
ToolExecutionResult   — {success, payload, reason}
ToolExecutionContext  — 工具执行时的环境（shell/auth/services）
SenseContext          — 一次心跳的环境快照
_ThinkCtx             — Brain._prepare_think() 的共享前置结果
ToolCallRequest       — LLM 返回的工具调用请求
ToolTurnResult        — 一轮 LLM 响应（text + tool_calls）
AuthLevel             — GUEST(0) / TRUSTED(1) / OWNER(2)
```

---

## 配置与环境变量

所有配置在 `config/settings.py` 中通过 `os.getenv()` 加载，文件来源 `config/.env`。

**模型路由（三组）：**

```
LLM_BASE_URL / LLM_MODEL         — 通用 fallback
LLM_CHAT_BASE_URL / LLM_CHAT_MODEL  — chat purpose
LLM_TOOL_BASE_URL / LLM_TOOL_MODEL  — tool purpose
NIM_BASE_URL / NIM_MODEL         — heartbeat purpose
```

**Feature Flags（`FEATURE_ENABLED` 命名模式）：**

```
SKILLS_ENABLED                — Skills 系统
MEMORY_CRUD_ENABLED           — 记忆 CRUD 工具
AUTO_MEMORY_EXTRACT_ENABLED   — 自动记忆提取（Wave 1）
SELF_SCHEDULE_ENABLED         — 自调度工具
SESSION_ENABLED               — 会话分段
EXPERIENCE_SKILLS_ENABLED     — 经验技能系统
QUALITY_CHECK_ENABLED         — 回复质量检查
QQ_ENABLED                    — QQ 通道
BROWSER_ENABLED               — 浏览器子系统
BROWSE_ENABLED                — 自主浏览（心跳动作）
LOOP_DETECTION_ENABLED        — 工具循环检测
SHELL_ENABLED                 — Shell 执行
DELEGATION_ENABLED            — 任务委派
MESSAGE_SPLIT_ENABLED         — 消息分段
```

---

## 数据目录结构

```
data/
  identity/
    soul.md             ← 核心人格（Lapwing 不可自改）
    constitution.md     ← 进化宪法（ConstitutionGuard 保护）
  memory/
    KEVIN.md            ← 对 Kevin 的了解（文件记忆）
    SELF.md             ← 自我认知
    _index.json         ← MemoryIndex 索引
    conversations/
      summaries/        ← 对话压缩摘要（Markdown）
    sessions/           ← SessionManager 的 session 元数据
  evolution/
    rules.md            ← 从纠错中积累的行为规则
    interests.md        ← 兴趣图谱
    changelog.md        ← 人格变化日志（diff 格式）
  browser/
    profile/            ← Playwright 持久化上下文（Cookie / LocalStorage）
    screenshots/        ← 页面截图（自动按天清理）
  credentials/
    vault.enc           ← 加密凭据存储
  config/
    model_routing.json  ← 运行时模型路由配置
  lapwing.pid           ← 进程锁文件
  lapwing.db            ← SQLite（conversations + user_facts + reminders）
  vitals.json           ← 启动/关闭状态（重启感知）
  chroma/               ← ChromaDB 向量存储
```

---

## 扩展指南

### 新增一个工具

1. 在 `src/tools/handlers.py` 实现 `async def my_tool(req, ctx) -> ToolExecutionResult`
2. 在 `src/tools/registry.py` 的 `build_default_tool_registry()` 中注册：
   ```python
   registry.register(ToolSpec(
       name="my_tool",
       description="...",
       json_schema={...},
       executor=my_tool,
       capability="my_cap",
       risk_level="low",
   ))
   ```
3. 若需权限限制，在 `src/core/authority_gate.py` 的工具权限表中添加条目。

### 新增一个心跳 Action

1. 在 `src/heartbeat/actions/` 创建文件，继承 `HeartbeatAction`（见上方接口说明）
2. 在 `AppContainer._build_heartbeat()` 中 `heartbeat.registry.register(MyAction())`

### 新增一个消息通道

1. 继承 `BaseAdapter`，实现 `start/stop/send_message/is_connected`
2. 在 `main.py` 中构造 adapter 并调用 `container.channel_manager.register(ChannelType.XXX, adapter)`
3. 消息进入 brain：调用 `brain.think_conversational(chat_id, text, send_fn, adapter="xxx", user_id="...")`
