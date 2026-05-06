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
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.eval.axes import AxisResult, AxisStatus, EvalAxis
from src.capabilities.schema import (
    CapabilityMaturity,
    CapabilityRiskLevel,
    CapabilityScope,
    CapabilityStatus,
    CapabilityType,
    SideEffect,
)

if TYPE_CHECKING:
    from src.capabilities.document import CapabilityDocument
    from src.capabilities.schema import CapabilityManifest

EVALUATOR_VERSION = "4c.0"
EVAL_SCHEMA_VERSION = "eval_record.v2"


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
    schema_version: str = EVAL_SCHEMA_VERSION
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    passed: bool = True
    score: float = 1.0
    findings: list[EvalFinding] = field(default_factory=list)
    required_approval: bool = False
    recommended_maturity: str | None = None
    axes: dict[str, AxisResult] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.axes:
            return
        functional = AxisStatus.PASS if self.passed else AxisStatus.FAIL
        self.axes = {
            EvalAxis.FUNCTIONAL.value: AxisResult(EvalAxis.FUNCTIONAL, functional),
            EvalAxis.SAFETY.value: AxisResult(EvalAxis.SAFETY, AxisStatus.UNKNOWN),
            EvalAxis.PRIVACY.value: AxisResult(EvalAxis.PRIVACY, AxisStatus.UNKNOWN),
            EvalAxis.REVERSIBILITY.value: AxisResult(EvalAxis.REVERSIBILITY, AxisStatus.UNKNOWN),
        }


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
        findings.extend(self._check_boundary_declarations(doc))
        findings.extend(self._check_eval_fixtures(doc))
        findings.extend(self._check_declared_side_effects(doc))
        findings.extend(self._check_script_scans(doc))
        findings.extend(self._check_composition_risk(doc))

        record.findings = findings
        record.passed = not any(f.severity == FindingSeverity.ERROR for f in findings)
        record.score = self._compute_score(findings)
        record.required_approval = self._compute_required_approval(manifest, findings)
        record.recommended_maturity = self._compute_recommended_maturity(manifest, findings)
        record.axes = _build_axis_results(manifest, findings, record.score)

        return record

    # ── Boundary declaration checks ─────────────────────────────────

    def _check_boundary_declarations(self, doc: "CapabilityDocument") -> list[EvalFinding]:
        findings: list[EvalFinding] = []
        manifest = doc.manifest
        risk = manifest.risk_level
        missing: list[str] = []
        if not manifest.do_not_apply_when:
            missing.append("do_not_apply_when")
        if not manifest.reuse_boundary:
            missing.append("reuse_boundary")
        if not missing:
            return findings
        severity = (
            FindingSeverity.ERROR
            if risk in (CapabilityRiskLevel.MEDIUM, CapabilityRiskLevel.HIGH)
            else FindingSeverity.WARNING
        )
        findings.append(EvalFinding(
            severity=severity,
            code="missing_boundary",
            message=f"Capability is missing boundary declarations: {', '.join(missing)}",
            location="manifest",
            details={"missing": missing, "risk_level": risk.value},
        ))
        return findings

    def _check_eval_fixtures(self, doc: "CapabilityDocument") -> list[EvalFinding]:
        findings: list[EvalFinding] = []
        manifest = doc.manifest
        evals_dir = doc.directory / "evals"

        def has_fixture(name: str) -> bool:
            return (evals_dir / name).is_file()

        risk = manifest.risk_level
        maturity = manifest.maturity
        entry_type = str(manifest.extra.get("entry_type", "")).strip()
        is_executable = entry_type in {"executable", "hybrid", "executable_script", "skill_bridge"}

        if maturity in (CapabilityMaturity.TESTING, CapabilityMaturity.STABLE) and not has_fixture("positive_cases.jsonl"):
            findings.append(EvalFinding(
                severity=FindingSeverity.WARNING,
                code="missing_positive_cases",
                message="Missing evals/positive_cases.jsonl fixture for testing/stable capability",
                location="evals/positive_cases.jsonl",
            ))

        if risk in (CapabilityRiskLevel.MEDIUM, CapabilityRiskLevel.HIGH) and is_executable and not has_fixture("negative_cases.jsonl"):
            findings.append(EvalFinding(
                severity=FindingSeverity.ERROR,
                code="missing_negative_cases",
                message="Medium/high executable or hybrid capability requires evals/negative_cases.jsonl",
                location="evals/negative_cases.jsonl",
            ))

        if not has_fixture("boundary_cases.jsonl"):
            severity = (
                FindingSeverity.ERROR
                if risk in (CapabilityRiskLevel.MEDIUM, CapabilityRiskLevel.HIGH)
                else FindingSeverity.WARNING
            )
            findings.append(EvalFinding(
                severity=severity,
                code="missing_boundary_cases",
                message="Missing evals/boundary_cases.jsonl fixture",
                location="evals/boundary_cases.jsonl",
                details={"risk_level": risk.value},
            ))

        destructive = _destructive_or_external_side_effects(_side_effect_values(manifest.side_effects))
        if destructive and not has_fixture("rollback_cases.jsonl"):
            findings.append(EvalFinding(
                severity=FindingSeverity.ERROR,
                code="missing_rollback_cases",
                message=(
                    "Missing evals/rollback_cases.jsonl fixture required by side effects: "
                    + ", ".join(sorted(destructive))
                ),
                location="evals/rollback_cases.jsonl",
                details={"side_effects": sorted(destructive)},
            ))

        return findings

    def _check_declared_side_effects(self, doc: "CapabilityDocument") -> list[EvalFinding]:
        findings: list[EvalFinding] = []
        manifest = doc.manifest
        declared = _side_effect_values(manifest.side_effects)
        declared_set = set(declared)
        risk = manifest.risk_level

        if not declared:
            findings.append(EvalFinding(
                severity=(
                    FindingSeverity.ERROR
                    if risk in (CapabilityRiskLevel.MEDIUM, CapabilityRiskLevel.HIGH)
                    else FindingSeverity.WARNING
                ),
                code="unknown_side_effects",
                message="side_effects=[] means side effects are unspecified/unknown",
                location="manifest.side_effects",
                details={"risk_level": risk.value},
            ))

        if SideEffect.NONE.value in declared_set and len(declared_set) > 1:
            findings.append(EvalFinding(
                severity=FindingSeverity.ERROR,
                code="invalid_side_effect_none_combination",
                message='side_effects cannot combine "none" with other side effects',
                location="manifest.side_effects",
            ))

        if manifest.rollback_available is True and manifest.rollback_mechanism is None:
            findings.append(EvalFinding(
                severity=FindingSeverity.ERROR,
                code="rollback_mechanism_required",
                message="rollback_available=True requires rollback_mechanism",
                location="manifest.rollback_mechanism",
            ))

        destructive = _destructive_or_external_side_effects(declared_set)
        if manifest.rollback_available is False and destructive:
            findings.append(EvalFinding(
                severity=FindingSeverity.WARNING,
                code="irreversible_side_effects",
                message="Destructive/external side effects declare rollback_available=False",
                location="manifest.rollback_available",
                details={"side_effects": sorted(destructive)},
            ))

        detected = self._detect_side_effects(doc)
        if detected:
            if SideEffect.NONE.value in declared_set:
                undeclared = detected
            elif declared_set:
                undeclared = detected - declared_set
            else:
                undeclared = detected
            if undeclared:
                findings.append(EvalFinding(
                    severity=FindingSeverity.ERROR,
                    code="undeclared_detected_side_effects",
                    message="Detected side effects are not declared: " + ", ".join(sorted(undeclared)),
                    location="CAPABILITY.md body",
                    details={"detected_side_effects": sorted(detected), "undeclared": sorted(undeclared)},
                ))

        return findings

    def _check_composition_risk(self, doc: "CapabilityDocument") -> list[EvalFinding]:
        manifest = doc.manifest
        declared = set(_side_effect_values(manifest.side_effects))
        detected = self._detect_side_effects(doc)
        effects = declared | detected
        inferred = _infer_composition_risk(doc, effects)
        if inferred is None:
            return []
        if _risk_rank(manifest.risk_level.value) >= _risk_rank(inferred):
            return []
        return [EvalFinding(
            severity=FindingSeverity.ERROR,
            code="declared_risk_below_inferred_composition_risk",
            message=(
                f"Declared risk_level={manifest.risk_level.value} is lower than "
                f"inferred composition risk={inferred}"
            ),
            location="manifest.risk_level",
            details={"inferred_risk": inferred, "side_effects": sorted(effects)},
        )]

    def _detect_side_effects(self, doc: "CapabilityDocument") -> set[str]:
        manifest = doc.manifest
        searchable = [doc.body, " ".join(manifest.required_tools), " ".join(manifest.required_permissions)]
        searchable.extend(str(v) for v in manifest.extra.values() if isinstance(v, str))
        text = "\n".join(searchable).lower()
        effects = _detect_text_side_effects(text)
        for _path, script_text in _script_contents(doc):
            effects |= _detect_text_side_effects(script_text.lower())
        return effects

    def _check_script_scans(self, doc: "CapabilityDocument") -> list[EvalFinding]:
        findings: list[EvalFinding] = []
        manifest = doc.manifest
        entry_type, entry_script = _entry_metadata(manifest)
        scripts_dir = doc.directory / "scripts"

        if entry_type == "executable_script":
            if not entry_script:
                findings.append(EvalFinding(
                    severity=FindingSeverity.ERROR,
                    code="missing_entry_script",
                    message="executable_script capability requires entry_script",
                    location="manifest.entry_script",
                ))
            else:
                entry_path = Path(entry_script)
                if entry_path.is_absolute():
                    findings.append(EvalFinding(
                        severity=FindingSeverity.ERROR,
                        code="absolute_entry_script",
                        message="entry_script must be relative",
                        location="manifest.entry_script",
                    ))
                else:
                    resolved = (doc.directory / entry_script).resolve()
                    try:
                        resolved.relative_to(doc.directory)
                    except ValueError:
                        findings.append(EvalFinding(
                            severity=FindingSeverity.ERROR,
                            code="entry_script_path_traversal",
                            message="entry_script must stay inside the capability directory",
                            location="manifest.entry_script",
                        ))
                    if not resolved.is_file():
                        findings.append(EvalFinding(
                            severity=FindingSeverity.ERROR,
                            code="entry_script_missing",
                            message="entry_script file does not exist",
                            location="manifest.entry_script",
                        ))

        if scripts_dir.is_dir() and entry_type == "executable_script" and not entry_script:
            findings.append(EvalFinding(
                severity=FindingSeverity.ERROR,
                code="scripts_without_entrypoint",
                message="scripts/ exists but executable entrypoint is missing",
                location="scripts/",
            ))

        declared = set(_side_effect_values(manifest.side_effects))
        detected: set[str] = set()
        for path, text in _script_contents(doc):
            lowered = text.lower()
            for pattern, description in _DANGEROUS_SHELL_PATTERNS:
                if re.search(pattern, lowered, re.IGNORECASE):
                    findings.append(EvalFinding(
                        severity=FindingSeverity.ERROR,
                        code="script_destructive_pattern",
                        message=f"Destructive script pattern detected: {description}",
                        location=str(path.relative_to(doc.directory)),
                        details={"pattern": pattern},
                    ))
            if re.search(r"\b(api_key|secret|password|token)\s*=\s*['\"][^'\"]+['\"]", lowered):
                findings.append(EvalFinding(
                    severity=FindingSeverity.ERROR,
                    code="script_secret_literal",
                    message="Secret-like literal detected in script",
                    location=str(path.relative_to(doc.directory)),
                ))
            if re.search(r"\b(requests\.post|httpx\.post|curl|webhook|send_message)\b", lowered):
                findings.append(EvalFinding(
                    severity=FindingSeverity.WARNING,
                    code="script_external_send_pattern",
                    message="External send/public output pattern detected in script",
                    location=str(path.relative_to(doc.directory)),
                ))
            detected |= _detect_text_side_effects(lowered)

        if detected:
            if SideEffect.NONE.value in declared:
                undeclared = detected
            elif declared:
                undeclared = detected - declared
            else:
                undeclared = detected
            if undeclared:
                findings.append(EvalFinding(
                    severity=FindingSeverity.ERROR,
                    code="script_undeclared_side_effects",
                    message="Detected script side effects are not declared: " + ", ".join(sorted(undeclared)),
                    location="scripts/",
                    details={"detected_side_effects": sorted(detected), "undeclared": sorted(undeclared)},
                ))

        return findings

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


