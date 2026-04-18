"""Unit tests for TrajectoryStore.

Covers Blueprint v2.0 Step 2 §2 requirements:
  1. append() basic write & id monotonicity per entry_type
  2. append() emits TRAJECTORY_APPENDED mutation with correct payload
  3. actor / entry_type / content-shape validation
  4. recent() / relevant_to_chat() / in_iteration() / in_window() correctness
  5. relevant_to_chat include_inner switch
  6. related_iteration_id auto-pickup from contextvars
  7. content_json roundtrips including nested dicts
"""

from __future__ import annotations

import time

import pytest

from src.core.trajectory_store import (
    TrajectoryEntry,
    TrajectoryEntryType,
    TrajectoryStore,
)
from src.logging.state_mutation_log import (
    MutationType,
    StateMutationLog,
    iteration_context,
    new_iteration_id,
)


@pytest.fixture
async def mutation_log(tmp_path):
    log = StateMutationLog(
        tmp_path / "mutation_log.db", logs_dir=tmp_path / "logs"
    )
    await log.init()
    yield log
    await log.close()


@pytest.fixture
async def store(tmp_path, mutation_log):
    s = TrajectoryStore(tmp_path / "lapwing.db", mutation_log)
    await s.init()
    yield s
    await s.close()


class TestAppendBasics:
    async def test_append_returns_monotonic_ids(self, store):
        id1 = await store.append(
            TrajectoryEntryType.USER_MESSAGE, "chat1", "user",
            {"text": "hi"},
        )
        id2 = await store.append(
            TrajectoryEntryType.ASSISTANT_TEXT, "chat1", "lapwing",
            {"text": "hello"},
        )
        assert id1 > 0
        assert id2 == id1 + 1

    async def test_append_roundtrips_content(self, store):
        await store.append(
            TrajectoryEntryType.USER_MESSAGE, "chat1", "user",
            {"text": "测试中文", "adapter": "qq", "nested": {"a": 1}},
        )
        rows = await store.recent(10)
        assert len(rows) == 1
        assert rows[0].content == {
            "text": "测试中文", "adapter": "qq", "nested": {"a": 1},
        }

    async def test_append_all_entry_types(self, store):
        types = [
            (TrajectoryEntryType.USER_MESSAGE, "user"),
            (TrajectoryEntryType.TELL_USER, "lapwing"),
            (TrajectoryEntryType.ASSISTANT_TEXT, "lapwing"),
            (TrajectoryEntryType.INNER_THOUGHT, "lapwing"),
            (TrajectoryEntryType.TOOL_CALL, "lapwing"),
            (TrajectoryEntryType.TOOL_RESULT, "system"),
            (TrajectoryEntryType.STATE_CHANGE, "system"),
            (TrajectoryEntryType.STAY_SILENT, "lapwing"),
        ]
        for et, actor in types:
            await store.append(et, "chat1", actor, {"marker": et.value})
        rows = await store.recent(20)
        assert len(rows) == len(types)
        assert {r.entry_type for r in rows} == {et.value for et, _ in types}


class TestValidation:
    async def test_rejects_non_enum_entry_type(self, store):
        with pytest.raises(TypeError):
            await store.append(
                "user_message", "chat1", "user", {"text": "x"},  # type: ignore[arg-type]
            )

    async def test_rejects_invalid_actor(self, store):
        with pytest.raises(ValueError):
            await store.append(
                TrajectoryEntryType.USER_MESSAGE, "chat1", "stranger",
                {"text": "x"},
            )

    async def test_rejects_non_dict_content(self, store):
        with pytest.raises(TypeError):
            await store.append(
                TrajectoryEntryType.USER_MESSAGE, "chat1", "user",
                "not a dict",  # type: ignore[arg-type]
            )

    async def test_rejects_call_before_init(self, tmp_path, mutation_log):
        s = TrajectoryStore(tmp_path / "u.db", mutation_log)
        with pytest.raises(RuntimeError):
            await s.append(
                TrajectoryEntryType.USER_MESSAGE, "chat1", "user", {"text": "x"},
            )


class TestMutationLogIntegration:
    async def test_append_emits_trajectory_appended(self, store, mutation_log):
        entry_id = await store.append(
            TrajectoryEntryType.USER_MESSAGE, "chat1", "user",
            {"text": "hello"},
        )
        muts = await mutation_log.query_by_type(MutationType.TRAJECTORY_APPENDED)
        assert len(muts) == 1
        payload = muts[0].payload
        assert payload["trajectory_id"] == entry_id
        assert payload["entry_type"] == "user_message"
        assert payload["source_chat_id"] == "chat1"
        assert payload["actor"] == "user"
        assert muts[0].chat_id == "chat1"

    async def test_auto_picks_up_iteration_id_from_contextvar(
        self, store, mutation_log
    ):
        it_id = new_iteration_id()
        with iteration_context(it_id, chat_id="chat1"):
            entry_id = await store.append(
                TrajectoryEntryType.INNER_THOUGHT, "__inner__", "lapwing",
                {"text": "pondering", "trigger_type": "timer_tick"},
            )
        rows = await store.in_iteration(it_id)
        assert len(rows) == 1
        assert rows[0].id == entry_id
        muts = await mutation_log.query_by_type(MutationType.TRAJECTORY_APPENDED)
        assert muts[0].iteration_id == it_id

    async def test_explicit_iteration_id_overrides_contextvar(
        self, store, mutation_log
    ):
        ctx_id = new_iteration_id()
        explicit_id = new_iteration_id()
        with iteration_context(ctx_id):
            await store.append(
                TrajectoryEntryType.TOOL_CALL, "chat1", "lapwing",
                {"tool_name": "shell_run", "purpose": "ls"},
                related_iteration_id=explicit_id,
            )
        rows_explicit = await store.in_iteration(explicit_id)
        rows_ctx = await store.in_iteration(ctx_id)
        assert len(rows_explicit) == 1
        assert rows_ctx == []

    async def test_no_contextvar_yields_null_iteration(self, store):
        await store.append(
            TrajectoryEntryType.USER_MESSAGE, "chat1", "user", {"text": "x"},
        )
        rows = await store.recent(1)
        assert rows[0].related_iteration_id is None


