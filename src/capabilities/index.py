"""SQLite-backed capability index for fast lookup.

The index is a derived cache — the filesystem (CapabilityStore) is the
source of truth.  Use ``rebuild_from_store()`` for full consistency
recovery.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from .schema import CapabilityManifest, CapabilityScope, CapabilityStatus

if TYPE_CHECKING:
    from .document import CapabilityDocument
    from .store import CapabilityStore

_SQL_CREATE = """
CREATE TABLE IF NOT EXISTS capability_index (
    id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL,
    scope TEXT NOT NULL,
    maturity TEXT NOT NULL,
    status TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    trust_required TEXT NOT NULL DEFAULT 'developer',
    required_tools_json TEXT NOT NULL DEFAULT '[]',
    required_permissions_json TEXT NOT NULL DEFAULT '[]',
    triggers_json TEXT NOT NULL DEFAULT '[]',
    tags_json TEXT NOT NULL DEFAULT '[]',
    do_not_apply_when_json TEXT NOT NULL DEFAULT '[]',
    sensitive_contexts_json TEXT NOT NULL DEFAULT '[]',
    path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    usage_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    last_used_at TEXT NULL,
    last_tested_at TEXT NULL,
    PRIMARY KEY (id, scope)
);

CREATE INDEX IF NOT EXISTS idx_cap_type ON capability_index(type);
CREATE INDEX IF NOT EXISTS idx_cap_scope ON capability_index(scope);
CREATE INDEX IF NOT EXISTS idx_cap_status ON capability_index(status);
CREATE INDEX IF NOT EXISTS idx_cap_maturity ON capability_index(maturity);
CREATE INDEX IF NOT EXISTS idx_cap_risk ON capability_index(risk_level);
CREATE INDEX IF NOT EXISTS idx_cap_scope_status ON capability_index(scope, status);
"""

SCOPE_PRECEDENCE: list[CapabilityScope] = [
    CapabilityScope.SESSION,
    CapabilityScope.WORKSPACE,
    CapabilityScope.USER,
    CapabilityScope.GLOBAL,
]


def _manifest_to_row(doc: "CapabilityDocument") -> dict:
    m = doc.manifest
    return {
        "id": m.id,
        "name": m.name,
        "description": m.description,
        "type": m.type.value,
        "scope": m.scope.value,
        "maturity": m.maturity.value,
        "status": m.status.value,
        "risk_level": m.risk_level.value,
        "trust_required": m.trust_required,
        "required_tools_json": json.dumps(m.required_tools, ensure_ascii=False),
        "required_permissions_json": json.dumps(m.required_permissions, ensure_ascii=False),
        "triggers_json": json.dumps(m.triggers, ensure_ascii=False),
        "tags_json": json.dumps(m.tags, ensure_ascii=False),
        "do_not_apply_when_json": json.dumps(m.do_not_apply_when, ensure_ascii=False),
        "sensitive_contexts_json": json.dumps(
            [v.value if hasattr(v, "value") else str(v) for v in m.sensitive_contexts],
            ensure_ascii=False,
        ),
        "path": str(doc.directory),
        "content_hash": doc.content_hash,
        "created_at": m.created_at.isoformat() if m.created_at else "",
        "updated_at": m.updated_at.isoformat() if m.updated_at else "",
    }


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for json_col in (
        "required_tools_json",
        "required_permissions_json",
        "triggers_json",
        "tags_json",
        "do_not_apply_when_json",
        "sensitive_contexts_json",
    ):
        try:
            d[json_col] = json.loads(d[json_col])
        except (json.JSONDecodeError, TypeError):
            d[json_col] = []
    return d


class CapabilityIndex:
    """SQLite-backed fast lookup for capability metadata."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    # ── lifecycle ──────────────────────────────────────────────

    def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SQL_CREATE)
        self._ensure_boundary_columns()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("CapabilityIndex not initialized — call init() first")
        return self._conn

    def _ensure_boundary_columns(self) -> None:
        existing = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(capability_index)").fetchall()
        }
        for column in ("do_not_apply_when_json", "sensitive_contexts_json"):
            if column not in existing:
                self.conn.execute(
                    f"ALTER TABLE capability_index ADD COLUMN {column} TEXT NOT NULL DEFAULT '[]'"
                )
        self.conn.commit()

    # ── indexing ───────────────────────────────────────────────

    def upsert(self, doc: "CapabilityDocument") -> None:
        row = _manifest_to_row(doc)
        columns = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        sql = f"INSERT OR REPLACE INTO capability_index ({columns}) VALUES ({placeholders})"
        self.conn.execute(sql, list(row.values()))
        self.conn.commit()

    def remove(self, cap_id: str, scope: str) -> None:
        self.conn.execute(
            "DELETE FROM capability_index WHERE id = ? AND scope = ?",
            (cap_id, scope),
        )
        self.conn.commit()

    def mark_disabled(self, cap_id: str, scope: str) -> None:
        self.conn.execute(
            "UPDATE capability_index SET status = ? WHERE id = ? AND scope = ?",
            (CapabilityStatus.DISABLED.value, cap_id, scope),
        )
        self.conn.commit()

    def mark_archived(self, cap_id: str, scope: str) -> None:
        self.conn.execute(
            "UPDATE capability_index SET status = ? WHERE id = ? AND scope = ?",
            (CapabilityStatus.ARCHIVED.value, cap_id, scope),
        )
        self.conn.commit()

    # ── lookup ─────────────────────────────────────────────────

    def get(self, cap_id: str, scope: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM capability_index WHERE id = ? AND scope = ?",
            (cap_id, scope),
        ).fetchone()
        return _row_to_dict(row) if row else None

    # ── search ─────────────────────────────────────────────────

    def search(
        self,
        query: str | None = None,
        *,
        filters: dict | None = None,
        limit: int = 20,
    ) -> list[dict]:
        conditions: list[str] = []
        params: list = []

        # By default, exclude disabled/archived/quarantined
        if filters is None or "status" not in filters:
            conditions.append("status = ?")
            params.append(CapabilityStatus.ACTIVE.value)

        if query and query.strip():
            q = f"%{query.strip()}%"
            conditions.append("(name LIKE ? OR description LIKE ? OR triggers_json LIKE ? OR tags_json LIKE ?)")
            params.extend([q, q, q, q])

        if filters:
            for fld in ("scope", "type", "maturity", "status", "risk_level"):
                if fld in filters:
                    conditions.append(f"{fld} = ?")
                    params.append(filters[fld])
            if "tags" in filters and filters["tags"]:
                tag_conds = " OR ".join("tags_json LIKE ?" for _ in filters["tags"])
                conditions.append(f"({tag_conds})")
                params.extend(f"%{t}%" for t in filters["tags"])
            if "required_tools" in filters and filters["required_tools"]:
                tool_conds = " OR ".join("required_tools_json LIKE ?" for _ in filters["required_tools"])
                conditions.append(f"({tool_conds})")
                params.extend(f"%{t}%" for t in filters["required_tools"])

        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        sql = f"SELECT * FROM capability_index{where} ORDER BY scope, name LIMIT ?"
        params.append(limit)

        rows = self.conn.execute(sql, params).fetchall()
        return [_row_to_dict(r) for r in rows]

    def resolve_with_precedence(
        self,
        cap_id: str,
        *,
        include_archived: bool = False,
    ) -> dict | None:
        status_condition = ""
        params: list = [cap_id]
        if not include_archived:
            status_condition = " AND status != ?"
            params.append(CapabilityStatus.ARCHIVED.value)

        rows = self.conn.execute(
            f"SELECT * FROM capability_index WHERE id = ?{status_condition}",
            params,
        ).fetchall()

        if not rows:
            return None

        scope_rank = {s.value: i for i, s in enumerate(SCOPE_PRECEDENCE)}

        def _rank(row: sqlite3.Row) -> int:
            return scope_rank.get(row["scope"], len(SCOPE_PRECEDENCE))

        best = min(rows, key=_rank)
        return _row_to_dict(best)

    # ── rebuild ────────────────────────────────────────────────

    def rebuild_from_store(self, store: "CapabilityStore") -> int:
        self.conn.execute("DELETE FROM capability_index")
        count = 0
        for doc in store._iter_all_dirs():
            try:
                self.upsert(doc)
                count += 1
            except Exception:
                pass
        self.conn.commit()
        return count

    # ── count ─────────────────────────────────────────────────

    def count(self, *, scope: str | None = None) -> int:
        if scope:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM capability_index WHERE scope = ?",
                (scope,),
            ).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) FROM capability_index").fetchone()
        return row[0] if row else 0
