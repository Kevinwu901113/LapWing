# Lapwing Session System — 设计蓝图

> 本文档是完整的实现规范，可直接交给 Claude Code 执行。
> 所有文件路径、行号和代码均基于当前 v6 代码库。

---

## 一、问题

当前所有对话共享一个永续的滑动窗口（40 条消息），不同话题混在一起送给 LLM。上午聊论文、中午查比赛、下午调代码——全部压在同一个上下文里，既浪费 token，又互相污染。

## 二、核心概念

在 `chat_id`（用户身份）之下引入 `session`（话题窗口）。

```
chat_id (用户)
├── Session A — "论文写作"     [active]
├── Session B — "道奇比赛查询"  [dormant, 40min ago]
├── Session C — "调试 bug"     [condensed, 5h ago]
└── (用户级数据：facts, interests, todos, reminders, discoveries)
```

### 四级生命周期

| 状态 | 含义 | LLM 上下文 | 内存 | 磁盘 |
|------|------|------------|------|------|
| **Active** | 当前正在聊的话题 | ✅ 完整送入 | 完整消息 | 实时写 DB |
| **Dormant** | 话题切走了，随时可回来 | ❌ | 完整消息 | DB |
| **Condensed** | 压缩保留，仍可唤回 | ❌ | 仅摘要 | 快照文件 + DB |
| **Deleted** | 最终清除 | ❌ | 无 | 仅 DB 归档 |

### 状态流转

```
[新消息] ──→ 创建 Active session
                │
                ├── 话题切换 / 超时 ──→ Dormant（完整消息在内存）
                │                        │
                │   ┌── 话题匹配回来 ←───┤
                │   │                    │
                │   │                    └── 超过 DORMANT_TTL ──→ Condensed（压缩 + 写快照）
                │   │                                              │
                │   ├── 话题匹配回来（从快照恢复完整上下文）←──────┤
                │   │                                              │
                │   │                                              └── 超过 CONDENSED_TTL ──→ Deleted
                │   │
                │   └── 消息数 < MIN_MESSAGES ──→ 直接 Deleted（太短不值得保留）
                │
                └── 继续聊 ──→ 保持 Active
```

**关键约束：** 同一 `chat_id` 同一时间只有 **一个** Active session。

---

## 三、数据模型

### 3.1 新增 `sessions` 表

```sql
CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,              -- UUID
    chat_id         TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active', -- active / dormant / condensed / deleted
    topic_summary   TEXT NOT NULL DEFAULT '',       -- 话题摘要（5-15字）
    topic_keywords  TEXT NOT NULL DEFAULT '[]',     -- JSON array，用于快速匹配
    snapshot_path   TEXT,                           -- condensed 快照文件路径（相对于 SESSION_SNAPSHOTS_DIR）
    created_at      TEXT NOT NULL,
    last_active_at  TEXT NOT NULL,
    condensed_at    TEXT,                           -- 进入 condensed 状态的时间
    message_count   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_chat_id_status
    ON sessions(chat_id, status);
CREATE INDEX IF NOT EXISTS idx_sessions_last_active
    ON sessions(last_active_at);
```

### 3.2 `conversations` 表增加 `session_id` 列

```sql
ALTER TABLE conversations ADD COLUMN session_id TEXT;
CREATE INDEX IF NOT EXISTS idx_conversations_session_id
    ON conversations(session_id);
```

迁移策略：已有消息的 `session_id` 为 NULL，视为"遗留对话"。`SessionManager` 首次为某 `chat_id` 创建 session 时，不迁移旧消息。

### 3.3 Session 快照文件

目录：`data/memory/sessions/`

每个 condensed session 对应一个 markdown 文件：

```
data/memory/sessions/
├── a1b2c3d4.md
├── e5f6g7h8.md
└── ...
```

文件格式：

```markdown
# Session a1b2c3d4
- 话题：论文数据集预处理
- 关键词：论文, 数据集, CSV, 预处理, 导师
- 时间：2026-03-31 14:00 ~ 15:20
- 消息数：12

## 摘要
讨论了导师要求的 CSV 数据集预处理方案，确定用 pandas 做清洗，约定明天提交初稿。

## 对话记录
用户: 帮我看看这个数据集要怎么预处理
Lapwing: 你说的是昨天导师提到的那个吗？
用户: 对，就是那个 CSV 格式的，有好多空值
Lapwing: 我看看... 空值比例大概多少？如果不超过 10% 可以直接 dropna，多的话得考虑填充策略
用户: 大概 15% 左右
...
```

**为什么用 markdown 文件而不是存 DB：**
- 符合"文件是 source of truth"原则，可检查、可 diff
- 与现有的 `data/memory/` 体系一致（journal、conversations/summaries 都是 markdown）
- 快照可能包含几十条消息，放 DB 的单个字段里不方便人工审查
- 快照文件是不可变的——写入后不会修改，只有删除

### 3.4 Python 数据类

```python
# src/core/session_manager.py

@dataclasses.dataclass
class Session:
    id: str
    chat_id: str
    status: str                # "active" | "dormant" | "condensed" | "deleted"
    topic_summary: str
    topic_keywords: list[str]
    snapshot_path: str | None  # condensed 快照文件路径
    created_at: datetime
    last_active_at: datetime
    condensed_at: datetime | None
    message_count: int
```

