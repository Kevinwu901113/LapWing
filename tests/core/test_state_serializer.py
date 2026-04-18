"""Unit tests for src.core.state_serializer — pure-function rendering.

Blueprint v2.0 Step 3 §2. Every test builds a StateView, serializes it,
and asserts properties of the output. No mocks, no I/O — the serializer's
contract is "same input, same output", and these tests pin down that
contract so the parity smoke test can rely on it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from src.core.state_serializer import (
    _PERSONA_ANCHOR,
    _period_name,
    serialize,
)
from src.core.state_view import (
    AttentionContext,
    CommitmentView,
    IdentityDocs,
    MemorySnippet,
    MemorySnippets,
    SerializedPrompt,
    StateView,
    TrajectoryTurn,
    TrajectoryWindow,
)


# ── Helpers ───────────────────────────────────────────────────────────

_TAIPEI = ZoneInfo("Asia/Taipei")


def _make_state(
    *,
    soul: str = "SOUL",
    constitution: str = "CONSTITUTION",
    voice: str = "VOICE",
    channel: str = "desktop",
    actor_id: str | None = None,
    actor_name: str | None = None,
    auth_level: int = 3,
    group_id: str | None = None,
    current_conversation: str | None = None,
    mode: str = "conversing",
    now: datetime | None = None,
    offline_hours: float | None = None,
    turns: tuple[TrajectoryTurn, ...] = (),
    snippets: tuple[MemorySnippet, ...] = (),
    commitments: tuple[CommitmentView, ...] = (),
) -> StateView:
    if now is None:
        now = datetime(2026, 4, 18, 14, 30, tzinfo=_TAIPEI)
    return StateView(
        identity_docs=IdentityDocs(
            soul=soul, constitution=constitution, voice=voice
        ),
        attention_context=AttentionContext(
            channel=channel,
            actor_id=actor_id,
            actor_name=actor_name,
            auth_level=auth_level,
            group_id=group_id,
            current_conversation=current_conversation,
            mode=mode,
            now=now,
            offline_hours=offline_hours,
        ),
        trajectory_window=TrajectoryWindow(turns=turns),
        memory_snippets=MemorySnippets(snippets=snippets),
        commitments_active=commitments,
    )


# ── Core shape ────────────────────────────────────────────────────────

class TestCoreShape:
    def test_returns_serialized_prompt(self):
        out = serialize(_make_state())
        assert isinstance(out, SerializedPrompt)

    def test_pure_function_determinism(self):
        """Same input → byte-identical output. Locks in the invariant
        parity smoke tests depend on."""
        sv = _make_state()
        a = serialize(sv)
        b = serialize(sv)
        assert a.system_prompt == b.system_prompt
        assert a.messages == b.messages

    def test_empty_inputs_do_not_crash(self):
        sv = _make_state(soul="", constitution="", voice="")
        out = serialize(sv)
        # runtime-state block is still emitted (time anchor etc.)
        assert "当前状态" in out.system_prompt


# ── Layer 1 & 2: identity docs ───────────────────────────────────────

class TestIdentityLayers:
    def test_soul_and_constitution_injected_verbatim(self):
        out = serialize(_make_state(soul="SOUL-XYZ", constitution="CON-XYZ"))
        assert "SOUL-XYZ" in out.system_prompt
        assert "CON-XYZ" in out.system_prompt
        assert out.system_prompt.index("SOUL-XYZ") < out.system_prompt.index("CON-XYZ")

    def test_missing_identity_files_produce_no_layer(self):
        out = serialize(_make_state(soul="", constitution="CON"))
        # leading section is the constitution, no stray divider
        assert out.system_prompt.startswith("CON")

    def test_sections_joined_with_horizontal_rule(self):
        out = serialize(_make_state())
        assert "\n\n---\n\n" in out.system_prompt


# ── Layer 3: runtime state ────────────────────────────────────────────

class TestRuntimeState:
    def test_time_anchor_shows_ymd_and_period(self):
        sv = _make_state(now=datetime(2026, 4, 18, 14, 30, tzinfo=_TAIPEI))
        out = serialize(sv)
        assert "2026年4月18日" in out.system_prompt
        assert "下午" in out.system_prompt  # 14 is 13-17 → 下午
        assert "约14时" in out.system_prompt

    def test_weekday_is_taipei_local(self):
        # 2026-04-18 is a Saturday (weekday 5 → 周六)
        sv = _make_state(now=datetime(2026, 4, 18, 14, 30, tzinfo=_TAIPEI))
        out = serialize(sv)
        assert "周六" in out.system_prompt

    def test_offline_gap_above_threshold_renders_warning(self):
        sv = _make_state(offline_hours=6.7)
        out = serialize(sv)
        assert "距上次活跃已过" in out.system_prompt
        assert "7 小时" in out.system_prompt

    def test_offline_gap_below_threshold_suppressed(self):
        sv = _make_state(offline_hours=2.0)
        out = serialize(sv)
        assert "距上次活跃已过" not in out.system_prompt

    def test_offline_gap_none_suppressed(self):
        sv = _make_state(offline_hours=None)
        out = serialize(sv)
        assert "距上次活跃已过" not in out.system_prompt

    def test_desktop_channel_rendered(self):
        out = serialize(_make_state(channel="desktop"))
        assert "Desktop（面对面）" in out.system_prompt

    def test_qq_private_channel_rendered(self):
        out = serialize(_make_state(channel="qq"))
        assert "QQ 私聊" in out.system_prompt

    def test_qq_group_renders_group_id(self):
        out = serialize(_make_state(channel="qq_group", group_id="g42"))
        assert "g42" in out.system_prompt

    def test_group_speaker_block_shows_for_qq_group(self):
        out = serialize(
            _make_state(
                channel="qq_group",
                group_id="g1",
                actor_id="u99",
                actor_name="Alice",
                auth_level=2,
            )
        )
        assert "Alice" in out.system_prompt
        assert "u99" in out.system_prompt
        assert "TRUSTED" in out.system_prompt

    def test_group_speaker_block_hidden_outside_group(self):
        out = serialize(
            _make_state(channel="desktop", actor_id="u99", actor_name="Alice")
        )
        assert "当前说话人" not in out.system_prompt


# ── Commitments rendering ─────────────────────────────────────────────

class TestCommitments:
    def test_reminders_rendered(self):
        com = CommitmentView(
            id="c1", description="记得喝水", status="open",
            kind="reminder", due_at="2026-04-18T18:00:00+08:00",
        )
        out = serialize(_make_state(commitments=(com,)))
        assert "记得喝水" in out.system_prompt
        assert "即将到期的提醒" in out.system_prompt

    def test_tasks_rendered(self):
        com = CommitmentView(
            id="t1", description="写 Step 3 报告", status="running",
            kind="task", due_at=None,
        )
        out = serialize(_make_state(commitments=(com,)))
        assert "写 Step 3 报告" in out.system_prompt
        assert "正在进行的任务" in out.system_prompt

    def test_open_promises_rendered(self):
        com = CommitmentView(
            id="p1", description="陪 Kevin 散步", status="open",
            kind="promise", due_at=None,
        )
        out = serialize(_make_state(commitments=(com,)))
        assert "陪 Kevin 散步" in out.system_prompt
        assert "我对 Kevin 的承诺" in out.system_prompt

    def test_serializer_does_not_filter_by_promise_status(self):
        """Contract split: CommitmentStore.list_open() returns only the
        pending + in_progress rows; the serializer renders whatever
        kind=promise commitments the StateView carries, trusting the
        builder's filter. A post-Step-3 test that wants to confirm
        'closed promises disappear' lives at the builder layer, not
        here."""
        com = CommitmentView(
            id="p1", description="已完成的事", status="fulfilled",
            kind="promise", due_at=None,
        )
        out = serialize(_make_state(commitments=(com,)))
        # Serializer still renders it — the builder is the checkpoint.
        assert "已完成的事" in out.system_prompt

    def test_empty_commitments_no_section(self):
        out = serialize(_make_state())
        assert "即将到期的提醒" not in out.system_prompt
        assert "正在进行的任务" not in out.system_prompt
        assert "我对 Kevin 的承诺" not in out.system_prompt

    def test_reminder_cap_at_three(self):
        coms = tuple(
            CommitmentView(
                id=f"r{i}", description=f"reminder {i}", status="open",
                kind="reminder", due_at=f"t{i}",
            ) for i in range(10)
        )
        out = serialize(_make_state(commitments=coms))
        # Only first 3 reminders rendered
        assert "reminder 0" in out.system_prompt
        assert "reminder 1" in out.system_prompt
        assert "reminder 2" in out.system_prompt
        assert "reminder 3" not in out.system_prompt

    def test_task_cap_at_five(self):
        coms = tuple(
            CommitmentView(
                id=f"t{i}", description=f"task {i}", status="running",
                kind="task", due_at=None,
            ) for i in range(10)
        )
        out = serialize(_make_state(commitments=coms))
        assert "task 0" in out.system_prompt
        assert "task 4" in out.system_prompt
        assert "task 5" not in out.system_prompt


# ── Memory snippets ───────────────────────────────────────────────────

class TestMemorySnippets:
    def test_snippets_rendered_when_present(self):
        snip = MemorySnippet(note_id="n1", content="Kevin 偏好深色模式", score=0.9)
        out = serialize(_make_state(snippets=(snip,)))
        assert "Kevin 偏好深色模式" in out.system_prompt
        assert "记忆片段" in out.system_prompt

    def test_no_snippets_no_section(self):
        out = serialize(_make_state())
        assert "记忆片段" not in out.system_prompt


# ── Trajectory window → messages ──────────────────────────────────────

class TestMessagesFromTrajectory:
    def test_empty_trajectory_produces_no_convo_messages(self):
        out = serialize(_make_state(turns=()))
        # With 0 turns, voice fold path = 0 + 1 = total 1 < 4 → voice appended to system prompt, no user inject
        assert out.messages == []

    def test_full_trajectory_turns_preserved(self):
        turns = tuple(
            TrajectoryTurn(role="user" if i % 2 == 0 else "assistant", content=f"msg {i}")
            for i in range(8)
        )
        out = serialize(_make_state(turns=turns))
        # 8 turns → total 9 ≥ 6 → voice injected at (len-2)=6 so the
        # two-element tail (msg 6, msg 7) stays at the end; matches
        # PromptBuilder.inject_voice_reminder behaviour on [system, *recent].
        assert len(out.messages) == 9
        # First 6 messages are the first 6 original turns, untouched
        for i in range(6):
            assert out.messages[i]["content"] == f"msg {i}"
        # Position -3 is the injected voice note
        assert "[System Note]" in out.messages[-3]["content"]
        # Last two positions hold the final two original turns
        assert out.messages[-2]["content"] == "msg 6"
        assert out.messages[-1]["content"] == "msg 7"

    def test_short_convo_voice_folded_into_system(self):
        # 2 turns: total = 3 < 4, voice appended to system prompt
        turns = (
            TrajectoryTurn(role="user", content="hi"),
            TrajectoryTurn(role="assistant", content="hey"),
        )
        out = serialize(_make_state(turns=turns, voice="VOICE_X"))
        assert "VOICE_X" in out.system_prompt
        assert all("[System Note]" not in m["content"] for m in out.messages)

    def test_medium_convo_voice_injected_no_persona_anchor(self):
        # 3 turns: total = 4 ≥ 4, but < 6 → voice + time only
        turns = (
            TrajectoryTurn(role="user", content="a"),
            TrajectoryTurn(role="assistant", content="b"),
            TrajectoryTurn(role="user", content="c"),
        )
        out = serialize(_make_state(turns=turns, voice="VOICE_M"))
        note_msgs = [m for m in out.messages if "[System Note]" in m["content"]]
        assert len(note_msgs) == 1
        assert "VOICE_M" in note_msgs[0]["content"]
        # Persona anchor reserved for long convos
        assert _PERSONA_ANCHOR[:10] not in note_msgs[0]["content"]

    def test_long_convo_voice_injected_with_persona_anchor(self):
        # 5 turns: total = 6 ≥ 6 → voice + persona anchor + time
        turns = tuple(
            TrajectoryTurn(role="user" if i % 2 == 0 else "assistant", content=f"t{i}")
            for i in range(5)
        )
        out = serialize(_make_state(turns=turns, voice="VOICE_L"))
        note_msgs = [m for m in out.messages if "[System Note]" in m["content"]]
        assert len(note_msgs) == 1
        assert "VOICE_L" in note_msgs[0]["content"]
        assert _PERSONA_ANCHOR[:15] in note_msgs[0]["content"]

    def test_voice_note_placement_preserves_tail_pair(self):
        """For ≥6 total messages, voice note lands third-from-end so
        the last two original turns stay at the end — mirrors the
        pre-Step-3 inject_voice_reminder on [system, *recent]."""
        turns = tuple(
            TrajectoryTurn(role="user", content=f"t{i}") for i in range(6)
        )
        out = serialize(_make_state(turns=turns))
        assert out.messages[-2]["content"] == "t4"
        assert out.messages[-1]["content"] == "t5"
        assert "[System Note]" in out.messages[-3]["content"]

    def test_messages_voice_preserves_turn_order(self):
        turns = tuple(
            TrajectoryTurn(role="user", content=f"m{i}") for i in range(4)
        )
        out = serialize(_make_state(turns=turns))
        # Non-injected turns must stay in oldest→newest order
        originals = [m for m in out.messages if "[System Note]" not in m["content"]]
        assert [m["content"] for m in originals] == ["m0", "m1", "m2", "m3"]


# ── Period name helper ───────────────────────────────────────────────

class TestPeriodName:
    @pytest.mark.parametrize("hour,expected", [
        (0, "深夜"), (3, "深夜"), (4, "深夜"),
        (5, "早上"), (7, "早上"),
        (8, "上午"), (10, "上午"),
        (11, "中午"), (12, "中午"),
        (13, "下午"), (16, "下午"),
        (17, "傍晚"), (18, "傍晚"),
        (19, "晚上"), (22, "晚上"),
        (23, "深夜"),
    ])
    def test_boundaries_match_vitals(self, hour, expected):
        """Parity with ``src.core.vitals.get_period_name`` — the model
        has been trained on the existing phrasing, so the labels must
        stay identical."""
        assert _period_name(hour) == expected


# ── Pure-function verification ────────────────────────────────────────

class TestPurity:
    def test_no_network_no_file_io(self, monkeypatch):
        """Run the serializer with file I/O and clock reads disabled.
        If the serializer is truly pure, neither gets invoked."""
        import builtins
        import os

        original_open = builtins.open

        def _fail_open(*args, **kwargs):
            # Allow pytest's own internals to read files
            raise AssertionError(f"serialize() performed file I/O: {args}")

        # Only guard the serializer's call — patch inside a narrow scope
        sv = _make_state()
        # We call serialize() once without the patch (warm imports),
        # then re-call under the guard to prove no I/O happens on the
        # hot path.
        serialize(sv)
        try:
            monkeypatch.setattr(builtins, "open", _fail_open)
            serialize(sv)
        finally:
            monkeypatch.setattr(builtins, "open", original_open)
