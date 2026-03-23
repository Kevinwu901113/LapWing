# Lapwing 下一阶段开发规格文档

> 本文档供 AI 编码 Agent（Codex）接手实现。每个任务均可独立执行，按顺序排列。

---

## 项目快照

**Lapwing** 是运行在 Ubuntu 22.04 PVE 虚拟机上的个人 AI 伴侣，通过 Telegram 交互。

### 技术栈
- Python 3.11+，全异步（async/await）
- Telegram 接入：`python-telegram-bot>=21.0`
- LLM：OpenAI 兼容格式（`openai>=1.50.0`）+ Anthropic SDK（`anthropic>=0.57.0`）
- 数据库：SQLite（`aiosqlite>=0.20.0`）
- 调度：APScheduler
- 搜索：`duckduckgo-search>=7.0`

### 关键约定
- 所有 Prompt 放在 `prompts/` 目录，以 `.md` 格式存储，通过 `src/core/prompt_loader.load_prompt(name)` 加载
- 不把 prompt 内容硬编码在 Python 代码中
- 环境变量在 `config/settings.py` 统一读取，通过 `os.getenv()` 加载
- 新增 Agent 放在 `src/agents/`，必须继承 `BaseAgent`（`src/agents/base.py`）
- 新增工具放在 `src/tools/`，提供模块级异步函数
- 中文注释，英文代码，完整 type hints
- 日志：`logging.getLogger("lapwing.模块名")`
- 测试：pytest + pytest-asyncio，放在 `tests/` 对应子目录

### 当前已完成
- Phase 1.5 全部完成（SQLite 持久化、多模型路由、用户画像提取、心跳引擎）
- Phase 2 全部完成（Agent 框架、ResearcherAgent、CoderAgent）
- 测试：145 个，全部通过

---

## 数据库 Schema（当前）

```sql
-- 对话历史
CREATE TABLE conversations (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id   TEXT NOT NULL,
    role      TEXT NOT NULL,   -- "user" | "assistant"
    content   TEXT NOT NULL,
    timestamp TEXT NOT NULL    -- ISO 8601
);

-- 用户长期记忆
CREATE TABLE user_facts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    TEXT NOT NULL,
    fact_key   TEXT NOT NULL,
    fact_value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(chat_id, fact_key)
);

-- 发现（搜索结果、兴趣内容，供主动分享）
CREATE TABLE discoveries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id       TEXT NOT NULL,
    source        TEXT NOT NULL,   -- "web_search" | "browsing" 等
    title         TEXT NOT NULL,
    summary       TEXT NOT NULL,
    url           TEXT,
    discovered_at TEXT NOT NULL,
    shared_at     TEXT            -- NULL = 未分享
);
```

---

## 关键接口速查

```python
# LLM 调用（src/core/llm_router.LLMRouter）
await router.complete(messages: list[dict], purpose: str = "chat", max_tokens: int = 1024) -> str
# purpose: "chat" | "tool" | "heartbeat"

# 记忆操作（src/memory/conversation.ConversationMemory）
await memory.get(chat_id: str) -> list[dict]
await memory.append(chat_id: str, role: str, content: str) -> None
await memory.get_user_facts(chat_id: str) -> list[dict]  # [{"fact_key", "fact_value", "updated_at"}]
await memory.set_user_fact(chat_id: str, fact_key: str, fact_value: str) -> None  # UPSERT
await memory.add_discovery(chat_id, source, title, summary, url) -> None
await memory.get_unshared_discoveries(chat_id: str, limit: int = 5) -> list[dict]
await memory.mark_discovery_shared(discovery_id: int) -> None
await memory.get_all_chat_ids() -> list[str]

# Prompt 加载（src/core/prompt_loader）
load_prompt(name: str) -> str  # 从 prompts/{name}.md 读取，带缓存

# Agent 框架（src/agents/base）
@dataclass AgentTask: chat_id, user_message, history, user_facts
@dataclass AgentResult: content, needs_persona_formatting=True, metadata={}
class BaseAgent(ABC): name, description, capabilities, execute(task, router) -> AgentResult

# 心跳动作框架（src/core/heartbeat）
@dataclass SenseContext: beat_type, now, last_interaction, silence_hours,
                         user_facts_summary, recent_memory_summary, chat_id
class HeartbeatAction(ABC): name, description, beat_types, execute(ctx, brain, bot)
```

