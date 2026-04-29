"""EpisodicStore unit tests.

Day-organised markdown + shared ChromaDB collection. Covers:
- add_episode writes section to YYYY-MM-DD.md
- multiple episodes on the same day land in the same file, ordered
- query only returns note_type="episodic" hits (filter by scoping via
  the shared MemoryVectorStore)
- source_trajectory_ids round-trip through metadata
"""

from __future__ import annotations
import pytest
pytestmark = pytest.mark.integration

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.memory.episodic_store import EpisodicStore
from src.memory.vector_store import MemoryVectorStore

_TAIPEI = ZoneInfo("Asia/Taipei")


@pytest.fixture
def vector_store(tmp_path):
    return MemoryVectorStore(persist_dir=str(tmp_path / "chroma"))


@pytest.fixture
def episodic(tmp_path, vector_store):
    return EpisodicStore(
        memory_dir=tmp_path / "episodic",
        vector_store=vector_store,
    )


class TestAddEpisode:
    async def test_creates_day_file(self, episodic, tmp_path):
        when = datetime(2026, 4, 17, 14, 30, tzinfo=_TAIPEI)
        entry = await episodic.add_episode(
            summary="Kevin 问了道奇的比赛结果",
            source_trajectory_ids=[101, 102],
            occurred_at=when,
        )
        expected = tmp_path / "episodic" / "2026-04-17.md"
        assert expected.exists()
        text = expected.read_text(encoding="utf-8")
        assert "# 2026-04-17 情景记录" in text
        assert "## 14:30" in text
        assert "Kevin 问了道奇的比赛结果" in text
        assert entry.episode_id.startswith("ep_20260417_1430")
        assert entry.date == "2026-04-17"

    async def test_append_same_day(self, episodic, tmp_path):
        d1 = datetime(2026, 4, 17, 9, 0, tzinfo=_TAIPEI)
        d2 = datetime(2026, 4, 17, 18, 45, tzinfo=_TAIPEI)
        await episodic.add_episode(summary="早上讨论了论文", occurred_at=d1)
        await episodic.add_episode(summary="晚上复盘了进展", occurred_at=d2)
        text = (tmp_path / "episodic" / "2026-04-17.md").read_text(encoding="utf-8")
        assert text.count("## ") == 2
        assert text.index("09:00") < text.index("18:45")

    async def test_different_days_split_files(self, episodic, tmp_path):
        d1 = datetime(2026, 4, 17, 10, 0, tzinfo=_TAIPEI)
        d2 = datetime(2026, 4, 18, 10, 0, tzinfo=_TAIPEI)
        await episodic.add_episode(summary="day one", occurred_at=d1)
        await episodic.add_episode(summary="day two", occurred_at=d2)
        assert (tmp_path / "episodic" / "2026-04-17.md").exists()
        assert (tmp_path / "episodic" / "2026-04-18.md").exists()

    async def test_derived_title_from_summary_first_line(self, episodic):
        entry = await episodic.add_episode(
            summary="讨论了下周的周报\n具体是在晚饭时聊的",
        )
        assert entry.title == "讨论了下周的周报"

    async def test_explicit_title_wins(self, episodic):
        entry = await episodic.add_episode(
            summary="具体内容在后面",
            title="周报讨论",
        )
        assert entry.title == "周报讨论"

    async def test_rejects_empty_summary(self, episodic):
        with pytest.raises(ValueError):
            await episodic.add_episode(summary="   ")


class TestQuery:
    async def test_query_returns_episodic_hits(self, episodic):
        await episodic.add_episode(summary="Kevin 喜欢看道奇比赛，我查了结果")
        await episodic.add_episode(summary="Kevin 问了天气，我告诉他会下雨")
        hits = await episodic.query("道奇", top_k=5)
        assert len(hits) >= 1
        assert any("道奇" in h.summary for h in hits)
        assert all(h.episode_id.startswith("ep_") for h in hits)

    async def test_query_empty_store_returns_empty(self, episodic):
        hits = await episodic.query("anything")
        assert hits == []

    async def test_query_empty_text(self, episodic):
        await episodic.add_episode(summary="有内容")
        assert await episodic.query("") == []
        assert await episodic.query("   ") == []

    async def test_filter_excludes_non_episodic_entries(
        self, episodic, vector_store,
    ):
        # Write a non-episodic entry directly to the vector store (like
        # NoteStore / SemanticStore would).
        await vector_store.add(
            note_id="note_001",
            content="Kevin 喜欢道奇 — 这是一条手写笔记",
            metadata={
                "note_type": "observation",
                "trust": "self",
                "created_at": "2026-04-15T10:00:00+08:00",
                "file_path": "some.md",
            },
        )
        await episodic.add_episode(summary="Kevin 道奇比赛查询")

        hits = await episodic.query("道奇", top_k=10)
        # Every hit must be episodic — filter must exclude note_001.
        assert all(h.episode_id != "note_001" for h in hits)
        assert all(h.episode_id.startswith("ep_") for h in hits)


class TestMetadataRoundTrip:
    async def test_source_trajectory_ids_round_trip(self, episodic):
        await episodic.add_episode(
            summary="带 trajectory id 的事件",
            source_trajectory_ids=[10, 11, 12],
        )
        hits = await episodic.query("trajectory id", top_k=3)
        assert hits
        assert hits[0].source_trajectory_ids == (10, 11, 12)

    async def test_date_round_trip(self, episodic):
        when = datetime(2026, 4, 18, 20, 0, tzinfo=_TAIPEI)
        await episodic.add_episode(summary="周六晚上", occurred_at=when)
        hits = await episodic.query("周六", top_k=3)
        assert hits
        assert hits[0].date == "2026-04-18"
