"""interest_topics 表及 ConversationMemory 兴趣方法集成测试。"""

import pytest

from src.memory.conversation import ConversationMemory


@pytest.fixture
async def memory(tmp_path):
    m = ConversationMemory(tmp_path / "test.db")
    await m.init_db()
    yield m
    await m.close()


@pytest.mark.asyncio
class TestConversationInterests:
    async def test_bump_interest_inserts_new(self, memory):
        await memory.bump_interest("c1", "Python")
        results = await memory.get_top_interests("c1")
        assert len(results) == 1
        assert results[0]["topic"] == "Python"
        assert results[0]["weight"] == 1.0

    async def test_bump_interest_accumulates(self, memory):
        await memory.bump_interest("c1", "Python", 1.0)
        await memory.bump_interest("c1", "Python", 2.0)
        results = await memory.get_top_interests("c1")
        assert results[0]["weight"] == 3.0

    async def test_get_top_interests_sorted_by_weight(self, memory):
        await memory.bump_interest("c1", "摄影", 1.0)
        await memory.bump_interest("c1", "机器学习", 2.0)
        await memory.bump_interest("c1", "Python", 3.0)
        results = await memory.get_top_interests("c1")
        assert [item["topic"] for item in results] == ["Python", "机器学习", "摄影"]

    async def test_get_top_interests_limit_respected(self, memory):
        for index in range(5):
            await memory.bump_interest("c1", f"topic-{index}", index + 1)
        results = await memory.get_top_interests("c1", limit=3)
        assert len(results) == 3

    async def test_decay_multiplies_all_weights(self, memory):
        await memory.bump_interest("c1", "Python", 2.0)
        await memory.bump_interest("c1", "摄影", 4.0)
        await memory.decay_interests("c1", factor=0.5)
        results = await memory.get_top_interests("c1")
        weights = {item["topic"]: item["weight"] for item in results}
        assert weights["Python"] == pytest.approx(1.0)
        assert weights["摄影"] == pytest.approx(2.0)

    async def test_interests_isolated_by_chat_id(self, memory):
        await memory.bump_interest("c1", "Python", 1.0)
        await memory.bump_interest("c2", "摄影", 2.0)
        c1_results = await memory.get_top_interests("c1")
        c2_results = await memory.get_top_interests("c2")
        assert [item["topic"] for item in c1_results] == ["Python"]
        assert [item["topic"] for item in c2_results] == ["摄影"]
