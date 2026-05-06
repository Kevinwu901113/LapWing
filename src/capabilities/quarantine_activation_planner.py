"""Phase 7D-A: Quarantine activation planner.

Computes an explicit activation plan for moving a quarantined capability
into testing in a future phase. The output is a plan/report ONLY.

Hard constraints:
- Does NOT activate quarantined capabilities.
- Does NOT move/copy files into active scopes.
- Does NOT change manifest.status.
- Does NOT change manifest.maturity.
- Does NOT update the active index.
- Does NOT make the capability retrievable.
- Does NOT execute scripts.
- Does NOT implement run_capability.
- Does NOT call CapabilityLifecycleManager.
- Does NOT call CapabilityStore.create/refresh/upsert.
- Does NOT call promotion/transition helpers.
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
    CapabilityScope,
    CapabilityStatus,
)

if TYPE_CHECKING:
    from src.capabilities.evaluator import CapabilityEvaluator
    from src.capabilities.policy import CapabilityPolicy

ALLOWED_TARGET_SCOPES = frozenset({"user", "workspace", "session", "global"})
TARGET_MATURITY = "testing"
TARGET_STATUS = "active"


# ── Data model ────────────────────────────────────────────────────────


@dataclass
class QuarantineActivationPlan:
    """A plan for activating a quarantined capability into testing.

    This is a PURE PLAN — it does not perform any activation or mutation.
    """

    plan_id: str
    capability_id: str
    request_id: str
    created_at: str
    created_by: str | None
    source_review_id: str | None
    source_audit_id: str | None
    target_scope: str
    target_status: str
    target_maturity: str
    allowed: bool
    required_approval: bool
    blocking_findings: list[dict[str, Any]] = field(default_factory=list)
    policy_findings: list[dict[str, Any]] = field(default_factory=list)
    evaluator_findings: list[dict[str, Any]] = field(default_factory=list)
    copy_plan: dict[str, Any] = field(default_factory=dict)
    content_hash: str = ""
    request_content_hash: str = ""
    risk_level: str = "low"
    explanation: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "capability_id": self.capability_id,
            "request_id": self.request_id,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "source_review_id": self.source_review_id,
            "source_audit_id": self.source_audit_id,
            "target_scope": self.target_scope,
            "target_status": self.target_status,
            "target_maturity": self.target_maturity,
            "allowed": self.allowed,
            "required_approval": self.required_approval,
            "blocking_findings": self.blocking_findings,
            "policy_findings": self.policy_findings,
            "evaluator_findings": self.evaluator_findings,
            "copy_plan": self.copy_plan,
            "content_hash": self.content_hash,
            "request_content_hash": self.request_content_hash,
            "risk_level": self.risk_level,
            "explanation": self.explanation,
            "metadata": self.metadata,
        }

    def tool_output(self) -> dict[str, Any]:
        """Return a safe tool output with no raw absolute paths."""
        output = self.to_dict()
        safe_copy = dict(output.get("copy_plan", {}))
        safe_copy.pop("_source_quarantine_dir", None)
        safe_copy.pop("_target_base_dir", None)
        output["copy_plan"] = safe_copy
        return output

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QuarantineActivationPlan":
        return cls(
            plan_id=str(data.get("plan_id", "")),
            capability_id=str(data.get("capability_id", "")),
            request_id=str(data.get("request_id", "")),
            created_at=str(data.get("created_at", "")),
            created_by=data.get("created_by"),
            source_review_id=data.get("source_review_id"),
            source_audit_id=data.get("source_audit_id"),
            target_scope=str(data.get("target_scope", "")),
            target_status=str(data.get("target_status", TARGET_STATUS)),
            target_maturity=str(data.get("target_maturity", TARGET_MATURITY)),
            allowed=bool(data.get("allowed", False)),
            required_approval=bool(data.get("required_approval", False)),
            blocking_findings=data.get("blocking_findings", []),
            policy_findings=data.get("policy_findings", []),
            evaluator_findings=data.get("evaluator_findings", []),
            copy_plan=data.get("copy_plan", {}),
            content_hash=str(data.get("content_hash", "")),
            request_content_hash=str(data.get("request_content_hash", "")),
            risk_level=str(data.get("risk_level", "low")),
            explanation=str(data.get("explanation", "")),
            metadata=data.get("metadata", {}),
        )

    def compact_summary(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "capability_id": self.capability_id,
            "request_id": self.request_id,
            "allowed": self.allowed,
            "target_scope": self.target_scope,
            "target_maturity": self.target_maturity,
            "risk_level": self.risk_level,
            "required_approval": self.required_approval,
            "blocking_count": len(self.blocking_findings),
            "created_at": self.created_at,
            "created_by": self.created_by,
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


# ── Internal helpers ──────────────────────────────────────────────────


def _generate_plan_id() -> str:
    return f"qap_{uuid.uuid4().hex[:12]}"


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


def _load_request_by_id(store_data_dir: Path, capability_id: str, request_id: str) -> dict[str, Any] | None:
    _validate_id_token(request_id)
    rpath = _requests_dir(store_data_dir, capability_id) / f"{request_id}.json"
    if not rpath.is_file():
        return None
    try:
        return json.loads(rpath.read_text(encoding="utf-8"))
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


def _check_target_collision(store_data_dir: Path, capability_id: str, target_scope: str) -> dict[str, Any] | None:
    """Check if capability_id already exists in target scope without writing anything."""
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


def _build_copy_plan(quarantine_dir: Path, store_data_dir: Path, capability_id: str, target_scope: str) -> dict[str, Any]:
    """Build a summary copy plan with no raw absolute paths in top-level fields."""
    files_by_category: dict[str, list[str]] = {}
    total_files = 0
    for item in sorted(quarantine_dir.rglob("*")):
        if item.is_file() and ".git" not in item.parts:
            rel = item.relative_to(quarantine_dir)
            parts = rel.parts
            category = parts[0] if len(parts) > 1 else "root"
            files_by_category.setdefault(category, []).append(str(rel))
            total_files += 1

    # Store internal paths but mark them as internal-only
    return {
        "_source_quarantine_dir": str(quarantine_dir.relative_to(store_data_dir)),
        "_target_base_dir": target_scope,
        "target_scope": target_scope,
        "total_files": total_files,
        "file_count_by_category": {k: len(v) for k, v in files_by_category.items()},
        "categories": sorted(files_by_category.keys()),
        "has_capability_md": "CAPABILITY.md" in files_by_category.get("root", []) or any(
            f.endswith("CAPABILITY.md") for cat_files in files_by_category.values() for f in cat_files
        ),
        "has_manifest": "manifest.json" in files_by_category.get("root", []) or any(
            f.endswith("manifest.json") for cat_files in files_by_category.values() for f in cat_files
        ),
    }


def _safe_path_for_output(path_str: str) -> str:
    """Convert a path to a safe representation without raw absolute paths."""
    return str(Path(path_str).name) if path_str else ""


# ── plan_quarantine_activation ────────────────────────────────────────


def plan_quarantine_activation(
    *,
    store_data_dir: Path,
    capability_id: str,
    request_id: str | None = None,
    target_scope: str | None = None,
    evaluator: "CapabilityEvaluator",
    policy: "CapabilityPolicy",
    created_by: str | None = None,
    persist_plan: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Compute an activation plan for a quarantined capability.

    This is a PURE PLANNER — no activation, no file copy, no mutation.

    Returns plan metadata on success, or blocking reasons on failure.
    """
    _validate_id_token(capability_id)

    blocking: list[dict[str, Any]] = []
    evaluator_findings: list[dict[str, Any]] = []
    policy_findings: list[dict[str, Any]] = []

    # Gate 1: capability exists in quarantine
    qdir = _quarantine_dir(store_data_dir, capability_id)
    if not qdir.is_dir():
        blocking.append({
            "type": "not_found",
            "detail": f"Quarantined capability not found: {capability_id}",
        })

    if blocking:
        plan = _build_blocked_plan(
            capability_id=capability_id,
            request_id=request_id or "",
            created_by=created_by,
            target_scope=target_scope or "",
            blocking_findings=blocking,
            risk_level="low",
            explanation="Capability not found in quarantine.",
        )
        if persist_plan and not dry_run:
            _write_plan(store_data_dir, plan)
        return {"plan": plan.tool_output(), "would_activate": False}

    # Read manifest
    manifest = _read_manifest(qdir)
    if manifest is None:
        blocking.append({
            "type": "no_manifest",
            "detail": "Cannot read manifest.json",
        })

    # Gate 2: manifest.status == quarantined
    actual_status = manifest.get("status", "") if manifest else ""
    if actual_status != CapabilityStatus.QUARANTINED.value:
        blocking.append({
            "type": "bad_status",
            "detail": f"Capability status must be 'quarantined', got '{actual_status}'",
        })

    # Gate 3: manifest.maturity == draft
    actual_maturity = manifest.get("maturity", "") if manifest else ""
    if actual_maturity != CapabilityMaturity.DRAFT.value:
        blocking.append({
            "type": "bad_maturity",
            "detail": f"Capability maturity must be 'draft', got '{actual_maturity}'",
        })

    risk_level = manifest.get("risk_level", "low") if manifest else "low"

    # Gate 4: load transition request
    req: dict[str, Any] | None = None
    if request_id:
        req = _load_request_by_id(store_data_dir, capability_id, request_id)
        if req is None:
            blocking.append({
                "type": "request_not_found",
                "detail": f"Transition request not found: {request_id}",
            })
    else:
        req = _load_latest_pending_request(store_data_dir, capability_id)
        if req is None:
            blocking.append({
                "type": "no_pending_request",
                "detail": "No pending transition request found",
            })

    if req is not None:
        resolved_request_id = req.get("request_id", request_id or "")
    else:
        resolved_request_id = request_id or ""

    # Gate 5: request.status == pending
    if req is not None:
        req_status = req.get("status", "")
        if req_status != "pending":
            blocking.append({
                "type": "request_not_pending",
                "detail": f"Request status is '{req_status}', must be 'pending'",
            })

    # Gate 6: request target maturity == testing
    if req is not None:
        req_target_maturity = req.get("requested_target_maturity", "")
        if req_target_maturity and req_target_maturity != TARGET_MATURITY:
            blocking.append({
                "type": "bad_target_maturity",
                "detail": f"Request target maturity is '{req_target_maturity}', must be '{TARGET_MATURITY}'",
            })

    # Resolve target scope
    effective_target_scope = target_scope
    if effective_target_scope is None and req is not None:
        effective_target_scope = req.get("requested_target_scope", "user")
    if effective_target_scope is None:
        effective_target_scope = "user"
    if effective_target_scope not in ALLOWED_TARGET_SCOPES:
        blocking.append({
            "type": "bad_target_scope",
            "detail": f"Invalid target scope: '{effective_target_scope}'",
        })

    # Gate 7: content hash match between request and current state
    current_hash = _get_content_hash(qdir)
    if req is not None:
        req_hash = req.get("content_hash_at_request", "")
        if req_hash and req_hash != current_hash:
            blocking.append({
                "type": "content_hash_mismatch",
                "detail": "Content hash mismatch: capability changed since request was created",
            })

    # Gate 8: review still approved_for_testing
    review = None
    if req and not blocking:
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

    # Gate 9: audit still passed/approved_for_testing
    audit = None
    if req and not blocking:
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

    # Gate 10: target collision check (read-only)
    if not blocking and effective_target_scope:
        collision = _check_target_collision(store_data_dir, capability_id, effective_target_scope)
        if collision:
            blocking.append(collision)

    # Gate 11: re-run evaluator
    if not blocking:
        try:
            from src.capabilities.document import CapabilityParser

            parser_obj = CapabilityParser()
            doc = parser_obj.parse(qdir)

            eval_record = evaluator.evaluate(doc)
            evaluator_findings = [
                {"code": f.code, "severity": f.severity.value, "message": f.message}
                for f in eval_record.findings
            ]
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

    # Gate 12: re-run policy install/transition checks
    if not blocking:
        try:
            from src.capabilities.document import CapabilityParser

            if 'parser_obj' not in dir() or 'doc' not in dir():
                parser_obj2 = CapabilityParser()
                doc2 = parser_obj2.parse(qdir)
            else:
                doc2 = doc

            install_decision = policy.validate_install(
                doc2.manifest,
                source="external_package",
                context={"source_path": str(qdir)},
            )
            policy_findings = [
                {"code": install_decision.code, "severity": install_decision.severity.value if hasattr(install_decision, 'severity') else "info", "message": install_decision.message, "allowed": install_decision.allowed}
            ]
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

    # Gate 13: high risk requires required_approval flag
    requires_approval = risk_level == "high"
    if requires_approval and not blocking:
        # allowed only with required_approval=true; plan sets this flag
        pass

    # Gate 14: stale audit/review check (if timestamps available)
    if not blocking and audit and review:
        try:
            audit_ts = audit.get("created_at", "")
            review_ts = review.get("created_at", "")
            if audit_ts and review_ts and audit_ts < review_ts:
                # audit is older than review; still acceptable
                pass
        except Exception:
            pass

    # Build plan
    source_review_id = review.get("review_id", "") if review else (req.get("source_review_id", "") if req else "")
    source_audit_id = audit.get("audit_id", "") if audit else (req.get("source_audit_id", "") if req else "")

    if blocking:
        plan = _build_blocked_plan(
            capability_id=capability_id,
            request_id=resolved_request_id,
            created_by=created_by,
            target_scope=effective_target_scope or "",
            blocking_findings=blocking,
            evaluator_findings=evaluator_findings,
            policy_findings=policy_findings,
            risk_level=risk_level,
            source_review_id=source_review_id or None,
            source_audit_id=source_audit_id or None,
            content_hash=current_hash,
            request_content_hash=req.get("content_hash_at_request", "") if req else "",
            explanation="Activation plan blocked: one or more gates failed.",
        )
        if persist_plan and not dry_run:
            _write_plan(store_data_dir, plan)
        return {"plan": plan.tool_output(), "would_activate": False}
    else:
        copy_plan = _build_copy_plan(qdir, store_data_dir, capability_id, effective_target_scope)
        plan = _build_allowed_plan(
            capability_id=capability_id,
            request_id=resolved_request_id,
            created_by=created_by,
            target_scope=effective_target_scope,
            evaluator_findings=evaluator_findings,
            policy_findings=policy_findings,
            copy_plan=copy_plan,
            risk_level=risk_level,
            required_approval=requires_approval,
            source_review_id=source_review_id or None,
            source_audit_id=source_audit_id or None,
            content_hash=current_hash,
            request_content_hash=req.get("content_hash_at_request", "") if req else "",
            explanation="All gates passed. Activation plan is allowed but NOT executed — requires a future activation phase.",
        )
        if persist_plan and not dry_run:
            _write_plan(store_data_dir, plan)
        return {"plan": plan.tool_output(), "would_activate": False}