def _side_effect_values(values) -> list[str]:
    return [v.value if hasattr(v, "value") else str(v) for v in (values or [])]


def _entry_metadata(manifest: "CapabilityManifest") -> tuple[str, str]:
    execution = manifest.extra.get("execution")
    if not isinstance(execution, dict):
        execution = {}
    entry_type = (
        execution.get("entry_type")
        or manifest.extra.get("entry_type")
        or manifest.extra.get("entrypoint_type")
        or ""
    )
    entry_script = execution.get("entry_script") or manifest.extra.get("entry_script") or ""
    return str(entry_type), str(entry_script)


def _script_contents(doc: "CapabilityDocument") -> list[tuple[Path, str]]:
    scripts_dir = doc.directory / "scripts"
    if not scripts_dir.is_dir():
        return []
    result: list[tuple[Path, str]] = []
    for pattern in ("**/*.py", "**/*.sh"):
        for path in sorted(scripts_dir.glob(pattern)):
            if not path.is_file():
                continue
            try:
                result.append((path, path.read_text(encoding="utf-8")))
            except OSError:
                continue
    return result


def _detect_text_side_effects(text: str) -> set[str]:
    effects: set[str] = set()
    if re.search(r"\b(rm\s+-rf|unlink|delete|remove_file)\b", text):
        effects.add(SideEffect.LOCAL_DELETE.value)
    if re.search(r"\b(write_file|file_write|file_append|open\s*\(.+['\"]w['\"])\b", text):
        effects.add(SideEffect.LOCAL_WRITE.value)
    if re.search(r"\b(execute_shell|subprocess|os\.system|shell_exec|bash|sh)\b", text):
        effects.add(SideEffect.SHELL_EXEC.value)
    if re.search(r"\b(requests\.post|httpx\.post|curl|webhook|send_message|network_send)\b", text):
        effects.add(SideEffect.NETWORK_SEND.value)
    if re.search(r"\b(public_output|publish|tweet|post publicly|external_publication)\b", text):
        effects.add(SideEffect.PUBLIC_OUTPUT.value)
    if re.search(r"\b(api update|external_mutation|mutate external|inverse_api_call)\b", text):
        effects.add(SideEffect.EXTERNAL_MUTATION.value)
    if re.search(r"\b(api_key|secret|credential|token|password|\.env)\b", text):
        effects.add(SideEffect.CREDENTIAL_ACCESS.value)
    return effects


