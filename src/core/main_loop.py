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
import hashlib
import logging
import time
import uuid
from typing import TYPE_CHECKING

from src.core.events import (
    PRIORITY_OWNER_MESSAGE,
    Event,
    InnerTickEvent,
    MessageEvent,
    OperatorControlEvent,
    SystemEvent,
)

if TYPE_CHECKING:  # pragma: no cover - type-only imports
    from src.core.brain import LapwingBrain
    from src.core.chat_activity import ChatActivityTracker
    from src.core.event_queue import EventQueue
    from src.core.inner_tick_scheduler import InnerTickScheduler

logger = logging.getLogger("lapwing.core.main_loop")

_AGENT_COGNITIVE_EVENT_KINDS = {
    "agent_task_result",
    "agent_needs_input",
    "agent_progress_urgency",
}


def _concurrent_bg_work_p4_enabled() -> bool:
    try:
        from src.config import get_settings
        flags = get_settings().concurrent_bg_work
        return bool(flags.enabled and flags.p4_cancellation_evolution)
    except Exception:
        return False


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

    # When an OWNER message is dispatched, wait this long before starting
    # the brain call so that rapid-fire follow-up messages can be merged
    # into a single turn. 0.5 s is short enough to feel instant but long
    # enough to catch copy-paste bursts.
    OWNER_COALESCE_SECONDS = 0.5

    FOREGROUND_TIMEOUT_REPLY = (
        "这次查询卡住了，可能是工具调度或外部检索异常。"
        "我先停止这次查询；如果你还需要，我可以不用实时搜索，"
        "直接按已有信息给你一个保守建议。"
    )
    FOREGROUND_EXCEPTION_REPLY = (
        "这次处理时内部出错了，不是你的问题。"
        "我已经停下这轮任务；你可以直接重发一句，我会重新处理。"
    )

    def __init__(
        self,
        queue: "EventQueue",
        brain: "LapwingBrain | None" = None,
        inner_tick_scheduler: "InnerTickScheduler | None" = None,
        *,
        chat_activity_tracker: "ChatActivityTracker | None" = None,
        foreground_turn_timeout_seconds: int | None = None,
        owner_status_probe_grace_seconds: int | None = None,
    ) -> None:
        self._queue = queue
        self._brain = brain
        self._scheduler = inner_tick_scheduler
        self._chat_activity_tracker = chat_activity_tracker
        self._alive = False
        self._current_task: asyncio.Task | None = None
        self._current_message_event: MessageEvent | None = None
        self._current_turn_id: str | None = None
        self._current_turn_started_mono: float | None = None
        self._cancel_user_visible_status: str | None = None
        # M4 will read this to decide whether a cancellation was
        # pre-emptive (set True) versus a normal task completion.
        self._cancel_requested = False
        self._owner_watcher_task: asyncio.Task | None = None
        self._handling_owner: bool = False
        self.foreground_turn_timeout_seconds = (
            int(foreground_turn_timeout_seconds)
            if foreground_turn_timeout_seconds is not None
            else _foreground_turn_timeout_seconds()
        )
        self.owner_status_probe_grace_seconds = (
            float(owner_status_probe_grace_seconds)
            if owner_status_probe_grace_seconds is not None
            else _owner_status_probe_grace_seconds()
        )
        from src.core.concurrent_bg_work.speaking import SpeakingArbiter
        self._speaking_arbiter = SpeakingArbiter(
            chat_activity_tracker=chat_activity_tracker,
        )

    @property
    def speaking_arbiter(self):
        return self._speaking_arbiter

    def is_handling_foreground_user_turn(self, chat_id: str) -> bool:
        event = self._current_message_event
        task = self._current_task
        return (
            event is not None
            and event.chat_id == chat_id
            and task is not None
            and not task.done()
        )

    def has_stuck_user_turn(self, chat_id: str) -> bool:
        if not self.is_handling_foreground_user_turn(chat_id):
            return False
        started = self._current_turn_started_mono
        if started is None:
            return False
        return (time.monotonic() - started) > self.foreground_turn_timeout_seconds

    # ── Lifecycle ────────────────────────────────────────────────────

    async def run(self) -> None:
        """Consume the queue until ``stop()`` is called."""
        self._alive = True
        logger.info("MainLoop started")
        # Step 4 M4: spawn a concurrent watcher that handles OWNER
        # preemption/status probes while the dispatcher is blocked awaiting a
        # long-running handler. This must stay active even when P4 cancellation
        # evolution is enabled; otherwise OWNER-over-OWNER recovery regresses.
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
                if self._handling_owner:
                    await self._maybe_handle_owner_over_owner_interrupt()
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
        any in-flight *non-OWNER* handler before the OWNER's own dispatch
        begins. An OWNER handler is never preempted by another OWNER
        message — the new message waits in the queue (and may be coalesced
        by ``_handle_message``).
        """
        if (
            not _concurrent_bg_work_p4_enabled()
            and
            event.priority == PRIORITY_OWNER_MESSAGE
            and self._current_task is not None
            and not self._current_task.done()
            and not self._handling_owner
        ):
            await self._interrupt_current(reason="owner_message_preempt")

        is_owner_msg = (
            isinstance(event, MessageEvent)
            and event.priority == PRIORITY_OWNER_MESSAGE
        )
        self._handling_owner = is_owner_msg
        try:
            if isinstance(event, MessageEvent):
                await self._handle_message(event)
            elif isinstance(event, InnerTickEvent):
                await self._handle_inner_tick(event)
            elif isinstance(event, SystemEvent):
                await self._handle_system(event)
            elif isinstance(event, OperatorControlEvent):
                await self._handle_operator_control(event)
            elif event.kind in _AGENT_COGNITIVE_EVENT_KINDS:
                await self._handle_agent_cognitive_event(event)
            else:
                logger.warning("Unknown event kind: %s", event.kind)
        except asyncio.CancelledError:
            logger.info("Handler cancelled while dispatching %s", event.kind)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Handler crashed for %s: %s", event.kind, exc)
        finally:
            self._handling_owner = False

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

        **OWNER coalescing**: when the event is OWNER-priority, we sleep
        briefly (``OWNER_COALESCE_SECONDS``) then drain any additional
        OWNER messages for the same ``chat_id`` from the queue, merging
        their text and images into a single brain call. This prevents
        rapid-fire messages from producing independent cancel→restart
        cycles while still giving the user a unified reply.
        """
        if self._brain is None:
            logger.warning("MessageEvent received but no brain wired")
            if event.done_future is not None and not event.done_future.done():
                event.done_future.set_result("")
            return

        turn_id = event.event_id or f"turn_{uuid.uuid4().hex}"
        tracker = self._chat_activity_tracker
        if tracker is not None:
            tracker.mark_inbound_user_message(
                event.chat_id,
                user_id=event.user_id,
                message_id=event.source_message_id,
                event_id=event.event_id,
                idempotency_key=event.idempotency_key,
            )

        # ── Coalesce rapid-fire OWNER messages ──────────────────────
        merged_text = event.text
        merged_images = list(event.images) if event.images else []
        coalesced_futures: list = []

        if event.priority == PRIORITY_OWNER_MESSAGE:
            await asyncio.sleep(self.OWNER_COALESCE_SECONDS)
            requeue: list[Event] = []
            while True:
                extra = self._queue.get_nowait()
                if extra is None:
                    break
                if (
                    isinstance(extra, MessageEvent)
                    and extra.priority == PRIORITY_OWNER_MESSAGE
                    and extra.chat_id == event.chat_id
                ):
                    logger.info(
                        "Coalescing OWNER message into current turn "
                        "(+%d chars)", len(extra.text),
                    )
                    if extra.text:
                        merged_text += "\n" + extra.text
                    if extra.images:
                        merged_images.extend(extra.images)
                    if extra.done_future is not None and not extra.done_future.done():
                        coalesced_futures.append(extra.done_future)
                    if tracker is not None:
                        tracker.mark_inbound_user_message(
                            extra.chat_id,
                            user_id=extra.user_id,
                            message_id=extra.source_message_id,
                            event_id=extra.event_id,
                            idempotency_key=extra.idempotency_key,
                        )
                else:
                    requeue.append(extra)
            for ev in requeue:
                await self._queue.put(ev)

        # ── Drive brain ─────────────────────────────────────────────
        final_text = merged_text
        final_images = merged_images
        await self._maybe_record_topic_stop(event.chat_id, final_text)

        send_fn = self._wrap_user_reply_send_fn(event)

        async def _drive() -> str:
            return await self._brain.think_conversational(
                chat_id=event.chat_id,
                user_message=final_text,
                send_fn=send_fn,
                typing_fn=event.typing_fn,
                status_callback=event.status_callback,
                adapter=event.adapter,
                user_id=event.user_id,
                images=final_images if final_images else None,
            )

        task = asyncio.create_task(_drive(), name=f"think_conv:{event.chat_id}")
        self._current_task = task
        self._current_message_event = event
        self._current_turn_id = turn_id
        self._current_turn_started_mono = time.monotonic()
        self._cancel_user_visible_status = None
        if tracker is not None:
            tracker.mark_turn_started(
                event.chat_id,
                turn_id=turn_id,
                user_id=event.user_id,
                event_id=event.event_id,
                source_message_id=event.source_message_id,
                text_preview=final_text,
            )
        logger.info(
            "foreground_turn_started chat_id=%s user_id=%s event_id=%s turn_id=%s message_id=%s",
            event.chat_id,
            event.user_id,
            event.event_id or "",
            turn_id,
            event.source_message_id or "",
        )
        try:
            reply = await asyncio.wait_for(
                task,
                timeout=self.foreground_turn_timeout_seconds,
            )
            if event.done_future is not None and not event.done_future.done():
                event.done_future.set_result(reply)
            for f in coalesced_futures:
                if not f.done():
                    f.set_result(reply)
            if tracker is not None:
                tracker.mark_turn_terminal(event.chat_id, turn_id=turn_id, status="replied")
            logger.info(
                "foreground_turn_ended chat_id=%s turn_id=%s status=replied",
                event.chat_id,
                turn_id,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "foreground_turn_timed_out chat_id=%s turn_id=%s timeout_seconds=%s",
                event.chat_id,
                turn_id,
                self.foreground_turn_timeout_seconds,
            )
            delivered = await self._send_user_visible_status(
                event,
                self.FOREGROUND_TIMEOUT_REPLY,
                source="foreground_timeout",
            )
            terminal_status = (
                "failed_with_user_visible_error"
                if delivered
                else "failed_without_user_visible_error"
            )
            if tracker is not None:
                tracker.mark_turn_terminal(
                    event.chat_id,
                    turn_id=turn_id,
                    status=terminal_status,
                )
            if event.done_future is not None and not event.done_future.done():
                event.done_future.set_result(self.FOREGROUND_TIMEOUT_REPLY if delivered else "")
            for f in coalesced_futures:
                if not f.done():
                    f.set_result(self.FOREGROUND_TIMEOUT_REPLY if delivered else "")
            logger.info(
                "stuck_user_turn_recovery chat_id=%s turn_id=%s delivered=%s",
                event.chat_id,
                turn_id,
                delivered,
            )
        except asyncio.CancelledError:
            status_text = self._cancel_user_visible_status
            if event.done_future is not None and not event.done_future.done():
                if status_text:
                    event.done_future.set_result(status_text)
                else:
                    event.done_future.cancel()
            for f in coalesced_futures:
                if not f.done():
                    if status_text:
                        f.set_result(status_text)
                    else:
                        f.cancel()
            if tracker is not None:
                tracker.mark_turn_terminal(
                    event.chat_id,
                    turn_id=turn_id,
                    status=(
                        "superseded_with_user_visible_status"
                        if status_text
                        else "cancelled"
                    ),
                )
            logger.info(
                "foreground_turn_cancelled chat_id=%s turn_id=%s user_visible=%s",
                event.chat_id,
                turn_id,
                bool(status_text),
            )
            raise
        except Exception as exc:
            logger.exception(
                "think_conversational failed for chat_id=%s user_id=%s event_id=%s "
                "turn_id=%s message_id=%s",
                event.chat_id,
                event.user_id,
                event.event_id or "",
                turn_id,
                event.source_message_id or "",
            )
            delivered = await self._send_user_visible_status(
                event,
                self.FOREGROUND_EXCEPTION_REPLY,
                source="foreground_exception",
            )
            terminal_status = (
                "failed_with_user_visible_error"
                if delivered
                else "failed_without_user_visible_error"
            )
            if event.done_future is not None and not event.done_future.done():
                if delivered:
                    event.done_future.set_result(self.FOREGROUND_EXCEPTION_REPLY)
                else:
                    event.done_future.set_exception(exc)
            for f in coalesced_futures:
                if not f.done():
                    if delivered:
                        f.set_result(self.FOREGROUND_EXCEPTION_REPLY)
                    else:
                        f.set_exception(exc)
            if tracker is not None:
                tracker.mark_turn_terminal(
                    event.chat_id,
                    turn_id=turn_id,
                    status=terminal_status,
                )
            logger.info(
                "foreground_exception_recovery chat_id=%s user_id=%s event_id=%s "
                "turn_id=%s message_id=%s delivered=%s terminal_status=%s",
                event.chat_id,
                event.user_id,
                event.event_id or "",
                turn_id,
                event.source_message_id or "",
                delivered,
                terminal_status,
            )
        finally:
            if self._current_task is task:
                self._current_task = None
            if self._current_message_event is event:
                self._current_message_event = None
                self._current_turn_id = None
                self._current_turn_started_mono = None
                self._cancel_user_visible_status = None

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

    async def _handle_agent_cognitive_event(self, event: Event) -> None:
        """Route background-agent events into Lapwing's cognitive path.

        The full P2.5 TurnBatcher is still foundation-only. Until that phase is
        enabled end-to-end, agent result/needs-input/progress events must not
        fall through as unknown queue entries; they become urgent inner-turn
        context so the global cognitive loop can observe the updated StateView.
        """
        if self._brain is None:
            logger.debug("Agent cognitive event received but no brain wired: %s", event.kind)
            return

        delivery_target = str(getattr(event, "delivery_target", "") or "")
        if delivery_target in {
            "parent_turn",
            "chat_status",
            "silent",
            "desktop_progress_only",
        }:
            triggering = getattr(event, "triggering_event", None)
            logger.info(
                "agent_task_result_routing task_id=%s parent_turn_id=%s parent_event_id=%s delivery_target=%s orphan=%s stale=%s",
                getattr(event, "task_id", ""),
                getattr(triggering, "payload", {}).get("parent_turn_id", "") if triggering else "",
                getattr(triggering, "payload", {}).get("parent_event_id", "") if triggering else "",
                delivery_target,
                bool(getattr(event, "orphan", False)),
                bool(getattr(event, "stale", False)),
            )
            if delivery_target in {"parent_turn", "chat_status"}:
                await self._deliver_agent_status_event(event, delivery_target)
            return

        urgent_item = _agent_event_to_urgent_item(event)

        async def _drive():
            return await self._brain.think_inner(urgent_items=[urgent_item])

        task = asyncio.create_task(_drive(), name=f"agent_event:{event.kind}")
        self._current_task = task
        try:
            await task
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("agent cognitive event handling failed: %s", event.kind)
        finally:
            if self._current_task is task:
                self._current_task = None

    async def _handle_operator_control(self, event: OperatorControlEvent) -> None:
        try:
            from src.config import get_settings
            enabled = get_settings().operator.emergency_control_enabled
        except Exception:
            enabled = False
        if not enabled:
            logger.info("OperatorControlEvent ignored because emergency control is disabled")
            return
        if event.command in {"freeze_loop", "stop_all"}:
            await self._cancel_in_flight(f"operator:{event.reason}")

    async def _deliver_agent_status_event(self, event: Event, delivery_target: str) -> bool:
        triggering = getattr(event, "triggering_event", None)
        triggering_payload = getattr(triggering, "payload", None) or {}
        raw_chat_id = str(
            triggering_payload.get("delivery_chat_id")
            or getattr(triggering, "chat_id", "")
            or ""
        )
        parent_turn_present = bool(triggering_payload.get("parent_turn_id"))
        parent_event_present = bool(triggering_payload.get("parent_event_id"))
        raw_tail, raw_hash = _safe_tail_hash(raw_chat_id)
        orphan = bool(getattr(event, "orphan", False))
        stale = bool(getattr(event, "stale", False))

        from src.adapters.base import ChannelType

        channel_manager = getattr(self._brain, "channel_manager", None)
        channel = getattr(channel_manager, "last_active_channel", None) if channel_manager is not None else None
        if channel is None:
            channel = ChannelType.QQ

        if orphan or stale:
            logger.info(
                "agent_task_result_delivery_skipped task_id=%s delivery_target=%s "
                "raw_chat_id_tail=%s raw_chat_id_hash=%s channel=%s "
                "parent_turn_id_present=%s parent_event_id_present=%s orphan=%s stale=%s "
                "reason=orphan_or_stale",
                getattr(event, "task_id", ""),
                delivery_target,
                raw_tail,
                raw_hash,
                channel.value,
                parent_turn_present,
                parent_event_present,
                orphan,
                stale,
            )
            return False

        if not raw_chat_id:
            logger.info(
                "agent_task_result_delivery_skipped task_id=%s delivery_target=%s "
                "parent_turn_id_present=%s parent_event_id_present=%s orphan=%s stale=%s "
                "reason=missing_chat_id",
                getattr(event, "task_id", ""),
                delivery_target,
                parent_turn_present,
                parent_event_present,
                orphan,
                stale,
            )
            return False

        # Only agent_user_status / owner_status purposes may fall back from a
        # non-numeric QQ chat_id to the configured owner QQ id.  Require a
        # recognised delivery target and a parent identifier in the triggering
        # event payload — otherwise the delivery target is ambiguous.
        if delivery_target in ("parent_turn", "chat_status"):
            if parent_turn_present or parent_event_present:
                purpose = "agent_user_status"
            else:
                logger.info(
                    "agent_task_result_delivery_skipped task_id=%s delivery_target=%s "
                    "raw_chat_id_tail=%s raw_chat_id_hash=%s channel=%s "
                    "parent_turn_id_present=%s parent_event_id_present=%s orphan=%s stale=%s "
                    "reason=invalid_or_ambiguous_delivery_target",
                    getattr(event, "task_id", ""),
                    delivery_target,
                    raw_tail,
                    raw_hash,
                    channel.value,
                    parent_turn_present,
                    parent_event_present,
                    orphan,
                    stale,
                )
                return False
        else:
            logger.info(
                "agent_task_result_delivery_skipped task_id=%s delivery_target=%s "
                "raw_chat_id_tail=%s raw_chat_id_hash=%s channel=%s "
                "parent_turn_id_present=%s parent_event_id_present=%s orphan=%s stale=%s "
                "reason=invalid_or_ambiguous_delivery_target",
                getattr(event, "task_id", ""),
                delivery_target,
                raw_tail,
                raw_hash,
                channel.value,
                parent_turn_present,
                parent_event_present,
                orphan,
                stale,
            )
            return False

        if channel_manager is not None:
            chat_id = channel_manager.resolve_delivery_target(channel, raw_chat_id, purpose=purpose)
        else:
            chat_id = raw_chat_id

        if chat_id is None:
            logger.info(
                "agent_task_result_delivery_skipped task_id=%s delivery_target=%s "
                "raw_chat_id_tail=%s raw_chat_id_hash=%s channel=%s "
                "parent_turn_id_present=%s parent_event_id_present=%s orphan=%s stale=%s "
                "reason=invalid_qq_chat_id",
                getattr(event, "task_id", ""),
                delivery_target,
                raw_tail,
                raw_hash,
                channel.value,
                parent_turn_present,
                parent_event_present,
                orphan,
                stale,
            )
            return False

        text = _agent_status_text(event)
        if not text:
            return False

        snapshot = getattr(event, "task_snapshot", None)
        metadata = {
            "task_id": getattr(event, "task_id", ""),
            "delivery_target": delivery_target,
            "stale": stale,
            "topic_key": getattr(snapshot, "topic_key", None),
            "intent_key": getattr(snapshot, "intent_key", None),
            "generation": getattr(snapshot, "generation", None),
        }
        store = getattr(self._brain, "_background_task_store_ref", None)
        if store is not None and metadata.get("topic_key"):
            try:
                metadata["stopped_at_generation"] = await store.stopped_generation(
                    chat_id=chat_id,
                    topic_key=str(metadata["topic_key"]),
                )
            except Exception:
                logger.debug("stopped topic lookup failed", exc_info=True)

        event_type = str(getattr(getattr(triggering, "type", None), "value", "") or "")
        if getattr(event, "kind", "") == "agent_needs_input" or event_type == "agent_needs_input":
            gate = getattr(self._brain, "_expression_gate_ref", None)
            if gate is not None:
                await gate.reject_internal(
                    text,
                    source="agent_needs_input",
                    chat_id=chat_id,
                    mutation_log=getattr(self._brain, "_mutation_log_ref", None),
                    metadata=metadata,
                )
            return False
        if event_type in {"agent_failed", "agent_budget_exhausted"}:
            source = "background_failure"
        elif event_type == "agent_completed":
            source = "background_completion"
        elif event_type == "agent_cancelled":
            source = "confirmation"
        else:
            source = f"agent_task_result_{delivery_target}"

        async def _send(text_to_send: str) -> None:
            if channel_manager is None:
                raise RuntimeError("channel_manager unavailable for agent status delivery")
            await channel_manager.send(channel, chat_id, text_to_send)

        from src.core.system_send import send_system_message

        delivered = await send_system_message(
            _send,
            text,
            source=source,
            chat_id=chat_id,
            adapter="agent_task",
            trajectory_store=getattr(self._brain, "trajectory_store", None),
            mutation_log=getattr(self._brain, "_mutation_log_ref", None),
            expression_gate=getattr(self._brain, "_expression_gate_ref", None),
            metadata=metadata,
        )
        if delivered and self._chat_activity_tracker is not None:
            self._chat_activity_tracker.mark_assistant_reply(
                chat_id,
                source=f"agent_task_result_{delivery_target}",
            )
        logger.info(
            "agent_task_result_delivered task_id=%s delivery_target=%s chat_id=%s delivered=%s",
            getattr(event, "task_id", ""),
            delivery_target,
            chat_id,
            delivered,
        )
        return delivered

    def _wrap_user_reply_send_fn(self, event: MessageEvent):
        if event.send_fn is None:
            return None

        async def _send(text: str, *, source: str = "direct_reply", metadata: dict | None = None) -> None:
            async with self._speaking_arbiter.acquire(
                event.chat_id,
                purpose="user_reply",
                chat_activity_tracker=self._chat_activity_tracker,
            ):
                from src.core.expression_gate import ExpressionGate, get_default_expression_gate, source_from_legacy
                gate = getattr(self._brain, "_expression_gate_ref", None)
                if not isinstance(gate, ExpressionGate):
                    gate = get_default_expression_gate()
                await gate.send(
                    text,
                    source=source_from_legacy(source),
                    chat_id=event.chat_id,
                    send_fn=event.send_fn,
                    trajectory_store=getattr(self._brain, "trajectory_store", None),
                    mutation_log=getattr(self._brain, "_mutation_log_ref", None),
                    adapter=event.adapter,
                    metadata=metadata or {},
                )
            if self._chat_activity_tracker is not None:
                self._chat_activity_tracker.mark_assistant_reply(
                    event.chat_id,
                    source=source,
                )

        return _send

    async def _maybe_record_topic_stop(self, chat_id: str, text: str) -> None:
        try:
            from src.config import get_settings
            if not get_settings().intent_cancellation.enabled:
                return
            from src.core.topic_lineage import is_stop_request_for_weather
            if not is_stop_request_for_weather(text):
                return
            store = getattr(self._brain, "_background_task_store_ref", None)
            if store is None:
                return
            stopped = await store.stop_topic_prefix(
                chat_id=chat_id,
                topic_prefix="weather:",
                reason="user_cancelled_weather_topic",
            )
            mutation_log = getattr(self._brain, "_mutation_log_ref", None)
            if mutation_log is not None:
                from src.logging.state_mutation_log import MutationType
                await mutation_log.record(
                    MutationType.TOPIC_STOPPED,
                    {
                        "chat_id": chat_id,
                        "topic_prefix": "weather:",
                        "stopped": stopped,
                        "reason": "user_cancelled_weather_topic",
                    },
                    chat_id=chat_id,
                )
        except Exception:
            logger.debug("topic stop marker write failed", exc_info=True)

    async def _send_user_visible_status(
        self,
        event: MessageEvent,
        text: str,
        *,
        source: str,
    ) -> bool:
        if event.send_fn is None:
            return False
        from src.core.system_send import send_system_message

        async def _send(text_to_send: str) -> None:
            async with self._speaking_arbiter.acquire(
                event.chat_id,
                purpose="user_reply",
                chat_activity_tracker=self._chat_activity_tracker,
            ):
                await event.send_fn(text_to_send)

        delivered = await send_system_message(
            _send,
            text,
            source=source,
            chat_id=event.chat_id,
            adapter=event.adapter,
            trajectory_store=getattr(self._brain, "trajectory_store", None),
            mutation_log=getattr(self._brain, "_mutation_log_ref", None),
            expression_gate=getattr(self._brain, "_expression_gate_ref", None),
        )
        if delivered and self._chat_activity_tracker is not None:
            self._chat_activity_tracker.mark_assistant_reply(
                event.chat_id,
                source=source,
            )
        return delivered

    async def _maybe_handle_owner_over_owner_interrupt(self) -> None:
        current = self._current_message_event
        if current is None:
            return

        selected: tuple[MessageEvent, str] | None = None

        def _candidate(ev: Event) -> bool:
            nonlocal selected
            if not (
                isinstance(ev, MessageEvent)
                and ev.priority == PRIORITY_OWNER_MESSAGE
                and ev.chat_id == current.chat_id
            ):
                return False
            kind = _classify_owner_over_owner_text(ev.text)
            if kind == "ordinary_followup":
                return False
            selected = (ev, kind)
            return True

        popped = self._queue.pop_matching(_candidate)
        if popped is None or selected is None:
            return
        event, classification = selected
        if self._chat_activity_tracker is not None:
            self._chat_activity_tracker.mark_inbound_user_message(
                event.chat_id,
                user_id=event.user_id,
                message_id=event.source_message_id,
                event_id=event.event_id,
                idempotency_key=event.idempotency_key,
            )
        elapsed = (
            time.monotonic() - self._current_turn_started_mono
            if self._current_turn_started_mono is not None
            else 0.0
        )
        should_cancel = (
            classification == "cancel_or_supersede"
            or elapsed >= self.owner_status_probe_grace_seconds
        )
        logger.info(
            "owner_over_owner_interrupt chat_id=%s classification=%s event_id=%s message_id=%s elapsed_seconds=%.3f cancel=%s",
            event.chat_id,
            classification,
            event.event_id or "",
            event.source_message_id or "",
            elapsed,
            should_cancel,
        )

        if should_cancel:
            reply = self.FOREGROUND_TIMEOUT_REPLY
            delivered = await self._send_user_visible_status(
                event,
                reply,
                source=f"owner_over_owner_{classification}",
            )
            if event.done_future is not None and not event.done_future.done():
                event.done_future.set_result(reply if delivered else "")
            if delivered:
                self._cancel_user_visible_status = reply
            await self._cancel_in_flight(f"owner_over_owner:{classification}")
            return

        reply = "还在处理这次请求，还没结束。我会继续盯着，结束后马上回你。"
        delivered = await self._send_user_visible_status(
            event,
            reply,
            source="owner_over_owner_status_probe",
        )
        if event.done_future is not None and not event.done_future.done():
            event.done_future.set_result(reply if delivered else "")


