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
from src.logging.event_logger import events as _events

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
        incident_manager: Any | None = None,
    ) -> None:
        self._brain = brain
        self._send_fn = send_fn
        self._reminder_scheduler = reminder_scheduler
        self._incident_manager = incident_manager

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
        self._task_resumption_action: Any | None = None

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
                await self._run_task_resumption()
                self._thinking_task = asyncio.create_task(
                    self._think_freely(), name="free-thinking"
                )
                try:
                    await self._thinking_task
                except asyncio.CancelledError:
                    logger.info("自由思考被中断（用户发消息）")
                    _events.log("consciousness", "interrupted", reason="用户发消息")
                    await self._save_interrupted_state()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("意识循环异常")
                self._next_interval = CONSCIOUSNESS_DEFAULT_INTERVAL
                await asyncio.sleep(30)

    # ── 自由思考 ──

    _NEXT_PATTERN = re.compile(r"\[T?NEXT:\s*(\d+)\s*(s|m|h|min)\]", re.IGNORECASE)

    def _parse_and_strip_next(self, text: str) -> tuple[str, float | None]:
        """从 LLM 响应中提取 [NEXT: Xm] 指令，返回 (剩余文本, 秒数或None)。"""
        match = self._NEXT_PATTERN.search(text)
        interval = None
        if match:
            value = int(match.group(1))
            unit = match.group(2).lower()
            multiplier = {"s": 1, "m": 60, "min": 60, "h": 3600}
            interval = value * multiplier.get(unit, 60)
            text = self._NEXT_PATTERN.sub("", text).strip()
        return text, interval

    async def _think_freely(self) -> None:
        internal_message = await self._build_consciousness_prompt()
        chat_id = "__consciousness__"
        try:
            response = await self._brain.think(
                chat_id=chat_id,
                user_message=internal_message,
            )
        except Exception as exc:
            # LLM 调用失败（529 过载等），退避后重试
            backoff = min(self._next_interval * 2, CONSCIOUSNESS_MAX_INTERVAL)
            self._next_interval = max(CONSCIOUSNESS_MIN_INTERVAL, backoff)
            logger.warning("意识循环 LLM 调用失败，退避 %ds: %s", self._next_interval, exc)
            _events.log("consciousness", "tick_failed",
                error=str(exc)[:200],
                next_interval=self._next_interval,
            )
            return

        clean_text, next_interval = self._parse_and_strip_next(response or "")
        if next_interval is not None:
            self._next_interval = max(
                CONSCIOUSNESS_MIN_INTERVAL,
                min(CONSCIOUSNESS_MAX_INTERVAL, int(next_interval)),
            )
        else:
            # 根据用户沉默时长动态调整默认间隔
            self._next_interval = self._silence_based_interval()
        await self._log_activity(clean_text)
        # 后处理：resolved incident → experience skill + 清理 linked rule
        await self._process_resolved_incidents()
        _events.log("consciousness", "tick_complete",
            decision=clean_text[:300] if clean_text else "无输出",
            next_interval=self._next_interval,
        )
        logger.info("自由思考完成，下次间隔 %ds", self._next_interval)

    async def _build_consciousness_prompt(self) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M %A")
        parts = [
            f"[内部意识 tick — {now}]",
            "",
            "这是你的自由时间。你可以做任何你觉得应该做的事，或者什么都不做。",
            "没有人在等你回复。你不需要跟任何人说话，除非你自己想。",
            "",
            "【重要】这不是用户对话。没有人刚才跟你说了什么话。",
            "不要说「你能再说一次吗」「抱歉走神了」「你好」之类的话——没有人在跟你说话。",
            "如果没有需要做的事，回复\"无事\"即可。",
            "",
        ]
        working_memory = self._read_working_memory()
        if working_memory:
            parts.append("## 你上次在做的事\n")
            parts.append(working_memory)
            parts.append("")
        # Incident 摘要注入
        if self._incident_manager is not None:
            incident_summary = self._incident_manager.format_for_consciousness(limit=5)
            if incident_summary:
                parts.append("## 待解决的问题\n")
                parts.append(incident_summary)
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

    async def _process_resolved_incidents(self) -> None:
        """后处理：将新 resolved 的 incident 转化为 experience skill，清理关联规则。"""
        if self._incident_manager is None:
            return
        import json
        incidents_dir = Path("data/memory/incidents")
        if not incidents_dir.exists():
            return

        esm = getattr(self._brain, "experience_skill_manager", None)
        tactical_rules = getattr(self._brain, "tactical_rules", None)

        for f in incidents_dir.glob("INC-*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("status") != "resolved":
                    continue
                if data.get("_skill_generated"):
                    continue

                # 生成 experience skill
                if esm is not None:
                    try:
                        await esm.create_from_incident(data)
                    except Exception:
                        logger.debug("从 incident 生成 skill 失败", exc_info=True)

                # 清理关联的 tactical rule
                if tactical_rules is not None and data.get("linked_rule"):
                    try:
                        await tactical_rules.remove_rule(data["linked_rule"])
                    except Exception:
                        logger.debug("清理关联规则失败", exc_info=True)

                # 标记已处理
                data["_skill_generated"] = True
                f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                continue

    # ── 工具方法 ──

    def _read_working_memory(self) -> str:
        if self._working_memory_path.exists():
            try:
                text = self._working_memory_path.read_text(encoding="utf-8").strip()
                return text[:2000] if len(text) > 2000 else text
            except Exception:
                return ""
        return ""

    def _silence_based_interval(self) -> int:
        """根据用户沉默时长动态调整默认间隔，避免空转浪费。"""
        if self._last_conversation_end <= 0:
            return CONSCIOUSNESS_DEFAULT_INTERVAL
        silence_seconds = time.time() - self._last_conversation_end
        if silence_seconds > 7200:      # 2h+ → 1 小时
            return 3600
        elif silence_seconds > 1800:    # 30min+ → 30 分钟
            return 1800
        else:
            return CONSCIOUSNESS_DEFAULT_INTERVAL

    # _parse_next_interval 已被 _parse_and_strip_next 替代

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

    async def _run_task_resumption(self) -> None:
        """每次 tick 检查是否有未完成任务需要恢复。"""
        try:
            from config.settings import TASK_RESUMPTION_ENABLED
            if not TASK_RESUMPTION_ENABLED:
                return
            if self._task_resumption_action is None:
                from src.heartbeat.actions.task_resumption import TaskResumptionAction
                self._task_resumption_action = TaskResumptionAction()
            ctx = self._build_maintenance_context("minute")
            await self._task_resumption_action.execute(ctx, self._brain, self._send_fn)
        except Exception as exc:
            logger.debug("任务恢复检查失败: %s", exc)

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
        """每小时维护：会话清理、任务通知、自主浏览、自动记忆提取。"""
        try:
            from config.settings import AUTO_MEMORY_EXTRACT_ENABLED, BROWSE_ENABLED, BROWSER_ENABLED
            from src.heartbeat.actions.session_reaper import SessionReaperAction
            from src.heartbeat.actions.task_notification import TaskNotificationAction
            action_classes = [SessionReaperAction, TaskNotificationAction]
            if BROWSE_ENABLED and BROWSER_ENABLED:
                from src.heartbeat.actions.autonomous_browsing import AutonomousBrowsingAction
                action_classes.append(AutonomousBrowsingAction)
            if AUTO_MEMORY_EXTRACT_ENABLED:
                from src.heartbeat.actions.auto_memory import AutoMemoryAction
                action_classes.append(AutoMemoryAction)
            for ActionCls in action_classes:
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
        """每日 3AM 维护：记忆整理、索引优化、压缩检查、自省、事件日志清理。"""
        try:
            from src.heartbeat.actions.consolidation import MemoryConsolidationAction
            from src.heartbeat.actions.memory_maintenance import MemoryMaintenanceAction
            from src.heartbeat.actions.compaction_check import CompactionCheckAction
            from src.heartbeat.actions.self_reflection import SelfReflectionAction
            for ActionCls in (MemoryConsolidationAction, MemoryMaintenanceAction, CompactionCheckAction, SelfReflectionAction):
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

        # 清理过期事件日志
        try:
            from src.logging.event_logger import get_event_logger
            get_event_logger().cleanup_old_events()
        except Exception:
            logger.debug("事件日志清理失败", exc_info=True)

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