def _destructive_or_external_side_effects(values) -> set[str]:
    return set(values) & {
        SideEffect.LOCAL_WRITE.value,
        SideEffect.LOCAL_DELETE.value,
        SideEffect.NETWORK_SEND.value,
        SideEffect.PUBLIC_OUTPUT.value,
        SideEffect.EXTERNAL_MUTATION.value,
        SideEffect.SHELL_EXEC.value,
    }


def _risk_rank(value: str) -> int:
    order = {
        CapabilityRiskLevel.LOW.value: 0,
        CapabilityRiskLevel.MEDIUM.value: 1,
        CapabilityRiskLevel.HIGH.value: 2,
    }
    return order.get(value, 0)


def _infer_composition_risk(doc: "CapabilityDocument", effects: set[str]) -> str | None:
    manifest = doc.manifest
    tools = {str(t).lower() for t in manifest.required_tools}
    permissions = {str(p).lower() for p in manifest.required_permissions}
    tags = {str(t).lower() for t in manifest.tags}
    sensitive = {v.value if hasattr(v, "value") else str(v) for v in manifest.sensitive_contexts}
    text = " ".join([doc.body, " ".join(tools), " ".join(permissions), " ".join(tags)]).lower()

    high_rules = [
        SideEffect.LOCAL_DELETE.value in effects and SideEffect.NETWORK_SEND.value in effects,
        SideEffect.CREDENTIAL_ACCESS.value in effects and SideEffect.NETWORK_SEND.value in effects,
        bool(sensitive) and SideEffect.PUBLIC_OUTPUT.value in effects,
        SideEffect.SHELL_EXEC.value in effects and SideEffect.EXTERNAL_MUTATION.value in effects,
        "memory_read" in tools | permissions and "capability_create" in tools | permissions,
    ]
    if any(high_rules):
        return CapabilityRiskLevel.HIGH.value

    if (
        SideEffect.LOCAL_WRITE.value in effects
        and "auto_run" in tags
        and manifest.rollback_available is not True
    ):
        if manifest.scope.value in {CapabilityScope.WORKSPACE.value, CapabilityScope.GLOBAL.value}:
            return CapabilityRiskLevel.HIGH.value
        return CapabilityRiskLevel.MEDIUM.value

    if "private_data_read" in text and SideEffect.PUBLIC_OUTPUT.value in effects:
        return CapabilityRiskLevel.HIGH.value
    return None


