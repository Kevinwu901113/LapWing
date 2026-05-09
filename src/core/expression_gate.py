"""Single choke point for user-visible outbound text."""

from __future__ import annotations

import hashlib
import logging
import re
import time
from enum import Enum
from typing import Any, Awaitable, Callable

from src.logging.state_mutation_log import MutationType, current_iteration_id

logger = logging.getLogger("lapwing.core.expression_gate")

SendFn = Callable[[str], Awaitable[Any]]


class OutboundSource(str, Enum):
    DIRECT_REPLY = "direct_reply"
    FRAMEWORK_FALLBACK = "framework_fallback"
    BACKGROUND_COMPLETION = "background_completion"
    BACKGROUND_FAILURE = "background_failure"
    PROACTIVE = "proactive"
    REMINDER = "reminder"
    CONFIRMATION = "confirmation"
    SUB_AGENT = "sub_agent"
    AGENT_NEEDS_INPUT = "agent_needs_input"
    INTERNAL_STATE = "internal_state"
    TOOL_INFRA_FAILURE = "tool_infra_failure"
    DEBUG = "debug"


USER_VISIBLE_CAPABLE = {
    OutboundSource.DIRECT_REPLY,
    OutboundSource.FRAMEWORK_FALLBACK,
    OutboundSource.BACKGROUND_COMPLETION,
    OutboundSource.BACKGROUND_FAILURE,
    OutboundSource.PROACTIVE,
    OutboundSource.REMINDER,
    OutboundSource.CONFIRMATION,
}

INTERNAL_ONLY = set(OutboundSource) - USER_VISIBLE_CAPABLE

_INTERNAL_TOKEN_RE = re.compile(
    r"\b(?:AGENT[_\s-]?NEEDS[_\s-]?INPUT|AGENTNEEDSINPUT|WAITING_INPUT|checkpoint_id)\b",
    re.IGNORECASE,
)


def source_from_legacy(source: str | OutboundSource) -> OutboundSource:
    if isinstance(source, OutboundSource):
        return source
    value = (source or "").strip()
    if not value:
        return OutboundSource.FRAMEWORK_FALLBACK
    lowered = value.lower()
    aliases = {
        "confirmation": OutboundSource.CONFIRMATION,
        "direct_reply": OutboundSource.DIRECT_REPLY,
        "framework_fallback": OutboundSource.FRAMEWORK_FALLBACK,
        "llm_error": OutboundSource.FRAMEWORK_FALLBACK,
        "foreground_exception": OutboundSource.FRAMEWORK_FALLBACK,
        "foreground_timeout": OutboundSource.FRAMEWORK_FALLBACK,
        "reminder_notify": OutboundSource.REMINDER,
        "reminder_agent_result": OutboundSource.REMINDER,
        "reminder_agent_fallback": OutboundSource.REMINDER,
        "send_message": OutboundSource.PROACTIVE,
        "proactive": OutboundSource.PROACTIVE,
    }
    if lowered in aliases:
        return aliases[lowered]
    if lowered.startswith("agent_task_result"):
        return OutboundSource.BACKGROUND_FAILURE if "failed" in lowered else OutboundSource.BACKGROUND_COMPLETION
    if lowered.startswith("owner_over_owner"):
        return OutboundSource.FRAMEWORK_FALLBACK
    return OutboundSource.FRAMEWORK_FALLBACK


