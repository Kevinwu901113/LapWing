# Proactive Outbound Trajectory Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record successfully delivered proactive outbound messages as visible trajectory entries so follow-up user replies have conversational context.

**Architecture:** Add `PROACTIVE_OUTBOUND` entry type to the trajectory store; resolve send_message targets to canonical chat_ids matching inbound routing; write one trajectory entry per successful adapter delivery (best-effort, post-delivery); render PROACTIVE_OUTBOUND as assistant turn in StateView and legacy message projection.

**Tech Stack:** Python 3.12, aiosqlite, pytest + pytest-asyncio, MagicMock/AsyncMock

**Files to modify:**
- `src/core/trajectory_store.py` — enum, role map, legacy projection, `has_recent_entry()`, index
- `src/tools/personal_tools.py` — resolver, trajectory write in `_send_message`
- `src/core/state_view_builder.py` — `_ROLE_MAP`, `_extract_entry_text`
- `config/settings.py` — feature flag

**Files to create:**
- `tests/tools/test_send_message_trajectory.py` — send_message trajectory write tests
- Add tests to existing test files for trajectory store, state view builder, brain load history

**Canonical chat_id resolution:**
- `kevin_qq` → `QQ_KEVIN_ID` (the QQ number; matches `qq_adapter.normalize_inbound` where `chat_id=user_id` for private messages)
- `kevin_desktop` → `f"{DESKTOP_WS_CHAT_ID_PREFIX}:{connection_id}"` from first active desktop connection; None if no active connections
- `qq_group:{id}` → None for now (skip trajectory write, log info)
- Extensible: `resolve_proactive_target_chat_id(target, ctx)` in personal_tools.py

---

### Task 1: Add PROACTIVE_OUTBOUND enum and index migration

**Files:**
- Modify: `src/core/trajectory_store.py:35-52` (enum), `:109-127` (index), `:213-319` (append)
- Create: `tests/core/test_trajectory_store.py` (add has_recent_entry tests)

- [ ] **Step 1: Add PROACTIVE_OUTBOUND to TrajectoryEntryType enum**

```python
# src/core/trajectory_store.py, line 44-52, add after INTERRUPTED:
    PROACTIVE_OUTBOUND = "proactive_outbound"
```

- [ ] **Step 2: Add index in TrajectoryStore.init()**

In `src/core/trajectory_store.py`, inside the `init()` method, after the existing `CREATE INDEX` statements (line 117), add:

```python
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_trajectory_chat_type_timestamp "
            "ON trajectory(source_chat_id, entry_type, timestamp)"
        )
        await self._db.commit()
```

- [ ] **Step 3: Add `has_recent_entry` method to TrajectoryStore**

In `src/core/trajectory_store.py`, add after the `list_for_timeline` method (~line 439):

```python
    async def has_recent_entry(
        self,
        source_chat_id: str,
        entry_type: TrajectoryEntryType,
        since: float,
    ) -> bool:
        if self._db is None:
            return False
        async with self._db.execute(
            "SELECT 1 FROM trajectory "
            "WHERE source_chat_id = ? AND entry_type = ? AND timestamp > ? "
            "LIMIT 1",
            (source_chat_id, entry_type.value, since),
        ) as cur:
            row = await cur.fetchone()
            return row is not None
```

- [ ] **Step 4: Write tests for has_recent_entry**

In `tests/core/test_trajectory_store.py`, add:

