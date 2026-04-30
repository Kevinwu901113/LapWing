"""Filesystem-backed CapabilityStore.

Creates, reads, lists, searches, disables, and archives capability
directories.  Not wired into any runtime path.

Directory layout::

    <data_dir>/
      global/       <scope>/<capability_id>/CAPABILITY.md
      user/                                   manifest.json
      workspace/                              versions/
      session/                                scripts/ ...
      archived/<scope>/<capability_id>/
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from .document import STANDARD_DIRS, CapabilityDocument, CapabilityParser
from .errors import CapabilityError, InvalidDocumentError
from .ids import generate_capability_id
from .schema import (
    CapabilityScope,
    CapabilityStatus,
)

if TYPE_CHECKING:
    from .index import CapabilityIndex

logger = logging.getLogger(__name__)

SCOPE_PRECEDENCE: list[CapabilityScope] = [
    CapabilityScope.SESSION,
    CapabilityScope.WORKSPACE,
    CapabilityScope.USER,
    CapabilityScope.GLOBAL,
]


class CapabilityStore:
    """Filesystem-backed CRUD for capability directories."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        mutation_log: object | None = None,
        index: "CapabilityIndex | None" = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self._mutation_log = mutation_log
        self._index = index
        self._parser = CapabilityParser()

    # ── internal helpers ───────────────────────────────────────

    def _scope_dir(self, scope: CapabilityScope) -> Path:
        return self.data_dir / scope.value

    def _archive_dir(self, scope: CapabilityScope) -> Path:
        return self.data_dir / "archived" / scope.value

    def _get_dir(self, cap_id: str, scope: CapabilityScope) -> Path:
        return self._scope_dir(scope) / cap_id

    def _write_capability_md(self, directory: Path, front_matter: dict, body: str) -> None:
        fm_yaml = yaml.dump(front_matter, allow_unicode=True, sort_keys=False).strip()
        md = f"---\n{fm_yaml}\n---\n\n{body}"
        (directory / "CAPABILITY.md").write_text(md, encoding="utf-8")

    def _write_manifest_json(self, directory: Path, data: dict) -> None:
        (directory / "manifest.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _sync_manifest_json(self, directory: Path, doc: CapabilityDocument) -> None:
        m = doc.manifest
        data = {
            "id": m.id,
            "name": m.name,
            "description": m.description,
            "type": m.type.value,
            "scope": m.scope.value,
            "version": m.version,
            "maturity": m.maturity.value,
            "status": m.status.value,
            "risk_level": m.risk_level.value,
            "trust_required": m.trust_required,
            "required_tools": m.required_tools,
            "required_permissions": m.required_permissions,
            "triggers": m.triggers,
            "tags": m.tags,
            "content_hash": doc.content_hash,
            "created_at": m.created_at.isoformat() if m.created_at else "",
            "updated_at": m.updated_at.isoformat() if m.updated_at else "",
            **m.extra,
        }
        self._write_manifest_json(directory, data)

    def _maybe_index(self, doc: CapabilityDocument) -> None:
        if self._index is not None:
            self._index.upsert(doc)

    def _maybe_record(self, event_type_str: str, payload: dict) -> None:
        if self._mutation_log is None:
            return
        try:
            record = getattr(self._mutation_log, "record", None)
            if callable(record):
                record(event_type_str, payload)
        except Exception:
            logger.debug("mutation_log record failed", exc_info=True)

    def _ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        for s in CapabilityScope:
            self._scope_dir(s).mkdir(parents=True, exist_ok=True)

    def _iter_all_dirs(self):
        """Yield all CapabilityDocuments from the filesystem."""
        for scope in CapabilityScope:
            scope_dir = self._scope_dir(scope)
            if not scope_dir.is_dir():
                continue
            for entry in sorted(scope_dir.iterdir()):
                if not entry.is_dir():
                    continue
                if not (entry / "CAPABILITY.md").exists():
                    continue
                try:
                    yield self._parser.parse(entry)
                except Exception:
                    logger.debug("Skipping invalid capability at %s", entry, exc_info=True)

    def _resolve_dir(self, cap_id: str, scope: CapabilityScope | None) -> tuple[Path, CapabilityScope]:
        if scope is not None:
            d = self._get_dir(cap_id, scope)
            if d.is_dir() and (d / "CAPABILITY.md").exists():
                return d, scope
            raise InvalidDocumentError(f"Capability '{cap_id}' not found in scope '{scope.value}'")

        for s in SCOPE_PRECEDENCE:
            d = self._get_dir(cap_id, s)
            if d.is_dir() and (d / "CAPABILITY.md").exists():
                return d, s

        raise InvalidDocumentError(f"Capability '{cap_id}' not found in any scope")

    def _write_doc_files(self, cap_dir: Path, front_matter: dict, body: str, scope: CapabilityScope) -> None:
        cap_dir.mkdir(parents=True)
        for sd in STANDARD_DIRS:
            (cap_dir / sd).mkdir(exist_ok=True)

        self._write_capability_md(cap_dir, front_matter, body)

        manifest_json_data = {
            "id": front_matter["id"],
            "name": front_matter["name"],
            "description": front_matter["description"],
            "type": front_matter["type"],
            "scope": scope.value,
            "version": front_matter.get("version", "0.1.0"),
            "maturity": "draft",
            "status": "active",
            "risk_level": front_matter.get("risk_level", "low"),
            "trust_required": front_matter.get("trust_required", "developer"),
            "required_tools": front_matter.get("required_tools", []),
            "required_permissions": front_matter.get("required_permissions", []),
            "triggers": front_matter.get("triggers", []),
            "tags": front_matter.get("tags", []),
        }
        self._write_manifest_json(cap_dir, manifest_json_data)

    # ── create_draft ───────────────────────────────────────────

    def create_draft(
        self,
        scope: CapabilityScope,
        *,
        cap_id: str | None = None,
        name: str,
        description: str,
        type: str = "skill",
        body: str = "",
        version: str = "0.1.0",
        risk_level: str = "low",
        tags: list[str] | None = None,
        triggers: list[str] | None = None,
        trust_required: str = "developer",
        required_tools: list[str] | None = None,
        required_permissions: list[str] | None = None,
        **extra,
    ) -> CapabilityDocument:
        cap_id = cap_id or generate_capability_id(scope.value)
        cap_dir = self._get_dir(cap_id, scope)

        self._ensure_dirs()

        if cap_dir.exists():
            raise FileExistsError(f"Capability '{cap_id}' already exists in scope '{scope.value}'")

        front_matter = {
            "id": cap_id,
            "name": name,
            "description": description,
            "type": type,
            "scope": scope.value,
            "version": version,
            "maturity": "draft",
            "status": "active",
            "risk_level": risk_level,
            "trust_required": trust_required,
            "required_tools": required_tools or [],
            "required_permissions": required_permissions or [],
            "triggers": triggers or [],
            "tags": tags or [],
            **extra,
        }

        self._write_doc_files(cap_dir, front_matter, body, scope)
        doc = self._parser.parse(cap_dir)

        self._maybe_index(doc)
        self._maybe_record("capability.draft_created", {
            "capability_id": cap_id, "scope": scope.value, "name": name,
        })
        return doc

    # ── get ────────────────────────────────────────────────────

    def get(self, capability_id: str, scope: CapabilityScope | None = None) -> CapabilityDocument:
        cap_dir, _resolved_scope = self._resolve_dir(capability_id, scope)
        return self._parser.parse(cap_dir)

    # ── list ───────────────────────────────────────────────────

    def list(  # noqa: C901
        self,
        *,
        scope: CapabilityScope | None = None,
        type: str | None = None,
        maturity: str | None = None,
        status: str | None = None,
        risk_level: str | None = None,
        tags: list[str] | None = None,
        include_disabled: bool = False,
        include_archived: bool = False,
        limit: int = 100,
    ) -> list[CapabilityDocument]:
        scopes = [scope] if scope else list(CapabilityScope)
        results: list[CapabilityDocument] = []

        search_dirs: list[Path] = [self._scope_dir(s) for s in scopes]
        if include_archived:
            archive_root = self.data_dir / "archived"
            if archive_root.is_dir():
                for scope_dir in sorted(archive_root.iterdir()):
                    if scope_dir.is_dir():
                        search_dirs.append(scope_dir)

        for search_dir in search_dirs:
            if not search_dir.is_dir():
                continue
            for entry in sorted(search_dir.iterdir()):
                if not entry.is_dir() or not (entry / "CAPABILITY.md").exists():
                    continue
                if len(results) >= limit:
                    return results
                try:
                    doc = self._parser.parse(entry)
                except Exception:
                    continue

                if doc.manifest.status == CapabilityStatus.ARCHIVED and not include_archived:
                    continue
                if doc.manifest.status == CapabilityStatus.DISABLED and not include_disabled:
                    continue
                if doc.manifest.status not in (CapabilityStatus.ACTIVE, CapabilityStatus.DISABLED, CapabilityStatus.ARCHIVED):
                    if not include_disabled and not include_archived:
                        continue

                if type and doc.type.value != type:
                    continue
                if maturity and doc.manifest.maturity.value != maturity:
                    continue
                if status and doc.manifest.status.value != status:
                    continue
                if risk_level and doc.manifest.risk_level.value != risk_level:
                    continue
                if tags:
                    doc_tags = {t.lower() for t in doc.manifest.tags}
                    if not doc_tags.intersection(t.lower() for t in tags):
                        continue

                results.append(doc)

        return results

    # ── search ─────────────────────────────────────────────────

    def search(
        self,
        query: str | None = None,
        *,
        filters: dict | None = None,
        limit: int = 20,
    ) -> list[CapabilityDocument]:
        if self._index is not None:
            rows = self._index.search(query=query, filters=filters, limit=limit)
            docs: list[CapabilityDocument] = []
            for row in rows:
                dir_path = Path(row["path"])
                if dir_path.is_dir() and (dir_path / "CAPABILITY.md").exists():
                    try:
                        docs.append(self._parser.parse(dir_path))
                    except Exception:
                        continue
            return docs

        results = self.list(limit=1000)
        if query and query.strip():
            q = query.strip().lower()
            results = [
                d for d in results
                if q in d.name.lower()
                or q in d.manifest.description.lower()
                or any(q in t.lower() for t in d.manifest.triggers)
                or any(q in t.lower() for t in d.manifest.tags)
            ]
        if filters:
            for fld in ("scope", "type", "maturity", "status", "risk_level"):
                if fld in filters:
                    results = [d for d in results if getattr(d.manifest, fld).value == filters[fld]]
        return results[:limit]

    # ── disable ────────────────────────────────────────────────

    def disable(self, capability_id: str, scope: CapabilityScope | None = None) -> CapabilityDocument:
        cap_dir, resolved_scope = self._resolve_dir(capability_id, scope)
        doc = self._parser.parse(cap_dir)

        now = datetime.now(timezone.utc)
        updated_manifest = doc.manifest.model_copy(update={
            "status": CapabilityStatus.DISABLED,
            "updated_at": now,
        })
        doc.manifest = updated_manifest

        self._sync_manifest_json(cap_dir, doc)
        doc = self._parser.parse(cap_dir)

        self._maybe_index(doc)
        self._maybe_record("capability.disabled", {
            "capability_id": capability_id, "scope": resolved_scope.value,
        })
        return doc

    # ── archive ────────────────────────────────────────────────

    def archive(self, capability_id: str, scope: CapabilityScope | None = None) -> CapabilityDocument:
        cap_dir, resolved_scope = self._resolve_dir(capability_id, scope)
        doc = self._parser.parse(cap_dir)

        now = datetime.now(timezone.utc)
        updated_manifest = doc.manifest.model_copy(update={
            "status": CapabilityStatus.ARCHIVED,
            "updated_at": now,
        })
        doc.manifest = updated_manifest

        self._sync_manifest_json(cap_dir, doc)

        archive_scope_dir = self._archive_dir(resolved_scope)
        archive_scope_dir.mkdir(parents=True, exist_ok=True)
        archive_dir = archive_scope_dir / capability_id

        if archive_dir.exists():
            ts = now.strftime("%Y%m%dT%H%M%S")
            archive_dir = archive_scope_dir / f"{capability_id}_{ts}"

        shutil.move(str(cap_dir), str(archive_dir))

        self._maybe_index(doc)
        self._maybe_record("capability.archived", {
            "capability_id": capability_id,
            "scope": resolved_scope.value,
            "archived_to": str(archive_dir),
        })
        return self._parser.parse(archive_dir)

    # ── index management ───────────────────────────────────────

    def rebuild_index(self) -> int:
        if self._index is None:
            return 0
        return self._index.rebuild_from_store(self)

    def refresh_index_for(self, capability_id: str, scope: CapabilityScope | None = None) -> None:
        if self._index is None:
            return
        try:
            doc = self.get(capability_id, scope)
            self._index.upsert(doc)
        except CapabilityError:
            pass
