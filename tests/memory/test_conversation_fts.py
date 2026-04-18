"""Tests for FTS5 full-text search on the legacy conversations table.

v2.0 Step 2h: ``ConversationMemory.append`` / ``append_to_session`` no
longer write to the ``conversations`` table (data goes to
TrajectoryStore). The FTS index + ``search_history`` API remain
operational over pre-existing rows (and the Step 2e migration legacy
data in production). Tests here seed the conversations table directly
via SQL — they validate the *search* path, not the *sync on write* path
(which is dead code). Step 3 drops the conversations table entirely and
removes these tests.
"""

import pytest
from pathlib import Path
from datetime import datetime, timezone

from src.memory.conversation import ConversationMemory, _cjk_tokenize


@pytest.fixture
async def memory(tmp_path):
    db_path = tmp_path / "test.db"
    m = ConversationMemory(db_path)
    await m.init_db()
    yield m
    await m.close()


async def _legacy_insert_with_fts(
    memory,
    chat_id,
    role,
    content,
    *,
    session_id=None,
    ts=None,
):
    """Insert a row into conversations + sync it to FTS, simulating the
    pre-2h auto-sync path that ``append`` used to perform."""
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()
    cursor = await memory._db.execute(
        "INSERT INTO conversations (chat_id, role, content, timestamp, session_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (chat_id, role, content, ts, session_id),
    )
    await memory._db.commit()
    if cursor.lastrowid:
        await memory._db.execute(
            "INSERT INTO conversations_fts(rowid, content) VALUES (?, ?)",
            (cursor.lastrowid, _cjk_tokenize(content)),
        )
        await memory._db.commit()


class TestFTS5Schema:
    async def test_fts_table_created(self, memory):
        async with memory._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='conversations_fts'"
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None


class TestSearchHistoryOverLegacyRows:
    async def test_single_row_search(self, memory):
        await _legacy_insert_with_fts(memory, "chat1", "user", "今天天气很好，适合出去玩")
        results = await memory.search_history("天气")
        assert len(results) == 1

    async def test_multiple_rows_filtered(self, memory):
        await _legacy_insert_with_fts(memory, "chat1", "user", "Python 是最好的编程语言")
        await _legacy_insert_with_fts(memory, "chat1", "assistant", "我同意，Python 确实很强大")
        await _legacy_insert_with_fts(memory, "chat1", "user", "Java 也不错")
        results = await memory.search_history("Python")
        assert len(results) == 2

    async def test_basic_search(self, memory):
        await _legacy_insert_with_fts(memory, "chat1", "user", "我昨天买了一个新键盘")
        await _legacy_insert_with_fts(memory, "chat1", "assistant", "什么键盘？机械的吗？")
        await _legacy_insert_with_fts(memory, "chat1", "user", "是的，Cherry 轴的")
        results = await memory.search_history("键盘")
        assert len(results) >= 1
        assert any("键盘" in r["content"] for r in results)

    async def test_search_returns_metadata(self, memory):
        await _legacy_insert_with_fts(memory, "chat1", "user", "明天要去看牙医")
        results = await memory.search_history("牙医")
        assert len(results) == 1
        r = results[0]
        assert r["chat_id"] == "chat1"
        assert r["role"] == "user"
        assert "牙医" in r["content"]
        assert r["timestamp"] is not None

    async def test_search_with_chat_id_filter(self, memory):
        await _legacy_insert_with_fts(memory, "chat1", "user", "我喜欢苹果")
        await _legacy_insert_with_fts(memory, "chat2", "user", "我也喜欢苹果")
        results = await memory.search_history("苹果", chat_id="chat1")
        assert len(results) == 1
        assert results[0]["chat_id"] == "chat1"

    async def test_search_no_results(self, memory):
        await _legacy_insert_with_fts(memory, "chat1", "user", "hello world")
        results = await memory.search_history("不存在的内容xyz")
        assert results == []

    async def test_search_empty_query(self, memory):
        results = await memory.search_history("")
        assert results == []

    async def test_search_with_limit(self, memory):
        for i in range(20):
            await _legacy_insert_with_fts(
                memory, "chat1", "user", f"测试消息 {i} 包含关键词",
            )
        results = await memory.search_history("关键词", limit=5)
        assert len(results) <= 5

    async def test_search_context(self, memory):
        await _legacy_insert_with_fts(memory, "chat1", "user", "我们来讨论一下项目进度")
        await _legacy_insert_with_fts(memory, "chat1", "assistant", "好的，目前完成了80%")
        await _legacy_insert_with_fts(memory, "chat1", "user", "还剩哪些任务？")
        results = await memory.search_history("80%")
        assert len(results) >= 1
        r = results[0]
        assert isinstance(r.get("context"), list)

    async def test_search_session_messages(self, memory):
        await _legacy_insert_with_fts(
            memory, "chat1", "user", "这是 session 中的消息",
            session_id="session1",
        )
        results = await memory.search_history("session 中的消息")
        assert len(results) >= 1
        assert results[0]["session_id"] == "session1"


class TestFTS5Migration:
    async def test_backfill_existing_data(self, tmp_path):
        """Init-time backfill indexes pre-existing rows that were written
        before the FTS table existed. This path is still exercised by
        real-world DBs that include migrated Step-2e rows."""
        import aiosqlite

        db_path = tmp_path / "test_migrate.db"

        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                );
            """)
            await db.execute(
                "INSERT INTO conversations (chat_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                ("chat1", "user", "历史数据应该被回填", "2024-01-01T00:00:00"),
            )
            await db.execute(
                "INSERT INTO conversations (chat_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                ("chat1", "assistant", "是的没错", "2024-01-01T00:01:00"),
            )
            await db.commit()

        m = ConversationMemory(db_path)
        await m.init_db()

        results = await m.search_history("回填")
        assert len(results) == 1
        assert "历史数据" in results[0]["content"]

        await m.close()
