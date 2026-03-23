# Lapwing Heartbeat 设计文档

**日期**: 2026-03-23
**状态**: 待实现
**替代**: Task 4（主动消息，APScheduler cron 方案）

---

## 背景与动机

Lapwing 目前是纯被动的——她只有在用户发消息时才会响应。Task 4 原计划用死板的 cron 任务实现早安/晚安问候，但这与 Lapwing"有真正内在生命"的设计目标不符。

Heartbeat 的目标是给 Lapwing 一个真正的"自主节奏"：她会周期性地感知当前状态，由 LLM 动态决定该做什么，而不是机械执行固定脚本。驱动模型使用 NVIDIA NIM 免费 API，将后台开销与主对话模型成本隔离。

---

## 核心设计原则

1. **感知优先于行动** — 每次心跳先 Sense（收集环境快照），再 Act（LLM 决策）
2. **静默是常态** — NIM 决定"无需行动"时不打扰用户，心跳对用户透明
3. **可扩展，不可破坏** — 新 action 只需实现接口并注册，不改引擎核心
4. **时间常识内置** — prompt 层硬约束，避免不合时宜的主动消息

---

## 架构概览

```
HeartbeatEngine
├── APScheduler
│   ├── 快心跳（每小时）— 检查是否主动联系用户
│   └── 慢心跳（每日固定时刻）— 整理记忆、深度提取
│
├── SenseLayer — 收集环境快照 → SenseContext
│
├── ActionRegistry — 注册并管理所有 HeartbeatAction
│
└── NIM Decision Layer
    └── SenseContext + 可用 actions → NIM → 结构化决策 → 执行
```

---

## 多用户处理

HeartbeatEngine 每次触发时，通过 `ConversationMemory` 执行
`SELECT DISTINCT chat_id FROM conversations` 枚举所有已知用户，
依次为每个 `chat_id` 生成 SenseContext 并独立决策。
单用户场景（个人部署）只有一条记录，多用户时每人独立运行。

---

## 数据结构

### SenseContext

```python
@dataclass
class SenseContext:
    beat_type: str                    # "fast" | "slow"
    now: datetime                     # 当前时间（含时区）
    last_interaction: datetime | None # 上次用户发消息的时间
    silence_hours: float              # 距上次对话已沉默多少小时
    user_facts_summary: str           # 用户画像文字摘要
    recent_memory_summary: str        # 最近 20 条对话格式化文本（慢心跳填充，快心跳为空字符串）
    chat_id: str                      # 目标用户的 chat_id
```

SenseLayer 从 `ConversationMemory` 读取数据：
- `silence_hours`：查询 `conversations` 表最后一条记录的 timestamp，与 `now` 做差
- `user_facts_summary`：调用 `get_user_facts()` 格式化为文本
- `recent_memory_summary`：慢心跳时取最近 20 条对话格式化，快心跳时为空字符串

### NIM 决策格式

决策是心跳的第一次 NIM 调用（`heartbeat` purpose，使用 `prompts/heartbeat_decision.md`）。

输入（prompt 包含）：
- SenseContext 的序列化文本
- 当前 beat_type 可用的 action 列表（名称 + 描述）

输出（NIM 返回 JSON，prompt 中明确要求此格式）：
```json
{
  "actions": ["proactive_message"],
  "reason": "用户已超过 20 小时未发消息，且当前是上午，适合主动关心"
}
```

`"actions": []` 时本次心跳静默结束，不调用任何 action 的 `execute()`。

注意：NIM 调用使用普通文本 prompt 指令要求 JSON 输出，不依赖
`response_format={"type": "json_object"}` 参数（部分 NIM 模型不支持该参数）。
`_parse_result()` 模式与 FactExtractor 一致，防御性解析，失败时静默跳过。

---

## HeartbeatAction 接口

`HeartbeatAction` 是 ABC（`abc.ABC`），所有内置和未来 action 必须继承它：

```python
from abc import ABC, abstractmethod

class HeartbeatAction(ABC):
    name: str              # action 唯一标识，也是 NIM 决策 JSON 中的名称
    description: str       # 告诉 NIM 这个 action 的用途（自然语言）
    beat_types: list[str]  # ["fast"] / ["slow"] / ["fast", "slow"]

    @abstractmethod
    async def execute(
        self,
        ctx: SenseContext,
        brain: "LapwingBrain",
        bot,               # python-telegram-bot Bot 实例（application.bot）
    ) -> None: ...
```

ActionRegistry 按 `beat_type` 过滤，只向 NIM 暴露当前心跳类型适用的 actions。

---

## 内置 Actions

### ProactiveMessageAction（快心跳）

