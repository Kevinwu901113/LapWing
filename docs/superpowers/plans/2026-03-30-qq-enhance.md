# QQ 真人体验增强 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Lapwing's QQ interactions feel human — emoji support, group chat participation with two-tier filtering, mark-as-read, reply quoting, and poke.

**Architecture:** All enhancements live in the QQ adapter layer and Brain prompt layer. No changes to Agent Team, Memory, or Heartbeat core logic. Group chat uses a two-tier filter: tier-1 is rule-based (zero LLM cost), tier-2 is a lightweight Brain decision (~500 tokens). New files: `qq_group_context.py` for group state, `qq_group_filter.py` for tier-1 rules, `group_engage_decision.md` for tier-2 prompt.

**Tech Stack:** Python 3.11+, websockets, existing LLM router (purpose="tool" for group decisions)

---

## File Structure

### New Files
- `src/adapters/qq_group_context.py` — `GroupMessage` and `GroupContext` dataclasses for per-group message buffer
- `src/adapters/qq_group_filter.py` — `GroupMessageFilter` tier-1 rule-based filter
- `prompts/group_engage_decision.md` — tier-2 Brain prompt for group engagement decision

### Modified Files
- `src/adapters/qq_adapter.py` — QQ face map, face-aware `_build_message_segments`, face-aware `_extract_text`, mark-as-read, reply, poke, group message handling, group send APIs
- `config/settings.py` — new `QQ_GROUP_*` settings
- `config/.env.example` — new group config entries
- `main.py:166-204` — expanded QQ config with group settings, inject router

### Unchanged Files
- `src/core/brain.py` — no modifications (Brain is called via `router.chat()` from adapter for group decisions)
- `src/adapters/base.py` — no changes needed
- `src/core/channel_manager.py` — no changes needed

---

## Task 1: QQ Face Emoji — Send Support

Add face ID map and face-aware message segment builder to `qq_adapter.py`.

**Files:**
- Modify: `src/adapters/qq_adapter.py:1-20` (imports, constants) and `src/adapters/qq_adapter.py:201-207` (`_build_message_segments`)

- [ ] **Step 1: Add QQ_FACE_MAP constant**

Add after line 19 (`MAX_QQ_MSG_LENGTH = 4000`):

```python
# QQ 表情 ID 映射（常用子集）
QQ_FACE_MAP: dict[str, str] = {
    "[微笑]": "14", "[撇嘴]": "1", "[色]": "2", "[发呆]": "3",
    "[得意]": "4", "[流泪]": "5", "[害羞]": "6", "[闭嘴]": "7",
    "[大哭]": "9", "[尴尬]": "10", "[发怒]": "11", "[调皮]": "12",
    "[呲牙]": "13", "[惊讶]": "0", "[难过]": "15", "[酷]": "16",
    "[抓狂]": "18", "[吐]": "19", "[偷笑]": "20", "[可爱]": "21",
    "[白眼]": "22", "[傲慢]": "23", "[饥饿]": "24", "[困]": "25",
    "[惊恐]": "26", "[流汗]": "27", "[憨笑]": "28", "[悠闲]": "29",
    "[奋斗]": "30", "[咒骂]": "31", "[疑问]": "32", "[嘘]": "33",
    "[晕]": "34", "[敲打]": "35", "[再见]": "36", "[抠鼻]": "53",
    "[鼓掌]": "47", "[坏笑]": "50", "[右哼哼]": "52",
    "[鄙视]": "49", "[委屈]": "55", "[亲亲]": "57",
    "[可怜]": "58", "[笑哭]": "182", "[doge]": "179",
    "[OK]": "324", "[爱心]": "66", "[心碎]": "67",
    "[拥抱]": "49", "[强]": "76", "[弱]": "77",
    "[握手]": "78", "[胜利]": "79",
}
```

- [ ] **Step 2: Rewrite `_build_message_segments` to parse face tags**

Replace the existing `_build_message_segments` method:

