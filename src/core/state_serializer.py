"""StateSerializer — pure function turning StateView into prompt bytes.

Blueprint v2.0 Step 3 §2. Replaces the procedural assembly that lived in
``PromptBuilder.build_system_prompt`` + ``inject_voice_reminder``. Every
input comes from ``StateView``; every output lands in ``SerializedPrompt``.
No file reads, no ``datetime.now`` calls, no store lookups — whatever the
prompt needs is either already in the view or is a deterministic function
of its fields.

Render order mirrors the pre-Step-3 layout so the parity smoke test can
compare outputs against the ``recast_v2_step2_complete`` tag semantics:

    1. soul.md                (identity_docs.soul)
    2. constitution.md        (identity_docs.constitution)
    3. runtime state block    (attention_context + commitments_active)
    4. memory snippets        (memory_snippets) — new explicit layer
    5. voice reminder         (identity_docs.voice) — depth-injected

The runtime-state block reproduces PromptBuilder's ordering:
    - current time anchor
    - offline-gap warning (if any)
    - channel tag + speaker info
    - due reminders (commitments_active, kind=reminder)
    - active tasks   (commitments_active, kind=task)
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from src.core.state_view import (
    CommitmentView,
    SerializedPrompt,
    SkillSummary,
    StateView,
    TrajectoryTurn,
)


# ── Constants mirrored from PromptBuilder ─────────────────────────────

_PERSONA_ANCHOR: Final[str] = (
    "记住：你是 Lapwing，说话像发微信，短句为主。"
    "不列清单，不用加粗标题，不用括号写动作。"
    "温暖自然，做事时保持人格，不切换成工具模式。"
    "用过工具查到的信息你就是知道了——不要装作不确定。搜索过程不发出来。"
    "【必须】回复超过两句话时用 [SPLIT] 分条发送，不要用换行符\\n代替。不分条是违规的。"
)

_WEEKDAY_NAMES: Final[tuple[str, ...]] = (
    "周一", "周二", "周三", "周四", "周五", "周六", "周日",
)

_CHANNEL_DESC: Final[dict[str, str]] = {
    "qq": "QQ 私聊（和 Kevin）",
    "qq_group": "QQ 群聊",  # group_id appended dynamically
    "desktop": "Desktop（面对面）",
}

_AUTH_NAMES: Final[dict[int, str]] = {
    0: "IGNORE", 1: "GUEST", 2: "TRUSTED", 3: "OWNER",
}

_SECTION_DIVIDER: Final[str] = "\n\n---\n\n"


# ── Public entry point ────────────────────────────────────────────────

def serialize(state: StateView) -> SerializedPrompt:
    """Turn a StateView into the concrete prompt LLMRouter will send.

    Pure function: no I/O, no clock reads, no global lookups. The output
    is fully determined by ``state``. This is what makes the parity
    smoke test meaningful — feed the same StateView, get the same bytes.
    """
    parts: list[str] = []

    # Layer 1: soul.md
    if state.identity_docs.soul:
        parts.append(state.identity_docs.soul)

    # Layer 2: constitution.md
    if state.identity_docs.constitution:
        parts.append(state.identity_docs.constitution)

    # Layer 3: runtime state
    parts.append(_render_runtime_state(state))

    # Layer 4: memory snippets (new explicit layer, opt-in — empty =
    # no section emitted, preserving pre-Step-3 prompts that didn't
    # surface retrieval hits)
    memory_block = _render_memory_snippets(state)
    if memory_block:
        parts.append(memory_block)

    system_prompt = _SECTION_DIVIDER.join(parts)

    # Build messages list + apply voice-reminder depth injection.
    messages = _build_messages(state)
    system_prompt, messages = _inject_voice(
        system_prompt, messages, state
    )

    return SerializedPrompt(system_prompt=system_prompt, messages=messages)


# ── Layer renderers ───────────────────────────────────────────────────

def _render_runtime_state(state: StateView) -> str:
    """Produce the "## 当前状态" block.

    Mirrors PromptBuilder._build_runtime_state line-by-line so the
    parity smoke test can compare against the Step-2 tag semantics.
    """
    att = state.attention_context
    lines: list[str] = []

    # Current time
    now: datetime = att.now
    weekday = _WEEKDAY_NAMES[now.weekday()]
    period = _period_name(now.hour)
    lines.append(
        f"当前时间：{now.year}年{now.month}月{now.day}日 {weekday} "
        f"{period}（约{now.hour}时，台北时间）"
    )

    # Offline-gap warning: only render when the builder flagged a gap.
    # Pre-Step-3 code wrote "距上次活跃已过 {h:.0f} 小时" — kept verbatim
    # so the model keeps recognising the phrase.
    if att.offline_hours is not None and att.offline_hours > 4:
        lines.append(
            f"⚠️ 距上次活跃已过 {att.offline_hours:.0f} 小时。"
            "记忆中的时效性信息（比赛、新闻、天气等）可能已过期，请搜索确认后再回答。"
        )

    # Channel tag
    channel_desc = _channel_description(att.channel, att.group_id)
    lines.append(f"当前通道：{channel_desc}")

    # Group speaker block — only populated for qq_group
    if att.channel == "qq_group" and att.actor_id:
        level_name = _AUTH_NAMES.get(att.auth_level, "UNKNOWN")
        lines.append(
            f"当前说话人：{att.actor_name or '未知'}"
            f"（{att.actor_id}，权限：{level_name}）"
        )

    # Due reminders (commitments with kind=reminder)
    reminder_lines = [
        f"  - {c.description}（{c.due_at or ''}）"
        for c in state.commitments_active[:8]
        if c.kind == "reminder"
    ][:3]
    if reminder_lines:
        lines.append("即将到期的提醒：\n" + "\n".join(reminder_lines))

    # Active tasks (commitments with kind=task)
    task_lines = [
        f"  - {c.description[:50]}"
        for c in state.commitments_active
        if c.kind == "task"
    ][:5]
    if task_lines:
        lines.append("正在进行的任务：\n" + "\n".join(task_lines))

    # Open promises (commitments with kind=promise) — new layer from
    # Step 3; previous PromptBuilder had no promise surface. Every
    # commitment in ``state.commitments_active`` has already been
    # filtered to "live" by the builder (CommitmentStore.list_open
    # returns pending + in_progress rows only) — the serializer does
    # not re-filter on status here.
    #
    # Step 5: 拆 overdue 与 active 两段，overdue 段加显著前缀
    # ⚠️ 让模型必然看到——它要决定重试 / 告知用户 / abandon。
    promise_active: list[str] = []
    promise_overdue: list[str] = []
    for c in state.commitments_active:
        if c.kind != "promise":
            continue
        if c.is_overdue:
            promise_overdue.append(
                f"  - ⚠️ 超时未完成：{c.description}"
                + (f"（截止 {c.due_at}）" if c.due_at else "")
                + f"  [id={c.id[:8]}]"
            )
        else:
            promise_active.append(
                f"  - {c.description}"
                + (f"（截止 {c.due_at}）" if c.due_at else "")
                + f"  [id={c.id[:8]}]"
            )

    if promise_overdue:
        # overdue 单独一段，标题强调
        lines.append("⚠️ 已超时的承诺（必须处理：重试 / 告诉用户 / abandon）：\n" + "\n".join(promise_overdue[:8]))
    if promise_active:
        lines.append("我对用户的承诺：\n" + "\n".join(promise_active[:5]))

    # Skill summary
    if state.skill_summary is not None:
        ss = state.skill_summary
        total = ss.stable_count + ss.testing_count + ss.draft_count + ss.broken_count
        if total > 0:
            skill_lines = [f"stable: {ss.stable_count} 个"]
            if ss.stable_names:
                skill_lines[0] += f"（{'、'.join(ss.stable_names)}）"
            if ss.testing_count:
                detail = f"（{'、'.join(ss.testing_details)}）" if ss.testing_details else ""
                skill_lines.append(f"testing: {ss.testing_count} 个{detail}")
            if ss.draft_count:
                skill_lines.append(f"draft: {ss.draft_count} 个")
            if ss.broken_count:
                skill_lines.append(f"broken: {ss.broken_count} 个")
            lines.append("我的技能：\n" + "\n".join(f"  - {l}" for l in skill_lines))

    return "## 当前状态\n\n" + "\n".join(lines)


def _render_memory_snippets(state: StateView) -> str:
    """Render the optional retrieval layer. Empty → no section."""
    snippets = state.memory_snippets.snippets
    if not snippets:
        return ""
    body = "\n".join(f"- {s.content}" for s in snippets)
    return "## 记忆片段\n\n" + body


# ── Messages + voice injection ────────────────────────────────────────

def _build_messages(state: StateView) -> list[dict]:
    """Convert the trajectory window into the LLM-SDK message shape."""
    out: list[dict] = []
    for turn in state.trajectory_window.turns:
        out.append({"role": turn.role, "content": turn.content})
    return out


def _inject_voice(
    system_prompt: str, messages: list[dict], state: StateView
) -> tuple[str, list[dict]]:
    """Place the voice reminder using the same depth rules as pre-Step-3.

    The classic ``inject_voice_reminder`` counted ``[system, *recent]``
    (the full messages array including the system row). Here we still
    reason in terms of that total, so ``effective_count = len(messages)
    + 1``. Rules preserved verbatim:

    - ≥ 5 recent turns (total ≥ 6): voice + persona anchor + time
      anchor, inserted two from the end.
    - ≥ 3 recent turns (total ≥ 4): voice + time anchor, inserted two
      from the end.
    - shorter conversations: voice appended to the system prompt.

    Output includes the final system_prompt (possibly with voice
    appended) and a fresh messages list with the depth-inserted note if
    applicable. The original system message is not yet in ``messages``
    — brain will prepend ``{"role":"system","content":system_prompt}``
    after calling the serializer.
    """
    voice = state.identity_docs.voice
    if not voice:
        return system_prompt, messages

    now = state.attention_context.now
    period = _period_name(now.hour)
    time_anchor = f"现在是{period}（约{now.hour}时）。说话要符合这个时间段。"

    # Effective total matches legacy count: [system] + recent_messages.
    total = len(messages) + 1

    # Insert position: the legacy helper operated on
    # [system, *recent] and used ``len(messages) - 2`` so the note
    # landed between the second-to-last and last original messages.
    # Converting to the recent-only view brain hands us, that becomes
    # ``len(messages) - 2`` still (because we lost one "system" slot on
    # both sides of the arithmetic). Keep the two-from-end behaviour
    # so two real turns follow the note, matching pre-Step-3 output.
    insert_at = max(0, len(messages) - 2)

    if total >= 6:
        note = (
            f"[System Note]\n{voice}\n\n{_PERSONA_ANCHOR}\n\n{time_anchor}\n"
            "[/System Note]"
        )
        new_messages = list(messages)
        new_messages.insert(insert_at, {"role": "user", "content": note})
        return system_prompt, new_messages

    if total >= 4:
        note = f"[System Note]\n{voice}\n\n{time_anchor}\n[/System Note]"
        new_messages = list(messages)
        new_messages.insert(insert_at, {"role": "user", "content": note})
        return system_prompt, new_messages

    # Very short convo: fold voice into system prompt tail.
    return system_prompt + "\n\n" + voice, messages


# ── Helpers ───────────────────────────────────────────────────────────

def _channel_description(channel: str, group_id: str | None) -> str:
    if channel == "qq_group":
        return f"QQ 群聊（群 {group_id})" if group_id else "QQ 群聊"
    return _CHANNEL_DESC.get(channel, channel)


def _period_name(hour: int) -> str:
    """Map hour-of-day to a Chinese period label.

    Boundaries copied verbatim from ``src.core.vitals.get_period_name``.
    We duplicate rather than import because the vitals helper has a
    default-arg branch that reads ``now_taipei()`` — pulling it in
    would give the serializer a wall-clock dependency and break the
    pure-function invariant. If vitals' boundaries ever shift, adjust
    here too.
    """
    if 0 <= hour < 5:
        return "深夜"
    if 5 <= hour < 8:
        return "早上"
    if 8 <= hour < 11:
        return "上午"
    if 11 <= hour < 13:
        return "中午"
    if 13 <= hour < 17:
        return "下午"
    if 17 <= hour < 19:
        return "傍晚"
    if 19 <= hour < 23:
        return "晚上"
    return "深夜"
