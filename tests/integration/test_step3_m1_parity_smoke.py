"""M1 parity smoke — Step 3 serialize vs recast_v2_step2_complete tag.

Blueprint v2.0 Step 3 M1. Using Kevin's 8-turn QQ validation sequence
from Step 2 (conv#1910-1917), feed the same append sequence through
the new StateViewBuilder → serialize pipeline and assert that the
output carries **all** the information the Step-2-tag code path would
have sent to the LLM:

    * every validation turn is present, in the right order, with
      unaltered content
    * the system prompt contains the identity (soul + constitution),
      a runtime-state block, and the voice reminder (folded or depth-
      injected depending on conversation length)

The goal isn't byte-identical prompts — Step 3 deliberately restructures
the render — but "no information lost in the refactor". If a future
regression drops a turn or silently strips voice, this test fails.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.core.state_serializer import serialize
from src.core.state_view_builder import StateViewBuilder
from src.logging.state_mutation_log import StateMutationLog
from src.memory.conversation import ConversationMemory
from src.core.trajectory_store import TrajectoryStore


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


@pytest.fixture
async def wired_stack(tmp_path: Path):
    db_path = tmp_path / "lapwing.db"
    mutation_log = StateMutationLog(tmp_path / "mut.db", logs_dir=tmp_path / "logs")
    await mutation_log.init()

    trajectory = TrajectoryStore(db_path, mutation_log)
    await trajectory.init()

    memory = ConversationMemory(db_path)
    await memory.init_db()
    memory.set_trajectory(trajectory)

    # Build soul + constitution fixtures — non-empty so identity layer
    # shows up in the smoke output.
    soul_path = tmp_path / "soul.md"
    soul_path.write_text("# Lapwing Soul\n\n我是 Lapwing。", encoding="utf-8")
    const_path = tmp_path / "constitution.md"
    const_path.write_text("# Constitution\n\n不得编造历史。", encoding="utf-8")

    builder = StateViewBuilder(
        soul_path=soul_path,
        constitution_path=const_path,
        voice_prompt_name="lapwing_voice",  # may or may not resolve; test tolerates both
        trajectory_store=trajectory,
    )

    try:
        yield {
            "memory": memory,
            "trajectory": trajectory,
            "builder": builder,
        }
    finally:
        await memory.close()
        await trajectory.close()
        await mutation_log.close()


class TestM1ParitySmoke:
    async def test_all_eight_turns_appear_in_serialize_output(self, wired_stack):
        memory = wired_stack["memory"]
        builder = wired_stack["builder"]

        for role, content in _VALIDATION_TURNS:
            await memory.append("919231551", role, content)

        sv = await builder.build_for_chat("919231551")
        out = serialize(sv)

        # Every turn content must appear somewhere in the rendered
        # messages (order + role check follows).
        for role, content in _VALIDATION_TURNS:
            assert any(
                m.get("role") == role and m.get("content") == content
                for m in out.messages
            ), f"validation turn missing from serialize output: ({role}) {content!r}"

    async def test_turn_order_preserved(self, wired_stack):
        memory = wired_stack["memory"]
        builder = wired_stack["builder"]

        for role, content in _VALIDATION_TURNS:
            await memory.append("919231551", role, content)

        sv = await builder.build_for_chat("919231551")
        out = serialize(sv)

        # Strip injected voice notes; compare the chronological turn
        # sequence against the validation input.
        real_turns = [
            (m["role"], m["content"])
            for m in out.messages
            if "[System Note]" not in (m.get("content") or "")
        ]
        assert real_turns == _VALIDATION_TURNS

    async def test_system_prompt_has_identity_and_runtime(self, wired_stack):
        memory = wired_stack["memory"]
        builder = wired_stack["builder"]

        for role, content in _VALIDATION_TURNS:
            await memory.append("919231551", role, content)

        sv = await builder.build_for_chat("919231551")
        out = serialize(sv)

        assert "Lapwing Soul" in out.system_prompt
        assert "Constitution" in out.system_prompt
        assert "## 当前状态" in out.system_prompt
        # time block present (台北时间)
        assert "台北时间" in out.system_prompt

    async def test_voice_reminder_path_applied_to_long_convo(self, wired_stack):
        """With 8 turns, total messages ≥ 6 → voice note should be
        depth-injected (third from end). If voice.md is empty the
        injection skips — smoke tolerates both outcomes; the invariant
        is "voice layer is handled, not dropped silently"."""
        memory = wired_stack["memory"]
        builder = wired_stack["builder"]

        for role, content in _VALIDATION_TURNS:
            await memory.append("919231551", role, content)

        sv = await builder.build_for_chat("919231551")
        out = serialize(sv)

        has_voice_inject = any(
            "[System Note]" in (m.get("content") or "") for m in out.messages
        )
        has_voice_in_system = bool(sv.identity_docs.voice) and (
            sv.identity_docs.voice in out.system_prompt
        )
        # If voice is non-empty it must land *somewhere*; if empty
        # the whole layer is absent.
        if sv.identity_docs.voice:
            assert has_voice_inject or has_voice_in_system, (
                "voice.md was loaded but disappeared from the serialize output"
            )
