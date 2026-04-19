"""MaintenanceTimer — periodic background actions.

Blueprint v2.0 Step 4 §M7. Owns the hourly/daily maintenance schedule
that ConsciousnessEngine used to handle inline. Splitting it out lets
M7 delete consciousness.py without losing real work (session reaping,
memory consolidation, etc.).

Design:
  * One asyncio task that ticks every minute and decides what to run.
  * Hourly actions run when the wall clock has advanced ≥ 3600s since
    the last hourly run.
  * Daily actions run once when the local hour reaches 3.
  * Actions are imported lazily inside the trigger methods so a missing
    optional dependency (e.g. browser) doesn't break the whole timer.
  * No interaction with the LLM main loop — maintenance writes through
    its own brain calls and is not interruptible by OWNER messages
    (these are bookkeeping operations, not user-facing).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:  # pragma: no cover
    from src.core.brain import LapwingBrain

logger = logging.getLogger("lapwing.maintenance_timer")


# Re-imported here so callers don't have to fish it out of the legacy
# consciousness module — keeps the maintenance API surface in one place.
@dataclass
class SenseContext:
    beat_type: str
    now: Any
    last_interaction: Any
    silence_hours: float
    user_facts_summary: str
    recent_memory_summary: str
    chat_id: str
    now_taipei_hour: int


class MaintenanceTimer:
    """Hourly + daily maintenance pulse.

    Constructed with a brain and a send_fn (used by some actions to
    push messages to OWNER). ``start()`` launches the background task;
    ``stop()`` cancels it. Internal state (``_last_hourly_at``,
    ``_daily_done_today``) persists only in process — reset at boot
    means a freshly-restarted process may run hourly maintenance
    immediately, which is the desired behaviour.
    """

    HOURLY_INTERVAL_SECONDS = 3600
    DAILY_HOUR = 3  # 3 AM local time
    TICK_SECONDS = 60

    def __init__(
        self,
        brain: "LapwingBrain",
        send_fn: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        self._brain = brain
        self._send_fn = send_fn
        self._task: asyncio.Task | None = None
        self._alive = False
        self._last_hourly_at: float = 0.0
        self._daily_done_today: bool = False

    async def start(self) -> None:
        if self._task is not None:
            return
        self._alive = True
        self._task = asyncio.create_task(self._loop(), name="maintenance-timer")
        logger.info("MaintenanceTimer 已启动")

    async def stop(self) -> None:
        self._alive = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("MaintenanceTimer 已停止")

    async def _loop(self) -> None:
        while self._alive:
            try:
                await asyncio.sleep(self.TICK_SECONDS)
                if not self._alive:
                    break
                await self._maybe_run_hourly()
                await self._maybe_run_daily()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("MaintenanceTimer loop crashed; backing off 30s")
                await asyncio.sleep(30)

    async def _maybe_run_hourly(self) -> None:
        now = time.time()
        if now - self._last_hourly_at < self.HOURLY_INTERVAL_SECONDS:
            return
        self._last_hourly_at = now
        await self._run_hourly()

    async def _maybe_run_daily(self) -> None:
        hour = datetime.now().hour
        if hour == self.DAILY_HOUR and not self._daily_done_today:
            self._daily_done_today = True
            await self._run_daily()
        elif hour != self.DAILY_HOUR:
            self._daily_done_today = False

    async def _run_hourly(self) -> None:
        try:
            from config.settings import BROWSE_ENABLED, BROWSER_ENABLED
            from src.heartbeat.actions.session_reaper import SessionReaperAction
            from src.heartbeat.actions.task_notification import TaskNotificationAction

            action_classes: list = [SessionReaperAction, TaskNotificationAction]
            if BROWSE_ENABLED and BROWSER_ENABLED:
                from src.heartbeat.actions.autonomous_browsing import (
                    AutonomousBrowsingAction,
                )
                action_classes.append(AutonomousBrowsingAction)

            for cls in action_classes:
                action = cls()
                try:
                    await action.execute(
                        self._build_context("fast"),
                        self._brain,
                        self._send_fn,
                    )
                except Exception as exc:
                    logger.warning("hourly maintenance %s failed: %s", action.name, exc)
        except Exception:
            logger.debug("hourly maintenance load failed", exc_info=True)

    async def _run_daily(self) -> None:
        try:
            from src.heartbeat.actions.compaction_check import CompactionCheckAction
            from src.heartbeat.actions.consolidation import MemoryConsolidationAction
            from src.heartbeat.actions.memory_maintenance import (
                MemoryMaintenanceAction,
            )
            from src.heartbeat.actions.self_reflection import SelfReflectionAction

            for cls in (
                MemoryConsolidationAction,
                MemoryMaintenanceAction,
                CompactionCheckAction,
                SelfReflectionAction,
            ):
                action = cls()
                try:
                    await action.execute(
                        self._build_context("slow"),
                        self._brain,
                        self._send_fn,
                    )
                except Exception as exc:
                    logger.warning("daily maintenance %s failed: %s", action.name, exc)
        except Exception:
            logger.debug("daily maintenance load failed", exc_info=True)

        # Step 7: semantic distillation — runs once per day after the
        # heartbeat actions so the distiller sees any episodes those
        # actions may have written. Failure is logged; no retry (next
        # day's cycle will catch up).
        distiller = getattr(self._brain, "_semantic_distiller", None)
        if distiller is not None:
            try:
                written = await distiller.distill_recent()
                logger.info("daily semantic distillation wrote %d facts", written)
            except Exception:
                logger.warning("daily semantic distillation failed", exc_info=True)

    def _build_context(self, beat_type: str) -> SenseContext:
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
