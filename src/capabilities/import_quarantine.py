"""External capability import into quarantined storage.

Phase 7A: local filesystem package paths only. No network, no execution,
no promotion, no active indexing. Imported capabilities land in quarantine
with status=quarantined, maturity=draft, and are excluded from default
search/retrieval.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.capabilities.errors import CapabilityError
from src.capabilities.schema import (
    CapabilityMaturity,
    CapabilityScope,
    CapabilityStatus,
)

if TYPE_CHECKING:
    from src.capabilities.document import CapabilityDocument
    from src.capabilities.evaluator import CapabilityEvaluator
    from src.capabilities.index import CapabilityIndex
    from src.capabilities.policy import CapabilityPolicy
    from src.capabilities.store import CapabilityStore

logger = logging.getLogger(__name__)


@dataclass
class InspectResult:
    """Result of inspecting an external capability package. No writes."""

    id: str
    name: str
    description: str
    type: str
    declared_scope: str
    target_scope: str
    maturity: str
    status: str
    risk_level: str
    required_tools: list[str] = field(default_factory=list)
    required_permissions: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    files: dict[str, list[str]] = field(default_factory=dict)
    eval_findings: list[dict] = field(default_factory=list)
    eval_passed: bool = True
    eval_score: float = 1.0
    policy_findings: list[dict] = field(default_factory=list)
    would_import: bool = False
    quarantine_reason: str = ""
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class ImportResult:
    """Result of importing an external capability package."""

    capability_id: str
    quarantine_path: str
    import_report_path: str
    dry_run: bool = False
    applied: bool = False
    inspect_result: InspectResult | None = None
    errors: list[str] = field(default_factory=list)


def _validate_source_path(path: Path) -> None:
    """Validate that a source path is safe for import.

    Rejects: non-existent paths, non-directories, symlinks, path traversal,
    and remote-looking paths.
    """
    if not path.exists():
        raise CapabilityError(f"Source path does not exist: {path}")

    if not path.is_dir():
        raise CapabilityError(f"Source path is not a directory: {path}")

    # Reject symlinks in v0
    resolved = path.resolve()
    if resolved != path.resolve():
        pass  # resolve is idempotent
    try:
        if path.is_symlink() or not path.samefile(resolved):
            raise CapabilityError(f"Symlinks are not accepted for import: {path}")
    except OSError:
        raise CapabilityError(f"Cannot resolve source path: {path}")

    # Reject remote-looking paths
    path_str = str(path)
    if "://" in path_str:
        raise CapabilityError("Remote paths (containing ://) are not accepted in Phase 7A")

    # Reject paths that traverse outside their own tree via .. after resolve
    resolved_str = str(resolved)
    if "/../" in resolved_str or resolved_str.endswith("/.."):
        raise CapabilityError("Path traversal patterns are not accepted")


def _scan_files(directory: Path) -> dict[str, list[str]]:
    """Scan standard subdirectories and return file listings."""
    result: dict[str, list[str]] = {}
    for sub in ("scripts", "tests", "examples", "evals", "traces", "versions"):
        sub_path = directory / sub
        if sub_path.is_dir():
            names = sorted(p.name for p in sub_path.iterdir() if p.name not in (".gitkeep",))
            result[sub] = names
        else:
            result[sub] = []
    return result


def _hash_source_path(path: Path) -> str:
    """Return a SHA256 hash of the resolved source path."""
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()


def inspect_capability_package(
    *,
    path: str | Path,
    store: CapabilityStore,
    evaluator: CapabilityEvaluator,
    policy: CapabilityPolicy,
    target_scope: str = "user",
    include_files: bool = True,
) -> InspectResult:
    """Inspect an external capability package without writing anything.

    Parses the package, runs evaluator and policy checks, and returns
    a detailed findings report. Never copies files, writes to disk,
    updates indices, or executes scripts.
    """
    # Reject remote URLs before Path() normalises ://
    if isinstance(path, str) and "://" in path:
        raise CapabilityError("Remote paths (containing ://) are not accepted in Phase 7A")

    source_path = Path(path).resolve()
    _validate_source_path(source_path)

    parser = store._parser
    doc = parser.parse(source_path)
    manifest = doc.manifest

    # Run evaluator
    eval_record = evaluator.evaluate(doc)
    eval_findings = [
        {
            "severity": f.severity.value,
            "code": f.code,
            "message": f.message,
            "location": f.location or "",
        }
        for f in eval_record.findings
    ]

    # Run install policy
    install_decision = policy.validate_install(
        manifest,
        source="external_package",
        context={"source_path": str(source_path)},
    )
    create_decision = policy.validate_create(manifest)

    policy_findings = []
    for d in (install_decision, create_decision):
        if d.code and d.code not in ("install_allowed", "create_allowed", "policy_allow"):
            policy_findings.append({
                "severity": d.severity.value,
                "code": d.code,
                "message": d.message,
                "allowed": d.allowed,
                "details": d.details,
            })

    # Determine if import would be allowed
    errors: list[str] = []
    warnings: list[str] = []

    if not eval_record.passed:
        for f in eval_record.findings:
            if f.severity.value == "error":
                errors.append(f"[eval] {f.code}: {f.message}")

    if not install_decision.allowed:
        errors.append(f"[policy] {install_decision.code}: {install_decision.message}")
    elif install_decision.severity.value == "warning":
        warnings.append(f"[policy] {install_decision.code}: {install_decision.message}")

    if not create_decision.allowed:
        errors.append(f"[policy] {create_decision.code}: {create_decision.message}")

    # Quarantine reason
    quarantine_reason = "External package imported from local filesystem — quarantined pending audit"

    # Additional risk-based reasons
    if manifest.risk_level.value == "high":
        quarantine_reason = (
            "High-risk external package — quarantined pending security review "
            "and explicit approval"
        )
    if eval_record.findings:
        error_codes = [f.code for f in eval_record.findings if f.severity.value == "error"]
        if error_codes:
            quarantine_reason += f". Eval errors: {', '.join(error_codes)}"

    would_import = len(errors) == 0

    files = _scan_files(source_path) if include_files else {}

    return InspectResult(
        id=manifest.id,
        name=manifest.name,
        description=manifest.description,
        type=manifest.type.value,
        declared_scope=manifest.scope.value,
        target_scope=target_scope,
        maturity=CapabilityMaturity.DRAFT.value,
        status=CapabilityStatus.QUARANTINED.value,
        risk_level=manifest.risk_level.value,
        required_tools=list(manifest.required_tools),
        required_permissions=list(manifest.required_permissions),
        triggers=list(manifest.triggers),
        tags=list(manifest.tags),
        files=files,
        eval_findings=eval_findings,
        eval_passed=eval_record.passed,
        eval_score=eval_record.score,
        policy_findings=policy_findings,
        would_import=would_import,
        quarantine_reason=quarantine_reason,
        warnings=warnings,
        errors=errors,
    )


def import_capability_package(
    *,
    path: str | Path,
    store: CapabilityStore,
    evaluator: CapabilityEvaluator,
    policy: CapabilityPolicy,
    index: CapabilityIndex | None = None,
    target_scope: str = "user",
    imported_by: str | None = None,
    reason: str | None = None,
    dry_run: bool = False,
) -> ImportResult:
    """Import an external capability package into quarantined storage.

    If dry_run=True, performs the same inspection as inspect_capability_package
    without writing anything.

    Otherwise:
    1. Inspects the package
    2. If invalid, returns clean denial
    3. Checks for duplicate IDs
    4. Copies to quarantine directory
    5. Forces status=quarantined, maturity=draft
    6. Writes import_report.json
    7. Indexes with status=quarantined
    """
    # Reject remote URLs before Path() normalises ://
    if isinstance(path, str) and "://" in path:
        raise CapabilityError("Remote paths (containing ://) are not accepted in Phase 7A")

    source_path = Path(path).resolve()
    _validate_source_path(source_path)

    # 1. Inspect
    inspect_result = inspect_capability_package(
        path=source_path,
        store=store,
        evaluator=evaluator,
        policy=policy,
        target_scope=target_scope,
        include_files=True,
    )

    if dry_run:
        return ImportResult(
            capability_id=inspect_result.id,
            quarantine_path="",
            import_report_path="",
            dry_run=True,
            applied=False,
            inspect_result=inspect_result,
            errors=inspect_result.errors,
        )

    if not inspect_result.would_import:
        return ImportResult(
            capability_id=inspect_result.id,
            quarantine_path="",
            import_report_path="",
            dry_run=False,
            applied=False,
            inspect_result=inspect_result,
            errors=inspect_result.errors,
        )

    cap_id = inspect_result.id

    # 2. Check for duplicate IDs
    # Check active store
    try:
        store.get(cap_id, scope=None)
        return ImportResult(
            capability_id=cap_id,
            quarantine_path="",
            import_report_path="",
            dry_run=False,
            applied=False,
            inspect_result=inspect_result,
            errors=[f"Capability '{cap_id}' already exists as active — cannot overwrite"],
        )
    except CapabilityError:
        pass

    # Check quarantine dir for existing
    quarantine_root = store.data_dir / "quarantine"
    quarantine_dir = quarantine_root / cap_id
    if quarantine_dir.exists():
        return ImportResult(
            capability_id=cap_id,
            quarantine_path="",
            import_report_path="",
            dry_run=False,
            applied=False,
            inspect_result=inspect_result,
            errors=[f"Capability '{cap_id}' already exists in quarantine — rejected for v0"],
        )

    # 3. Copy to quarantine
    quarantine_root.mkdir(parents=True, exist_ok=True)

    try:
        shutil.copytree(str(source_path), str(quarantine_dir), symlinks=False)
    except shutil.Error as e:
        return ImportResult(
            capability_id=cap_id,
            quarantine_path="",
            import_report_path="",
            dry_run=False,
            applied=False,
            inspect_result=inspect_result,
            errors=[f"Failed to copy package: {e}"],
        )

    # 4. Force status=quarantined, maturity=draft in manifest.json
    now = datetime.now(timezone.utc)
    normalized_manifest = {
        "id": cap_id,
        "name": inspect_result.name,
        "description": inspect_result.description,
        "type": inspect_result.type,
        "scope": target_scope,
        "version": "0.1.0",
        "maturity": CapabilityMaturity.DRAFT.value,
        "status": CapabilityStatus.QUARANTINED.value,
        "risk_level": inspect_result.risk_level,
        "trust_required": "developer",
        "required_tools": inspect_result.required_tools,
        "required_permissions": inspect_result.required_permissions,
        "triggers": inspect_result.triggers,
        "tags": inspect_result.tags,
    }
    manifest_path = quarantine_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(normalized_manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # 5. Re-parse to compute content hash after normalization
    parser = store._parser
    quarantined_doc = parser.parse(quarantine_dir)

    # 6. Write import_report.json
    source_path_hash = _hash_source_path(source_path)
    import_report = {
        "capability_id": cap_id,
        "source_type": "local_package",
        "source_path_hash": source_path_hash,
        "imported_at": now.isoformat(),
        "imported_by": imported_by,
        "original_content_hash": quarantined_doc.content_hash,
        "quarantine_reason": reason or inspect_result.quarantine_reason,
        "target_scope": target_scope,
        "eval_passed": inspect_result.eval_passed,
        "eval_score": inspect_result.eval_score,
        "eval_findings": inspect_result.eval_findings,
        "policy_findings": inspect_result.policy_findings,
        "files_summary": inspect_result.files,
    }
    report_path = quarantine_dir / "import_report.json"
    report_path.write_text(
        json.dumps(import_report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # 6b. Write provenance.json (Phase 8A-1)
    # Fail-closed: if provenance write fails, clean up quarantine and reject.
    try:
        from src.capabilities.provenance import write_provenance, compute_package_hash

        source_content_hash = compute_package_hash(source_path)
        write_provenance(
            quarantine_dir,
            capability_id=cap_id,
            source_type="local_package",
            source_path_hash=source_path_hash,
            source_content_hash=source_content_hash,
            imported_at=now.isoformat(),
            imported_by=imported_by,
            trust_level="untrusted",
            integrity_status="verified",
            signature_status="not_present",
            metadata={"import_report_id": "import_report.json"},
        )
    except Exception:
        shutil.rmtree(quarantine_dir)
        return ImportResult(
            capability_id=cap_id,
            quarantine_path="",
            import_report_path="",
            dry_run=False,
            applied=False,
            inspect_result=inspect_result,
            errors=[f"Failed to write provenance record for '{cap_id}' — import rejected"],
        )

    # 7. Index with quarantined status (excluded by default from search/retrieval)
    if index is not None:
        try:
            index.upsert(quarantined_doc)
        except Exception:
            logger.debug("Failed to index quarantined capability %s", cap_id, exc_info=True)

    logger.info(
        "Imported capability '%s' into quarantine: %s",
        cap_id, quarantine_dir,
    )

    return ImportResult(
        capability_id=cap_id,
        quarantine_path=str(quarantine_dir),
        import_report_path=str(report_path),
        dry_run=False,
        applied=True,
        inspect_result=inspect_result,
        errors=[],
    )