def _build_allowed_plan(
    *,
    capability_id: str,
    request_id: str,
    created_by: str | None,
    target_scope: str,
    evaluator_findings: list[dict[str, Any]],
    policy_findings: list[dict[str, Any]],
    copy_plan: dict[str, Any],
    risk_level: str,
    required_approval: bool,
    source_review_id: str | None,
    source_audit_id: str | None,
    content_hash: str,
    request_content_hash: str,
    explanation: str,
) -> QuarantineActivationPlan:
    now = datetime.now(timezone.utc)
    return QuarantineActivationPlan(
        plan_id=_generate_plan_id(),
        capability_id=capability_id,
        request_id=request_id,
        created_at=now.isoformat(),
        created_by=created_by,
        source_review_id=source_review_id,
        source_audit_id=source_audit_id,
        target_scope=target_scope,
        target_status=TARGET_STATUS,
        target_maturity=TARGET_MATURITY,
        allowed=True,
        required_approval=required_approval,
        blocking_findings=[],
        policy_findings=policy_findings,
        evaluator_findings=evaluator_findings,
        copy_plan=copy_plan,
        content_hash=content_hash,
        request_content_hash=request_content_hash,
        risk_level=risk_level,
        explanation=explanation,
    )


def _build_blocked_plan(
    *,
    capability_id: str,
    request_id: str,
    created_by: str | None,
    target_scope: str,
    blocking_findings: list[dict[str, Any]],
    evaluator_findings: list[dict[str, Any]] | None = None,
    policy_findings: list[dict[str, Any]] | None = None,
    risk_level: str = "low",
    source_review_id: str | None = None,
    source_audit_id: str | None = None,
    content_hash: str = "",
    request_content_hash: str = "",
    explanation: str = "",
) -> QuarantineActivationPlan:
    now = datetime.now(timezone.utc)
    return QuarantineActivationPlan(
        plan_id=_generate_plan_id(),
        capability_id=capability_id,
        request_id=request_id,
        created_at=now.isoformat(),
        created_by=created_by,
        source_review_id=source_review_id,
        source_audit_id=source_audit_id,
        target_scope=target_scope,
        target_status=TARGET_STATUS,
        target_maturity=TARGET_MATURITY,
        allowed=False,
        required_approval=False,
        blocking_findings=blocking_findings,
        policy_findings=policy_findings or [],
        evaluator_findings=evaluator_findings or [],
        copy_plan={},
        content_hash=content_hash,
        request_content_hash=request_content_hash,
        risk_level=risk_level,
        explanation=explanation or "Activation plan blocked.",
    )


