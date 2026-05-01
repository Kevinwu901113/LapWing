"""Deterministic capability policy layer.

Returns structured allow/deny decisions. Never mutates store state,
calls LLMs, executes scripts, or registers tools.

Phase 3A: policy foundation only — not wired into runtime paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from src.capabilities.schema import (
    ALLOWED_MATURITIES,
    ALLOWED_RISK_LEVELS,
    ALLOWED_SCOPES,
    ALLOWED_STATUSES,
    ALLOWED_TYPES,
    CapabilityMaturity,
    CapabilityRiskLevel,
    CapabilityStatus,
)

if TYPE_CHECKING:
    from src.capabilities.schema import CapabilityManifest


# ── Decision model ──────────────────────────────────────────────────────


class PolicySeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class PolicyDecision:
    allowed: bool
    severity: PolicySeverity = PolicySeverity.INFO
    code: str = ""
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def allow(cls, code: str = "policy_allow", message: str = "", **details) -> "PolicyDecision":
        return cls(allowed=True, severity=PolicySeverity.INFO, code=code, message=message, details=details)

    @classmethod
    def deny(cls, code: str, message: str, severity: PolicySeverity = PolicySeverity.ERROR, **details) -> "PolicyDecision":
        return cls(allowed=False, severity=severity, code=code, message=message, details=details)

    @classmethod
    def warn(cls, code: str, message: str, **details) -> "PolicyDecision":
        return cls(allowed=True, severity=PolicySeverity.WARNING, code=code, message=message, details=details)


# ── Helpers ─────────────────────────────────────────────────────────────


def _get_field(obj: Any, name: str, default: Any = None) -> Any:
    """Safely get a field from a dict-like or object-like value."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


# ── Policy ──────────────────────────────────────────────────────────────