```python
def _build_message_segments(self, text: str, image_base64: str | None = None) -> list:
    segments: list[dict] = []
    if text:
        parts = re.split(r'(\[[^\[\]]+\])', text)
        for part in parts:
            if not part:
                continue
            face_id = QQ_FACE_MAP.get(part)
            if face_id is not None:
                segments.append({"type": "face", "data": {"id": face_id}})
            else:
                segments.append({"type": "text", "data": {"text": part}})
    if image_base64:
        segments.append({"type": "image", "data": {"file": f"base64://{image_base64}"}})
    return segments
```

- [ ] **Step 3: Verify no regressions in send_text path**

Manually trace: `send_text` → `_send_private_msg` → `_build_message_segments`. Plain text without `[brackets]` should produce identical segments as before (single text segment). Text with `[微笑]` should produce `[text, face, text]`.

- [ ] **Step 4: Commit**

```bash
git add src/adapters/qq_adapter.py
git commit -m "feat(qq): add QQ face emoji send support with face ID map"
```

---

## Task 2: QQ Face Emoji — Receive Support

Parse incoming face segments into `[表情名]` text so Brain can understand them.

**Files:**
- Modify: `src/adapters/qq_adapter.py:168-178` (`_extract_text`) and add reverse map

- [ ] **Step 1: Add reverse face map and lookup method**

Add as a class attribute and method to `QQAdapter`:

```python
_FACE_ID_TO_NAME: dict[str, str] | None = None

@classmethod
def _face_id_to_name(cls, face_id: str) -> str:
    if cls._FACE_ID_TO_NAME is None:
        cls._FACE_ID_TO_NAME = {v: k for k, v in QQ_FACE_MAP.items()}
    return cls._FACE_ID_TO_NAME.get(face_id, "")
```

- [ ] **Step 2: Update `_extract_text` to handle face segments**

Replace the existing `_extract_text`:

```python
def _extract_text(self, event: dict) -> str:
    message = event.get("message", "")
    if isinstance(message, str):
        return message.strip()
    if isinstance(message, list):
        parts: list[str] = []
        for seg in message:
            seg_type = seg.get("type")
            data = seg.get("data", {})
            if seg_type == "text":
                parts.append(data.get("text", ""))
            elif seg_type == "face":
                face_name = self._face_id_to_name(str(data.get("id", "")))
                if face_name:
                    parts.append(face_name)
        return "".join(parts).strip()
    return str(message).strip()
```

- [ ] **Step 3: Commit**

```bash
git add src/adapters/qq_adapter.py
git commit -m "feat(qq): parse incoming QQ face segments into [表情名] text"
```

---

## Task 3: Mark As Read

Auto-mark private messages as read for natural conversation feel.

**Files:**
- Modify: `src/adapters/qq_adapter.py` — add `_mark_as_read` method and call it in `_handle_message_event`

- [ ] **Step 1: Add `_mark_as_read` method**

Add after the `_handle_message_event` method:

```python
async def _mark_as_read(self, user_id: str) -> None:
    try:
        await self._call_api("mark_private_msg_as_read", {
            "user_id": int(user_id),
        })
    except Exception:
        pass  # Non-critical, failure doesn't affect main flow
```

- [ ] **Step 2: Call `_mark_as_read` in `_handle_message_event`**

In `_handle_message_event`, after the Kevin-only filter (line 148-149), before `text = self._extract_text(event)`, add:

```python
asyncio.create_task(self._mark_as_read(user_id))
```

- [ ] **Step 3: Commit**

```bash
git add src/adapters/qq_adapter.py
git commit -m "feat(qq): auto mark private messages as read"
```

---

## Task 4: Message Reply (Quoting)

Support sending replies with quote reference to a specific message.

**Files:**
- Modify: `src/adapters/qq_adapter.py` — add `send_reply` and `_send_private_msg_segments` methods

- [ ] **Step 1: Add `_send_private_msg_segments` helper**