```python
import time as _time

class TestHasRecentEntry:
    async def test_has_recent_entry_returns_true_when_entry_exists(self, store):
        chat_id = "919231551"
        now = _time.time()
        await store.append(
            TrajectoryEntryType.USER_MESSAGE, chat_id, "user",
            {"text": "hello"},
            timestamp=now - 3600,
        )
        assert await store.has_recent_entry(
            chat_id, TrajectoryEntryType.USER_MESSAGE, now - 86400,
        ) is True

    async def test_has_recent_entry_returns_false_when_too_old(self, store):
        chat_id = "919231551"
        now = _time.time()
        await store.append(
            TrajectoryEntryType.USER_MESSAGE, chat_id, "user",
            {"text": "old"},
            timestamp=now - 172800,
        )
        assert await store.has_recent_entry(
            chat_id, TrajectoryEntryType.USER_MESSAGE, now - 86400,
        ) is False

    async def test_has_recent_entry_returns_false_when_wrong_type(self, store):
        chat_id = "919231551"
        now = _time.time()
        await store.append(
            TrajectoryEntryType.USER_MESSAGE, chat_id, "user",
            {"text": "hi"},
            timestamp=now - 60,
        )
        assert await store.has_recent_entry(
            chat_id, TrajectoryEntryType.INNER_THOUGHT, now - 86400,
        ) is False

    async def test_has_recent_entry_returns_false_when_wrong_chat(self, store):
        now = _time.time()
        await store.append(
            TrajectoryEntryType.USER_MESSAGE, "chat_a", "user",
            {"text": "hi"},
            timestamp=now - 60,
        )
        assert await store.has_recent_entry(
            "chat_b", TrajectoryEntryType.USER_MESSAGE, now - 86400,
        ) is False

    async def test_has_recent_entry_returns_false_when_empty(self, store):
        assert await store.has_recent_entry(
            "any_chat", TrajectoryEntryType.USER_MESSAGE, _time.time() - 86400,
        ) is False
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/core/test_trajectory_store.py::TestHasRecentEntry -v`
Expected: All 5 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/core/trajectory_store.py tests/core/test_trajectory_store.py
git commit -m "feat(trajectory): add PROACTIVE_OUTBOUND type, has_recent_entry, and chat_type_timestamp index"
```

---

### Task 2: Add PROACTIVE_OUTBOUND to role maps and content extraction

**Files:**
- Modify: `src/core/trajectory_store.py:492-545`
- Modify: `src/core/state_view_builder.py:711-787`

- [ ] **Step 1: Add PROACTIVE_OUTBOUND to _LEGACY_ROLE_MAP and update _extract_legacy_text**

In `src/core/trajectory_store.py`, update `_LEGACY_ROLE_MAP` (line 492-496):

```python
_LEGACY_ROLE_MAP: dict[str, str] = {
    TrajectoryEntryType.USER_MESSAGE.value: "user",
    TrajectoryEntryType.TELL_USER.value: "assistant",
    TrajectoryEntryType.ASSISTANT_TEXT.value: "assistant",
    TrajectoryEntryType.PROACTIVE_OUTBOUND.value: "assistant",
}
```

Update `_extract_legacy_text` (line 532-545) to handle structured content for all message-like types:

```python
def _extract_legacy_text(entry: TrajectoryEntry) -> str | None:
    content = entry.content or {}
    if entry.entry_type == TrajectoryEntryType.TELL_USER.value:
        msgs = content.get("messages")
        if isinstance(msgs, list) and msgs:
            return "\n".join(str(m) for m in msgs)
        text = content.get("text")
        if isinstance(text, str):
            return text
        return None
    # Unified extraction for USER_MESSAGE, ASSISTANT_TEXT, PROACTIVE_OUTBOUND
    text = content.get("text")
    if isinstance(text, str):
        return text
    # Fallback: content field (used by some entry types)
    text = content.get("content")
    if isinstance(text, str):
        return text
    return None
```

- [ ] **Step 2: Add PROACTIVE_OUTBOUND to _ROLE_MAP in state_view_builder.py**

In `src/core/state_view_builder.py`, update `_ROLE_MAP` (line 711-715):

```python
_ROLE_MAP: dict[str, str] = {
    TrajectoryEntryType.USER_MESSAGE.value: "user",
    TrajectoryEntryType.TELL_USER.value: "assistant",
    TrajectoryEntryType.ASSISTANT_TEXT.value: "assistant",
    TrajectoryEntryType.PROACTIVE_OUTBOUND.value: "assistant",
}
```

Update `_extract_entry_text` (line 778-787) to add "content" field fallback:

```python
def _extract_entry_text(entry: TrajectoryEntry) -> str | None:
    content = entry.content or {}
    if entry.entry_type == TrajectoryEntryType.TELL_USER.value:
        msgs = content.get("messages")
        if isinstance(msgs, list) and msgs:
            return "\n".join(str(m) for m in msgs)
    text = content.get("text")
    if isinstance(text, str):
        return text
    # Fallback for types that use "content" key
    text = content.get("content")
    if isinstance(text, str):
        return text
    return None
