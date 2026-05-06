"""Unit tests for src.core.state_view_builder.

Blueprint v2.0 Step 3 §3. The builder reads identity files and async
stores; these tests drive it with in-memory stubs so we can assert on
the exact StateView it emits. No serializer here — that's pinned by its
own suite. We care only that (a) every wired store ends up in the
right StateView field, (b) missing stores collapse to empty sections,
and (c) the offline-gap probe is honoured.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from src.core.state_view import CommitmentView, StateView
from src.core.state_view_builder import StateViewBuilder
from src.core.trajectory_store import TrajectoryEntry, TrajectoryEntryType
from src.ambient.models import AmbientEntry


# ── Stubs ────────────────────────────────────────────────────────────

class _StubAttention:
    def __init__(self, current_conversation: str | None, mode: str) -> None:
        @dataclass(frozen=True)
        class _State:
            current_conversation: str | None
            current_action: str | None
            last_interaction_at: float
            last_action_at: float
            mode: str

        self._state = _State(
            current_conversation=current_conversation,
            current_action=None,
            last_interaction_at=0.0,
            last_action_at=0.0,
            mode=mode,
        )

    def get(self):
        return self._state


class _StubTrajectory:
    def __init__(self, entries: list[TrajectoryEntry]) -> None:
        self._entries = entries

    async def relevant_to_chat(
        self, chat_id: str, n: int, *, include_inner: bool = False
    ) -> list[TrajectoryEntry]:
        return list(self._entries)

    async def recent(self, n: int) -> list[TrajectoryEntry]:
        return list(self._entries)


class _StubCommitmentStore:
    def __init__(self, commitments: list[Any]) -> None:
        self._commitments = commitments

    async def list_open(self, chat_id: str | None = None) -> list[Any]:
        return list(self._commitments)


class _StubReminders:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    async def get_due_reminders(
        self, *, chat_id: str, now: datetime, grace_seconds: int, limit: int
    ) -> list[dict]:
        return list(self._rows[:limit])


class _StubTaskStore:
    def __init__(self, tasks: list[Any]) -> None:
        self._tasks = tasks

    async def list_active(self) -> list[Any]:
        return list(self._tasks)


class _StubAmbient:
    def __init__(self, entries: list[AmbientEntry]) -> None:
        self._entries = tuple(entries)

    async def get_all_fresh(self):
        return self._entries


@dataclass(frozen=True)
class _FakeCommitment:
    id: str
    content: str
    status: str
    created_at: float = 0.0
    target_chat_id: str = "chat1"
    source_trajectory_entry_id: int = 0
    status_changed_at: float = 0.0
    fulfilled_by_entry_ids: list[int] | None = None
    reasoning: str | None = None


@dataclass(frozen=True)
class _FakeTask:
    task_id: str
    request: str
    status: str


def _make_entry(
    eid: int, entry_type: str, actor: str, text: str, chat_id: str = "chat1"
) -> TrajectoryEntry:
    return TrajectoryEntry(
        id=eid,
        timestamp=float(eid),
        entry_type=entry_type,
        source_chat_id=chat_id,
        actor=actor,
        content={"text": text},
        related_commitment_id=None,
        related_iteration_id=None,
        related_tool_call_id=None,
    )


# ── Identity loading ─────────────────────────────────────────────────

class TestIdentityLoading:
    def test_missing_files_produce_empty_strings(self, tmp_path: Path):
        # No files written at the target paths → builder treats as empty
        builder = StateViewBuilder(
            soul_path=tmp_path / "missing_soul.md",
            constitution_path=tmp_path / "missing_const.md",
            voice_prompt_name="does_not_exist",
        )
        sv = asyncio.run(builder.build_for_chat("chat1"))
        assert sv.identity_docs.soul == ""
        assert sv.identity_docs.constitution == ""
        assert sv.identity_docs.voice == ""

    def test_loads_soul_and_constitution(self, tmp_path: Path):
        soul = tmp_path / "soul.md"
        const = tmp_path / "const.md"
        soul.write_text("SOUL_TEXT", encoding="utf-8")
        const.write_text("CONST_TEXT", encoding="utf-8")
        builder = StateViewBuilder(
            soul_path=soul, constitution_path=const,
            voice_prompt_name="does_not_exist",
        )
        sv = asyncio.run(builder.build_for_chat("chat1"))
        assert sv.identity_docs.soul == "SOUL_TEXT"
        assert sv.identity_docs.constitution == "CONST_TEXT"


# ── Attention ────────────────────────────────────────────────────────

class TestAttention:
    def test_no_attention_manager_yields_idle_defaults(self, tmp_path: Path):
        builder = StateViewBuilder(
            soul_path=tmp_path / "_",
            constitution_path=tmp_path / "_",
            voice_prompt_name="does_not_exist",
        )
        sv = asyncio.run(builder.build_for_chat("chat1"))
        assert sv.attention_context.mode == "idle"
        assert sv.attention_context.current_conversation is None

    def test_reads_attention_state(self, tmp_path: Path):
        builder = StateViewBuilder(
            soul_path=tmp_path / "_",
            constitution_path=tmp_path / "_",
            voice_prompt_name="does_not_exist",
            attention_manager=_StubAttention(
                current_conversation="chat:abc", mode="conversing"
            ),
        )
        sv = asyncio.run(builder.build_for_chat("chat:abc"))
        assert sv.attention_context.mode == "conversing"
        assert sv.attention_context.current_conversation == "chat:abc"


class TestOfflineGap:
    def test_no_reader_yields_none(self, tmp_path: Path):
        builder = StateViewBuilder(
            soul_path=tmp_path / "_",
            constitution_path=tmp_path / "_",
            voice_prompt_name="does_not_exist",
        )
        sv = asyncio.run(builder.build_for_chat("c"))
        assert sv.attention_context.offline_hours is None

    def test_gap_below_threshold_suppressed(self, tmp_path: Path):
        last = datetime.now(timezone.utc) - timedelta(hours=1)
        builder = StateViewBuilder(
            soul_path=tmp_path / "_",
            constitution_path=tmp_path / "_",
            voice_prompt_name="does_not_exist",
            previous_state_reader=lambda: {"last_active": last.isoformat()},
        )
        sv = asyncio.run(builder.build_for_chat("c"))
        assert sv.attention_context.offline_hours is None

    def test_gap_above_threshold_returned(self, tmp_path: Path):
        last = datetime.now(timezone.utc) - timedelta(hours=9)
        builder = StateViewBuilder(
            soul_path=tmp_path / "_",
            constitution_path=tmp_path / "_",
            voice_prompt_name="does_not_exist",
            previous_state_reader=lambda: {"last_active": last.isoformat()},
        )
        sv = asyncio.run(builder.build_for_chat("c"))
        assert sv.attention_context.offline_hours is not None
        assert 8.9 < sv.attention_context.offline_hours < 9.2

    def test_bad_timestamp_yields_none(self, tmp_path: Path):
        builder = StateViewBuilder(
            soul_path=tmp_path / "_",
            constitution_path=tmp_path / "_",
            voice_prompt_name="does_not_exist",
            previous_state_reader=lambda: {"last_active": "not-a-date"},
        )
        sv = asyncio.run(builder.build_for_chat("c"))
        assert sv.attention_context.offline_hours is None


# ── Trajectory projection ────────────────────────────────────────────

class TestTrajectoryProjection:
    def test_no_store_yields_empty_window(self, tmp_path: Path):
        builder = StateViewBuilder(
            soul_path=tmp_path / "_",
            constitution_path=tmp_path / "_",
            voice_prompt_name="does_not_exist",
        )
        sv = asyncio.run(builder.build_for_chat("c"))
        assert sv.trajectory_window.turns == ()

    def test_user_and_assistant_passed_through(self, tmp_path: Path):
        entries = [
            _make_entry(1, TrajectoryEntryType.USER_MESSAGE.value, "user", "hi"),
            _make_entry(2, TrajectoryEntryType.ASSISTANT_TEXT.value, "lapwing", "hey"),
            _make_entry(3, TrajectoryEntryType.USER_MESSAGE.value, "user", "how r u"),
        ]
        builder = StateViewBuilder(
            soul_path=tmp_path / "_",
            constitution_path=tmp_path / "_",
            voice_prompt_name="does_not_exist",
            trajectory_store=_StubTrajectory(entries),
        )
        sv = asyncio.run(builder.build_for_chat("chat1"))
        turns = sv.trajectory_window.turns
        assert [t.role for t in turns] == ["user", "assistant", "user"]
        assert [t.content for t in turns] == ["hi", "hey", "how r u"]

    def test_tool_rows_are_dropped(self, tmp_path: Path):
        entries = [
            _make_entry(1, TrajectoryEntryType.USER_MESSAGE.value, "user", "do x"),
            _make_entry(2, TrajectoryEntryType.TOOL_CALL.value, "lapwing", "call"),
            _make_entry(3, TrajectoryEntryType.TOOL_RESULT.value, "system", "ok"),
            _make_entry(4, TrajectoryEntryType.ASSISTANT_TEXT.value, "lapwing", "done"),
        ]
        builder = StateViewBuilder(
            soul_path=tmp_path / "_",
            constitution_path=tmp_path / "_",
            voice_prompt_name="does_not_exist",
            trajectory_store=_StubTrajectory(entries),
        )
        sv = asyncio.run(builder.build_for_chat("chat1"))
        assert len(sv.trajectory_window.turns) == 2
        assert sv.trajectory_window.turns[0].content == "do x"
        assert sv.trajectory_window.turns[1].content == "done"

    def test_tell_user_with_messages_list_joined(self, tmp_path: Path):
        entry = TrajectoryEntry(
            id=1, timestamp=1.0,
            entry_type=TrajectoryEntryType.TELL_USER.value,
            source_chat_id="chat1", actor="lapwing",
            content={"messages": ["first", "second"]},
            related_commitment_id=None,
            related_iteration_id=None,
            related_tool_call_id=None,
        )
        builder = StateViewBuilder(
            soul_path=tmp_path / "_",
            constitution_path=tmp_path / "_",
            voice_prompt_name="does_not_exist",
            trajectory_store=_StubTrajectory([entry]),
        )
        sv = asyncio.run(builder.build_for_chat("chat1"))
        assert sv.trajectory_window.turns[0].role == "assistant"
        assert sv.trajectory_window.turns[0].content == "first\nsecond"


# ── Commitments / reminders / tasks ──────────────────────────────────

class TestCommitmentsProjection:
    def test_open_commitments_promoted_to_promises(self, tmp_path: Path):
        opens = [
            _FakeCommitment(id="c1", content="陪 Kevin 散步", status="pending"),
            _FakeCommitment(id="c2", content="写 step3 报告", status="in_progress"),
        ]
        builder = StateViewBuilder(
            soul_path=tmp_path / "_",
            constitution_path=tmp_path / "_",
            voice_prompt_name="does_not_exist",
            commitment_store=_StubCommitmentStore(opens),
        )
        sv = asyncio.run(builder.build_for_chat("chat1"))
        promises = [c for c in sv.commitments_active if c.kind == "promise"]
        assert len(promises) == 2
        assert {c.description for c in promises} == {"陪 Kevin 散步", "写 step3 报告"}

    def test_reminders_projected_with_due_at(self, tmp_path: Path):
        rows = [
            {"content": "喝水", "next_trigger_at": "2026-04-18T18:00:00+08:00"},
            {"content": "站起来", "next_trigger_at": "2026-04-18T18:30:00+08:00"},
        ]
        builder = StateViewBuilder(
            soul_path=tmp_path / "_",
            constitution_path=tmp_path / "_",
            voice_prompt_name="does_not_exist",
            reminder_source=_StubReminders(rows),
        )
        sv = asyncio.run(builder.build_for_chat("chat1"))
        reminders = [c for c in sv.commitments_active if c.kind == "reminder"]
        assert len(reminders) == 2
        assert reminders[0].description == "喝水"
        assert reminders[0].due_at.endswith("+08:00")

    def test_tasks_projected(self, tmp_path: Path):
        tasks = [
            _FakeTask(task_id="t1", request="审查PR", status="running"),
            _FakeTask(task_id="t2", request="研究雷达", status="queued"),
        ]
        builder = StateViewBuilder(
            soul_path=tmp_path / "_",
            constitution_path=tmp_path / "_",
            voice_prompt_name="does_not_exist",
            task_store=_StubTaskStore(tasks),
        )
        sv = asyncio.run(builder.build_for_chat("chat1"))
        task_views = [c for c in sv.commitments_active if c.kind == "task"]
        assert [c.description for c in task_views] == ["审查PR", "研究雷达"]

    def test_no_stores_yields_empty_tuple(self, tmp_path: Path):
        builder = StateViewBuilder(
            soul_path=tmp_path / "_",
            constitution_path=tmp_path / "_",
            voice_prompt_name="does_not_exist",
        )
        sv = asyncio.run(builder.build_for_chat("chat1"))
        assert sv.commitments_active == ()

    def test_store_failures_are_non_fatal(self, tmp_path: Path):
        """If any store raises, the builder logs and moves on. The rest
        of the StateView must still come through."""
        class _BoomCommitments:
            async def list_open(self, chat_id=None):
                raise RuntimeError("db down")

        class _BoomTasks:
            async def list_active(self):
                raise RuntimeError("db down")

        builder = StateViewBuilder(
            soul_path=tmp_path / "_",
            constitution_path=tmp_path / "_",
            voice_prompt_name="does_not_exist",
            commitment_store=_BoomCommitments(),
            task_store=_BoomTasks(),
        )
        sv = asyncio.run(builder.build_for_chat("chat1"))
        assert sv.commitments_active == ()


# ── Inner-loop entry point ───────────────────────────────────────────

class TestInnerBuild:
    def test_inner_uses_recent_not_chat_scoped(self, tmp_path: Path):
        entries = [
            _make_entry(1, TrajectoryEntryType.USER_MESSAGE.value, "user", "hi", chat_id="A"),
            _make_entry(2, TrajectoryEntryType.ASSISTANT_TEXT.value, "lapwing", "yo", chat_id="B"),
        ]
        builder = StateViewBuilder(
            soul_path=tmp_path / "_",
            constitution_path=tmp_path / "_",
            voice_prompt_name="does_not_exist",
            trajectory_store=_StubTrajectory(entries),
        )
        sv = asyncio.run(builder.build_for_inner())
        # cross-channel turns accepted
        assert len(sv.trajectory_window.turns) == 2

    def test_inner_channel_tag_empty(self, tmp_path: Path):
        builder = StateViewBuilder(
            soul_path=tmp_path / "_",
            constitution_path=tmp_path / "_",
            voice_prompt_name="does_not_exist",
        )
        sv = asyncio.run(builder.build_for_inner())
        assert sv.attention_context.channel == ""


class TestAmbientEntries:
    def _entry(
        self,
        key: str,
        *,
        category: str = "snooker",
        confidence: float = 0.8,
        fetched_at: str = "2026-05-04T10:00:00+00:00",
        expires_at: str = "2999-01-01T00:00:00+00:00",
    ) -> AmbientEntry:
        return AmbientEntry(
            key=key,
            category=category,
            topic=key,
            data="{}",
            summary=key,
            fetched_at=fetched_at,
            expires_at=expires_at,
            source="research_writeback",
            confidence=confidence,
        )

    def test_filters_low_confidence_and_keeps_top_per_category(self, tmp_path: Path):
        entries = [
            self._entry("low", confidence=0.6),
            self._entry("fresh-high", confidence=0.9, fetched_at="2026-05-04T09:00:00+00:00"),
            self._entry("fresh-mid", confidence=0.8, fetched_at="2026-05-04T11:00:00+00:00"),
        ]
        builder = StateViewBuilder(
            soul_path=tmp_path / "_",
            constitution_path=tmp_path / "_",
            voice_prompt_name="does_not_exist",
        )
        builder._ambient = _StubAmbient(entries)

        result = asyncio.run(builder._build_ambient_entries())

        assert tuple(e.key for e in result) == ("fresh-high",)


# ── End-to-end: builder → serializer ──────────────────────────────────

class TestBuilderSerializerRoundtrip:
    def test_produces_prompt_bytes(self, tmp_path: Path):
        from src.core.state_serializer import serialize

        soul = tmp_path / "soul.md"
        soul.write_text("I am Lapwing.", encoding="utf-8")
        entries = [
            _make_entry(1, TrajectoryEntryType.USER_MESSAGE.value, "user", "你好"),
            _make_entry(2, TrajectoryEntryType.ASSISTANT_TEXT.value, "lapwing", "嗨"),
        ]
        builder = StateViewBuilder(
            soul_path=soul,
            constitution_path=tmp_path / "_no_const",
            voice_prompt_name="does_not_exist",
            trajectory_store=_StubTrajectory(entries),
        )
        sv = asyncio.run(builder.build_for_chat("chat1"))
        out = serialize(sv)
        assert "I am Lapwing." in out.system_prompt
        # Two trajectory turns + total=3 < 4 → no voice inject
        assert len(out.messages) == 2
