"""src/agents/catalog.py — SQLite-backed AgentSpec catalog."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import aiosqlite

from src.agents.spec import (
    AgentLifecyclePolicy,
    AgentResourceLimits,
    AgentSpec,
)
from src.core.time_utils import now as local_now

logger = logging.getLogger("lapwing.agents.catalog")


_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS agent_catalog (
    id           TEXT PRIMARY KEY,
    name         TEXT UNIQUE NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'dynamic',
    status       TEXT NOT NULL DEFAULT 'active',
    spec_json    TEXT NOT NULL,
    spec_hash    TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    created_by   TEXT NOT NULL DEFAULT 'brain',
    created_reason TEXT NOT NULL DEFAULT ''
);
"""

_INDEX_NAME_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_catalog_name ON agent_catalog(name);"
)
_INDEX_KIND_STATUS_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_catalog_kind_status "
    "ON agent_catalog(kind, status);"
)


class AgentCatalog:
    TABLE = "agent_catalog"

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)

    async def init(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(_CREATE_SQL)
            await conn.execute(_INDEX_NAME_SQL)
            await conn.execute(_INDEX_KIND_STATUS_SQL)
            await conn.commit()

    async def save(self, spec: AgentSpec) -> None:
        spec.updated_at = local_now()
        data = asdict(spec)
        spec_json = json.dumps(data, default=str, ensure_ascii=False)
        spec_hash = spec.spec_hash()
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                f"""INSERT OR REPLACE INTO {self.TABLE}
                    (id, name, kind, status, spec_json, spec_hash,
                     created_at, updated_at, created_by, created_reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    spec.id,
                    spec.name,
                    spec.kind,
                    spec.status,
                    spec_json,
                    spec_hash,
                    spec.created_at.isoformat(),
                    spec.updated_at.isoformat(),
                    spec.created_by,
                    spec.created_reason,
                ),
            )
            await conn.commit()

    async def get(self, agent_id: str) -> AgentSpec | None:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM {self.TABLE} WHERE id = ?", (agent_id,)
            ) as cur:
                row = await cur.fetchone()
        if row is None:
            return None
        return self._row_to_spec(row)

    async def get_by_name(self, name: str) -> AgentSpec | None:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM {self.TABLE} WHERE name = ?", (name,)
            ) as cur:
                row = await cur.fetchone()
        if row is None:
            return None
        return self._row_to_spec(row)

    async def list_specs(
        self,
        *,
        kind: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[AgentSpec]:
        clauses: list[str] = []
        params: list[object] = []
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            f"SELECT * FROM {self.TABLE} {where} "
            f"ORDER BY updated_at DESC LIMIT ?"
        )
        params.append(limit)
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(sql, tuple(params)) as cur:
                rows = await cur.fetchall()
        return [self._row_to_spec(r) for r in rows]

    async def archive(self, agent_id: str) -> None:
        now_iso = local_now().isoformat()
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                f"UPDATE {self.TABLE} SET status = 'archived', "
                f"updated_at = ? WHERE id = ?",
                (now_iso, agent_id),
            )
            await conn.commit()

    async def delete(self, agent_id: str) -> None:
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                f"DELETE FROM {self.TABLE} WHERE id = ?", (agent_id,)
            )
            await conn.commit()

    async def count(
        self,
        *,
        kind: str | None = None,
        status: str | None = None,
    ) -> int:
        clauses: list[str] = []
        params: list[object] = []
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT COUNT(*) FROM {self.TABLE} {where}"
        async with aiosqlite.connect(self._db_path) as conn:
            async with conn.execute(sql, tuple(params)) as cur:
                row = await cur.fetchone()
        return int(row[0]) if row else 0

    def _row_to_spec(self, row) -> AgentSpec:
        """Reconstruct AgentSpec from a SELECT row (uses spec_json).

        Authoritative columns (status, updated_at) override spec_json so
        mutations like archive() are reflected even though spec_json is not
        re-serialized on those updates.
        """
        raw = json.loads(row["spec_json"])
        lifecycle = AgentLifecyclePolicy(**raw.pop("lifecycle"))
        limits = AgentResourceLimits(**raw.pop("resource_limits"))
        created_at = datetime.fromisoformat(raw.pop("created_at"))
        updated_at = datetime.fromisoformat(row["updated_at"])
        raw.pop("updated_at", None)
        raw["status"] = row["status"]
        return AgentSpec(
            **raw,
            lifecycle=lifecycle,
            resource_limits=limits,
            created_at=created_at,
            updated_at=updated_at,
        )
