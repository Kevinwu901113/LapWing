"""Read-only capability maintenance health report.

Maintenance A: generates a health report with findings and recommendations.
No mutation. No execution. No network. No LLM judge. No automatic repair.
No automatic promotion. No run_capability. Determined by static checks only.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

from src.capabilities.eval_records import get_latest_eval_record
from src.capabilities.provenance import (
    INTEGRITY_MISMATCH,
    compute_capability_tree_hash,
    read_provenance,
)
from src.capabilities.schema import CapabilityMaturity, CapabilityStatus

if TYPE_CHECKING:
    from src.agents.candidate_store import AgentCandidateStore
    from src.capabilities.document import CapabilityDocument
    from src.capabilities.index import CapabilityIndex
    from src.capabilities.store import CapabilityStore
    from src.capabilities.trust_roots import TrustRootStore

logger = logging.getLogger(__name__)

# ── Constants ──

DEFAULT_STALE_EVAL_DAYS: int = 30
DEFAULT_STALE_PROPOSAL_DAYS: int = 90
DEFAULT_TRUST_ROOT_EXPIRY_WARN_DAYS: int = 30

_SEVERITY_INFO = "info"
_SEVERITY_WARNING = "warning"
_SEVERITY_ERROR = "error"

_VALID_SEVERITIES: frozenset[str] = frozenset({
    _SEVERITY_INFO, _SEVERITY_WARNING, _SEVERITY_ERROR,
})

# Directories expected inside a quarantine capability directory
_QUARANTINE_ARTIFACT_DIRS: tuple[str, ...] = (
    "quarantine_audit_reports",
    "quarantine_reviews",
    "quarantine_transition_requests",
    "quarantine_activation_plans",
    "quarantine_activation_reports",
)


# ── Data models ──


@dataclass
class CapabilityHealthFinding:
    """A single finding in a capability health report.

    Findings are read-only observations. They describe state, never
    prescribe mutation.
    """

    severity: str  # info, warning, error
    code: str
    message: str
    capability_id: str | None = None
    scope: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.severity not in _VALID_SEVERITIES:
            raise ValueError(
                f"Invalid severity {self.severity!r}, expected one of {sorted(_VALID_SEVERITIES)}"
            )


@dataclass
class CapabilityHealthReport:
    """Read-only health report for the capability system.

    Counters, findings, and recommendations are all computed from
    filesystem and index state at generation time. The report itself
    never mutates anything.
    """

    generated_at: str
    total_capabilities: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    by_maturity: dict[str, int] = field(default_factory=dict)
    by_scope: dict[str, int] = field(default_factory=dict)
    quarantined_count: int = 0
    testing_count: int = 0
    stable_count: int = 0
    broken_count: int = 0
    repairing_count: int = 0
    proposals_count: int = 0
    agent_candidates_count: int = 0
    trust_roots_count: int = 0
    stale_eval_count: int = 0
    stale_provenance_count: int = 0
    integrity_mismatch_count: int = 0
    missing_provenance_count: int = 0
    stale_trust_root_count: int = 0
    orphaned_artifact_count: int = 0
    index_drift_count: int = 0
    findings: list[CapabilityHealthFinding] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "total_capabilities": self.total_capabilities,
            "by_status": self.by_status,
            "by_maturity": self.by_maturity,
            "by_scope": self.by_scope,
            "quarantined_count": self.quarantined_count,
            "testing_count": self.testing_count,
            "stable_count": self.stable_count,
            "broken_count": self.broken_count,
            "repairing_count": self.repairing_count,
            "proposals_count": self.proposals_count,
            "agent_candidates_count": self.agent_candidates_count,
            "trust_roots_count": self.trust_roots_count,
            "stale_eval_count": self.stale_eval_count,
            "stale_provenance_count": self.stale_provenance_count,
            "integrity_mismatch_count": self.integrity_mismatch_count,
            "missing_provenance_count": self.missing_provenance_count,
            "stale_trust_root_count": self.stale_trust_root_count,
            "orphaned_artifact_count": self.orphaned_artifact_count,
            "index_drift_count": self.index_drift_count,
            "findings": [
                {
                    "severity": f.severity,
                    "code": f.code,
                    "message": f.message,
                    "capability_id": f.capability_id,
                    "scope": f.scope,
                    "details": f.details,
                }
                for f in self.findings
            ],
            "recommendations": self.recommendations,
        }


# ── Helpers ──


def _iter_all_capabilities(store: "CapabilityStore") -> list["CapabilityDocument"]:
    """Iterate all capabilities from the store, including disabled/archived/quarantined."""
    try:
        return store.list(include_disabled=True, include_archived=True, limit=10000)
    except Exception:
        logger.debug("Failed to list capabilities from store", exc_info=True)
        return []


def _quarantine_root(data_dir: Path) -> Path:
    return data_dir / "quarantine"


def _quarantine_cap_dirs(data_dir: Path) -> list[Path]:
    """List all quarantine capability directories."""
    root = _quarantine_root(data_dir)
    if not root.is_dir():
        return []
    result: list[Path] = []
    for entry in sorted(root.iterdir()):
        if entry.is_dir() and (entry / "CAPABILITY.md").exists():
            result.append(entry)
    return result


def _parse_iso_datetime(s: str | None) -> datetime | None:
    """Parse an ISO 8601 string to a UTC datetime, or None."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _build_recommendations(report: CapabilityHealthReport) -> list[str]:
    """Build human-readable, non-executable recommendations from findings."""
    recs: list[str] = []

    if report.index_drift_count > 0:
        recs.append(
            f"Index drift detected ({report.index_drift_count} entries). "
            "Consider running a manual index rebuild (CapabilityIndex.rebuild_from_store) "
            "to restore consistency."
        )

    if report.missing_provenance_count > 0:
        recs.append(
            f"{report.missing_provenance_count} capabilities are missing provenance records. "
            "For imported or quarantine-activated capabilities, this may indicate an interrupted "
            "import/activation. For legacy capabilities, provenance can be created manually."
        )

    if report.integrity_mismatch_count > 0:
        recs.append(
            f"{report.integrity_mismatch_count} capabilities have integrity mismatches "
            "(current tree hash does not match provenance source_content_hash). "
            "These capabilities may have been modified since import/activation. "
            "Review each case to determine if the change is legitimate."
        )

    if report.stale_eval_count > 0:
        recs.append(
            f"{report.stale_eval_count} testing or stable capabilities have stale or "
            "missing evaluation records. Consider re-running the evaluator for these "
            "capabilities to refresh their safety assessment."
        )

    if report.stale_trust_root_count > 0:
        recs.append(
            f"{report.stale_trust_root_count} trust roots are expired, disabled, revoked, "
            "or nearing expiry. Review trust root status and update or rotate as needed."
        )

    quarantine_findings = [
        f for f in report.findings
        if f.code.startswith("quarantine_") and f.severity != _SEVERITY_INFO
    ]
    if quarantine_findings:
        recs.append(
            f"{len(quarantine_findings)} quarantine backlog issues detected. "
            "Review quarantined capabilities and advance them through the pipeline "
            "(audit → review → transition request → activation plan → apply) "
            "or reject them if unsuitable."
        )

    proposal_findings = [
        f for f in report.findings
        if f.code.startswith("proposal_") and f.severity != _SEVERITY_INFO
    ]
    if proposal_findings:
        recs.append(
            f"{len(proposal_findings)} proposal backlog issues detected. "
            "Review pending proposals and either apply or discard them."
        )

    candidate_findings = [
        f for f in report.findings
        if f.code.startswith("candidate_") and f.severity != _SEVERITY_INFO
    ]
    if candidate_findings:
        recs.append(
            f"{len(candidate_findings)} agent candidate issues detected. "
            "Review pending candidates and approve, reject, or archive them."
        )

    if report.orphaned_artifact_count > 0:
        recs.append(
            f"{report.orphaned_artifact_count} orphaned or corrupt artifacts detected. "
            "These may be remnants of failed operations. Consider manual cleanup."
        )

    if report.broken_count > 0:
        recs.append(
            f"{report.broken_count} capabilities are in broken maturity. "
            "Review failure evidence and either transition to repairing or retire them."
        )

    return recs


