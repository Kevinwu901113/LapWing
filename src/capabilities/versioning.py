"""Version snapshots for capability state transitions.

Creates lightweight metadata snapshots before destructive operations
(disable, archive) so changes can be audited and later rolled back.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .document import CapabilityDocument


@dataclass
class VersionSnapshot:
    version: str
    snapshot_at: str       # ISO 8601
    content_hash: str
    trigger: str           # "disabled" | "archived" | "manual"
    reason: str
    snapshot_dir: str      # relative path to snapshot directory


_VERSIONS_DIR = "versions"


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")


def _snapshot_dir_name(doc: CapabilityDocument) -> str:
    return f"v{doc.manifest.version}_{_timestamp()}"


def create_version_snapshot(
    doc: CapabilityDocument,
    trigger: str,
    *,
    reason: str = "",
) -> VersionSnapshot:
    cap_dir = doc.directory
    versions_dir = cap_dir / _VERSIONS_DIR
    versions_dir.mkdir(parents=True, exist_ok=True)

    snap_dir_name = _snapshot_dir_name(doc)
    snap_dir = versions_dir / snap_dir_name
    snap_dir.mkdir()

    shutil.copy2(str(cap_dir / "CAPABILITY.md"), str(snap_dir / "CAPABILITY.md"))
    manifest_path = cap_dir / "manifest.json"
    if manifest_path.exists():
        shutil.copy2(str(manifest_path), str(snap_dir / "manifest.json"))
    else:
        # Reconstruct manifest.json from the parsed document
        fm = {
            "id": doc.manifest.id,
            "name": doc.manifest.name,
            "description": doc.manifest.description,
            "type": doc.manifest.type.value,
            "scope": doc.manifest.scope.value,
            "version": doc.manifest.version,
            "maturity": doc.manifest.maturity.value,
            "status": doc.manifest.status.value,
            "risk_level": doc.manifest.risk_level.value,
            "trust_required": doc.manifest.trust_required,
            "required_tools": doc.manifest.required_tools,
            "required_permissions": doc.manifest.required_permissions,
            "triggers": doc.manifest.triggers,
            "tags": doc.manifest.tags,
            "content_hash": doc.content_hash,
            "created_at": doc.manifest.created_at.isoformat() if doc.manifest.created_at else "",
            "updated_at": doc.manifest.updated_at.isoformat() if doc.manifest.updated_at else "",
            **doc.manifest.extra,
        }
        (snap_dir / "manifest.json").write_text(
            json.dumps(fm, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    return VersionSnapshot(
        version=doc.manifest.version,
        snapshot_at=datetime.now(timezone.utc).isoformat(),
        content_hash=doc.content_hash,
        trigger=trigger,
        reason=reason,
        snapshot_dir=str(snap_dir.relative_to(cap_dir)),
    )


def list_version_snapshots(doc: CapabilityDocument) -> list[VersionSnapshot]:
    versions_dir = doc.directory / _VERSIONS_DIR
    if not versions_dir.is_dir():
        return []

    snapshots: list[VersionSnapshot] = []
    for entry in sorted(versions_dir.iterdir(), reverse=True):
        if not entry.is_dir():
            continue
        name = entry.name
        if not name.startswith("v"):
            continue
        manifest_file = entry / "manifest.json"
        version = ""
        content_hash = ""
        if manifest_file.exists():
            try:
                data = json.loads(manifest_file.read_text(encoding="utf-8"))
                version = data.get("version", "")
                content_hash = data.get("content_hash", "")
            except (json.JSONDecodeError, OSError):
                pass

        snapshot_at = name.rsplit("_", 1)[-1] if "_" in name else ""
        snapshots.append(VersionSnapshot(
            version=version,
            snapshot_at=snapshot_at,
            content_hash=content_hash,
            trigger="",
            reason="",
            snapshot_dir=str(entry.relative_to(doc.directory)),
        ))

    return snapshots


def snapshot_on_disable(doc: CapabilityDocument, *, reason: str = "") -> VersionSnapshot:
    return create_version_snapshot(doc, "disabled", reason=reason)


def snapshot_on_archive(doc: CapabilityDocument, *, reason: str = "") -> VersionSnapshot:
    return create_version_snapshot(doc, "archived", reason=reason)