_FUNCTIONAL_CODES = frozenset({
    "missing_section_when_to_use",
    "missing_section_procedure",
    "missing_section_verification",
    "missing_section_failure_handling",
    "empty_description",
    "invalid_required_tools_format",
    "invalid_tool_entry",
    "invalid_required_permissions_format",
    "invalid_permission_entry",
    "missing_positive_cases",
})

_SAFETY_CODES = frozenset({
    "missing_boundary",
    "missing_boundary_cases",
    "missing_negative_cases",
    "dangerous_shell_pattern",
    "prompt_injection_like",
    "declared_risk_below_inferred_composition_risk",
    "script_destructive_pattern",
    "script_external_send_pattern",
    "absolute_entry_script",
    "entry_script_path_traversal",
    "entry_script_missing",
    "missing_entry_script",
    "scripts_without_entrypoint",
})

_PRIVACY_CODES = frozenset({
    "undeclared_detected_side_effects",
    "declared_risk_below_inferred_composition_risk",
    "script_secret_literal",
    "script_undeclared_side_effects",
})

_REVERSIBILITY_CODES = frozenset({
    "unknown_side_effects",
    "invalid_side_effect_none_combination",
    "rollback_mechanism_required",
    "irreversible_side_effects",
    "missing_rollback_cases",
    "undeclared_detected_side_effects",
    "script_undeclared_side_effects",
})


