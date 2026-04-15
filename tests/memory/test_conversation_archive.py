import pytest
from datetime import datetime, timedelta, timezone
from src.memory.conversation import ConversationMemory


@pytest.fixture
async def memory(tmp_path):
    m = ConversationMemory(db_path=tmp_path / "test.db")
    await m.init_db()
    return m


class TestArchiveTiers:
    async def test_get_active_returns_recent(self, memory):
        """Messages from last 1 day should be returned."""
        await memory.append("chat1", "user", "recent message")
        results = await memory.get_active("chat1", limit=30)
        assert len(results) >= 1
        assert any("recent message" in r.get("content", "") for r in results)

    async def test_get_active_limit(self, memory):
        for i in range(10):
            await memory.append("chat1", "user", f"msg {i}")
        results = await memory.get_active("chat1", limit=5)
        assert len(results) <= 5

    async def test_search_deep_archive_keyword(self, memory):
        """Keyword search in deep archive (>7 days)."""
        # Insert a message with old timestamp directly via SQL
        old_ts = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
        await memory._db.execute(
            "INSERT INTO conversations (chat_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
            ("chat1", "user", "ancient conversation about dragons", old_ts),
        )
        await memory._db.commit()
        results = await memory.search_deep_archive("chat1", "dragons", limit=10)
        assert len(results) >= 1
        assert any("dragons" in r.get("content", "") for r in results)

    async def test_search_deep_archive_no_match(self, memory):
        results = await memory.search_deep_archive("chat1", "nonexistent_xyz", limit=10)
        assert results == []
