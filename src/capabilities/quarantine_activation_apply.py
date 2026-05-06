"""Phase 7D-B: Quarantine activation apply.

Explicit operator-only application of a previously approved quarantine
activation plan into testing. This is the first phase that copies a
quarantined capability into an active target scope.

Hard constraints:
- Only produces status=active, maturity=testing.
- Never produces maturity=stable.
- No run_capability.
- No script execution.
- No automatic retrieval beyond normal testing filters.
- No automatic promotion.
- No dynamic agent binding.
- Original quarantine copy remains unchanged (quarantined/draft).
"""

from __future__ import annotations

import json
import shutil
import uuid
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
    from src.capabilities.evaluator import CapabilityEvaluator
    from src.capabilities.index import CapabilityIndex
    from src.capabilities.policy import CapabilityPolicy

ALLOWED_TARGET_SCOPES = frozenset({"user", "workspace", "session", "global"})
TARGET_MATURITY = "testing"
TARGET_STATUS = "active"


# ── Data model ────────────────────────────────────────────────────────


@dataclass
class ActivationResult:
    """Result of an activation apply operation.

    Never contains raw absolute paths or script contents.
    """

    capability_id: str
    source_quarantine_id: str
    target_scope: str
    target_status: str
    target_maturity: str
    applied: bool
    dry_run: bool
    plan_id: str = ""
    request_id: str = ""
    activation_report_id: str = ""
    content_hash_before: str = ""
    content_hash_after: str = ""
    index_refreshed: bool = False
    blocking_findings: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""
    partial_failure: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "source_quarantine_id": self.source_quarantine_id,
            "target_scope": self.target_scope,
            "target_status": self.target_status,
            "target_maturity": self.target_maturity,
            "applied": self.applied,
            "dry_run": self.dry_run,
            "plan_id": self.plan_id,
            "request_id": self.request_id,
            "activation_report_id": self.activation_report_id,
            "content_hash_before": self.content_hash_before,
            "content_hash_after": self.content_hash_after,
            "index_refreshed": self.index_refreshed,
            "blocking_findings": self.blocking_findings,
            "message": self.message,
            "partial_failure": self.partial_failure,
        }


# ── Path helpers ──────────────────────────────────────────────────────


def _validate_id_token(token: str) -> None:
    if not token or "/" in token or "\\" in token or ".." in token:
        raise CapabilityError(f"Invalid identifier: {token!r}")


def _quarantine_dir(store_data_dir: Path, capability_id: str) -> Path:
    _validate_id_token(capability_id)
    return store_data_dir / "quarantine" / capability_id


def _plans_dir(store_data_dir: Path, capability_id: str) -> Path:
    return _quarantine_dir(store_data_dir, capability_id) / "quarantine_activation_plans"


def _plan_path(store_data_dir: Path, capability_id: str, plan_id: str) -> Path:
    _validate_id_token(plan_id)
    return _plans_dir(store_data_dir, capability_id) / f"{plan_id}.json"


def _requests_dir(store_data_dir: Path, capability_id: str) -> Path:
    return _quarantine_dir(store_data_dir, capability_id) / "quarantine_transition_requests"


def _request_path(store_data_dir: Path, capability_id: str, request_id: str) -> Path:
    _validate_id_token(request_id)
    return _requests_dir(store_data_dir, capability_id) / f"{request_id}.json"


def _activation_reports_dir(store_data_dir: Path, capability_id: str) -> Path:
    return _quarantine_dir(store_data_dir, capability_id) / "quarantine_activation_reports"


# ── Internal helpers ──────────────────────────────────────────────────


def _generate_report_id() -> str:
    return f"qar_{uuid.uuid4().hex[:12]}"


def _read_manifest(quarantine_dir: Path) -> dict[str, Any] | None:
    manifest_path = quarantine_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _get_content_hash(quarantine_dir: Path) -> str:
    from src.capabilities.document import CapabilityParser

    try:
        parser = CapabilityParser()
        doc = parser.parse(quarantine_dir)
        return doc.content_hash or ""
    except Exception:
        return ""


