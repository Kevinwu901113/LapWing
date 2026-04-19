"""MainLoop — single runtime driver for Lapwing.

Blueprint v2.0 Step 4. Replaces the parallel "consciousness loop +
adapter-driven think_conversational" model with one consumer that
serves a single ``EventQueue``. Adapters, the inner-tick scheduler, and
system signals all enqueue ``Event`` instances; MainLoop dispatches
each to the correct handler.

This file ships the skeleton (M1.c). Handlers are stubbed with TODO
markers that point at the milestone that fills them in:

  * ``_handle_message``  → M2 (adapter migration)
  * ``_handle_inner_tick`` → M3 (consciousness migration)
  * ``_handle_system``   → M4+ (interrupt-driven control)

The OWNER-interrupt cancellation machinery hangs off ``_current_task``
and is implemented in M4. For now ``_dispatch`` only logs the event and
calls the stub handler so the loop can be unit-tested for lifecycle
behaviour without depending on brain wiring.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from src.core.events import (
    PRIORITY_OWNER_MESSAGE,
    Event,
    InnerTickEvent,
    MessageEvent,
    SystemEvent,
)

if TYPE_CHECKING:  # pragma: no cover - type-only imports
    from src.core.brain import LapwingBrain
    from src.core.event_queue import EventQueue
    from src.core.inner_tick_scheduler import InnerTickScheduler

logger = logging.getLogger("lapwing.core.main_loop")


class MainLoop:
    """Single consumer for the EventQueue.

    Lifecycle:
      * ``await loop.run()`` blocks until ``stop()`` is called.
      * ``stop()`` flips ``_alive`` to False and cancels any in-flight
        handler so shutdown does not hang waiting on an LLM stream.

    The brain reference is optional in M1 so the skeleton can be
    constructed and exercised without wiring AppContainer.
    """

    # How often the OWNER-preempt watcher checks the queue. 50 ms keeps
    # interrupt latency well under perceptual threshold without burning
    # measurable CPU on an idle loop.
    OWNER_WATCHER_POLL_SECONDS = 0.05

    def __init__(
        self,
        queue: "EventQueue",
        brain: "LapwingBrain | None" = None,
        inner_tick_scheduler: "InnerTickScheduler | None" = None,
    ) -> None:
        self._queue = queue
        self._brain = brain
        self._scheduler = inner_tick_scheduler
        self._alive = False
        self._current_task: asyncio.Task | None = None
        # M4 will read this to decide whether a cancellation was
        # pre-emptive (set True) versus a normal task completion.
        self._cancel_requested = False
        self._owner_watcher_task: asyncio.Task | None = None

    # ── Lifecycle ────────────────────────────────────────────────────

    async def run(self) -> None:
        """Consume the queue until ``stop()`` is called."""
        self._alive = True
        logger.info("MainLoop started")
        # Step 4 M4: spawn a concurrent watcher that cancels the in-flight
        # handler the moment an OWNER message lands in the queue. Without
        # it the run loop is blocked awaiting the handler and only sees
        # the new event after the handler completes.
        self._owner_watcher_task = asyncio.create_task(
            self._owner_preempt_watcher(), name="lapwing-owner-watcher",
        )
        try:
            while self._alive:
                event = await self._queue.get()
                if not self._alive:
                    # stop() raced the queue.get; do not dispatch.
                    break
                await self._dispatch(event)
        finally:
            if self._owner_watcher_task is not None:
                self._owner_watcher_task.cancel()
                try:
                    await self._owner_watcher_task
                except asyncio.CancelledError:
                    pass
                self._owner_watcher_task = None
            await self._cancel_in_flight("loop_shutdown")
            logger.info("MainLoop stopped")

    async def stop(self) -> None:
        """Signal the loop to exit and cancel any in-flight handler."""
        self._alive = False
        await self._cancel_in_flight("loop_shutdown")

    async def _owner_preempt_watcher(self) -> None:
        """Concurrent observer: cancel ``_current_task`` when OWNER queues.

        Run as a separate asyncio task alongside the dispatch loop. The
        only state it touches is ``_current_task`` (cancel) and the
        queue (read-only ``has_owner_message`` peek), so it never races
        with the dispatcher's own bookkeeping.

        When it fires, the OWNER message stays in the queue — the next
        ``queue.get()`` in the run loop pops it, ``_dispatch`` sees the
        OWNER priority, and the regular handler path runs.
        """
        try:
            while self._alive:
                await asyncio.sleep(self.OWNER_WATCHER_POLL_SECONDS)
                task = self._current_task
                if task is None or task.done():
                    continue
                if not self._queue.has_owner_message():
                    continue
                logger.info("OWNER message detected — preempting in-flight handler")
                await self._cancel_in_flight("owner_message_preempt")
        except asyncio.CancelledError:
            pass

    # ── Dispatch ─────────────────────────────────────────────────────

    async def _dispatch(self, event: Event) -> None:
        """Route ``event`` to the handler that matches its kind.

        OWNER messages (``priority == PRIORITY_OWNER_MESSAGE``) preempt
        any in-flight handler before the OWNER's own dispatch begins —
        this is Step 4 M4's "instant interrupt" guarantee. The cancelled
        handler's brain side persists whatever partial output it had as
        an INTERRUPTED trajectory entry, then re-raises CancelledError;
        we swallow that here (the partial is already saved) and proceed
        to the OWNER dispatch.
        """
        if (
            event.priority == PRIORITY_OWNER_MESSAGE
            and self._current_task is not None
            and not self._current_task.done()
        ):
            await self._interrupt_current(reason="owner_message_preempt")

        try:
            if isinstance(event, MessageEvent):
                await self._handle_message(event)
            elif isinstance(event, InnerTickEvent):
                await self._handle_inner_tick(event)
            elif isinstance(event, SystemEvent):
                await self._handle_system(event)
            else:
                logger.warning("Unknown event kind: %s", event.kind)
        except asyncio.CancelledError:
            # The handler cancelled itself (preempt or shutdown). Brain
            # side already persisted partial output; nothing more to do.
            logger.info("Handler cancelled while dispatching %s", event.kind)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Handler crashed for %s: %s", event.kind, exc)

    async def _interrupt_current(self, reason: str) -> None:
        """Preempt the in-flight handler, marking the cancellation as requested.

        Wrapper around ``_cancel_in_flight`` that records ``reason`` for
        observability and the cancellation flag for any handler-side
        logic that wants to distinguish a deliberate preempt from a
        normal task completion.
        """
        await self._cancel_in_flight(reason)

    async def _cancel_in_flight(self, reason: str) -> None:
        """Cancel ``_current_task`` if one is running."""
        task = self._current_task
        if task is None or task.done():
            return
        self._cancel_requested = True
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            logger.info("In-flight handler cancelled (reason=%s)", reason)
        except Exception:  # pragma: no cover - defensive
            logger.exception("Error awaiting cancelled task")
        finally:
            self._current_task = None
            self._cancel_requested = False

    # ── Handlers (stubs filled in later milestones) ──────────────────

    async def _handle_message(self, event: MessageEvent) -> None:
        """Drive ``brain.think_conversational`` for ``event``.

        The actual call is wrapped in ``self._current_task`` so M4's
        interrupt path can cancel it. ``done_future`` (when supplied by
        the producer) gets the assistant's full reply or the exception
        the brain raised — this is how the desktop ``/ws/chat`` route
        keeps its synchronous "send reply, then close turn" semantic.
        """
        if self._brain is None:
            logger.warning("MessageEvent received but no brain wired")
            if event.done_future is not None and not event.done_future.done():
                event.done_future.set_result("")
            return

        async def _drive() -> str:
            return await self._brain.think_conversational(
                chat_id=event.chat_id,
                user_message=event.text,
                send_fn=event.send_fn,
                typing_fn=event.typing_fn,
                status_callback=event.status_callback,
                adapter=event.adapter,
                user_id=event.user_id,
                images=list(event.images) if event.images else None,
            )

        task = asyncio.create_task(_drive(), name=f"think_conv:{event.chat_id}")
        self._current_task = task
        try:
            reply = await task
            if event.done_future is not None and not event.done_future.done():
                event.done_future.set_result(reply)
        except asyncio.CancelledError:
            # M4 surfaces partial output here; for now propagate the
            # cancellation to the producer so it can clean up.
            if event.done_future is not None and not event.done_future.done():
                event.done_future.cancel()
            raise
        except Exception as exc:
            logger.exception("think_conversational failed for %s", event.chat_id)
            if event.done_future is not None and not event.done_future.done():
                event.done_future.set_exception(exc)
        finally:
            if self._current_task is task:
                self._current_task = None

    async def _handle_inner_tick(self, event: InnerTickEvent) -> None:
        """Drive ``brain.think_inner`` for one tick.

        Self-yield rule: if a higher-priority OWNER message arrived
        between scheduling and dispatch, skip this tick and re-enqueue
        nothing — the scheduler will fire again after its own delay.
        Avoids burning a turn on inner thinking when Kevin is mid-typing.
        """
        if self._brain is None:
            logger.debug("InnerTickEvent received but no brain wired")
            return

        if self._queue.has_owner_message():
            logger.info(
                "Inner tick skipped — OWNER message queued (reason=%s)",
                event.reason,
            )
            return

        urgent_items: list[dict] = []
        if self._scheduler is not None:
            urgent_items = self._scheduler.drain_urgency()

        async def _drive():
            return await self._brain.think_inner(urgent_items=urgent_items)

        task = asyncio.create_task(_drive(), name="think_inner")
        self._current_task = task
        try:
            reply, next_interval, did_something = await task
        except asyncio.CancelledError:
            if self._scheduler is not None:
                self._scheduler.note_tick_failed()
            raise
        except Exception:
            logger.exception("think_inner crashed")
            if self._scheduler is not None:
                self._scheduler.note_tick_failed()
            return
        finally:
            if self._current_task is task:
                self._current_task = None

        if self._scheduler is not None:
            self._scheduler.note_tick_result(
                did_something=did_something,
                llm_next_interval=next_interval,
            )
        logger.info(
            "Inner tick done — did_something=%s next_interval=%s",
            did_something, next_interval,
        )

    async def _handle_system(self, event: SystemEvent) -> None:
        # Right now only "shutdown" is meaningful — it triggers stop()
        # so the runtime can be torn down via an in-band event.
        logger.debug("SystemEvent stub: action=%s", event.action)
        if event.action == "shutdown":
            await self.stop()