---

## 任务列表

### 任务 A：网页内容抓取工具

**文件**：`src/tools/web_fetcher.py`
**前置**：无
**依赖新增**：`httpx>=0.27`（requirements.txt）

#### 目标
给 ResearcherAgent 和后续 BrowserAgent 提供「抓取指定 URL 并提取正文」的能力。

#### 实现要求

```python
# src/tools/web_fetcher.py

from dataclasses import dataclass

@dataclass
class FetchResult:
    url: str
    title: str        # <title> 标签内容，失败时为空字符串
    text: str         # 提取的正文纯文本，截断到 _MAX_TEXT 字符
    success: bool
    error: str        # 失败原因，成功时为空字符串

_MAX_TEXT = 4000      # 正文截断上限（字符数）
_TIMEOUT = 10         # 请求超时秒数

async def fetch(url: str) -> FetchResult:
    """抓取指定 URL，返回标题和正文纯文本。

    - 使用 httpx.AsyncClient，timeout=_TIMEOUT
    - User-Agent 设为 "Lapwing/1.0 (personal assistant)"
    - 仅处理 text/html 响应，其他 Content-Type 返回 success=False
    - 正文提取：用 Python stdlib html.parser（不引入 BeautifulSoup）
      - 去除 <script>、<style>、<nav>、<header>、<footer> 标签及其内容
      - 提取剩余文本，合并连续空白
      - 截断到 _MAX_TEXT 字符
    - 任何异常（连接失败、超时、非 HTML）均返回 success=False + error 描述
    - 日志：logger = logging.getLogger("lapwing.tools.web_fetcher")
    """
```

#### 测试要求
文件：`tests/tools/test_web_fetcher.py`

- `test_fetch_success`：mock httpx 返回一个简单 HTML，验证 title 和 text 正确提取
- `test_fetch_strips_script_and_style`：验证 `<script>` 和 `<style>` 内容被去除
- `test_fetch_timeout`：mock httpx 抛出 `httpx.TimeoutException`，验证 success=False
- `test_fetch_non_html`：响应 Content-Type 为 application/pdf，验证 success=False
- `test_fetch_truncates_long_text`：超过 4000 字符的正文被截断
- `test_fetch_connection_error`：mock httpx 抛出 `httpx.ConnectError`，验证 success=False

---

### 任务 B：BrowserAgent（自主浏览）

**文件**：`src/agents/browser.py`，`prompts/browser_analyze.md`
**前置**：任务 A（web_fetcher）
**注册**：在 `main.py` 的 `post_init` 中注册

#### 目标
让 Lapwing 能直接访问用户提到的 URL，阅读页面内容并总结。

#### Agent 定义

```python
# src/agents/browser.py
class BrowserAgent(BaseAgent):
    name = "browser"
    description = "访问指定网页，阅读并总结页面内容"
    capabilities = ["浏览指定网址", "阅读网页文章", "提取网页信息"]

    def __init__(self, memory) -> None: ...
```

#### execute 流程

1. 从用户消息中提取 URL（正则 `https?://[^\s]+`），取第一个有效 URL
2. 如果没有 URL，返回 `AgentResult(content="请提供一个网址", needs_persona_formatting=True)`
3. 调用 `web_fetcher.fetch(url)`
4. 如果 `fetch_result.success is False`，返回友好错误提示（needs_persona_formatting=True）
5. 将页面标题 + 正文 + 用户原始问题发给 LLM（`browser_analyze.md`，purpose="tool"，max_tokens=1024）
6. 调用 `memory.add_discovery(chat_id, source="browsing", title=fetch_result.title, summary=summary[:500], url=url)`
7. 返回 `AgentResult(content=summary, needs_persona_formatting=True, metadata={"url": url, "title": fetch_result.title})`

