"""ActionRegistry 和 SenseContext 测试。"""
import pytest
from datetime import datetime, timezone
from src.core.heartbeat import HeartbeatAction, ActionRegistry, SenseContext


class FakeFastAction(HeartbeatAction):
    name = "fake_fast"
    description = "快心跳 action"
    beat_types = ["fast"]
    async def execute(self, ctx, brain, bot): pass


class FakeSlowAction(HeartbeatAction):
    name = "fake_slow"
    description = "慢心跳 action"
    beat_types = ["slow"]
    async def execute(self, ctx, brain, bot): pass


class FakeBothAction(HeartbeatAction):
    name = "fake_both"
    description = "快慢都有"
    beat_types = ["fast", "slow"]
    async def execute(self, ctx, brain, bot): pass


@pytest.fixture
def registry():
    r = ActionRegistry()
    r.register(FakeFastAction())
    r.register(FakeSlowAction())
    r.register(FakeBothAction())
    return r


class TestActionRegistry:
    def test_get_for_fast_returns_fast_and_both(self, registry):
        names = {a.name for a in registry.get_for_beat("fast")}
        assert names == {"fake_fast", "fake_both"}

    def test_get_for_slow_returns_slow_and_both(self, registry):
        names = {a.name for a in registry.get_for_beat("slow")}
        assert names == {"fake_slow", "fake_both"}

    def test_get_by_name_found(self, registry):
        assert registry.get_by_name("fake_fast").name == "fake_fast"

    def test_get_by_name_not_found(self, registry):
        assert registry.get_by_name("nonexistent") is None

    def test_as_descriptions_includes_name_and_description(self, registry):
        descs = registry.as_descriptions("fast")
        assert any(d["name"] == "fake_fast" for d in descs)
        assert all("description" in d for d in descs)

    def test_as_descriptions_excludes_wrong_beat_type(self, registry):
        descs = registry.as_descriptions("fast")
        assert not any(d["name"] == "fake_slow" for d in descs)


class TestSenseContext:
    def test_dataclass_instantiation(self):
        ctx = SenseContext(
            beat_type="fast",
            now=datetime.now(timezone.utc),
            last_interaction=None,
            silence_hours=0.0,
            user_facts_summary="",
            recent_memory_summary="",
            chat_id="c1",
        )
        assert ctx.beat_type == "fast"
        assert ctx.chat_id == "c1"
        assert ctx.top_interests_summary == "（暂无明显兴趣）"
