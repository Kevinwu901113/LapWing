"""Unit tests for ConversationMemory → TrajectoryStore dual-write.

Covers Blueprint v2.0 Step 2f contract:
  1. append() writes both the legacy conversations row and a trajectory entry
  2. append_to_session() mirrors with legacy_session_id in trajectory payload
  3. Role → entry_type / actor mapping (user → USER_MESSAGE/user;
     assistant → ASSISTANT_TEXT/lapwing; other → trajectory skip + warn)
  4. trajectory.append failure does NOT abort the legacy write
  5. set_trajectory(None) disables dual-write without affecting the old path
  6. Metadata passthrough: adapter / source / user_id / session_id
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from src.core.trajectory_store import TrajectoryEntryType, TrajectoryStore
from src.logging.state_mutation_log import StateMutationLog
from src.memory.conversation import ConversationMemory


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
async def mutation_log(tmp_path):
    log = StateMutationLog(
        tmp_path / "mutation_log.db", logs_dir=tmp_path / "logs"
    )
    await log.init()
    yield log
    await log.close()


@pytest.fixture
async def trajectory(tmp_path, mutation_log):
    db_path = tmp_path / "shared.db"
    t = TrajectoryStore(db_path, mutation_log)
    await t.init()
    yield t
    await t.close()


@pytest.fixture
async def memory(tmp_path, trajectory):
    # Share the same DB file so we can diff rows side-by-side
    m = ConversationMemory(tmp_path / "shared.db")
    await m.init_db()
    m.set_trajectory(trajectory)
    yield m
    await m.close()


async def _count_conversations(db_path: Path) -> int:
    db = await aiosqlite.connect(db_path)
    try:
        async with db.execute("SELECT COUNT(*) FROM conversations") as cur:
            return (await cur.fetchone())[0]
    finally:
        await db.close()


# ── Dual-write basics ─────────────────────────────────────────────────

class TestDualWriteMapping:
    async def test_user_role_becomes_user_message_actor_user(
        self, memory, trajectory
    ):
        await memory.append("chat1", "user", "hi there")
        rows = await trajectory.recent(10)
        assert len(rows) == 1
        assert rows[0].entry_type == TrajectoryEntryType.USER_MESSAGE.value
        assert rows[0].actor == "user"
        assert rows[0].source_chat_id == "chat1"
        assert rows[0].content["text"] == "hi there"

    async def test_assistant_role_becomes_assistant_text_actor_lapwing(
        self, memory, trajectory
    ):
        await memory.append("chat1", "assistant", "ok")
        rows = await trajectory.recent(10)
        assert rows[0].entry_type == TrajectoryEntryType.ASSISTANT_TEXT.value
        assert rows[0].actor == "lapwing"
        assert rows[0].content["text"] == "ok"

    async def test_unknown_role_skips_trajectory_and_warns(
        self, memory, trajectory, caplog
    ):
        with caplog.at_level(logging.WARNING, logger="lapwing.memory.conversation"):
            await memory.append("chat1", "system", "internal note")
        rows = await trajectory.recent(10)
        assert rows == []
        assert any("trajectory mirror skipped" in r.message for r in caplog.records)


class TestDualWriteConsistency:
    async def test_conversations_and_trajectory_grow_together(
        self, memory, trajectory, tmp_path
    ):
        db_path = tmp_path / "shared.db"
        assert await _count_conversations(db_path) == 0
        assert await trajectory.recent(100) == []

        await memory.append("chat1", "user", "a")
        await memory.append("chat1", "assistant", "b")
        await memory.append("chat1", "user", "c")

        assert await _count_conversations(db_path) == 3
        assert len(await trajectory.recent(100)) == 3

    async def test_content_text_identical_across_both_stores(
        self, memory, trajectory, tmp_path
    ):
        db_path = tmp_path / "shared.db"
        texts = ["测试 CJK", "emoji 🎉", "multi\nline"]
        for t in texts:
            await memory.append("chat1", "user", t)

        traj_rows = await trajectory.recent(100)
        traj_texts = [r.content["text"] for r in traj_rows]

        db = await aiosqlite.connect(db_path)
        try:
            async with db.execute(
                "SELECT content FROM conversations ORDER BY id ASC"
            ) as cur:
                legacy_texts = [row[0] async for row in cur]
        finally:
            await db.close()

        assert legacy_texts == texts
        assert traj_texts == texts


class TestMetadataPassthrough:
    async def test_adapter_source_user_id_land_in_trajectory_payload(
        self, memory, trajectory
    ):
        await memory.append(
            "chat1", "user", "hello",
            channel="desktop", source="desktop",
            actor_id="kevin_device_a",
        )
        row = (await trajectory.recent(1))[0]
        assert row.content["adapter"] == "desktop"
        assert row.content["source"] == "desktop"
        assert row.content["user_id"] == "kevin_device_a"

    async def test_append_to_session_records_legacy_session_id(
        self, memory, trajectory
    ):
        await memory.append_to_session(
            "chat1", "sess_abc", "assistant", "done", channel="qq"
        )
        rows = await trajectory.recent(1)
        assert rows[0].entry_type == TrajectoryEntryType.ASSISTANT_TEXT.value
        assert rows[0].source_chat_id == "chat1"
        assert rows[0].content["legacy_session_id"] == "sess_abc"


class TestFailureIsolation:
    async def test_trajectory_failure_does_not_abort_legacy_write(
        self, memory, trajectory, tmp_path, caplog
    ):
        db_path = tmp_path / "shared.db"
        # Force the trajectory append to raise
        trajectory.append = AsyncMock(side_effect=RuntimeError("simulated"))
        with caplog.at_level(logging.WARNING, logger="lapwing.memory.conversation"):
            await memory.append("chat1", "user", "payload")
        assert await _count_conversations(db_path) == 1
        assert any(
            "trajectory mirror write failed" in r.message for r in caplog.records
        )


class TestConsciousnessRemap:
    async def test_consciousness_assistant_becomes_inner_thought_lapwing(
        self, memory, trajectory
    ):
        await memory.append("__consciousness__", "assistant", "pondering")
        row = (await trajectory.recent(1))[0]
        assert row.entry_type == TrajectoryEntryType.INNER_THOUGHT.value
        assert row.source_chat_id == "__inner__"
        assert row.actor == "lapwing"
        assert row.content["text"] == "pondering"
        assert row.content["trigger_type"] == "live_dual_write"

    async def test_consciousness_user_becomes_inner_thought_system(
        self, memory, trajectory
    ):
        await memory.append("__consciousness__", "user", "tick prompt")
        row = (await trajectory.recent(1))[0]
        assert row.entry_type == TrajectoryEntryType.INNER_THOUGHT.value
        assert row.source_chat_id == "__inner__"
        assert row.actor == "system"

    async def test_real_chat_unaffected_by_consciousness_branch(
        self, memory, trajectory
    ):
        await memory.append("919231551", "user", "real user message")
        row = (await trajectory.recent(1))[0]
        assert row.entry_type == TrajectoryEntryType.USER_MESSAGE.value
        assert row.source_chat_id == "919231551"
        assert row.actor == "user"


class TestOptionalWiring:
    async def test_no_trajectory_wired_falls_back_to_legacy_only(self, tmp_path):
        m = ConversationMemory(tmp_path / "legacy_only.db")
        await m.init_db()
        try:
            # No set_trajectory call
            await m.append("chat1", "user", "solo")
            assert await _count_conversations(tmp_path / "legacy_only.db") == 1
        finally:
            await m.close()

    async def test_set_trajectory_none_disables_mirror(
        self, memory, trajectory
    ):
        memory.set_trajectory(None)
        await memory.append("chat1", "user", "silent")
        assert await trajectory.recent(10) == []