```python
async def _send_private_msg_segments(self, user_id: str, segments: list) -> dict:
    try:
        numeric_id = int(user_id)
    except ValueError:
        return {"status": "failed", "retcode": -3}
    return await self._call_api("send_private_msg", {
        "user_id": numeric_id,
        "message": segments,
    })
```

- [ ] **Step 2: Add `send_reply` method**

```python
async def send_reply(self, chat_id: str, text: str, reply_to_message_id: str) -> None:
    text_plain = self._markdown_to_plain(text)
    segments: list[dict] = [{"type": "reply", "data": {"id": reply_to_message_id}}]
    segments.extend(self._build_message_segments(text_plain))
    await self._send_private_msg_segments(chat_id, segments)
```

- [ ] **Step 3: Add `poke` method**

```python
async def poke(self, user_id: str) -> None:
    """Friend poke (戳一戳)."""
    try:
        await self._call_api("friend_poke", {"user_id": int(user_id)})
    except Exception:
        pass
```

- [ ] **Step 4: Commit**

```bash
git add src/adapters/qq_adapter.py
git commit -m "feat(qq): add reply quoting and poke support"
```

---

## Task 5: Group Chat Config

Add group chat configuration to settings and .env.example.

**Files:**
- Modify: `config/settings.py:92-96` (after QQ settings)
- Modify: `config/.env.example:14-18` (after QQ settings)

- [ ] **Step 1: Add group settings to `config/settings.py`**

Add after `QQ_KEVIN_ID` (line 96):

```python
QQ_GROUP_IDS: list[str] = [
    g.strip() for g in os.getenv("QQ_GROUP_IDS", "").split(",") if g.strip()
]
QQ_GROUP_CONTEXT_SIZE: int = int(os.getenv("QQ_GROUP_CONTEXT_SIZE", "30"))
QQ_GROUP_COOLDOWN: int = int(os.getenv("QQ_GROUP_COOLDOWN", "60"))
QQ_GROUP_INTEREST_KEYWORDS: list[str] = [
    k.strip() for k in os.getenv("QQ_GROUP_INTEREST_KEYWORDS", "").split(",") if k.strip()
]
```

- [ ] **Step 2: Add group settings to `.env.example`**

Add after `QQ_KEVIN_ID=`:

```env
QQ_GROUP_IDS=               # Comma-separated group IDs Lapwing may participate in
QQ_GROUP_CONTEXT_SIZE=30    # Number of recent messages to keep per group
QQ_GROUP_COOLDOWN=60        # Minimum seconds between group replies
QQ_GROUP_INTEREST_KEYWORDS= # Comma-separated interest keywords (optional)
```

- [ ] **Step 3: Commit**

```bash
git add config/settings.py config/.env.example
git commit -m "feat(qq): add group chat configuration settings"
```

---

## Task 6: GroupContext Data Structure

Create the per-group message buffer and context state.

**Files:**
- Create: `src/adapters/qq_group_context.py`

- [ ] **Step 1: Create `qq_group_context.py`**

```python
"""QQ 群聊上下文缓冲区。"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class GroupMessage:
    """群聊消息记录。"""

    message_id: str
    user_id: str
    nickname: str
    text: str
    timestamp: float
    is_at_self: bool = False
    is_reply_to_self: bool = False
    replied_by_self: bool = False


@dataclass
class GroupContext:
    """单个群的上下文状态。"""

    group_id: str
    buffer: deque[GroupMessage] = field(default_factory=lambda: deque(maxlen=50))
    last_reply_time: float = 0.0
    my_recent_message_ids: list[str] = field(default_factory=list)

    def add_message(self, msg: GroupMessage) -> None:
        self.buffer.append(msg)

    def recent_messages(self, n: int = 30) -> list[GroupMessage]:
        return list(self.buffer)[-n:]

    def format_for_prompt(self, n: int = 30) -> str:
        """Format recent group chat for Brain prompt."""
        messages = self.recent_messages(n)
        lines: list[str] = []
        for msg in messages:
            prefix = "(我回复过) " if msg.replied_by_self else ""
            lines.append(f"{msg.nickname}: {prefix}{msg.text}")
        return "\n".join(lines)

    def seconds_since_last_reply(self) -> float:
        if self.last_reply_time == 0:
            return float("inf")
        return time.time() - self.last_reply_time

    def record_my_message(self, message_id: str) -> None:
        """Track a message ID sent by Lapwing (for reply-to-self detection)."""
        self.my_recent_message_ids.append(message_id)
        # Keep only the last 20 IDs
        if len(self.my_recent_message_ids) > 20:
            self.my_recent_message_ids = self.my_recent_message_ids[-20:]
```

