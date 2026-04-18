"""Step 4 M6 — AttentionManager session window."""

from __future__ import annotations

import pytest

from src.core.attention import AttentionManager, AttentionState
from src.logging.state_mutation_log import MutationType, StateMutationLog


@pytest.mark.asyncio
async def test_session_starts_on_idle_to_conversing(tmp_path):
    log = StateMutationLog(tmp_path / "mut.db", logs_dir=tmp_path / "logs")
    await log.init()
    try:
        am = AttentionManager(log)
        await am.initialize()
        assert am.is_in_session() is False
        assert am.current_session_start is None

        await am.update(current_conversation="kev", mode="conversing")
        assert am.is_in_session() is True
        assert am.current_session_start is not None
    finally:
        await log.close()


@pytest.mark.asyncio
async def test_end_session_resets_state(tmp_path):
    log = StateMutationLog(tmp_path / "mut.db", logs_dir=tmp_path / "logs")
    await log.init()
    try:
        am = AttentionManager(log)
        await am.initialize()
        await am.update(current_conversation="kev", mode="conversing")
        assert am.is_in_session() is True

        new_state = await am.end_session()
        assert new_state.session_started_at is None
        assert new_state.mode == "idle"
        assert am.is_in_session() is False
    finally:
        await log.close()


@pytest.mark.asyncio
async def test_session_persists_across_initialize(tmp_path):
    """Session state is restored from the most recent ATTENTION_CHANGED."""
    log = StateMutationLog(tmp_path / "mut.db", logs_dir=tmp_path / "logs")
    await log.init()
    try:
        am1 = AttentionManager(log)
        await am1.initialize()
        await am1.update(current_conversation="kev", mode="conversing")
        ssa = am1.current_session_start
        assert ssa is not None

        # Fresh manager replays the latest mutation.
        am2 = AttentionManager(log)
        await am2.initialize()
        assert am2.current_session_start == ssa
    finally:
        await log.close()


@pytest.mark.asyncio
async def test_end_session_emits_session_boundary_mutation(tmp_path):
    log = StateMutationLog(tmp_path / "mut.db", logs_dir=tmp_path / "logs")
    await log.init()
    try:
        am = AttentionManager(log)
        await am.initialize()
        await am.update(current_conversation="kev", mode="conversing")

        await am.end_session()
        muts = await log.query_by_type(MutationType.ATTENTION_CHANGED, limit=1)
        assert muts
        latest = muts[0]
        assert latest.payload.get("session_boundary") == "ended"
    finally:
        await log.close()


@pytest.mark.asyncio
async def test_end_session_idempotent_when_already_idle(tmp_path):
    log = StateMutationLog(tmp_path / "mut.db", logs_dir=tmp_path / "logs")
    await log.init()
    try:
        am = AttentionManager(log)
        await am.initialize()
        # No conversation started; end_session should be a no-op.
        before = am.get()
        new = await am.end_session()
        assert new is before
    finally:
        await log.close()


@pytest.mark.asyncio
async def test_back_to_back_idle_to_conversing_preserves_session(tmp_path):
    """Going idle → conversing twice without explicit end starts only one session."""
    log = StateMutationLog(tmp_path / "mut.db", logs_dir=tmp_path / "logs")
    await log.init()
    try:
        am = AttentionManager(log)
        await am.initialize()
        await am.update(current_conversation="kev", mode="conversing")
        first = am.current_session_start

        # Same session: switching to acting then back to conversing without
        # end_session() must NOT restart the session timer.
        await am.update(mode="acting")
        await am.update(mode="conversing")
        assert am.current_session_start == first
    finally:
        await log.close()