class ExpressionGate:
    def __init__(self, *, now_fn=time.monotonic) -> None:
        self._now = now_fn
        self._dedup: dict[tuple[str, str, str, str, str], float] = {}
        self.fail_open_count = 0

    async def send(
        self,
        text: str,
        *,
        source: str | OutboundSource,
        chat_id: str | None,
        send_fn: SendFn,
        trajectory_store: Any = None,
        mutation_log: Any = None,
        adapter: str | None = None,
        focus_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        metadata = dict(metadata or {})
        src = source_from_legacy(source)
        flags = _settings_flags()
        if not flags["enabled"] or (
            src == OutboundSource.DIRECT_REPLY and not flags["direct_reply_through_gate"]
        ):
            return await self._legacy_send_and_log(
                text,
                src,
                chat_id=chat_id,
                send_fn=send_fn,
                trajectory_store=trajectory_store,
                mutation_log=mutation_log,
                adapter=adapter,
                focus_id=focus_id,
                metadata=metadata,
            )

        if src == OutboundSource.DIRECT_REPLY:
            delivered = False
            try:
                await send_fn(text)
                delivered = True
            except Exception:
                logger.warning("direct_reply delivery failed", exc_info=True)
                return False
            try:
                await self._record_delivered(
                    text,
                    src,
                    chat_id=chat_id,
                    trajectory_store=trajectory_store,
                    mutation_log=mutation_log,
                    adapter=adapter,
                    focus_id=focus_id,
                    metadata=metadata,
                    delivered=True,
                )
                return True
            except Exception as exc:
                if not flags["fail_open_direct_reply"]:
                    raise
                self.fail_open_count += 1
                await self._audit(
                    mutation_log,
                    MutationType.EXPRESSION_GATE_FAIL_OPEN,
                    {
                        "source": src.value,
                        "chat_id": chat_id,
                        "error": type(exc).__name__,
                        "delivered": delivered,
                    },
                    chat_id=chat_id,
                )
                return delivered

        decision = await self._non_direct_decision(
            text,
            src,
            chat_id=chat_id,
            metadata=metadata,
            mutation_log=mutation_log,
        )
        if decision is not None:
            action, reason = decision
            await self._audit(
                mutation_log,
                MutationType.EXPRESSION_GATE_REJECTED
                if action == "reject"
                else MutationType.EXPRESSION_GATE_SUPPRESSED,
                {
                    "source": src.value,
                    "chat_id": chat_id,
                    "reason": reason,
                    "text_hash": _hash_text(text),
                    "metadata": _safe_metadata(metadata),
                },
                chat_id=chat_id,
            )
            return False

        delivered = False
        try:
            await send_fn(text)
            delivered = True
        except Exception:
            logger.warning("expression_gate delivery failed source=%s chat=%s", src.value, chat_id, exc_info=True)
        await self._record_delivered(
            text,
            src,
            chat_id=chat_id,
            trajectory_store=trajectory_store,
            mutation_log=mutation_log,
            adapter=adapter,
            focus_id=focus_id,
            metadata=metadata,
            delivered=delivered,
        )
        return delivered

    async def reject_internal(
        self,
        text: str,
        *,
        source: str | OutboundSource,
        chat_id: str | None = None,
        mutation_log: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        src = source_from_legacy(source)
        await self._audit(
            mutation_log,
            MutationType.EXPRESSION_GATE_REJECTED,
            {
                "source": src.value,
                "chat_id": chat_id,
                "reason": "internal_only_source",
                "text_hash": _hash_text(text),
                "metadata": _safe_metadata(metadata or {}),
            },
            chat_id=chat_id,
        )
        return False

    async def _non_direct_decision(
        self,
        text: str,
        src: OutboundSource,
        *,
        chat_id: str | None,
        metadata: dict[str, Any],
        mutation_log: Any,
    ) -> tuple[str, str] | None:
        if src in INTERNAL_ONLY:
            return "reject", "internal_only_source"
        if _INTERNAL_TOKEN_RE.search(text or ""):
            return "reject", "internal_state_leak"
        if metadata.get("cancelled") is True:
            return "suppress", "cancelled-task-result"
        if metadata.get("stale") is True:
            return "suppress", "stale-generation"
        if _is_cancelled_or_stale_lineage(metadata):
            return "suppress", "cancelled-task-result"
        if metadata.get("infra_failure_class"):
            key = (
                str(chat_id or ""),
                src.value,
                str(metadata.get("infra_failure_class") or ""),
                str(metadata.get("organ") or ""),
                str(metadata.get("topic_key") or ""),
            )
            now = self._now()
            window = float(metadata.get("dedup_window_seconds") or _settings_flags()["dedup_window_seconds"])
            expiry = self._dedup.get(key)
            if expiry is not None and expiry > now:
                return "suppress", "duplicate-infra-failure"
            self._dedup[key] = now + window
        if metadata.get("long_artifact") and metadata.get("user_requested_artifact") is not True:
            return "suppress", "long-artifact"
        return None

    async def _legacy_send_and_log(
        self,
        text: str,
        src: OutboundSource,
        *,
        chat_id: str | None,
        send_fn: SendFn,
        trajectory_store: Any,
        mutation_log: Any,
        adapter: str | None,
        focus_id: str | None,
        metadata: dict[str, Any],
    ) -> bool:
        delivered = False
        try:
            await send_fn(text)
            delivered = True
        except Exception:
            logger.warning("legacy outbound delivery failed source=%s chat=%s", src.value, chat_id, exc_info=True)
        await self._record_delivered(
            text,
            src,
            chat_id=chat_id,
            trajectory_store=trajectory_store,
            mutation_log=mutation_log,
            adapter=adapter,
            focus_id=focus_id,
            metadata=metadata,
            delivered=delivered,
        )
        return delivered

    async def _record_delivered(
        self,
        text: str,
        src: OutboundSource,
        *,
        chat_id: str | None,
        trajectory_store: Any,
        mutation_log: Any,
        adapter: str | None,
        focus_id: str | None,
        metadata: dict[str, Any],
        delivered: bool,
    ) -> int | None:
        trajectory_id: int | None = None
        if trajectory_store is not None:
            try:
                from src.core.trajectory_store import TrajectoryEntryType

                entry_type = (
                    TrajectoryEntryType.PROACTIVE_OUTBOUND
                    if src == OutboundSource.PROACTIVE
                    else TrajectoryEntryType.TELL_USER
                )
                trajectory_id = await trajectory_store.append(
                    entry_type,
                    chat_id,
                    "lapwing" if src in {OutboundSource.DIRECT_REPLY, OutboundSource.PROACTIVE} else "system",
                    {
                        "text": text,
                        "source": src.value,
                        "delivered": delivered,
                        "text_hash": _hash_text(text),
                        "metadata": _safe_metadata(metadata),
                    },
                    related_iteration_id=current_iteration_id(),
                    focus_id=focus_id,
                )
            except Exception:
                logger.warning("expression_gate trajectory append failed", exc_info=True)
        if mutation_log is not None:
            await self._audit(
                mutation_log,
                MutationType.TELL_USER,
                {
                    "text": text,
                    "chat_id": chat_id,
                    "adapter": adapter,
                    "source": src.value,
                    "delivered": delivered,
                    "trajectory_id": trajectory_id,
                    "metadata": _safe_metadata(metadata),
                    "text_hash": _hash_text(text),
                },
                chat_id=chat_id,
            )
        return trajectory_id

    async def _audit(self, mutation_log: Any, event_type: MutationType, payload: dict[str, Any], *, chat_id: str | None) -> None:
        if mutation_log is None:
            return
        try:
            await mutation_log.record(
                event_type,
                payload,
                iteration_id=current_iteration_id(),
                chat_id=chat_id,
            )
        except Exception:
            logger.warning("expression_gate audit failed: %s", event_type.value, exc_info=True)


_DEFAULT_GATE = ExpressionGate()


def get_default_expression_gate() -> ExpressionGate:
    return _DEFAULT_GATE


def _hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _safe_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        if key in {"task_snapshot", "record"}:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
        elif isinstance(value, dict):
            safe[key] = {
                str(k): v
                for k, v in value.items()
                if isinstance(v, (str, int, float, bool)) or v is None
            }
        else:
            safe[key] = str(value)
    return safe


def _is_cancelled_or_stale_lineage(metadata: dict[str, Any]) -> bool:
    stopped = metadata.get("stopped_at_generation")
    generation = metadata.get("generation")
    topic_key = metadata.get("topic_key")
    if stopped is None or generation is None or not topic_key:
        return False
    try:
        return int(generation) <= int(stopped)
    except (TypeError, ValueError):
        return False


def _settings_flags() -> dict[str, Any]:
    try:
        from src.config import get_settings

        settings = get_settings()
        expression = settings.expression_gate
        return {
            "enabled": bool(expression.enabled),
            "direct_reply_through_gate": bool(expression.direct_reply_through_gate),
            "fail_open_direct_reply": bool(expression.fail_open_direct_reply),
            "dedup_window_seconds": int(expression.duplicate_infra_failure_window_seconds),
        }
    except Exception:
        return {
            "enabled": True,
            "direct_reply_through_gate": True,
            "fail_open_direct_reply": True,
            "dedup_window_seconds": 300,
        }