- [ ] **Step 2: Commit**

```bash
git add src/adapters/qq_group_context.py
git commit -m "feat(qq): add GroupMessage and GroupContext data structures"
```

---

## Task 7: GroupMessageFilter (Tier-1 Rules)

Create the lightweight rule-based filter that decides which group messages warrant Brain evaluation.

**Files:**
- Create: `src/adapters/qq_group_filter.py`

- [ ] **Step 1: Create `qq_group_filter.py`**

```python
"""轻量级群消息过滤器 — 不走 LLM，纯规则判断。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.adapters.qq_group_context import GroupContext, GroupMessage


class GroupMessageFilter:
    """Tier-1 filter: pure rules, zero LLM cost."""

    def __init__(
        self,
        self_id: str,
        self_names: list[str],
        kevin_id: str,
        interest_keywords: list[str],
        cooldown_seconds: int = 60,
    ) -> None:
        self.self_id = self_id
        self.self_names = [n.lower() for n in self_names]
        self.kevin_id = kevin_id
        self.interest_keywords = [k.lower() for k in interest_keywords]
        self.cooldown_seconds = cooldown_seconds

    def should_engage(self, msg: GroupMessage, ctx: GroupContext) -> tuple[bool, str]:
        """
        Decide whether this group message warrants Brain evaluation.
        Returns (should_pass, reason).
        """
        # Ignore own messages
        if msg.user_id == self.self_id:
            return False, "self"

        # @ me -> must respond
        if msg.is_at_self:
            return True, "at_self"

        # Reply to my message -> must respond
        if msg.is_reply_to_self:
            return True, "reply_to_self"

        # Name mentioned
        text_lower = msg.text.lower()
        for name in self.self_names:
            if name in text_lower:
                return True, "name_mention"

        # Kevin is speaking -> high priority
        if msg.user_id == self.kevin_id:
            return True, "kevin_speaking"

        # Interest keyword match (with cooldown)
        for keyword in self.interest_keywords:
            if keyword in text_lower:
                if ctx.seconds_since_last_reply() < self.cooldown_seconds:
                    return False, "keyword_cooldown"
                return True, f"keyword:{keyword}"

        # Activity burst: 10+ messages in 2 minutes
        recent = ctx.recent_messages(15)
        if len(recent) >= 10:
            time_span = msg.timestamp - recent[-10].timestamp
            if time_span < 120 and ctx.seconds_since_last_reply() > self.cooldown_seconds * 3:
                return True, "active_chat"

        return False, "no_match"
```

- [ ] **Step 2: Commit**

```bash
git add src/adapters/qq_group_filter.py
git commit -m "feat(qq): add tier-1 GroupMessageFilter with rule-based filtering"
```

---

## Task 8: Group Engage Decision Prompt

Write the tier-2 Brain prompt for group engagement decisions.

**Files:**
- Create: `prompts/group_engage_decision.md`

- [ ] **Step 1: Create prompt file**