---

## 四、SessionManager

新文件：`src/core/session_manager.py`

### 4.1 职责

| 方法 | 作用 |
|------|------|
| `resolve_session(chat_id, message) → Session` | 核心方法：为本次消息确定应使用的 session |
| `get_or_create_active(chat_id) → Session` | 获取当前 active session；没有则创建 |
| `create_session(chat_id, topic_summary) → Session` | 创建新 active，现有 active 自动降级 dormant |
| `deactivate(session) → None` | Active → Dormant，生成 topic_summary |
| `condense(session) → None` | Dormant → Condensed，写快照 + 清内存 |
| `reactivate(session) → None` | Dormant/Condensed → Active，按需从快照恢复 |
| `delete_session(session) → None` | 清除内存缓存 + 删快照文件，DB 标记 deleted |
| `find_matching_session(chat_id, message) → Session \| None` | 在 dormant + condensed 中找话题匹配的 |
| `reap_expired(chat_id) → tuple[int, int]` | 两级清理：dormant → condensed，condensed → deleted |
| `list_sessions(chat_id, status) → list[Session]` | 列出指定状态的 sessions |

### 4.2 与 ConversationMemory 的关系

SessionManager **持有** ConversationMemory 的引用，但不替代它。ConversationMemory 的接口扩展：

```python
# 新增方法
async def append_to_session(self, chat_id: str, session_id: str, role: str, content: str, *, channel: str = "telegram") -> None
async def get_session_messages(self, session_id: str) -> list[dict]
async def load_session_from_snapshot(self, session_id: str, messages: list[dict]) -> None
async def clear_session_cache(self, session_id: str) -> None
```

原有的 `get(chat_id)` 和 `append(chat_id, ...)` 保持向后兼容——当 session 系统未启用时走老路径。

### 4.3 内存缓存结构变化

当前：
```python
self._store: dict[str, list[dict]]  # key = chat_id
```

改为双层：
```python
self._store: dict[str, list[dict]]          # key = session_id（active + dormant）
self._legacy_store: dict[str, list[dict]]   # key = chat_id（session 系统关闭时的 fallback）
```

Condensed session 不占 `_store` 空间——它们的完整消息在磁盘快照里，内存中只有 `sessions` 表里的 `topic_summary`。

---

## 五、话题检测（三层策略）

### Layer 1：时间间隔（零成本）

```python
SESSION_TIMEOUT_MINUTES = 30
```

距离 active session 最后一条消息超过 30 分钟 → 直接创建新 session，不调 LLM。

**理由**：30 分钟的沉默几乎一定意味着话题切换或重新开始。这一层拦截了大部分场景。

### Layer 2：LLM 话题判断（轻量）

连续对话中（未超时），每条消息到达时调用一次轻量 LLM：

```
prompt (topic_detect.md):
---
当前话题：{topic_summary}
最近 3 条消息：
{recent_messages}

新消息：{new_message}

这条新消息是否在继续当前话题？
- 如果是同一话题或自然延伸，回答：SAME
- 如果是完全不同的话题，回答：NEW|简短话题描述（10字以内）

只回答一行。
---
```

调用参数：
- `purpose="tool"`（使用最便宜的模型）
- `max_tokens=30`
- `session_key=f"chat:{chat_id}"`
- `origin="core.session_manager.topic_detect"`

**边界处理**：
- 解析失败 → 默认 SAME（保守策略，不误创建）
- "帮我查个东西" 这类模糊请求 → LLM 决定
- 极短消息（<5字，如"嗯"、"好"、"哈哈"）→ 跳过检测，视为 SAME

### Layer 3：Dormant + Condensed 匹配（话题切换时）

当 Layer 1 或 Layer 2 判定"新话题"时，在创建新 session 之前，检查是否有匹配的旧 session 可以复活：

```
prompt (topic_match_dormant.md):
---
休眠中的对话：
{sessions_numbered}

新消息：{new_message}

这条消息是否在回到某个休眠话题？回答编号（如 1），或 NONE。
只回答一行。
---
```

匹配范围同时包含 dormant 和 condensed sessions——它们都有 `topic_summary`，对 LLM 来说匹配逻辑相同，只是复活时的数据来源不同（dormant 从内存、condensed 从快照文件）。

如果 dormant + condensed 数量为 0，跳过此步，直接创建新 session。

**优化路径（Phase 4）**：当 sqlite-vec 迁移完成后，可用 embedding 余弦距离替代 LLM 调用，成本更低。

---

## 六、Session 生命周期细节

### 6.1 创建

触发条件：
1. `chat_id` 没有任何 active session（首次消息 / 所有 session 都已过期）
2. Layer 1 超时判定
3. Layer 2 LLM 判定 NEW 且 Layer 3 无匹配旧 session

创建流程：
1. 现有 active session（如有）→ `deactivate()`
2. 生成 UUID
3. 写入 sessions 表（status=active）
4. 初始化内存缓存

