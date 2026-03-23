"""discoveries 表及新增 ConversationMemory 方法的集成测试。"""
import pytest
from src.memory.conversation import ConversationMemory


@pytest.fixture
async def memory(tmp_path):
    m = ConversationMemory(tmp_path / "test.db")
    await m.init_db()
    yield m
    await m.close()


class TestDiscoveries:
    async def test_add_and_retrieve_unshared(self, memory):
        await memory.add_discovery("c1", "test", "标题", "摘要", "http://x.com")
        results = await memory.get_unshared_discoveries("c1", limit=10)
        assert len(results) == 1
        assert results[0]["title"] == "标题"
        assert results[0]["shared_at"] is None

    async def test_mark_shared_removes_from_unshared(self, memory):
        await memory.add_discovery("c1", "test", "标题", "摘要", None)
        results = await memory.get_unshared_discoveries("c1", limit=10)
        await memory.mark_discovery_shared(results[0]["id"])
        after = await memory.get_unshared_discoveries("c1", limit=10)
        assert len(after) == 0

    async def test_limit_is_respected(self, memory):
        for i in range(5):
            await memory.add_discovery("c1", "test", f"标题{i}", "摘要", None)
        results = await memory.get_unshared_discoveries("c1", limit=3)
        assert len(results) == 3

    async def test_discoveries_isolated_by_chat_id(self, memory):
        await memory.add_discovery("c1", "test", "c1内容", "摘要", None)
        await memory.add_discovery("c2", "test", "c2内容", "摘要", None)
        c1 = await memory.get_unshared_discoveries("c1", limit=10)
        assert len(c1) == 1 and c1[0]["title"] == "c1内容"

    async def test_url_can_be_none(self, memory):
        await memory.add_discovery("c1", "test", "标题", "摘要", None)
        results = await memory.get_unshared_discoveries("c1", limit=10)
        assert results[0]["url"] is None


class TestGetAllChatIds:
    async def test_empty_when_no_conversations(self, memory):
        result = await memory.get_all_chat_ids()
        assert result == []

    async def test_returns_distinct_ids(self, memory):
        await memory.append("c1", "user", "msg")
        await memory.append("c1", "user", "msg2")
        await memory.append("c2", "user", "msg")
        result = await memory.get_all_chat_ids()
        assert set(result) == {"c1", "c2"}


class TestGetLastInteraction:
    async def test_returns_none_when_no_messages(self, memory):
        result = await memory.get_last_interaction("c1")
        assert result is None

    async def test_returns_datetime_of_last_message(self, memory):
        await memory.append("c1", "user", "hello")
        from datetime import datetime
        result = await memory.get_last_interaction("c1")
        assert result is not None
        assert isinstance(result, datetime)
