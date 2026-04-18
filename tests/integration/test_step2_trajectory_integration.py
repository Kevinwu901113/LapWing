"""End-to-end integration for Blueprint v2.0 Step 2.

Exercises the full Step 2 write chain without mocks on the data layer:

    ConversationMemory.append (with TrajectoryStore wired)
        → TrajectoryStore.append (writes trajectory row)
            → StateMutationLog.record TRAJECTORY_APPENDED

    AttentionManager.update
        → StateMutationLog.record ATTENTION_CHANGED

And verifies that the sub-phase B read path (TrajectoryStore.
relevant_to_chat) returns the rows just written with the correct
ordering, that the legacy conversations table is not touched (post-2h
invariant), and that the Step 2e __inner__ remap still routes
consciousness-style writes correctly after the 2i rename.

The tests here are the canonical "did Step 2 actually ship" check —
if they pass, trajectory + commitments + attention are operational and
correctly connected to mutation_log.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from src.core.attention import AttentionManager
from src.core.commitments import CommitmentStatus, CommitmentStore
from src.core.trajectory_compat import trajectory_entries_to_legacy_messages
from src.core.trajectory_store import TrajectoryEntryType, TrajectoryStore
from src.logging.state_mutation_log import (
    MutationType,
    StateMutationLog,
    iteration_context,
    new_iteration_id,
)
from src.memory.conversation import ConversationMemory


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
async def lapwing_db(tmp_path):
    """Bootstrap the shared data/lapwing.db equivalent: conversations table
    (legacy facade) + trajectory + commitments. Mutation log + attention
    manager hang off the same path."""
    db_path = tmp_path / "lapwing.db"
    mutation_log_path = tmp_path / "mutation_log.db"
    logs_dir = tmp_path / "logs"

    mutation_log = StateMutationLog(mutation_log_path, logs_dir=logs_dir)
    await mutation_log.init()

    trajectory = TrajectoryStore(db_path, mutation_log)
    await trajectory.init()

    commitments = CommitmentStore(db_path, mutation_log)
    await commitments.init()

    memory = ConversationMemory(db_path)
    await memory.init_db()
    memory.set_trajectory(trajectory)

    attention = AttentionManager(mutation_log)
    await attention.initialize()

    yield {
        "db_path": db_path,
        "mutation_log": mutation_log,
        "trajectory": trajectory,
        "commitments": commitments,
        "memory": memory,
        "attention": attention,
    }

    await memory.close()
    await trajectory.close()
    await commitments.close()
    await mutation_log.close()


async def _count_conversations(db_path: Path) -> int:
    db = await aiosqlite.connect(db_path)
    try:
        async with db.execute("SELECT COUNT(*) FROM conversations") as cur:
            return (await cur.fetchone())[0]
    finally:
        await db.close()


# ── Full conversational turn ──────────────────────────────────────────

class TestConversationalTurn:
    async def test_user_turn_lands_in_trajectory_and_mutation_log(
        self, lapwing_db
    ):
        memory = lapwing_db["memory"]
        trajectory = lapwing_db["trajectory"]
        mutation_log = lapwing_db["mutation_log"]

        with iteration_context(new_iteration_id(), chat_id="919231551"):
            await memory.append("919231551", "user", "你好", channel="qq", source="qq")
            await memory.append("919231551", "assistant", "嗨", channel="qq", source="qq")

        rows = await trajectory.recent(10)
        types = [r.entry_type for r in rows]
        assert types == [
            TrajectoryEntryType.USER_MESSAGE.value,
            TrajectoryEntryType.ASSISTANT_TEXT.value,
        ]
        assert [r.actor for r in rows] == ["user", "lapwing"]
        assert [r.content["text"] for r in rows] == ["你好", "嗨"]

        muts = await mutation_log.query_by_type(
            MutationType.TRAJECTORY_APPENDED, limit=10,
        )
        # query_by_type returns newest-first
        assert len(muts) == 2
        for m in muts:
            assert m.chat_id == "919231551"
            assert m.iteration_id is not None

    async def test_conversations_table_not_written_post_2h(self, lapwing_db):
        """Step 2h invariant: legacy table stays empty in production flow."""
        memory = lapwing_db["memory"]
        db_path = lapwing_db["db_path"]

        assert await _count_conversations(db_path) == 0
        await memory.append("919231551", "user", "a")
        await memory.append("919231551", "assistant", "b")
        await memory.append("919231551", "user", "c")
        assert await _count_conversations(db_path) == 0

    async def test_read_path_relevant_to_chat_returns_turn(self, lapwing_db):
        """Step 2g: the read path used by brain._load_history returns the
        rows just written, in oldest→newest order."""
        memory = lapwing_db["memory"]
        trajectory = lapwing_db["trajectory"]

        await memory.append("919231551", "user", "first")
        await memory.append("919231551", "assistant", "second")
        await memory.append("919231551", "user", "third")

        rows = await trajectory.relevant_to_chat(
            "919231551", n=10, include_inner=False,
        )
        assert [r.content["text"] for r in rows] == ["first", "second", "third"]

        # Legacy-shape via trajectory_compat, as brain._load_history uses
        legacy = trajectory_entries_to_legacy_messages(rows)
        assert legacy == [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "second"},
            {"role": "user", "content": "third"},
        ]


class TestInnerTickRemap:
    async def test_inner_write_categorised_as_inner_thought(self, lapwing_db):
        """Step 2i: chat_id='__inner__' writes land as INNER_THOUGHT /
        source_chat_id='__inner__', with user→system / assistant→lapwing."""
        memory = lapwing_db["memory"]
        trajectory = lapwing_db["trajectory"]

        await memory.append("__inner__", "user", "[tick prompt]")
        await memory.append("__inner__", "assistant", "pondering")

        rows = await trajectory.recent(10)
        assert [r.entry_type for r in rows] == [
            TrajectoryEntryType.INNER_THOUGHT.value,
            TrajectoryEntryType.INNER_THOUGHT.value,
        ]
        assert [r.actor for r in rows] == ["system", "lapwing"]
        assert all(r.source_chat_id == "__inner__" for r in rows)

    async def test_inner_rows_excluded_from_chat_read_by_default(
        self, lapwing_db
    ):
        memory = lapwing_db["memory"]
        trajectory = lapwing_db["trajectory"]

        await memory.append("919231551", "user", "chat message")
        await memory.append("__inner__", "assistant", "inner thought")

        chat_only = await trajectory.relevant_to_chat(
            "919231551", n=10, include_inner=False,
        )
        assert len(chat_only) == 1
        assert chat_only[0].content["text"] == "chat message"

        with_inner = await trajectory.relevant_to_chat(
            "919231551", n=10, include_inner=True,
        )
        assert len(with_inner) == 2


class TestAttentionIntegration:
    async def test_attention_update_persists_and_emits_mutation(
        self, lapwing_db
    ):
        attention = lapwing_db["attention"]
        mutation_log = lapwing_db["mutation_log"]

        before = attention.get()
        assert before.current_conversation is None
        assert before.mode == "idle"

        await attention.update(current_conversation="919231551", mode="conversing")

        after = attention.get()
        assert after.current_conversation == "919231551"
        assert after.mode == "conversing"
        assert after.last_interaction_at >= before.last_interaction_at

        muts = await mutation_log.query_by_type(MutationType.ATTENTION_CHANGED)
        assert len(muts) == 1
        assert muts[0].payload["new"]["current_conversation"] == "919231551"
        assert muts[0].payload["new"]["mode"] == "conversing"

    async def test_attention_state_survives_restart(self, lapwing_db, tmp_path):
        attention = lapwing_db["attention"]
        mutation_log = lapwing_db["mutation_log"]

        await attention.update(
            current_conversation="919231551",
            current_action="fetching_reminders",
            mode="conversing",
        )
        snapshot = attention.get()

        # Simulate process restart: fresh AttentionManager pointing at same log
        fresh = AttentionManager(mutation_log)
        await fresh.initialize()
        restored = fresh.get()

        assert restored.current_conversation == snapshot.current_conversation
        assert restored.current_action == snapshot.current_action
        assert restored.mode == snapshot.mode


class TestMasterVsStep2Parity:
    """Smoke test: given the same append sequence, the history read the
    2g switch (TrajectoryStore path) returns is equivalent — shape,
    order, content — to what pre-Step-2 would have produced via the
    in-memory cache.

    Input is the exact 4-turn QQ conversation Kevin used for the 2g
    real-conversation validation. The intent is to catch any future
    regression where the read-path switch silently drops or reorders
    messages even though unit tests still pass.

    The two scenarios exercised:
      - 'pre-Step-2 shape' : ConversationMemory with trajectory wired.
                              Each append writes to trajectory; reading
                              via brain's new _load_history path, then
                              the trajectory_compat shim, yields
                              legacy-shape dicts.
      - 'baseline shape'   : ConversationMemory without trajectory.
                              append() falls back to the in-memory cache
                              (the pre-Step-2 behaviour, preserved as the
                              phase-0 / unit-test fallback). memory.get
                              returns that cache.

    If these diverge on the same sequence of writes, sub-phase B
    silently regressed.
    """

    # Verbatim from Kevin's 2g QQ validation (conv#1910-1917), reproduced
    # here so a future reader knows exactly what input triggers this
    # smoke test.
    VALIDATION_TURNS = [
        ("user",      "帮我记一下我下周末去泡温泉"),
        ("assistant", "好 帮你记了\n\n下周末泡温泉\n\n要去之前提醒你吗"),
        ("user",      "你刚刚记了什么"),
        ("assistant", "泡温泉\n\n刚没真的记下来 现在帮你弄\n\n要我设置什么时候提醒你吗"),
        ("user",      "除了那个还有周一提醒我找老师签名"),
        ("assistant", "好 周一4/20提醒你找老师签名\n\n帮你设置了 周一早上九点提醒你\n\n这样不会忘"),
        ("user",      "把你记住的都说一遍"),
        ("assistant", "等我看一下"),
    ]

    async def test_2g_validation_turns_read_identically(self, lapwing_db, tmp_path):
        memory = lapwing_db["memory"]
        trajectory = lapwing_db["trajectory"]

        # Scenario A: trajectory-wired (Step 2g read path)
        for role, content in self.VALIDATION_TURNS:
            await memory.append("919231551", role, content)

        rows = await trajectory.relevant_to_chat(
            "919231551", n=len(self.VALIDATION_TURNS) * 2, include_inner=False,
        )
        step2_history = trajectory_entries_to_legacy_messages(rows)

        # Scenario B: baseline shape (trajectory None, cache-only fallback)
        baseline_memory = ConversationMemory(tmp_path / "baseline.db")
        await baseline_memory.init_db()
        # NO set_trajectory — falls back to cache
        try:
            for role, content in self.VALIDATION_TURNS:
                await baseline_memory.append("919231551", role, content)
            baseline_history = await baseline_memory.get("919231551")
        finally:
            await baseline_memory.close()

        # Parity assertions — shape, order, content must match exactly
        assert len(step2_history) == len(baseline_history) == len(self.VALIDATION_TURNS)

        for i, ((role, content), step2_msg, baseline_msg) in enumerate(zip(
            self.VALIDATION_TURNS, step2_history, baseline_history
        )):
            assert step2_msg["role"] == role, f"turn {i}: step2 role mismatch"
            assert baseline_msg["role"] == role, f"turn {i}: baseline role mismatch"
            assert step2_msg["content"] == content, f"turn {i}: step2 content drift"
            assert baseline_msg["content"] == content, f"turn {i}: baseline content drift"
            # And they must agree with each other
            assert step2_msg == baseline_msg, f"turn {i}: step2 vs baseline diverged"

    async def test_2g_validation_turns_cross_turn_recall_pattern(self, lapwing_db):
        """The 2g validation's whole point was 'does she remember turn 1
        when answering turn 2?'. This asserts the read-path gives the
        caller (brain._load_history) access to all earlier turns at
        each point in the conversation — the necessary condition for
        cross-turn recall."""
        memory = lapwing_db["memory"]
        trajectory = lapwing_db["trajectory"]

        for i, (role, content) in enumerate(self.VALIDATION_TURNS):
            await memory.append("919231551", role, content)

            # After each turn, the read path should expose every prior
            # turn so the model's context includes them
            rows = await trajectory.relevant_to_chat(
                "919231551", n=100, include_inner=False,
            )
            seen = trajectory_entries_to_legacy_messages(rows)
            expected = [
                {"role": r, "content": c}
                for r, c in self.VALIDATION_TURNS[: i + 1]
            ]
            assert seen == expected, (
                f"after turn {i} ({role}: {content[:30]!r}): "
                f"read path returned {len(seen)} msgs, expected {len(expected)}"
            )


class TestCommitmentsOutlet:
    async def test_commitments_wired_but_list_open_empty_pre_step5(
        self, lapwing_db
    ):
        """Step 2b: CommitmentStore is allocated and queryable — but
        there are no callers populating it until Step 5. list_open
        returning [] is the expected shape for the StateSerializer
        'outstanding commitments' region in Step 3."""
        commitments = lapwing_db["commitments"]
        assert await commitments.list_open() == []
        assert await commitments.list_open("919231551") == []

    async def test_commitment_create_emits_mutation(self, lapwing_db):
        """Sanity: the wiring between CommitmentStore and mutation_log
        works — when the Step 5 reviewer loop lands, COMMITMENT_CREATED
        events will flow correctly."""
        commitments = lapwing_db["commitments"]
        mutation_log = lapwing_db["mutation_log"]

        cid = await commitments.create(
            "919231551", "check fixture", source_trajectory_entry_id=1,
        )
        muts = await mutation_log.query_by_type(MutationType.COMMITMENT_CREATED)
        assert len(muts) == 1
        assert muts[0].payload["commitment_id"] == cid
        assert muts[0].chat_id == "919231551"

        await commitments.set_status(cid, CommitmentStatus.FULFILLED.value)
        muts_change = await mutation_log.query_by_type(
            MutationType.COMMITMENT_STATUS_CHANGED
        )
        assert len(muts_change) == 1