### 6.2 降级为 Dormant

触发条件：新 session 创建时，旧 active 自动降级。

降级流程：
1. 如果 session 消息数 < `SESSION_MIN_MESSAGES_TO_KEEP`（默认 4），直接删除而不是降级（太短的对话不值得保留）
2. 生成 topic_summary（如果还没有）：
   - 用 LLM 从最近几条消息提取 10 字以内的话题摘要
   - 提取 3-5 个 topic_keywords
3. 更新 sessions 表 status → dormant
4. 内存缓存保留（随时可复活，零成本恢复）

### 6.3 Dormant → Condensed（压缩归档）

触发条件：dormant session 的 `last_active_at` 超过 `SESSION_DORMANT_TTL_HOURS`（默认 3 小时）。

压缩流程：
1. **Memory flush**：让 Lapwing 主动审视这段对话，通过 `memory_note` 工具把值得长期保留的信息写入记忆文件（`data/memory/KEVIN.md` 或相关记忆文件）。这一步是静默的——不向用户发送任何消息，仅在后台执行一次 LLM 调用。
2. 触发一次 `fact_extractor.force_extraction()` 确保自动提取的用户画像也已更新
3. 生成摘要（用 LLM 压缩完整对话为一段 100-200 字的摘要）
4. 写快照文件到 `data/memory/sessions/{session_id}.md`：
   - 元信息（话题、关键词、时间范围、消息数）
   - 摘要
   - 完整对话记录（用于复活时恢复上下文）
5. 清除内存缓存（`_store.pop(session_id)`）
6. 更新 sessions 表：status → condensed，填写 `snapshot_path`，记录 `condensed_at`
7. topic_summary 保留在 sessions 表中（用于 Layer 3 匹配）

**Memory flush 的意义**：`fact_extractor` 只能提取结构化的用户画像信息（键值对），但对话中可能包含更丰富的上下文——比如"导师对论文的具体反馈"、"讨论中达成的技术决定"、"未完成的待办事项"。Memory flush 让 Lapwing 用她自己的判断力来决定什么值得记住，写入她自己维护的记忆文件。这比被动提取更可靠，也符合"Lapwing 握着自己的笔"这一设计原则。

**压缩后的内存占用**：sessions 表中一行（topic_summary + keywords），无 `_store` 条目。
相比 dormant 的完整消息列表在内存中，condensed 几乎不占空间。

### 6.4 复活（Dormant 或 Condensed → Active）

**从 Dormant 复活**（消息还在内存里）：
1. 现有 active session → `deactivate()`
2. 目标 dormant session status → active
3. 更新 last_active_at
4. 内存缓存已在，零开销

**从 Condensed 复活**（消息在快照文件里）：
1. 现有 active session → `deactivate()`
2. 读取快照文件 `data/memory/sessions/{session_id}.md`
3. 解析"对话记录"部分，还原为 `list[dict]` 消息列表
4. 加载到内存缓存 `_store[session_id] = messages`
5. 更新 sessions 表：status → active，清空 `condensed_at`
6. 删除快照文件（它已回到内存了，下次降级时会重新生成）

**复活后的上下文质量**：和从未离开一样——完整的对话消息，不是摘要。LLM 能看到之前讨论的所有细节。

### 6.5 Condensed → Deleted（最终清除）

触发条件：condensed session 的 `condensed_at` 超过 `SESSION_CONDENSED_TTL_HOURS`（默认 24 小时）。

删除流程：
1. 删除快照文件 `data/memory/sessions/{session_id}.md`
2. 更新 sessions 表 status → deleted
3. DB 中的消息记录（conversations 表）保留

**不删除 DB 消息的理由**：自省引擎需要按日期回顾所有对话；`conversations` 表是 Lapwing 的完整历史日志。
快照文件可以安全删除——它只是 conversations 表数据的一份格式化副本，随时可以从 DB 重建（但正常流程不需要）。

### 6.6 过期扫描

由 Heartbeat 的 slow beat 触发（已有 compaction_check 先例）。
一次扫描处理两级过期：dormant → condensed，condensed → deleted。

```python
# src/heartbeat/actions/session_reaper.py

class SessionReaperAction(HeartbeatAction):
    name = "session_reaper"
    description = "清理过期的对话会话（压缩休眠 + 删除过期）"
    beat_types = ["slow"]
    selection_mode = "always"

    async def execute(self, ctx, brain, send_fn):
        if brain.session_manager is not None:
            condensed, deleted = await brain.session_manager.reap_expired(ctx.chat_id)
            if condensed > 0 or deleted > 0:
                logger.info(
                    f"[{ctx.chat_id}] Session 清理：{condensed} 个压缩归档，{deleted} 个删除"
                )
```

### 6.7 Dormant + Condensed 总量控制

`SESSION_MAX_DORMANT_PER_CHAT`（默认 5）限制的是 dormant + condensed 的总数。
超出时，按 `last_active_at` 从最老的开始处理：
- 如果最老的是 dormant → condense
- 如果最老的已经是 condensed → delete

这保证内存和磁盘占用都有上限。

---

## 七、配置项

