"""Step 3 M3.c — end-to-end parity smoke across all five prompt layers.

Feeds the same 8-turn QQ validation sequence from Step 2 plus a
simulated consciousness tick through the full StateViewBuilder →
StateSerializer pipeline, then asserts that:

  * all five prompt layers (identity / trajectory / commitments /
    memory / attention) are represented in the serialize output
  * forbidden tokens (get_context, trajectory_compat, conversations,
    session_id — PromptSnapshot was deleted in M3.b so no cache_key
    either) do not appear anywhere in the rendered bytes

The M1 parity smoke test already proves turn preservation on the
user-facing path alone; this broader test catches regressions where a
future cleanup changes one layer's shape without breaking the simpler
smoke.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.attention import AttentionManager
from src.core.commitments import CommitmentStore
from src.core.state_serializer import serialize
from src.core.state_view_builder import StateViewBuilder
from src.core.trajectory_store import TrajectoryEntryType, TrajectoryStore
from src.logging.state_mutation_log import StateMutationLog
from src.memory.conversation import ConversationMemory


_VALIDATION_TURNS = [
    ("user",      "帮我记一下我下周末去泡温泉"),
    ("assistant", "好 帮你记了\n\n下周末泡温泉\n\n要去之前提醒你吗"),
    ("user",      "你刚刚记了什么"),
    ("assistant", "泡温泉\n\n刚没真的记下来 现在帮你弄\n\n要我设置什么时候提醒你吗"),
    ("user",      "除了那个还有周一提醒我找老师签名"),
    ("assistant", "好 周一4/20提醒你找老师签名\n\n帮你设置了 周一早上九点提醒你\n\n这样不会忘"),
    ("user",      "把你记住的都说一遍"),
    ("assistant", "等我看一下"),
]

# Forbidden tokens — anything left over from the old read paths
# should never appear in the rendered prompt. If one shows up, the
# refactor regressed (PromptSnapshot is deleted → no cache_key either).
_FORBIDDEN_TOKENS = (
    "get_context",
    "trajectory_compat",
    "conversations",  # the table name, not the word "conversation"
    "session_id",
)


@pytest.fixture
async def wired_stack(tmp_path: Path):
    db_path = tmp_path / "lapwing.db"
    mutation_log = StateMutationLog(tmp_path / "mut.db", logs_dir=tmp_path / "logs")
    await mutation_log.init()

    trajectory = TrajectoryStore(db_path, mutation_log)
    await trajectory.init()

    commitments = CommitmentStore(db_path, mutation_log)
    await commitments.init()
    commitment = await commitments.create(
        target_chat_id="919231551",
        content="记得提醒 Kevin 周一找老师签名",
        source_trajectory_entry_id=-1,
        reasoning="validation-turn 5 promised this",
    )

    memory = ConversationMemory(db_path)
    await memory.init_db()
    memory.set_trajectory(trajectory)

    attention = AttentionManager(mutation_log)
    await attention.initialize()
    await attention.update(current_conversation="919231551", mode="conversing")

    # Identity fixtures large enough to show up in the render.
    soul_path = tmp_path / "soul.md"
    soul_path.write_text("# Lapwing\n我是 Lapwing。", encoding="utf-8")
    const_path = tmp_path / "constitution.md"
    const_path.write_text("# 宪法\n不得编造历史。", encoding="utf-8")

    builder = StateViewBuilder(
        soul_path=soul_path,
        constitution_path=const_path,
        voice_prompt_name="lapwing_voice",
        attention_manager=attention,
        trajectory_store=trajectory,
        commitment_store=commitments,
    )

    try:
        yield {
            "memory": memory,
            "trajectory": trajectory,
            "commitments": commitments,
            "attention": attention,
            "builder": builder,
            "commitment_id": commitment,
        }
    finally:
        await memory.close()
        await commitments.close()
        await trajectory.close()
        await mutation_log.close()


class TestStep3EndToEndParity:
    async def test_all_five_layers_represented(self, wired_stack):
        memory = wired_stack["memory"]
        builder = wired_stack["builder"]
        trajectory = wired_stack["trajectory"]

        # Write the 8 validation turns.
        for role, content in _VALIDATION_TURNS:
            await memory.append("919231551", role, content)

        # Simulate one consciousness-loop tick — an INNER_THOUGHT row.
        await trajectory.append(
            TrajectoryEntryType.INNER_THOUGHT,
            source_chat_id="__inner__",
            actor="system",
            content={"text": "[内部意识 tick] free time"},
        )

        sv = await builder.build_for_chat("919231551")
        out = serialize(sv)
        blob = out.system_prompt + "\n".join(
            str(m.get("content", "")) for m in out.messages
        )

        # 1. identity layer (soul + constitution)
        assert "我是 Lapwing" in out.system_prompt
        assert "不得编造历史" in out.system_prompt

        # 2. trajectory_window — every validation-turn content present
        for role, content in _VALIDATION_TURNS:
            assert any(
                m.get("role") == role and m.get("content") == content
                for m in out.messages
            ), f"missing validation turn: ({role}) {content!r}"

        # 3. commitments — the promise we created should render
        assert "记得提醒 Kevin 周一找老师签名" in out.system_prompt
        # Step 5: 标题改为通道无关的"我对用户的承诺"
        assert "我对用户的承诺" in out.system_prompt

        # 4. memory — layer is intentionally empty in the chat builder
        #    (StateSerializer elides the section when snippets == ()).
        #    Test that elision stays correct: no stray "## 记忆片段"
        #    header when nothing to show.
        assert sv.memory_snippets.snippets == ()
        assert "## 记忆片段" not in out.system_prompt

        # 5. attention — current conversation + mode reflected in the
        #    runtime-state block
        assert "## 当前状态" in out.system_prompt
        assert "台北时间" in out.system_prompt

        # Full blob (system + messages) contains no forbidden tokens
        for token in _FORBIDDEN_TOKENS:
            assert token not in blob, f"forbidden token {token!r} appeared in output"

    async def test_inner_tick_renders_via_build_for_inner(self, wired_stack):
        """The consciousness-loop render path must also clear the
        forbidden-token check — its trajectory window and attention
        rendering use the same helpers."""
        trajectory = wired_stack["trajectory"]
        builder = wired_stack["builder"]

        await trajectory.append(
            TrajectoryEntryType.INNER_THOUGHT,
            source_chat_id="__inner__",
            actor="system",
            content={"text": "[内部意识 tick] free time"},
        )

        sv = await builder.build_for_inner()
        out = serialize(sv)
        blob = out.system_prompt + "\n".join(
            str(m.get("content", "")) for m in out.messages
        )
        for token in _FORBIDDEN_TOKENS:
            assert token not in blob

    async def test_determinism_two_serializes_identical(self, wired_stack):
        """serialize() must be a pure function — two calls with the same
        StateView produce identical bytes. This guarantee is what makes
        the parity smoke assertions above reliable."""
        memory = wired_stack["memory"]
        builder = wired_stack["builder"]

        for role, content in _VALIDATION_TURNS:
            await memory.append("919231551", role, content)

        sv = await builder.build_for_chat("919231551")
        a = serialize(sv)
        b = serialize(sv)
        assert a.system_prompt == b.system_prompt
        assert a.messages == b.messages
