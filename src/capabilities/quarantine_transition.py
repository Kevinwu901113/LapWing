"""Phase 7C: Quarantine-to-testing transition request bridge.

Creates an explicit operator-only request object that records:
"a quarantined capability has passed review and may be considered for a
future transition into testing."

Hard constraints:
- Does NOT activate or promote quarantined capabilities.
- Does NOT move files from quarantine to user/workspace/global.
- Does NOT set status=active.
- Does NOT set maturity=testing directly.
- Does NOT execute scripts.
- Does NOT run imported tests.
- Does NOT implement run_capability.
- Does NOT make quarantined capabilities visible in default retrieval/StateView.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.capabilities.errors import CapabilityError
from src.capabilities.schema import (
    CapabilityMaturity,
    CapabilityStatus,
)

if TYPE_CHECKING:
    from src.capabilities.evaluator import CapabilityEvaluator
    from src.capabilities.policy import CapabilityPolicy

ALLOWED_TARGET_SCOPES = frozenset({"user", "workspace", "session", "global"})
ALLOWED_REQUEST_STATUSES = frozenset({"pending", "cancelled", "rejected", "superseded"})
ALLOWED_REVIEW_STATUSES = frozenset({"approved_for_testing", "needs_changes", "rejected"})
TARGET_MATURITY = "testing"


# ── Data model ────────────────────────────────────────────────────────


@dataclass
class QuarantineTransitionRequest:
    """A request to transition a quarantined capability into testing.

    This is a PURE REQUEST — it does not perform any lifecycle mutation.
    """

    request_id: str
    capability_id: str
    created_at: str
    requested_target_scope: str  # user, workspace, session, global
    requested_target_maturity: str  # always "testing" in Phase 7C
    status: str  # pending, cancelled, rejected, superseded
    reason: str
    risk_level: str
    required_approval: bool
    findings_summary: dict[str, Any] = field(default_factory=dict)
    content_hash_at_request: str = ""
    created_by: str | None = None
    source_review_id: str | None = None
    source_audit_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "capability_id": self.capability_id,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "source_review_id": self.source_review_id,
            "source_audit_id": self.source_audit_id,
            "requested_target_scope": self.requested_target_scope,
            "requested_target_maturity": self.requested_target_maturity,
            "status": self.status,
            "reason": self.reason,
            "risk_level": self.risk_level,
            "required_approval": self.required_approval,
            "findings_summary": self.findings_summary,
            "content_hash_at_request": self.content_hash_at_request,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QuarantineTransitionRequest":
        return cls(
            request_id=str(data["request_id"]),
            capability_id=str(data["capability_id"]),
            created_at=str(data.get("created_at", "")),
            created_by=data.get("created_by"),
            source_review_id=data.get("source_review_id"),
            source_audit_id=data.get("source_audit_id"),
            requested_target_scope=str(data.get("requested_target_scope", "user")),
            requested_target_maturity=str(data.get("requested_target_maturity", "testing")),
            status=str(data.get("status", "pending")),
            reason=str(data.get("reason", "")),
            risk_level=str(data.get("risk_level", "low")),
            required_approval=bool(data.get("required_approval", False)),
            findings_summary=data.get("findings_summary", {}),
            content_hash_at_request=str(data.get("content_hash_at_request", "")),
            metadata=data.get("metadata", {}),
        )

    def compact_summary(self) -> dict[str, Any]:
        """Return a compact summary suitable for list responses."""
        return {
            "request_id": self.request_id,
            "capability_id": self.capability_id,
            "status": self.status,
            "requested_target_scope": self.requested_target_scope,
            "requested_target_maturity": self.requested_target_maturity,
            "risk_level": self.risk_level,
            "required_approval": self.required_approval,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "reason": self.reason,
        }


# ── Path helpers ──────────────────────────────────────────────────────


def _validate_id_token(token: str) -> None:
    """Reject path traversal and invalid characters in IDs."""
    if not token or "/" in token or "\\" in token or ".." in token:
        raise CapabilityError(f"Invalid identifier: {token!r}")


def _quarantine_dir(store_data_dir: Path, capability_id: str) -> Path:
    _validate_id_token(capability_id)
    return store_data_dir / "quarantine" / capability_id


def _requests_dir(store_data_dir: Path, capability_id: str) -> Path:
    return _quarantine_dir(store_data_dir, capability_id) / "quarantine_transition_requests"


def _request_path(store_data_dir: Path, capability_id: str, request_id: str) -> Path:
    _validate_id_token(request_id)
    return _requests_dir(store_data_dir, capability_id) / f"{request_id}.json"


# ── Internal helpers ──────────────────────────────────────────────────


def _generate_request_id() -> str:
    return f"qtr_{uuid.uuid4().hex[:12]}"


def _latest_review(quarantine_dir: Path) -> dict[str, Any] | None:
    """Find the latest review decision in quarantine_reviews/."""
    reviews_dir = quarantine_dir / "quarantine_reviews"
    if not reviews_dir.is_dir():
        return None
    latest: tuple[str, dict[str, Any]] | None = None
    for rev_file in sorted(reviews_dir.iterdir()):
        if not rev_file.suffix == ".json":
            continue
        try:
            rev = json.loads(rev_file.read_text(encoding="utf-8"))
            created = rev.get("created_at", "")
            if latest is None or created > latest[0]:
                latest = (created, rev)
        except (json.JSONDecodeError, OSError):
            continue
    return latest[1] if latest else None


def _latest_audit(quarantine_dir: Path) -> dict[str, Any] | None:
    """Find the latest audit report in quarantine_audit_reports/."""
    audits_dir = quarantine_dir / "quarantine_audit_reports"
    if not audits_dir.is_dir():
        return None
    latest: tuple[str, dict[str, Any]] | None = None
    for audit_file in sorted(audits_dir.iterdir()):
        if not audit_file.suffix == ".json":
            continue
        try:
            audit = json.loads(audit_file.read_text(encoding="utf-8"))
            created = audit.get("created_at", "")
            if latest is None or created > latest[0]:
                latest = (created, audit)
        except (json.JSONDecodeError, OSError):
            continue
    return latest[1] if latest else None


def _read_manifest(quarantine_dir: Path) -> dict[str, Any] | None:
    """Read manifest.json from quarantine directory."""
    manifest_path = quarantine_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _read_import_report(quarantine_dir: Path) -> dict[str, Any] | None:
    """Read import_report.json from quarantine directory."""
    report_path = quarantine_dir / "import_report.json"
    if not report_path.is_file():
        return None
    try:
        return json.loads(report_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _get_content_hash(quarantine_dir: Path) -> str:
    """Compute a content hash of the quarantined capability for consistency checks."""
    from src.capabilities.document import CapabilityParser

    try:
        parser = CapabilityParser()
        doc = parser.parse(quarantine_dir)
        return doc.content_hash or ""
    except Exception:
        return ""


def _check_existing_pending(
    store_data_dir: Path, capability_id: str, target_scope: str
) -> bool:
    """Return True if there's already a pending request for this capability + target scope."""
    rdir = _requests_dir(store_data_dir, capability_id)
    if not rdir.is_dir():
        return False
    for req_file in rdir.iterdir():
        if not req_file.suffix == ".json":
            continue
        try:
            req = json.loads(req_file.read_text(encoding="utf-8"))
            if req.get("status") == "pending" and req.get("requested_target_scope") == target_scope:
                return True
        except (json.JSONDecodeError, OSError):
            continue
    return False


