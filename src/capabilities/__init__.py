"""Capability Evolution System — Phase 0/1 non-runtime foundation + Phase 2A store/index.

This package is NOT wired into Brain, TaskRuntime, StateViewBuilder,
SkillExecutor, ToolDispatcher, or agent execution paths.

Public API:
  parse_capability(directory) -> CapabilityDocument
  CapabilityStore, CapabilityIndex
  CapabilityManifest, CapabilityDocument
  CapabilityType, CapabilityScope, CapabilityMaturity, CapabilityStatus, CapabilityRiskLevel
  compute_content_hash
  generate_capability_id, is_valid_capability_id
  VersionSnapshot, create_version_snapshot, list_version_snapshots
"""

from src.capabilities.document import CapabilityDocument, CapabilityParser, parse_capability
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
    # Errors
    "CapabilityError",
    "InvalidManifestError",
    "InvalidDocumentError",
    "MissingFieldError",
    "InvalidEnumValueError",
    "HashVerificationError",
    "MalformedFrontMatterError",
]