```markdown
你正在一个 QQ 群里。以下是最近的群聊记录：

{group_context}

触发原因：{trigger_reason}

请判断你要不要参与这段对话。你有三个选择：

1. SKIP — 不参与。大多数情况下你应该选这个。你是一个安静的人，不需要什么都插嘴。
2. REACT {emoji_id} — 给最后一条消息点一个表情回应。适合你觉得有意思但没什么要说的时候。emoji_id 是 0-330 的数字。
3. REPLY {你要说的话} — 发一条消息。只在你真的有话想说的时候。

回复格式（严格遵守，只输出一行）：
SKIP
或
REACT {emoji_id}
或
REPLY {你要说的话}

注意：
- 你不是客服，不需要回答所有问题
- 你不是百科全书，不需要科普
- 你是群里的一个普通人，偶尔说话
- 如果触发原因是 "at_self" 或 "reply_to_self"，你应该回复
- 如果是 Kevin 在说话，你可以更主动一些
- 回复要简短自然，像群聊中的真人，不要长篇大论
- 可以用 [表情名] 格式的 QQ 表情
```

- [ ] **Step 2: Commit**

```bash
git add prompts/group_engage_decision.md
git commit -m "feat(qq): add group engagement decision prompt"
```

---

## Task 9: Group Chat Integration in QQAdapter

Wire group context, filter, and message handling into the adapter. This is the largest task.

**Files:**
- Modify: `src/adapters/qq_adapter.py` — `__init__`, `_handle_message_event`, new group methods

- [ ] **Step 1: Add imports and router attribute**

Add to imports at top of file:

```python
from src.adapters.qq_group_context import GroupContext, GroupMessage
from src.adapters.qq_group_filter import GroupMessageFilter
from src.core.prompt_loader import load_prompt
```

- [ ] **Step 2: Expand `__init__` with group attributes**

Add after `self._connection_task` (line 44):

```python
# Group chat
self._allowed_groups: set[str] = set(config.get("group_ids", []))
self._group_contexts: dict[str, GroupContext] = {}
self._group_filter: GroupMessageFilter | None = None
if self._allowed_groups:
    self._group_filter = GroupMessageFilter(
        self_id=self.self_id,
        self_names=config.get("self_names", ["Lapwing", "lapwing"]),
        kevin_id=self.kevin_id,
        interest_keywords=config.get("interest_keywords", []),
        cooldown_seconds=config.get("group_cooldown", 60),
    )
self._group_context_size: int = config.get("group_context_size", 30)
self.router = None  # Injected by main.py for group engagement decisions
```

- [ ] **Step 3: Rewrite `_handle_message_event` for private + group**

Replace the existing method:

```python
async def _handle_message_event(self, event: dict) -> None:
    user_id = str(event.get("user_id", ""))
    message_id = str(event.get("message_id", ""))
    message_type = event.get("message_type")

    if user_id == self.self_id:
        return

    # Dedup
    dedup_key = f"{user_id}:{message_id}"
    now = time.time()
    if dedup_key in self._message_dedup:
        return
    self._message_dedup[dedup_key] = now
    self._message_dedup = {k: v for k, v in self._message_dedup.items() if now - v < 60}

    text = self._extract_text(event)

    if message_type == "private":
        # Private: Kevin only (existing logic)
        if self.kevin_id and user_id != self.kevin_id:
            return
        if not text:
            return
        asyncio.create_task(self._mark_as_read(user_id))
        if self.on_message:
            asyncio.create_task(self.on_message(
                chat_id=user_id,
                text=text,
                channel=ChannelType.QQ,
                raw_event=event,
            ))

    elif message_type == "group":
        group_id = str(event.get("group_id", ""))
        if group_id not in self._allowed_groups:
            return

        ctx = self._get_group_context(group_id)
        is_at_self = self._check_at_self(event)
        is_reply_to_self = self._check_reply_to_self(event, ctx)
        nickname = self._get_sender_nickname(event)

        group_msg = GroupMessage(
            message_id=message_id,
            user_id=user_id,
            nickname=nickname,
            text=text or "(非文本消息)",
            timestamp=now,
            is_at_self=is_at_self,
            is_reply_to_self=is_reply_to_self,
        )
        ctx.add_message(group_msg)

        if not text:
            return
        if self._group_filter is None:
            return

        should_engage, reason = self._group_filter.should_engage(group_msg, ctx)
        if not should_engage:
            return

        asyncio.create_task(self._handle_group_engagement(ctx, group_msg, reason))
```