#### Prompt：`prompts/browser_analyze.md`

```
你是一个网页内容分析助手。根据网页内容，回答用户的问题或提供摘要。

要求：
- 直接回答用户问题，无法回答时提供内容摘要
- 内容客观，不要添加主观评价
- 在末尾注明来源：[{title}]({url})

用户问题：{user_message}

网页标题：{title}

网页内容：
{page_text}
```

#### main.py 修改

在 `post_init` 中，在 CoderAgent 注册后添加：
```python
from src.agents.browser import BrowserAgent
agent_registry.register(BrowserAgent(memory=brain.memory))
```
日志行改为：`"Agent dispatcher initialized with: researcher, coder, browser"`

#### 测试要求
文件：`tests/agents/test_browser.py`

- `test_extracts_url_and_fetches`：mock fetch 返回成功，验证调用了 fetch(url) 且返回摘要
- `test_no_url_in_message_returns_hint`：用户消息无 URL，返回提示
- `test_fetch_failure_returns_error`：fetch 返回 success=False，返回友好错误
- `test_saves_discovery`：验证 memory.add_discovery 被调用，参数正确
- `test_uses_tool_purpose`：验证 router.complete 用 purpose="tool"
- `test_llm_failure_returns_graceful_error`：router.complete 抛异常，不崩溃

---

### 任务 C：兴趣图谱

**文件**：`src/memory/interest_tracker.py`，需新增数据库表
**前置**：无（独立任务）

#### 目标
从对话中自动提取用户感兴趣的话题，建立兴趣权重图谱，供主动消息和心跳决策使用。

#### 数据库新增表

在 `src/memory/conversation.py` 的 `_create_tables()` 方法中添加：

```sql
CREATE TABLE IF NOT EXISTS interest_topics (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    TEXT NOT NULL,
    topic      TEXT NOT NULL,
    weight     REAL NOT NULL DEFAULT 1.0,   -- 累计权重，每次提及 +1
    last_seen  TEXT NOT NULL,               -- ISO 8601，最近一次提及
    UNIQUE(chat_id, topic)
);
CREATE INDEX IF NOT EXISTS idx_interest_topics_chat_id ON interest_topics(chat_id);
```

#### ConversationMemory 新增方法

在 `src/memory/conversation.py` 中添加：

```python
async def bump_interest(self, chat_id: str, topic: str, increment: float = 1.0) -> None:
    """增加话题权重（UPSERT：首次插入，已有则累加）。"""

async def get_top_interests(self, chat_id: str, limit: int = 10) -> list[dict]:
    """按权重降序返回兴趣话题。
    Returns: [{"topic": str, "weight": float, "last_seen": str}, ...]
    """

async def decay_interests(self, chat_id: str, factor: float = 0.95) -> None:
    """对所有话题权重乘以 factor（衰减），保持兴趣的时效性。"""
```

#### InterestTracker 类

文件：`src/memory/interest_tracker.py`

```python
class InterestTracker:
    """从对话中提取兴趣话题并更新权重图谱。"""

    def __init__(self, memory: ConversationMemory, router) -> None:
        self._memory = memory
        self._router = router

    def notify(self, chat_id: str) -> None:
        """每次对话后调用，累积轮次，到阈值时触发提取。
        阈值：INTEREST_EXTRACT_TURN_THRESHOLD（默认 5 轮，从 settings 读取）。
        触发时 fire-and-forget：asyncio.create_task(self._extract(chat_id))
        """

    async def _extract(self, chat_id: str) -> None:
        """提取近期对话中的兴趣话题，调用 LLM（interest_extract.md，purpose="tool"）。
        LLM 返回 JSON 数组：[{"topic": str, "weight": float}]
        topic: 简短关键词（不超过 10 字），weight: 0.5~2.0
        对每个结果调用 memory.bump_interest(chat_id, topic, weight)
        """

    @staticmethod
    def _parse_result(text: str) -> list[dict]:
        """防御性 JSON 解析，处理 markdown 代码块包裹，失败返回 []。"""

    async def shutdown(self) -> None:
        """取消所有 pending 异步任务。"""
```

