"""Unit tests for src.core.state_serializer — pure-function rendering.

Blueprint A prompt-caching overhaul. Voice is always in the system prompt
(no depth injection into messages). Offline-gap threshold raised to 12
hours. Overdue promise rendering toned down (no ⚠️ prefix). _PERSONA_ANCHOR
removed: voice.md alone enforces speaking style; "记住你是 X" framing was
priming a roleplay posture rather than natural identity.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from src.core.state_serializer import (
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
        """Same input → byte-identical output."""
        sv = _make_state()
        a = serialize(sv)
        b = serialize(sv)
        assert a.system_prompt == b.system_prompt
        assert a.messages == b.messages

    def test_empty_inputs_do_not_crash(self):
        sv = _make_state(soul="", constitution="", voice="")
        out = serialize(sv)
        assert "当前状态" in out.system_prompt

    def test_stable_prefix_is_deterministic(self):
        """The stable prefix (soul + constitution + voice) is byte-identical
        across calls with the same identity docs — this is what makes
        prompt caching work."""
        sv1 = _make_state(offline_hours=None)
        sv2 = _make_state(offline_hours=15.0)  # different dynamic state
        out1 = serialize(sv1)
        out2 = serialize(sv2)
        # voice ("VOICE" from _make_state default) is the last stable section.
        voice_end_1 = out1.system_prompt.index("VOICE") + len("VOICE")
        voice_end_2 = out2.system_prompt.index("VOICE") + len("VOICE")
        assert out1.system_prompt[:voice_end_1] == out2.system_prompt[:voice_end_2]


# ── Layer 1 & 2: identity docs ───────────────────────────────────────

class TestIdentityLayers:
    def test_soul_and_constitution_injected_verbatim(self):
        out = serialize(_make_state(soul="SOUL-XYZ", constitution="CON-XYZ"))
        assert "SOUL-XYZ" in out.system_prompt
        assert "CON-XYZ" in out.system_prompt
        assert out.system_prompt.index("SOUL-XYZ") < out.system_prompt.index("CON-XYZ")

    def test_missing_identity_files_produce_no_layer(self):
        out = serialize(_make_state(soul="", constitution="CON"))
        assert out.system_prompt.startswith("CON")

    def test_sections_joined_with_horizontal_rule(self):
        out = serialize(_make_state())
        assert "\n\n---\n\n" in out.system_prompt


# ── Layer 3: voice in system prompt ──────────────────────────────────

class TestVoiceInSystem:
    def test_voice_always_in_system_prompt(self):
        """Voice is always part of the system prompt regardless of
        conversation length."""
        out = serialize(_make_state(voice="VOICE_CORE"))
        assert "VOICE_CORE" in out.system_prompt

    def test_voice_before_dynamic_sections(self):
        """Voice appears in the stable prefix, before runtime state."""
        out = serialize(_make_state(voice="VOICE_CORE"))
        voice_pos = out.system_prompt.index("VOICE_CORE")
        state_pos = out.system_prompt.index("当前状态")
        assert voice_pos < state_pos

    def test_no_depth_injection_in_messages(self):
        """No [System Note] injected into messages regardless of
        conversation length — voice lives in the system prompt only."""
        turns = tuple(
            TrajectoryTurn(
                role="user" if i % 2 == 0 else "assistant",
                content=f"msg {i}"
            )
            for i in range(10)
        )
        out = serialize(_make_state(turns=turns, voice="VOICE_LONG"))
        for m in out.messages:
            assert "[System Note]" not in m["content"]
        assert "VOICE_LONG" in out.system_prompt

    def test_empty_voice_no_crash(self):
        # Voice empty must not break rendering. Without _PERSONA_ANCHOR,
        # the only assertion is "doesn't crash and produces a string".
        out = serialize(_make_state(voice=""))
        assert isinstance(out.system_prompt, str)


# ── Layer 6: runtime state ───────────────────────────────────────────

class TestRuntimeState:
    def test_time_anchor_shows_ymd_and_period(self):
        sv = _make_state(now=datetime(2026, 4, 18, 14, 30, tzinfo=_TAIPEI))
        out = serialize(sv)
        assert "2026年4月18日" in out.system_prompt
        assert "下午" in out.system_prompt
        assert "约14时" in out.system_prompt

    def test_weekday_is_taipei_local(self):
        sv = _make_state(now=datetime(2026, 4, 18, 14, 30, tzinfo=_TAIPEI))
        out = serialize(sv)
        assert "周六" in out.system_prompt

    def test_offline_gap_above_12h_renders_warning(self):
        sv = _make_state(offline_hours=14.5)
        out = serialize(sv)
        assert "距上次活跃已过" in out.system_prompt
        assert "14 小时" in out.system_prompt

    def test_offline_gap_below_12h_suppressed(self):
        """Gaps under 12 hours (normal idle) don't emit a warning."""
        sv = _make_state(offline_hours=6.7)
        out = serialize(sv)
        assert "距上次活跃已过" not in out.system_prompt

    def test_offline_gap_at_4h_suppressed(self):
        """Old threshold was 4h; now suppressed."""
        sv = _make_state(offline_hours=4.5)
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
        assert "我对用户的承诺" in out.system_prompt
        assert "已超时的承诺" not in out.system_prompt

    def test_overdue_promise_rendered(self):
        """Overdue promises go to a separate section."""
        com = CommitmentView(
            id="p2", description="查比赛", status="open",
            kind="promise", due_at="2026-04-19T01:00:00+00:00",
            is_overdue=True,
        )
        out = serialize(_make_state(commitments=(com,)))
        assert "已超时的承诺" in out.system_prompt
        assert "超时未完成：查比赛" in out.system_prompt
        assert "我对用户的承诺" not in out.system_prompt

    def test_overdue_and_active_promises_split(self):
        """Both overdue and active sections appear when both exist."""
        active = CommitmentView(
            id="p3", description="还没到期的事", status="open",
            kind="promise", due_at=None, is_overdue=False,
        )
        overdue = CommitmentView(
            id="p4", description="超时的事", status="open",
            kind="promise", due_at=None, is_overdue=True,
        )
        out = serialize(_make_state(commitments=(active, overdue)))
        assert "已超时的承诺" in out.system_prompt
        assert "我对用户的承诺" in out.system_prompt
        assert "超时的事" in out.system_prompt
        assert "还没到期的事" in out.system_prompt

    def test_serializer_does_not_filter_by_promise_status(self):
        """The builder is the checkpoint, not the serializer."""
        com = CommitmentView(
            id="p1", description="已完成的事", status="fulfilled",
            kind="promise", due_at=None,
        )
        out = serialize(_make_state(commitments=(com,)))
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
    def test_empty_trajectory_produces_no_messages(self):
        out = serialize(_make_state(turns=()))
        assert out.messages == []

    def test_trajectory_turns_preserved_verbatim(self):
        """Messages are a 1:1 mapping of trajectory turns, no injection."""
        turns = tuple(
            TrajectoryTurn(
                role="user" if i % 2 == 0 else "assistant",
                content=f"msg {i}"
            )
            for i in range(8)
        )
        out = serialize(_make_state(turns=turns))
        assert len(out.messages) == 8
        for i in range(8):
            assert out.messages[i]["content"] == f"msg {i}"

    def test_messages_preserve_turn_order(self):
        turns = tuple(
            TrajectoryTurn(role="user", content=f"m{i}") for i in range(4)
        )
        out = serialize(_make_state(turns=turns))
        assert [m["content"] for m in out.messages] == ["m0", "m1", "m2", "m3"]

    def test_voice_always_in_system_not_messages(self):
        """Even with many turns, voice stays in system prompt."""
        turns = tuple(
            TrajectoryTurn(role="user", content=f"t{i}") for i in range(10)
        )
        out = serialize(_make_state(turns=turns, voice="VOICE_TEST"))
        assert "VOICE_TEST" in out.system_prompt
        assert len(out.messages) == 10
        for m in out.messages:
            assert "VOICE_TEST" not in m["content"]


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
        assert _period_name(hour) == expected