- [ ] **Step 4: Add group helper methods**

```python
def _get_group_context(self, group_id: str) -> GroupContext:
    if group_id not in self._group_contexts:
        self._group_contexts[group_id] = GroupContext(group_id=group_id)
    return self._group_contexts[group_id]

def _check_at_self(self, event: dict) -> bool:
    message = event.get("message", [])
    if isinstance(message, list):
        for seg in message:
            if seg.get("type") == "at" and str(seg.get("data", {}).get("qq", "")) == self.self_id:
                return True
    return False

def _check_reply_to_self(self, event: dict, ctx: GroupContext) -> bool:
    message = event.get("message", [])
    if isinstance(message, list):
        for seg in message:
            if seg.get("type") == "reply":
                reply_id = str(seg.get("data", {}).get("id", ""))
                if reply_id in ctx.my_recent_message_ids:
                    return True
    return False

def _get_sender_nickname(self, event: dict) -> str:
    sender = event.get("sender", {})
    return sender.get("card", "") or sender.get("nickname", "") or str(event.get("user_id", ""))
```

- [ ] **Step 5: Add group engagement handler**

```python
async def _handle_group_engagement(
    self, ctx: GroupContext, msg: GroupMessage, reason: str
) -> None:
    """Tier-2: ask Brain whether and how to participate."""
    action, content = await self._decide_group_engagement(ctx, reason)

    if action == "SKIP":
        return

    if action == "REACT":
        await self._react_to_message(msg.message_id, content)

    elif action == "REPLY":
        if msg.is_at_self or msg.is_reply_to_self:
            result = await self._send_group_reply(ctx.group_id, content, msg.message_id)
        else:
            result = await self._send_group_msg(ctx.group_id, content)

        # Track our message for reply-to-self detection
        resp_msg_id = str(result.get("data", {}).get("message_id", ""))
        if resp_msg_id:
            ctx.record_my_message(resp_msg_id)
        ctx.last_reply_time = time.time()

async def _decide_group_engagement(
    self, ctx: GroupContext, trigger_reason: str
) -> tuple[str, str]:
    """Call LLM to decide group participation. Returns (action, content)."""
    if self.router is None:
        return "SKIP", ""

    prompt_template = load_prompt("group_engage_decision")
    prompt = prompt_template.format(
        group_context=ctx.format_for_prompt(n=self._group_context_size),
        trigger_reason=trigger_reason,
    )

    try:
        response = await self.router.complete(
            messages=[{"role": "user", "content": prompt}],
            purpose="tool",
            max_tokens=200,
        )
    except Exception as exc:
        logger.warning("Group engagement decision failed: %s", exc)
        return "SKIP", ""

    text = response.strip()
    if text.startswith("SKIP"):
        return "SKIP", ""
    if text.startswith("REACT"):
        emoji_id = text.replace("REACT", "", 1).strip()
        return "REACT", emoji_id
    if text.startswith("REPLY"):
        reply = text.replace("REPLY", "", 1).strip()
        return "REPLY", reply
    return "SKIP", ""
```

- [ ] **Step 6: Add group send API methods**

```python
async def _send_group_msg(self, group_id: str, text: str) -> dict:
    text_plain = self._markdown_to_plain(text)
    segments = self._build_message_segments(text_plain)
    return await self._call_api("send_group_msg", {
        "group_id": int(group_id),
        "message": segments,
    })

async def _send_group_reply(self, group_id: str, text: str, reply_to_id: str) -> dict:
    text_plain = self._markdown_to_plain(text)
    segments: list[dict] = [{"type": "reply", "data": {"id": reply_to_id}}]
    segments.extend(self._build_message_segments(text_plain))
    return await self._call_api("send_group_msg", {
        "group_id": int(group_id),
        "message": segments,
    })

async def _react_to_message(self, message_id: str, emoji_id: str) -> None:
    try:
        await self._call_api("set_msg_emoji_like", {
            "message_id": message_id,
            "emoji_id": emoji_id,
        })
    except Exception:
        pass  # Non-critical
```