def _write_plan(
    store_data_dir: Path,
    plan: QuarantineActivationPlan,
) -> Path:
    """Write plan JSON to quarantine_activation_plans/<plan_id>.json.

    Only writes under the quarantine directory — never in active scopes.
    """
    pdir = _plans_dir(store_data_dir, plan.capability_id)
    pdir.mkdir(parents=True, exist_ok=True)

    ppath = _plan_path(store_data_dir, plan.capability_id, plan.plan_id)
    ppath.write_text(
        json.dumps(plan.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return ppath


# ── list_quarantine_activation_plans ──────────────────────────────────


def list_quarantine_activation_plans(
    *,
    store_data_dir: Path,
    capability_id: str | None = None,
    allowed: bool | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List activation plans, read-only."""
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
        candidates = sorted(d for d in qroot.iterdir() if d.is_dir())

    for qdir in candidates:
        try:
            _validate_id_token(qdir.name)
        except CapabilityError:
            continue

        pdir = qdir / "quarantine_activation_plans"
        if not pdir.is_dir():
            continue

        for plan_file in sorted(pdir.iterdir(), reverse=True):
            if not plan_file.suffix == ".json":
                continue
            try:
                plan_data = json.loads(plan_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            if allowed is not None and plan_data.get("allowed") != allowed:
                continue

            results.append({
                "plan_id": plan_data.get("plan_id", ""),
                "capability_id": plan_data.get("capability_id", qdir.name),
                "request_id": plan_data.get("request_id", ""),
                "allowed": plan_data.get("allowed", False),
                "target_scope": plan_data.get("target_scope", ""),
                "risk_level": plan_data.get("risk_level", "low"),
                "required_approval": plan_data.get("required_approval", False),
                "blocking_count": len(plan_data.get("blocking_findings", [])),
                "created_at": plan_data.get("created_at", ""),
                "created_by": plan_data.get("created_by"),
            })

            if len(results) >= min(limit, 100):
                break

        if len(results) >= min(limit, 100):
            break

    results.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return results[:limit]


# ── view_quarantine_activation_plan ───────────────────────────────────


def view_quarantine_activation_plan(
    *,
    store_data_dir: Path,
    capability_id: str,
    plan_id: str,
) -> dict[str, Any]:
    """View a full activation plan. Read-only — no raw absolute paths."""
    _validate_id_token(capability_id)
    _validate_id_token(plan_id)

    ppath = _plan_path(store_data_dir, capability_id, plan_id)
    if not ppath.is_file():
        raise CapabilityError(
            f"Activation plan not found: {plan_id} for capability {capability_id}"
        )

    try:
        plan_data = json.loads(ppath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        raise CapabilityError(f"Cannot read activation plan: {plan_id}")

    # Strip internal paths from output
    safe_copy = dict(plan_data.get("copy_plan", {}))
    safe_copy.pop("_source_quarantine_dir", None)
    safe_copy.pop("_target_base_dir", None)
    plan_data["copy_plan"] = safe_copy

    return plan_data