```python
# config/settings.py 新增

# ── Session 管理 ──
SESSION_ENABLED: bool = bool(os.getenv("SESSION_ENABLED", "true").lower() in ("true", "1"))
SESSION_TIMEOUT_MINUTES: int = int(os.getenv("SESSION_TIMEOUT_MINUTES", "30"))
SESSION_DORMANT_TTL_HOURS: float = float(os.getenv("SESSION_DORMANT_TTL_HOURS", "3"))
SESSION_CONDENSED_TTL_HOURS: float = float(os.getenv("SESSION_CONDENSED_TTL_HOURS", "24"))
SESSION_MIN_MESSAGES_TO_KEEP: int = int(os.getenv("SESSION_MIN_MESSAGES_TO_KEEP", "4"))
SESSION_MAX_DORMANT_PER_CHAT: int = int(os.getenv("SESSION_MAX_DORMANT_PER_CHAT", "5"))
SESSION_TOPIC_DETECT_ENABLED: bool = bool(os.getenv("SESSION_TOPIC_DETECT_ENABLED", "true").lower() in ("true", "1"))
SESSION_SHORT_MESSAGE_SKIP_THRESHOLD: int = int(os.getenv("SESSION_SHORT_MESSAGE_SKIP_THRESHOLD", "5"))
SESSION_SNAPSHOTS_DIR: Path = MEMORY_DIR / "sessions"
SESSION_CONDENSE_SUMMARY_MAX_TOKENS: int = int(os.getenv("SESSION_CONDENSE_SUMMARY_MAX_TOKENS", "300"))
```

---

## 八、现有模块影响分析

### 受影响的模块

| 模块 | 变化 | 复杂度 |
|------|------|--------|
| `brain.py` | `_prepare_think` 改用 SessionManager 获取消息 | 中 |
| `conversation.py` | 新增 session-aware 方法，缓存结构变更 | 高 |
| `compactor.py` | 压缩范围限制在 active session 内 | 低 |
| `fact_extractor.py` | 从 session 消息提取，提取结果仍写到 chat_id | 低 |
| `dispatcher.py` | `history` 改从 session 获取 | 低 |
| `heartbeat.py / actions/` | 新增 session_reaper；proactive 消息写入 active session | 低 |
| `telegram_app.py` | `/clear` 命令需要决定清哪个 session | 低 |
| `qq_adapter.py` | 传 session_id 给 memory.append | 低 |

### 不受影响的模块

| 模块 | 原因 |
|------|------|
| `llm_router.py` | session_key 仍然是 `chat:{chat_id}`，不变 |
| `task_runtime.py` | pending_confirmation 仍然按 chat_id，不变 |
| `self_reflection.py` | 按日期查 DB，不依赖内存缓存 |
| `tactical_rules.py` | 从 memory.get() 获取 history，改为 session messages 即可 |
| `evolution_engine.py` | 不直接操作对话历史 |
| `constitution_guard.py` | 不直接操作对话历史 |
| `interest_tracker.py` | notify 仍按 chat_id，不变 |
| `vector_store.py` | 按 chat_id 存取，不变 |
| `auth/` | 独立的认证系统 |
| `tools/` | 不关心 session |
| `agents/` | 接收 AgentTask，不直接操作 memory |

---

## 九、关键代码变更

### 9.1 `brain.py` — `_ThinkCtx` 扩展

```python
@dataclasses.dataclass
class _ThinkCtx:
    """think() / think_conversational() 共享前置逻辑的结果。"""
    messages: list[dict]
    effective_user_message: str
    approved_directory: str | None
    early_reply: str | None = None
    matched_experience_skills: list | None = None
    session_id: str | None = None  # 新增：当前使用的 session ID
```

### 9.2 `brain.py` — `_prepare_think` 改造

```python
# 在 _prepare_think 开头，resolve session 并用 session 写入消息：

if self.session_manager is not None:
    session = await self.session_manager.resolve_session(chat_id, user_message)
    session_id = session.id
    await self.memory.append_to_session(chat_id, session_id, "user", user_message)
else:
    session_id = None
    await self.memory.append(chat_id, "user", user_message)

# ...（中间逻辑不变）...

# 获取历史时：
if session_id is not None:
    history = await self.memory.get_session_messages(session_id)
else:
    history = await self.memory.get(chat_id)

# 压缩时：
await self.compactor.try_compact(chat_id, session_id=session_id)

# 返回 ctx 时带上 session_id：
return _ThinkCtx(
    messages=messages,
    effective_user_message=effective_user_message,
    approved_directory=approved_directory,
    matched_experience_skills=matched_experience_skills,
    session_id=session_id,
)
```

### 9.3 `brain.py` — `think` / `think_conversational` 中的 append 改造

```python
# think() 中：
if ctx.session_id is not None:
    await self.memory.append_to_session(chat_id, ctx.session_id, "assistant", reply)
else:
    await self.memory.append(chat_id, "assistant", reply)

# think_conversational() 中同理
```

### 9.4 `conversation.py` — 新增方法

