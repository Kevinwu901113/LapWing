"""Tests for FTS5 full-text search on conversation history."""

import pytest
from pathlib import Path

from src.memory.conversation import ConversationMemory


@pytest.fixture
async def memory(tmp_path):
    """创建临时数据库的 ConversationMemory 实例。"""
    db_path = tmp_path / "test.db"
    m = ConversationMemory(db_path)
    await m.init_db()
    yield m
    await m.close()


class TestFTS5Schema:
    async def test_fts_table_created(self, memory):
        """FTS5 虚拟表应在 init_db 时创建。"""
        async with memory._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='conversations_fts'"
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None


class TestFTS5AutoSync:
    async def test_insert_syncs_to_fts(self, memory):
        """通过 append 插入的消息应自动同步到 FTS 索引。"""
        await memory.append("chat1", "user", "今天天气很好，适合出去玩")

        results = await memory.search_history("天气")
        assert len(results) == 1

    async def test_multiple_inserts_sync(self, memory):
        """多条消息插入后 FTS 索引应正确。"""
        await memory.append("chat1", "user", "Python 是最好的编程语言")
        await memory.append("chat1", "assistant", "我同意，Python 确实很强大")
        await memory.append("chat1", "user", "Java 也不错")

        results = await memory.search_history("Python")
        assert len(results) == 2  # 两条包含 Python


class TestSearchHistory:
    async def test_basic_search(self, memory):
        """基本关键词搜索。"""
        await memory.append("chat1", "user", "我昨天买了一个新键盘")
        await memory.append("chat1", "assistant", "什么键盘？机械的吗？")
        await memory.append("chat1", "user", "是的，Cherry 轴的")

        results = await memory.search_history("键盘")
        assert len(results) >= 1
        assert any("键盘" in r["content"] for r in results)

    async def test_search_returns_metadata(self, memory):
        """搜索结果应包含完整元数据。"""
        await memory.append("chat1", "user", "明天要去看牙医")

        results = await memory.search_history("牙医")
        assert len(results) == 1
        r = results[0]
        assert r["chat_id"] == "chat1"
        assert r["role"] == "user"
        assert "牙医" in r["content"]
        assert r["timestamp"] is not None

    async def test_search_with_chat_id_filter(self, memory):
        """限定 chat_id 的搜索应只返回该对话的结果。"""
        await memory.append("chat1", "user", "我喜欢苹果")
        await memory.append("chat2", "user", "我也喜欢苹果")

        results = await memory.search_history("苹果", chat_id="chat1")
        assert len(results) == 1
        assert results[0]["chat_id"] == "chat1"

    async def test_search_no_results(self, memory):
        """搜索不到时应返回空列表。"""
        await memory.append("chat1", "user", "hello world")
        results = await memory.search_history("不存在的内容xyz")
        assert results == []

    async def test_search_empty_query(self, memory):
        """空查询应返回空列表。"""
        results = await memory.search_history("")
        assert results == []

    async def test_search_with_limit(self, memory):
        """限制返回条数。"""
        for i in range(20):
            await memory.append("chat1", "user", f"测试消息 {i} 包含关键词")

        results = await memory.search_history("关键词", limit=5)
        assert len(results) <= 5

    async def test_search_context(self, memory):
        """搜索结果应包含上下文消息。"""
        await memory.append("chat1", "user", "我们来讨论一下项目进度")
        await memory.append("chat1", "assistant", "好的，目前完成了80%")
        await memory.append("chat1", "user", "还剩哪些任务？")

        results = await memory.search_history("80%")
        assert len(results) >= 1
        # 上下文应包含前后消息
        r = results[0]
        assert isinstance(r.get("context"), list)

    async def test_search_session_messages(self, memory):
        """session 消息也应被 FTS 索引。"""
        await memory.append_to_session("chat1", "session1", "user", "这是 session 中的消息")

        results = await memory.search_history("session 中的消息")
        assert len(results) >= 1
        assert results[0]["session_id"] == "session1"


class TestFTS5Migration:
    async def test_backfill_existing_data(self, tmp_path):
        """回填已有数据到 FTS 索引。"""
        import aiosqlite

        db_path = tmp_path / "test_migrate.db"

        # 手动创建表并插入数据（模拟旧版本数据库）
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

        # 用 ConversationMemory 初始化（触发 FTS 创建 + 回填）
        m = ConversationMemory(db_path)
        await m.init_db()

        # 搜索历史数据
        results = await m.search_history("回填")
        assert len(results) == 1
        assert "历史数据" in results[0]["content"]

        await m.close()
