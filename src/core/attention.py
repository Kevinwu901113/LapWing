"""AttentionManager — in-memory subject-focus state with event-sourced recovery.

Blueprint v2.0 Step 2 §4. Attention is high-frequency read/write runtime
state ("who am I talking to right now?", "am I in the middle of doing
something?"), so it is NOT backed by its own SQLite table. Persistence
comes from ``mutation_log`` — every ``update()`` records an
``ATTENTION_CHANGED`` event, and ``initialize()`` replays the latest one
at process start. The in-memory value is the single source of truth at
runtime; queryable history lives in mutation_log.

StateSerializer (Step 3) calls ``get()`` synchronously on every prompt
render, so ``get()`` must not await.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, replace
from typing import Any

from src.logging.state_mutation_log import (
    MutationType,
    StateMutationLog,
    current_iteration_id,
)

logger = logging.getLogger("lapwing.core.attention")


# Sentinel distinguishing "no argument passed" from "explicitly set to None"
# (Python's normal default-value idiom cannot express that).
class _Unset:
    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


UNSET: Any = _Unset()


# Attention modes. "conversing" = actively in a user-facing exchange.
# "acting" = executing something (tool loop, background task). "idle" = waiting.
_VALID_MODES: frozenset[str] = frozenset({"conversing", "acting", "idle"})


@dataclass(frozen=True)
class AttentionState:
    current_conversation: str | None
    current_action: str | None
    last_interaction_at: float
    last_action_at: float
    mode: str
    # Step 4 M6: a "session" is the continuous attention window from the
    # moment Lapwing pivots from idle into conversation until silence
    # ends it. Stored on AttentionState because the boundary IS the
    # attention transition — there's no separate "sessions" subsystem
    # any more (the SQL table was dropped in Step 2j).
    session_started_at: float | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "current_conversation": self.current_conversation,
            "current_action": self.current_action,
            "last_interaction_at": self.last_interaction_at,
            "last_action_at": self.last_action_at,
            "mode": self.mode,
            "session_started_at": self.session_started_at,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "AttentionState":
        ssa = payload.get("session_started_at")
        return cls(
            current_conversation=payload.get("current_conversation"),
            current_action=payload.get("current_action"),
            last_interaction_at=float(payload.get("last_interaction_at", 0.0)),
            last_action_at=float(payload.get("last_action_at", 0.0)),
            mode=str(payload.get("mode", "idle")),
            session_started_at=float(ssa) if ssa is not None else None,
        )


class AttentionManager:
    """Owns the live ``AttentionState`` singleton.

    Use via ``await initialize()`` then synchronous ``get()`` + async
    ``update()``. Multiple concurrent ``update()`` calls are serialized
    via an internal lock; reads never block writes (Python GIL guarantees
    dataclass atomicity since ``AttentionState`` is frozen — the state
    attribute is swapped wholesale).
    """

    def __init__(self, mutation_log: StateMutationLog) -> None:
        self._mutation_log = mutation_log
        self._state: AttentionState = self._default_state()
        self._lock = asyncio.Lock()
        self._initialized = False

    @staticmethod
    def _default_state() -> AttentionState:
        now = time.time()
        return AttentionState(
            current_conversation=None,
            current_action=None,
            last_interaction_at=now,
            last_action_at=now,
            mode="idle",
            session_started_at=None,
        )

    async def initialize(self) -> None:
        """Restore the most recent state from mutation_log.

        Idempotent: calling twice is a no-op after the first. If no prior
        ATTENTION_CHANGED events exist, keeps the default (idle) state.
        """
        if self._initialized:
            return
        muts = await self._mutation_log.query_by_type(
            MutationType.ATTENTION_CHANGED, limit=1
        )
        if muts:
            latest = muts[0]
            new_state_payload = latest.payload.get("new") or latest.payload
            try:
                self._state = AttentionState.from_payload(new_state_payload)
            except (TypeError, ValueError):
                logger.warning(
                    "could not decode last ATTENTION_CHANGED payload %r; "
                    "keeping default state", new_state_payload,
                )
        self._initialized = True

    def get(self) -> AttentionState:
        """Synchronous snapshot of current attention."""
        return self._state

    async def update(
        self,
        *,
        current_conversation: Any = UNSET,
        current_action: Any = UNSET,
        mode: Any = UNSET,
    ) -> AttentionState:
        """Atomic partial update. Only fields passed explicitly change.

        ``last_interaction_at`` auto-stamps when ``current_conversation`` is
        provided; ``last_action_at`` auto-stamps when ``current_action`` is
        provided. Emits ``ATTENTION_CHANGED`` with both old and new state,
        plus the list of changed field names.

        Passing no fields is a no-op (does not emit).
        """
        if mode is not UNSET and mode not in _VALID_MODES:
            raise ValueError(
                f"mode must be one of {sorted(_VALID_MODES)}, got {mode!r}"
            )

        async with self._lock:
            now = time.time()
            old = self._state
            changes: dict[str, Any] = {}
            changed_fields: list[str] = []

            if current_conversation is not UNSET:
                changes["current_conversation"] = current_conversation
                changes["last_interaction_at"] = now
                if old.current_conversation != current_conversation:
                    changed_fields.append("current_conversation")
                changed_fields.append("last_interaction_at")
            if current_action is not UNSET:
                changes["current_action"] = current_action
                changes["last_action_at"] = now
                if old.current_action != current_action:
                    changed_fields.append("current_action")
                changed_fields.append("last_action_at")
            if mode is not UNSET:
                changes["mode"] = mode
                if old.mode != mode:
                    changed_fields.append("mode")

            if not changes:
                return old

            # Step 4 M6: implicit session-start when attention pivots from
            # idle → conversing. The session ends only via end_session()
            # (so silence-detection logic stays in one place).
            new_mode = changes.get("mode", old.mode)
            if (
                old.mode == "idle"
                and new_mode == "conversing"
                and old.session_started_at is None
            ):
                changes["session_started_at"] = now
                changed_fields.append("session_started_at")

            new = replace(old, **changes)
            self._state = new

            try:
                await self._mutation_log.record(
                    MutationType.ATTENTION_CHANGED,
                    {
                        "old": old.to_payload(),
                        "new": new.to_payload(),
                        "changed_fields": changed_fields,
                    },
                    iteration_id=current_iteration_id(),
                    chat_id=new.current_conversation,
                )
            except Exception:
                logger.warning(
                    "ATTENTION_CHANGED mutation emit failed", exc_info=True
                )

            return new

    # ── Session window (Step 4 M6) ──────────────────────────────────

    @property
    def current_session_start(self) -> float | None:
        """Unix timestamp when the current session began, or None if idle.

        Synchronous projection of ``self._state.session_started_at`` —
        StateSerializer reads this on every prompt render.
        """
        return self._state.session_started_at

    def is_in_session(self) -> bool:
        return self._state.session_started_at is not None

    async def end_session(self) -> AttentionState:
        """Close the current session window.

        Sets ``session_started_at = None`` and ``mode = "idle"`` in one
        atomic update, emitting an ATTENTION_CHANGED mutation that
        carries the session boundary.
        """
        async with self._lock:
            old = self._state
            if old.session_started_at is None and old.mode == "idle":
                return old
            now = time.time()
            new = replace(
                old,
                session_started_at=None,
                mode="idle",
                last_interaction_at=now,
            )
            self._state = new
            try:
                await self._mutation_log.record(
                    MutationType.ATTENTION_CHANGED,
                    {
                        "old": old.to_payload(),
                        "new": new.to_payload(),
                        "changed_fields": ["session_started_at", "mode", "last_interaction_at"],
                        "session_boundary": "ended",
                    },
                    iteration_id=current_iteration_id(),
                    chat_id=new.current_conversation,
                )
            except Exception:
                logger.warning(
                    "session-end ATTENTION_CHANGED mutation emit failed",
                    exc_info=True,
                )
            return new
