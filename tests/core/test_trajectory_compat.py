"""Unit tests for trajectory_compat (Step 2g transitional shim)."""

from __future__ import annotations

import pytest

from src.core.trajectory_compat import trajectory_entries_to_legacy_messages
from src.core.trajectory_store import (
    TrajectoryEntry,
    TrajectoryEntryType,
    TrajectoryStore,
)
from src.logging.state_mutation_log import StateMutationLog


@pytest.fixture
async def mutation_log(tmp_path):
    log = StateMutationLog(tmp_path / "mlog.db", logs_dir=tmp_path / "logs")
    await log.init()
    yield log
    await log.close()


@pytest.fixture
async def store(tmp_path, mutation_log):
    s = TrajectoryStore(tmp_path / "shared.db", mutation_log)
    await s.init()
    yield s
    await s.close()


def _mk(id_, entry_type, source_chat_id, actor, content):
    return TrajectoryEntry(
        id=id_, timestamp=1.0, entry_type=entry_type.value,
        source_chat_id=source_chat_id, actor=actor,
        content=content,
        related_commitment_id=None, related_iteration_id=None,
        related_tool_call_id=None,
    )


class TestBasicMapping:
    def test_user_message_becomes_user_role(self):
        entry = _mk(1, TrajectoryEntryType.USER_MESSAGE, "c1", "user", {"text": "hi"})
        out = trajectory_entries_to_legacy_messages([entry])
        assert out == [{"role": "user", "content": "hi"}]

    def test_assistant_text_becomes_assistant_role(self):
        entry = _mk(1, TrajectoryEntryType.ASSISTANT_TEXT, "c1", "lapwing", {"text": "ok"})
        out = trajectory_entries_to_legacy_messages([entry])
        assert out == [{"role": "assistant", "content": "ok"}]

    def test_tell_user_with_messages_list_joins_newlines(self):
        entry = _mk(1, TrajectoryEntryType.TELL_USER, "c1", "lapwing",
                    {"messages": ["line 1", "line 2"]})
        out = trajectory_entries_to_legacy_messages([entry])
        assert out == [{"role": "assistant", "content": "line 1\nline 2"}]

    def test_tell_user_with_text_fallback(self):
        entry = _mk(1, TrajectoryEntryType.TELL_USER, "c1", "lapwing",
                    {"text": "single"})
        out = trajectory_entries_to_legacy_messages([entry])
        assert out == [{"role": "assistant", "content": "single"}]

    def test_preserves_input_order(self):
        entries = [
            _mk(1, TrajectoryEntryType.USER_MESSAGE, "c1", "user", {"text": "a"}),
            _mk(2, TrajectoryEntryType.ASSISTANT_TEXT, "c1", "lapwing", {"text": "b"}),
            _mk(3, TrajectoryEntryType.USER_MESSAGE, "c1", "user", {"text": "c"}),
        ]
        out = trajectory_entries_to_legacy_messages(entries)
        assert [m["content"] for m in out] == ["a", "b", "c"]


class TestInnerThoughtHandling:
    def test_inner_thought_dropped_by_default(self):
        entry = _mk(1, TrajectoryEntryType.INNER_THOUGHT, "__inner__", "lapwing",
                    {"text": "思考"})
        assert trajectory_entries_to_legacy_messages([entry]) == []

    def test_inner_thought_kept_with_include_inner_as_system_note(self):
        entry = _mk(1, TrajectoryEntryType.INNER_THOUGHT, "__inner__", "lapwing",
                    {"text": "思考"})
        out = trajectory_entries_to_legacy_messages([entry], include_inner=True)
        assert out == [{"role": "system", "content": "[内部思考] 思考"}]


class TestDroppedTypes:
    @pytest.mark.parametrize("entry_type", [
        TrajectoryEntryType.TOOL_CALL,
        TrajectoryEntryType.TOOL_RESULT,
        TrajectoryEntryType.STATE_CHANGE,
        TrajectoryEntryType.STAY_SILENT,
    ])
    def test_non_message_types_dropped(self, entry_type):
        entry = _mk(1, entry_type, "c1", "lapwing", {"text": "ignored"})
        assert trajectory_entries_to_legacy_messages([entry]) == []


class TestMissingTextPayload:
    def test_empty_content_dict_skipped(self):
        entry = _mk(1, TrajectoryEntryType.USER_MESSAGE, "c1", "user", {})
        assert trajectory_entries_to_legacy_messages([entry]) == []

    def test_text_wrong_type_skipped(self):
        entry = _mk(1, TrajectoryEntryType.USER_MESSAGE, "c1", "user", {"text": 42})
        assert trajectory_entries_to_legacy_messages([entry]) == []


class TestEndToEndWithStore:
    async def test_roundtrip_through_real_store(self, store):
        await store.append(
            TrajectoryEntryType.USER_MESSAGE, "c1", "user", {"text": "hi"},
        )
        await store.append(
            TrajectoryEntryType.ASSISTANT_TEXT, "c1", "lapwing", {"text": "hey"},
        )
        await store.append(
            TrajectoryEntryType.TOOL_CALL, "c1", "lapwing",
            {"tool_name": "search", "purpose": "look up"},
        )
        rows = await store.recent(10)
        msgs = trajectory_entries_to_legacy_messages(rows)
        assert msgs == [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hey"},
        ]

    async def test_include_inner_merges_consciousness_rows(self, store):
        await store.append(
            TrajectoryEntryType.USER_MESSAGE, "c1", "user", {"text": "hi"},
        )
        await store.append(
            TrajectoryEntryType.INNER_THOUGHT, "__inner__", "lapwing",
            {"text": "thinking"},
        )
        rows = await store.recent(10)
        msgs_default = trajectory_entries_to_legacy_messages(rows)
        msgs_with_inner = trajectory_entries_to_legacy_messages(
            rows, include_inner=True
        )
        assert len(msgs_default) == 1
        assert len(msgs_with_inner) == 2
        assert msgs_with_inner[1]["role"] == "system"
