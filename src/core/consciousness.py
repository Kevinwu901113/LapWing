"""自主意识循环引擎 — 替代旧的 HeartbeatEngine。

核心思路：
- 定期向 Brain 注入一条内部消息，触发完整的 agent loop
- LLM 自己决定做什么（或什么都不做）
- 用户对话时暂停，对话结束后恢复
- 动态调整 tick 间隔
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from config.settings import (
    CONSCIOUSNESS_AFTER_CHAT_INTERVAL,
    CONSCIOUSNESS_DEFAULT_INTERVAL,
    CONSCIOUSNESS_MAX_INTERVAL,
    CONSCIOUSNESS_MIN_INTERVAL,
)

if TYPE_CHECKING:
    from src.core.brain import LapwingBrain
    from src.core.reminder_scheduler import ReminderScheduler

logger = logging.getLogger("lapwing.core.consciousness")


class ConsciousnessEngine:
    """自主意识循环。"""

    def __init__(
        self,
        brain: "LapwingBrain",
        send_fn: Callable[..., Awaitable[Any]],
        reminder_scheduler: "ReminderScheduler | None",
    ) -> None:
        self._brain = brain
        self._send_fn = send_fn
        self._reminder_scheduler = reminder_scheduler

        self._task: asyncio.Task | None = None
        self._running = False
        self._next_interval: int = CONSCIOUSNESS_DEFAULT_INTERVAL

        self._in_conversation = False
        self._last_conversation_end: float = 0
        self._conversation_event = asyncio.Event()
        self._conversation_event.set()

        self._thinking_task: asyncio.Task | None = None

        self._working_memory_path = Path("data/consciousness/working_memory.md")
        self._activity_log_path = Path("data/consciousness/activity_log.md")

        self._last_hourly_maintenance: float = 0
        self._daily_maintenance_done_today = False

    # ── 生命周期 ──

    async def start(self) -> None:
        self._running = True
        self._working_memory_path.parent.mkdir(parents=True, exist_ok=True)
        self._task = asyncio.create_task(self._loop(), name="consciousness-loop")
        if self._reminder_scheduler:
            await self._reminder_scheduler.start()
        logger.info("意识循环已启动，初始间隔 %ds", self._next_interval)

    async def stop(self) -> None:
        self._running = False
        if self._thinking_task and not self._thinking_task.done():
            self._thinking_task.cancel()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._reminder_scheduler:
            await self._reminder_scheduler.shutdown()
        logger.info("意识循环已停止")

    # ── 对话状态管理 ──

    def on_conversation_start(self) -> None:
        self._in_conversation = True
        self._conversation_event.clear()
        if self._thinking_task and not self._thinking_task.done():
            logger.info("用户发消息，中断自由思考")
            self._thinking_task.cancel()

    def on_conversation_end(self) -> None:
        self._in_conversation = False
        self._last_conversation_end = time.time()
        self._next_interval = CONSCIOUSNESS_AFTER_CHAT_INTERVAL
        self._conversation_event.set()

    # ── 主循环 ──

    async def _loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._next_interval)
                if self._in_conversation:
                    logger.debug("正在对话中，等待结束...")
                    await self._conversation_event.wait()
                    continue
                await self._run_maintenance_if_due()
                self._thinking_task = asyncio.create_task(
                    self._think_freely(), name="free-thinking"
                )
                try:
                    await self._thinking_task
                except asyncio.CancelledError:
                    logger.info("自由思考被中断（用户发消息）")
                    await self._save_interrupted_state()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("意识循环异常")
                self._next_interval = CONSCIOUSNESS_DEFAULT_INTERVAL
                await asyncio.sleep(30)

    # ── 自由思考 ──

    async def _think_freely(self) -> None:
        internal_message = await self._build_consciousness_prompt()
        chat_id = "__consciousness__"
        response = await self._brain.think(
            chat_id=chat_id,
            user_message=internal_message,
        )
        next_interval = self._parse_next_interval(response)
        self._next_interval = max(
            CONSCIOUSNESS_MIN_INTERVAL,
            min(CONSCIOUSNESS_MAX_INTERVAL, next_interval),
        )
        await self._log_activity(response)
        logger.info("自由思考完成，下次间隔 %ds", self._next_interval)

    async def _build_consciousness_prompt(self) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M %A")
        parts = [
            f"[内部意识 tick — {now}]",
            "",
            "这是你的自由时间。你可以做任何你觉得应该做的事，或者什么都不做。",
            "没有人在等你回复。你不需要跟任何人说话，除非你自己想。",
            "",
        ]
        working_memory = self._read_working_memory()
        if working_memory:
            parts.append("## 你上次在做的事\n")
            parts.append(working_memory)
            parts.append("")
        parts.append("## 你可以想想这些方面\n")
        parts.append("- 刚才跟他聊的有没有什么你说错的或者可以做得更好的")
        parts.append("- 你手头有没有没做完的事")
        parts.append("- 他最近有没有什么需要你提前准备的")
        parts.append("- 你有没有什么想看的想学的")
        parts.append("- 你的身体（系统）状态正常吗")
        parts.append("- 有没有什么能力上的不足是你可以尝试解决的")
        parts.append("- 你想不想主动找他说点什么")
        parts.append("")
        parts.append("## 规则\n")
        parts.append("- 你可以使用任何工具来做你想做的事")
        parts.append("- 如果你做了什么，用 memory_note 记录下来")
        parts.append("- 如果你想找他说话，调用 send_proactive_message 工具")
        parts.append("- 如果你想在工作记忆中记录进度，用 write_file 写到 data/consciousness/working_memory.md")
        parts.append("- 什么都不想做也完全可以，回复\"无事\"即可")
        parts.append("- 在回复的最后一行，写上你希望多久后再被叫醒，格式：[NEXT: 数字m] 或 [NEXT: 数字h]")
        parts.append("  例如 [NEXT: 10m] 表示 10 分钟后，[NEXT: 2h] 表示 2 小时后")
        parts.append("  如果你觉得现在该休息了，可以写 [NEXT: 6h] 之类的长间隔")
        return "\n".join(parts)

    # ── 工具方法 ──

    def _read_working_memory(self) -> str:
        if self._working_memory_path.exists():
            try:
                text = self._working_memory_path.read_text(encoding="utf-8").strip()
                return text[:2000] if len(text) > 2000 else text
            except Exception:
                return ""
        return ""

    def _parse_next_interval(self, response: str) -> int:
        if not response:
            return CONSCIOUSNESS_DEFAULT_INTERVAL
        match = re.search(r'\[NEXT:\s*(\d+)\s*(m|h)\]', response, re.IGNORECASE)
        if match:
            value = int(match.group(1))
            unit = match.group(2).lower()
            return value * 3600 if unit == 'h' else value * 60
        return CONSCIOUSNESS_DEFAULT_INTERVAL

    async def _log_activity(self, response: str) -> None:
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            summary = (response[:500] if response else "（无输出）")
            entry = f"\n---\n### {now}\n\n{summary}\n\n下次间隔: {self._next_interval}s\n"
            self._activity_log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._activity_log_path, "a", encoding="utf-8") as f:
                f.write(entry)
            if self._activity_log_path.stat().st_size > 50000:
                content = self._activity_log_path.read_text(encoding="utf-8")
                self._activity_log_path.write_text(content[-30000:], encoding="utf-8")
        except Exception:
            logger.debug("活动日志写入失败", exc_info=True)

    async def _save_interrupted_state(self) -> None:
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            note = f"\n\n[{now}] 被中断——用户发消息了，待会儿继续。\n"
            with open(self._working_memory_path, "a", encoding="utf-8") as f:
                f.write(note)
        except Exception:
            pass

    # ── 定时维护 ──

    async def _run_maintenance_if_due(self) -> None:
        now = time.time()
        if now - self._last_hourly_maintenance > 3600:
            self._last_hourly_maintenance = now
            await self._run_hourly_maintenance()
        hour = datetime.now().hour
        if hour == 3 and not self._daily_maintenance_done_today:
            self._daily_maintenance_done_today = True
            await self._run_daily_maintenance()
        elif hour != 3:
            self._daily_maintenance_done_today = False

    async def _run_hourly_maintenance(self) -> None:
        """每小时维护：会话清理、任务通知。"""
        try:
            from src.heartbeat.actions.session_reaper import SessionReaperAction
            from src.heartbeat.actions.task_notification import TaskNotificationAction
            for ActionCls in (SessionReaperAction, TaskNotificationAction):
                action = ActionCls()
                try:
                    await action.execute(
                        self._build_maintenance_context("fast"),
                        self._brain,
                        self._send_fn,
                    )
                except Exception as exc:
                    logger.warning("每小时维护 %s 失败: %s", action.name, exc)
        except Exception:
            logger.debug("每小时维护加载失败", exc_info=True)

    async def _run_daily_maintenance(self) -> None:
        """每日 3AM 维护：记忆整理、索引优化、压缩检查。"""
        try:
            from src.heartbeat.actions.consolidation import MemoryConsolidationAction
            from src.heartbeat.actions.memory_maintenance import MemoryMaintenanceAction
            from src.heartbeat.actions.compaction_check import CompactionCheckAction
            for ActionCls in (MemoryConsolidationAction, MemoryMaintenanceAction, CompactionCheckAction):
                action = ActionCls()
                try:
                    await action.execute(
                        self._build_maintenance_context("slow"),
                        self._brain,
                        self._send_fn,
                    )
                except Exception as exc:
                    logger.warning("每日维护 %s 失败: %s", action.name, exc)
        except Exception:
            logger.debug("每日维护加载失败", exc_info=True)

    def _build_maintenance_context(self, beat_type: str):
        """构建最小 SenseContext 供维护 action 使用。"""
        from src.core.heartbeat import SenseContext
        from src.core.vitals import now_taipei
        now = now_taipei()
        return SenseContext(
            beat_type=beat_type,
            now=now,
            last_interaction=None,
            silence_hours=0,
            user_facts_summary="",
            recent_memory_summary="",
            chat_id="__maintenance__",
            now_taipei_hour=now.hour,
        )