# ── Pure-function verification ────────────────────────────────────────

class TestPurity:
    def test_no_network_no_file_io(self, monkeypatch):
        """Serializer performs no I/O on the hot path."""
        import builtins

        original_open = builtins.open

        def _fail_open(*args, **kwargs):
            raise AssertionError(f"serialize() performed file I/O: {args}")

        sv = _make_state()
        serialize(sv)
        try:
            monkeypatch.setattr(builtins, "open", _fail_open)
            serialize(sv)
        finally:
            monkeypatch.setattr(builtins, "open", original_open)


# ── Ambient awareness ────────────────────────────────────────────────

class TestAmbientAwareness:
    """Tests for the 环境感知 section (time context + ambient knowledge)."""

    def _make_time_context(self):
        from src.ambient.models import TimeContext
        return TimeContext(
            datetime_str="2026年4月22日 15:24",
            weekday="星期三",
            time_period="下午",
            lunar_date="三月初六",
            season="春季",
            upcoming_events=("距劳动节还有9天。",),
        )

    def _make_ambient_entry(
        self,
        key="weather:la",
        topic="洛杉矶天气",
        summary="晴 28°C",
        category="weather",
        confidence=1.0,
        fetched_at="2026-04-22T13:00:00Z",
        expires_at="2026-04-22T17:00:00Z",
        source="test",
    ):
        from src.ambient.models import AmbientEntry
        return AmbientEntry(
            key=key, category=category, topic=topic, data="{}",
            summary=summary, fetched_at=fetched_at,
            expires_at=expires_at, source=source,
            confidence=confidence,
        )

    def test_time_context_renders_awareness_section(self):
        sv = _make_state(now=datetime(2026, 4, 22, 15, 24, tzinfo=_TAIPEI))
        sv_with_tc = StateView(
            identity_docs=sv.identity_docs,
            attention_context=sv.attention_context,
            trajectory_window=sv.trajectory_window,
            memory_snippets=sv.memory_snippets,
            commitments_active=sv.commitments_active,
            time_context=self._make_time_context(),
        )
        out = serialize(sv_with_tc)
        assert "你的环境感知" in out.system_prompt
        assert "2026年4月22日" in out.system_prompt
        assert "星期三" in out.system_prompt
        assert "春季" in out.system_prompt

    def test_time_context_removes_legacy_time_line(self):
        sv = _make_state(now=datetime(2026, 4, 22, 15, 24, tzinfo=_TAIPEI))
        sv_with_tc = StateView(
            identity_docs=sv.identity_docs,
            attention_context=sv.attention_context,
            trajectory_window=sv.trajectory_window,
            memory_snippets=sv.memory_snippets,
            commitments_active=sv.commitments_active,
            time_context=self._make_time_context(),
        )
        out = serialize(sv_with_tc)
        assert "当前时间：" not in out.system_prompt
        assert "约15时，台北时间" not in out.system_prompt

    def test_no_time_context_keeps_legacy(self):
        sv = _make_state(now=datetime(2026, 4, 22, 15, 24, tzinfo=_TAIPEI))
        out = serialize(sv)
        assert "当前时间：" in out.system_prompt
        assert "你的环境感知" not in out.system_prompt

    def test_ambient_entries_rendered(self):
        tc = self._make_time_context()
        entries = (self._make_ambient_entry(),)
        sv = _make_state()
        sv_with = StateView(
            identity_docs=sv.identity_docs,
            attention_context=sv.attention_context,
            trajectory_window=sv.trajectory_window,
            memory_snippets=sv.memory_snippets,
            commitments_active=sv.commitments_active,
            time_context=tc,
            ambient_entries=entries,
        )
        out = serialize(sv_with)
        assert "你已知的信息" in out.system_prompt
        assert "洛杉矶天气" in out.system_prompt
        assert "晴 28°C" in out.system_prompt
        assert "(来源:test, 置信:1" in out.system_prompt

    def test_no_ambient_entries_shows_placeholder(self):
        tc = self._make_time_context()
        sv = _make_state()
        sv_with = StateView(
            identity_docs=sv.identity_docs,
            attention_context=sv.attention_context,
            trajectory_window=sv.trajectory_window,
            memory_snippets=sv.memory_snippets,
            commitments_active=sv.commitments_active,
            time_context=tc,
            ambient_entries=(),
        )
        out = serialize(sv_with)
        assert "暂无已缓存的环境知识" in out.system_prompt
        assert "你已知的信息" not in out.system_prompt

    def test_conflict_suppression_keeps_top_category_entry(self):
        now = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
        entries = (
            self._make_ambient_entry(
                key="a", category="snooker", topic="斯诺克A", summary="低置信",
                confidence=0.6, fetched_at="2026-05-04T09:00:00+00:00",
                expires_at="2026-05-04T15:00:00+00:00",
            ),
            self._make_ambient_entry(
                key="b", category="snooker", topic="斯诺克B", summary="中置信",
                confidence=0.8, fetched_at="2026-05-04T10:00:00+00:00",
                expires_at="2026-05-04T15:00:00+00:00",
            ),
            self._make_ambient_entry(
                key="c", category="snooker", topic="斯诺克C", summary="高置信",
                confidence=0.9, fetched_at="2026-05-04T08:00:00+00:00",
                expires_at="2026-05-04T15:00:00+00:00",
            ),
        )
        sv = _make_state(now=now)
        out = serialize(StateView(
            identity_docs=sv.identity_docs,
            attention_context=sv.attention_context,
            trajectory_window=sv.trajectory_window,
            memory_snippets=sv.memory_snippets,
            commitments_active=sv.commitments_active,
            time_context=self._make_time_context(),
            ambient_entries=entries,
        ))
        assert "斯诺克C" in out.system_prompt
        assert "斯诺克A" not in out.system_prompt
        assert "斯诺克B" not in out.system_prompt

    def test_low_confidence_ambient_entry_not_rendered(self):
        now = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
        entry = self._make_ambient_entry(
            confidence=0.65,
            fetched_at="2026-05-04T11:00:00+00:00",
            expires_at="2026-05-04T15:00:00+00:00",
        )
        sv = _make_state(now=now)
        out = serialize(StateView(
            identity_docs=sv.identity_docs,
            attention_context=sv.attention_context,
            trajectory_window=sv.trajectory_window,
            memory_snippets=sv.memory_snippets,
            commitments_active=sv.commitments_active,
            time_context=self._make_time_context(),
            ambient_entries=(entry,),
        ))
        assert "洛杉矶天气" not in out.system_prompt
        assert "暂无已缓存的环境知识" in out.system_prompt

    def test_ambient_render_includes_source_confidence_and_age(self):
        now = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
        entry = self._make_ambient_entry(
            source="research_writeback",
            confidence=0.8,
            fetched_at="2026-05-04T11:00:00+00:00",
            expires_at="2026-05-04T15:00:00+00:00",
        )
        sv = _make_state(now=now)
        out = serialize(StateView(
            identity_docs=sv.identity_docs,
            attention_context=sv.attention_context,
            trajectory_window=sv.trajectory_window,
            memory_snippets=sv.memory_snippets,
            commitments_active=sv.commitments_active,
            time_context=self._make_time_context(),
            ambient_entries=(entry,),
        ))
        assert "(来源:" in out.system_prompt
        assert "置信:" in out.system_prompt
        assert "前)" in out.system_prompt


# ── Prompt layering order ────────────────────────────────────────────

class TestLayeringOrder:
    """Verify the stable-first, dynamic-second ordering that enables
    prompt caching."""

    def test_soul_before_constitution(self):
        out = serialize(_make_state(soul="[SOUL]", constitution="[CON]"))
        assert out.system_prompt.index("[SOUL]") < out.system_prompt.index("[CON]")

    def test_voice_before_runtime_state(self):
        out = serialize(_make_state(voice="[VOICE]"))
        assert out.system_prompt.index("[VOICE]") < out.system_prompt.index("当前状态")

    def test_constitution_before_voice(self):
        out = serialize(_make_state(constitution="[CON]", voice="[VOICE]"))
        assert out.system_prompt.index("[CON]") < out.system_prompt.index("[VOICE]")
