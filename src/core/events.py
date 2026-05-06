"""Events — every input that reaches MainLoop is one of these.

Blueprint v2.0 Step 4 §M1. The MainLoop consumes a single EventQueue;
adapters, the inner-tick scheduler, and system-level signals all
produce instances of these dataclasses. Concrete handlers branch on
``Event.kind`` rather than on which subsystem called them, so adding a
new producer (a new adapter, a new beat type) does not require touching
the dispatch core.

Ordering rule:
  * ``priority`` (lower number = handled first) is the first key.
  * ``timestamp`` (``time.monotonic`` at construction time) is the
    tiebreaker, so two events at the same priority preserve FIFO order.

The ``__lt__`` implementation makes ``asyncio.PriorityQueue`` happy
without forcing callers to wrap events in tuples.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from src.core.authority_gate import AuthLevel


# ── Priority constants ──────────────────────────────────────────────
# Event-queue priority (NOT auth level). Lower number = handled first.
PRIORITY_OWNER_MESSAGE = 0
PRIORITY_USER_MESSAGE = 1
PRIORITY_INNER_TICK = 2
PRIORITY_TOOL_COMPLETE = 3
PRIORITY_SYSTEM = 4


@dataclass(frozen=True, order=False)
class Event:
    """Base event. Subclasses add their own payload fields.

    ``priority`` and ``timestamp`` together define total ordering. The
    runtime fills ``timestamp`` automatically via ``field(default_factory=...)``
    so producers only need to set ``priority`` + ``kind`` + payload.
    """

    priority: int
    kind: str
    timestamp: float = field(default_factory=time.monotonic)

    def __lt__(self, other: "Event") -> bool:
        # PriorityQueue compares heap entries. We define total order on
        # (priority, timestamp) so equal-priority events stay FIFO.
        if not isinstance(other, Event):
            return NotImplemented
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.timestamp < other.timestamp


@dataclass(frozen=True, order=False)
class MessageEvent(Event):
    """An incoming chat message that needs a reply.

    ``send_fn`` is the adapter callback the brain uses to deliver each
    chunk back to the user — kept on the event so MainLoop does not need
    to know about adapter-specific delivery. ``typing_fn`` and
    ``status_callback`` are optional siblings used by the desktop WS
    channel (typing indicator + execution status updates); QQ producers
    leave them ``None``.

    ``done_future`` is supplied by callers that need to wait for the
    handler's reply (the desktop ``/ws/chat`` route). The handler resolves
    it with the assistant's full reply (or sets an exception). Producers
    that fire-and-forget (QQ private/group) leave it ``None``.

    ``auth_level`` follows ``AuthorityGate`` convention (3=OWNER, 2=TRUSTED,
    1=GUEST, 0=IGNORE). Producers should derive event ``priority`` from
    it: OWNER → ``PRIORITY_OWNER_MESSAGE``, everyone else →
    ``PRIORITY_USER_MESSAGE`` (see ``MessageEvent.from_message`` helper).
    """

    chat_id: str = ""
    user_id: str = ""
    text: str = ""
    images: tuple[Any, ...] = ()
    adapter: str = ""
    send_fn: Callable[..., Awaitable[Any]] | None = None
    typing_fn: Callable[..., Awaitable[Any]] | None = None
    status_callback: Callable[..., Awaitable[Any]] | None = None
    done_future: Any = None  # asyncio.Future[str] | None — kept loose to avoid evaluating the loop at construction
    auth_level: int = int(AuthLevel.IGNORE)
    interaction_mode: str = "normal"
    source_message_id: str | None = None

    @classmethod
    def from_message(
        cls,
        *,
        chat_id: str,
        user_id: str,
        text: str,
        adapter: str,
        send_fn: Callable[..., Awaitable[Any]],
        auth_level: int,
        images: tuple[Any, ...] = (),
        typing_fn: Callable[..., Awaitable[Any]] | None = None,
        status_callback: Callable[..., Awaitable[Any]] | None = None,
        done_future: Any = None,
        interaction_mode: str = "normal",
        source_message_id: str | None = None,
    ) -> "MessageEvent":
        priority = (
            PRIORITY_OWNER_MESSAGE
            if auth_level >= int(AuthLevel.OWNER)
            else PRIORITY_USER_MESSAGE
        )
        return cls(
            priority=priority,
            kind="owner_message" if priority == PRIORITY_OWNER_MESSAGE else "user_message",
            chat_id=chat_id,
            user_id=user_id,
            text=text,
            images=images,
            adapter=adapter,
            send_fn=send_fn,
            typing_fn=typing_fn,
            status_callback=status_callback,
            done_future=done_future,
            auth_level=auth_level,
            interaction_mode=interaction_mode,
            source_message_id=source_message_id,
        )


@dataclass(frozen=True, order=False)
class InnerTickEvent(Event):
    """Self-driven thinking pulse.

    Fired by ``InnerTickScheduler`` when Lapwing should consider taking
    initiative (commitment review, idle reflection, periodic heartbeat).
    """

    scheduled_at: float = 0.0
    reason: str = "periodic"  # "periodic" / "commitment_check" / "idle_threshold"

    @classmethod
    def make(cls, *, reason: str = "periodic") -> "InnerTickEvent":
        return cls(
            priority=PRIORITY_INNER_TICK,
            kind="inner_tick",
            scheduled_at=time.monotonic(),
            reason=reason,
        )


@dataclass(frozen=True, order=False)
class SystemEvent(Event):
    """Out-of-band runtime control: shutdown, model swap, persona reload."""

    action: str = ""
    payload: dict = field(default_factory=dict)

    @classmethod
    def make(cls, *, action: str, payload: dict | None = None) -> "SystemEvent":
        return cls(
            priority=PRIORITY_SYSTEM,
            kind="system",
            action=action,
            payload=payload or {},
        )