**触发条件**：由 NIM 决策层判断（第一次 NIM 调用）。

**执行流程**（`execute()` 内的第二次 NIM 调用，专门生成消息文本）：
1. 调用 `memory.get_unshared_discoveries(chat_id, limit=3)` 取未分享内容（如有）
2. 将 SenseContext + discoveries 摘要喂给 NIM（`heartbeat` purpose，使用 `prompts/heartbeat_proactive.md`）
3. NIM 返回一条符合 Lapwing 人格的纯文字消息
4. 通过 `bot.send_message(chat_id=ctx.chat_id, text=reply)` 发送
5. 调用 `memory.append(chat_id, "assistant", reply)` 存入对话历史
6. 如使用了 discovery，调用 `memory.mark_discovery_shared(discovery_id)` 标记已分享

**时间约束**（写入 `prompts/heartbeat_decision.md`，第一次 NIM 调用的 prompt）：
- 23:00–07:00 之间不发早安类消息
- 白天不发晚安类消息
- 如有疑问，选择 `"actions": []`

### MemoryConsolidationAction（慢心跳）

**执行流程**：
1. 取最近 50 条对话记录格式化为文本
2. 调用 NIM（`heartbeat` purpose）生成记忆摘要，存入 `user_facts`（key: `memory_summary_YYYY-MM-DD`）
   - `UNIQUE(chat_id, fact_key)` 约束确保每日只保留最新摘要，旧日期 key 自然积累但不覆盖
3. 调用 `FactExtractor.force_extraction(chat_id)`（公开方法，见下）做深度 fact 提取

**FactExtractor 新增公开方法**：

```python
async def force_extraction(self, chat_id: str) -> None:
    """外部主动触发一次提取（供 HeartbeatEngine 的慢心跳调用）。"""
    await self._run_extraction(chat_id)
```

---

## 数据库变更

`ConversationMemory._create_tables()` 新增 `discoveries` 表：

```sql
CREATE TABLE IF NOT EXISTS discoveries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id       TEXT NOT NULL,
    source        TEXT NOT NULL,     -- 来源标识（如 "web_explore"）
    title         TEXT NOT NULL,
    summary       TEXT NOT NULL,
    url           TEXT,
    discovered_at TEXT NOT NULL,
    shared_at     TEXT              -- NULL 表示未分享
);
CREATE INDEX IF NOT EXISTS idx_discoveries_chat_id ON discoveries(chat_id);
CREATE INDEX IF NOT EXISTS idx_discoveries_shared ON discoveries(chat_id, shared_at);
```

新增方法：
- `add_discovery(chat_id, source, title, summary, url)` → None
- `get_unshared_discoveries(chat_id, limit)` → list[dict]
- `mark_discovery_shared(discovery_id)` → None

---

## LLM 路由变更

`config/.env` 新增：
```
NIM_API_KEY=nvapi-...
NIM_BASE_URL=https://integrate.api.nvidia.com/v1
NIM_MODEL=meta/llama-3.1-8b-instruct
```

`LLMRouter._PURPOSE_ENV` 新增 `heartbeat` 条目：
```python
"heartbeat": (NIM_API_KEY, NIM_BASE_URL, NIM_MODEL),
```

现有 `_setup_clients()` 逻辑：若 `api_key/base_url/model` 任一为空，则回退到通用 `LLM_*` 配置（与 `chat`/`tool` 的回退逻辑一致）。NIM 未配置时 `heartbeat` purpose 回退到通用模型，而不是特指 `tool` 模型——与现有代码保持一致。

---

## Prompt 文件

### `prompts/heartbeat_decision.md`

**用途**：决策 prompt，判断本次心跳是否需要行动。

**输入变量**（通过 `.format()` 注入）：
- `{beat_type}` — "fast" 或 "slow"
- `{now}` — 当前时间（格式：`2026-03-23 03:00 CST 星期一`）
- `{silence_hours}` — 距上次对话小时数（float）
- `{user_facts_summary}` — 用户画像摘要文本
- `{available_actions}` — JSON 数组，每项含 `name` 和 `description`

**输出格式**（prompt 中明确要求，不使用 API 参数强制）：
```json
{"actions": [...action names...], "reason": "..."}
```
或 `{"actions": [], "reason": "..."}` 表示静默。

**关键约束（写入 prompt）**：
- 23:00–07:00 不发早安类消息
- 白天不发晚安类消息
- silence_hours < 1 时不发主动消息（用户刚刚活跃）
- 如无明确理由行动，选择空 actions

### `prompts/heartbeat_proactive.md`

**用途**：根据 SenseContext 生成 Lapwing 的主动消息文本。