def _sanitize_for_findings_summary(report: dict[str, Any]) -> dict[str, Any]:
    """Extract safe fields from audit/review for findings_summary."""
    return {
        "passed": report.get("passed"),
        "risk_level": report.get("risk_level", ""),
        "recommended_review_status": report.get("recommended_review_status", ""),
        "finding_count": len(report.get("findings", [])),
        "error_count": sum(
            1 for f in report.get("findings", []) if f.get("severity") == "error"
        ),
        "warning_count": sum(
            1 for f in report.get("findings", []) if f.get("severity") == "warning"
        ),
        "remediation_count": len(report.get("remediation_suggestions", [])),
    }


# ── request_quarantine_testing_transition ─────────────────────────────


def request_quarantine_testing_transition(
    *,
    store_data_dir: Path,
    capability_id: str,
    requested_target_scope: str = "user",
    reason: str,
    evaluator: "CapabilityEvaluator",
    policy: "CapabilityPolicy",
    created_by: str | None = None,
    source_review_id: str | None = None,
    source_audit_id: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create a quarantine testing transition request.

    This is a PURE REQUEST — no lifecycle mutation occurs.
    Returns request metadata on success, or blocking reasons on failure.

    Required gates:
    - capability exists in quarantine
    - manifest.status == quarantined
    - manifest.maturity == draft
    - latest review_status == approved_for_testing
    - latest audit report exists
    - evaluator/policy install checks pass
    - no existing pending request for same capability + target scope
    - high-risk sets required_approval=true
    - scripts are not executed
    - tests are not run
    """
    _validate_id_token(capability_id)
    if requested_target_scope not in ALLOWED_TARGET_SCOPES:
        raise CapabilityError(
            f"Invalid target_scope: {requested_target_scope!r}. "
            f"Allowed: {sorted(ALLOWED_TARGET_SCOPES)}"
        )
    if not reason or not reason.strip():
        raise CapabilityError("reason is required")

    blocking: list[str] = []

    # Gate 1: capability exists in quarantine
    qdir = _quarantine_dir(store_data_dir, capability_id)
    if not qdir.is_dir():
        blocking.append(f"Quarantined capability not found: {capability_id}")

    if blocking:
        if dry_run:
            return {"would_create": False, "blocking_reasons": blocking}
        raise CapabilityError(f"Cannot create transition request: {'; '.join(blocking)}")

    # Read manifest
    manifest = _read_manifest(qdir)
    if manifest is None:
        blocking.append("Cannot read manifest.json")

    # Gate 2: manifest.status == quarantined
    actual_status = manifest.get("status", "") if manifest else ""
    if actual_status != CapabilityStatus.QUARANTINED.value:
        blocking.append(
            f"Capability status must be 'quarantined', got '{actual_status}'"
        )

    # Gate 3: manifest.maturity == draft
    actual_maturity = manifest.get("maturity", "") if manifest else ""
    if actual_maturity != CapabilityMaturity.DRAFT.value:
        blocking.append(
            f"Capability maturity must be 'draft', got '{actual_maturity}'"
        )

    risk_level = manifest.get("risk_level", "low") if manifest else "low"

    # Gate 4: latest review_status == approved_for_testing
    review = _latest_review(qdir)
    if source_review_id:
        # Look up specific review
        specified_found = False
        reviews_dir = qdir / "quarantine_reviews"
        if reviews_dir.is_dir():
            rev_path = reviews_dir / f"{source_review_id}.json"
            if rev_path.is_file():
                try:
                    review = json.loads(rev_path.read_text(encoding="utf-8"))
                    specified_found = True
                except (json.JSONDecodeError, OSError):
                    pass
        if not specified_found:
            blocking.append(f"Specified review not found: {source_review_id}")
    elif review is None:
        blocking.append("No review decision found — must be approved_for_testing")
    elif review.get("review_status") != "approved_for_testing":
        blocking.append(
            f"Latest review status is '{review.get('review_status')}', "
            "must be 'approved_for_testing'"
        )

    # Gate 5: latest audit report exists
    audit = _latest_audit(qdir)
    if source_audit_id:
        specified_found = False
        audits_dir = qdir / "quarantine_audit_reports"
        if audits_dir.is_dir():
            aud_path = audits_dir / f"{source_audit_id}.json"
            if aud_path.is_file():
                try:
                    audit = json.loads(aud_path.read_text(encoding="utf-8"))
                    specified_found = True
                except (json.JSONDecodeError, OSError):
                    pass
        if not specified_found:
            blocking.append(f"Specified audit not found: {source_audit_id}")
    elif audit is None:
        blocking.append("No audit report found")
    else:
        # Check audit recommendation consistency
        audit_recommendation = audit.get("recommended_review_status", "")
        if audit_recommendation not in ("approved_for_testing", "") and not audit.get("passed", False):
            blocking.append(
                f"Audit recommendation is '{audit_recommendation}', "
                "does not support approved_for_testing"
            )

    # Gate 6: content hash consistency check (if supported)
    current_hash = _get_content_hash(qdir)
    review_hash = review.get("content_hash_at_review", "") if review else ""
    audit_hash = audit.get("content_hash_at_audit", "") if audit else ""

    if review_hash and review_hash != current_hash:
        blocking.append(
            "Content hash mismatch: review was performed on different content"
        )
    if audit_hash and audit_hash != current_hash:
        blocking.append(
            "Content hash mismatch: audit was performed on different content"
        )

    # Gate 7: no existing pending request for same capability + target scope
    if _check_existing_pending(store_data_dir, capability_id, requested_target_scope):
        blocking.append(
            f"A pending transition request already exists for capability "
            f"'{capability_id}' with target scope '{requested_target_scope}'"
        )

    # Gate 8: re-run evaluator/policy install checks
    if not blocking:
        try:
            from src.capabilities.document import CapabilityParser

            parser_obj = CapabilityParser()
            doc = parser_obj.parse(qdir)

            eval_record = evaluator.evaluate(doc)
            if not eval_record.passed:
                error_findings = [
                    f for f in eval_record.findings
                    if f.severity.value == "error"
                ]
                if error_findings:
                    blocking.append(
                        "Evaluator check failed: "
                        + "; ".join(f"{f.code}: {f.message}" for f in error_findings[:3])
                    )

            install_decision = policy.validate_install(
                doc.manifest,
                source="external_package",
                context={"source_path": str(qdir)},
            )
            if not install_decision.allowed:
                blocking.append(
                    f"Policy install check failed: {install_decision.code}: "
                    f"{install_decision.message}"
                )
        except Exception as e:
            blocking.append(f"Evaluator/policy check error: {e}")

    # Gate 9: high risk requires required_approval
    requires_approval = risk_level == "high"

    if blocking:
        if dry_run:
            return {"would_create": False, "blocking_reasons": blocking}
        raise CapabilityError(f"Cannot create transition request: {'; '.join(blocking)}")

    # Build findings_summary from audit
    findings_summary = {}
    if audit:
        findings_summary = _sanitize_for_findings_summary(audit)

    # Build request
    request_id = _generate_request_id()
    now = datetime.now(timezone.utc)

    req = QuarantineTransitionRequest(
        request_id=request_id,
        capability_id=capability_id,
        created_at=now.isoformat(),
        created_by=created_by,
        source_review_id=source_review_id or (review.get("review_id", "") if review else ""),
        source_audit_id=source_audit_id or (audit.get("audit_id", "") if audit else ""),
        requested_target_scope=requested_target_scope,
        requested_target_maturity=TARGET_MATURITY,
        status="pending",
        reason=reason.strip(),
        risk_level=risk_level,
        required_approval=requires_approval,
        findings_summary=findings_summary,
        content_hash_at_request=current_hash,
    )

    if dry_run:
        return {
            "would_create": True,
            "blocking_reasons": [],
            "request_preview": req.compact_summary(),
        }

    # Write request JSON
    _write_request(store_data_dir, req)

    return {
        "would_create": True,
        "blocking_reasons": [],
        "request": req.to_dict(),
    }


def _write_request(
    store_data_dir: Path,
    req: QuarantineTransitionRequest,
) -> Path:
    """Write the request JSON to quarantine_transition_requests/<request_id>.json."""
    rdir = _requests_dir(store_data_dir, req.capability_id)
    rdir.mkdir(parents=True, exist_ok=True)

    rpath = _request_path(store_data_dir, req.capability_id, req.request_id)
    rpath.write_text(
        json.dumps(req.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return rpath


# ── list_quarantine_transition_requests ───────────────────────────────


def list_quarantine_transition_requests(
    *,
    store_data_dir: Path,
    capability_id: str | None = None,
    status: str | None = None,
    target_scope: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List quarantine transition requests, read-only.

    Filters by capability_id, status, target_scope.
    Returns compact summaries — no script contents or raw paths.
    """
    results: list[dict[str, Any]] = []

    if capability_id:
        _validate_id_token(capability_id)
        qdir = _quarantine_dir(store_data_dir, capability_id)
        if not qdir.is_dir():
            return []
        candidates = [qdir]
    else:
        qroot = store_data_dir / "quarantine"
        if not qroot.is_dir():
            return []
        candidates = sorted(
            d for d in qroot.iterdir() if d.is_dir()
        )

    for qdir in candidates:
        try:
            _validate_id_token(qdir.name)
        except CapabilityError:
            continue

        rdir = qdir / "quarantine_transition_requests"
        if not rdir.is_dir():
            continue

        for req_file in sorted(rdir.iterdir(), reverse=True):
            if not req_file.suffix == ".json":
                continue
            try:
                req = json.loads(req_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            req_status = req.get("status", "")
            if status and req_status != status:
                continue
            req_scope = req.get("requested_target_scope", "")
            if target_scope and req_scope != target_scope:
                continue

            results.append({
                "request_id": req.get("request_id", ""),
                "capability_id": req.get("capability_id", qdir.name),
                "status": req_status,
                "requested_target_scope": req_scope,
                "requested_target_maturity": req.get("requested_target_maturity", "testing"),
                "risk_level": req.get("risk_level", "low"),
                "required_approval": req.get("required_approval", False),
                "created_at": req.get("created_at", ""),
                "created_by": req.get("created_by"),
                "reason": req.get("reason", ""),
                "source_review_id": req.get("source_review_id"),
                "source_audit_id": req.get("source_audit_id"),
            })

            if len(results) >= min(limit, 100):
                break

        if len(results) >= min(limit, 100):
            break

    results.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return results[:limit]


# ── view_quarantine_transition_request ────────────────────────────────


def view_quarantine_transition_request(
    *,
    store_data_dir: Path,
    capability_id: str,
    request_id: str,
) -> dict[str, Any]:
    """View a full transition request detail. Read-only — no script contents or raw paths."""
    _validate_id_token(capability_id)
    _validate_id_token(request_id)

    rpath = _request_path(store_data_dir, capability_id, request_id)
    if not rpath.is_file():
        raise CapabilityError(
            f"Transition request not found: {request_id} for capability {capability_id}"
        )

    try:
        req = json.loads(rpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        raise CapabilityError(f"Cannot read transition request: {request_id}")

    return req


# ── cancel_quarantine_transition_request ──────────────────────────────


def cancel_quarantine_transition_request(
    *,
    store_data_dir: Path,
    capability_id: str,
    request_id: str,
    reason: str,
) -> dict[str, Any]:
    """Cancel a pending transition request.

    Changes status from pending -> cancelled.
    Does NOT alter the capability, delete the request, or affect active store/index.
    """
    _validate_id_token(capability_id)
    _validate_id_token(request_id)

    if not reason or not reason.strip():
        raise CapabilityError("reason is required to cancel a transition request")

    rpath = _request_path(store_data_dir, capability_id, request_id)
    if not rpath.is_file():
        raise CapabilityError(
            f"Transition request not found: {request_id} for capability {capability_id}"
        )

    try:
        req = json.loads(rpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        raise CapabilityError(f"Cannot read transition request: {request_id}")

    if req.get("status") != "pending":
        raise CapabilityError(
            f"Cannot cancel request with status '{req.get('status')}'. "
            "Only 'pending' requests can be cancelled."
        )

    now = datetime.now(timezone.utc)
    req["status"] = "cancelled"
    req.setdefault("metadata", {})
    req["metadata"]["cancelled_at"] = now.isoformat()
    req["metadata"]["cancellation_reason"] = reason.strip()

    rpath.write_text(
        json.dumps(req, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return req
