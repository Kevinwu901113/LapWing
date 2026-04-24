from __future__ import annotations

# 身份基底事件溯源存储 — 异步 SQLite 后端
# Identity substrate event-sourced store — async SQLite backend

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import aiosqlite

from src.identity.auth import AuthContext, check_scope
from src.identity.models import (
    ClaimRevision,
    ClaimStatus,
    ClaimType,
    ClaimOwner,
    IdentityClaim,
    RevisionAction,
    Sensitivity,
)

logger = logging.getLogger("lapwing.identity.store")


class IdentityStore:
    """事件溯源身份存储。

    所有主张变更先写入 identity_revisions（追加），再投影到
    identity_claims（物化视图）。outbox 队列供向量索引异步消费。
    """

    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def init(self) -> None:
        """打开数据库，启用 WAL 和外键，执行迁移。"""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self._db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._apply_migrations()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    # ------------------------------------------------------------------
    # 迁移
    # ------------------------------------------------------------------

    async def _apply_migrations(self) -> None:
        """按序执行 src/identity/migrations/*.sql。"""
        migrations_dir = Path(__file__).parent / "migrations"
        for sql_file in sorted(migrations_dir.glob("*.sql")):
            sql = sql_file.read_text()
            try:
                await self._db.executescript(sql)
            except Exception:
                # 002 包含 ALTER TABLE ADD COLUMN，重复执行会报 duplicate column
                for statement in sql.split(";"):
                    statement = statement.strip()
                    if not statement:
                        continue
                    try:
                        await self._db.execute(statement)
                        await self._db.commit()
                    except Exception:
                        pass

    # ------------------------------------------------------------------
    # 内省辅助
    # ------------------------------------------------------------------

    async def _get_tables(self) -> list[str]:
        cursor = await self._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        rows = await cursor.fetchall()
        return [r[0] for r in rows]

    async def _get_migration_version(self) -> int:
        cursor = await self._db.execute(
            "SELECT MAX(version) FROM identity_migration_version"
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    # ------------------------------------------------------------------
    # 鉴权上下文 / 特性快照持久化
    # ------------------------------------------------------------------

    async def save_auth_context(self, auth: AuthContext) -> str:
        """持久化 AuthContext，返回 context_id。"""
        ctx_id = str(uuid4())
        await self._db.execute(
            "INSERT INTO identity_auth_contexts "
            "(context_id, actor, scopes, session_id) VALUES (?, ?, ?, ?)",
            (ctx_id, auth.actor, json.dumps(sorted(auth.scopes)), auth.session_id),
        )
        await self._db.commit()
        return ctx_id

    async def save_feature_flags_snapshot(self, flags: dict) -> str:
        """持久化特性开关快照，基于哈希去重（Addendum P1.3）。"""
        canonical = json.dumps(flags, sort_keys=True)
        snapshot_hash = hashlib.sha256(canonical.encode()).hexdigest()[:16]
        cursor = await self._db.execute(
            "SELECT snapshot_id FROM identity_feature_flags_snapshots "
            "WHERE snapshot_hash=?",
            (snapshot_hash,),
        )
        existing = await cursor.fetchone()
        if existing:
            await self._db.execute(
                "UPDATE identity_feature_flags_snapshots "
                "SET reference_count = reference_count + 1 WHERE snapshot_id=?",
                (existing[0],),
            )
            await self._db.commit()
            return existing[0]
        snapshot_id = str(uuid4())
        await self._db.execute(
            "INSERT INTO identity_feature_flags_snapshots "
            "(snapshot_id, flags, snapshot_hash, reference_count) "
            "VALUES (?, ?, ?, 1)",
            (snapshot_id, canonical, snapshot_hash),
        )
        await self._db.commit()
        return snapshot_id

    # ------------------------------------------------------------------
    # 事件溯源核心 — 写路径
    # ------------------------------------------------------------------

    async def append_revision(
        self, revision: ClaimRevision, auth: AuthContext
    ) -> None:
        """追加修订，同时投影主张、写 outbox——单事务。"""
        check_scope(auth, "identity.write")
        ctx_id = await self.save_auth_context(auth)
        now = datetime.now(timezone.utc).isoformat()

        async with self._db.cursor() as cur:
            # 1) 写入修订日志
            await cur.execute(
                "INSERT INTO identity_revisions "
                "(revision_id, claim_id, action, old_snapshot, new_snapshot, "
                "actor, reason, auth_context_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    revision.revision_id,
                    revision.claim_id,
                    revision.action.value
                    if isinstance(revision.action, RevisionAction)
                    else revision.action,
                    json.dumps(revision.old_snapshot) if revision.old_snapshot else None,
                    json.dumps(revision.new_snapshot),
                    revision.actor,
                    revision.reason,
                    ctx_id,
                    revision.created_at or now,
                ),
            )
            # 2) 投影到 identity_claims
            await self._materialize_claim(revision, cur)
            # 3) 写 outbox
            action = (
                "delete_vector"
                if revision.action in (RevisionAction.DEPRECATED, RevisionAction.ERASED)
                else "upsert_vector"
            )
            await cur.execute(
                "INSERT INTO identity_index_outbox "
                "(claim_id, action, payload, created_at) VALUES (?, ?, ?, ?)",
                (revision.claim_id, action, json.dumps(revision.new_snapshot), now),
            )
        await self._db.commit()

    async def _materialize_claim(
        self,
        revision: ClaimRevision,
        cursor: aiosqlite.Cursor | None = None,
    ) -> None:
        """根据 revision.new_snapshot 做 INSERT OR REPLACE 到 identity_claims。"""
        snap = revision.new_snapshot
        now = datetime.now(timezone.utc).isoformat()

        sql = (
            "INSERT OR REPLACE INTO identity_claims "
            "(claim_id, raw_block_id, claim_local_key, source_file, stable_block_key, "
            "claim_type, owner, predicate, object_val, confidence, sensitivity, "
            "status, tags, evidence_ids, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        params = (
            snap.get("claim_id", revision.claim_id),
            snap.get("raw_block_id", ""),
            snap.get("claim_local_key", "claim_0"),
            snap.get("source_file", ""),
            snap.get("stable_block_key", ""),
            snap.get("claim_type", "value"),
            snap.get("owner", "lapwing"),
            snap.get("predicate", ""),
            snap.get("object_val", ""),
            snap.get("confidence", 0.5),
            snap.get("sensitivity", "public"),
            snap.get("status", "active"),
            json.dumps(snap.get("tags", [])),
            json.dumps(snap.get("evidence_ids", [])),
            snap.get("created_at", now),
            now,
        )
        exe = cursor or self._db
        await exe.execute(sql, params)

    # ------------------------------------------------------------------
    # 读路径
    # ------------------------------------------------------------------

    async def get_claim(
        self, claim_id: str, auth: AuthContext
    ) -> IdentityClaim | None:
        """按 claim_id 读取投影后的主张。"""
        check_scope(auth, "identity.read")
        cursor = await self._db.execute(
            "SELECT * FROM identity_claims WHERE claim_id=?", (claim_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_claim(row)

    async def list_claims(
        self, auth: AuthContext, *, status: str | None = None
    ) -> list[IdentityClaim]:
        """列出所有主张，可按状态过滤。"""
        check_scope(auth, "identity.read")
        if status:
            cursor = await self._db.execute(
                "SELECT * FROM identity_claims WHERE status=? ORDER BY created_at",
                (status,),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM identity_claims ORDER BY created_at"
            )
        rows = await cursor.fetchall()
        return [self._row_to_claim(r) for r in rows]

    async def get_revisions(
        self, claim_id: str, auth: AuthContext
    ) -> list[ClaimRevision]:
        """读取某条主张的修订历史，按 created_at 升序。"""
        check_scope(auth, "identity.read")
        cursor = await self._db.execute(
            "SELECT * FROM identity_revisions WHERE claim_id=? ORDER BY created_at",
            (claim_id,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_revision(r) for r in rows]

    # ------------------------------------------------------------------
    # 弃用 / 导出
    # ------------------------------------------------------------------

    async def deprecate_claim(
        self, claim_id: str, auth: AuthContext, reason: str
    ) -> None:
        """软删除主张：追加 DEPRECATED 修订并更新投影。"""
        check_scope(auth, "identity.deprecate")
        existing = await self.get_claim(claim_id, auth)
        if existing is None:
            raise ValueError(f"claim {claim_id!r} not found")

        now = datetime.now(timezone.utc).isoformat()
        old_snapshot = {
            "object_val": existing.object_val,
            "status": existing.status
            if isinstance(existing.status, str)
            else existing.status.value,
        }
        new_snapshot = {
            "claim_id": existing.claim_id,
            "raw_block_id": existing.raw_block_id,
            "claim_local_key": existing.claim_local_key,
            "source_file": existing.source_file,
            "stable_block_key": existing.stable_block_key,
            "claim_type": existing.claim_type
            if isinstance(existing.claim_type, str)
            else existing.claim_type.value,
            "owner": existing.owner
            if isinstance(existing.owner, str)
            else existing.owner.value,
            "predicate": existing.predicate,
            "object_val": existing.object_val,
            "confidence": existing.confidence,
            "sensitivity": existing.sensitivity
            if isinstance(existing.sensitivity, str)
            else existing.sensitivity.value,
            "status": "deprecated",
            "tags": existing.tags if isinstance(existing.tags, list) else json.loads(existing.tags),
            "created_at": existing.created_at,
        }
        revision = ClaimRevision(
            revision_id=str(uuid4()),
            claim_id=claim_id,
            action=RevisionAction.DEPRECATED,
            old_snapshot=old_snapshot,
            new_snapshot=new_snapshot,
            actor=auth.actor,
            reason=reason,
            created_at=now,
        )
        await self.append_revision(revision, auth)

    async def export_claim(
        self, claim_id: str, auth: AuthContext
    ) -> dict:
        """导出主张及其所有修订为字典。"""
        check_scope(auth, "identity.read")
        claim = await self.get_claim(claim_id, auth)
        if claim is None:
            raise ValueError(f"claim {claim_id!r} not found")
        revisions = await self.get_revisions(claim_id, auth)
        return {
            "claim": {
                "claim_id": claim.claim_id,
                "object_val": claim.object_val,
                "status": claim.status
                if isinstance(claim.status, str)
                else claim.status.value,
                "claim_type": claim.claim_type
                if isinstance(claim.claim_type, str)
                else claim.claim_type.value,
            },
            "revisions": [
                {
                    "revision_id": r.revision_id,
                    "action": r.action
                    if isinstance(r.action, str)
                    else r.action.value,
                    "reason": r.reason,
                    "created_at": r.created_at,
                }
                for r in revisions
            ],
        }

    # ------------------------------------------------------------------
    # 投影重建
    # ------------------------------------------------------------------

    async def rebuild_projection(self, auth: AuthContext) -> None:
        """从修订日志重建所有 identity_claims 行。"""
        check_scope(auth, "identity.rebuild")
        # 清空投影表
        await self._db.execute("DELETE FROM identity_claims")
        # 按 created_at 重放
        cursor = await self._db.execute(
            "SELECT * FROM identity_revisions ORDER BY created_at"
        )
        rows = await cursor.fetchall()
        for row in rows:
            rev = self._row_to_revision(row)
            await self._materialize_claim(rev)
        await self._db.commit()

    # ------------------------------------------------------------------
    # Outbox 内部查询（测试用）
    # ------------------------------------------------------------------

    async def _get_pending_outbox(self) -> list[dict]:
        cursor = await self._db.execute(
            "SELECT * FROM identity_index_outbox WHERE processed_at IS NULL "
            "ORDER BY outbox_id"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # 行 → 数据类转换
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_claim(row) -> IdentityClaim:
        tags_raw = row["tags"]
        tags = json.loads(tags_raw) if isinstance(tags_raw, str) else (tags_raw or [])
        ev_raw = row["evidence_ids"]
        evidence_ids = json.loads(ev_raw) if isinstance(ev_raw, str) else (ev_raw or [])
        return IdentityClaim(
            claim_id=row["claim_id"],
            raw_block_id=row["raw_block_id"],
            claim_local_key=row["claim_local_key"],
            source_file=row["source_file"],
            stable_block_key=row["stable_block_key"],
            claim_type=row["claim_type"],
            owner=row["owner"],
            predicate=row["predicate"],
            object_val=row["object_val"],
            confidence=row["confidence"],
            sensitivity=row["sensitivity"],
            status=row["status"],
            tags=tags,
            evidence_ids=evidence_ids,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_revision(row) -> ClaimRevision:
        old_snap = row["old_snapshot"]
        new_snap = row["new_snapshot"]
        return ClaimRevision(
            revision_id=row["revision_id"],
            claim_id=row["claim_id"],
            action=row["action"],
            old_snapshot=json.loads(old_snap) if old_snap else None,
            new_snapshot=json.loads(new_snap),
            actor=row["actor"],
            reason=row["reason"],
            created_at=row["created_at"],
        )