```python
async def append_to_session(
    self, chat_id: str, session_id: str, role: str, content: str, *, channel: str = "telegram"
) -> None:
    """追加消息到指定 session（先写缓存，再持久化）。"""
    if session_id not in self._store:
        self._store[session_id] = []
    self._store[session_id].append({"role": role, "content": content})

    try:
        timestamp = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT INTO conversations (chat_id, role, content, timestamp, channel, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (chat_id, role, content, timestamp, channel, session_id),
        )
        await self._db.commit()
    except Exception as e:
        logger.error(f"对话消息写入数据库失败: {e}")

async def get_session_messages(self, session_id: str) -> list[dict]:
    """获取指定 session 的对话历史（从缓存读取）。"""
    if session_id not in self._store:
        await self._load_session_history(session_id)
    return self._store.get(session_id, [])

async def _load_session_history(self, session_id: str) -> None:
    """从 DB 加载指定 session 的消息到内存缓存。"""
    max_messages = MAX_HISTORY_TURNS * 2
    try:
        async with self._db.execute(
            """SELECT role, content FROM (
                SELECT id, role, content FROM conversations
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
            ) ORDER BY id ASC""",
            (session_id, max_messages),
        ) as cursor:
            messages = [
                {"role": row[0], "content": row[1]}
                async for row in cursor
            ]
        if messages:
            self._store[session_id] = messages
    except Exception as e:
        logger.error(f"从 DB 加载 session {session_id} 历史失败: {e}")

async def load_session_from_snapshot(self, session_id: str, messages: list[dict]) -> None:
    """从快照恢复的消息加载到内存缓存（用于 condensed session 复活）。"""
    self._store[session_id] = list(messages)

async def clear_session_cache(self, session_id: str) -> None:
    """仅清除指定 session 的内存缓存。"""
    self._store.pop(session_id, None)
```

### 9.5 `session_manager.py` — 快照读写

```python
async def _write_snapshot(self, session: Session, messages: list[dict], summary: str) -> Path:
    """将 session 完整对话写入快照文件。"""
    SESSION_SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    path = SESSION_SNAPSHOTS_DIR / f"{session.id}.md"

    keywords_str = ", ".join(session.topic_keywords)
    time_range = f"{session.created_at.strftime('%Y-%m-%d %H:%M')} ~ {session.last_active_at.strftime('%H:%M')}"

    lines = [
        f"# Session {session.id}",
        f"- 话题：{session.topic_summary}",
        f"- 关键词：{keywords_str}",
        f"- 时间：{time_range}",
        f"- 消息数：{session.message_count}",
        "",
        "## 摘要",
        summary,
        "",
        "## 对话记录",
    ]
    for msg in messages:
        role_label = "用户" if msg["role"] == "user" else "Lapwing"
        lines.append(f"{role_label}: {msg['content']}")

    content = "\n".join(lines) + "\n"
    await asyncio.to_thread(path.write_text, content, encoding="utf-8")
    return path

def _read_snapshot(self, snapshot_path: Path) -> list[dict]:
    """从快照文件解析对话记录，还原为消息列表。"""
    if not snapshot_path.exists():
        logger.warning(f"快照文件不存在: {snapshot_path}")
        return []

    text = snapshot_path.read_text(encoding="utf-8")
    messages = []
    in_conversation = False

    for line in text.splitlines():
        if line.strip() == "## 对话记录":
            in_conversation = True
            continue
        if not in_conversation:
            continue
        if line.startswith("## "):
            break  # 遇到下一个 section 就停止

        if line.startswith("用户: "):
            messages.append({"role": "user", "content": line[4:]})
        elif line.startswith("Lapwing: "):
            messages.append({"role": "assistant", "content": line[9:]})

    return messages
```

### 9.6 `session_manager.py` — condense 方法

