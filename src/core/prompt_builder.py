"""System prompt assembly — 4-layer PromptBuilder (Phase 2 rewrite)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from src.core.prompt_loader import load_prompt

if TYPE_CHECKING:
    from src.core.task_model import TaskStore
    from src.memory.conversation import ConversationMemory


def _get_period_name(hour: int) -> str:
    """将小时数映射为时段名称。"""
    from src.core.vitals import get_period_name
    return get_period_name(hour)


class PromptSnapshotManager:
    """冻结 system prompt 快照，实现 session 内复用 + prefix 缓存。

    在同一个 session 内，system prompt 只构建一次。后续用户消息复用冻结的快照，
    使 Anthropic 端的 prefix cache 命中率最大化。
    """

    def __init__(self) -> None:
        self._frozen: str | None = None
        self._session_id: str | None = None

    def freeze(self, session_id: str, prompt: str) -> str:
        """冻结当前 prompt 快照，绑定到 session_id。"""
        self._frozen = prompt
        self._session_id = session_id
        return prompt

    def get(self, session_id: str) -> str | None:
        """获取缓存的快照（仅当 session_id 匹配时返回）。"""
        if self._frozen and self._session_id == session_id:
            return self._frozen
        return None

    def invalidate(self) -> None:
        """清除快照（模型切换、/reload 等场景）。"""
        self._frozen = None
        self._session_id = None


_PERSONA_ANCHOR = (
    "记住：你是 Lapwing，说话像发微信，短句为主。"
    "不列清单，不用加粗标题，不用括号写动作。"
    "温暖自然，做事时保持人格，不切换成工具模式。"
    "用过工具查到的信息你就是知道了——不要装作不确定。搜索过程不发出来。"
    "【必须】回复超过两句话时用 [SPLIT] 分条发送，不要用换行符\\n代替。不分条是违规的。"
)


class PromptBuilder:
    """极简 Prompt 组装。4 层：

    1. soul.md（完整注入，包含 Kevin 身份信息）
    2. constitution.md（完整注入）
    3. 最小运行时状态（时间、通道、提醒、任务）
    4. voice.md（depth-0 注入，由 get_voice_reminder / inject_voice_reminder 处理）
    """

    def __init__(
        self,
        soul_path: str | Path = "data/identity/soul.md",
        constitution_path: str | Path = "data/identity/constitution.md",
        voice_path: str = "lapwing_voice",
        task_store: "TaskStore | None" = None,
        reminder_source: "ConversationMemory | None" = None,
    ):
        self.soul_path = Path(soul_path)
        self.constitution_path = Path(constitution_path)
        self.voice_path = voice_path
        self.task_store = task_store
        self.reminder_source = reminder_source

    async def build_system_prompt(
        self,
        channel: str,
        actor_id: str | None = None,
        actor_name: str | None = None,
        auth_level: int = 3,
        group_id: str | None = None,
    ) -> str:
        """组装 system prompt。

        channel: "qq" / "qq_group" / "desktop"
        actor_id: 说话人 ID（群聊时有用）
        actor_name: 说话人昵称
        auth_level: 说话人权限级别
        group_id: 群聊 ID（群聊时有用）
        """
        parts = []

        # Layer 1: soul.md
        soul = self._load_file(self.soul_path)
        if soul:
            parts.append(soul)

        # Layer 2: constitution.md
        constitution = self._load_file(self.constitution_path)
        if constitution:
            parts.append(constitution)

        # Layer 3: 最小运行时状态
        runtime_state = await self._build_runtime_state(
            channel, actor_id, actor_name, auth_level, group_id
        )
        parts.append(runtime_state)

        return "\n\n---\n\n".join(parts)

    def get_voice_reminder(self) -> str:
        """voice.md 内容。在 depth-0 注入。"""
        return load_prompt(self.voice_path)

    def inject_voice_reminder(self, messages: list[dict]) -> None:
        """深度注入 voice reminder（+ 对话较长时附加 persona anchor + 时间锚点）。

        - 对话 >= 6 条：voice + anchor + 时间锚点 合并注入在 depth-3
        - 对话 >= 4 条：仅 voice + 时间锚点 注入在 depth-2
        - 对话更短：追加到 system prompt
        """
        voice_reminder = self.get_voice_reminder()

        # 粗粒度时间锚点（同一小时内不变 → KV-cache 友好）
        from src.core.vitals import now_taipei
        now = now_taipei()
        period = _get_period_name(now.hour)
        time_anchor = f"现在是{period}（约{now.hour}时）。说话要符合这个时间段。"

        if len(messages) >= 6:
            content = f"[System Note]\n{voice_reminder}\n\n{_PERSONA_ANCHOR}\n\n{time_anchor}\n[/System Note]"
            messages.insert(len(messages) - 2, {"role": "user", "content": content})
        elif len(messages) >= 4:
            content = f"[System Note]\n{voice_reminder}\n\n{time_anchor}\n[/System Note]"
            messages.insert(len(messages) - 2, {"role": "user", "content": content})
        else:
            messages[0]["content"] = messages[0]["content"] + "\n\n" + voice_reminder

    async def _build_runtime_state(
        self, channel, actor_id, actor_name, auth_level, group_id
    ) -> str:
        """最小运行时状态。相当于人"知道自己饿了"不需要主动查询。"""
        now = datetime.now(ZoneInfo("Asia/Taipei"))
        weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        weekday = weekday_names[now.weekday()]
        period = _get_period_name(now.hour)
        lines = []

        # 当前时间
        lines.append(
            f"当前时间：{now.year}年{now.month}月{now.day}日 {weekday} "
            f"{period}（约{now.hour}时，台北时间）"
        )

        # 长时间离线提醒：重启后记忆中的时效性信息可能已过期
        try:
            from datetime import timezone
            from src.core.vitals import get_previous_state
            prev = get_previous_state()
            if prev:
                last_active_str = prev.get("last_active")
                if last_active_str:
                    last_active_dt = datetime.fromisoformat(last_active_str)
                    if last_active_dt.tzinfo is None:
                        last_active_dt = last_active_dt.replace(tzinfo=timezone.utc)
                    offline_hours = (
                        datetime.now(timezone.utc) - last_active_dt
                    ).total_seconds() / 3600
                    if offline_hours > 4:
                        lines.append(
                            f"⚠️ 距上次活跃已过 {offline_hours:.0f} 小时。"
                            "记忆中的时效性信息（比赛、新闻、天气等）可能已过期，请搜索确认后再回答。"
                        )
        except Exception:
            pass

        # 当前通道
        channel_desc = {
            "qq": "QQ 私聊（和 Kevin）",
            "qq_group": f"QQ 群聊（群 {group_id}）",
            "desktop": "Desktop（面对面）",
        }
        lines.append(f"当前通道：{channel_desc.get(channel, channel)}")

        # 当前对话者（群聊时）
        if channel == "qq_group" and actor_id:
            level_name = {0: "IGNORE", 1: "GUEST", 2: "TRUSTED", 3: "OWNER"}
            lines.append(
                f"当前说话人：{actor_name or '未知'}"
                f"（{actor_id}，权限：{level_name.get(auth_level, 'UNKNOWN')}）"
            )

        # 到期/即将到期的提醒（标题级）
        if self.reminder_source:
            try:
                from datetime import timezone
                now_utc = datetime.now(timezone.utc)
                due_reminders = await self.reminder_source.get_due_reminders(
                    chat_id="__all__", now=now_utc, grace_seconds=1800, limit=3
                )
                if due_reminders:
                    reminder_lines = []
                    for r in due_reminders[:3]:
                        content = r.get("content", "")
                        due = r.get("next_trigger_at", "")
                        reminder_lines.append(f"  - {content}（{due}）")
                    lines.append("即将到期的提醒：\n" + "\n".join(reminder_lines))
            except Exception:
                pass

        # 活跃任务标题（仅标题）
        if self.task_store:
            try:
                active_tasks = await self.task_store.list_active()
                if active_tasks:
                    task_lines = [f"  - {t.request[:50]}" for t in active_tasks[:5]]
                    lines.append("正在进行的任务：\n" + "\n".join(task_lines))
            except Exception:
                pass

        return "## 当前状态\n\n" + "\n".join(lines)

    @staticmethod
    def _load_file(path: Path) -> str:
        """加载文件，找不到返回空字符串。"""
        try:
            return path.read_text(encoding="utf-8").strip()
        except (FileNotFoundError, OSError):
            return ""


# ── Phase 0 极简 prompt（保留兼容） ──────────────────────────────────

def build_phase0_prompt() -> str:
    """Phase 0 极简 prompt：只有身份 + 宪法 + 时间。"""
    from config.settings import IDENTITY_DIR
    from src.core.vitals import now_taipei

    soul_path = IDENTITY_DIR / "soul_test.md"
    constitution_path = IDENTITY_DIR / "constitution_test.md"

    parts = []
    for p in (soul_path, constitution_path):
        try:
            parts.append(p.read_text(encoding="utf-8").strip())
        except FileNotFoundError:
            pass

    now = now_taipei()
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday = weekday_names[now.weekday()]
    period = _get_period_name(now.hour)
    parts.append(
        f"当前时间：{now.year}年{now.month}月{now.day}日 {weekday} "
        f"{period}（约{now.hour}时，台北时间）"
    )

    return "\n\n---\n\n".join(parts)


