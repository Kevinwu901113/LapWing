"""Capability Evolution System — Phase 0/1 non-runtime foundation + Phase 2A/B store/index/tools
+ Phase 3A policy/evaluator/records/promotion + Phase 3B lifecycle manager
+ Phase 4 CapabilityRetriever + progressive disclosure.

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
    # Errors
    "CapabilityError",
    "InvalidManifestError",
    "InvalidDocumentError",
    "MissingFieldError",
    "InvalidEnumValueError",
    "HashVerificationError",
    "MalformedFrontMatterError",
]
