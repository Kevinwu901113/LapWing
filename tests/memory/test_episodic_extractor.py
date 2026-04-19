"""EpisodicExtractor unit tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.trajectory_store import TrajectoryEntry, TrajectoryEntryType
from src.memory.episodic_extractor import EpisodicExtractor
from src.memory.episodic_store import EpisodicStore
from src.memory.vector_store import MemoryVectorStore


def _row(
    row_id: int,
    entry_type: TrajectoryEntryType,
    text: str,
    actor: str,
) -> TrajectoryEntry:
    payload: dict
    if entry_type == TrajectoryEntryType.TELL_USER:
        payload = {"messages": [text]}
    else:
        payload = {"text": text}
    return TrajectoryEntry(
        id=row_id,
        timestamp=float(row_id),
        entry_type=entry_type.value,
        source_chat_id="kev",
        actor=actor,
        content=payload,
        related_commitment_id=None,
        related_iteration_id=None,
        related_tool_call_id=None,
    )


@pytest.fixture
def vector_store(tmp_path):
    return MemoryVectorStore(persist_dir=str(tmp_path / "chroma"))


@pytest.fixture
def episodic_store(tmp_path, vector_store):
    return EpisodicStore(
        memory_dir=tmp_path / "episodic",
        vector_store=vector_store,
    )


def _make_extractor(
    *, trajectory_rows, router_response: str, episodic_store,
    min_turns: int = 3,
):
    trajectory = MagicMock()
    trajectory.relevant_to_chat = AsyncMock(return_value=trajectory_rows)
    router = MagicMock()
    router.complete = AsyncMock(return_value=router_response)
    return EpisodicExtractor(
        router=router,
        trajectory_store=trajectory,
        episodic_store=episodic_store,
        window_size=10,
        min_turns=min_turns,
    ), router


class TestExtractFromChat:
    async def test_writes_episode_from_conversation(self, episodic_store):
        rows = [
            _row(1, TrajectoryEntryType.USER_MESSAGE, "道奇今天赢了吗？", "user"),
            _row(2, TrajectoryEntryType.TELL_USER, "我查一下", "lapwing"),
            _row(3, TrajectoryEntryType.USER_MESSAGE, "结果呢？", "user"),
            _row(4, TrajectoryEntryType.TELL_USER, "网超时了", "lapwing"),
        ]
        extractor, router = _make_extractor(
            trajectory_rows=rows,
            router_response=(
                "Kevin 问道奇比赛，我网超时没查到\n\n"
                "Kevin 问道奇今天的比赛，我用 research 查但网络超时。"
            ),
            episodic_store=episodic_store,
        )
        ok = await extractor.extract_from_chat("kev")
        assert ok is True

        hits = await episodic_store.query("道奇", top_k=3)
        assert hits
        assert "道奇" in hits[0].summary
        # source_trajectory_ids should round-trip through metadata
        assert hits[0].source_trajectory_ids == (1, 2, 3, 4)

        router.complete.assert_awaited_once()
        call_kwargs = router.complete.await_args.kwargs
        assert call_kwargs.get("slot") == "memory_processing"

    async def test_skips_when_too_few_turns(self, episodic_store):
        rows = [_row(1, TrajectoryEntryType.USER_MESSAGE, "hi", "user")]
        extractor, router = _make_extractor(
            trajectory_rows=rows,
            router_response="should not be called",
            episodic_store=episodic_store,
            min_turns=3,
        )
        ok = await extractor.extract_from_chat("kev")
        assert ok is False
        router.complete.assert_not_called()

    async def test_returns_false_on_llm_failure(self, episodic_store):
        rows = [
            _row(i, TrajectoryEntryType.USER_MESSAGE, f"m{i}", "user")
            for i in range(1, 5)
        ]
        trajectory = MagicMock()
        trajectory.relevant_to_chat = AsyncMock(return_value=rows)
        router = MagicMock()
        router.complete = AsyncMock(side_effect=RuntimeError("LLM down"))
        extractor = EpisodicExtractor(
            router=router,
            trajectory_store=trajectory,
            episodic_store=episodic_store,
            min_turns=2,
        )
        ok = await extractor.extract_from_chat("kev")
        assert ok is False

        hits = await episodic_store.query("m1", top_k=3)
        # No episode written.
        assert hits == []

    async def test_returns_false_on_empty_summary(self, episodic_store):
        rows = [
            _row(1, TrajectoryEntryType.USER_MESSAGE, "a", "user"),
            _row(2, TrajectoryEntryType.ASSISTANT_TEXT, "b", "lapwing"),
            _row(3, TrajectoryEntryType.USER_MESSAGE, "c", "user"),
        ]
        extractor, _ = _make_extractor(
            trajectory_rows=rows,
            router_response="   \n\n   ",
            episodic_store=episodic_store,
            min_turns=2,
        )
        ok = await extractor.extract_from_chat("kev")
        assert ok is False

    async def test_title_parse_from_two_part_output(self, episodic_store):
        rows = [
            _row(i, TrajectoryEntryType.USER_MESSAGE, f"msg {i}", "user")
            for i in range(1, 5)
        ]
        extractor, _ = _make_extractor(
            trajectory_rows=rows,
            router_response="事件标题行\n\n事件的详细正文说明",
            episodic_store=episodic_store,
            min_turns=2,
        )
        ok = await extractor.extract_from_chat("kev")
        assert ok is True
        hits = await episodic_store.query("事件", top_k=1)
        assert hits
        assert hits[0].title == "事件标题行"
        assert "详细正文" in hits[0].summary
