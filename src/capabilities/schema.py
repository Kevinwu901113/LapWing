"""Pydantic models and enums for the capability document system.

This is a non-runtime data model. Nothing here is wired into Brain,
TaskRuntime, StateViewBuilder, SkillExecutor, or ToolDispatcher.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


# ── Enums ──────────────────────────────────────────────────────────────


class CapabilityType(str, Enum):
    SKILL = "skill"
    WORKFLOW = "workflow"
    DYNAMIC_AGENT = "dynamic_agent"
    MEMORY_PATTERN = "memory_pattern"
    TOOL_WRAPPER = "tool_wrapper"
    PROJECT_PLAYBOOK = "project_playbook"


class CapabilityScope(str, Enum):
    GLOBAL = "global"
    USER = "user"
    WORKSPACE = "workspace"
    SESSION = "session"


class CapabilityMaturity(str, Enum):
    DRAFT = "draft"
    TESTING = "testing"
    STABLE = "stable"
    BROKEN = "broken"
    REPAIRING = "repairing"


class CapabilityStatus(str, Enum):
    ACTIVE = "active"
    BROKEN = "broken"
    REPAIRING = "repairing"
    DISABLED = "disabled"
    ARCHIVED = "archived"
    QUARANTINED = "quarantined"
    NEEDS_PERMISSION = "needs_permission"
    ENVIRONMENT_MISMATCH = "environment_mismatch"


class CapabilityRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SensitiveContext(str, Enum):
    PERSONAL_DATA = "personal_data"
    CREDENTIALS = "credentials"
    FINANCIAL = "financial"
    MEDICAL = "medical"
    LEGAL = "legal"
    PRIVATE_PROJECT = "private_project"
    PRIVATE_COMMUNICATIONS = "private_communications"
    IDENTITY = "identity"
    EXTERNAL_PUBLICATION = "external_publication"


class SideEffect(str, Enum):
    NONE = "none"
    LOCAL_WRITE = "local_write"
    LOCAL_DELETE = "local_delete"
    SHELL_EXEC = "shell_exec"
    NETWORK_SEND = "network_send"
    PUBLIC_OUTPUT = "public_output"
    EXTERNAL_MUTATION = "external_mutation"
    CREDENTIAL_ACCESS = "credential_access"


class RollbackMechanism(str, Enum):
    BACKUP_FILE = "backup_file"
    TRANSACTION = "transaction"
    INVERSE_API_CALL = "inverse_api_call"
    DRAFT_ONLY = "draft_only"
    GIT_REVERT = "git_revert"
    SANDBOX_ONLY = "sandbox_only"


# ── Allowed value sets (for validation error messages) ─────────────────

ALLOWED_TYPES: frozenset[str] = frozenset(e.value for e in CapabilityType)
ALLOWED_SCOPES: frozenset[str] = frozenset(e.value for e in CapabilityScope)
ALLOWED_MATURITIES: frozenset[str] = frozenset(e.value for e in CapabilityMaturity)
ALLOWED_STATUSES: frozenset[str] = frozenset(e.value for e in CapabilityStatus)
ALLOWED_RISK_LEVELS: frozenset[str] = frozenset(e.value for e in CapabilityRiskLevel)
ALLOWED_SENSITIVE_CONTEXTS: frozenset[str] = frozenset(e.value for e in SensitiveContext)
ALLOWED_SIDE_EFFECTS: frozenset[str] = frozenset(e.value for e in SideEffect)
ALLOWED_ROLLBACK_MECHANISMS: frozenset[str] = frozenset(e.value for e in RollbackMechanism)

# Fields that are set by the parser / runtime and must NOT appear in
# manifest.json or CAPABILITY.md front matter (they would cause
# self-referential hash churn).
COMPUTED_FIELDS: frozenset[str] = frozenset({"content_hash", "created_at", "updated_at"})

# Fields that must be present in the manifest / front matter.
REQUIRED_METADATA_FIELDS: tuple[str, ...] = (
    "id", "name", "description", "type", "scope", "version",
    "maturity", "status", "risk_level",
)


# ── Manifest model ─────────────────────────────────────────────────────


class CapabilityManifest(BaseModel):
    """The resolved metadata for a capability after merging manifest.json
    and CAPABILITY.md front matter.

    ``extra`` is the extensibility escape hatch — any fields not defined
    in the v1 schema land here instead of crashing the parser.
    """

    model_config = {"extra": "ignore"}

    # Required
    id: str
    name: str
    description: str
    type: CapabilityType
    scope: CapabilityScope
    version: str
    maturity: CapabilityMaturity
    status: CapabilityStatus
    risk_level: CapabilityRiskLevel

    # Optional with defaults
    trust_required: str = "developer"
    required_tools: list[str] = Field(default_factory=list)
    required_permissions: list[str] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    do_not_apply_when: list[str] = Field(default_factory=list)
    sensitive_contexts: list[SensitiveContext] = Field(default_factory=list)
    reuse_boundary: str | None = None
    required_preflight_checks: list[str] = Field(default_factory=list)
    side_effects: list[SideEffect] = Field(default_factory=list)
    rollback_available: bool | None = None
    rollback_mechanism: RollbackMechanism | None = None

    # Computed (set by parser, never from user input)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    content_hash: str = ""

    # Extensibility escape hatch
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _reject_computed_fields_in_extra(self) -> "CapabilityManifest":
        for field in COMPUTED_FIELDS:
            if field in self.extra:
                self.extra.pop(field)
        return self
