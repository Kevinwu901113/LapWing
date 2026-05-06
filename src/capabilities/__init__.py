"""Capability Evolution System — Phase 0/1 non-runtime foundation + Phase 2A/B store/index/tools
+ Phase 3A policy/evaluator/records/promotion + Phase 3B lifecycle manager
+ Phase 4 CapabilityRetriever + progressive disclosure
+ Phase 5A: ExperienceCurator + CapabilityProposal
+ Phase 8B-2: TrustRootStore.
+ Maintenance A: CapabilityHealthReport.
+ Maintenance B: RepairQueueItem, RepairQueueStore.

This package is NOT wired into Brain, TaskRuntime, SkillExecutor,
ToolDispatcher, or agent execution paths. StateViewBuilder receives
precomputed summaries from container/services.

Public API:
  parse_capability(directory) -> CapabilityDocument
  CapabilityStore, CapabilityIndex
  CapabilityManifest, CapabilityDocument
  CapabilityType, CapabilityScope, CapabilityMaturity, CapabilityStatus, CapabilityRiskLevel
  compute_content_hash
  generate_capability_id, is_valid_capability_id
  VersionSnapshot, create_version_snapshot, list_version_snapshots
  CapabilityPolicy, PolicyDecision, PolicySeverity
  CapabilityEvaluator, EvalRecord, EvalFinding, FindingSeverity
  write_eval_record, read_eval_record, list_eval_records, get_latest_eval_record
  PromotionPlanner, PromotionPlan
  CapabilityLifecycleManager, TransitionResult
  CapabilityRetriever, CapabilitySummary, RetrievalContext
  TraceSummary, ExperienceCurator, CuratorDecision, CuratedExperience
  CapabilityProposal, persist_proposal, load_proposal, list_proposals
  TrustRootStore
  CapabilityHealthReport, CapabilityHealthFinding, generate_capability_health_report
"""

from src.capabilities.document import CapabilityDocument, CapabilityParser, parse_capability
from src.capabilities.eval_records import (
    get_latest_eval_record,
    list_eval_records,
    read_eval_record,
    write_eval_record,
)
from src.capabilities.evaluator import (
    EvalFinding,
    EvalRecord,
    FindingSeverity,
    CapabilityEvaluator,
)
from src.capabilities.policy import (
    CapabilityPolicy,
    PolicyDecision,
    PolicySeverity,
)
from src.capabilities.promotion import PromotionPlan, PromotionPlanner
from src.capabilities.errors import (
    CapabilityError,
    HashVerificationError,
    InvalidDocumentError,
    InvalidEnumValueError,
    InvalidManifestError,
    MalformedFrontMatterError,
    MissingFieldError,
)
from src.capabilities.hashing import compute_content_hash
from src.capabilities.ids import generate_capability_id, is_valid_capability_id
from src.capabilities.index import CapabilityIndex
from src.capabilities.lifecycle import CapabilityLifecycleManager, TransitionResult
from src.capabilities.schema import (
    ALLOWED_MATURITIES,
    ALLOWED_RISK_LEVELS,
    ALLOWED_SCOPES,
    ALLOWED_STATUSES,
    ALLOWED_TYPES,
    CapabilityManifest,
    CapabilityMaturity,
    CapabilityRiskLevel,
    CapabilityScope,
    CapabilityStatus,
    CapabilityType,
)
from src.capabilities.search import (
    SCOPE_PRECEDENCE,
    deduplicate_by_precedence,
    filter_active,
    filter_by_scope,
    filter_by_tags,
    filter_by_type,
    filter_stable,
    filter_trust_level,
    resolve_by_scope,
    sort_by_maturity,
    sort_by_name,
    sort_by_updated,
    text_search,
)
from src.capabilities.retriever import (
    CapabilityRetriever,
    CapabilitySummary,
    RetrievalContext,
)
from src.capabilities.curator import CuratedExperience, CuratorDecision, ExperienceCurator
from src.capabilities.proposal import (
    CapabilityProposal,
    list_proposals,
    load_proposal,
    persist_proposal,
)
from src.capabilities.provenance import (
    PROVENANCE_INTEGRITY_STATUSES,
    PROVENANCE_SIGNATURE_STATUSES,
    PROVENANCE_SOURCE_TYPES,
    PROVENANCE_TRUST_LEVELS,
    CapabilityProvenance,
    CapabilityTrustPolicy,
    TrustDecision,
    compute_capability_tree_hash,
    compute_package_hash,
    read_provenance,
    update_provenance_integrity_status,
    verify_content_hash_against_provenance,
    write_provenance,
)
from src.capabilities.signature import (
    CapabilitySignature,
    CapabilityTrustRoot,
    SignatureVerificationResult,
    parse_signature_dict,
    parse_trust_root_dict,
    read_signature,
    verify_signature_stub,
    write_signature,
)
from src.capabilities.trust_roots import TrustRootStore
from src.capabilities.trace_summary import TraceSummary
from src.capabilities.import_quarantine import (
    ImportResult,
    InspectResult,
    import_capability_package,
    inspect_capability_package,
)
from src.capabilities.quarantine_review import (
    AuditFinding,
    AuditReport,
    ReviewDecision,
    audit_quarantined_capability,
    list_quarantined_capabilities,
    mark_quarantine_review,
    view_quarantine_report,
)
from src.capabilities.quarantine_transition import (
    QuarantineTransitionRequest,
    cancel_quarantine_transition_request,
    list_quarantine_transition_requests,
    request_quarantine_testing_transition,
    view_quarantine_transition_request,
)
from src.capabilities.quarantine_activation_planner import (
    QuarantineActivationPlan,
    list_quarantine_activation_plans,
    plan_quarantine_activation,
    view_quarantine_activation_plan,
)
from src.capabilities.health import (
    CapabilityHealthFinding,
    CapabilityHealthReport,
    generate_capability_health_report,
)
from src.capabilities.repair_queue import (
    RepairQueueItem,
    RepairQueueStore,
)
from src.capabilities.store import CapabilityStore
from src.capabilities.versioning import (
    VersionSnapshot,
    create_version_snapshot,
    list_version_snapshots,
    snapshot_on_archive,
    snapshot_on_disable,
)