**输入变量**：
- `{now}` — 当前时间
- `{silence_hours}` — 沉默时长
- `{user_facts_summary}` — 用户画像
- `{discoveries_summary}` — 未分享的发现内容摘要（无内容时为空字符串）

**输出**：一段纯文字，符合 Lapwing 人格（温柔知性，不用表情符号），长度控制在 100 字以内。不包含 JSON 或其他格式。

---

## 配置项（config/.env）

```
# NVIDIA NIM
NIM_API_KEY=...
NIM_BASE_URL=https://integrate.api.nvidia.com/v1
NIM_MODEL=meta/llama-3.1-8b-instruct

# 心跳调度
HEARTBEAT_ENABLED=true
HEARTBEAT_FAST_INTERVAL_MINUTES=60  # 快心跳间隔（分钟，整数）— 测试时可设为 3
HEARTBEAT_SLOW_HOUR=3               # 慢心跳触发时刻（每天几点，整数，默认凌晨3点）
```

APScheduler 使用 `minutes=HEARTBEAT_FAST_INTERVAL_MINUTES` 参数配置快心跳，避免浮点小时数的歧义。

---

## 文件变更清单

**新建**：
- `src/core/heartbeat.py` — HeartbeatEngine、SenseContext、ActionRegistry、HeartbeatAction（ABC）
- `src/heartbeat/__init__.py`
- `src/heartbeat/actions/__init__.py`
- `src/heartbeat/actions/proactive.py` — ProactiveMessageAction
- `src/heartbeat/actions/consolidation.py` — MemoryConsolidationAction
- `prompts/heartbeat_decision.md`
- `prompts/heartbeat_proactive.md`

**修改**：
- `src/memory/conversation.py` — 新增 `discoveries` 表 + 3 个方法（带类型注解）
- `src/memory/fact_extractor.py` — 新增 `force_extraction(chat_id)` 公开方法
- `src/core/llm_router.py` — 新增 `heartbeat` purpose
- `config/settings.py` — 新增 NIM 和心跳配置项
- `config/.env.example` — 新增示例
- `main.py` — `post_init` 中初始化并启动 HeartbeatEngine；`post_shutdown` 中调用 `await heartbeat_engine.shutdown()`
- `requirements.txt` — 新增 apscheduler

## HeartbeatEngine 生命周期集成

```python
# main.py — post_init
async def post_init(application: Application) -> None:
    await brain.init_db()
    heartbeat_engine = HeartbeatEngine(brain=brain, bot=application.bot)
    heartbeat_engine.start()                    # 启动 AsyncIOScheduler
    application.bot_data["heartbeat"] = heartbeat_engine

# main.py — post_shutdown
async def post_shutdown(application: Application) -> None:
    heartbeat = application.bot_data.get("heartbeat")
    if heartbeat:
        await heartbeat.shutdown()              # 停止 scheduler
    await brain.fact_extractor.shutdown()
    await brain.memory.close()
```

`HeartbeatEngine.__init__` 接受 `brain: LapwingBrain` 和 `bot`（`telegram.Bot`）。
`start()` 创建并启动 `AsyncIOScheduler`，注册两个 job：
- `trigger=IntervalTrigger(minutes=HEARTBEAT_FAST_INTERVAL_MINUTES)`，调用 `self._run_beat("fast")`
- `trigger=CronTrigger(hour=HEARTBEAT_SLOW_HOUR)`，调用 `self._run_beat("slow")`

`shutdown()` 调用 `scheduler.shutdown(wait=False)` 并 await 所有 in-flight tasks 完成。

---

## 未来扩展接口

以下 actions 无需改引擎，直接新建文件并注册即可：

| 未来 Action | 对应路线图任务 | 所需新增能力 |
|---|---|---|
| `WebExploreAction` | Task 6 Researcher Agent | 调用 Researcher Agent，写入 discoveries 表 |
| `ShareDiscoveryAction` | Task 10 主动分享发现 | 读取 discoveries 表，ProactiveMessageAction 已预留入口 |
| `ReminderAction` | Task 4 提醒功能 | 读取 user_facts 中的日程信息 |

---

## 验证方式

1. 配置 `HEARTBEAT_FAST_INTERVAL_MINUTES=3` → 等待 3 分钟 → Lapwing 主动发来消息
2. 消息内容符合时间常识（白天发关心，不在深夜发早安）
3. 重启后心跳自动随 `post_init` 重启，无需手动干预
4. 未配置 NIM 时（NIM_API_KEY 为空）系统正常启动，日志显示 `heartbeat` purpose 回退到通用模型
5. `post_shutdown` 时 scheduler 干净退出，无 asyncio 警告