```python
async def condense(self, session: Session) -> None:
    """Dormant → Condensed：memory flush + 生成摘要 + 写快照 + 清内存。"""
    # 1. Memory flush：让 Lapwing 主动保存重要信息到记忆文件
    messages = await self._memory.get_session_messages(session.id)
    if not messages:
        await self.delete_session(session)
        return

    await self._memory_flush(session, messages)

    # 2. 确保自动提取的用户画像也已更新
    if self._fact_extractor is not None:
        await self._fact_extractor.force_extraction(session.chat_id)

    # 3. 生成压缩摘要
    summary = await self._generate_condense_summary(messages)

    # 4. 写快照文件
    path = await self._write_snapshot(session, messages, summary)

    # 5. 清内存缓存
    await self._memory.clear_session_cache(session.id)

    # 6. 更新 DB
    now = datetime.now(timezone.utc)
    await self._update_session_status(
        session.id,
        status="condensed",
        snapshot_path=str(path.relative_to(SESSION_SNAPSHOTS_DIR)),
        condensed_at=now,
    )
    session.status = "condensed"
    session.snapshot_path = str(path.relative_to(SESSION_SNAPSHOTS_DIR))
    session.condensed_at = now

async def _memory_flush(self, session: Session, messages: list[dict]) -> None:
    """静默 memory flush：让 Lapwing 审视对话，用 memory_note 工具写入持久记忆。

    这一步不向用户发送任何消息。通过给 LLM 提供对话内容和 memory_note 工具，
    让 Lapwing 自行判断哪些信息值得长期保留。
    """
    conversation_text = "\n".join(
        f"{'用户' if m['role'] == 'user' else 'Lapwing'}: {m['content']}"
        for m in messages[-20:]
    )
    prompt = load_prompt("session_memory_flush").replace("{conversation}", conversation_text)

    try:
        # 提供 memory_note 工具，让 Lapwing 自主决定写入什么
        tools = self._tool_registry.function_tools(
            include_internal=False,
            tool_names={"memory_note"},
        )
        await self._router.complete(
            [
                {"role": "system", "content": load_prompt("lapwing_soul")},
                {"role": "user", "content": prompt},
            ],
            purpose="tool",
            max_tokens=200,
            tools=tools,
            origin="core.session_manager.memory_flush",
        )
        logger.debug(f"[{session.chat_id}] Session {session.id} memory flush 完成")
    except Exception as exc:
        logger.warning(f"[{session.chat_id}] Session memory flush 失败: {exc}")
        # flush 失败不阻塞 condense 流程

async def _generate_condense_summary(self, messages: list[dict]) -> str:
    """用 LLM 生成对话压缩摘要。"""
    conversation_text = "\n".join(
        f"{'用户' if m['role'] == 'user' else 'Lapwing'}: {m['content']}"
        for m in messages[-20:]  # 最多取最近 20 条
    )
    prompt = load_prompt("session_condense").replace("{conversation}", conversation_text)
    try:
        return await self._router.complete(
            [{"role": "user", "content": prompt}],
            purpose="tool",
            max_tokens=SESSION_CONDENSE_SUMMARY_MAX_TOKENS,
            origin="core.session_manager.condense",
        )
    except Exception as exc:
        logger.warning(f"Session 压缩摘要生成失败: {exc}")
        return "（摘要生成失败）"
```

### 9.7 `session_manager.py` — 从 Condensed 复活

```python
async def reactivate(self, session: Session) -> None:
    """Dormant/Condensed → Active，按需从快照恢复完整上下文。"""
    # 先降级当前 active
    current_active = await self._get_active(session.chat_id)
    if current_active is not None:
        await self.deactivate(current_active)

    if session.status == "condensed":
        # 从快照恢复完整消息到内存
        snapshot_path = SESSION_SNAPSHOTS_DIR / session.snapshot_path
        messages = self._read_snapshot(snapshot_path)
        if messages:
            await self._memory.load_session_from_snapshot(session.id, messages)
        else:
            logger.warning(f"从快照恢复 session {session.id} 失败，创建空缓存")
            await self._memory.load_session_from_snapshot(session.id, [])

        # 删除快照文件（已回到内存，下次降级时重新生成）
        try:
            snapshot_path.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning(f"删除快照文件失败: {exc}")

    # 更新状态
    now = datetime.now(timezone.utc)
    await self._update_session_status(
        session.id,
        status="active",
        condensed_at=None,
        snapshot_path=None,
    )
    session.status = "active"
    session.last_active_at = now
    session.condensed_at = None
    session.snapshot_path = None
```

### 9.8 `session_manager.py` — reap_expired（两级扫描）

```python
async def reap_expired(self, chat_id: str) -> tuple[int, int]:
    """清理过期 sessions。返回 (condensed_count, deleted_count)。"""
    now = datetime.now(timezone.utc)
    condensed_count = 0
    deleted_count = 0

    sessions = await self.list_sessions(chat_id)

    # Pass 1: dormant → condensed
    for s in sessions:
        if s.status == "dormant":
            elapsed = (now - s.last_active_at).total_seconds() / 3600
            if elapsed >= SESSION_DORMANT_TTL_HOURS:
                await self.condense(s)
                condensed_count += 1

    # Pass 2: condensed → deleted
    for s in sessions:
        if s.status == "condensed" and s.condensed_at is not None:
            elapsed = (now - s.condensed_at).total_seconds() / 3600
            if elapsed >= SESSION_CONDENSED_TTL_HOURS:
                await self.delete_session(s)
                deleted_count += 1

    # Pass 3: 总量控制
    alive = [s for s in await self.list_sessions(chat_id) if s.status in ("dormant", "condensed")]
    alive.sort(key=lambda s: s.last_active_at)
    while len(alive) > SESSION_MAX_DORMANT_PER_CHAT:
        oldest = alive.pop(0)
        if oldest.status == "dormant":
            await self.condense(oldest)
            condensed_count += 1
        else:
            await self.delete_session(oldest)
            deleted_count += 1

    return condensed_count, deleted_count
```

### 9.9 `compactor.py` — Session 感知

```python
async def try_compact(self, chat_id: str, *, session_id: str | None = None) -> bool:
    key = session_id or chat_id
    if key in self._compacting:
        return False

    history = (
        await self._memory.get_session_messages(session_id)
        if session_id
        else await self._memory.get(chat_id)
    )
    if not self.should_compact(len(history)):
        return False

    self._compacting.add(key)
    try:
        return await self._do_compact(key, history, is_session=session_id is not None)
    finally:
        self._compacting.discard(key)
```