def _agent_event_to_urgent_item(event: Event) -> dict:
    triggering = getattr(event, "triggering_event", None)
    summary = getattr(triggering, "summary_for_lapwing", None)
    if not summary:
        summary = getattr(event, "summary", "") or getattr(event, "kind", "agent_event")
    return {
        "type": getattr(event, "kind", "agent_event"),
        "content": str(summary),
        "task_id": getattr(event, "task_id", ""),
        "salience": str(getattr(event, "effective_salience", "")),
    }


def _agent_status_text(event: Event) -> str:
    triggering = getattr(event, "triggering_event", None)
    summary = ""
    if triggering is not None:
        summary = str(
            getattr(triggering, "summary_for_owner", None)
            or getattr(triggering, "summary_for_lapwing", "")
            or ""
        ).strip()
    snapshot = getattr(event, "task_snapshot", None)
    if not summary and snapshot is not None:
        summary = str(
            getattr(snapshot, "error_summary", None)
            or getattr(snapshot, "result_summary", None)
            or getattr(snapshot, "last_progress_summary", None)
            or ""
        ).strip()
    task_id = str(getattr(event, "task_id", "") or "").strip()
    # task_id suffix removed — internal IDs should not leak to user-facing text.
    suffix = ""
    kind = str(getattr(event, "kind", "") or "")
    event_type = str(getattr(getattr(triggering, "type", None), "value", "") or "")

    if kind == "agent_needs_input" or event_type == "agent_needs_input":
        payload = getattr(event, "payload", None)
        question = str(
            getattr(payload, "question_for_owner", None)
            or getattr(payload, "question_for_lapwing", None)
            or summary
        ).strip()
        return f"这个后台任务需要你补充一下：{question}{suffix}" if question else ""

    if event_type in {"agent_failed", "agent_budget_exhausted"}:
        detail = summary or "没有拿到可用结果。"
        return f"刚才那个后台任务失败了：{detail}{suffix}"

    if event_type == "agent_cancelled":
        detail = summary or "已取消。"
        return f"后台任务已取消：{detail}{suffix}"

    if event_type == "agent_completed":
        detail = summary or "已经完成。"
        return f"后台任务完成了：{detail}{suffix}"

    if event_type == "agent_progress_summary":
        detail = summary or "有新的进展。"
        return f"后台任务进展：{detail}{suffix}"

    return f"后台任务状态更新：{summary}{suffix}" if summary else ""


