"""StateSerializer — pure function turning StateView into prompt bytes.

Blueprint A prompt-caching overhaul. Layers the system prompt so stable
identity sections come first (cache-friendly), dynamic context follows.
Voice is always in the system prompt; depth injection into messages is
removed.

Render order (stable prefix → dynamic suffix):

    1. soul.md                (identity_docs.soul)          ← stable
    2. constitution.md        (identity_docs.constitution)  ← stable
    3. voice core             (identity_docs.voice)         ← stable
    ─── cache breakpoint ───
    4. ambient awareness      (time_context + ambient)      ← dynamic
    5. runtime state block    (attention + commitments)     ← dynamic
    6. memory snippets        (memory_snippets)             ← dynamic
    7. corrections            (corrections_text)            ← dynamic

No file reads, no ``datetime.now`` calls, no store lookups — whatever the
prompt needs is either already in the view or is a deterministic function
of its fields.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Final

from src.core.state_view import (
    CommitmentView,
    SerializedPrompt,
    SkillSummary,
    StateView,
    TrajectoryTurn,
)


# ── Constants ────────────────────────────────────────────────────────

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

_OFFLINE_GAP_THRESHOLD_HOURS: Final[float] = 12.0


# ── Public entry point ────────────────────────────────────────────────

def serialize(state: StateView) -> SerializedPrompt:
    """Turn a StateView into the concrete prompt LLMRouter will send.

    Pure function: no I/O, no clock reads, no global lookups. The output
    is fully determined by ``state``. This is what makes the parity
    smoke test meaningful — feed the same StateView, get the same bytes.

    System prompt is layered: stable identity sections first (soul,
    constitution, voice, persona anchor), then dynamic context (ambient,
    runtime state, memory, corrections). The stable prefix is identical
    across turns within a 5-minute window, enabling prompt caching.
    """
    parts: list[str] = []

    # ── Stable prefix (cache-friendly) ──

    # Layer 1: soul.md
    if state.identity_docs.soul:
        parts.append(state.identity_docs.soul)

    # Layer 2: constitution.md
    if state.identity_docs.constitution:
        parts.append(state.identity_docs.constitution)

    # Layer 3: voice core (5 rules + tool guidance)
    if state.identity_docs.voice:
        parts.append(state.identity_docs.voice)

    # ── Dynamic suffix ──

    # Layer 4: ambient awareness (time context + cached knowledge)
    if state.time_context is not None:
        parts.append(_render_ambient_awareness(state))

    # Layer 5: runtime state
    parts.append(_render_runtime_state(state))

    # Layer 6: memory snippets (opt-in — empty = no section emitted)
    memory_block = _render_memory_snippets(state)
    if memory_block:
        parts.append(memory_block)

    # Layer 7: corrections
    if state.corrections_text:
        parts.append(state.corrections_text)

    system_prompt = _SECTION_DIVIDER.join(parts)

    # Build messages list (no depth injection).
    messages = _build_messages(state)

    return SerializedPrompt(system_prompt=system_prompt, messages=messages)


# ── Layer renderers ───────────────────────────────────────────────────

def _render_runtime_state(state: StateView) -> str:
    """Produce the "## 当前状态" block.

    Offline-gap warning uses a 12-hour threshold to avoid cluttering
    every turn with stale-data warnings for normal idle periods.
    """
    att = state.attention_context
    lines: list[str] = []

    # 时间行：当 time_context 已填充时由环境感知段渲染，此处跳过
    if state.time_context is None:
        now: datetime = att.now
        weekday = _WEEKDAY_NAMES[now.weekday()]
        period = _period_name(now.hour)
        lines.append(
            f"当前时间：{now.year}年{now.month}月{now.day}日 {weekday} "
            f"{period}（约{now.hour}时）"
        )

    # Offline-gap warning: only for genuinely long absences.
    if att.offline_hours is not None and att.offline_hours > _OFFLINE_GAP_THRESHOLD_HOURS:
        lines.append(
            f"距上次活跃已过 {att.offline_hours:.0f} 小时。"
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

    # Available agents (Blueprint §9.3 — render before commitments).
    if state.agent_summary:
        lines.append("")
        lines.append(state.agent_summary)

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

    # Open promises
    promise_active: list[str] = []
    promise_overdue: list[str] = []
    for c in state.commitments_active:
        if c.kind != "promise":
            continue
        if c.is_overdue:
            promise_overdue.append(
                f"  - 超时未完成：{c.description}"
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
        lines.append("已超时的承诺：\n" + "\n".join(promise_overdue[:8]))
    if promise_active:
        lines.append("我对用户的承诺：\n" + "\n".join(promise_active[:5]))

    if state.focus_context:
        lines.append(state.focus_context)

    # Skill summary
    if state.skill_summary is not None:
        ss = state.skill_summary
        total = ss.stable_count + ss.testing_count + ss.draft_count
        if total > 0:
            skill_lines = []
            for name in ss.stable_names:
                skill_lines.append(f"  - [stable] {name}")
            for name in ss.testing_details:
                skill_lines.append(f"  - [testing] {name}")
            if ss.draft_count:
                skill_lines.append(f"  - draft: {ss.draft_count} 个")
            if ss.broken_count:
                skill_lines.append(f"  - broken: {ss.broken_count} 个（需修复）")
            lines.append("我的技能：\n" + "\n".join(skill_lines))

    return "## 当前状态\n\n" + "\n".join(lines)


def _render_ambient_awareness(state: StateView) -> str:
    """渲染"你的环境感知"段落：时间语境 + 已缓存的环境知识。"""
    lines: list[str] = []
    if state.time_context is not None:
        lines.append(state.time_context.to_prompt_text())
    ambient_entries = _filtered_ambient_entries(state)
    if ambient_entries:
        lines.append("")
        lines.append("### 你已知的信息")
        for e in ambient_entries:
            age = _ambient_age_label(e.fetched_at, state.attention_context.now)
            lines.append(
                f"- {e.topic}：{e.summary} "
                f"(来源:{e.source}, 置信:{e.confidence:g}, {age})"
            )
    elif state.time_context is not None:
        lines.append("")
        lines.append("（暂无已缓存的环境知识）")
    return "## 你的环境感知\n\n" + "\n".join(lines)


def _filtered_ambient_entries(state: StateView) -> tuple:
    now = state.attention_context.now
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)

    by_category = {}
    for entry in state.ambient_entries:
        if float(getattr(entry, "confidence", 0.0) or 0.0) < 0.7:
            continue
        expires_at = _parse_aware_datetime(getattr(entry, "expires_at", ""))
        if expires_at is None or expires_at <= now:
            continue
        category = getattr(entry, "category", "")
        current = by_category.get(category)
        if current is None or _ambient_rank(entry) > _ambient_rank(current):
            by_category[category] = entry
    return tuple(sorted(by_category.values(), key=_ambient_rank, reverse=True))


def _parse_aware_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _ambient_rank(entry) -> tuple[float, datetime]:
    fetched_at = _parse_aware_datetime(getattr(entry, "fetched_at", ""))
    if fetched_at is None:
        fetched_at = datetime.min.replace(tzinfo=timezone.utc)
    return (float(getattr(entry, "confidence", 0.0) or 0.0), fetched_at)


def _ambient_age_label(fetched_at: str, now: datetime) -> str:
    fetched = _parse_aware_datetime(fetched_at)
    if fetched is None:
        return "时间未知"
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    seconds = max((now.astimezone(timezone.utc) - fetched).total_seconds(), 0)
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}分钟前"
    return f"{int(minutes // 60)}小时前"


def _render_memory_snippets(state: StateView) -> str:
    """Render the optional retrieval layer. Empty → no section."""
    snippets = state.memory_snippets.snippets
    if not snippets:
        return ""
    body = "\n".join(f"- {s.content}" for s in snippets)
    note = (
        "以下是历史记忆线索，不是当前事实。"
        "赛事、新闻、天气、股价等时效信息必须以实时工具或搜索结果为准。"
    )
    body = note + "\n" + body
    return "## 记忆片段\n\n" + body


# ── Messages ─────────────────────────────────────────────────────────

def _build_messages(state: StateView) -> list[dict]:
    """Convert the trajectory window into the LLM-SDK message shape."""
    out: list[dict] = []
    for turn in state.trajectory_window.turns:
        out.append({"role": turn.role, "content": turn.content})
    return out


# ── Helpers ───────────────────────────────────────────────────────────

def _channel_description(channel: str, group_id: str | None) -> str:
    if channel == "qq_group":
        return f"QQ 群聊（群 {group_id})" if group_id else "QQ 群聊"
    return _CHANNEL_DESC.get(channel, channel)


def _period_name(hour: int) -> str:
    """Map hour-of-day to a Chinese period label.

    Boundaries copied verbatim from ``src.core.vitals.get_period_name``.
    We duplicate rather than import because the vitals helper has a
    default-arg branch that reads the wall clock — pulling it in
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