# ── Check functions ──


def check_index_drift(
    store: "CapabilityStore",
    index: "CapabilityIndex | None",
) -> list[CapabilityHealthFinding]:
    """Compare store directories against index entries.

    Detects: entries in store but not index (missing index rows),
    entries in index but not store (stale rows).
    Does NOT rebuild the index.
    """
    findings: list[CapabilityHealthFinding] = []

    # Collect store entries: (id, scope)
    store_entries: set[tuple[str, str]] = set()
    for doc in _iter_all_capabilities(store):
        store_entries.add((doc.id, doc.scope.value))

    # Collect index entries
    index_entries: set[tuple[str, str]] = set()
    if index is not None and index._conn is not None:
        try:
            rows = index.conn.execute(
                "SELECT id, scope FROM capability_index"
            ).fetchall()
            index_entries = {(r["id"], r["scope"]) for r in rows}
        except Exception:
            logger.debug("Failed to query index for drift check", exc_info=True)

    if not index_entries and index is None:
        return findings

    # Missing from index (store has it, index doesn't)
    missing = store_entries - index_entries
    for cap_id, scope in sorted(missing):
        findings.append(CapabilityHealthFinding(
            severity=_SEVERITY_WARNING,
            code="index_missing_row",
            message=f"Capability {cap_id} ({scope}) exists in store but is missing from the index.",
            capability_id=cap_id,
            scope=scope,
            details={"issue": "missing_index_row"},
        ))

    # Stale index entries (index has it, store doesn't)
    stale = index_entries - store_entries
    for cap_id, scope in sorted(stale):
        findings.append(CapabilityHealthFinding(
            severity=_SEVERITY_WARNING,
            code="index_stale_row",
            message=f"Index has entry for {cap_id} ({scope}) but no corresponding store directory exists.",
            capability_id=cap_id,
            scope=scope,
            details={"issue": "stale_index_row"},
        ))

    return findings


