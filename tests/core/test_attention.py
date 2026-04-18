"""Unit tests for AttentionManager.

Covers Blueprint v2.0 Step 2 §4 requirements:
  1. Default state shape
  2. UNSET sentinel — partial updates don't touch other fields
  3. update() emits ATTENTION_CHANGED with old/new/changed_fields
  4. initialize() rebuilds state from latest ATTENTION_CHANGED
  5. initialize() is idempotent
  6. mode validation
  7. last_interaction_at / last_action_at auto-stamping
"""

from __future__ import annotations

import time

import pytest

from src.core.attention import (
    UNSET,
    AttentionManager,
    AttentionState,
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
async def manager(mutation_log):
    m = AttentionManager(mutation_log)
    await m.initialize()
    yield m


class TestDefault:
    async def test_default_state_is_idle(self, manager):
        state = manager.get()
        assert state.current_conversation is None
        assert state.current_action is None
        assert state.mode == "idle"
        assert state.last_interaction_at > 0
        assert state.last_action_at > 0


class TestUnsetSentinel:
    async def test_update_without_args_noop(self, manager, mutation_log):
        before = manager.get()
        result = await manager.update()
        after = manager.get()
        assert result is before  # returns old unchanged
        assert after is before
        muts = await mutation_log.query_by_type(MutationType.ATTENTION_CHANGED)
        assert muts == []

    async def test_update_only_conversation_leaves_action(self, manager):
        await manager.update(current_action="writing_email")
        await manager.update(current_conversation="chat1")
        state = manager.get()
        assert state.current_conversation == "chat1"
        assert state.current_action == "writing_email"  # preserved

    async def test_update_explicit_none_clears_field(self, manager):
        await manager.update(current_action="something")
        assert manager.get().current_action == "something"
        await manager.update(current_action=None)
        assert manager.get().current_action is None


class TestMutationEmission:
    async def test_update_emits_with_old_new_changed_fields(
        self, manager, mutation_log
    ):
        it_id = new_iteration_id()
        with iteration_context(it_id):
            await manager.update(current_conversation="chat1", mode="conversing")
        muts = await mutation_log.query_by_type(MutationType.ATTENTION_CHANGED)
        assert len(muts) == 1
        p = muts[0].payload
        assert p["old"]["current_conversation"] is None
        assert p["new"]["current_conversation"] == "chat1"
        assert p["new"]["mode"] == "conversing"
        assert "current_conversation" in p["changed_fields"]
        assert "mode" in p["changed_fields"]
        assert "last_interaction_at" in p["changed_fields"]
        assert muts[0].iteration_id == it_id
        assert muts[0].chat_id == "chat1"

    async def test_update_same_value_still_stamps_timestamp(
        self, manager, mutation_log
    ):
        await manager.update(current_conversation="chat1")
        await manager.update(current_conversation="chat1")
        muts = await mutation_log.query_by_type(MutationType.ATTENTION_CHANGED)
        # Both update calls emitted (stamp updates are non-trivial state change)
        assert len(muts) == 2
        # But "current_conversation" shouldn't appear in changed_fields
        # for the second call, only "last_interaction_at" does.
        second_call_changes = muts[0].payload["changed_fields"]
        assert "current_conversation" not in second_call_changes
        assert "last_interaction_at" in second_call_changes


class TestAutoStamping:
    async def test_current_conversation_updates_last_interaction_at(
        self, manager
    ):
        before = manager.get().last_interaction_at
        # Sleep briefly so timestamp advances
        time.sleep(0.01)
        await manager.update(current_conversation="chat1")
        after = manager.get().last_interaction_at
        assert after > before

    async def test_current_action_updates_last_action_at(self, manager):
        before = manager.get().last_action_at
        time.sleep(0.01)
        await manager.update(current_action="running_tool")
        after = manager.get().last_action_at
        assert after > before

    async def test_mode_change_alone_does_not_restamp(self, manager):
        i_before = manager.get().last_interaction_at
        a_before = manager.get().last_action_at
        time.sleep(0.01)
        await manager.update(mode="acting")
        state = manager.get()
        assert state.last_interaction_at == i_before
        assert state.last_action_at == a_before


class TestValidation:
    async def test_rejects_invalid_mode(self, manager):
        with pytest.raises(ValueError):
            await manager.update(mode="wandering")


class TestInitializeFromLog:
    async def test_restore_latest_state_after_restart(
        self, tmp_path, mutation_log
    ):
        # Seed initial state
        m1 = AttentionManager(mutation_log)
        await m1.initialize()
        await m1.update(current_conversation="chat1", mode="conversing")
        await m1.update(current_action="fetching_docs")
        before_restart = m1.get()

        # Fresh manager pointing at same mutation_log
        m2 = AttentionManager(mutation_log)
        await m2.initialize()
        after_restart = m2.get()

        assert after_restart.current_conversation == before_restart.current_conversation
        assert after_restart.current_action == before_restart.current_action
        assert after_restart.mode == before_restart.mode
        assert after_restart.last_interaction_at == before_restart.last_interaction_at
        assert after_restart.last_action_at == before_restart.last_action_at

    async def test_initialize_idempotent(self, manager):
        await manager.update(current_conversation="chat1")
        first = manager.get()
        await manager.initialize()  # second call
        await manager.initialize()  # third call
        assert manager.get() is first  # state unchanged

    async def test_initialize_without_history_keeps_default(
        self, mutation_log
    ):
        m = AttentionManager(mutation_log)
        await m.initialize()
        state = m.get()
        assert state.current_conversation is None
        assert state.mode == "idle"


class TestStateImmutability:
    async def test_state_is_frozen_dataclass(self, manager):
        state = manager.get()
        with pytest.raises(Exception):  # FrozenInstanceError
            state.mode = "conversing"  # type: ignore[misc]

    async def test_state_payload_roundtrip(self, manager):
        state = manager.get()
        roundtripped = AttentionState.from_payload(state.to_payload())
        assert roundtripped == state
