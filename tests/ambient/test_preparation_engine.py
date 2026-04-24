"""PreparationEngine + InterestProfile 单元测试。"""

from __future__ import annotations

import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.ambient.models import AmbientEntry, Interest, PreparationStatus
from src.ambient.preparation_engine import InterestProfile, PreparationEngine


# ── fixtures ────────────────────────────────────────────────────────

SAMPLE_MD = textwrap.dedent("""\
    # Kevin 兴趣画像
    <!-- Lapwing维护。记录Kevin关注的信息领域，驱动准备系统的信息预取。 -->
    <!-- 最后更新：2026-04-22 -->

    ## 高优先级（每日关注）

    ### MLB棒球
    - 具体关注：道奇队、NL West赛区
    - 频率：daily（赛季中，4月-10月）
    - 典型时段：evening
    - 来源：显式声明
    - 备注：休赛期优先级降至低

    ### 天气
    - 具体关注：洛杉矶地区
    - 频率：daily
    - 典型时段：morning
    - 来源：基本常识

    ## 中优先级（定期关注）

    ### AI/LLM动态
    - 具体关注：新模型发布、重要研究进展
    - 频率：weekly
    - 典型时段：anytime
    - 来源：观察（频繁讨论相关话题）

    ## 低优先级（事件驱动）

    ### 课业/考试
    - 具体关注：大学课程相关截止日期
    - 频率：event_driven
    - 典型时段：anytime
    - 来源：对话中提及时激活
""")


