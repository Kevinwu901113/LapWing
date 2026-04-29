"""WikiStore — wiki page CRUD + audit trail.

Phase 2 §2.2 + §2.5. The single funnel for every wiki page write. All
markdown writes in the project go through ``apply_patch``; nothing else
is permitted to ``open(...).write()`` a wiki file.

apply_patch is a 10-step pipeline (Phase 2 blueprint §2.2):

    1. check write_enabled feature flag
    2. check patch.risk (high → pending queue, no auto-apply)
    3. MemoryGuard.scan(content) — guard-blocked patches go to pending
       with a MEMORY_WIKI_GUARD_BLOCKED audit event, not silently dropped
    4. read target page (skip if operation == "create")
    5. before_hash optimistic lock — abort if drift detected
    6. apply the operation
    7. validate frontmatter schema on the new content
    8. atomic write: tempfile + rename
    9. compute after_hash
    10. emit mutation events + record manifest entry
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import aiosqlite
import yaml
from pydantic import BaseModel

from src.guards.memory_guard import MemoryGuard, ScanResult
from src.logging.state_mutation_log import MutationType, StateMutationLog
from src.memory.candidate import CompiledMemoryPatch
from src.memory.manifest_store import ManifestEntry, ManifestStore
from src.memory.memory_schema import MemorySchema, PAGE_SECTIONS

logger = logging.getLogger("lapwing.memory.wiki_store")


# ── Errors ──────────────────────────────────────────────────────────


class MemoryGuardBlocked(Exception):
    """Raised when MemoryGuard rejects patch content."""

    def __init__(self, threats: list[str]) -> None:
        super().__init__("memory_guard blocked patch: " + "; ".join(threats))
        self.threats = threats


class HashMismatch(Exception):
    """Optimistic-lock failure on apply_patch."""


class WikiWriteDisabled(Exception):
    """Raised when a write is attempted with write_enabled=False."""


# ── Page model ──────────────────────────────────────────────────────


class WikiPageMeta(BaseModel):
    id: str
    type: str
    title: str
    status: str = "active"
    confidence: float | None = None
    updated_at: str
    path: str


class WikiPage(WikiPageMeta):
    frontmatter: dict[str, Any]
    body: str
    sections: dict[str, str]


# ── Store ───────────────────────────────────────────────────────────


class WikiStore:
    """All wiki writes funnel through here."""

    def __init__(
        self,
        wiki_dir: str | Path,
        *,
        db_path: str | Path,
        manifest: ManifestStore | None = None,
        mutation_log: StateMutationLog | None = None,
        schema: MemorySchema | None = None,
        memory_guard: MemoryGuard | None = None,
        write_enabled_provider=None,  # callable[[], bool]
    ) -> None:
        self._wiki_dir = Path(wiki_dir)
        self._db_path = Path(db_path)
        self._manifest = manifest
        self._mutation_log = mutation_log
        self._schema = schema or MemorySchema()
        self._guard = memory_guard or MemoryGuard()
        self._db: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()
        self._write_enabled_provider = (
            write_enabled_provider if write_enabled_provider is not None
            else (lambda: False)
        )

    # ── Lifecycle ───────────────────────────────────────────────────

    async def init(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS memory_patches (
                id              TEXT PRIMARY KEY,
                candidate_id    TEXT,
                target_page_id  TEXT NOT NULL,
                target_path     TEXT NOT NULL,
                operation       TEXT NOT NULL,
                risk            TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'pending',
                patch_json      TEXT NOT NULL,
                reason          TEXT,
                created_at      TEXT NOT NULL,
                applied_at      TEXT,
                rejected_reason TEXT,
                last_error      TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_patches_status ON memory_patches(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_patches_target ON memory_patches(target_page_id);
            CREATE INDEX IF NOT EXISTS idx_patches_risk ON memory_patches(risk, status);
            """
        )
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    # ── Read ────────────────────────────────────────────────────────

    async def get_page(self, page_id: str) -> WikiPage | None:
        path = self._page_path(page_id)
        if path is None or not path.exists():
            return None
        return self._load_page(path)

    async def list_pages(
        self,
        *,
        type_filter: str | None = None,
        status_filter: str | None = None,
    ) -> list[WikiPageMeta]:
        out: list[WikiPageMeta] = []
        for path in sorted(self._wiki_dir.rglob("*.md")):
            try:
                page = self._load_page(path)
            except Exception:  # noqa: BLE001
                continue
            if page is None:
                continue
            if type_filter and page.type != type_filter:
                continue
            if status_filter and page.status != status_filter:
                continue
            out.append(WikiPageMeta(
                id=page.id, type=page.type, title=page.title,
                status=page.status, confidence=page.confidence,
                updated_at=page.updated_at, path=page.path,
            ))
        return out

    # ── Write ───────────────────────────────────────────────────────

    async def apply_patch(self, patch: CompiledMemoryPatch) -> bool:
        """Apply a patch atomically. See module docstring for the
        10-step pipeline. Returns True on apply, False if the patch
        was diverted to the pending queue (high risk / guard block)."""
        if not self._write_enabled_provider():
            raise WikiWriteDisabled("memory.wiki.write_enabled is false")

        # 2. risk gate: high-risk goes to the audit queue, not auto-apply
        if patch.risk == "high":
            await self.record_pending_patch(patch, reason="high_risk")
            return False

        # 3. MemoryGuard scan — blocked patches get logged + queued
        scan: ScanResult = self._guard.scan(patch.content)
        if not scan.passed:
            blocked_patch = patch.model_copy(update={"risk": "high"})
            patch_id = await self.record_pending_patch(
                blocked_patch, reason="guard_blocked"
            )
            await self._emit(
                MutationType.MEMORY_WIKI_GUARD_BLOCKED,
                {
                    "patch_id": patch_id,
                    "candidate_id": patch.candidate_id,
                    "target_page_id": patch.target_page_id,
                    "operation": patch.operation,
                    "threats": scan.threats,
                },
            )
            raise MemoryGuardBlocked(scan.threats)

        async with self._write_lock:
            # 4 + 5: read existing + before_hash check
            target_path = Path(patch.target_path)
            if not target_path.is_absolute():
                target_path = (self._wiki_dir.parent.parent / patch.target_path).resolve()
            existing_text = ""
            if patch.operation != "create":
                if not target_path.exists():
                    raise FileNotFoundError(
                        f"target page does not exist: {target_path}"
                    )
                existing_text = target_path.read_text(encoding="utf-8")
                actual_hash = _sha256(existing_text)
                if patch.before_hash and actual_hash != patch.before_hash:
                    raise HashMismatch(
                        f"page drifted since patch was generated "
                        f"(expected {patch.before_hash[:8]}, got {actual_hash[:8]})"
                    )

            # 6 + 7: apply + validate
            new_text = self._apply_operation(patch, existing_text)
            errors = self._schema.validate_text(new_text)
            schema_errors = [e for e in errors if e.severity == "error"]
            if schema_errors:
                msg = "; ".join(f"{e.field}: {e.message}" for e in schema_errors)
                raise ValueError(f"patch produces invalid frontmatter: {msg}")

            # 8: atomic write
            after_hash = _sha256(new_text)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write(target_path, new_text)

            # 10a: mutation events
            is_create = patch.operation == "create" or not existing_text
            event = (
                MutationType.MEMORY_WIKI_PAGE_CREATED if is_create
                else MutationType.MEMORY_WIKI_PAGE_UPDATED
            )
            await self._emit(
                event,
                {
                    "page_id": patch.target_page_id,
                    "operation": patch.operation,
                    "section": patch.section,
                    "before_hash": patch.before_hash,
                    "after_hash": after_hash,
                    "candidate_id": patch.candidate_id,
                    "source_ids": patch.source_ids,
                },
            )
            await self._emit(
                MutationType.MEMORY_WIKI_PATCH_APPLIED,
                {
                    "candidate_id": patch.candidate_id,
                    "target_page_id": patch.target_page_id,
                    "operation": patch.operation,
                    "after_hash": after_hash,
                    "reason": patch.reason,
                },
            )

            # 10b: manifest provenance
            if self._manifest is not None and patch.source_ids:
                for src in patch.source_ids:
                    await self._manifest.record_processing(ManifestEntry(
                        source_id=src,
                        source_type="trajectory",
                        source_hash=patch.before_hash or after_hash,
                        processed_at=_utc_now(),
                        output_page_ids=[patch.target_page_id],
                        dirty_entities=[],
                        gate_decision="accept",
                    ))

            # 10c: changelog mirror (best-effort)
            self._append_changelog(
                f"{event.value} {patch.target_page_id}: {patch.reason}"
            )
        return True

    # ── Pending queue ───────────────────────────────────────────────

    async def record_pending_patch(
        self, patch: CompiledMemoryPatch, *, reason: str = "",
    ) -> str:
        """Persist a patch into the pending queue. Returns the patch row id."""
        assert self._db is not None, "WikiStore.init() not called"
        patch_id = f"patch:{uuid.uuid4().hex[:8]}"
        now = _utc_now()
        await self._db.execute(
            """
            INSERT INTO memory_patches (
                id, candidate_id, target_page_id, target_path,
                operation, risk, status, patch_json, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (
                patch_id,
                patch.candidate_id,
                patch.target_page_id,
                patch.target_path,
                patch.operation,
                patch.risk,
                patch.model_dump_json(),
                reason or patch.reason,
                now,
            ),
        )
        await self._db.commit()
        await self._emit(
            MutationType.MEMORY_WIKI_PATCH_CREATED,
            {
                "patch_id": patch_id,
                "candidate_id": patch.candidate_id,
                "target_page_id": patch.target_page_id,
                "risk": patch.risk,
                "reason": reason or patch.reason,
            },
        )
        return patch_id

    async def list_pending_patches(
        self, *, risk: str | None = None,
    ) -> list[CompiledMemoryPatch]:
        assert self._db is not None
        if risk:
            sql = (
                "SELECT patch_json FROM memory_patches "
                "WHERE status='pending' AND risk=? ORDER BY created_at"
            )
            params: tuple[Any, ...] = (risk,)
        else:
            sql = (
                "SELECT patch_json FROM memory_patches "
                "WHERE status='pending' ORDER BY created_at"
            )
            params = ()
        async with self._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [CompiledMemoryPatch.model_validate_json(r[0]) for r in rows]

    async def mark_patch_applied(self, patch_id: str) -> None:
        assert self._db is not None
        await self._db.execute(
            """
            UPDATE memory_patches SET status='applied', applied_at=?
             WHERE id=?
            """,
            (_utc_now(), patch_id),
        )
        await self._db.commit()

    async def reject_patch(self, patch_id: str, reason: str) -> None:
        assert self._db is not None
        await self._db.execute(
            """
            UPDATE memory_patches SET status='rejected', rejected_reason=?
             WHERE id=?
            """,
            (reason, patch_id),
        )
        await self._db.commit()
        await self._emit(
            MutationType.MEMORY_WIKI_PATCH_REJECTED,
            {"patch_id": patch_id, "reason": reason},
        )

    # ── History / provenance ────────────────────────────────────────

    async def get_provenance(self, page_id: str) -> list[ManifestEntry]:
        if self._manifest is None:
            return []
        return await self._manifest.get_provenance(page_id)

    async def get_page_history(self, page_id: str) -> list[dict[str, Any]]:
        """Return all wiki mutation events for a page (as raw payloads).

        The mutation_log is the source of truth; we surface a plain list
        here so callers don't have to know the schema.
        """
        if self._mutation_log is None:
            return []
        # Public StateMutationLog API doesn't expose a generic "by event
        # type + filter" query; this is a thin wrapper for ergonomics.
        # We rely on the underlying SQLite directly.
        history: list[dict[str, Any]] = []
        if getattr(self._mutation_log, "_db", None) is None:
            return history
        async with self._mutation_log._db.execute(  # type: ignore[union-attr]
            """
            SELECT timestamp, event_type, payload_json
              FROM mutations
             WHERE event_type LIKE 'memory.wiki_%'
             ORDER BY timestamp
            """,
        ) as cur:
            for ts, event_type, payload_json in await cur.fetchall():
                try:
                    payload = json.loads(payload_json)
                except json.JSONDecodeError:
                    continue
                if payload.get("page_id") == page_id or payload.get("target_page_id") == page_id:
                    history.append({
                        "timestamp": ts,
                        "event_type": event_type,
                        "payload": payload,
                    })
        return history

    # ── Operation kernel ────────────────────────────────────────────

    def _apply_operation(
        self, patch: CompiledMemoryPatch, existing_text: str,
    ) -> str:
        if patch.operation == "create":
            return patch.content if patch.content.endswith("\n") else patch.content + "\n"

        fm, body = self._schema.parse(existing_text)
        if not fm:
            raise ValueError(f"target page has no frontmatter: {patch.target_path}")

        sections = self._schema.extract_sections(body)

        if patch.operation == "update_section":
            if not patch.section:
                raise ValueError("update_section requires patch.section")
            sections[patch.section] = patch.content.strip()
        elif patch.operation == "add_fact":
            existing = sections.get("Stable facts", "").strip()
            line = patch.content.strip()
            if not line.startswith("-"):
                line = f"- {line}"
            sections["Stable facts"] = (
                line if not existing or _is_placeholder(existing)
                else existing + "\n" + line
            )
        elif patch.operation == "supersede_fact":
            # Move the named fact from Stable facts → Superseded notes
            target_text = patch.content.strip()
            stable = sections.get("Stable facts", "").strip()
            kept_lines: list[str] = []
            superseded_line: str | None = None
            for ln in stable.splitlines():
                if ln.strip().lstrip("- ").startswith(target_text):
                    superseded_line = ln
                else:
                    kept_lines.append(ln)
            if superseded_line is None:
                raise ValueError(
                    f"supersede_fact: cannot find fact starting with "
                    f"{target_text!r} in Stable facts"
                )
            sections["Stable facts"] = "\n".join(kept_lines).strip() or "（暂无）"
            old_super = sections.get("Superseded notes", "").strip()
            note = f"{superseded_line.strip()} — {patch.reason}".strip()
            sections["Superseded notes"] = (
                note if not old_super or _is_placeholder(old_super)
                else old_super + "\n" + note
            )
        elif patch.operation == "add_relation":
            relations = fm.get("relations") or []
            if not isinstance(relations, list):
                relations = []
            try:
                rel = json.loads(patch.content)
            except json.JSONDecodeError:
                raise ValueError("add_relation: patch.content must be JSON")
            if not isinstance(rel, dict) or "type" not in rel or "target" not in rel:
                raise ValueError("add_relation: payload must have type + target")
            if rel not in relations:
                relations.append(rel)
            fm["relations"] = relations
        else:
            raise ValueError(f"unknown operation: {patch.operation}")

        fm["updated_at"] = _utc_now()
        return _render_page(fm, patch.target_page_id, sections)

    # ── Helpers ─────────────────────────────────────────────────────

    def _page_path(self, page_id: str) -> Path | None:
        if "." not in page_id:
            return None
        ns, slug = page_id.split(".", 1)
        slug = slug.replace("/", "-")
        if ns == "entity":
            return self._wiki_dir / "entities" / f"{slug}.md"
        if ns == "knowledge":
            return self._wiki_dir / "knowledge" / f"{slug}.md"
        if ns == "meta":
            return self._wiki_dir / "_meta" / f"{slug}.md"
        return None

    def _load_page(self, path: Path) -> WikiPage | None:
        text = path.read_text(encoding="utf-8")
        fm, body = self._schema.parse(text)
        if not fm:
            return None
        sections = self._schema.extract_sections(body)
        return WikiPage(
            id=fm.get("id", ""),
            type=fm.get("type", ""),
            title=fm.get("title", ""),
            status=fm.get("status", "active"),
            confidence=fm.get("confidence"),
            updated_at=fm.get("updated_at", ""),
            path=str(path),
            frontmatter=fm,
            body=body,
            sections=sections,
        )

    def _atomic_write(self, target_path: Path, content: str) -> None:
        tmp = target_path.with_suffix(target_path.suffix + f".tmp-{uuid.uuid4().hex[:6]}")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(target_path)

    def _append_changelog(self, line: str) -> None:
        log = self._wiki_dir / "_meta" / "changelog.md"
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as fh:
                fh.write(f"- {_utc_now()} — {line}\n")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[wiki_store] changelog write failed: %s", exc)

    async def _emit(self, event: MutationType, payload: dict[str, Any]) -> None:
        if self._mutation_log is None:
            return
        try:
            await self._mutation_log.record(event, payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[wiki_store] mutation_log emit failed: %s", exc)


# ── Free helpers ────────────────────────────────────────────────────


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_PLACEHOLDER_RE = re.compile(r"^[（(]\s*暂无\s*[)）]\s*$")


def _is_placeholder(text: str) -> bool:
    return bool(_PLACEHOLDER_RE.match(text.strip()))


def _render_page(
    frontmatter: dict[str, Any],
    page_id: str,
    sections: dict[str, str],
) -> str:
    fm_yaml = yaml.safe_dump(
        frontmatter, sort_keys=False, allow_unicode=True,
    )
    parts = [f"---\n{fm_yaml}---\n", f"# {frontmatter.get('title', page_id)}\n"]
    for name in PAGE_SECTIONS:
        body = sections.get(name, "（暂无）").strip() or "（暂无）"
        parts.append(f"\n## {name}\n\n{body}\n")
    # Forward-compat: preserve any extra sections not in PAGE_SECTIONS
    for name, body in sections.items():
        if name in PAGE_SECTIONS:
            continue
        body = body.strip() or "（暂无）"
        parts.append(f"\n## {name}\n\n{body}\n")
    return "".join(parts)
