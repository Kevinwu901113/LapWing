"""环境知识工具单元测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.ambient.models import AmbientEntry
from src.tools.ambient_tools import (
    check_ambient_knowledge_executor,
    manage_interest_profile_executor,
    prepare_ambient_knowledge_executor,
)
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _future_iso(hours: int = 6) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def _make_entry(
    key: str = "interest:MLB棒球",
    category: str = "MLB棒球",
    topic: str = "道奇 vs 巨人",
    summary: str = "道奇 5-3 胜巨人",
) -> AmbientEntry:
    return AmbientEntry(
        key=key,
        category=category,
        topic=topic,
        data="{}",
        summary=summary,
        fetched_at=_now_iso(),
        expires_at=_future_iso(),
        source="test",
        confidence=0.9,
    )


def _ctx(engine=None, ambient_store=None, interest_profile=None):
    services: dict = {}
    if engine is not None:
        services["research_engine"] = engine
    if ambient_store is not None:
        services["ambient_store"] = ambient_store
    if interest_profile is not None:
        services["interest_profile"] = interest_profile
    return ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
        services=services,
    )


def _mock_research_result():
    result = MagicMock()
    result.answer = "道奇 5-3 胜巨人"
    result.evidence = []
    result.confidence = 0.85
    result.search_backend_used = ["tavily"]
    return result


def _mock_engine(result=None):
    engine = MagicMock()
    engine.research = AsyncMock(return_value=result or _mock_research_result())
    return engine


# ═══════════════════════════════════════════════════════════════════
# prepare_ambient_knowledge
# ═══════════════════════════════════════════════════════════════════

class TestPrepareAmbientKnowledge:
    async def test_missing_topic(self) -> None:
        result = await prepare_ambient_knowledge_executor(
            ToolExecutionRequest(name="prepare_ambient_knowledge", arguments={"topic": ""}),
            _ctx(engine=MagicMock(), ambient_store=AsyncMock()),
        )
        assert result.success is False
        assert "topic" in result.payload["error"]

    async def test_missing_engine(self) -> None:
        result = await prepare_ambient_knowledge_executor(
            ToolExecutionRequest(name="prepare_ambient_knowledge", arguments={"topic": "道奇"}),
            _ctx(engine=None, ambient_store=AsyncMock()),
        )
        assert result.success is False
        assert "research_engine" in result.payload["error"]

    async def test_missing_store(self) -> None:
        result = await prepare_ambient_knowledge_executor(
            ToolExecutionRequest(name="prepare_ambient_knowledge", arguments={"topic": "道奇"}),
            _ctx(engine=_mock_engine(), ambient_store=None),
        )
        assert result.success is False
        assert "ambient_store" in result.payload["error"]

    async def test_success_caches_result(self) -> None:
        store = AsyncMock()
        store.put = AsyncMock()
        engine = _mock_engine()

        result = await prepare_ambient_knowledge_executor(
            ToolExecutionRequest(
                name="prepare_ambient_knowledge",
                arguments={"topic": "道奇今天比赛结果", "category": "MLB棒球"},
            ),
            _ctx(engine=engine, ambient_store=store),
        )
        assert result.success is True
        assert result.payload["cached"] is True
        assert result.payload["category"] == "MLB棒球"
        store.put.assert_awaited_once()
        engine.research.assert_awaited_once()

    async def test_low_confidence_without_evidence_not_cached(self) -> None:
        store = AsyncMock()
        store.put = AsyncMock()
        research_result = _mock_research_result()
        research_result.answer = "没有找到可靠信息"
        research_result.confidence = 0.3
        research_result.evidence = []
        engine = _mock_engine(research_result)

        result = await prepare_ambient_knowledge_executor(
            ToolExecutionRequest(
                name="prepare_ambient_knowledge",
                arguments={"topic": "道奇小熊4月25日先发投手", "category": "MLB棒球"},
            ),
            _ctx(engine=engine, ambient_store=store),
        )

        assert result.success is True
        assert result.payload["cached"] is False
        assert result.reason == "low_confidence_no_evidence"
        store.put.assert_not_awaited()

    async def test_custom_ttl(self) -> None:
        store = AsyncMock()
        store.put = AsyncMock()
        engine = _mock_engine()

        result = await prepare_ambient_knowledge_executor(
            ToolExecutionRequest(
                name="prepare_ambient_knowledge",
                arguments={"topic": "天气", "ttl_hours": 2},
            ),
            _ctx(engine=engine, ambient_store=store),
        )
        assert result.success is True
        assert "expires_at" in result.payload

    async def test_research_failure(self) -> None:
        engine = MagicMock()
        engine.research = AsyncMock(side_effect=RuntimeError("network error"))
        store = AsyncMock()

        result = await prepare_ambient_knowledge_executor(
            ToolExecutionRequest(
                name="prepare_ambient_knowledge",
                arguments={"topic": "道奇"},
            ),
            _ctx(engine=engine, ambient_store=store),
        )
        assert result.success is False
        assert "搜索失败" in result.payload["error"]


# ═══════════════════════════════════════════════════════════════════
# check_ambient_knowledge
# ═══════════════════════════════════════════════════════════════════

class TestCheckAmbientKnowledge:
    async def test_missing_params(self) -> None:
        result = await check_ambient_knowledge_executor(
            ToolExecutionRequest(name="check_ambient_knowledge", arguments={}),
            _ctx(ambient_store=AsyncMock()),
        )
        assert result.success is False
        assert "至少填一个" in result.payload["error"]

    async def test_missing_store(self) -> None:
        result = await check_ambient_knowledge_executor(
            ToolExecutionRequest(
                name="check_ambient_knowledge",
                arguments={"category": "MLB棒球"},
            ),
            _ctx(ambient_store=None),
        )
        assert result.success is False
        assert "ambient_store" in result.payload["error"]

    async def test_category_match(self) -> None:
        store = AsyncMock()
        store.get_by_category = AsyncMock(return_value=[_make_entry()])

        result = await check_ambient_knowledge_executor(
            ToolExecutionRequest(
                name="check_ambient_knowledge",
                arguments={"category": "MLB棒球"},
            ),
            _ctx(ambient_store=store),
        )
        assert result.success is True
        assert len(result.payload["entries"]) == 1
        assert result.payload["entries"][0]["summary"] == "道奇 5-3 胜巨人"

    async def test_no_match(self) -> None:
        store = AsyncMock()
        store.get_by_category = AsyncMock(return_value=[])
        store.get_all_fresh = AsyncMock(return_value=[])

        result = await check_ambient_knowledge_executor(
            ToolExecutionRequest(
                name="check_ambient_knowledge",
                arguments={"topic": "不存在的话题"},
            ),
            _ctx(ambient_store=store),
        )
        assert result.success is True
        assert result.payload["entries"] == []

    async def test_topic_fuzzy_match(self) -> None:
        store = AsyncMock()
        store.get_by_category = AsyncMock(return_value=[])
        store.get_all_fresh = AsyncMock(return_value=[_make_entry(topic="道奇 vs 巨人")])

        result = await check_ambient_knowledge_executor(
            ToolExecutionRequest(
                name="check_ambient_knowledge",
                arguments={"topic": "道奇"},
            ),
            _ctx(ambient_store=store),
        )
        assert result.success is True
        assert len(result.payload["entries"]) == 1


# ═══════════════════════════════════════════════════════════════════
# manage_interest_profile
# ═══════════════════════════════════════════════════════════════════

class TestManageInterestProfile:
    @pytest.fixture()
    def profile(self, tmp_path: Path):
        from src.ambient.preparation_engine import InterestProfile
        p = tmp_path / "kevin_interests.md"
        # 写入一个最小的测试文件
        p.write_text(
            "# Kevin 兴趣画像\n"
            "<!-- 最后更新：2026-04-22 -->\n\n"
            "## 高优先级（每日关注）\n\n"
            "### MLB棒球\n"
            "- 具体关注：道奇队\n"
            "- 频率：daily\n"
            "- 典型时段：evening\n"
            "- 来源：显式声明\n",
            encoding="utf-8",
        )
        return InterestProfile(p)

    async def test_invalid_action(self) -> None:
        result = await manage_interest_profile_executor(
            ToolExecutionRequest(
                name="manage_interest_profile",
                arguments={"action": "invalid"},
            ),
            _ctx(interest_profile=MagicMock()),
        )
        assert result.success is False

    async def test_missing_profile(self) -> None:
        result = await manage_interest_profile_executor(
            ToolExecutionRequest(
                name="manage_interest_profile",
                arguments={"action": "view"},
            ),
            _ctx(interest_profile=None),
        )
        assert result.success is False
        assert "interest_profile" in result.payload["error"]

    async def test_view(self, profile) -> None:
        result = await manage_interest_profile_executor(
            ToolExecutionRequest(
                name="manage_interest_profile",
                arguments={"action": "view"},
            ),
            _ctx(interest_profile=profile),
        )
        assert result.success is True
        assert result.payload["count"] == 1
        assert result.payload["interests"][0]["name"] == "MLB棒球"

    async def test_add(self, profile) -> None:
        result = await manage_interest_profile_executor(
            ToolExecutionRequest(
                name="manage_interest_profile",
                arguments={
                    "action": "add",
                    "name": "天气",
                    "priority": "high",
                    "details": "洛杉矶",
                    "frequency": "daily",
                    "typical_time": "morning",
                },
            ),
            _ctx(interest_profile=profile),
        )
        assert result.success is True
        assert result.payload["added"] == "天气"
        # 验证实际写入
        loaded = profile.load()
        assert len(loaded) == 2
        assert any(i.name == "天气" for i in loaded)

    async def test_add_duplicate_fails(self, profile) -> None:
        result = await manage_interest_profile_executor(
            ToolExecutionRequest(
                name="manage_interest_profile",
                arguments={"action": "add", "name": "MLB棒球"},
            ),
            _ctx(interest_profile=profile),
        )
        assert result.success is False
        assert "已存在" in result.payload["error"]

    async def test_update(self, profile) -> None:
        result = await manage_interest_profile_executor(
            ToolExecutionRequest(
                name="manage_interest_profile",
                arguments={
                    "action": "update",
                    "name": "MLB棒球",
                    "priority": "medium",
                    "details": "道奇队、Angels",
                },
            ),
            _ctx(interest_profile=profile),
        )
        assert result.success is True
        loaded = profile.load()
        mlb = next(i for i in loaded if i.name == "MLB棒球")
        assert mlb.priority == "medium"
        assert "Angels" in mlb.details

    async def test_update_not_found(self, profile) -> None:
        result = await manage_interest_profile_executor(
            ToolExecutionRequest(
                name="manage_interest_profile",
                arguments={"action": "update", "name": "不存在"},
            ),
            _ctx(interest_profile=profile),
        )
        assert result.success is False
        assert "未找到" in result.payload["error"]

    async def test_deactivate(self, profile) -> None:
        result = await manage_interest_profile_executor(
            ToolExecutionRequest(
                name="manage_interest_profile",
                arguments={"action": "deactivate", "name": "MLB棒球"},
            ),
            _ctx(interest_profile=profile),
        )
        assert result.success is True
        loaded = profile.load()
        # deactivated interests are still saved but marked inactive
        content = profile.path.read_text(encoding="utf-8")
        assert "已停用" in content

    async def test_missing_name_for_add(self, profile) -> None:
        result = await manage_interest_profile_executor(
            ToolExecutionRequest(
                name="manage_interest_profile",
                arguments={"action": "add", "name": ""},
            ),
            _ctx(interest_profile=profile),
        )
        assert result.success is False
        assert "name" in result.payload["error"]