class CapabilityPolicy:
    """Deterministic policy decisions for capability lifecycle operations.

    All methods are pure — they accept data and return decisions.
    No store mutation, no LLM calls, no script execution, no tool registration.
    """

    def validate_create(self, manifest: "CapabilityManifest", context: dict[str, Any] | None = None) -> PolicyDecision:
        """Validate that a capability manifest is acceptable for creation."""
        ctx = context if isinstance(context, dict) else {}

        decisions = [
            self.validate_scope(manifest, ctx),
            self._validate_type(manifest, ctx),
            self._validate_maturity(manifest, ctx),
            self._validate_status(manifest, ctx),
            self._validate_risk_level(manifest, ctx),
            self.validate_risk(manifest, ctx),
            self.validate_required_tools(manifest, ctx.get("available_tools"), ctx),
        ]

        for d in decisions:
            if not d.allowed and d.severity == PolicySeverity.ERROR:
                return d

        warnings = [d for d in decisions if d.severity == PolicySeverity.WARNING]
        if warnings:
            return PolicyDecision.warn(
                "create_with_warnings",
                f"Create allowed with {len(warnings)} warning(s)",
                warnings=[{"code": w.code, "message": w.message} for w in warnings],
            )

        return PolicyDecision.allow("create_allowed", "Capability creation is allowed")

    def validate_patch(
        self,
        old_manifest: "CapabilityManifest",
        new_manifest: "CapabilityManifest",
        context: dict[str, Any] | None = None,
    ) -> PolicyDecision:
        """Validate a patch (update) from old_manifest to new_manifest."""
        ctx = context if isinstance(context, dict) else {}

        if old_manifest.id != new_manifest.id:
            return PolicyDecision.deny("patch_id_mismatch", "Cannot change capability id during patch")

        if old_manifest.scope != new_manifest.scope:
            return PolicyDecision.deny("patch_scope_change", "Cannot change capability scope during patch")

        decisions = [
            self.validate_scope(new_manifest, ctx),
            self._validate_type(new_manifest, ctx),
            self._validate_maturity(new_manifest, ctx),
            self._validate_status(new_manifest, ctx),
            self._validate_risk_level(new_manifest, ctx),
            self.validate_risk(new_manifest, ctx),
            self.validate_required_tools(new_manifest, ctx.get("available_tools"), ctx),
        ]

        for d in decisions:
            if not d.allowed and d.severity == PolicySeverity.ERROR:
                return d

        return PolicyDecision.allow("patch_allowed", "Capability patch is allowed")

    def validate_promote(
        self,
        manifest: "CapabilityManifest",
        eval_record: Any | None = None,
        approval: Any | None = None,
        context: dict[str, Any] | None = None,
    ) -> PolicyDecision:
        """Validate whether a capability is eligible for promotion."""
        ctx = context if isinstance(context, dict) else {}

        status = manifest.status
        if status == CapabilityStatus.QUARANTINED:
            return PolicyDecision.deny(
                "promote_quarantined",
                "Quarantined capability cannot be promoted",
            )
        if status == CapabilityStatus.ARCHIVED:
            return PolicyDecision.deny(
                "promote_archived",
                "Archived capability cannot be promoted",
            )

        risk = manifest.risk_level

        if risk == CapabilityRiskLevel.HIGH:
            if approval is None:
                return PolicyDecision.deny(
                    "promote_high_risk_no_approval",
                    "High risk capability promotion requires explicit owner approval",
                )
            if not _get_field(approval, "approved", False):
                return PolicyDecision.deny(
                    "promote_high_risk_denied",
                    "High risk capability promotion requires explicit owner approval",
                )
            return PolicyDecision.allow(
                "promote_high_risk_approved",
                "High risk promotion allowed with explicit approval",
            )

        if risk == CapabilityRiskLevel.MEDIUM:
            has_approval = bool(_get_field(approval, "approved", False)) if approval is not None else False
            has_eval = eval_record is not None
            if not has_approval and not has_eval:
                return PolicyDecision.deny(
                    "promote_medium_risk_needs_approval_or_eval",
                    "Medium risk promotion requires either approval or sufficient eval evidence",
                )
            if has_approval:
                return PolicyDecision.allow(
                    "promote_medium_risk_approved",
                    "Medium risk promotion allowed with approval",
                )
            if has_eval:
                eval_passed = _get_field(eval_record, "passed", False)
                if eval_passed:
                    return PolicyDecision.allow(
                        "promote_medium_risk_eval_passed",
                        "Medium risk promotion allowed with passing eval evidence",
                    )
                return PolicyDecision.deny(
                    "promote_medium_risk_eval_failed",
                    "Medium risk promotion requires passing eval evidence",
                )

        # Low risk
        if eval_record is not None:
            eval_passed = _get_field(eval_record, "passed", False)
            if not eval_passed:
                return PolicyDecision.deny(
                    "promote_low_risk_eval_failed",
                    "Low risk promotion requires passing evaluation",
                )
        return PolicyDecision.allow("promote_allowed", "Promotion is allowed")

    def validate_run(
        self,
        manifest: "CapabilityManifest",
        runtime_profile: Any | None = None,
        context: dict[str, Any] | None = None,
    ) -> PolicyDecision:
        """Validate whether a capability is allowed to run."""
        ctx = context if isinstance(context, dict) else {}

        status = manifest.status
        if status == CapabilityStatus.DISABLED:
            return PolicyDecision.deny("run_disabled", "Disabled capability cannot be run")
        if status == CapabilityStatus.ARCHIVED:
            return PolicyDecision.deny("run_archived", "Archived capability cannot be run")
        if status == CapabilityStatus.QUARANTINED:
            return PolicyDecision.deny("run_quarantined", "Quarantined capability cannot be run")

        return PolicyDecision.allow("run_allowed", "Capability is allowed to run")

    def validate_install(
        self,
        manifest: "CapabilityManifest",
        source: Any | None = None,
        context: dict[str, Any] | None = None,
    ) -> PolicyDecision:
        """Validate whether a capability can be installed from an external source."""
        ctx = context if isinstance(context, dict) else {}

        source_str = str(source) if source is not None else ""
        is_external = source_str not in ("", "local", "trusted")
        is_trusted = "trusted" in source_str.lower()

        if is_external and not is_trusted:
            return PolicyDecision.warn(
                "install_external_quarantine",
                "External install source defaults to quarantined unless explicitly trusted",
                source=source_str,
            )

        return PolicyDecision.allow("install_allowed", "Install is allowed")

    def validate_scope(
        self,
        manifest: "CapabilityManifest",
        context: dict[str, Any] | None = None,
    ) -> PolicyDecision:
        """Validate that the capability scope is known and valid."""
        scope_val = manifest.scope.value if hasattr(manifest.scope, "value") else str(manifest.scope)
        if scope_val not in ALLOWED_SCOPES:
            return PolicyDecision.deny(
                "unknown_scope",
                f"Unknown scope '{scope_val}'; allowed: {sorted(ALLOWED_SCOPES)}",
            )
        return PolicyDecision.allow("scope_valid", "Scope is valid")

    def validate_required_tools(
        self,
        manifest: "CapabilityManifest",
        available_tools: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> PolicyDecision:
        """Validate that required_tools are known when available_tools is provided."""
        required = manifest.required_tools
        if not required:
            return PolicyDecision.allow("no_required_tools", "No required tools specified")

        if available_tools is None:
            return PolicyDecision.allow(
                "tools_not_validated",
                "Required tools not validated — available_tools not provided",
            )

        available_set = set(available_tools)
        unknown = [t for t in required if t not in available_set]
        if unknown:
            return PolicyDecision.deny(
                "unknown_required_tools",
                f"Required tools not in available_tools: {unknown}",
                unknown_tools=unknown,
            )

        return PolicyDecision.allow("required_tools_valid", "All required tools are available")

    def validate_risk(
        self,
        manifest: "CapabilityManifest",
        context: dict[str, Any] | None = None,
    ) -> PolicyDecision:
        """Validate that risk_level is compatible with required_permissions."""
        risk = manifest.risk_level
        permissions = manifest.required_permissions

        if not permissions:
            return PolicyDecision.allow("risk_no_permissions", "No required permissions to check against risk")

        high_risk_perms = {"write", "execute", "network", "sudo", "admin"}
        risky_perms_in_low = [
            p for p in permissions
            if p.lower() in high_risk_perms and risk == CapabilityRiskLevel.LOW
        ]
        if risky_perms_in_low:
            return PolicyDecision.warn(
                "risk_permission_mismatch",
                f"Low risk capability requests sensitive permissions: {risky_perms_in_low}",
                mismatched_permissions=risky_perms_in_low,
            )

        return PolicyDecision.allow("risk_permissions_ok", "Risk level compatible with required permissions")

    # ── internal validators ─────────────────────────────────────────

    @staticmethod
    def _validate_type(manifest: "CapabilityManifest", ctx: dict[str, Any]) -> PolicyDecision:
        type_val = manifest.type.value if hasattr(manifest.type, "value") else str(manifest.type)
        if type_val not in ALLOWED_TYPES:
            return PolicyDecision.deny(
                "unknown_type",
                f"Unknown type '{type_val}'; allowed: {sorted(ALLOWED_TYPES)}",
            )
        return PolicyDecision.allow("type_valid", "Type is valid")

    @staticmethod
    def _validate_maturity(manifest: "CapabilityManifest", ctx: dict[str, Any]) -> PolicyDecision:
        mat_val = manifest.maturity.value if hasattr(manifest.maturity, "value") else str(manifest.maturity)
        if mat_val not in ALLOWED_MATURITIES:
            return PolicyDecision.deny(
                "invalid_maturity",
                f"Invalid maturity '{mat_val}'; allowed: {sorted(ALLOWED_MATURITIES)}",
            )
        return PolicyDecision.allow("maturity_valid", "Maturity is valid")

    @staticmethod
    def _validate_status(manifest: "CapabilityManifest", ctx: dict[str, Any]) -> PolicyDecision:
        status_val = manifest.status.value if hasattr(manifest.status, "value") else str(manifest.status)
        if status_val not in ALLOWED_STATUSES:
            return PolicyDecision.deny(
                "invalid_status",
                f"Invalid status '{status_val}'; allowed: {sorted(ALLOWED_STATUSES)}",
            )
        return PolicyDecision.allow("status_valid", "Status is valid")

    @staticmethod
    def _validate_risk_level(manifest: "CapabilityManifest", ctx: dict[str, Any]) -> PolicyDecision:
        risk_val = manifest.risk_level.value if hasattr(manifest.risk_level, "value") else str(manifest.risk_level)
        if risk_val not in ALLOWED_RISK_LEVELS:
            return PolicyDecision.deny(
                "invalid_risk_level",
                f"Invalid risk_level '{risk_val}'; allowed: {sorted(ALLOWED_RISK_LEVELS)}",
            )
        return PolicyDecision.allow("risk_level_valid", "Risk level is valid")
