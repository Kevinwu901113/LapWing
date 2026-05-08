"""Inbound interaction gates before EventQueue.

The runtime path is intentionally boring and explicit:

    ChannelAdapter.normalize_inbound()
      -> InboundMessageGate
      -> CommandInterceptLayer
      -> BusySessionController
      -> EventQueue
      -> MainLoop
      -> Brain

None of these classes call Brain directly. They classify, reject, defer, or
shape events so MainLoop remains the sole consumer.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Literal

from src.adapters.base import NormalizedInboundMessage
from src.core.authority_gate import AuthLevel


class BusyInputMode(str, Enum):
    NORMAL = "normal"
    INTERRUPT = "interrupt"
    QUEUE = "queue"
    STEER = "steer"
    APPROVAL = "approval"
    COMMAND = "command"
    REJECT = "reject"
    DEFER = "defer"


@dataclass(frozen=True)
class InboundGateDecision:
    accepted: bool
    message: NormalizedInboundMessage | None
    reason: str = ""
    mode: BusyInputMode = BusyInputMode.NORMAL


@dataclass(frozen=True)
class CommandInterceptResult:
    mode: BusyInputMode
    command: str | None = None
    args: str = ""
    reason: str = ""


@dataclass(frozen=True)
class BusyDecision:
    mode: BusyInputMode
    message: NormalizedInboundMessage
    queued: bool = False
    reason: str = ""


@dataclass(frozen=True)
class QueuedInput:
    message: NormalizedInboundMessage
    mode: BusyInputMode
    created_at: datetime
    expires_at: datetime


class InboundMessageGate:
    """Trust/channel policy gate for normalized inbound messages."""

    def __init__(
        self,
        *,
        allow_private: bool = True,
        allow_group: bool = True,
        allow_untrusted: bool = False,
    ) -> None:
        self.allow_private = allow_private
        self.allow_group = allow_group
        self.allow_untrusted = allow_untrusted

    def evaluate(
        self,
        message: NormalizedInboundMessage | None,
        *,
        auth_level: int,
    ) -> InboundGateDecision:
        if message is None:
            return InboundGateDecision(False, None, "empty_or_unhandled_message", BusyInputMode.REJECT)
        if message.message_type == "private" and not self.allow_private:
            return InboundGateDecision(False, message, "private_messages_disabled", BusyInputMode.REJECT)
        if message.message_type == "group" and not self.allow_group:
            return InboundGateDecision(False, message, "group_messages_disabled", BusyInputMode.REJECT)
        if auth_level <= int(AuthLevel.IGNORE):
            return InboundGateDecision(False, message, "ignored_identity", BusyInputMode.REJECT)
        if auth_level < int(AuthLevel.TRUSTED) and not self.allow_untrusted:
            return InboundGateDecision(False, message, "untrusted_identity", BusyInputMode.DEFER)
        return InboundGateDecision(True, message)


class CommandInterceptLayer:
    """Intercept commands and approvals before normal chat routing."""

    _approval_re = re.compile(r"^\s*(approve|approved|yes|y|allow|允许|同意|批准)\s*$", re.I)

    def intercept(self, message: NormalizedInboundMessage) -> CommandInterceptResult:
        text = (message.text or "").strip()
        if not text:
            return CommandInterceptResult(BusyInputMode.NORMAL)
        if self._approval_re.match(text):
            return CommandInterceptResult(BusyInputMode.APPROVAL, reason="approval_reply")
        if text.lower().startswith("/steer"):
            return CommandInterceptResult(BusyInputMode.NORMAL)
        if text.startswith("/"):
            command, _, args = text[1:].partition(" ")
            return CommandInterceptResult(
                BusyInputMode.COMMAND,
                command=command.strip().lower(),
                args=args.strip(),
                reason="slash_command",
            )
        return CommandInterceptResult(BusyInputMode.NORMAL)


class BusySessionController:
    """Classify busy-time input.

    MainLoop/EventQueue remain the only runtime handoff. The private queue
    helpers are kept for explicit tests/tools, but ``classify`` no longer hides
    accepted chat input there.
    """

    _interrupt_tokens = ("stop", "cancel", "interrupt", "停止", "取消", "中止", "别继续")
    _steer_prefixes = ("/steer", "steer:", "纠正：", "纠正:", "补充约束：", "补充约束:")

    def __init__(
        self,
        *,
        queue_max_per_chat: int = 20,
        queue_ttl: timedelta = timedelta(minutes=30),
        dedupe_window_seconds: float = 3.0,
    ) -> None:
        self.queue_max_per_chat = queue_max_per_chat
        self.queue_ttl = queue_ttl
        self.dedupe_window_seconds = dedupe_window_seconds
        self._queues: dict[str, list[QueuedInput]] = {}
        self._recent: dict[str, tuple[str, float]] = {}
        self.interrupt_requests: list[dict[str, str]] = []

    def classify(
        self,
        message: NormalizedInboundMessage,
        *,
        session_state: Literal["idle", "thinking", "running", "awaiting_approval"] = "idle",
        intercept: CommandInterceptResult | None = None,
    ) -> BusyDecision:
        if intercept is not None and intercept.mode in (BusyInputMode.COMMAND, BusyInputMode.APPROVAL):
            return BusyDecision(intercept.mode, message, reason=intercept.reason)

        text = (message.text or "").strip()
        lowered = text.lower()
        if session_state == "awaiting_approval":
            return BusyDecision(BusyInputMode.APPROVAL, message, reason="awaiting_approval")
        if any(lowered.startswith(t) for t in self._interrupt_tokens):
            self.interrupt_requests.append({
                "chat_id": message.chat_id,
                "message_id": message.message_id,
                "reason": "explicit_interrupt",
            })
            return BusyDecision(BusyInputMode.INTERRUPT, message, reason="explicit_interrupt")
        if self._looks_like_steer(text):
            return BusyDecision(BusyInputMode.STEER, message, reason="explicit_steer")
        if session_state in ("thinking", "running"):
            return BusyDecision(BusyInputMode.QUEUE, message, queued=False, reason="session_busy")
        return BusyDecision(BusyInputMode.NORMAL, message)

    def enqueue(self, message: NormalizedInboundMessage, *, mode: BusyInputMode) -> bool:
        self.prune_expired(message.chat_id)
        if self._is_duplicate(message):
            return False
        queue = self._queues.setdefault(message.chat_id, [])
        if len(queue) >= self.queue_max_per_chat:
            queue.pop(0)
        now = datetime.now(timezone.utc)
        queue.append(QueuedInput(
            message=message,
            mode=mode,
            created_at=now,
            expires_at=now + self.queue_ttl,
        ))
        self._recent[self._dedupe_key(message)] = (self._normalized_text(message.text), time.time())
        return True

    def pop_next(self, chat_id: str) -> QueuedInput | None:
        self.prune_expired(chat_id)
        queue = self._queues.get(chat_id) or []
        if not queue:
            return None
        return queue.pop(0)

    def queue_for(self, chat_id: str) -> tuple[QueuedInput, ...]:
        self.prune_expired(chat_id)
        return tuple(self._queues.get(chat_id) or [])

    def prune_expired(self, chat_id: str) -> int:
        now = datetime.now(timezone.utc)
        queue = self._queues.get(chat_id) or []
        kept = [item for item in queue if item.expires_at > now]
        removed = len(queue) - len(kept)
        if kept:
            self._queues[chat_id] = kept
        else:
            self._queues.pop(chat_id, None)
        return removed

    def steering_event_from_message(
        self,
        message: NormalizedInboundMessage,
        *,
        task_id: str | None = None,
        priority: Literal["low", "normal", "high"] = "normal",
        ttl: timedelta | None = None,
        source_trust_level: str | None = None,
    ):
        from src.core.steering import SteeringEvent

        created_at = datetime.now(timezone.utc)
        content = _strip_steer_marker(message.text)
        return SteeringEvent(
            id=f"steer_{uuid.uuid4().hex}",
            task_id=task_id,
            source_message_id=message.message_id,
            content=content,
            created_at=created_at,
            expires_at=created_at + (ttl or self.queue_ttl),
            acknowledged_at=None,
            priority=priority,
            reason="explicit_steer",
            source_channel=message.channel,
            source_trust_level=source_trust_level,
            chat_id=message.chat_id,
        )

    def _looks_like_steer(self, text: str) -> bool:
        lowered = text.lower()
        return any(lowered.startswith(prefix.lower()) for prefix in self._steer_prefixes)

    def _is_duplicate(self, message: NormalizedInboundMessage) -> bool:
        key = self._dedupe_key(message)
        normalized = self._normalized_text(message.text)
        existing = self._recent.get(key)
        now = time.time()
        if existing is not None and now - existing[1] <= self.dedupe_window_seconds:
            return True
        for recent_text, seen_at in self._recent.values():
            if recent_text == normalized and now - seen_at <= self.dedupe_window_seconds:
                return True
        return False

    @staticmethod
    def _dedupe_key(message: NormalizedInboundMessage) -> str:
        return f"{message.channel}:{message.chat_id}:{message.message_id}"

    @staticmethod
    def _normalized_text(text: str) -> str:
        return " ".join((text or "").strip().lower().split())


def _strip_steer_marker(text: str) -> str:
    stripped = (text or "").strip()
    for marker in BusySessionController._steer_prefixes:
        if stripped.lower().startswith(marker.lower()):
            return stripped[len(marker):].strip()
    return stripped
