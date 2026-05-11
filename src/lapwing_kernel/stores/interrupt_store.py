"""InterruptStore — persistent state machine for owner-attention interrupts.

State machine (blueprint §8.5):
  pending  ──(owner approve)─────→ resolved
           ──(owner deny)────────→ denied
           ──(timeout / expire)──→ expired
           ──(task cancel / lost)→ cancelled

Sync (sqlite3, not aiosqlite) per blueprint §8.2 — low-volume access pattern.

See docs/architecture/lapwing_v1_blueprint.md §8.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from ..primitives.interrupt import Interrupt


SCHEMA_PATH = Path(__file__).parent / "interrupt_store.sql"


def _row_to_interrupt(row: sqlite3.Row | tuple) -> Interrupt:
    """Reconstruct Interrupt from sqlite row. Frozen dataclass needs the
    full constructor (we bypass `Interrupt.new` because the row may carry
    a status that isn't 'pending')."""
    # Column order matches schema above
    (
        id_,
        kind,
        status,
        actor_required,
        resource,
        resource_ref,
        continuation_ref,
        non_resumable_int,
        non_resumable_reason,
        summary,
        payload_redacted_json,
        created_at_str,
        updated_at_str,
        expires_at_str,
        resolved_payload_json,
    ) = row

    return Interrupt(
        id=id_,
        kind=kind,
        status=status,
        actor_required=actor_required,
        resource=resource,
        resource_ref=resource_ref,
        continuation_ref=continuation_ref,
        non_resumable=bool(non_resumable_int),
        non_resumable_reason=non_resumable_reason,
        summary=summary or "",
        payload_redacted=json.loads(payload_redacted_json) if payload_redacted_json else {},
        created_at=datetime.fromisoformat(created_at_str),
        updated_at=datetime.fromisoformat(updated_at_str),
        expires_at=datetime.fromisoformat(expires_at_str) if expires_at_str else None,
        resolved_payload=(
            json.loads(resolved_payload_json) if resolved_payload_json else None
        ),
    )


class InterruptStore:
    """Sync sqlite-backed Interrupt persistence.

    Implements the InterruptStoreProtocol consumed by ActionExecutor:
      persist(interrupt) / get(id) / resolve(id, payload) / cancel(id, reason)
    Plus admin helpers:
      list_pending(actor=None) / deny(id, reason) / expire_overdue() /
      list_expired_continuations()
    """

    def __init__(self, db_path: Path | str):
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with sqlite3.connect(self._path) as conn:
            conn.executescript(SCHEMA_PATH.read_text())

    def persist(self, interrupt: Interrupt) -> None:
        with sqlite3.connect(self._path) as conn:
            conn.execute(
                """
                INSERT INTO interrupts
                (id, kind, status, actor_required, resource, resource_ref,
                 continuation_ref, non_resumable, non_resumable_reason, summary,
                 payload_redacted_json, created_at, updated_at, expires_at,
                 resolved_payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    interrupt.id,
                    interrupt.kind,
                    interrupt.status,
                    interrupt.actor_required,
                    interrupt.resource,
                    interrupt.resource_ref,
                    interrupt.continuation_ref,
                    1 if interrupt.non_resumable else 0,
                    interrupt.non_resumable_reason,
                    interrupt.summary,
                    json.dumps(interrupt.payload_redacted, ensure_ascii=False),
                    interrupt.created_at.isoformat(),
                    interrupt.updated_at.isoformat(),
                    interrupt.expires_at.isoformat() if interrupt.expires_at else None,
                    json.dumps(interrupt.resolved_payload, ensure_ascii=False)
                    if interrupt.resolved_payload is not None
                    else None,
                ),
            )

    def get(self, interrupt_id: str) -> Interrupt | None:
        with sqlite3.connect(self._path) as conn:
            row = conn.execute(
                "SELECT id, kind, status, actor_required, resource, resource_ref, "
                "continuation_ref, non_resumable, non_resumable_reason, summary, "
                "payload_redacted_json, created_at, updated_at, expires_at, "
                "resolved_payload_json "
                "FROM interrupts WHERE id = ?",
                (interrupt_id,),
            ).fetchone()
        return _row_to_interrupt(row) if row else None

    def list_pending(self, *, actor: str | None = None) -> list[Interrupt]:
        with sqlite3.connect(self._path) as conn:
            base_select = (
                "SELECT id, kind, status, actor_required, resource, resource_ref, "
                "continuation_ref, non_resumable, non_resumable_reason, summary, "
                "payload_redacted_json, created_at, updated_at, expires_at, "
                "resolved_payload_json FROM interrupts "
            )
            if actor:
                rows = conn.execute(
                    base_select
                    + "WHERE status = 'pending' AND actor_required = ? "
                    "ORDER BY created_at DESC",
                    (actor,),
                ).fetchall()
            else:
                rows = conn.execute(
                    base_select + "WHERE status = 'pending' ORDER BY created_at DESC"
                ).fetchall()
        return [_row_to_interrupt(r) for r in rows]

    def resolve(self, interrupt_id: str, owner_payload: dict[str, Any]) -> None:
        with sqlite3.connect(self._path) as conn:
            conn.execute(
                """
                UPDATE interrupts
                SET status = 'resolved',
                    updated_at = ?,
                    resolved_payload_json = ?
                WHERE id = ? AND status = 'pending'
                """,
                (
                    datetime.utcnow().isoformat(),
                    json.dumps(owner_payload, ensure_ascii=False),
                    interrupt_id,
                ),
            )

    def deny(self, interrupt_id: str, reason: str) -> None:
        with sqlite3.connect(self._path) as conn:
            conn.execute(
                """
                UPDATE interrupts SET status = 'denied', updated_at = ?,
                    resolved_payload_json = ?
                WHERE id = ? AND status = 'pending'
                """,
                (
                    datetime.utcnow().isoformat(),
                    json.dumps({"reason": reason}, ensure_ascii=False),
                    interrupt_id,
                ),
            )

    def cancel(self, interrupt_id: str, *, reason: str) -> None:
        """Mark a pending interrupt as cancelled.

        Used when continuation is lost (kernel restart) — see
        ActionExecutor.resume. Never call from owner-approve path.
        """
        with sqlite3.connect(self._path) as conn:
            conn.execute(
                """
                UPDATE interrupts SET status = 'cancelled', updated_at = ?,
                    resolved_payload_json = ?
                WHERE id = ? AND status = 'pending'
                """,
                (
                    datetime.utcnow().isoformat(),
                    json.dumps({"reason": reason}, ensure_ascii=False),
                    interrupt_id,
                ),
            )

    def expire_overdue(self) -> int:
        """Background task: mark overdue pending interrupts as expired.

        Returns number of rows transitioned to 'expired'.
        """
        now_iso = datetime.utcnow().isoformat()
        with sqlite3.connect(self._path) as conn:
            cur = conn.execute(
                """
                UPDATE interrupts SET status = 'expired', updated_at = ?
                WHERE status = 'pending' AND expires_at IS NOT NULL
                  AND expires_at < ?
                """,
                (now_iso, now_iso),
            )
            return cur.rowcount

    def list_expired_continuations(self) -> list[str]:
        """Return continuation_refs for newly-expired interrupts so the
        expire_overdue_loop can ContinuationRegistry.cancel() them."""
        with sqlite3.connect(self._path) as conn:
            rows = conn.execute(
                "SELECT continuation_ref FROM interrupts "
                "WHERE status = 'expired' AND continuation_ref IS NOT NULL"
            ).fetchall()
        return [r[0] for r in rows if r[0]]