def check_missing_provenance(
    store: "CapabilityStore",
) -> list[CapabilityHealthFinding]:
    """Check for capabilities missing provenance.json.

    - Quarantined caps: missing provenance is an error (import should have created it).
    - Active caps with import artifacts: missing provenance is a warning.
    - Other active caps (legacy/manual): missing provenance is info.
    """
    findings: list[CapabilityHealthFinding] = []

    for doc in _iter_all_capabilities(store):
        try:
            prov = read_provenance(doc.directory)
        except Exception:
            logger.debug("Failed to read provenance for %s", doc.id, exc_info=True)
            prov = None
        if prov is not None:
            continue

        status = doc.manifest.status
        has_import_artifacts = (
            (doc.directory / "import_report.json").exists()
            or (doc.directory / "activation_report.json").exists()
        )

        if status == CapabilityStatus.QUARANTINED:
            findings.append(CapabilityHealthFinding(
                severity=_SEVERITY_ERROR,
                code="missing_provenance_quarantined",
                message=f"Quarantined capability {doc.id} is missing provenance.json. "
                        "Import should have created this record.",
                capability_id=doc.id,
                scope=doc.manifest.scope.value,
                details={"status": status.value},
            ))
        elif has_import_artifacts:
            findings.append(CapabilityHealthFinding(
                severity=_SEVERITY_WARNING,
                code="missing_provenance_imported",
                message=f"Imported/activated capability {doc.id} is missing provenance.json.",
                capability_id=doc.id,
                scope=doc.manifest.scope.value,
                details={"status": status.value},
            ))
        elif status == CapabilityStatus.ARCHIVED:
            findings.append(CapabilityHealthFinding(
                severity=_SEVERITY_INFO,
                code="missing_provenance_archived",
                message=f"Archived capability {doc.id} has no provenance record.",
                capability_id=doc.id,
                scope=doc.manifest.scope.value,
                details={"status": status.value},
            ))
        else:
            findings.append(CapabilityHealthFinding(
                severity=_SEVERITY_INFO,
                code="missing_provenance_legacy",
                message=f"Capability {doc.id} has no provenance record (legacy/manual).",
                capability_id=doc.id,
                scope=doc.manifest.scope.value,
                details={"status": status.value},
            ))

    return findings