def _build_axis_results(
    manifest: "CapabilityManifest",
    findings: list[EvalFinding],
    score: float,
) -> dict[str, AxisResult]:
    return {
        EvalAxis.FUNCTIONAL.value: _axis_result(EvalAxis.FUNCTIONAL, findings, _FUNCTIONAL_CODES, score),
        EvalAxis.SAFETY.value: _axis_result(EvalAxis.SAFETY, findings, _SAFETY_CODES, score),
        EvalAxis.PRIVACY.value: _privacy_axis_result(manifest, findings, score),
        EvalAxis.REVERSIBILITY.value: _axis_result(EvalAxis.REVERSIBILITY, findings, _REVERSIBILITY_CODES, score),
    }


def _privacy_axis_result(
    manifest: "CapabilityManifest",
    findings: list[EvalFinding],
    score: float,
) -> AxisResult:
    relevant = [
        f for f in findings
        if f.code in _PRIVACY_CODES or "credential" in f.code or "privacy" in f.code
    ]
    if not manifest.sensitive_contexts:
        status = AxisStatus.UNKNOWN
        if relevant:
            status = _status_from_findings(relevant)
        return AxisResult(
            EvalAxis.PRIVACY,
            status,
            score=score if relevant else None,
            findings=tuple(f.code for f in relevant),
        )
    return AxisResult(
        EvalAxis.PRIVACY,
        _status_from_findings(relevant),
        score=score,
        findings=tuple(f.code for f in relevant),
    )


def _axis_result(
    axis: EvalAxis,
    findings: list[EvalFinding],
    codes: frozenset[str],
    score: float,
) -> AxisResult:
    relevant = [f for f in findings if f.code in codes]
    return AxisResult(
        axis,
        _status_from_findings(relevant),
        score=score,
        findings=tuple(f.code for f in relevant),
    )


def _status_from_findings(findings: list[EvalFinding]) -> AxisStatus:
    if any(f.severity == FindingSeverity.ERROR for f in findings):
        return AxisStatus.FAIL
    if any(f.severity == FindingSeverity.WARNING for f in findings):
        return AxisStatus.WARNING
    return AxisStatus.PASS