def _safe_tail_hash(value: str) -> tuple[str, str]:
    if not value:
        return "", ""
    return value[-8:], hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def _foreground_turn_timeout_seconds() -> int:
    try:
        from src.config import get_settings
        return int(
            get_settings()
            .runtime_interaction_hardening
            .foreground_turn_timeout_seconds
        )
    except Exception:
        return 300


def _owner_status_probe_grace_seconds() -> float:
    try:
        from src.config import get_settings
        return float(
            get_settings()
            .runtime_interaction_hardening
            .owner_status_probe_grace_seconds
        )
    except Exception:
        return 30.0


_OWNER_CANCEL_TOKENS = (
    "先别管",
    "别管",
    "别查",
    "不用查",
    "停一下",
    "停一停",
    "停止",
    "取消",
    "算了",
    "中止",
    "stop",
    "cancel",
)

_OWNER_STATUS_TOKENS = (
    "还在查",
    "还在处理",
    "还在吗",
    "在吗",
    "是不是还在",
    "怎么不回",
    "为什么不回",
    "喂",
    "喂?",
    "喂？",
)


def _classify_owner_over_owner_text(text: str) -> str:
    normalized = " ".join((text or "").strip().lower().split())
    if not normalized:
        return "ordinary_followup"
    if any(token in normalized for token in _OWNER_CANCEL_TOKENS):
        return "cancel_or_supersede"
    if any(token in normalized for token in _OWNER_STATUS_TOKENS):
        return "status_probe"
    return "ordinary_followup"
