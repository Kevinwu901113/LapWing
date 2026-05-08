"""Per-chat runtime activity used by hard outbound-safety gates."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ForegroundTurnState:
    turn_id: str
    chat_id: str
    user_id: str
    event_id: str | None
    source_message_id: str | None
    started_at: datetime
    text_preview: str = ""


@dataclass(frozen=True)
class ChatActivitySnapshot:
    chat_id: str
    latest_user_message_at: datetime | None = None
    latest_assistant_reply_at: datetime | None = None
    active_turn: ForegroundTurnState | None = None
    last_terminal_status: str | None = None
    last_terminal_at: datetime | None = None

    @property
    def has_unanswered_user_message(self) -> bool:
        if self.latest_user_message_at is None:
            return False
        if self.latest_assistant_reply_at is None:
            return True
        return self.latest_user_message_at > self.latest_assistant_reply_at


@dataclass
class _ChatActivityState:
    latest_user_message_at: datetime | None = None
    latest_assistant_reply_at: datetime | None = None
    active_turn: ForegroundTurnState | None = None
    last_terminal_status: str | None = None
    last_terminal_at: datetime | None = None


class ChatActivityTracker:
    """In-memory truth for unresolved user-visible activity.

    Durable trajectory is still the audit record. This tracker is the live
    guardrail used by proactive and speaking policy checks, including the
    window before MainLoop has had a chance to persist an inbound message.
    """

    def __init__(self, *, clock=None) -> None:
        self._clock = clock or _utcnow
        self._lock = threading.RLock()
        self._states: dict[str, _ChatActivityState] = {}

    def mark_inbound_user_message(
        self,
        chat_id: str,
        *,
        user_id: str = "",
        message_id: str | None = None,
        event_id: str | None = None,
        idempotency_key: str | None = None,
        at: datetime | None = None,
    ) -> None:
        if not chat_id:
            return
        when = at or self._clock()
        with self._lock:
            state = self._states.setdefault(chat_id, _ChatActivityState())
            if state.latest_user_message_at is None or when >= state.latest_user_message_at:
                state.latest_user_message_at = when

    def mark_assistant_reply(
        self,
        chat_id: str,
        *,
        at: datetime | None = None,
        source: str = "",
        delivered: bool = True,
    ) -> None:
        if not chat_id or not delivered:
            return
        when = at or self._clock()
        with self._lock:
            state = self._states.setdefault(chat_id, _ChatActivityState())
            if state.latest_assistant_reply_at is None or when >= state.latest_assistant_reply_at:
                state.latest_assistant_reply_at = when

    def mark_turn_started(
        self,
        chat_id: str,
        *,
        turn_id: str,
        user_id: str = "",
        event_id: str | None = None,
        source_message_id: str | None = None,
        text_preview: str = "",
        at: datetime | None = None,
    ) -> None:
        if not chat_id:
            return
        when = at or self._clock()
        with self._lock:
            state = self._states.setdefault(chat_id, _ChatActivityState())
            state.active_turn = ForegroundTurnState(
                turn_id=turn_id,
                chat_id=chat_id,
                user_id=user_id,
                event_id=event_id,
                source_message_id=source_message_id,
                started_at=when,
                text_preview=text_preview[:200],
            )

    def mark_turn_terminal(
        self,
        chat_id: str,
        *,
        turn_id: str | None,
        status: str,
        at: datetime | None = None,
    ) -> None:
        if not chat_id:
            return
        when = at or self._clock()
        with self._lock:
            state = self._states.setdefault(chat_id, _ChatActivityState())
            if (
                state.active_turn is not None
                and (turn_id is None or state.active_turn.turn_id == turn_id)
            ):
                state.active_turn = None
            state.last_terminal_status = status
            state.last_terminal_at = when

    def snapshot(self, chat_id: str) -> ChatActivitySnapshot:
        with self._lock:
            state = self._states.get(chat_id)
            if state is None:
                return ChatActivitySnapshot(chat_id=chat_id)
            return ChatActivitySnapshot(
                chat_id=chat_id,
                latest_user_message_at=state.latest_user_message_at,
                latest_assistant_reply_at=state.latest_assistant_reply_at,
                active_turn=state.active_turn,
                last_terminal_status=state.last_terminal_status,
                last_terminal_at=state.last_terminal_at,
            )

    def has_active_user_turn(self, chat_id: str) -> bool:
        return self.snapshot(chat_id).active_turn is not None

    def has_unanswered_user_message(self, chat_id: str) -> bool:
        return self.snapshot(chat_id).has_unanswered_user_message

    def has_stuck_user_turn(self, chat_id: str, *, timeout_seconds: float) -> bool:
        active = self.snapshot(chat_id).active_turn
        if active is None:
            return False
        return (self._clock() - active.started_at).total_seconds() > timeout_seconds