def _latest_review(quarantine_dir: Path) -> dict[str, Any] | None:
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


def _load_latest_plan(store_data_dir: Path, capability_id: str) -> dict[str, Any] | None:
    """Load the latest activation plan regardless of allowed status."""
    pdir = _plans_dir(store_data_dir, capability_id)
    if not pdir.is_dir():
        return None
    latest: tuple[str, dict[str, Any]] | None = None
    for plan_file in sorted(pdir.iterdir()):
        if not plan_file.suffix == ".json":
            continue
        try:
            plan = json.loads(plan_file.read_text(encoding="utf-8"))
            created = plan.get("created_at", "")
            if latest is None or created > latest[0]:
                latest = (created, plan)
        except (json.JSONDecodeError, OSError):
            continue
    return latest[1] if latest else None


def _load_plan_by_id(store_data_dir: Path, capability_id: str, plan_id: str) -> dict[str, Any] | None:
    _validate_id_token(plan_id)
    ppath = _plan_path(store_data_dir, capability_id, plan_id)
    if not ppath.is_file():
        return None
    try:
        return json.loads(ppath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _load_latest_pending_request(store_data_dir: Path, capability_id: str) -> dict[str, Any] | None:
    rdir = _requests_dir(store_data_dir, capability_id)
    if not rdir.is_dir():
        return None
    latest: tuple[str, dict[str, Any]] | None = None
    for req_file in sorted(rdir.iterdir()):
        if not req_file.suffix == ".json":
            continue
        try:
            req = json.loads(req_file.read_text(encoding="utf-8"))
            if req.get("status") != "pending":
                continue
            created = req.get("created_at", "")
            if latest is None or created > latest[0]:
                latest = (created, req)
        except (json.JSONDecodeError, OSError):
            continue
    return latest[1] if latest else None


def _load_request_by_id(store_data_dir: Path, capability_id: str, request_id: str) -> dict[str, Any] | None:
    _validate_id_token(request_id)
    rpath = _request_path(store_data_dir, capability_id, request_id)
    if not rpath.is_file():
        return None
    try:
        return json.loads(rpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _check_target_collision(store_data_dir: Path, capability_id: str, target_scope: str) -> dict[str, Any] | None:
    scope_dir = store_data_dir / target_scope
    target_cap_dir = scope_dir / capability_id
    if target_cap_dir.exists():
        return {
            "type": "target_collision",
            "capability_id": capability_id,
            "target_scope": target_scope,
            "detail": f"Capability '{capability_id}' already exists in scope '{target_scope}'",
        }
    return None


def _recompute_content_hash(directory: Path) -> str:
    from src.capabilities.document import CapabilityParser

    try:
        parser = CapabilityParser()
        doc = parser.parse(directory)
        return doc.content_hash or ""
    except Exception:
        return ""


# ── apply_quarantine_activation ───────────────────────────────────────


def apply_quarantine_activation(
    *,
    store_data_dir: Path,
    capability_id: str,
    plan_id: str | None = None,
    request_id: str | None = None,
    target_scope: str | None = None,
    applied_by: str | None = None,
    reason: str,
    evaluator: "CapabilityEvaluator",
    policy: "CapabilityPolicy",
    index: "CapabilityIndex | None" = None,
    dry_run: bool = False,
) -> ActivationResult:
    """Apply an allowed activation plan to copy a quarantined capability into testing.

    This is the first operation that actually copies a quarantined capability
    into an active target scope. It requires all prior phases to have completed
    successfully (import→review→audit→request→plan).

    Args:
        store_data_dir: Root data directory for capabilities.
        capability_id: The quarantined capability to activate.
        plan_id: Specific plan to use. If omitted, uses latest allowed plan.
        request_id: Specific transition request. If omitted, uses latest pending.
        target_scope: Target scope. Must match plan/request if provided.
        applied_by: Who is applying the activation.
        reason: Required reason for the activation.
        evaluator: Capability evaluator instance.
        policy: Capability policy instance.
        index: Capability index for refreshing after copy.
        dry_run: If True, perform all gate checks but write nothing.

    Returns:
        ActivationResult with applied=True only if all gates pass and copy succeeds.
    """
    _validate_id_token(capability_id)

    if not reason or not reason.strip():
        return ActivationResult(
            capability_id=capability_id,
            source_quarantine_id=capability_id,
            target_scope=target_scope or "",
            target_status=TARGET_STATUS,
            target_maturity=TARGET_MATURITY,
            applied=False,
            dry_run=dry_run,
            blocking_findings=[{"type": "missing_reason", "detail": "reason is required"}],
            message="Activation denied: reason is required.",
        )

    blocking: list[dict[str, Any]] = []

    # Gate 1: capability exists in quarantine
    qdir = _quarantine_dir(store_data_dir, capability_id)
    if not qdir.is_dir():
        blocking.append({
            "type": "not_found",
            "detail": f"Quarantined capability not found: {capability_id}",
        })

    # Gate 2: manifest.status == quarantined
    manifest = _read_manifest(qdir) if not blocking else None
    if manifest is None and not blocking:
        blocking.append({"type": "no_manifest", "detail": "Cannot read manifest.json"})

    if manifest is not None:
        actual_status = manifest.get("status", "")
        if actual_status != CapabilityStatus.QUARANTINED.value:
            blocking.append({
                "type": "bad_status",
                "detail": f"Capability status must be 'quarantined', got '{actual_status}'",
            })

        # Gate 3: manifest.maturity == draft
        actual_maturity = manifest.get("maturity", "")
        if actual_maturity != CapabilityMaturity.DRAFT.value:
            blocking.append({
                "type": "bad_maturity",
                "detail": f"Capability maturity must be 'draft', got '{actual_maturity}'",
            })

    risk_level = manifest.get("risk_level", "low") if manifest else "low"

    # Early exit if fundamental gates fail
    if blocking:
        return ActivationResult(
            capability_id=capability_id,
            source_quarantine_id=capability_id,
            target_scope=target_scope or "",
            target_status=TARGET_STATUS,
            target_maturity=TARGET_MATURITY,
            applied=False,
            dry_run=dry_run,
            blocking_findings=blocking,
            message="Activation denied: basic gates failed.",
        )

    content_hash_before = _get_content_hash(qdir)

    # Gate 4: load activation plan
    plan: dict[str, Any] | None = None
    if plan_id:
        plan = _load_plan_by_id(store_data_dir, capability_id, plan_id)
        if plan is None:
            blocking.append({
                "type": "plan_not_found",
                "detail": f"Activation plan not found: {plan_id}",
            })
    else:
        plan = _load_latest_plan(store_data_dir, capability_id)
        if plan is None:
            blocking.append({
                "type": "no_allowed_plan",
                "detail": "No activation plan found",
            })

    resolved_plan_id = plan.get("plan_id", plan_id or "") if plan else (plan_id or "")

    # Gate 5: plan.allowed == True
    if plan is not None and not plan.get("allowed", False):
        blocking.append({
            "type": "plan_not_allowed",
            "detail": "Activation plan is not allowed",
        })

    # Gate 6: plan target_maturity == testing
    if plan is not None:
        plan_maturity = plan.get("target_maturity", "")
        if plan_maturity != TARGET_MATURITY:
            blocking.append({
                "type": "bad_plan_maturity",
                "detail": f"Plan target maturity is '{plan_maturity}', must be '{TARGET_MATURITY}'",
            })

    # Gate 7: plan target_status == active
    if plan is not None:
        plan_status = plan.get("target_status", "")
        if plan_status != TARGET_STATUS:
            blocking.append({
                "type": "bad_plan_status",
                "detail": f"Plan target status is '{plan_status}', must be '{TARGET_STATUS}'",
            })

    # Resolve target scope
    effective_target_scope = target_scope
    if effective_target_scope is None and plan is not None:
        effective_target_scope = plan.get("target_scope", "user")
    if effective_target_scope is None:
        effective_target_scope = "user"
    if effective_target_scope not in ALLOWED_TARGET_SCOPES:
        blocking.append({
            "type": "bad_target_scope",
            "detail": f"Invalid target scope: '{effective_target_scope}'",
        })

    # Gate 8: would_activate must be false in persisted plan
    # The persisted plan is from 7D-A which always sets would_activate=False.
    # If someone hand-edits the plan to set would_activate=True, reject it.
    if plan is not None:
        plan_file_would = plan.get("would_activate", None)
        if plan_file_would is True:
            blocking.append({
                "type": "invalid_plan_state",
                "detail": "Persisted plan has would_activate=True; apply is a separate authority.",
            })

    # Gate 9: load transition request
    req: dict[str, Any] | None = None
    if request_id and not blocking:
        req = _load_request_by_id(store_data_dir, capability_id, request_id)
        if req is None:
            blocking.append({
                "type": "request_not_found",
                "detail": f"Transition request not found: {request_id}",
            })
    elif not blocking:
        req = _load_latest_pending_request(store_data_dir, capability_id)
        if req is None:
            blocking.append({
                "type": "no_pending_request",
                "detail": "No pending transition request found",
            })

    resolved_request_id = request_id or (req.get("request_id", "") if req else "")

    # Gate 10: request.status == pending
    if req is not None:
        req_status = req.get("status", "")
        if req_status != "pending":
            blocking.append({
                "type": "request_not_pending",
                "detail": f"Request status is '{req_status}', must be 'pending'",
            })

    # Gate 11: content hash match
    if req is not None:
        req_hash = req.get("content_hash_at_request", "")
        if req_hash and req_hash != content_hash_before:
            blocking.append({
                "type": "content_hash_mismatch",
                "detail": "Content hash mismatch: capability changed since request was created",
            })

    if plan is not None:
        plan_hash = plan.get("content_hash", "")
        if plan_hash and plan_hash != content_hash_before:
            blocking.append({
                "type": "plan_content_hash_mismatch",
                "detail": "Content hash mismatch: capability changed since plan was created",
            })

    # Gate 12: review still approved_for_testing
    review = None
    if not blocking:
        if req:
            source_review_id = req.get("source_review_id", "")
            if source_review_id:
                reviews_dir = qdir / "quarantine_reviews"
                rev_path = reviews_dir / f"{source_review_id}.json"
                if rev_path.is_file():
                    try:
                        review = json.loads(rev_path.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError):
                        pass
                if review is None:
                    blocking.append({
                        "type": "review_not_found",
                        "detail": f"Source review not found: {source_review_id}",
                    })
            else:
                review = _latest_review(qdir)

            if review is not None:
                review_status = review.get("review_status", "")
                if review_status != "approved_for_testing":
                    blocking.append({
                        "type": "review_not_approved",
                        "detail": f"Review status is '{review_status}', must be 'approved_for_testing'",
                    })
            elif not blocking:
                blocking.append({
                    "type": "no_review",
                    "detail": "No approved_for_testing review found",
                })

    # Gate 13: audit still passed / recommends approved_for_testing
    audit = None
    if not blocking:
        if req:
            source_audit_id = req.get("source_audit_id", "")
            if source_audit_id:
                audits_dir = qdir / "quarantine_audit_reports"
                aud_path = audits_dir / f"{source_audit_id}.json"
                if aud_path.is_file():
                    try:
                        audit = json.loads(aud_path.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError):
                        pass
                if audit is None:
                    blocking.append({
                        "type": "audit_not_found",
                        "detail": f"Source audit not found: {source_audit_id}",
                    })
            else:
                audit = _latest_audit(qdir)

            if audit is not None:
                audit_passed = audit.get("passed", False)
                audit_recommendation = audit.get("recommended_review_status", "")
                if not audit_passed and audit_recommendation not in ("approved_for_testing", ""):
                    blocking.append({
                        "type": "audit_not_approved",
                        "detail": f"Audit does not support activation: passed={audit_passed}, recommendation={audit_recommendation}",
                    })
            elif not blocking:
                blocking.append({
                    "type": "no_audit",
                    "detail": "No audit report found",
                })

    # Gate 14: target collision check
    if not blocking:
        collision = _check_target_collision(store_data_dir, capability_id, effective_target_scope)
        if collision:
            blocking.append(collision)

    # Gate 15: re-run evaluator
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
                    blocking.append({
                        "type": "evaluator_failed",
                        "detail": "Evaluator check failed: " + "; ".join(
                            f"{f.code}: {f.message}" for f in error_findings[:3]
                        ),
                        "error_codes": [f.code for f in error_findings],
                    })
        except Exception as e:
            blocking.append({
                "type": "evaluator_error",
                "detail": f"Evaluator error: {e}",
            })

    # Gate 16: re-run policy install check
    if not blocking:
        try:
            from src.capabilities.document import CapabilityParser

            parser_obj2 = CapabilityParser()
            doc2 = parser_obj2.parse(qdir)

            install_decision = policy.validate_install(
                doc2.manifest,
                source="external_package",
                context={"source_path": str(qdir)},
            )
            if not install_decision.allowed:
                blocking.append({
                    "type": "policy_denied",
                    "detail": f"Policy check failed: {install_decision.code}: {install_decision.message}",
                })
        except Exception as e:
            blocking.append({
                "type": "policy_error",
                "detail": f"Policy check error: {e}",
            })

    # Gate 17: high risk requires explicit approval model
    # Conservative: block high risk in 7D-B — no human approval model exists yet.
    if risk_level == "high" and not blocking:
        blocking.append({
            "type": "high_risk_blocked",
            "detail": (
                "High-risk capabilities cannot be activated in Phase 7D-B. "
                "A separate human approval model is required but not yet implemented."
            ),
        })

    # Gate 18: reject symlinks in quarantine directory
    if not blocking:
        symlinks_found = []
        for item in qdir.rglob("*"):
            if item.is_symlink():
                symlinks_found.append(str(item.relative_to(qdir)))
        if symlinks_found:
            blocking.append({
                "type": "symlinks_rejected",
                "detail": f"Quarantine directory contains symlinks, which are rejected: {symlinks_found[:5]}",
            })

    # ── Build result ──────────────────────────────────────────────────

    if blocking:
        return ActivationResult(
            capability_id=capability_id,
            source_quarantine_id=capability_id,
            target_scope=effective_target_scope,
            target_status=TARGET_STATUS,
            target_maturity=TARGET_MATURITY,
            applied=False,
            dry_run=dry_run,
            plan_id=resolved_plan_id,
            request_id=resolved_request_id,
            content_hash_before=content_hash_before,
            blocking_findings=blocking,
            message="Activation denied: one or more gates failed.",
        )

    # All gates passed.
    if dry_run:
        return ActivationResult(
            capability_id=capability_id,
            source_quarantine_id=capability_id,
            target_scope=effective_target_scope,
            target_status=TARGET_STATUS,
            target_maturity=TARGET_MATURITY,
            applied=False,
            dry_run=True,
            plan_id=resolved_plan_id,
            request_id=resolved_request_id,
            content_hash_before=content_hash_before,
            message="Dry run: all gates passed. Would apply activation.",
        )

    # ── Apply: copy to target scope ───────────────────────────────────

    report_id = _generate_report_id()
    now = datetime.now(timezone.utc)
    target_dir = store_data_dir / effective_target_scope / capability_id
    target_dir_created = False
    reports_dir_created = False

    try:
        # 1. Copy quarantine directory to target scope
        # shutil.copytree with symlinks=False rejects symlinks
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(str(qdir), str(target_dir), symlinks=False)
        target_dir_created = True

        # 2. Read copied manifest and normalize
        target_manifest_path = target_dir / "manifest.json"
        if not target_manifest_path.is_file():
            # Create manifest if it doesn't exist (from CAPABILITY.md only)
            target_manifest = {
                "id": capability_id,
                "name": manifest.get("name", capability_id) if manifest else capability_id,
                "description": manifest.get("description", "") if manifest else "",
                "type": manifest.get("type", "skill") if manifest else "skill",
                "scope": effective_target_scope,
                "maturity": TARGET_MATURITY,
                "status": TARGET_STATUS,
                "risk_level": risk_level,
            }
        else:
            target_manifest = json.loads(target_manifest_path.read_text(encoding="utf-8"))

        # Normalize
        target_manifest["scope"] = effective_target_scope
        target_manifest["status"] = TARGET_STATUS
        target_manifest["maturity"] = TARGET_MATURITY
        target_manifest["updated_at"] = now.isoformat()

        # Add origin metadata
        if "extra" not in target_manifest:
            target_manifest["extra"] = {}
        target_manifest["extra"]["origin"] = {
            "quarantine_capability_id": capability_id,
            "activation_plan_id": resolved_plan_id,
            "transition_request_id": resolved_request_id,
            "import_source_hash": manifest.get("extra", {}).get("origin", {}).get(
                "import_source_hash", ""
            ) if manifest else "",
            "activated_at": now.isoformat(),
            "activated_by": applied_by or "system",
        }

        # Write normalized manifest
        target_manifest_path.write_text(
            json.dumps(target_manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # 3. Recompute content hash for target
        content_hash_after = _recompute_content_hash(target_dir)
        target_manifest["content_hash"] = content_hash_after

        # 4. Write activation_report.json in target copy
        activation_report = {
            "activation_report_id": report_id,
            "capability_id": capability_id,
            "source_quarantine_id": capability_id,
            "target_scope": effective_target_scope,
            "target_status": TARGET_STATUS,
            "target_maturity": TARGET_MATURITY,
            "plan_id": resolved_plan_id,
            "request_id": resolved_request_id,
            "applied_by": applied_by,
            "reason": reason,
            "applied_at": now.isoformat(),
            "content_hash_before": content_hash_before,
            "content_hash_after": content_hash_after,
            "risk_level": risk_level,
        }
        (target_dir / "activation_report.json").write_text(
            json.dumps(activation_report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # 5. Write activation_report.json in quarantine
        reports_dir = _activation_reports_dir(store_data_dir, capability_id)
        reports_dir.mkdir(parents=True, exist_ok=True)
        reports_dir_created = True
        (reports_dir / f"{report_id}.json").write_text(
            json.dumps(activation_report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # 5b. Write provenance.json in target directory (Phase 8A-1)
        # Fail-closed: if provenance write fails, roll back the activation.
        try:
            from src.capabilities.provenance import read_provenance, write_provenance

            # Determine parent provenance from quarantine
            q_prov = read_provenance(qdir)
            parent_prov_id = q_prov.provenance_id if q_prov else None
            inherited_sig = q_prov.signature_status if q_prov else "not_present"

            # Trust level: reviewed if review+audit passed, else untrusted
            review_ok = review is not None and review.get("review_status") == "approved_for_testing"
            audit_ok = audit is not None and audit.get("passed", False)
            prov_trust = "reviewed" if (review_ok and audit_ok) else "untrusted"

            write_provenance(
                target_dir,
                capability_id=capability_id,
                source_type="quarantine_activation",
                source_content_hash=content_hash_after,
                parent_provenance_id=parent_prov_id,
                origin_capability_id=capability_id,
                origin_scope="quarantine",
                activated_at=now.isoformat(),
                activated_by=applied_by or "system",
                trust_level=prov_trust,
                integrity_status="verified",
                signature_status=inherited_sig,
                metadata={
                    "activation_plan_id": resolved_plan_id,
                    "transition_request_id": resolved_request_id,
                },
            )
        except Exception:
            if target_dir_created:
                shutil.rmtree(target_dir)
                target_dir_created = False
            return ActivationResult(
                capability_id=capability_id,
                source_quarantine_id=capability_id,
                target_scope=effective_target_scope,
                target_status=TARGET_STATUS,
                target_maturity=TARGET_MATURITY,
                applied=False,
                dry_run=False,
                plan_id=resolved_plan_id,
                request_id=resolved_request_id,
                activation_report_id=report_id,
                content_hash_before=content_hash_before,
                content_hash_after=content_hash_after,
                blocking_findings=[{
                    "type": "provenance_write_failed",
                    "detail": "Failed to write provenance.json in target directory; activation rolled back.",
                }],
                message="Activation failed: provenance write error, changes rolled back.",
            )

        # 6. Refresh CapabilityIndex for target copy
        index_refreshed = False
        if index is not None:
            try:
                from src.capabilities.document import CapabilityParser

                parser_idx = CapabilityParser()
                target_doc = parser_idx.parse(target_dir)
                index.upsert(target_doc)
                index_refreshed = True
            except Exception:
                # Rollback: remove target dir
                shutil.rmtree(target_dir)
                target_dir_created = False
                return ActivationResult(
                    capability_id=capability_id,
                    source_quarantine_id=capability_id,
                    target_scope=effective_target_scope,
                    target_status=TARGET_STATUS,
                    target_maturity=TARGET_MATURITY,
                    applied=False,
                    dry_run=False,
                    plan_id=resolved_plan_id,
                    request_id=resolved_request_id,
                    activation_report_id=report_id,
                    content_hash_before=content_hash_before,
                    content_hash_after=content_hash_after,
                    index_refreshed=False,
                    blocking_findings=[{
                        "type": "index_refresh_failed",
                        "detail": "Index refresh failed; target copy rolled back.",
                    }],
                    message="Activation failed: index refresh error, changes rolled back.",
                    partial_failure=False,
                )

        # 7. Mark transition request as superseded
        if resolved_request_id and req is not None:
            try:
                from src.capabilities.quarantine_transition import ALLOWED_REQUEST_STATUSES
                if "superseded" in ALLOWED_REQUEST_STATUSES:
                    req["status"] = "superseded"
                    req["metadata"] = req.get("metadata", {})
                    req["metadata"]["superseded_by"] = "activation_apply"
                    req["metadata"]["superseded_at"] = now.isoformat()
                    req["metadata"]["activation_report_id"] = report_id
                    rpath = _request_path(store_data_dir, capability_id, resolved_request_id)
                    rpath.write_text(
                        json.dumps(req, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
            except Exception:
                # Non-fatal: request marking failed but activation succeeded
                pass

        return ActivationResult(
            capability_id=capability_id,
            source_quarantine_id=capability_id,
            target_scope=effective_target_scope,
            target_status=TARGET_STATUS,
            target_maturity=TARGET_MATURITY,
            applied=True,
            dry_run=False,
            plan_id=resolved_plan_id,
            request_id=resolved_request_id,
            activation_report_id=report_id,
            content_hash_before=content_hash_before,
            content_hash_after=content_hash_after,
            index_refreshed=index_refreshed,
            message="Activation applied successfully.",
        )

    except Exception as e:
        # Clean up on any exception
        if target_dir_created and target_dir.exists():
            shutil.rmtree(target_dir)
        return ActivationResult(
            capability_id=capability_id,
            source_quarantine_id=capability_id,
            target_scope=effective_target_scope,
            target_status=TARGET_STATUS,
            target_maturity=TARGET_MATURITY,
            applied=False,
            dry_run=False,
            plan_id=resolved_plan_id,
            request_id=resolved_request_id,
            activation_report_id=report_id,
            content_hash_before=content_hash_before,
            blocking_findings=[{
                "type": "apply_error",
                "detail": f"Activation failed: {e}",
            }],
            message=f"Activation failed: {e}",
            partial_failure=False,
        )