### 9.10 `dispatcher.py` — History 来源

```python
async def try_dispatch(self, chat_id: str, user_message: str, *, session_id: str | None = None) -> str | None:
    ...
    if session_id:
        history = await self._memory.get_session_messages(session_id)
    else:
        history = await self._memory.get(chat_id)
```

### 9.11 Heartbeat proactive 消息

```python
# proactive.py — 主动消息写入 active session
async def execute(self, ctx, brain, send_fn):
    ...
    await send_fn(reply)

    if brain.session_manager is not None:
        session = await brain.session_manager.get_or_create_active(ctx.chat_id)
        await brain.memory.append_to_session(ctx.chat_id, session.id, "assistant", reply)
    else:
        await brain.memory.append(ctx.chat_id, "assistant", reply)
```

---

## 十、新增 Prompt 文件

### `prompts/topic_detect.md`

```markdown
你是一个话题分类器。判断新消息是否在继续当前话题。

当前话题：{topic_summary}
最近对话：
{recent_messages}

新消息：{new_message}

规则：
- 如果新消息是对当前话题的延续、补充、追问，回答 SAME
- 如果新消息开始了一个完全不同的话题，回答 NEW|话题描述（10字以内）
- "嗯""好""哈哈"等极短回应视为 SAME
- 如果不确定，回答 SAME

只回答一行。
```

### `prompts/topic_match_dormant.md`

```markdown
以下是休眠中的对话话题：
{dormant_list}

新消息：{new_message}

这条消息是否在回到某个休眠话题？回答对应编号（如 1），或 NONE。
只回答一行。
```

### `prompts/topic_summarize.md`

```markdown
以下是一段对话：
{conversation_text}

用10个字以内概括这段对话的核心话题。同时提取3-5个关键词（JSON数组）。

格式：
话题|["关键词1","关键词2","关键词3"]
```

### `prompts/session_condense.md`

```markdown
以下是一段对话：
{conversation}

用100-200字概括这段对话的核心内容，包括：
- 讨论了什么
- 得出了什么结论或决定
- 有没有未完成的事项

直接写摘要，不要加标题或格式标记。
```

### `prompts/session_memory_flush.md`（新增）

```markdown
以下是一段即将归档的对话。请审视这段对话，判断是否有值得长期记住的信息。

{conversation}

如果有值得记住的内容，使用 memory_note 工具写入。值得记住的信息包括：
- Kevin 提到的重要决定、偏好、计划
- 你们约定的事情或承诺
- Kevin 分享的个人经历或感受
- 有价值的技术讨论结论
- 未完成的事项或待跟进的话题

如果这段对话是日常闲聊、简单查询，没有什么特别值得记录的，就不需要写任何东西。

不要向用户发送任何消息。
```

---

## 十一、实施阶段

### Phase 1：基础骨架（可独立部署验证）

**目标**：Session 创建/切换能跑起来，仅用时间间隔检测，仅 Active ↔ Dormant ↔ Deleted 三级。

1. 新增 `src/core/session_manager.py`（SessionManager 类，不含 condense 逻辑）
2. 新增 `sessions` 表，`conversations` 表加 `session_id` 列（migration）
3. `conversation.py` 新增 session-aware 方法
4. `brain.py` 集成 SessionManager（`_prepare_think` + `think` + `think_conversational`）
5. `config/settings.py` 新增配置项
6. `SESSION_TOPIC_DETECT_ENABLED=false`（Phase 1 不启用 LLM 检测）
7. Heartbeat 新增 `SessionReaperAction`（Phase 1 仅处理 dormant → deleted）

**验证方式**：
- 连续聊天 → 同一个 session
- 沉默 30 分钟后发消息 → 新 session，旧 session dormant
- 3 小时后旧 session 被清理（直接 deleted）
- `/clear` 仍然正常工作

### Phase 2：LLM 话题检测 + Dormant 匹配

**目标**：Lapwing 在连续对话中也能识别话题切换，并能切回旧话题。

1. 新增 `prompts/topic_detect.md`、`topic_match_dormant.md`、`topic_summarize.md`
2. SessionManager 实现 Layer 2 + Layer 3 逻辑
3. `SESSION_TOPIC_DETECT_ENABLED=true`
4. Dormant session 降级时自动生成 topic_summary

**验证方式**：
- 聊论文 → 突然说"道奇今天打谁" → 自动新 session
- 回头说"对了那个数据集" → 匹配回论文 session，上下文恢复
- 查天气这种极短对话 → 不保留 dormant（< 4 条消息）

### Phase 3：Condensed 层 + 快照归档

**目标**：Dormant 不直接删除，先压缩归档；通过快照文件实现低成本长时间保留 + 完整复活。

1. 新增 `prompts/session_condense.md`
2. SessionManager 实现 `condense()` / `_write_snapshot()` / `_read_snapshot()`
3. `reactivate()` 支持从 condensed 恢复
4. `reap_expired()` 实现两级扫描（dormant → condensed，condensed → deleted）
5. Layer 3 匹配范围扩展到 condensed sessions
6. `SESSION_CONDENSED_TTL_HOURS` 配置项生效

