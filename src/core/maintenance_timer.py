"""MaintenanceTimer — daily background housekeeping.

Blueprint v2.0 Step 4 §M7 introduced this timer to own the hourly/daily
schedule that ``ConsciousnessEngine`` used to run inline. After the
MVP cleanup (2026-04-19) the only real work left is the daily semantic
distillation pass at 3 AM local time; the old hourly heartbeat actions
(session reaping, task notifications, autonomous browsing, etc.) have
all been retired — their responsibilities either moved to the
``InnerTickScheduler`` / ``DurableScheduler`` or were deleted with the
v1 "compensation engineering" layer.

Design:
  * One asyncio task that ticks every minute and decides what to run.
  * Daily actions run once when the local hour reaches 3.
  * No interaction with the LLM main loop — maintenance is bookkeeping,
    not user-facing, and is not interruptible by OWNER messages.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from src.core.brain import LapwingBrain

logger = logging.getLogger("lapwing.core.maintenance_timer")


class MaintenanceTimer:
    """Daily maintenance pulse (semantic distillation)."""

    DAILY_HOUR = 3  # 3 AM local time
    TICK_SECONDS = 60

    def __init__(self, brain: "LapwingBrain") -> None:
        self._brain = brain
        self._task: asyncio.Task | None = None
        self._alive = False
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
                await self._maybe_run_daily()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("MaintenanceTimer loop crashed; backing off 30s")
                await asyncio.sleep(30)

    async def _maybe_run_daily(self) -> None:
        hour = datetime.now().hour
        if hour == self.DAILY_HOUR and not self._daily_done_today:
            self._daily_done_today = True
            await self._run_daily()
        elif hour != self.DAILY_HOUR:
            self._daily_done_today = False

    async def _run_daily(self) -> None:
        distiller = getattr(self._brain, "_semantic_distiller", None)
        if distiller is None:
            return
        try:
            written = await distiller.distill_recent()
            logger.info("daily semantic distillation wrote %d facts", written)
        except Exception:
            logger.warning("daily semantic distillation failed", exc_info=True)