#### Prompt：`prompts/interest_extract.md`

```
你是一个兴趣分析助手。从对话中提取用户感兴趣的话题。

规则：
- 只提取真实体现用户兴趣的主题（不是随口一提）
- 话题要简洁（≤10字），如"Python编程"、"机器学习"、"摄影"
- weight 反映兴趣强度：深度探讨=2.0，一般提及=1.0，路过提到=0.5
- 最多提取 5 个话题
- 如果没有明显兴趣话题，返回空数组
- 返回严格 JSON 数组，不要任何解释

对话内容：
{conversation}
```

#### settings.py 新增配置

```python
INTEREST_EXTRACT_TURN_THRESHOLD: int = int(os.getenv("INTEREST_EXTRACT_TURN_THRESHOLD", "5"))
```

#### .env.example 新增

```
# 兴趣图谱（可选）
INTEREST_EXTRACT_TURN_THRESHOLD=5   # 对话满多少轮触发兴趣提取，默认 5 轮
```

#### brain.py 修改

1. 在 `LapwingBrain.__init__` 中添加：`self.interest_tracker: InterestTracker | None = None`
2. `think()` 方法中，在 `self.fact_extractor.notify(chat_id)` 后添加：
   ```python
   if self.interest_tracker is not None:
       self.interest_tracker.notify(chat_id)
   ```

#### main.py 修改

在 `post_init` 中，`await brain.init_db()` 之后添加：
```python
from src.memory.interest_tracker import InterestTracker
brain.interest_tracker = InterestTracker(memory=brain.memory, router=brain.router)
```

在 `post_shutdown` 中添加：
```python
if brain.interest_tracker:
    await brain.interest_tracker.shutdown()
```

#### 测试要求
文件：`tests/memory/test_interest_tracker.py`，`tests/memory/test_conversation_interests.py`

**test_conversation_interests.py**（SQLite 集成测试）：
- `test_bump_interest_inserts_new`：新话题插入后权重正确
- `test_bump_interest_accumulates`：同话题多次 bump，权重累加
- `test_get_top_interests_sorted_by_weight`：按权重降序返回
- `test_get_top_interests_limit_respected`：limit 参数有效
- `test_decay_multiplies_all_weights`：decay 后所有权重乘以 factor
- `test_interests_isolated_by_chat_id`：不同 chat_id 数据隔离

**test_interest_tracker.py**（单元测试，mock）：
- `test_notify_triggers_extraction_at_threshold`
- `test_notify_does_not_trigger_before_threshold`
- `test_extract_calls_bump_for_each_topic`
- `test_parse_result_valid_json`
- `test_parse_result_strips_markdown_fence`
- `test_parse_result_invalid_json_returns_empty`
- `test_shutdown_cancels_pending_tasks`

---

### 任务 D：增强主动分享（兴趣驱动的心跳消息）

**文件**：`src/heartbeat/actions/interest_proactive.py`，`prompts/heartbeat_interest_proactive.md`
**前置**：任务 C（InterestTracker + interest_topics 表）

#### 目标
在现有 `ProactiveMessageAction`（基于沉默时间触发）的基础上，新增一种主动消息：基于用户兴趣图谱，用 ResearcherAgent 能力搜索相关内容并主动分享。

#### HeartbeatAction 定义