def check_integrity_mismatch(
    store: "CapabilityStore",
) -> list[CapabilityHealthFinding]:
    """Check for provenance integrity mismatches.

    For each capability with a provenance record, compares the current
    tree hash against the stored source_content_hash. Reports mismatches.
    Does NOT update provenance.
    """
    findings: list[CapabilityHealthFinding] = []

    for doc in _iter_all_capabilities(store):
        try:
            prov = read_provenance(doc.directory)
        except Exception:
            logger.debug("Failed to read provenance for %s", doc.id, exc_info=True)
            prov = None
        if prov is None:
            continue

        current_hash = compute_capability_tree_hash(doc.directory)
        if not current_hash or not prov.source_content_hash:
            continue

        if current_hash != prov.source_content_hash:
            findings.append(CapabilityHealthFinding(
                severity=_SEVERITY_WARNING,
                code="integrity_mismatch",
                message=(
                    f"Capability {doc.id} tree hash ({current_hash[:12]}...) "
                    f"does not match provenance source_content_hash "
                    f"({prov.source_content_hash[:12]}...). "
                    f"Content may have been modified since provenance was recorded."
                ),
                capability_id=doc.id,
                scope=doc.manifest.scope.value,
                details={
                    "current_hash": current_hash,
                    "provenance_hash": prov.source_content_hash,
                    "integrity_status": prov.integrity_status,
                },
            ))

    return findings


def check_stale_eval_records(
    store: "CapabilityStore",
    *,
    stale_days: int = DEFAULT_STALE_EVAL_DAYS,
) -> list[CapabilityHealthFinding]:
    """Check for stale or missing evaluation records.

    Testing and stable capabilities should have recent eval records.
    Does NOT create eval records.
    """
    findings: list[CapabilityHealthFinding] = []
    threshold = datetime.now(timezone.utc) - timedelta(days=stale_days)

    for doc in _iter_all_capabilities(store):
        maturity = doc.manifest.maturity
        if maturity not in (CapabilityMaturity.TESTING, CapabilityMaturity.STABLE):
            continue
        if doc.manifest.status in (CapabilityStatus.ARCHIVED, CapabilityStatus.DISABLED):
            continue

        latest = get_latest_eval_record(doc)
        if latest is None:
            findings.append(CapabilityHealthFinding(
                severity=_SEVERITY_WARNING,
                code="eval_missing",
                message=(
                    f"{maturity.value} capability {doc.id} has no evaluation record. "
                    f"Eval records are expected for testing/stable capabilities."
                ),
                capability_id=doc.id,
                scope=doc.manifest.scope.value,
                details={"maturity": maturity.value},
            ))
            continue

        eval_time = _parse_iso_datetime(latest.created_at)
        if eval_time is not None and eval_time < threshold:
            findings.append(CapabilityHealthFinding(
                severity=_SEVERITY_INFO,
                code="eval_stale",
                message=(
                    f"Capability {doc.id} last evaluated at {latest.created_at} "
                    f"(>{stale_days} days ago). Consider re-evaluating."
                ),
                capability_id=doc.id,
                scope=doc.manifest.scope.value,
                details={
                    "maturity": maturity.value,
                    "last_eval_at": latest.created_at,
                    "stale_days": stale_days,
                },
            ))

    return findings


def check_stale_trust_roots(
    trust_root_store: "TrustRootStore | None",
    *,
    expiry_warn_days: int = DEFAULT_TRUST_ROOT_EXPIRY_WARN_DAYS,
) -> list[CapabilityHealthFinding]:
    """Check trust roots for expiry, disabled, and revoked status.

    Does NOT revoke, disable, or modify any trust root.
    """
    findings: list[CapabilityHealthFinding] = []
    if trust_root_store is None:
        return findings

    try:
        all_roots = trust_root_store.list_trust_roots()
    except Exception:
        logger.debug("Failed to list trust roots", exc_info=True)
        return findings

    now = datetime.now(timezone.utc)
    warn_threshold = now + timedelta(days=expiry_warn_days)

    for root in all_roots:
        if root.status == "revoked":
            findings.append(CapabilityHealthFinding(
                severity=_SEVERITY_WARNING,
                code="trust_root_revoked",
                message=f"Trust root {root.trust_root_id} ({root.name}) is revoked.",
                details={
                    "trust_root_id": root.trust_root_id,
                    "status": root.status,
                },
            ))
        elif root.status == "disabled":
            findings.append(CapabilityHealthFinding(
                severity=_SEVERITY_INFO,
                code="trust_root_disabled",
                message=f"Trust root {root.trust_root_id} ({root.name}) is disabled.",
                details={
                    "trust_root_id": root.trust_root_id,
                    "status": root.status,
                },
            ))
        elif root.status == "active" and root.expires_at:
            expires = _parse_iso_datetime(root.expires_at)
            if expires is None:
                continue
            if expires <= now:
                findings.append(CapabilityHealthFinding(
                    severity=_SEVERITY_WARNING,
                    code="trust_root_expired",
                    message=(
                        f"Trust root {root.trust_root_id} ({root.name}) "
                        f"expired at {root.expires_at}."
                    ),
                    details={
                        "trust_root_id": root.trust_root_id,
                        "expires_at": root.expires_at,
                    },
                ))
            elif expires <= warn_threshold:
                findings.append(CapabilityHealthFinding(
                    severity=_SEVERITY_INFO,
                    code="trust_root_nearing_expiry",
                    message=(
                        f"Trust root {root.trust_root_id} ({root.name}) "
                        f"expires at {root.expires_at} (within {expiry_warn_days} days)."
                    ),
                    details={
                        "trust_root_id": root.trust_root_id,
                        "expires_at": root.expires_at,
                        "days_until_expiry": (expires - now).days,
                    },
                ))

    return findings


