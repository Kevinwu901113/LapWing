"""CredentialUseState — persistent record of approved credential uses.

PolicyDecider consults this on every credential.use action to decide
INTERRUPT (first-use, owner must approve) vs ALLOW (already approved).

State, NOT config (blueprint §7.4 — GPT non-blocking B). First-use approval
must survive process restart so that Kevin doesn't keep re-approving the
same credential after every kernel reboot.

See docs/architecture/lapwing_v1_blueprint.md §7.4.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


SCHEMA_PATH = Path(__file__).parent / "credential_use_state.sql"


class CredentialUseState:
    """sqlite-backed approval ledger. Append-only (no revoke in v1).

    Implements the CredentialUseStateProtocol consumed by PolicyDecider:
      has_been_used(service: str) -> bool
    Plus admin helper:
      mark_used(service, by='owner')
    """

    def __init__(self, db_path: Path | str):
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with sqlite3.connect(self._path) as conn:
            conn.executescript(SCHEMA_PATH.read_text())

    def has_been_used(self, service: str) -> bool:
        with sqlite3.connect(self._path) as conn:
            row = conn.execute(
                "SELECT 1 FROM credential_use_approvals WHERE service = ?",
                (service,),
            ).fetchone()
        return row is not None

    def mark_used(self, service: str, *, by: str = "owner") -> None:
        """Record that `service` has been approved for use. Idempotent."""
        with sqlite3.connect(self._path) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO credential_use_approvals
                (service, approved_at, approved_by)
                VALUES (?, ?, ?)
                """,
                (service, datetime.utcnow().isoformat(), by),
            )

    def list_approved(self) -> list[str]:
        """Admin/diagnostic — list services Kevin has approved."""
        with sqlite3.connect(self._path) as conn:
            rows = conn.execute(
                "SELECT service FROM credential_use_approvals ORDER BY approved_at DESC"
            ).fetchall()
        return [r[0] for r in rows]
