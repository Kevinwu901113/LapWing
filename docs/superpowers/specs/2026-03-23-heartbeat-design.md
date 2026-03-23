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
    recent_memory_summary: str        # 最近 N 条对话摘要（慢心跳用）
    chat_id: str                      # 目标用户的 chat_id
```

SenseLayer 从 `ConversationMemory` 读取数据：
- `silence_hours`：从最后一条 conversations 记录的 timestamp 推算
- `user_facts_summary`：调用 `get_user_facts()` 格式化为文本
- `recent_memory_summary`：取最近 20 条对话格式化（慢心跳）/ 空字符串（快心跳）

### NIM 决策格式

输入（prompt 包含）：
- SenseContext 的序列化文本
- 当前 beat_type 可用的 action 名称和描述

输出（NIM 返回 JSON）：
```json
{
  "actions": ["proactive_message"],
  "reason": "用户已超过 20 小时未发消息，且当前是上午，适合主动关心"
}
```

`"actions": []` 时本次心跳静默结束。

---

## HeartbeatAction 接口

```python
class HeartbeatAction:
    name: str            # action 唯一标识，也是 NIM 决策 JSON 中的名称
    description: str     # 告诉 NIM 这个 action 的用途（自然语言）
    beat_types: list[str]  # ["fast"] / ["slow"] / ["fast", "slow"]

    async def execute(
        self,
        ctx: SenseContext,
        brain: LapwingBrain,
        bot,             # python-telegram-bot Bot 实例
    ) -> None:
        ...
```

ActionRegistry 按 `beat_type` 过滤，只向 NIM 暴露当前心跳类型适用的 actions。

---

## 内置 Actions

### ProactiveMessageAction（快心跳）

**触发条件**：由 NIM 根据 SenseContext 判断（silence_hours、时间段、discoveries 等）

**执行流程**：
1. 检查 `discoveries` 表是否有未分享内容
2. 将 SenseContext + 发现内容（如有）喂给 NIM（`heartbeat` purpose）
3. NIM 根据 `prompts/heartbeat_proactive.md` 生成符合 Lapwing 人格的消息
4. 通过 Telegram bot 发送消息
5. 将消息存入 `conversations` 表（Lapwing 主动说的话也是记忆）
6. 如使用了 discovery，标记为已分享

**时间约束**（写入 `prompts/heartbeat_decision.md`）：
- 23:00–07:00 之间不发早安类消息
- 白天不发晚安类消息
- 如有疑问，宁可不发

### MemoryConsolidationAction（慢心跳）

**执行流程**：
1. 取最近 50 条对话记录
2. NIM 生成记忆摘要，存入 `user_facts`（key: `memory_summary_YYYY-MM-DD`）
3. 主动触发一次 `FactExtractor._run_extraction()`，做深度 fact 提取

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

`LLMRouter._PURPOSE_ENV` 新增 `heartbeat` purpose，指向 NIM 配置。
保持向后兼容：NIM 未配置时回退到 `tool` 模型。

---

## Prompt 文件

| 文件 | 用途 |
|---|---|
| `prompts/heartbeat_decision.md` | 决策 prompt：输入 SenseContext + actions，输出 JSON 决策 |
| `prompts/heartbeat_proactive.md` | 生成主动消息：输入 SenseContext，输出一条符合人格的消息 |

---

## 配置项（config/.env）

```
# NVIDIA NIM
NIM_API_KEY=...
NIM_BASE_URL=https://integrate.api.nvidia.com/v1
NIM_MODEL=meta/llama-3.1-8b-instruct

# 心跳调度
HEARTBEAT_FAST_INTERVAL_HOURS=1   # 快心跳间隔（小时）
HEARTBEAT_SLOW_HOUR=3              # 慢心跳触发时刻（每天几点，默认凌晨3点）
HEARTBEAT_ENABLED=true
```

---

## 文件变更清单

**新建**：
- `src/core/heartbeat.py` — HeartbeatEngine、SenseContext、ActionRegistry
- `src/heartbeat/actions/proactive.py` — ProactiveMessageAction
- `src/heartbeat/actions/consolidation.py` — MemoryConsolidationAction
- `src/heartbeat/actions/__init__.py`
- `prompts/heartbeat_decision.md`
- `prompts/heartbeat_proactive.md`

**修改**：
- `src/memory/conversation.py` — 新增 discoveries 表和相关方法
- `src/core/llm_router.py` — 新增 `heartbeat` purpose
- `config/settings.py` — 新增 NIM 和心跳配置项
- `config/.env.example` — 新增示例
- `main.py` — 启动时初始化并启动 HeartbeatEngine
- `requirements.txt` — 新增 apscheduler

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

1. 配置 `HEARTBEAT_FAST_INTERVAL_HOURS=0.05`（3 分钟）→ 等待 → Lapwing 主动发来消息
2. 消息时间符合常识（白天发早安、晚上发关心）
3. 重启后心跳自动重启，无需手动干预
4. 未配置 NIM 时系统正常启动，心跳回退到 tool 模型