- [ ] **Step 7: Commit**

```bash
git add src/adapters/qq_adapter.py
git commit -m "feat(qq): integrate group chat handling with tier-1 filter and tier-2 Brain decision"
```

---

## Task 10: Main.py Integration

Wire group config and router injection into main.py.

**Files:**
- Modify: `main.py:166-204`

- [ ] **Step 1: Expand QQ config in main.py**

Replace the QQ registration block (lines 167-204):

```python
    from config.settings import QQ_ENABLED
    if QQ_ENABLED:
        from config.settings import (
            QQ_WS_URL, QQ_ACCESS_TOKEN, QQ_SELF_ID, QQ_KEVIN_ID,
            QQ_GROUP_IDS, QQ_GROUP_CONTEXT_SIZE, QQ_GROUP_COOLDOWN,
            QQ_GROUP_INTEREST_KEYWORDS,
        )
        from src.adapters.base import ChannelType
        from src.adapters.qq_adapter import QQAdapter

        qq_config = {
            "ws_url": QQ_WS_URL,
            "access_token": QQ_ACCESS_TOKEN,
            "self_id": QQ_SELF_ID,
            "kevin_id": QQ_KEVIN_ID,
            "group_ids": QQ_GROUP_IDS,
            "self_names": ["Lapwing", "lapwing", "小翅"],
            "interest_keywords": QQ_GROUP_INTEREST_KEYWORDS,
            "group_cooldown": QQ_GROUP_COOLDOWN,
            "group_context_size": QQ_GROUP_CONTEXT_SIZE,
        }

        async def _qq_on_message(chat_id: str, text: str, channel, raw_event: dict) -> None:
            """QQ 消息进入 Brain 的桥接。"""
            container.channel_manager.last_active_channel = channel
            brain = container.brain

            async def send_fn(reply_text: str) -> None:
                await container.channel_manager.send(ChannelType.QQ, chat_id, reply_text)

            async def typing_fn() -> None:
                pass  # QQ 无 typing indicator

            async def noop_status(cid: str, t: str) -> None:
                pass

            await brain.think_conversational(
                chat_id,
                text,
                send_fn=send_fn,
                typing_fn=typing_fn,
                status_callback=noop_status,
            )

        qq_adapter = QQAdapter(config=qq_config, on_message=_qq_on_message)
        qq_adapter.router = container.brain.router  # Inject LLM router for group decisions
        container.channel_manager.register(ChannelType.QQ, qq_adapter)
        logger.info("QQ 通道已注册（群聊: %s）", QQ_GROUP_IDS or "无")
```

- [ ] **Step 2: Commit**

```bash
git add main.py
git commit -m "feat(qq): wire group chat config and router injection in main.py"
```

---

## Task 11: Smoke Test Checklist

Manual verification after deploying all changes.

- [ ] **Step 1: Private chat — face emoji send**

Send a reply containing `[微笑]` from Brain. Verify QQ client shows the emoji, not raw text.

- [ ] **Step 2: Private chat — face emoji receive**

Send a QQ face emoji to Lapwing. Check logs that Brain sees `[表情名]` text.

- [ ] **Step 3: Private chat — mark as read**

Send a message to Lapwing on QQ. Verify the message shows as "read" in QQ client.

- [ ] **Step 4: Group chat — non-whitelist ignored**

Send messages in a group NOT in `QQ_GROUP_IDS`. Verify no log activity.

- [ ] **Step 5: Group chat — @ triggers response**

@ Lapwing in a whitelisted group. Verify she responds.

- [ ] **Step 6: Group chat — cooldown respected**

Trigger two responses within cooldown period. Verify second is suppressed for keyword triggers.

- [ ] **Step 7: Group chat — SKIP respected**

Verify most keyword triggers result in SKIP from Brain (check logs).

- [ ] **Step 8: Memory stability**

Run for 30+ minutes with active group. Verify no memory growth (deque maxlen bounds the buffer).