@pytest.fixture()
def interest_path(tmp_path: Path) -> Path:
    p = tmp_path / "kevin_interests.md"
    p.write_text(SAMPLE_MD, encoding="utf-8")
    return p


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _future_iso(hours: int = 1) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def _past_iso(hours: int = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _make_entry(
    key: str = "test:key",
    category: str = "MLB棒球",
    topic: str = "道奇 vs 巨人",
    summary: str = "道奇 5-3 胜巨人",
    fetched_at: str | None = None,
    expires_at: str | None = None,
) -> AmbientEntry:
    return AmbientEntry(
        key=key,
        category=category,
        topic=topic,
        data="{}",
        summary=summary,
        fetched_at=fetched_at or _now_iso(),
        expires_at=expires_at or _future_iso(6),
        source="test",
        confidence=0.9,
    )


# ═══════════════════════════════════════════════════════════════════
# InterestProfile.load()
# ═══════════════════════════════════════════════════════════════════

class TestInterestProfileLoad:
    def test_parses_all_interests(self, interest_path: Path) -> None:
        profile = InterestProfile(interest_path)
        interests = profile.load()
        assert len(interests) == 4
        names = [i.name for i in interests]
        assert "MLB棒球" in names
        assert "天气" in names
        assert "AI/LLM动态" in names
        assert "课业/考试" in names

    def test_priorities(self, interest_path: Path) -> None:
        profile = InterestProfile(interest_path)
        interests = profile.load()
        by_name = {i.name: i for i in interests}
        assert by_name["MLB棒球"].priority == "high"
        assert by_name["天气"].priority == "high"
        assert by_name["AI/LLM动态"].priority == "medium"
        assert by_name["课业/考试"].priority == "low"

    def test_fields(self, interest_path: Path) -> None:
        profile = InterestProfile(interest_path)
        interests = profile.load()
        mlb = next(i for i in interests if i.name == "MLB棒球")
        assert "道奇队" in mlb.details
        assert mlb.frequency == "daily"
        assert mlb.typical_time == "evening"
        assert mlb.source == "显式声明"
        assert "休赛期" in mlb.notes

    def test_frequency_strips_parenthesized_notes(self, interest_path: Path) -> None:
        profile = InterestProfile(interest_path)
        interests = profile.load()
        mlb = next(i for i in interests if i.name == "MLB棒球")
        assert mlb.frequency == "daily"

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        profile = InterestProfile(tmp_path / "nonexistent.md")
        assert profile.load() == []

    def test_malformed_lines_dont_crash(self, tmp_path: Path) -> None:
        p = tmp_path / "test.md"
        p.write_text(textwrap.dedent("""\
            # Kevin 兴趣画像

            ## 高优先级（每日关注）

            ### 测试
            这行不符合格式
            - 没有冒号的行
            - 具体关注：正常字段
            - : 空key
        """), encoding="utf-8")
        profile = InterestProfile(p)
        interests = profile.load()
        assert len(interests) == 1
        assert interests[0].name == "测试"
        assert interests[0].details == "正常字段"

    def test_all_active_by_default(self, interest_path: Path) -> None:
        profile = InterestProfile(interest_path)
        for i in profile.load():
            assert i.active is True


# ═══════════════════════════════════════════════════════════════════
# InterestProfile.save()
# ═══════════════════════════════════════════════════════════════════

class TestInterestProfileSave:
    def test_round_trip(self, interest_path: Path) -> None:
        profile = InterestProfile(interest_path)
        original = profile.load()
        profile.save(original)
        reloaded = profile.load()
        assert len(reloaded) == len(original)
        for o, r in zip(original, reloaded):
            assert o.name == r.name
            assert o.priority == r.priority
            assert o.details == r.details
            assert o.frequency == r.frequency

    def test_save_preserves_inactive(self, tmp_path: Path) -> None:
        p = tmp_path / "interests.md"
        interests = [
            Interest(name="Active", priority="high", details="", frequency="daily",
                     typical_time="morning", source="test", notes="", active=True),
            Interest(name="Inactive", priority="low", details="", frequency="weekly",
                     typical_time="anytime", source="test", notes="", active=False),
        ]
        profile = InterestProfile(p)
        profile.save(interests)

        content = p.read_text(encoding="utf-8")
        assert "已停用" in content

    def test_atomic_write(self, tmp_path: Path) -> None:
        p = tmp_path / "interests.md"
        p.write_text("original content", encoding="utf-8")
        profile = InterestProfile(p)
        profile.save([
            Interest(name="New", priority="high", details="d", frequency="daily",
                     typical_time="morning", source="s", notes="n", active=True),
        ])
        assert "New" in p.read_text(encoding="utf-8")
        assert "original content" not in p.read_text(encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════
# PreparationEngine
# ═══════════════════════════════════════════════════════════════════

class TestPreparationEngine:
    @pytest.fixture()
    def profile(self, interest_path: Path) -> InterestProfile:
        return InterestProfile(interest_path)

    @pytest.fixture()
    def mock_store(self) -> AsyncMock:
        store = AsyncMock()
        store.get_by_category = AsyncMock(return_value=[])
        return store

    async def test_all_missing(self, profile: InterestProfile, mock_store: AsyncMock) -> None:
        engine = PreparationEngine(interest_profile=profile, ambient_store=mock_store)
        statuses = await engine.get_preparation_status()
        assert len(statuses) == 4
        for s in statuses:
            assert s.has_data is False
            assert s.is_fresh is False

    async def test_fresh_data(self, profile: InterestProfile, mock_store: AsyncMock) -> None:
        mock_store.get_by_category = AsyncMock(side_effect=lambda cat: (
            [_make_entry(category=cat)] if cat == "MLB棒球" else []
        ))
        engine = PreparationEngine(interest_profile=profile, ambient_store=mock_store)
        statuses = await engine.get_preparation_status()
        mlb = next(s for s in statuses if s.interest_name == "MLB棒球")
        assert mlb.has_data is True
        assert mlb.is_fresh is True
        assert "道奇" in mlb.cached_summary

    async def test_stale_data(self, profile: InterestProfile, mock_store: AsyncMock) -> None:
        stale_entry = _make_entry(
            fetched_at=_past_iso(hours=8),
            expires_at=_past_iso(hours=2),
        )
        mock_store.get_by_category = AsyncMock(side_effect=lambda cat: (
            [stale_entry] if cat == "MLB棒球" else []
        ))
        engine = PreparationEngine(interest_profile=profile, ambient_store=mock_store)
        statuses = await engine.get_preparation_status()
        mlb = next(s for s in statuses if s.interest_name == "MLB棒球")
        assert mlb.has_data is True
        assert mlb.is_fresh is False
        assert mlb.staleness_hours > 7

    async def test_format_for_prompt_nonempty(self, profile: InterestProfile, mock_store: AsyncMock) -> None:
        mock_store.get_by_category = AsyncMock(side_effect=lambda cat: (
            [_make_entry(category=cat)] if cat == "MLB棒球" else []
        ))
        engine = PreparationEngine(interest_profile=profile, ambient_store=mock_store)
        text = await engine.format_for_prompt()
        assert "MLB棒球" in text
        assert "✅" in text
        assert "❌" in text  # 其他兴趣无数据

    async def test_format_for_prompt_empty(self, tmp_path: Path, mock_store: AsyncMock) -> None:
        empty_profile = InterestProfile(tmp_path / "missing.md")
        engine = PreparationEngine(interest_profile=empty_profile, ambient_store=mock_store)
        text = await engine.format_for_prompt()
        assert text == ""


# ═══════════════════════════════════════════════════════════════════
# Model frozen behavior
# ═══════════════════════════════════════════════════════════════════

class TestModels:
    def test_interest_frozen(self) -> None:
        i = Interest(
            name="test", priority="high", details="d", frequency="daily",
            typical_time="morning", source="s", notes="n",
        )
        with pytest.raises(AttributeError):
            i.name = "other"  # type: ignore[misc]

    def test_preparation_status_frozen(self) -> None:
        s = PreparationStatus(
            interest_name="test", priority="high", has_data=True,
            is_fresh=True, cached_summary="ok", staleness_hours=1.0,
        )
        with pytest.raises(AttributeError):
            s.has_data = False  # type: ignore[misc]
