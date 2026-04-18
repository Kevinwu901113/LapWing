"""Tests for legacy conversations-table archive APIs.

v2.0 Step 2h: ``ConversationMemory.append`` no longer writes to the
``conversations`` table (writes go to TrajectoryStore instead). The
legacy APIs (``get_active`` / ``search_deep_archive``) are still
functional over pre-existing rows — tests here populate the table with
direct SQL so they stay meaningful without depending on the removed
write path. Step 3 drops the conversations table entirely and removes
these tests along with the underlying APIs.
"""

import pytest
from datetime import datetime, timedelta, timezone
from src.memory.conversation import ConversationMemory


@pytest.fixture
async def memory(tmp_path):
    m = ConversationMemory(db_path=tmp_path / "test.db")
    await m.init_db()
    yield m
    await m.close()


async def _legacy_insert(memory, chat_id, role, content, *, ts=None):
    """Direct INSERT into the legacy conversations table — simulates
    pre-Step-2h rows (or migrated data) that the legacy read APIs still
    need to search over."""
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()
    await memory._db.execute(
        "INSERT INTO conversations (chat_id, role, content, timestamp) "
        "VALUES (?, ?, ?, ?)",
        (chat_id, role, content, ts),
    )
    await memory._db.commit()


class TestArchiveTiers:
    async def test_get_active_returns_recent(self, memory):
        """Messages from the last 1 day should be returned."""
        await _legacy_insert(memory, "chat1", "user", "recent message")
        results = await memory.get_active("chat1", limit=30)
        assert len(results) >= 1
        assert any("recent message" in r.get("content", "") for r in results)

    async def test_get_active_limit(self, memory):
        for i in range(10):
            await _legacy_insert(memory, "chat1", "user", f"msg {i}")
        results = await memory.get_active("chat1", limit=5)
        assert len(results) == 5

