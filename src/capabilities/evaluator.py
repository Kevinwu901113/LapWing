"""Deterministic capability evaluator / safety lint.

Inspects CapabilityDocument and returns an EvalRecord with findings.
Does not mutate state, call LLMs, execute scripts, or register tools.

Phase 3A: evaluation foundation only — not wired into promotion or runtime.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

from src.capabilities.schema import (
    CapabilityMaturity,
    CapabilityRiskLevel,
    CapabilityScope,
    CapabilityStatus,
    CapabilityType,
)

if TYPE_CHECKING:
    from src.capabilities.document import CapabilityDocument
    from src.capabilities.schema import CapabilityManifest

EVALUATOR_VERSION = "3a.1"


# ── Finding model ───────────────────────────────────────────────────────


class FindingSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class EvalFinding:
    severity: FindingSeverity
    code: str
    message: str
    location: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


# ── Record model ────────────────────────────────────────────────────────


@dataclass
class EvalRecord:
    capability_id: str
    scope: str
    content_hash: str
    evaluator_version: str = EVALUATOR_VERSION
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    passed: bool = True
    score: float = 1.0
    findings: list[EvalFinding] = field(default_factory=list)
    required_approval: bool = False
    recommended_maturity: str | None = None


# ── Dangerous pattern definitions ───────────────────────────────────────

_DANGEROUS_SHELL_PATTERNS: list[tuple[str, str]] = [
    (r"rm\s+-rf\s+/", "rm -rf / (recursive root removal)"),
    (r"sudo\s+rm", "sudo rm (privileged file removal)"),
    (r"chmod\s+777", "chmod 777 (world-writable permissions)"),
    (r"curl\b.*\|.*(?:ba)?sh", "curl piped to shell"),
    (r"wget\b.*\|.*(?:ba)?sh", "wget piped to shell"),
    (r"dd\s+if=", "dd if= (raw device access)"),
    (r"\bmkfs\b", "mkfs (filesystem creation)"),
    (r":\(\)\s*\{\s*:\|\:&\s*\};:", "fork bomb"),
    (r"~\s*/\.ssh", "modifying ~/.ssh"),
    (r"/etc/(?:passwd|shadow|sudoers)", "modifying system auth files"),
    (r"/dev/(?:null|zero|random)\b.*>", "writing to /dev devices"),
    (r">\s*(?:/etc|/var|/usr|/boot|/sys)", "writing outside workspace boundaries"),
    (r"eval\s+\$", "eval with variable expansion"),
    (r"\.\./\.\./(?:/etc|/var|/usr)", "path traversal to system dirs"),
]

_PROMPT_INJECTION_PATTERNS: list[tuple[str, str]] = [
    (r"ignore\s+(?:all\s+)?(?:previous|prior|above|the\s+above)\s+instructions?", "prompt injection: 'ignore instructions'"),
    (r"you\s+are\s+(?:now|acting\s+as)", "prompt injection: 'you are now'"),
    (r"pretend\s+(?:you\s+are|to\s+be)", "prompt injection: 'pretend you are'"),
    (r"(?:system|developer)\s+prompt", "prompt injection: system prompt reference"),
    (r"override\s+(?:the\s+)?(?:system|safety|security)", "prompt injection: 'override safety'"),
    (r"bypass\s+(?:the\s+)?(?:filter|restriction|policy)", "prompt injection: 'bypass filter'"),
    (r"act\s+as\s+(?:an?\s+)?(?:unrestricted|unfiltered|uncensored)", "prompt injection: 'act as unrestricted'"),
    (r"do\s+not\s+(?:follow|obey)\s+(?:your\s+)?instructions", "prompt injection: 'do not follow instructions'"),
]

_OVERBROAD_TRIGGER_PATTERNS: list[str] = [
    r"^\*$",
    r"^\.\*$",
    r"^always$",
    r"^on_message$",
    r"^on_any$",
    r"^every$",
    r"^all$",
]


# ── Required CAPABILITY.md sections ────────────────────────────────────

_REQUIRED_SECTIONS = {
    "when to use": "When to use",
    "procedure": "Procedure",
    "verification": "Verification",
    "failure handling": "Failure handling",
}


# ── Evaluator ──────────────────────────────────────────────────────────


class CapabilityEvaluator:
    """Deterministic safety and quality lint for capability documents.

    Usage:
        evaluator = CapabilityEvaluator()
        record = evaluator.evaluate(doc)
    """

    def evaluate(
        self,
        doc: "CapabilityDocument",
        *,
        available_tools: list[str] | None = None,
    ) -> EvalRecord:
        """Run all evaluation checks and return an EvalRecord."""
        manifest = doc.manifest
        record = EvalRecord(
            capability_id=manifest.id,
            scope=manifest.scope.value,
            content_hash=doc.content_hash,
        )
        findings: list[EvalFinding] = []

        # Structural checks
        findings.extend(self._check_required_sections(doc))
        findings.extend(self._check_description_quality(manifest))
        findings.extend(self._check_triggers(doc))
        findings.extend(self._check_required_tools_format(manifest))
        findings.extend(self._check_required_permissions_format(manifest))
        findings.extend(self._check_risk_permission_consistency(manifest))

        # Content safety checks
        findings.extend(self._check_dangerous_patterns(doc))
        findings.extend(self._check_prompt_injection(doc))
        findings.extend(self._check_path_references(doc))

        # Promotion eligibility checks
        findings.extend(self._check_stable_no_eval(manifest))
        findings.extend(self._check_high_risk_no_approval(manifest))
        findings.extend(self._check_quarantined_restrictions(manifest))

        record.findings = findings
        record.passed = not any(f.severity == FindingSeverity.ERROR for f in findings)
        record.score = self._compute_score(findings)
        record.required_approval = self._compute_required_approval(manifest, findings)
        record.recommended_maturity = self._compute_recommended_maturity(manifest, findings)

        return record

    # ── Section checks ──────────────────────────────────────────────

    def _check_required_sections(self, doc: "CapabilityDocument") -> list[EvalFinding]:
        findings: list[EvalFinding] = []
        body_lower = doc.body.lower()

        for key, label in _REQUIRED_SECTIONS.items():
            # Look for markdown headings containing the section name
            if not re.search(rf"^#+\s+.*{re.escape(key)}", body_lower, re.MULTILINE):
                code = f"missing_section_{key.replace(' ', '_')}"
                msg = f"Missing required section: '{label}'"
                findings.append(EvalFinding(
                    severity=FindingSeverity.ERROR,
                    code=code,
                    message=msg,
                    location="CAPABILITY.md body",
                ))

        return findings

    # ── Description quality ─────────────────────────────────────────

    def _check_description_quality(self, manifest: "CapabilityManifest") -> list[EvalFinding]:
        findings: list[EvalFinding] = []
        desc = manifest.description.strip()

        if not desc:
            findings.append(EvalFinding(
                severity=FindingSeverity.ERROR,
                code="empty_description",
                message="Description is empty",
                location="manifest.description",
            ))
            return findings

        vague_phrases = ["todo", "tbd", "wip", "work in progress", "something", "stuff"]
        desc_lower = desc.lower()
        for phrase in vague_phrases:
            if phrase in desc_lower:
                findings.append(EvalFinding(
                    severity=FindingSeverity.WARNING,
                    code="vague_description",
                    message=f"Description contains vague placeholder: '{phrase}'",
                    location="manifest.description",
                    details={"phrase": phrase},
                ))
                break

        if len(desc) < 20:
            findings.append(EvalFinding(
                severity=FindingSeverity.WARNING,
                code="short_description",
                message=f"Description is very short ({len(desc)} chars); consider more detail",
                location="manifest.description",
            ))

        return findings

    # ── Trigger checks ──────────────────────────────────────────────

    def _check_triggers(self, doc: "CapabilityDocument") -> list[EvalFinding]:
        findings: list[EvalFinding] = []
        manifest = doc.manifest
        triggers = manifest.triggers

        needs_triggers = manifest.type in (CapabilityType.SKILL, CapabilityType.WORKFLOW)
        if needs_triggers and not triggers:
            findings.append(EvalFinding(
                severity=FindingSeverity.WARNING,
                code="missing_triggers",
                message=f"Reusable {manifest.type.value} has no triggers defined",
                location="manifest.triggers",
            ))

        for trigger in triggers:
            for pattern in _OVERBROAD_TRIGGER_PATTERNS:
                if re.match(pattern, trigger, re.IGNORECASE):
                    findings.append(EvalFinding(
                        severity=FindingSeverity.WARNING,
                        code="overbroad_trigger",
                        message=f"Trigger '{trigger}' is overbroad; consider a more specific trigger",
                        location="manifest.triggers",
                        details={"trigger": trigger},
                    ))
                    break

        return findings

    # ── Format checks ───────────────────────────────────────────────

    @staticmethod
    def _check_required_tools_format(manifest: "CapabilityManifest") -> list[EvalFinding]:
        findings: list[EvalFinding] = []
        tools = manifest.required_tools
        if not isinstance(tools, list):
            return [EvalFinding(
                severity=FindingSeverity.ERROR,
                code="invalid_required_tools_format",
                message="required_tools must be a list of strings",
                location="manifest.required_tools",
            )]
        for i, tool in enumerate(tools):
            if not isinstance(tool, str) or not tool.strip():
                findings.append(EvalFinding(
                    severity=FindingSeverity.ERROR,
                    code="invalid_tool_entry",
                    message=f"required_tools[{i}] is not a valid string: {tool!r}",
                    location="manifest.required_tools",
                    details={"index": i, "value": str(tool)},
                ))
        return findings

    @staticmethod
    def _check_required_permissions_format(manifest: "CapabilityManifest") -> list[EvalFinding]:
        findings: list[EvalFinding] = []
        perms = manifest.required_permissions
        if not isinstance(perms, list):
            return [EvalFinding(
                severity=FindingSeverity.ERROR,
                code="invalid_required_permissions_format",
                message="required_permissions must be a list of strings",
                location="manifest.required_permissions",
            )]
        for i, perm in enumerate(perms):
            if not isinstance(perm, str) or not perm.strip():
                findings.append(EvalFinding(
                    severity=FindingSeverity.ERROR,
                    code="invalid_permission_entry",
                    message=f"required_permissions[{i}] is not a valid string: {perm!r}",
                    location="manifest.required_permissions",
                    details={"index": i, "value": str(perm)},
                ))
        return findings

    # ── Risk/permission consistency ─────────────────────────────────

    @staticmethod
    def _check_risk_permission_consistency(manifest: "CapabilityManifest") -> list[EvalFinding]:
        findings: list[EvalFinding] = []
        risk = manifest.risk_level
        perms = {p.lower() for p in manifest.required_permissions}

        high_risk_perms = {"write", "execute", "network", "sudo", "admin"}
        risky_found = perms & high_risk_perms

        if risky_found and risk == CapabilityRiskLevel.LOW:
            findings.append(EvalFinding(
                severity=FindingSeverity.WARNING,
                code="low_risk_sensitive_permissions",
                message=f"Low risk capability requests sensitive permissions: {sorted(risky_found)}",
                location="manifest.required_permissions",
                details={"mismatched": sorted(risky_found)},
            ))

        return findings

    # ── Dangerous pattern detection ─────────────────────────────────

    def _check_dangerous_patterns(self, doc: "CapabilityDocument") -> list[EvalFinding]:
        """Scan body and known script references for dangerous shell patterns."""
        findings: list[EvalFinding] = []
        body = doc.body
        manifest = doc.manifest

        # Also collect content from trigger descriptions and extra fields
        searchable = [body]
        for trigger in manifest.triggers:
            searchable.append(trigger)
        extra_text = " ".join(str(v) for v in manifest.extra.values() if isinstance(v, str))
        if extra_text:
            searchable.append(extra_text)

        combined = "\n".join(searchable)

        for pattern, description in _DANGEROUS_SHELL_PATTERNS:
            if re.search(pattern, combined, re.IGNORECASE):
                findings.append(EvalFinding(
                    severity=FindingSeverity.ERROR,
                    code="dangerous_shell_pattern",
                    message=f"Dangerous shell pattern detected: {description}",
                    location="CAPABILITY.md body",
                    details={"pattern": pattern, "description": description},
                ))

        return findings

    # ── Prompt injection detection ──────────────────────────────────

    def _check_prompt_injection(self, doc: "CapabilityDocument") -> list[EvalFinding]:
        """Scan body for prompt-injection-like phrases."""
        findings: list[EvalFinding] = []
        body_lower = doc.body.lower()

        for pattern, description in _PROMPT_INJECTION_PATTERNS:
            if re.search(pattern, body_lower):
                findings.append(EvalFinding(
                    severity=FindingSeverity.WARNING,
                    code="prompt_injection_like",
                    message=f"Potential prompt injection: {description}",
                    location="CAPABILITY.md body",
                    details={"pattern": pattern},
                ))

        return findings

    # ── Path reference checks ───────────────────────────────────────

    def _check_path_references(self, doc: "CapabilityDocument") -> list[EvalFinding]:
        """Check that path references are within allowed standard dirs."""
        findings: list[EvalFinding] = []
        body = doc.body

        # Find markdown code blocks or inline code that reference paths
        path_refs = re.findall(r"`([^`]*(?:scripts|tests|examples)/[^`]*)`", body, re.IGNORECASE)
        allowed_prefixes = ("scripts/", "tests/", "examples/")

        for ref in path_refs:
            if not any(ref.lower().startswith(p) for p in allowed_prefixes):
                findings.append(EvalFinding(
                    severity=FindingSeverity.WARNING,
                    code="path_outside_standard_dirs",
                    message=f"Path reference outside standard dirs: '{ref}'",
                    location="CAPABILITY.md body",
                    details={"reference": ref},
                ))

        # Detect absolute paths outside the capability directory
        abs_paths = re.findall(r"(?:/[a-zA-Z0-9_\-./]+)", body)
        for path in abs_paths:
            if path.startswith("/home/") or path.startswith("/etc/") or path.startswith("/var/"):
                findings.append(EvalFinding(
                    severity=FindingSeverity.WARNING,
                    code="absolute_path_reference",
                    message=f"Absolute path reference detected: '{path}'",
                    location="CAPABILITY.md body",
                    details={"path": path},
                ))

        return findings

    # ── Promotion eligibility ───────────────────────────────────────

    @staticmethod
    def _check_stable_no_eval(manifest: "CapabilityManifest") -> list[EvalFinding]:
        findings: list[EvalFinding] = []
        if manifest.maturity == CapabilityMaturity.STABLE:
            findings.append(EvalFinding(
                severity=FindingSeverity.INFO,
                code="stable_without_eval_evidence",
                message="Stable maturity without eval evidence may require re-evaluation for promotion",
                location="manifest.maturity",
            ))
        return findings

    @staticmethod
    def _check_high_risk_no_approval(manifest: "CapabilityManifest") -> list[EvalFinding]:
        findings: list[EvalFinding] = []
        if manifest.risk_level == CapabilityRiskLevel.HIGH:
            findings.append(EvalFinding(
                severity=FindingSeverity.INFO,
                code="high_risk_requires_approval",
                message="High risk capability requires owner approval for promotion to stable",
                location="manifest.risk_level",
            ))
        return findings

    @staticmethod
    def _check_quarantined_restrictions(manifest: "CapabilityManifest") -> list[EvalFinding]:
        findings: list[EvalFinding] = []
        if manifest.status == CapabilityStatus.QUARANTINED:
            findings.append(EvalFinding(
                severity=FindingSeverity.WARNING,
                code="quarantined_restricted",
                message="Quarantined capability cannot be promoted or run",
                location="manifest.status",
            ))
        return findings

    # ── Scoring ─────────────────────────────────────────────────────

    @staticmethod
    def _compute_score(findings: list[EvalFinding]) -> float:
        score = 1.0
        for f in findings:
            if f.severity == FindingSeverity.ERROR:
                score -= 0.3
            elif f.severity == FindingSeverity.WARNING:
                score -= 0.1
        return max(0.0, round(score, 2))

    # ── Approval requirement ────────────────────────────────────────

    @staticmethod
    def _compute_required_approval(manifest: "CapabilityManifest", findings: list[EvalFinding]) -> bool:
        if manifest.risk_level == CapabilityRiskLevel.HIGH:
            return True
        if manifest.risk_level == CapabilityRiskLevel.MEDIUM:
            return any(f.code == "dangerous_shell_pattern" for f in findings)
        return False

    # ── Maturity recommendation ─────────────────────────────────────

    @staticmethod
    def _compute_recommended_maturity(
        manifest: "CapabilityManifest",
        findings: list[EvalFinding],
    ) -> str | None:
        has_errors = any(f.severity == FindingSeverity.ERROR for f in findings)
        has_warnings = any(f.severity == FindingSeverity.WARNING for f in findings)

        if has_errors:
            return CapabilityMaturity.DRAFT.value
        if has_warnings:
            return CapabilityMaturity.TESTING.value
        return CapabilityMaturity.STABLE.value