def check_quarantine_backlog(
    data_dir: Path,
) -> list[CapabilityHealthFinding]:
    """Check quarantine pipeline state for each quarantined capability.

    Reports gaps in the pipeline: audit → review → transition request →
    activation plan → apply. Does NOT create any pipeline artifacts.
    """
    findings: list[CapabilityHealthFinding] = []
    cap_dirs = _quarantine_cap_dirs(data_dir)

    for cap_dir in cap_dirs:
        cap_id = cap_dir.name

        # Check for manifest to determine status
        manifest_path = cap_dir / "manifest.json"
        try:
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            else:
                continue
        except (json.JSONDecodeError, OSError):
            continue

        has_audit = _dir_has_files(cap_dir / "quarantine_audit_reports")
        has_review = _dir_has_files(cap_dir / "quarantine_reviews")
        has_request = _dir_has_files(cap_dir / "quarantine_transition_requests")
        has_plan = _dir_has_files(cap_dir / "quarantine_activation_plans")
        has_activation = _dir_has_files(cap_dir / "quarantine_activation_reports")

        if has_activation:
            continue  # Pipeline complete

        if has_plan and not has_activation:
            findings.append(CapabilityHealthFinding(
                severity=_SEVERITY_INFO,
                code="quarantine_plan_pending_apply",
                message=f"Quarantined capability {cap_id} has an activation plan but is not yet applied.",
                capability_id=cap_id,
                details={"stage": "plan_pending_apply"},
            ))
            continue

        if has_request and not has_plan:
            findings.append(CapabilityHealthFinding(
                severity=_SEVERITY_INFO,
                code="quarantine_request_pending_plan",
                message=f"Quarantined capability {cap_id} has a transition request but no activation plan.",
                capability_id=cap_id,
                details={"stage": "request_pending_plan"},
            ))
            continue

        if has_review and not has_request:
            findings.append(CapabilityHealthFinding(
                severity=_SEVERITY_INFO,
                code="quarantine_review_pending_request",
                message=f"Quarantined capability {cap_id} has a review but no transition request.",
                capability_id=cap_id,
                details={"stage": "review_pending_request"},
            ))
            continue

        if has_audit and not has_review:
            findings.append(CapabilityHealthFinding(
                severity=_SEVERITY_WARNING,
                code="quarantine_audit_pending_review",
                message=f"Quarantined capability {cap_id} has an audit report but no review decision.",
                capability_id=cap_id,
                details={"stage": "audit_pending_review"},
            ))
            continue

        if not has_audit:
            findings.append(CapabilityHealthFinding(
                severity=_SEVERITY_WARNING,
                code="quarantine_no_audit",
                message=f"Quarantined capability {cap_id} has not been audited.",
                capability_id=cap_id,
                details={"stage": "no_audit"},
            ))

    return findings


