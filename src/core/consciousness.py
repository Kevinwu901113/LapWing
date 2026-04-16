"""自主意识循环引擎 — 替代旧的 HeartbeatEngine。

核心思路：
- 定期向 Brain 注入一条内部消息，触发完整的 agent loop
- LLM 自己决定做什么（或什么都不做）
- 用户对话时暂停，对话结束后恢复
- 动态调整 tick 间隔（退避 + [NEXT: Xm] 指令）
- Phase 4：紧急队列、TickBudget、OWNER 中断信号、退避逻辑
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
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

logger = logging.getLogger("lapwing.core.consciousness")


# ── SenseContext：维护 action 使用的最小上下文 ──

@dataclass
class SenseContext:
    """维护 action 使用的最小上下文（替代旧 heartbeat.SenseContext）。"""
    beat_type: str
    now: Any
    last_interaction: Any
    silence_hours: float
    user_facts_summary: str
    recent_memory_summary: str
    chat_id: str
    now_taipei_hour: int


@dataclass
class TickBudget:
    """单次 tick 的资源上限。"""
    max_tokens: int = 10000
    max_tool_calls: int = 10
    max_time_seconds: int = 120


class ConsciousnessEngine:
    """自主意识循环。"""

    # 退避参数
    BASE_INTERVAL = CONSCIOUSNESS_DEFAULT_INTERVAL   # 600s
    MIN_INTERVAL = CONSCIOUSNESS_MIN_INTERVAL        # 120s
    MAX_INTERVAL = CONSCIOUSNESS_MAX_INTERVAL        # 14400s
    BACKOFF_FACTOR = 1.5

    def __init__(
        self,
        brain: "LapwingBrain",
        send_fn: Callable[..., Awaitable[Any]],
        dispatcher=None,
    ) -> None:
        self._brain = brain
        self._send_fn = send_fn
        self._dispatcher = dispatcher

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

        # Phase 4 新增
        self.urgency_queue: asyncio.Queue = asyncio.Queue()
        self._interrupt_flag = asyncio.Event()
        self.idle_streak: int = 0
        self.tick_budget = TickBudget()

    # ── 生命周期 ──

    async def start(self) -> None:
        self._running = True
        self._working_memory_path.parent.mkdir(parents=True, exist_ok=True)
        self._task = asyncio.create_task(self._loop(), name="consciousness-loop")
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
        logger.info("意识循环已停止")

    # ── 对话状态管理 ──

    def on_conversation_start(self) -> None:
        self._in_conversation = True
        self._conversation_event.clear()
        self.interrupt()  # 通知 tick 立即让出
        if self._thinking_task and not self._thinking_task.done():
            logger.info("用户发消息，中断自由思考")
            self._thinking_task.cancel()

    def on_conversation_end(self) -> None:
        self._in_conversation = False
        self._last_conversation_end = time.time()
        self._next_interval = CONSCIOUSNESS_AFTER_CHAT_INTERVAL
        self._conversation_event.set()

    # ── Phase 4 公共接口 ──

    def interrupt(self) -> None:
        """Kevin 发消息时调用，通知工具循环检查是否需要让出。"""
        self._interrupt_flag.set()

    def push_urgency(self, item: dict) -> None:
        """推送紧急事件（如 reminder 到期、agent 完成、系统事件）。
        item 格式: {"type": "reminder"|"agent_done"|"system", "content": str}
        """
        self.urgency_queue.put_nowait(item)

    def _drain_urgency(self) -> list:
        """取出队列中所有待处理事件。"""
        items: list = []
        while not self.urgency_queue.empty():
            try:
                items.append(self.urgency_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return items

    # ── 主循环 ──

    async def _loop(self) -> None:
        while self._running:
            try:
                # 等待下一个 tick，或提前被紧急事件唤醒
                try:
                    first_item = await asyncio.wait_for(
                        self.urgency_queue.get(),
                        timeout=self._next_interval,
                    )
                    # 有紧急事件——放回队列，让 _drain_urgency 统一处理
                    self.urgency_queue.put_nowait(first_item)
                    logger.info("紧急事件唤醒意识循环")
                except asyncio.TimeoutError:
                    pass  # 正常超时，进入 tick

                if self._in_conversation:
                    logger.debug("正在对话中，等待结束...")
                    await self._conversation_event.wait()
                    continue

                await self._run_maintenance_if_due()

                # 取出所有紧急事件
                urgent_items = self._drain_urgency()

                # 清除中断标志，准备本次 tick
                self._interrupt_flag.clear()

                self._thinking_task = asyncio.create_task(
                    self._think_freely(urgent_items), name="free-thinking"
                )
                try:
                    did_something: bool = await self._thinking_task
                except asyncio.CancelledError:
                    logger.info("自由思考被中断（用户发消息）")
                    await self._save_interrupted_state()
                    did_something = False

                if self._dispatcher is not None:
                    try:
                        await self._dispatcher.submit(
                            "system.heartbeat_tick",
                            payload={
                                "did_something": did_something,
                                "urgent_count": len(urgent_items),
                                "next_interval_seconds": self._next_interval,
                                "idle_streak": self.idle_streak,
                            },
                            actor="lapwing",
                        )
                    except Exception:
                        logger.debug("heartbeat_tick 事件提交失败", exc_info=True)

                # 根据是否有实质性活动调整退避
                self._adjust_interval_after_tick(did_something)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("意识循环异常")
                self._next_interval = CONSCIOUSNESS_DEFAULT_INTERVAL
                await asyncio.sleep(30)

    def _adjust_interval_after_tick(self, did_something: bool) -> None:
        if did_something:
            self.idle_streak = 0
            if self._next_interval > self.BASE_INTERVAL:
                self._next_interval = self.BASE_INTERVAL
        else:
            self.idle_streak += 1
            backoff = self.BASE_INTERVAL * (self.BACKOFF_FACTOR ** self.idle_streak)
            self._next_interval = min(int(backoff), self.MAX_INTERVAL)
            logger.debug(
                "空闲退避：streak=%d，下次间隔 %ds",
                self.idle_streak, self._next_interval,
            )

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

    async def _think_freely(self, urgent_items: list | None = None) -> bool:
        """执行一次自由思考 tick。返回是否做了有意义的事（用于退避逻辑）。"""
        internal_message = await self._build_consciousness_prompt(urgent_items)
        chat_id = "__consciousness__"
        try:
            response = await asyncio.wait_for(
                self._brain.think(
                    chat_id=chat_id,
                    user_message=internal_message,
                ),
                timeout=self.tick_budget.max_time_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "意识 tick 超时（>%ds），退避", self.tick_budget.max_time_seconds
            )
            backoff = min(self._next_interval * 2, self.MAX_INTERVAL)
            self._next_interval = max(self.MIN_INTERVAL, backoff)
            return False
        except Exception as exc:
            backoff = min(self._next_interval * 2, self.MAX_INTERVAL)
            self._next_interval = max(self.MIN_INTERVAL, backoff)
            logger.warning("意识循环 LLM 调用失败，退避 %ds: %s", self._next_interval, exc)
            return False

        clean_text, next_interval = self._parse_and_strip_next(response or "")

        # 优先使用 LLM 指定间隔，否则基于沉默时长推断
        if next_interval is not None:
            self._next_interval = max(
                self.MIN_INTERVAL,
                min(self.MAX_INTERVAL, int(next_interval)),
            )
        else:
            self._next_interval = self._silence_based_interval()

        await self._log_activity(clean_text)

        # 判断是否有实质性输出（非空回复且非"无事"表达）
        did_something = bool(
            clean_text
            and clean_text.strip()
            and clean_text.strip() not in {"无事", "无事。", "无事，", "nothing"}
        )

        logger.info(
            "自由思考完成，下次间隔 %ds，did_something=%s",
            self._next_interval, did_something,
        )
        return did_something

    async def _build_consciousness_prompt(self, urgent_items: list | None = None) -> str:
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

        # 紧急事件优先展示
        if urgent_items:
            parts.append("## ⚡ 紧急事件（请优先处理）\n")
            for item in urgent_items:
                event_type = item.get("type", "unknown")
                content = item.get("content", "")
                parts.append(f"- [{event_type}] {content}")
            parts.append("")
            parts.append("以上事件需要你立即响应，请先处理完再做其他事。")
            parts.append("")

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
        parts.append("- 如果你做了什么，用 write_note 记录下来")
        parts.append("- 如果你想找他说话，调用 send_message 工具")
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
        """每小时维护：会话清理、任务通知、自主浏览。"""
        try:
            from config.settings import BROWSE_ENABLED, BROWSER_ENABLED
            from src.heartbeat.actions.session_reaper import SessionReaperAction
            from src.heartbeat.actions.task_notification import TaskNotificationAction
            action_classes = [SessionReaperAction, TaskNotificationAction]
            if BROWSE_ENABLED and BROWSER_ENABLED:
                from src.heartbeat.actions.autonomous_browsing import AutonomousBrowsingAction
                action_classes.append(AutonomousBrowsingAction)
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
        """每日 3AM 维护：记忆整理、索引优化、压缩检查、自省。"""
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

    def _build_maintenance_context(self, beat_type: str):
        """构建最小 SenseContext 供维护 action 使用。"""
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