class TestReadQueries:
    async def test_recent_ordering_oldest_to_newest(self, store):
        for i in range(5):
            await store.append(
                TrajectoryEntryType.USER_MESSAGE, "chat1", "user",
                {"text": f"msg{i}"},
            )
        rows = await store.recent(3)
        assert len(rows) == 3
        texts = [r.content["text"] for r in rows]
        assert texts == ["msg2", "msg3", "msg4"]

    async def test_recent_crosses_chat_ids(self, store):
        await store.append(
            TrajectoryEntryType.USER_MESSAGE, "chat1", "user", {"text": "a"},
        )
        await store.append(
            TrajectoryEntryType.USER_MESSAGE, "chat2", "user", {"text": "b"},
        )
        await store.append(
            TrajectoryEntryType.INNER_THOUGHT, "__inner__", "lapwing",
            {"text": "c"},
        )
        rows = await store.recent(10)
        chats = [r.source_chat_id for r in rows]
        assert chats == ["chat1", "chat2", "__inner__"]

    async def test_relevant_to_chat_default_includes_inner(self, store):
        await store.append(
            TrajectoryEntryType.USER_MESSAGE, "chat1", "user", {"text": "a"},
        )
        await store.append(
            TrajectoryEntryType.USER_MESSAGE, "chat2", "user", {"text": "b"},
        )
        await store.append(
            TrajectoryEntryType.INNER_THOUGHT, "__inner__", "lapwing",
            {"text": "c"},
        )
        rows = await store.relevant_to_chat("chat1", n=10)
        chats = [r.source_chat_id for r in rows]
        assert chats == ["chat1", "__inner__"]

    async def test_relevant_to_chat_exclude_inner(self, store):
        await store.append(
            TrajectoryEntryType.USER_MESSAGE, "chat1", "user", {"text": "a"},
        )
        await store.append(
            TrajectoryEntryType.INNER_THOUGHT, "__inner__", "lapwing",
            {"text": "c"},
        )
        rows = await store.relevant_to_chat(
            "chat1", n=10, include_inner=False
        )
        assert len(rows) == 1
        assert rows[0].source_chat_id == "chat1"

    async def test_relevant_to_chat_limits_take_most_recent(self, store):
        for i in range(6):
            await store.append(
                TrajectoryEntryType.USER_MESSAGE, "chat1", "user",
                {"text": f"m{i}"},
            )
        rows = await store.relevant_to_chat("chat1", n=2)
        texts = [r.content["text"] for r in rows]
        assert texts == ["m4", "m5"]

    async def test_in_iteration_returns_all_within(self, store):
        it_a = new_iteration_id()
        it_b = new_iteration_id()
        with iteration_context(it_a):
            for i in range(3):
                await store.append(
                    TrajectoryEntryType.TOOL_CALL, "chat1", "lapwing",
                    {"tool_name": "t", "purpose": f"a{i}"},
                )
        with iteration_context(it_b):
            await store.append(
                TrajectoryEntryType.TOOL_CALL, "chat1", "lapwing",
                {"tool_name": "t", "purpose": "b"},
            )
        rows_a = await store.in_iteration(it_a)
        rows_b = await store.in_iteration(it_b)
        assert [r.content["purpose"] for r in rows_a] == ["a0", "a1", "a2"]
        assert [r.content["purpose"] for r in rows_b] == ["b"]

    async def test_in_window_filters_by_timestamp(self, store):
        now = time.time()
        await store.append(
            TrajectoryEntryType.USER_MESSAGE, "chat1", "user",
            {"text": "past"}, timestamp=now - 100,
        )
        await store.append(
            TrajectoryEntryType.USER_MESSAGE, "chat1", "user",
            {"text": "present"}, timestamp=now,
        )
        await store.append(
            TrajectoryEntryType.USER_MESSAGE, "chat1", "user",
            {"text": "future"}, timestamp=now + 100,
        )
        rows = await store.in_window(now - 1, now + 1)
        assert [r.content["text"] for r in rows] == ["present"]


class TestRelatedReferences:
    async def test_related_commitment_and_tool_call_persisted(self, store):
        await store.append(
            TrajectoryEntryType.TOOL_CALL, "chat1", "lapwing",
            {"tool_name": "remind_me", "purpose": "set reminder"},
            related_commitment_id="c-123",
            related_tool_call_id="tc-abc",
        )
        rows = await store.recent(1)
        assert rows[0].related_commitment_id == "c-123"
        assert rows[0].related_tool_call_id == "tc-abc"


class TestEmptyStore:
    async def test_recent_on_empty_returns_empty_list(self, store):
        assert await store.recent(10) == []

    async def test_relevant_to_chat_on_empty_returns_empty(self, store):
        assert await store.relevant_to_chat("nobody", n=10) == []