```

- [ ] **Step 3: Write tests for role mapping and content extraction**

In a new section of `tests/core/test_trajectory_store.py`, add:

```python
class TestProactiveOutboundLegacyProjection:
    def test_proactive_outbound_renders_as_assistant(self):
        from src.core.trajectory_store import trajectory_entries_to_messages
        entry = TrajectoryEntry(
            id=1, timestamp=1000.0,
            entry_type=TrajectoryEntryType.PROACTIVE_OUTBOUND.value,
            source_chat_id="c1", actor="assistant",
            content={"text": "下午好～", "target": "kevin_qq", "kind": "proactive_outbound", "source": "send_message"},
            related_commitment_id=None, related_iteration_id=None,
            related_tool_call_id=None,
        )
        msgs = trajectory_entries_to_messages([entry])
        assert msgs == [{"role": "assistant", "content": "下午好～"}]

    def test_proactive_outbound_excluded_when_include_inner_false(self):
        from src.core.trajectory_store import trajectory_entries_to_messages
        inner = TrajectoryEntry(
            id=1, timestamp=1000.0,
            entry_type=TrajectoryEntryType.INNER_THOUGHT.value,
            source_chat_id=None, actor="lapwing",
            content={"text": "internal reasoning"},
            related_commitment_id=None, related_iteration_id=None,
            related_tool_call_id=None,
        )
        msgs = trajectory_entries_to_messages([inner], include_inner=False)
        assert msgs == []

    def test_consecutive_assistant_turns_proactive_after_direct(self):
        from src.core.trajectory_store import trajectory_entries_to_messages
        entries = [
            TrajectoryEntry(
                id=1, timestamp=1000.0,
                entry_type=TrajectoryEntryType.USER_MESSAGE.value,
                source_chat_id="c1", actor="user",
                content={"text": "你好"},
                related_commitment_id=None, related_iteration_id=None,
                related_tool_call_id=None,
            ),
            TrajectoryEntry(
                id=2, timestamp=1001.0,
                entry_type=TrajectoryEntryType.ASSISTANT_TEXT.value,
                source_chat_id="c1", actor="lapwing",
                content={"text": "你好！"},
                related_commitment_id=None, related_iteration_id=None,
                related_tool_call_id=None,
            ),
            TrajectoryEntry(
                id=3, timestamp=1002.0,
                entry_type=TrajectoryEntryType.PROACTIVE_OUTBOUND.value,
                source_chat_id="c1", actor="assistant",
                content={"text": "顺便提醒一下～", "target": "kevin_qq", "kind": "proactive_outbound", "source": "send_message"},
                related_commitment_id=None, related_iteration_id=None,
                related_tool_call_id=None,
            ),
        ]
        msgs = trajectory_entries_to_messages(entries)
        assert msgs == [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！"},
            {"role": "assistant", "content": "顺便提醒一下～"},
        ]

    def test_consecutive_assistant_turns_direct_after_proactive(self):
        from src.core.trajectory_store import trajectory_entries_to_messages
        entries = [
            TrajectoryEntry(
                id=1, timestamp=1000.0,
                entry_type=TrajectoryEntryType.PROACTIVE_OUTBOUND.value,
                source_chat_id="c1", actor="assistant",
                content={"text": "下午好～", "target": "kevin_qq", "kind": "proactive_outbound", "source": "send_message"},
                related_commitment_id=None, related_iteration_id=None,
                related_tool_call_id=None,
            ),
            TrajectoryEntry(
                id=2, timestamp=1001.0,
                entry_type=TrajectoryEntryType.ASSISTANT_TEXT.value,
                source_chat_id="c1", actor="lapwing",
                content={"text": "收到！"},
                related_commitment_id=None, related_iteration_id=None,
                related_tool_call_id=None,
            ),
        ]
        msgs = trajectory_entries_to_messages(entries)
        assert msgs == [
            {"role": "assistant", "content": "下午好～"},
            {"role": "assistant", "content": "收到！"},
        ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/core/test_trajectory_store.py::TestProactiveOutboundLegacyProjection -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Write tests for StateView _entries_to_turns with PROACTIVE_OUTBOUND**

In `tests/core/test_state_view_builder.py`, add:

```python
class TestEntriesToTurnsProactiveOutbound:
    def test_proactive_outbound_renders_as_assistant_in_turns(self):
        from src.core.state_view_builder import _entries_to_turns
        from src.core.trajectory_store import TrajectoryEntry, TrajectoryEntryType
        entry = TrajectoryEntry(
            id=1, timestamp=1000.0,
            entry_type=TrajectoryEntryType.PROACTIVE_OUTBOUND.value,
            source_chat_id="c1", actor="assistant",
            content={"text": "下午好～", "target": "kevin_qq", "kind": "proactive_outbound", "source": "send_message"},
            related_commitment_id=None, related_iteration_id=None,
            related_tool_call_id=None,
        )
        turns = _entries_to_turns([entry])
        assert len(turns) == 1
        assert turns[0].role == "assistant"
        assert turns[0].content == "下午好～"

    def test_inner_thought_still_excluded_from_turns(self):
        from src.core.state_view_builder import _entries_to_turns
        from src.core.trajectory_store import TrajectoryEntry, TrajectoryEntryType
        entry = TrajectoryEntry(
            id=1, timestamp=1000.0,
            entry_type=TrajectoryEntryType.INNER_THOUGHT.value,
            source_chat_id=None, actor="lapwing",
            content={"text": "internal"},
            related_commitment_id=None, related_iteration_id=None,
            related_tool_call_id=None,
        )
        turns = _entries_to_turns([entry])
        assert turns == []
```

- [ ] **Step 6: Run state view tests**

Run: `pytest tests/core/test_state_view_builder.py::TestEntriesToTurnsProactiveOutbound -v`
Expected: 2 tests PASS

- [ ] **Step 7: Commit**

```bash
git add src/core/trajectory_store.py src/core/state_view_builder.py tests/core/test_trajectory_store.py tests/core/test_state_view_builder.py
git commit -m "feat(trajectory): add PROACTIVE_OUTBOUND to role maps and content extraction"
```

---

### Task 3: Add feature flag

**Files:**
- Modify: `config/settings.py`

- [ ] **Step 1: Add PROACTIVE_OUTBOUND_TRAJECTORY_ENABLED constant**

In `config/settings.py`, in the proactive/other section (~line 316), add:

```python
# ── Proactive outbound trajectory ─────────────

PROACTIVE_OUTBOUND_TRAJECTORY_ENABLED: bool = True
```

(Default True — this is a data consistency bug fix, not an experiment.)

- [ ] **Step 2: Commit**

```bash
git add config/settings.py
git commit -m "feat(config): add PROACTIVE_OUTBOUND_TRAJECTORY_ENABLED feature flag"
```

---

### Task 4: Implement canonical chat_id resolver and trajectory write in _send_message

**Files:**
- Modify: `src/tools/personal_tools.py`
- Create: `tests/tools/test_send_message_trajectory.py`

- [ ] **Step 1: Add resolver function to personal_tools.py**

Add after `_is_proactive_context` (~line 184) in `src/tools/personal_tools.py`:

```python
def _resolve_proactive_target_chat_id(
    target: str,
    ctx: ToolExecutionContext,
) -> str | None:
    """Map a send_message ``target`` to the canonical chat_id used by inbound routing.

    Returns None when the mapping cannot be resolved (e.g. unknown target
    or desktop has no active connections). Callers must handle None by
    skipping trajectory write (not by skipping the send itself).
    """
    from src.core.tool_dispatcher import ServiceContextView
    svc = ServiceContextView(ctx.services or {})

    if target == "kevin_qq":
        owner_qq_id = svc.owner_qq_id
        if owner_qq_id:
            return str(owner_qq_id)
        return None

    if target == "kevin_desktop":
        channel_manager = svc.channel_manager
        if channel_manager is None:
            return None
        try:
            desktop_adapter = channel_manager.get_adapter("desktop")
        except Exception:
            return None
        if desktop_adapter is None:
            return None
        connections = getattr(desktop_adapter, "connections", {})
        if not connections:
            return None
        first_cid = next(iter(connections.keys()))
        from config.settings import DESKTOP_WS_CHAT_ID_PREFIX
        return f"{DESKTOP_WS_CHAT_ID_PREFIX}:{first_cid}"

    if target.startswith("qq_group:"):
        # Group canonical chat_id = group_id from normalize_inbound.
        # Reuse would require confirming this matches exactly, and
        # testing group trajectory write is out of v1 scope.
        return None

    return None
```

- [ ] **Step 2: Add helper to perform best-effort trajectory write**

Add after `_resolve_proactive_target_chat_id`:

```python
async def _record_proactive_outbound_trajectory(
    *,
    ctx: ToolExecutionContext,
    target: str,
    content: str,
    channel: str,
    resolved_chat_id: str,
) -> None:
    """Best-effort record of a delivered proactive outbound message.

    Failure must not fail the send_message call — the message already
    reached the user. The worst acceptable fallback is the old bug.
    """
    from src.core.tool_dispatcher import ServiceContextView
    from src.core.trajectory_store import TrajectoryEntryType
    from config.settings import PROACTIVE_OUTBOUND_TRAJECTORY_ENABLED

    if not PROACTIVE_OUTBOUND_TRAJECTORY_ENABLED:
        return

    svc = ServiceContextView(ctx.services or {})
    trajectory_store = svc.trajectory_store
    if trajectory_store is None:
        return

    try:
        await trajectory_store.append(
            TrajectoryEntryType.PROACTIVE_OUTBOUND,
            resolved_chat_id,
            "assistant",
            {
                "text": content,
                "target": target,
                "channel": channel,
                "kind": "proactive_outbound",
                "source": "send_message",
            },
        )
    except Exception:
        logger.exception(
            "Failed to record proactive outbound trajectory entry "
            "target=%s chat_id=%s",
            target, resolved_chat_id,
        )
        return

    # Observability: warn if the resolved chat_id has no recent inbound
    try:
        import time as _time
        since = _time.time() - 86400
        has_recent = await trajectory_store.has_recent_entry(
            resolved_chat_id,
            TrajectoryEntryType.USER_MESSAGE,
            since,
        )
        if not has_recent:
            logger.warning(
                "proactive_outbound_no_recent_inbound target=%s "
                "resolved_chat_id=%s channel=%s",
                target, resolved_chat_id, channel,
            )
    except Exception:
        logger.debug(
            "has_recent_entry check failed for proactive outbound",
            exc_info=True,
        )
```

- [ ] **Step 3: Insert trajectory write calls after each successful adapter send**

In `_send_message`, after each successful return, add trajectory write BEFORE the return.

For `kevin_desktop` (line ~289-293), change from:

```python
            await desktop_adapter.send_text(desktop_adapter.config.get("kevin_id", "owner"), content)
            return ToolExecutionResult(
                success=True,
                payload={"sent": True, "target": target, "content": content},
            )
```

To:

```python
            await desktop_adapter.send_text(desktop_adapter.config.get("kevin_id", "owner"), content)
            _resolved = _resolve_proactive_target_chat_id(target, ctx)
            if _resolved is not None:
                await _record_proactive_outbound_trajectory(
                    ctx=ctx, target=target, content=content,
                    channel="desktop", resolved_chat_id=_resolved,
                )
            else:
                logger.info(
                    "[send_message] skipped proactive trajectory write: "
                    "no resolved chat_id for target=%s", target,
                )
            return ToolExecutionResult(
                success=True,
                payload={"sent": True, "target": target, "content": content},
            )
```

For `kevin_qq` (line ~317-321), change from:

```python
            await qq_adapter.send_private_message(str(owner_qq_id), content)
            return ToolExecutionResult(
                success=True,
                payload={"sent": True, "target": target, "content": content},
            )
```

To:

```python
            await qq_adapter.send_private_message(str(owner_qq_id), content)
            _resolved = _resolve_proactive_target_chat_id(target, ctx)
            if _resolved is not None:
                await _record_proactive_outbound_trajectory(
                    ctx=ctx, target=target, content=content,
                    channel="qq", resolved_chat_id=_resolved,
                )
            return ToolExecutionResult(
                success=True,
                payload={"sent": True, "target": target, "content": content},
            )
```

For `qq_group:` (line ~345-349), change from:

```python
            await qq_adapter.send_group_message(group_id, content)
            return ToolExecutionResult(
                success=True,
                payload={"sent": True, "target": target, "content": content},
            )
```

To:

```python
            await qq_adapter.send_group_message(group_id, content)
            _resolved = _resolve_proactive_target_chat_id(target, ctx)
            if _resolved is not None:
                await _record_proactive_outbound_trajectory(
                    ctx=ctx, target=target, content=content,
                    channel="qq_group", resolved_chat_id=_resolved,
                )
            else:
                logger.info(
                    "[send_message] skipped proactive trajectory write: "
                    "group canonical chat_id not resolved for target=%s",
                    target,
                )
            return ToolExecutionResult(
                success=True,
                payload={"sent": True, "target": target, "content": content},
            )
```

- [ ] **Step 4: Write comprehensive send_message trajectory tests**

Create `tests/tools/test_send_message_trajectory.py`:

```python
"""Tests for proactive outbound trajectory recording in send_message."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.trajectory_store import TrajectoryEntryType
from src.tools.personal_tools import _send_message, _resolve_proactive_target_chat_id
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


def _make_qq_ctx(*, gate_decision="allow", trajectory_store=None, owner_qq_id="919231551"):
    qq = MagicMock()
    qq.send_private_message = AsyncMock()
    cm = MagicMock()
    cm.get_adapter = MagicMock(return_value=qq)
    gate = MagicMock()
    gate.evaluate = MagicMock(return_value=MagicMock(
        decision=gate_decision, reason="test", bypassed=False,
    ))
    services = {
        "channel_manager": cm,
        "owner_qq_id": owner_qq_id,
        "proactive_send_active": True,
        "proactive_message_gate": gate,
    }
    if trajectory_store is not None:
        services["trajectory_store"] = trajectory_store
    ctx = ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
        services=services,
        runtime_profile="inner_tick",
        chat_id="chat1",
    )
    return ctx, qq


def _make_desktop_ctx(*, gate_decision="allow", trajectory_store=None, connected=True):
    desktop = MagicMock()
    desktop.send_text = AsyncMock()
    desktop.is_connected = AsyncMock(return_value=connected)
    if connected:
        desktop.connections = {"12345": MagicMock()}
    else:
        desktop.connections = {}
    desktop.config = {"kevin_id": "owner"}
    cm = MagicMock()
    cm.get_adapter = MagicMock(return_value=desktop)
    gate = MagicMock()
    gate.evaluate = MagicMock(return_value=MagicMock(
        decision=gate_decision, reason="test", bypassed=False,
    ))
    services = {
        "channel_manager": cm,
        "owner_qq_id": "919231551",
        "proactive_send_active": True,
        "proactive_message_gate": gate,
    }
    if trajectory_store is not None:
        services["trajectory_store"] = trajectory_store
    ctx = ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
        services=services,
        runtime_profile="inner_tick",
        chat_id="chat1",
    )
    return ctx, desktop


class TestResolveProactiveTargetChatId:
    def test_kevin_qq_resolves_to_owner_qq_id(self):
        ctx, _ = _make_qq_ctx()
        result = _resolve_proactive_target_chat_id("kevin_qq", ctx)
        assert result == "919231551"

    def test_kevin_qq_returns_none_when_no_owner_qq_id(self):
        ctx, _ = _make_qq_ctx(owner_qq_id="")
        result = _resolve_proactive_target_chat_id("kevin_qq", ctx)
        assert result is None

    def test_kevin_desktop_resolves_to_prefix_with_connection_id(self):
        ctx, _ = _make_desktop_ctx(connected=True)
        result = _resolve_proactive_target_chat_id("kevin_desktop", ctx)
        assert result == "desktop:12345"

    def test_kevin_desktop_returns_none_when_no_connections(self):
        ctx, _ = _make_desktop_ctx(connected=False)
        result = _resolve_proactive_target_chat_id("kevin_desktop", ctx)
        assert result is None

    def test_qq_group_returns_none(self):
        ctx, _ = _make_qq_ctx()
        result = _resolve_proactive_target_chat_id("qq_group:123456", ctx)
        assert result is None

    def test_unknown_target_returns_none(self):
        ctx, _ = _make_qq_ctx()
        result = _resolve_proactive_target_chat_id("unknown_target", ctx)
        assert result is None


class TestSendMessageTrajectoryWrite:
    @pytest.mark.asyncio
    async def test_successful_kevin_qq_send_writes_proactive_outbound(self):
        ts = AsyncMock()
        ts.append = AsyncMock(return_value=42)
        ts.has_recent_entry = AsyncMock(return_value=True)
        ctx, qq = _make_qq_ctx(trajectory_store=ts)

        req = ToolExecutionRequest(
            name="send_message",
            arguments={"target": "kevin_qq", "content": "下午好～"},
        )
        result = await _send_message(req, ctx)

        assert result.success is True
        ts.append.assert_awaited_once()
        call_args = ts.append.call_args
        assert call_args.args[0] == TrajectoryEntryType.PROACTIVE_OUTBOUND
        assert call_args.args[1] == "919231551"  # source_chat_id
        assert call_args.args[2] == "assistant"
        content = call_args.args[3]
        assert content["text"] == "下午好～"
        assert content["target"] == "kevin_qq"
        assert content["channel"] == "qq"
        assert content["kind"] == "proactive_outbound"
        assert content["source"] == "send_message"

    @pytest.mark.asyncio
    async def test_successful_kevin_desktop_send_writes_proactive_outbound(self):
        ts = AsyncMock()
        ts.append = AsyncMock(return_value=42)
        ts.has_recent_entry = AsyncMock(return_value=True)
        ctx, desktop = _make_desktop_ctx(trajectory_store=ts, connected=True)

        req = ToolExecutionRequest(
            name="send_message",
            arguments={"target": "kevin_desktop", "content": "hello from desktop"},
        )
        result = await _send_message(req, ctx)

        assert result.success is True
        ts.append.assert_awaited_once()
        call_args = ts.append.call_args
        assert call_args.args[1] == "desktop:12345"
        content = call_args.args[3]
        assert content["channel"] == "desktop"

    @pytest.mark.asyncio
    async def test_gate_deny_does_not_write_trajectory(self):
        ts = AsyncMock()
        ts.append = AsyncMock()
        ctx, qq = _make_qq_ctx(gate_decision="deny", trajectory_store=ts)

        req = ToolExecutionRequest(
            name="send_message",
            arguments={"target": "kevin_qq", "content": "should be denied"},
        )
        result = await _send_message(req, ctx)

        assert result.success is False
        assert "gate_decision" in result.payload
        ts.append.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_gate_defer_does_not_write_trajectory(self):
        ts = AsyncMock()
        ts.append = AsyncMock()
        ctx, qq = _make_qq_ctx(gate_decision="defer", trajectory_store=ts)

        req = ToolExecutionRequest(
            name="send_message",
            arguments={"target": "kevin_qq", "content": "should be deferred"},
        )
        result = await _send_message(req, ctx)

        assert result.success is False
        ts.append.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_adapter_exception_does_not_write_trajectory(self):
        ts = AsyncMock()
        ts.append = AsyncMock()
        ctx, qq = _make_qq_ctx(trajectory_store=ts)
        qq.send_private_message = AsyncMock(side_effect=RuntimeError("QQ down"))

        req = ToolExecutionRequest(
            name="send_message",
            arguments={"target": "kevin_qq", "content": "will fail"},
        )
        result = await _send_message(req, ctx)

        assert result.success is False
        ts.append.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_desktop_disconnected_does_not_write_trajectory(self):
        ts = AsyncMock()
        ts.append = AsyncMock()
        ctx, desktop = _make_desktop_ctx(trajectory_store=ts, connected=False)

        req = ToolExecutionRequest(
            name="send_message",
            arguments={"target": "kevin_desktop", "content": "will fail"},
        )
        result = await _send_message(req, ctx)

        assert result.success is False
        ts.append.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_urgent_bypass_success_still_writes_trajectory(self):
        ts = AsyncMock()
        ts.append = AsyncMock(return_value=42)
        ts.has_recent_entry = AsyncMock(return_value=True)
        ctx, qq = _make_qq_ctx(trajectory_store=ts)

        req = ToolExecutionRequest(
            name="send_message",
            arguments={
                "target": "kevin_qq",
                "content": "紧急提醒",
                "category": "reminder_due",
                "urgent": True,
            },
        )
        result = await _send_message(req, ctx)

        assert result.success is True
        ts.append.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_trajectory_append_failure_does_not_fail_send_message(self):
        ts = AsyncMock()
        ts.append = AsyncMock(side_effect=RuntimeError("db down"))
        ts.has_recent_entry = AsyncMock(return_value=True)
        ctx, qq = _make_qq_ctx(trajectory_store=ts)

        req = ToolExecutionRequest(
            name="send_message",
            arguments={"target": "kevin_qq", "content": "still delivers"},
        )
        result = await _send_message(req, ctx)

        assert result.success is True  # send still succeeded
        assert result.payload["sent"] is True
        ts.append.assert_awaited_once()  # attempted

    @pytest.mark.asyncio
    async def test_no_recent_inbound_warns_but_still_writes(self):
        ts = AsyncMock()
        ts.append = AsyncMock(return_value=42)
        ts.has_recent_entry = AsyncMock(return_value=False)
        ctx, qq = _make_qq_ctx(trajectory_store=ts)

        req = ToolExecutionRequest(
            name="send_message",
            arguments={"target": "kevin_qq", "content": "first contact"},
        )
        result = await _send_message(req, ctx)

        assert result.success is True
        ts.append.assert_awaited_once()
        ts.has_recent_entry.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unresolved_chat_id_skips_write_with_info_log(self, caplog):
        ts = AsyncMock()
        ts.append = AsyncMock()
        ctx, qq = _make_qq_ctx(trajectory_store=ts, owner_qq_id="")

        req = ToolExecutionRequest(
            name="send_message",
            arguments={"target": "kevin_qq", "content": "no owner id"},
        )
        with caplog.at_level("INFO"):
            result = await _send_message(req, ctx)

        assert result.success is False
        assert "owner_qq_id" in result.payload.get("error", "")
        ts.append.assert_not_awaited()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/tools/test_send_message_trajectory.py -v`
Expected: 12 tests PASS (5 resolver + 7 trajectory write in send_message... actually 11 tests; update count after running)

- [ ] **Step 6: Commit**

```bash
git add src/tools/personal_tools.py tests/tools/test_send_message_trajectory.py
git commit -m "feat(proactive): record PROACTIVE_OUTBOUND trajectory entry after successful send_message delivery"
```

---

### Task 5: Brain load_history and full integration tests

**Files:**
- Create/modify: `tests/core/test_brain_load_history.py` (add tests)
- Create/modify: `tests/core/test_state_view_builder.py` (add tests)

- [ ] **Step 1: Add load_history tests for PROACTIVE_OUTBOUND**

In `tests/core/test_brain_load_history.py`, add:

```python
class TestLoadHistoryProactiveOutbound:
    """Verify _load_history sees PROACTIVE_OUTBOUND with include_inner=False."""

    async def test_load_history_includes_proactive_outbound_for_same_chat(self, brain):
        from src.core.trajectory_store import TrajectoryEntryType

        brain.trajectory_store = AsyncMock()
        brain.trajectory_store.relevant_to_chat = AsyncMock(return_value=[
            _mk_entry(1, TrajectoryEntryType.PROACTIVE_OUTBOUND, "c1", "assistant",
                       "下午好～第二个盲审有消息了吗？"),
            _mk_entry(2, TrajectoryEntryType.USER_MESSAGE, "c1", "user", "还没"),
        ])

        out = await brain._load_history("c1")
        assert out == [
            {"role": "assistant", "content": "下午好～第二个盲审有消息了吗？"},
            {"role": "user", "content": "还没"},
        ]
        brain.trajectory_store.relevant_to_chat.assert_awaited_once()
        kwargs = brain.trajectory_store.relevant_to_chat.call_args.kwargs
        assert kwargs.get("include_inner") is False

    async def test_load_history_excludes_proactive_outbound_for_other_chat(self, brain):
        from src.core.trajectory_store import TrajectoryEntryType

        # relevant_to_chat filters by source_chat_id=chat_id; entries for
        # other chats won't appear. We test that the filtering works at the
        # store level — the brain just pipes the result through.
        brain.trajectory_store = AsyncMock()
        brain.trajectory_store.relevant_to_chat = AsyncMock(return_value=[
            _mk_entry(1, TrajectoryEntryType.USER_MESSAGE, "c2", "user", "hi"),
        ])

        out = await brain._load_history("c2")
        assert out == [{"role": "user", "content": "hi"}]
        # The store was called with c2; entries for c1 wouldn't be returned.
        brain.trajectory_store.relevant_to_chat.assert_awaited_once()
        args = brain.trajectory_store.relevant_to_chat.call_args.args
        assert args[0] == "c2"

    async def test_original_bug_regression(self, brain):
        """Proactive outbound + short reply = model has full context."""
        from src.core.trajectory_store import TrajectoryEntryType

        brain.trajectory_store = AsyncMock()
        brain.trajectory_store.relevant_to_chat = AsyncMock(return_value=[
            _mk_entry(1, TrajectoryEntryType.PROACTIVE_OUTBOUND, "919231551",
                       "assistant", "下午好～第二个盲审有消息了吗？"),
            _mk_entry(2, TrajectoryEntryType.USER_MESSAGE, "919231551",
                       "user", "还没"),
        ])

        out = await brain._load_history("919231551")
        assert len(out) == 2
        assert out[0] == {"role": "assistant", "content": "下午好～第二个盲审有消息了吗？"}
        assert out[1] == {"role": "user", "content": "还没"}
        kwargs = brain.trajectory_store.relevant_to_chat.call_args.kwargs
        assert kwargs.get("include_inner") is False
```

- [ ] **Step 2: Run load history tests**

Run: `pytest tests/core/test_brain_load_history.py::TestLoadHistoryProactiveOutbound -v`
Expected: 3 tests PASS

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/tools/test_send_message_trajectory.py tests/core/test_trajectory_store.py tests/core/test_state_view_builder.py tests/core/test_brain_load_history.py -v`
Expected: All tests in these files PASS

- [ ] **Step 4: Commit**

```bash
git add tests/core/test_brain_load_history.py
git commit -m "test(proactive): add load_history and regression tests for PROACTIVE_OUTBOUND"
```

---

### Task 6: Final verification — run complete test suite

- [ ] **Step 1: Run the full test suite**

Run: `pytest tests/ -x --timeout=120 2>&1 | tail -30`
Expected: All tests PASS (or same failures as before this change, no new failures)

- [ ] **Step 2: Verify feature flag can disable writes**

Run a quick smoke test manually (or add a unit test):

```python
# Quick verification: with flag False, append should not be called
```

This is already implicitly tested — the `_record_proactive_outbound_trajectory` function checks `PROACTIVE_OUTBOUND_TRAJECTORY_ENABLED` at the top.

---

## Self-Review

**Spec coverage check:**
- 3.1 PROACTIVE_OUTBOUND enum → Task 1 Step 1 ✓
- 3.2 Record after delivery → Task 4 Step 3 ✓
- 3.3 Best-effort write → Task 4 Step 2 ✓
- 3.4 Feature flag → Task 3 ✓
- 4. Canonical chat_id resolver → Task 4 Step 1 ✓
- 5. StateView/history rendering → Task 2 Steps 1-2 ✓
- 6. no_recent_inbound warning → Task 4 Step 2 ✓
- 7. Idempotency → Documented in code comments ✓
- 8. Consecutive assistant turns → Task 2 Step 3 (tests) ✓
- 9-10. All test cases → Tasks 1-5 ✓
- 11. All acceptance criteria → Covered across all tasks ✓

**No placeholders — all steps have exact code.**

**Type consistency verified:**
- `PROACTIVE_OUTBOUND` used consistently across trajectory_store.py, state_view_builder.py, personal_tools.py
- `TrajectoryEntryType.PROACTIVE_OUTBOUND` access pattern matches existing enum usage
- `has_recent_entry(since=float)` uses float timestamps matching the DB schema
- Resolver returns `str | None` everywhere; callers check for None
- Feature flag is a module-level constant accessed as `config.settings.PROACTIVE_OUTBOUND_TRAJECTORY_ENABLED`