**验证方式**：
- Dormant 3 小时后变 condensed，快照文件生成
- Condense 之前触发 memory flush，Lapwing 写入记忆文件
- 聊到相关话题 → condensed 被匹配 → 从快照完整恢复
- Condensed 24 小时后删除，快照文件清理
- 总量超过 5 个时，最老的被正确处理

### Phase 4：优化与可观测（随 sqlite-vec 迁移）

1. Embedding 替代 LLM 做 dormant/condensed 匹配
2. Session 统计写入 Lapwing 自省（"今天和 Kevin 聊了 5 个话题"）
3. 桌面应用 session 可视化（话题切换指示器）

---

## 十二、风险与降级策略

| 风险 | 降级方案 |
|------|----------|
| LLM 话题检测误判（SAME 判成 NEW） | 保守策略：不确定时默认 SAME；误创建的短 session 会被 MIN_MESSAGES 机制自动清理 |
| LLM 话题检测误判（NEW 判成 SAME） | 影响较小：只是上下文多了一些无关消息，不如当前"全部混在一起"糟糕 |
| Session 系统 bug 导致消息丢失 | `SESSION_ENABLED=false` 一键回退到老路径；所有 fallback 逻辑保留 |
| API 成本增加（每条消息多一次 LLM call） | Layer 1 时间间隔拦截大部分；Layer 2 用最便宜模型 + 极低 max_tokens |
| Dormant + Condensed 太多占资源 | `SESSION_MAX_DORMANT_PER_CHAT=5` 硬上限；condensed 仅占磁盘不占内存 |
| 快照文件解析失败 | 复活时 fallback 到空消息列表；DB 中的原始消息仍在，理论上可从 DB 重建 |
| 快照文件被手动修改 | 解析器做防御性处理（跳过无法识别的行）；不影响 DB 完整性 |

---

## 十三、测试要点

### 单元测试（新增文件：`tests/core/test_session_manager.py`）

1. `test_create_first_session` — 没有 active 时创建
2. `test_timeout_creates_new` — 超时自动新建
3. `test_deactivate_moves_to_dormant` — 降级正确
4. `test_short_session_deleted_not_dormant` — < MIN_MESSAGES 直接删
5. `test_reactivate_from_dormant` — 从 dormant 复活，内存中上下文完整
6. `test_condense_writes_snapshot` — 压缩写快照文件，格式正确
7. `test_condense_clears_memory` — 压缩后内存缓存被清除
8. `test_condense_memory_flush` — 压缩前触发 memory flush，Lapwing 通过 memory_note 写入记忆
9. `test_condense_memory_flush_failure_non_blocking` — memory flush 失败不阻塞 condense 流程
10. `test_reactivate_from_condensed` — 从 condensed 复活，快照解析正确，上下文完整恢复
9. `test_reactivate_condensed_deletes_snapshot` — 复活后快照文件被删除
10. `test_snapshot_parse_robustness` — 快照格式异常时不崩溃
11. `test_max_dormant_eviction` — 超过上限时最老的被正确降级/删除
12. `test_reap_expired_two_pass` — dormant → condensed 和 condensed → deleted 两级清理
13. `test_session_disabled_fallback` — SESSION_ENABLED=false 走老路径
14. `test_topic_detect_same` — LLM 返回 SAME
15. `test_topic_detect_new` — LLM 返回 NEW
16. `test_dormant_match_found` — 匹配到 dormant 话题
17. `test_condensed_match_found` — 匹配到 condensed 话题
18. `test_match_none` — 无匹配，创建新 session

### 集成测试

1. 完整四级流程：active → dormant → condensed → deleted
2. Condensed 复活后继续对话，再次降级时重新生成快照
3. Heartbeat reaper 正确触发两级扫描
4. Compactor 限制在 session 内
5. Proactive 消息写入正确 session
6. `/clear` 命令行为
7. QQ adapter 与 session 的交互

---

## 十四、文件清单

| 操作 | 文件 |
|------|------|
| 新增 | `src/core/session_manager.py` |
| 新增 | `src/heartbeat/actions/session_reaper.py` |
| 新增 | `prompts/topic_detect.md` |
| 新增 | `prompts/topic_match_dormant.md` |
| 新增 | `prompts/topic_summarize.md` |
| 新增 | `prompts/session_condense.md` |
| 新增 | `prompts/session_memory_flush.md` |
| 新增 | `tests/core/test_session_manager.py` |
| 新增 | `data/memory/sessions/`（目录，运行时创建） |
| 修改 | `src/memory/conversation.py` — 新增 session-aware 方法 + 缓存结构 |
| 修改 | `src/core/brain.py` — `_ThinkCtx`, `_prepare_think`, `think`, `think_conversational` |
| 修改 | `src/core/dispatcher.py` — history 来源 |
| 修改 | `src/memory/compactor.py` — session 感知 |
| 修改 | `src/heartbeat/actions/proactive.py` — 写入 active session |
| 修改 | `src/app/telegram_app.py` — `/clear` 行为 |
| 修改 | `config/settings.py` — 新增配置项 |
| 修改 | `main.py` — 初始化 SessionManager |