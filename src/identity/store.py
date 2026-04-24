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

from dataclasses import dataclass

from src.identity.auth import AuthContext, check_scope
from src.identity.models import (
    AuditAction,
    AuditLogEntry,
    ClaimEvidence,
    ClaimRevision,
    ClaimStatus,
    ClaimType,
    ClaimOwner,
    ConflictEvent,
    GateEvent,
    IdentityClaim,
    InjectionTrace,
    OverrideToken,
    RetrievalTrace,
    RevisionAction,
    Sensitivity,
)


@dataclass
class RedactResult:
    """redact_claim 的返回结果"""
    success: bool
    requires_source_redaction: bool = False

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
        await self._db.execute("PRAGMA synchronous=NORMAL")
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
        """导出主张及其所有修订为字典，并写入审计日志。"""
        check_scope(auth, "identity.read")
        claim = await self.get_claim(claim_id, auth)
        if claim is None:
            raise ValueError(f"claim {claim_id!r} not found")
        revisions = await self.get_revisions(claim_id, auth)

        # 写入审计日志
        audit_entry = AuditLogEntry(
            entry_id=str(uuid4()),
            action=AuditAction.CLAIM_CREATED,  # 使用 closest action; 导出事件
            claim_id=claim_id,
            actor=auth.actor,
            details={"event": "export"},
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        await self.write_audit_log(audit_entry, auth)

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
    # Task 7: Trace + Event Writers
    # ------------------------------------------------------------------

    async def write_gate_event(self, event: GateEvent) -> None:
        """INSERT into identity_gate_events."""
        await self._db.execute(
            "INSERT INTO identity_gate_events "
            "(event_id, claim_id, outcome, pass_reason, gate_level, "
            "context_profile, signals, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.event_id,
                event.claim_id,
                event.outcome.value if hasattr(event.outcome, "value") else event.outcome,
                (event.pass_reason.value if hasattr(event.pass_reason, "value") else event.pass_reason)
                if event.pass_reason is not None else None,
                event.gate_level.value if hasattr(event.gate_level, "value") else event.gate_level,
                (event.context_profile.value if hasattr(event.context_profile, "value") else event.context_profile)
                if event.context_profile is not None else None,
                json.dumps(event.signals),
                event.created_at,
            ),
        )
        await self._db.commit()

    async def write_retrieval_trace(self, trace: RetrievalTrace) -> None:
        """INSERT into identity_retrieval_traces."""
        await self._db.execute(
            "INSERT INTO identity_retrieval_traces "
            "(trace_id, query, context_profile, candidate_ids, selected_ids, "
            "redacted_ids, latency_ms, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                trace.trace_id,
                trace.query,
                (trace.context_profile.value if hasattr(trace.context_profile, "value") else trace.context_profile)
                if trace.context_profile is not None else None,
                json.dumps(trace.candidate_ids),
                json.dumps(trace.selected_ids),
                json.dumps(trace.redacted_ids),
                trace.latency_ms,
                trace.created_at,
            ),
        )
        await self._db.commit()

    async def write_injection_trace(self, trace: InjectionTrace) -> None:
        """INSERT into identity_injection_traces."""
        await self._db.execute(
            "INSERT INTO identity_injection_traces "
            "(trace_id, retrieval_trace_id, claim_ids, token_count, "
            "budget_total, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                trace.trace_id,
                trace.retrieval_trace_id,
                json.dumps(trace.claim_ids),
                trace.token_count,
                trace.budget_total,
                trace.created_at,
            ),
        )
        await self._db.commit()

    async def write_audit_log(self, entry: AuditLogEntry, auth: AuthContext) -> None:
        """INSERT into identity_audit_log with auth_context_id."""
        ctx_id = await self.save_auth_context(auth)
        await self._db.execute(
            "INSERT INTO identity_audit_log "
            "(entry_id, action, claim_id, actor, details, auth_context_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                entry.entry_id,
                entry.action.value if hasattr(entry.action, "value") else entry.action,
                entry.claim_id,
                entry.actor,
                json.dumps(entry.details),
                ctx_id,
                entry.created_at,
            ),
        )
        await self._db.commit()

    async def write_conflict_event(self, event: ConflictEvent) -> None:
        """INSERT into identity_conflict_events."""
        await self._db.execute(
            "INSERT INTO identity_conflict_events "
            "(event_id, claim_id_a, claim_id_b, conflict_type, resolution, "
            "resolved, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event.event_id,
                event.claim_id_a,
                event.claim_id_b,
                event.conflict_type.value if hasattr(event.conflict_type, "value") else event.conflict_type,
                event.resolution,
                1 if event.resolved else 0,
                event.created_at,
            ),
        )
        await self._db.commit()

    async def create_override_token(
        self, token: OverrideToken, auth: AuthContext
    ) -> None:
        """INSERT into identity_override_tokens."""
        await self._db.execute(
            "INSERT INTO identity_override_tokens "
            "(token_id, claim_id, issuer, reason, action_payload_hash, "
            "consumed, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
            (
                token.token_id,
                token.claim_id,
                token.issuer,
                token.reason,
                token.action_payload_hash,
                token.expires_at,
                token.created_at,
            ),
        )
        await self._db.commit()

    async def consume_override_token(
        self, token_id: str, payload_hash: str, auth: AuthContext
    ) -> bool:
        """SET consumed=1 WHERE not consumed AND payload hash matches.

        Returns True if consumed, False if already consumed or not found.
        """
        cursor = await self._db.execute(
            "UPDATE identity_override_tokens SET consumed=1 "
            "WHERE token_id=? AND action_payload_hash=? AND consumed=0",
            (token_id, payload_hash),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def _list_gate_events(self) -> list[dict]:
        """内部辅助：列出所有门控事件（测试用）。"""
        cursor = await self._db.execute(
            "SELECT * FROM identity_gate_events ORDER BY created_at"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def _list_retrieval_traces(self) -> list[dict]:
        """内部辅助：列出所有检索追踪（测试用）。"""
        cursor = await self._db.execute(
            "SELECT * FROM identity_retrieval_traces ORDER BY created_at"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Task 8: Evidence + Privacy (Redact / Erase) + Tombstones
    # ------------------------------------------------------------------

    async def add_evidence(
        self, evidence: ClaimEvidence, auth: AuthContext
    ) -> None:
        """INSERT into identity_evidence."""
        await self._db.execute(
            "INSERT INTO identity_evidence "
            "(evidence_id, claim_id, evidence_type, content, source_ref, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                evidence.evidence_id,
                evidence.claim_id,
                evidence.evidence_type,
                evidence.content,
                evidence.source,
                evidence.created_at,
            ),
        )
        await self._db.commit()

    async def get_evidence(
        self, claim_id: str, auth: AuthContext
    ) -> list[dict]:
        """SELECT from identity_evidence."""
        cursor = await self._db.execute(
            "SELECT * FROM identity_evidence WHERE claim_id=? ORDER BY created_at",
            (claim_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def redact_claim(
        self,
        claim_id: str,
        auth: AuthContext,
        reason: str,
        *,
        source_already_redacted: bool = False,
    ) -> RedactResult:
        """抹除主张文本。

        如果主张有 markdown_span 证据且 source_already_redacted=False，
        返回 requires_source_redaction=True, success=False。
        """
        check_scope(auth, "identity.redact")

        existing = await self.get_claim(claim_id, auth)
        if existing is None:
            raise ValueError(f"claim {claim_id!r} not found")

        # 检查是否有 markdown_span 证据
        evidence = await self.get_evidence(claim_id, auth)
        has_markdown = any(e["evidence_type"] == "markdown_span" for e in evidence)

        if has_markdown and not source_already_redacted:
            return RedactResult(success=False, requires_source_redaction=True)

        # 清理旧 outbox 条目中的敏感文本
        await self._db.execute(
            "DELETE FROM identity_index_outbox WHERE claim_id=? AND processed_at IS NULL",
            (claim_id,),
        )
        await self._db.commit()

        # 执行 redact：清除 object_val，设置 predicate 为 [REDACTED]，状态为 redacted
        now = datetime.now(timezone.utc).isoformat()
        old_snapshot = {
            "object_val": existing.object_val,
            "predicate": existing.predicate,
            "status": existing.status if isinstance(existing.status, str) else existing.status.value,
        }
        new_snapshot = {
            "claim_id": existing.claim_id,
            "raw_block_id": existing.raw_block_id,
            "claim_local_key": existing.claim_local_key,
            "source_file": existing.source_file,
            "stable_block_key": existing.stable_block_key,
            "claim_type": existing.claim_type if isinstance(existing.claim_type, str) else existing.claim_type.value,
            "owner": existing.owner if isinstance(existing.owner, str) else existing.owner.value,
            "predicate": "[REDACTED]",
            "object_val": "",
            "confidence": existing.confidence,
            "sensitivity": existing.sensitivity if isinstance(existing.sensitivity, str) else existing.sensitivity.value,
            "status": "redacted",
            "tags": existing.tags if isinstance(existing.tags, list) else json.loads(existing.tags),
            "created_at": existing.created_at,
        }
        revision = ClaimRevision(
            revision_id=str(uuid4()),
            claim_id=claim_id,
            action=RevisionAction.REDACTED,
            old_snapshot=old_snapshot,
            new_snapshot=new_snapshot,
            actor=auth.actor,
            reason=reason,
            created_at=now,
        )
        await self.append_revision(revision, auth)
        return RedactResult(success=True)

    async def erase_claim(
        self, claim_id: str, auth: AuthContext, reason: str
    ) -> None:
        """完全擦除主张（Addendum P1.1）。

        保留 tombstone 投影行，清除字段。删除证据和关系。
        写入 identity_redaction_tombstones。追加 ERASED 修订。
        Enqueue delete_vector。
        """
        check_scope(auth, "identity.erase")

        existing = await self.get_claim(claim_id, auth)
        if existing is None:
            raise ValueError(f"claim {claim_id!r} not found")

        now = datetime.now(timezone.utc).isoformat()

        # 删除证据、关系和旧 outbox 条目
        await self._db.execute(
            "DELETE FROM identity_evidence WHERE claim_id=?", (claim_id,)
        )
        await self._db.execute(
            "DELETE FROM identity_relations WHERE source_claim_id=? OR target_claim_id=?",
            (claim_id, claim_id),
        )
        await self._db.execute(
            "DELETE FROM identity_index_outbox WHERE claim_id=? AND processed_at IS NULL",
            (claim_id,),
        )

        # 写入 tombstone
        tombstone_id = str(uuid4())
        await self._db.execute(
            "INSERT INTO identity_redaction_tombstones "
            "(tombstone_id, claim_id, source_file, stable_block_key, "
            "raw_block_id, erased_at, reason) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                tombstone_id,
                claim_id,
                existing.source_file,
                existing.stable_block_key,
                existing.raw_block_id,
                now,
                reason,
            ),
        )
        await self._db.commit()

        # 追加 ERASED 修订
        old_snapshot = {
            "object_val": existing.object_val,
            "predicate": existing.predicate,
            "status": existing.status if isinstance(existing.status, str) else existing.status.value,
        }
        new_snapshot = {
            "claim_id": existing.claim_id,
            "raw_block_id": existing.raw_block_id,
            "claim_local_key": existing.claim_local_key,
            "source_file": existing.source_file,
            "stable_block_key": existing.stable_block_key,
            "claim_type": existing.claim_type if isinstance(existing.claim_type, str) else existing.claim_type.value,
            "owner": existing.owner if isinstance(existing.owner, str) else existing.owner.value,
            "predicate": "[ERASED]",
            "object_val": "",
            "confidence": existing.confidence,
            "sensitivity": existing.sensitivity if isinstance(existing.sensitivity, str) else existing.sensitivity.value,
            "status": "erased",
            "tags": existing.tags if isinstance(existing.tags, list) else json.loads(existing.tags),
            "created_at": existing.created_at,
        }
        revision = ClaimRevision(
            revision_id=str(uuid4()),
            claim_id=claim_id,
            action=RevisionAction.ERASED,
            old_snapshot=old_snapshot,
            new_snapshot=new_snapshot,
            actor=auth.actor,
            reason=reason,
            created_at=now,
        )
        await self.append_revision(revision, auth)

    async def _list_tombstones(self) -> list[dict]:
        """内部辅助：列出所有 tombstone（测试用）。"""
        cursor = await self._db.execute(
            "SELECT * FROM identity_redaction_tombstones ORDER BY erased_at"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Task 9: Relations / Evidence / Claim Sources / Explicit Access
    # ------------------------------------------------------------------

    async def add_relation(
        self,
        source_id: str,
        target_id: str,
        relation_type: str,
        weight: float,
        auth: AuthContext,
    ) -> None:
        """INSERT into identity_relations."""
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT INTO identity_relations "
            "(source_claim_id, target_claim_id, relation_type, weight, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (source_id, target_id, relation_type, weight, now),
        )
        await self._db.commit()

    async def get_neighbors(
        self, claim_id: str, auth: AuthContext
    ) -> list[dict]:
        """SELECT from identity_relations where source_claim_id=claim_id."""
        cursor = await self._db.execute(
            "SELECT * FROM identity_relations WHERE source_claim_id=? "
            "ORDER BY created_at",
            (claim_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def upsert_claim_source(
        self,
        claim_id: str,
        source_file: str,
        byte_start: int,
        byte_end: int,
        sha256: str,
        stable_block_key: str,
    ) -> None:
        """INSERT OR REPLACE into identity_claim_sources (Addendum P0.3)."""
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT OR REPLACE INTO identity_claim_sources "
            "(claim_id, source_file, source_span_start, source_span_end, "
            "sha256_at_parse, stable_block_key, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (claim_id, source_file, byte_start, byte_end, sha256, stable_block_key, now),
        )
        await self._db.commit()

    async def get_claim_sources(self, claim_id: str) -> list[dict]:
        """SELECT from identity_claim_sources."""
        cursor = await self._db.execute(
            "SELECT * FROM identity_claim_sources WHERE claim_id=? ORDER BY created_at",
            (claim_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def create_explicit_access_request(
        self,
        actor_id: str,
        scope: str,
        target_claim_ids: list[str],
        ttl_seconds: int,
        auth: AuthContext,
    ) -> str:
        """INSERT into identity_explicit_access_requests, returns request_id."""
        from datetime import timedelta

        request_id = str(uuid4())
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat()
        await self._db.execute(
            "INSERT INTO identity_explicit_access_requests "
            "(request_id, actor_id, scope, target_claim_ids, ttl_seconds, "
            "consumed, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
            (
                request_id,
                actor_id,
                scope,
                json.dumps(target_claim_ids),
                ttl_seconds,
                now.isoformat(),
                expires_at,
            ),
        )
        await self._db.commit()
        return request_id

    async def _raw_execute(self, sql: str) -> None:
        """内部辅助：直接执行原始 SQL（测试 trigger 用）。"""
        await self._db.execute(sql)
        await self._db.commit()

    # ------------------------------------------------------------------
    # Task 10: Extraction Cache + Gate Cache + Outbox drain
    # ------------------------------------------------------------------

    async def set_extraction_cache(self, key: str, result: dict) -> None:
        """INSERT OR REPLACE into identity_extraction_cache."""
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT OR REPLACE INTO identity_extraction_cache "
            "(cache_key, result, created_at) VALUES (?, ?, ?)",
            (key, json.dumps(result), now),
        )
        await self._db.commit()

    async def get_extraction_cache(self, key: str) -> dict | None:
        """SELECT, parse JSON. Returns None on miss."""
        cursor = await self._db.execute(
            "SELECT result FROM identity_extraction_cache WHERE cache_key=?",
            (key,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    async def clear_extraction_cache(
        self, scope: str, source_file: str | None, auth: AuthContext
    ) -> int:
        """DELETE all or by source; returns count."""
        if scope == "all" or source_file is None:
            cursor = await self._db.execute(
                "DELETE FROM identity_extraction_cache"
            )
        else:
            cursor = await self._db.execute(
                "DELETE FROM identity_extraction_cache WHERE cache_key LIKE ?",
                (f"%{source_file}%",),
            )
        await self._db.commit()
        return cursor.rowcount

    async def set_gate_cache(
        self, key: str, result: dict, ttl_seconds: int
    ) -> None:
        """INSERT OR REPLACE with computed expires_at."""
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat()
        await self._db.execute(
            "INSERT OR REPLACE INTO identity_gate_cache "
            "(cache_key, outcome, computed_at, expires_at) VALUES (?, ?, ?, ?)",
            (key, json.dumps(result), now.isoformat(), expires_at),
        )
        await self._db.commit()

    async def get_gate_cache(self, key: str) -> dict | None:
        """SELECT, check not expired, parse JSON."""
        now = datetime.now(timezone.utc).isoformat()
        cursor = await self._db.execute(
            "SELECT outcome FROM identity_gate_cache "
            "WHERE cache_key=? AND expires_at > ?",
            (key, now),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    async def enqueue_outbox(
        self, claim_id: str, action: str, payload: dict | None = None
    ) -> None:
        """INSERT into identity_index_outbox."""
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT INTO identity_index_outbox "
            "(claim_id, action, payload, created_at) VALUES (?, ?, ?, ?)",
            (claim_id, action, json.dumps(payload or {}), now),
        )
        await self._db.commit()

    async def drain_outbox(self, batch_size: int = 50) -> int:
        """Mark pending entries as processed. Returns count processed."""
        now = datetime.now(timezone.utc).isoformat()
        cursor = await self._db.execute(
            "UPDATE identity_index_outbox SET processed_at=? "
            "WHERE outbox_id IN ("
            "  SELECT outbox_id FROM identity_index_outbox "
            "  WHERE processed_at IS NULL ORDER BY outbox_id LIMIT ?"
            ")",
            (now, batch_size),
        )
        await self._db.commit()
        return cursor.rowcount

    # ------------------------------------------------------------------
    # Task 19: Acceptance test helper methods
    # ------------------------------------------------------------------

    async def _count_retrieval_traces(self) -> int:
        cursor = await self._db.execute("SELECT COUNT(*) FROM identity_retrieval_traces")
        row = await cursor.fetchone()
        return row[0]

    async def _search_all_tables(self, text: str) -> bool:
        """Search for text in non-audit tables. Returns True if found."""
        # Check identity_claims.object_val and identity_claims.predicate
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM identity_claims WHERE object_val LIKE ? OR predicate LIKE ?",
            (f"%{text}%", f"%{text}%"),
        )
        if (await cursor.fetchone())[0] > 0:
            return True
        # Check identity_evidence.content
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM identity_evidence WHERE content LIKE ?",
            (f"%{text}%",),
        )
        if (await cursor.fetchone())[0] > 0:
            return True
        # Check identity_index_outbox.payload
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM identity_index_outbox WHERE payload LIKE ?",
            (f"%{text}%",),
        )
        if (await cursor.fetchone())[0] > 0:
            return True
        return False

    async def _list_audit_entries(self, action: str | None = None) -> list[dict]:
        if action:
            cursor = await self._db.execute(
                "SELECT * FROM identity_audit_log WHERE action=? ORDER BY created_at",
                (action,),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM identity_audit_log ORDER BY created_at"
            )
        return [dict(r) for r in await cursor.fetchall()]

    async def verify_explicit_request(
        self, request_id: str, actor_id: str, scope: str, target_claim_id: str,
    ) -> bool:
        """Verify and consume an explicit access request. Returns True if valid and consumed."""
        cursor = await self._db.execute(
            "SELECT * FROM identity_explicit_access_requests WHERE request_id=?",
            (request_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return False
        row_dict = dict(row)
        # Check not consumed
        if row_dict.get("consumed", 0):
            return False
        # Check not expired
        expires = row_dict.get("expires_at", "")
        if expires:
            try:
                exp_dt = datetime.fromisoformat(expires)
                if exp_dt < datetime.now(timezone.utc):
                    return False
            except (ValueError, TypeError):
                pass
        # Check actor matches
        if row_dict.get("actor_id") != actor_id:
            return False
        # Check scope matches
        if row_dict.get("scope") != scope:
            return False
        # Check target_claim_id in target list
        target_ids = json.loads(row_dict.get("target_claim_ids", "[]"))
        if target_claim_id not in target_ids:
            return False
        # Mark consumed
        await self._db.execute(
            "UPDATE identity_explicit_access_requests SET consumed=1 WHERE request_id=?",
            (request_id,),
        )
        await self._db.commit()
        return True

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