__all__ = [
    # Document
    "CapabilityDocument",
    "CapabilityParser",
    "parse_capability",
    # Store / Index
    "CapabilityStore",
    "CapabilityIndex",
    # Schema
    "CapabilityManifest",
    "CapabilityType",
    "CapabilityScope",
    "CapabilityMaturity",
    "CapabilityStatus",
    "CapabilityRiskLevel",
    "ALLOWED_TYPES",
    "ALLOWED_SCOPES",
    "ALLOWED_MATURITIES",
    "ALLOWED_STATUSES",
    "ALLOWED_RISK_LEVELS",
    # Hashing
    "compute_content_hash",
    # IDs
    "generate_capability_id",
    "is_valid_capability_id",
    # Search helpers
    "SCOPE_PRECEDENCE",
    "filter_active",
    "filter_by_tags",
    "filter_by_type",
    "filter_by_scope",
    "filter_stable",
    "filter_trust_level",
    "text_search",
    "deduplicate_by_precedence",
    "resolve_by_scope",
    "sort_by_name",
    "sort_by_maturity",
    "sort_by_updated",
    # Versioning
    "VersionSnapshot",
    "create_version_snapshot",
    "list_version_snapshots",
    "snapshot_on_disable",
    "snapshot_on_archive",
    # Phase 3A — Policy
    "CapabilityPolicy",
    "PolicyDecision",
    "PolicySeverity",
    # Phase 3A — Evaluator
    "CapabilityEvaluator",
    "EvalRecord",
    "EvalFinding",
    "FindingSeverity",
    # Phase 3A — Eval records
    "write_eval_record",
    "read_eval_record",
    "list_eval_records",
    "get_latest_eval_record",
    # Phase 3A — Promotion
    "PromotionPlanner",
    "PromotionPlan",
    # Phase 3B — Lifecycle
    "CapabilityLifecycleManager",
    "TransitionResult",
    # Phase 4 — Retriever + progressive disclosure
    "CapabilityRetriever",
    "CapabilitySummary",
    "RetrievalContext",
    # Phase 5A — Curator + Proposal
    "TraceSummary",
    "ExperienceCurator",
    "CuratorDecision",
    "CuratedExperience",
    "CapabilityProposal",
    "persist_proposal",
    "load_proposal",
    "list_proposals",
    # Phase 7A — External import / quarantine
    "InspectResult",
    "ImportResult",
    "inspect_capability_package",
    "import_capability_package",
    # Phase 7B — Quarantine review / audit
    "AuditFinding",
    "AuditReport",
    "ReviewDecision",
    "list_quarantined_capabilities",
    "view_quarantine_report",
    "audit_quarantined_capability",
    "mark_quarantine_review",
    # Phase 7C — Quarantine transition requests
    "QuarantineTransitionRequest",
    "request_quarantine_testing_transition",
    "list_quarantine_transition_requests",
    "view_quarantine_transition_request",
    "cancel_quarantine_transition_request",
    # Phase 7D-A — Quarantine activation planner
    "QuarantineActivationPlan",
    "plan_quarantine_activation",
    "list_quarantine_activation_plans",
    "view_quarantine_activation_plan",
    # Errors
    "CapabilityError",
    "InvalidManifestError",
    "InvalidDocumentError",
    "MissingFieldError",
    "InvalidEnumValueError",
    "HashVerificationError",
    "MalformedFrontMatterError",
    # Phase 8B-1 — Signature metadata / verifier stub
    "CapabilitySignature",
    "CapabilityTrustRoot",
    "SignatureVerificationResult",
    "parse_signature_dict",
    "parse_trust_root_dict",
    "read_signature",
    "write_signature",
    "verify_signature_stub",
    # Phase 8B-2 — Trust root store
    "TrustRootStore",
    # Maintenance A — Health report
    "CapabilityHealthReport",
    "CapabilityHealthFinding",
    "generate_capability_health_report",
    # Maintenance B — Repair queue
    "RepairQueueItem",
    "RepairQueueStore",
    # Phase 8A-1 — Provenance / integrity foundation
    "CapabilityProvenance",
    "TrustDecision",
    "CapabilityTrustPolicy",
    "PROVENANCE_SOURCE_TYPES",
    "PROVENANCE_TRUST_LEVELS",
    "PROVENANCE_INTEGRITY_STATUSES",
    "PROVENANCE_SIGNATURE_STATUSES",
    "compute_capability_tree_hash",
    "compute_package_hash",
    "verify_content_hash_against_provenance",
    "write_provenance",
    "read_provenance",
    "update_provenance_integrity_status",
]