```python
# src/heartbeat/actions/interest_proactive.py
class InterestProactiveAction(HeartbeatAction):
    """基于用户兴趣主动搜索并分享相关内容。"""
    name = "interest_proactive"
    description = "基于用户兴趣图谱，搜索并主动分享相关内容"
    beat_types = ["fast"]
```

#### execute 流程

1. 获取用户兴趣：`top_interests = await brain.memory.get_top_interests(ctx.chat_id, limit=3)`
2. 如果 `top_interests` 为空，直接 return（无事可做）
3. 从兴趣中选权重最高的 topic 作为搜索词
4. 调用 `web_search.search(topic, max_results=3)`
5. 如果搜索无结果，直接 return
6. 用 LLM（`heartbeat_interest_proactive.md`，purpose="heartbeat"，max_tokens=300）生成主动消息
   - prompt 变量：`{topic}`, `{search_results}`, `{user_facts_summary}`
7. 发送消息：`await bot.send_message(chat_id=ctx.chat_id, text=message)`
8. 将搜索结果存为 discovery：`await brain.memory.add_discovery(..., source="interest_search")`
9. 将回复存入对话历史：`await brain.memory.append(ctx.chat_id, "assistant", message)`
10. 衰减兴趣权重（已分享的话题略降低权重，避免反复推送同一话题）：
    `await brain.memory.decay_interests(ctx.chat_id, factor=0.9)`

#### 触发条件（在 execute 开始时检查）
- 用户沉默时间 `ctx.silence_hours >= 2.0`（不打扰刚聊完的用户）
- 当前小时不在 23:00–07:00 之间

#### Prompt：`prompts/heartbeat_interest_proactive.md`

```
你是 Lapwing，用户的私人 AI 伴侣。你注意到用户对「{topic}」感兴趣，刚好发现了一些相关内容，想主动分享给他们。

语气要求：
- 自然随意，像朋友分享链接一样，不要过于正式
- 简短（3~5句话），点到为止
- 不需要问"你想了解更多吗"之类的问句
- 说话自然不做作，偶尔带一点书卷气

用户信息：{user_facts_summary}

搜索到的相关内容：
{search_results}
```

#### main.py 修改

在心跳引擎注册 action 处添加：
```python
from src.heartbeat.actions.interest_proactive import InterestProactiveAction
heartbeat.registry.register(InterestProactiveAction())
```

#### 测试要求
文件：`tests/heartbeat/actions/test_interest_proactive.py`

- `test_skips_when_no_interests`：top_interests 为空，不发消息
- `test_skips_when_search_empty`：搜索无结果，不发消息
- `test_sends_message_with_topic`：正常路径，bot.send_message 被调用
- `test_saves_discovery`：verify memory.add_discovery called with source="interest_search"
- `test_appends_to_memory`：verify memory.append called
- `test_decays_interests_after_share`：verify memory.decay_interests called
- `test_skips_during_quiet_hours`：ctx.now 在 23:00，不发消息
- `test_skips_when_silence_too_short`：silence_hours < 2.0，不发消息
- `test_uses_heartbeat_purpose`：router.complete 用 purpose="heartbeat"

---

## 执行顺序建议

```
任务 A（web_fetcher）→ 任务 B（BrowserAgent）
任务 C（InterestTracker）→ 任务 D（InterestProactiveAction）
```

任务 A+B 和任务 C+D 可并行开发。

---

## 验证方式

每个任务完成后运行：
```bash
source venv/bin/activate
pytest -v
```
确保全量测试通过（当前基线：145 个测试）。

手动验证：
- **任务 B**：发送包含 URL 的消息（如"帮我看看这个 https://example.com"），确认 Lapwing 返回页面摘要
- **任务 C**：聊 5 轮关于某话题 → 查 SQLite：`SELECT * FROM interest_topics;`
- **任务 D**：等心跳触发（设置 `HEARTBEAT_FAST_INTERVAL_MINUTES=3`）→ 确认收到基于兴趣的消息