def check_proposal_backlog(
    data_dir: Path,
    *,
    stale_days: int = DEFAULT_STALE_PROPOSAL_DAYS,
) -> list[CapabilityHealthFinding]:
    """Check proposal backlog: unapplied proposals, old proposals,
    and high-risk proposals awaiting approval.

    Does NOT apply or modify any proposals.
    """
    findings: list[CapabilityHealthFinding] = []
    proposals_dir = data_dir / "proposals"
    if not proposals_dir.is_dir():
        return findings

    threshold = datetime.now(timezone.utc) - timedelta(days=stale_days)

    for entry in sorted(proposals_dir.iterdir()):
        if not entry.is_dir():
            continue
        prop_json = entry / "proposal.json"
        if not prop_json.is_file():
            continue
        try:
            data = json.loads(prop_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            findings.append(CapabilityHealthFinding(
                severity=_SEVERITY_WARNING,
                code="proposal_corrupt",
                message=f"Proposal {entry.name} has corrupt or unreadable proposal.json.",
                details={"proposal_id": entry.name},
            ))
            continue

        prop_id = data.get("proposal_id", entry.name)
        applied = data.get("applied", False)
        risk_level = data.get("risk_level", "low")
        required_approval = data.get("required_approval", False)
        created_at = data.get("created_at", "")

        if applied:
            continue

        created = _parse_iso_datetime(created_at)

        if required_approval and risk_level == "high":
            findings.append(CapabilityHealthFinding(
                severity=_SEVERITY_WARNING,
                code="proposal_high_risk_pending",
                message=(
                    f"High-risk proposal {prop_id} requires approval "
                    f"and has not been applied."
                ),
                details={
                    "proposal_id": prop_id,
                    "risk_level": risk_level,
                    "required_approval": required_approval,
                },
            ))
        elif created is not None and created < threshold:
            findings.append(CapabilityHealthFinding(
                severity=_SEVERITY_INFO,
                code="proposal_stale",
                message=(
                    f"Proposal {prop_id} was created at {created_at} "
                    f"(>{stale_days} days ago) and has not been applied."
                ),
                details={
                    "proposal_id": prop_id,
                    "created_at": created_at,
                    "stale_days": stale_days,
                },
            ))
        elif not applied:
            findings.append(CapabilityHealthFinding(
                severity=_SEVERITY_INFO,
                code="proposal_pending",
                message=f"Proposal {prop_id} is pending (not yet applied).",
                details={"proposal_id": prop_id},
            ))

    return findings


def check_agent_candidate_backlog(
    candidate_store: "AgentCandidateStore | None",
) -> list[CapabilityHealthFinding]:
    """Check agent candidate backlog: pending candidates,
    approved but not saved, stale evidence, high-risk without evidence.

    Does NOT modify any candidates.
    """
    findings: list[CapabilityHealthFinding] = []
    if candidate_store is None:
        return findings

    try:
        candidates = candidate_store.list_candidates()
    except Exception:
        logger.debug("Failed to list agent candidates", exc_info=True)
        return findings

    for cand in candidates:
        cid = cand.candidate_id

        if cand.approval_state == "pending":
            has_evidence = len(cand.eval_evidence) > 0
            if cand.risk_level == "high" and not has_evidence:
                findings.append(CapabilityHealthFinding(
                    severity=_SEVERITY_WARNING,
                    code="candidate_high_risk_no_evidence",
                    message=(
                        f"High-risk agent candidate {cid} is pending "
                        f"and has no evaluation evidence."
                    ),
                    details={
                        "candidate_id": cid,
                        "risk_level": cand.risk_level,
                        "approval_state": cand.approval_state,
                    },
                ))
            elif cand.risk_level == "high":
                findings.append(CapabilityHealthFinding(
                    severity=_SEVERITY_INFO,
                    code="candidate_high_risk_pending",
                    message=(
                        f"High-risk agent candidate {cid} is pending approval "
                        f"with {len(cand.eval_evidence)} evidence record(s)."
                    ),
                    details={
                        "candidate_id": cid,
                        "risk_level": cand.risk_level,
                        "evidence_count": len(cand.eval_evidence),
                    },
                ))
            else:
                findings.append(CapabilityHealthFinding(
                    severity=_SEVERITY_INFO,
                    code="candidate_pending",
                    message=f"Agent candidate {cid} is pending approval.",
                    details={
                        "candidate_id": cid,
                        "approval_state": cand.approval_state,
                    },
                ))

        elif cand.approval_state == "approved":
            findings.append(CapabilityHealthFinding(
                severity=_SEVERITY_INFO,
                code="candidate_approved_not_saved",
                message=(
                    f"Agent candidate {cid} is approved but may not be saved "
                    f"as a persistent agent yet."
                ),
                details={
                    "candidate_id": cid,
                    "approval_state": cand.approval_state,
                },
            ))

        elif cand.approval_state == "rejected":
            findings.append(CapabilityHealthFinding(
                severity=_SEVERITY_INFO,
                code="candidate_rejected",
                message=(
                    f"Agent candidate {cid} was rejected. "
                    f"Consider archiving it."
                ),
                details={
                    "candidate_id": cid,
                    "approval_state": cand.approval_state,
                },
            ))

    return findings


def check_orphaned_artifacts(
    data_dir: Path,
    trust_root_store: "TrustRootStore | None" = None,
) -> list[CapabilityHealthFinding]:
    """Check for orphaned or corrupt artifacts.

    Detects: corrupt trust root files, quarantine directories without
    valid manifests, and other dangling artifacts. Does NOT delete anything.
    """
    findings: list[CapabilityHealthFinding] = []

    # Check trust roots for corrupt files
    if trust_root_store is not None:
        roots_dir = trust_root_store.roots_dir
        if roots_dir.is_dir():
            for file_path in sorted(roots_dir.glob("*.json")):
                try:
                    data = json.loads(file_path.read_text(encoding="utf-8"))
                    if not isinstance(data, dict):
                        findings.append(CapabilityHealthFinding(
                            severity=_SEVERITY_WARNING,
                            code="orphaned_corrupt_trust_root",
                            message=(
                                f"Trust root file {file_path.name} does not contain "
                                f"a valid JSON object."
                            ),
                            details={"file": str(file_path)},
                        ))
                except (json.JSONDecodeError, OSError):
                    findings.append(CapabilityHealthFinding(
                        severity=_SEVERITY_WARNING,
                        code="orphaned_corrupt_trust_root",
                        message=(
                            f"Trust root file {file_path.name} is corrupt or "
                            f"unparseable."
                        ),
                        details={"file": str(file_path)},
                    ))

    # Check quarantine root for directories without CAPABILITY.md
    quarantine_root = _quarantine_root(data_dir)
    if quarantine_root.is_dir():
        for entry in sorted(quarantine_root.iterdir()):
            if not entry.is_dir():
                continue
            if not (entry / "CAPABILITY.md").exists():
                # Directory in quarantine without a capability manifest
                has_artifacts = any(
                    (entry / subdir).is_dir()
                    for subdir in _QUARANTINE_ARTIFACT_DIRS
                )
                if has_artifacts:
                    findings.append(CapabilityHealthFinding(
                        severity=_SEVERITY_WARNING,
                        code="orphaned_quarantine_artifacts",
                        message=(
                            f"Quarantine directory {entry.name} has pipeline "
                            f"artifacts but no CAPABILITY.md manifest."
                        ),
                        details={"directory": str(entry)},
                    ))
                else:
                    findings.append(CapabilityHealthFinding(
                        severity=_SEVERITY_INFO,
                        code="orphaned_empty_quarantine",
                        message=(
                            f"Quarantine directory {entry.name} has no "
                            f"CAPABILITY.md and no pipeline artifacts."
                        ),
                        details={"directory": str(entry)},
                    ))

    return findings


def _dir_has_files(directory: Path) -> bool:
    """Return True if the directory exists and contains at least one file."""
    if not directory.is_dir():
        return False
    for entry in directory.iterdir():
        if entry.is_file():
            return True
    return False


# ── Main report generator ──


def generate_capability_health_report(
    store: "CapabilityStore",
    *,
    index: "CapabilityIndex | None" = None,
    trust_root_store: "TrustRootStore | None" = None,
    candidate_store: "AgentCandidateStore | None" = None,
    data_dir: Path | str | None = None,
    stale_eval_days: int = DEFAULT_STALE_EVAL_DAYS,
    stale_proposal_days: int = DEFAULT_STALE_PROPOSAL_DAYS,
    trust_root_expiry_warn_days: int = DEFAULT_TRUST_ROOT_EXPIRY_WARN_DAYS,
) -> CapabilityHealthReport:
    """Generate a read-only capability health report.

    Collects capability inventory counts, runs all health checks, and
    generates non-executable recommendations. No mutation is performed
    on any file, index, lifecycle state, proposal, candidate, or trust root.

    Args:
        store: CapabilityStore for listing capabilities.
        index: Optional CapabilityIndex for drift detection.
        trust_root_store: Optional TrustRootStore for trust root checks.
        candidate_store: Optional AgentCandidateStore for candidate checks.
        data_dir: Path to the data directory (defaults to store.data_dir).
        stale_eval_days: Days after which an eval record is considered stale.
        stale_proposal_days: Days after which a proposal is considered stale.
        trust_root_expiry_warn_days: Days before expiry to warn about trust roots.

    Returns:
        CapabilityHealthReport with counts, findings, and recommendations.
    """
    resolved_data_dir = Path(data_dir) if data_dir is not None else store.data_dir
    generated_at = datetime.now(timezone.utc).isoformat()

    report = CapabilityHealthReport(generated_at=generated_at)
    all_findings: list[CapabilityHealthFinding] = []

    # ── 1. Inventory counts ──────────────────────────────────────────

    all_caps = _iter_all_capabilities(store)
    report.total_capabilities = len(all_caps)

    by_status: dict[str, int] = {}
    by_maturity: dict[str, int] = {}
    by_scope: dict[str, int] = {}

    for doc in all_caps:
        s = doc.manifest.status.value
        m = doc.manifest.maturity.value
        sc = doc.manifest.scope.value

        by_status[s] = by_status.get(s, 0) + 1
        by_maturity[m] = by_maturity.get(m, 0) + 1
        by_scope[sc] = by_scope.get(sc, 0) + 1

    report.by_status = by_status
    report.by_maturity = by_maturity
    report.by_scope = by_scope
    report.quarantined_count = by_status.get("quarantined", 0)
    report.testing_count = by_maturity.get("testing", 0)
    report.stable_count = by_maturity.get("stable", 0)
    report.broken_count = by_maturity.get("broken", 0)
    report.repairing_count = by_maturity.get("repairing", 0)

    # ── 2. Check functions ───────────────────────────────────────────

    drift_findings = check_index_drift(store, index)
    report.index_drift_count = len(drift_findings)
    all_findings.extend(drift_findings)

    provenance_findings = check_missing_provenance(store)
    report.missing_provenance_count = len(provenance_findings)
    all_findings.extend(provenance_findings)

    integrity_findings = check_integrity_mismatch(store)
    report.integrity_mismatch_count = len(integrity_findings)
    all_findings.extend(integrity_findings)

    eval_findings = check_stale_eval_records(store, stale_days=stale_eval_days)
    report.stale_eval_count = len(eval_findings)
    all_findings.extend(eval_findings)

    trust_findings = check_stale_trust_roots(
        trust_root_store,
        expiry_warn_days=trust_root_expiry_warn_days,
    )
    report.stale_trust_root_count = len(trust_findings)
    all_findings.extend(trust_findings)

    quarantine_findings = check_quarantine_backlog(resolved_data_dir)
    all_findings.extend(quarantine_findings)

    proposal_findings = check_proposal_backlog(
        resolved_data_dir,
        stale_days=stale_proposal_days,
    )
    all_findings.extend(proposal_findings)

    candidate_findings = check_agent_candidate_backlog(candidate_store)
    all_findings.extend(candidate_findings)

    orphan_findings = check_orphaned_artifacts(resolved_data_dir, trust_root_store)
    report.orphaned_artifact_count = len(orphan_findings)
    all_findings.extend(orphan_findings)

    # ── 3. Counts for proposals / candidates / trust roots ──────────

    if trust_root_store is not None:
        try:
            report.trust_roots_count = len(trust_root_store.list_trust_roots())
        except Exception:
            pass

    if candidate_store is not None:
        try:
            report.agent_candidates_count = len(candidate_store.list_candidates())
        except Exception:
            pass

    # Count proposals from filesystem
    proposals_dir = resolved_data_dir / "proposals"
    if proposals_dir.is_dir():
        try:
            report.proposals_count = sum(
                1 for e in proposals_dir.iterdir()
                if e.is_dir() and (e / "proposal.json").is_file()
            )
        except Exception:
            pass

    # ── 4. Assemble ──────────────────────────────────────────────────

    report.stale_provenance_count = report.missing_provenance_count + report.integrity_mismatch_count
    report.findings = all_findings
    report.recommendations = _build_recommendations(report)

    return report
