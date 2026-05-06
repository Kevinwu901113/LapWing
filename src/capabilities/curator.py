"""Deterministic ExperienceCurator — converts trace summaries into curated experiences
and draft capability proposals via heuristic analysis.

No LLM, no network, no shell, no file reads.  Same input → same output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.capabilities.proposal import CapabilityProposal

from src.capabilities.trace_summary import TraceSummary


# ── Data models ────────────────────────────────────────────────────────────


@dataclass
class CuratorDecision:
    """Deterministic reflection decision on whether a trace is worth proposal-izing."""

    should_create: bool
    recommended_action: str = "no_action"
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    risk_level: str = "low"
    required_approval: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "should_create": self.should_create,
            "recommended_action": self.recommended_action,
            "confidence": self.confidence,
            "reasons": self.reasons,
            "risk_level": self.risk_level,
            "required_approval": self.required_approval,
        }


@dataclass
class CuratedExperience:
    """Structured extraction from a TraceSummary, ready for proposal generation."""

    problem: str = ""
    context: str = ""
    successful_steps: list[str] = field(default_factory=list)
    failed_attempts: list[str] = field(default_factory=list)
    key_commands: list[str] = field(default_factory=list)
    key_files: list[str] = field(default_factory=list)
    required_tools: list[str] = field(default_factory=list)
    verification: list[str] = field(default_factory=list)
    pitfalls: list[str] = field(default_factory=list)
    generalization_boundary: str = ""
    recommended_capability_type: str = "skill"
    suggested_triggers: list[str] = field(default_factory=list)
    suggested_tags: list[str] = field(default_factory=list)
    source_trace_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "problem": self.problem,
            "context": self.context,
            "successful_steps": self.successful_steps,
            "failed_attempts": self.failed_attempts,
            "key_commands": self.key_commands,
            "key_files": self.key_files,
            "required_tools": self.required_tools,
            "verification": self.verification,
            "pitfalls": self.pitfalls,
            "generalization_boundary": self.generalization_boundary,
            "recommended_capability_type": self.recommended_capability_type,
            "suggested_triggers": self.suggested_triggers,
            "suggested_tags": self.suggested_tags,
            "source_trace_id": self.source_trace_id,
        }


# ── Helper: dangerous command detection ────────────────────────────────────

_DANGEROUS_CMD_PATTERNS: list[str] = [
    "rm -rf", "sudo rm", "chmod 777", "curl", "| sh", "| bash",
    "wget", "mkfs", "dd if=", "> /etc", "> /var", "eval $",
    "/etc/passwd", "/etc/shadow", "/etc/sudoers",
]

_CORRECTION_KEYWORDS: list[str] = [
    "wrong", "incorrect", "no,", "actually", "instead", "fix", "correct",
]

_REUSE_KEYWORDS: list[str] = [
    "save this", "remember this", "make this a", "create a skill",
    "create a workflow", "reuse", "template",
]

_ENV_SETUP_KEYWORDS: list[str] = [
    "export ", "venv", "virtualenv", "docker", "nix", "pip install",
    "apt-get", "brew", "conda", "poetry", "pipenv", "source ",
    "PATH=", "PYTHONPATH=", ".env",
]

_PROJECT_PATH_PATTERNS: list[str] = [
    "src/", "tests/", "config/", "data/",
]


# ── Curator ─────────────────────────────────────────────────────────────────


class ExperienceCurator:
    """Stateless, deterministic curator. No constructor args, no side effects."""

    # ── Public API ──────────────────────────────────────────────────────

    def should_reflect(self, trace: TraceSummary) -> CuratorDecision:
        """Analyze a TraceSummary and decide whether a capability proposal is warranted."""
        reasons: list[str] = []
        should_create = False
        action = "no_action"
        confidence = 0.0

        # ── No-action gates (checked first, they override everything) ──
        no_action_reason = self._check_no_action(trace)
        if no_action_reason:
            return CuratorDecision(
                should_create=False,
                recommended_action="no_action",
                confidence=0.9,
                reasons=[no_action_reason],
                risk_level="low",
                required_approval=False,
            )

        # ── Signal detection ──────────────────────────────────────────
        signals: list[tuple[str, str, float]] = []

        if len(trace.tools_used) >= 5:
            signals.append(("create_skill_draft", "tools_used >= 5 (complex multi-tool workflow)", 0.6))

        if trace.failed_attempts and trace.successful_steps and trace.errors_seen:
            action_type = "create_workflow_draft" if trace.commands_run else "create_skill_draft"
            signals.append((action_type, "failed_then_succeeded pattern detected", 0.7))

        fb = (trace.user_feedback or "").lower()
        if any(kw in fb for kw in _CORRECTION_KEYWORDS):
            signals.append(("create_skill_draft", "user correction detected in feedback", 0.6))

        repeat_count = trace.metadata.get("repetition_count", 0)
        if isinstance(repeat_count, (int, float)) and repeat_count >= 3:
            signals.append(("create_skill_draft", f"repeated task pattern (count={repeat_count})", 0.6))
        elif trace.task_type and trace.task_type.strip() and trace.task_type.strip().lower() not in ("chat",):
            signals.append(("create_skill_draft", f"non-trivial task type: {trace.task_type}", 0.5))

        if trace.files_touched and trace.commands_run:
            edit_cmds = {"sed", "awk", "patch", "apply", "write", "edit", "replace"}
            if any(any(ec in cmd.lower() for ec in edit_cmds) for cmd in trace.commands_run):
                signals.append(("create_workflow_draft", "file patch/edit workflow detected", 0.6))

        if len(trace.commands_run) >= 3:
            has_pipes = any("&&" in c or "|" in c for c in trace.commands_run)
            if has_pipes:
                signals.append(("create_workflow_draft", "multi-step shell workflow with pipes/chains", 0.7))
            else:
                signals.append(("create_workflow_draft", f"multi-step shell workflow ({len(trace.commands_run)} commands)", 0.6))

        ur = (trace.user_request or "").lower()
        if any(kw in ur or kw in fb for kw in _REUSE_KEYWORDS):
            signals.append(("create_skill_draft", "user explicitly requested reuse", 0.8))

        if trace.existing_capability_id and trace.errors_seen:
            signals.append(("patch_existing_proposal", f"existing capability {trace.existing_capability_id} failed", 0.7))

        ctx = (trace.context or "").lower()
        project_paths = sum(1 for p in _PROJECT_PATH_PATTERNS if p in ctx)
        if project_paths >= 2:
            signals.append(("create_project_playbook_draft", "project-specific workflow (multiple project paths)", 0.6))

        if any(any(ek in cmd.lower() for ek in _ENV_SETUP_KEYWORDS) for cmd in trace.commands_run):
            signals.append(("create_workflow_draft", "non-obvious environment setup detected", 0.6))

        if trace.verification and len(trace.successful_steps) >= 2:
            confidence_boost = 0.1
        else:
            confidence_boost = 0.0

        # ── No usable signals → no_action ──────────────────────────────
        if not signals:
            return CuratorDecision(
                should_create=False,
                recommended_action="no_action",
                confidence=0.8,
                reasons=["no reusable procedure detected"],
                risk_level="low",
                required_approval=False,
            )

        # ── Pick highest-confidence signal ─────────────────────────────
        best = max(signals, key=lambda s: s[2])
        action, reason, base_conf = best
        confidence = min(1.0, round(base_conf + confidence_boost, 2))

        # ── Risk level determination ───────────────────────────────────
        risk_level = self._determine_risk(trace)
        required_approval = risk_level == "high" or confidence < 0.5

        reasons = [reason]
        if confidence_boost:
            reasons.append("stable verification steps boost confidence")

        return CuratorDecision(
            should_create=True,
            recommended_action=action,
            confidence=confidence,
            reasons=reasons,
            risk_level=risk_level,
            required_approval=required_approval,
        )

    def summarize(self, trace: TraceSummary) -> CuratedExperience:
        """Extract a CuratedExperience from a TraceSummary."""
        # Derive capability type from the decision.
        decision = self.should_reflect(trace)
        cap_type = _action_to_type(decision.recommended_action)

        # Derive triggers from task_type and user request keywords.
        triggers: list[str] = []
        if trace.task_type:
            triggers.append(trace.task_type)
        ur = trace.user_request.lower()
        for kw in ["deploy", "test", "build", "analyze", "fix", "refactor", "migrate", "setup"]:
            if kw in ur:
                triggers.append(kw)

        # Derive tags.
        tags: list[str] = []
        for tool in trace.tools_used[:5]:
            tags.append(tool.replace("_", "-"))
        if trace.task_type:
            tags.append(trace.task_type.replace(" ", "-").lower())

        # Generalization boundary heuristic.
        ctx = (trace.context or "").lower()
        if any(p in ctx for p in _PROJECT_PATH_PATTERNS):
            boundary = "this project — patterns may differ in other codebases"
        else:
            boundary = "similar tasks with the same tools and environment"

        return CuratedExperience(
            problem=trace.user_request[:500] if trace.user_request else "",
            context=trace.context or "",
            successful_steps=list(trace.successful_steps),
            failed_attempts=list(trace.failed_attempts),
            key_commands=list(dict.fromkeys(trace.commands_run))[-10:],
            key_files=list(dict.fromkeys(trace.files_touched))[:20],
            required_tools=list(dict.fromkeys(trace.tools_used)),
            verification=list(trace.verification),
            pitfalls=_derive_pitfalls(trace),
            generalization_boundary=boundary,
            recommended_capability_type=cap_type,
            suggested_triggers=list(dict.fromkeys(triggers))[:6],
            suggested_tags=list(dict.fromkeys(tags))[:8],
            source_trace_id=trace.trace_id,
        )

    def propose_capability(
        self,
        curated: CuratedExperience,
        *,
        scope: str = "workspace",
        cap_type: str | None = None,
        risk_level: str | None = None,
        approval: dict[str, Any] | None = None,
        proposed_id: str | None = None,
        name: str | None = None,
    ) -> "CapabilityProposal":
        """Generate a CapabilityProposal from a CuratedExperience.

        Returns a CapabilityProposal (lazy import to avoid circular deps).
        """
        from src.capabilities.ids import generate_capability_id
        from src.capabilities.proposal import CapabilityProposal

        import uuid

        proposal_id = proposed_id or f"prop_{uuid.uuid4().hex[:8]}"
        if ".." in proposal_id or "/" in proposal_id or "\\" in proposal_id:
            raise ValueError(f"proposed_id contains unsafe characters: {proposal_id!r}")
        capability_id = generate_capability_id(scope)

        resolved_type = cap_type or curated.recommended_capability_type or "skill"
        resolved_risk = risk_level or "low"
        required_approval = resolved_risk == "high" or bool(
            approval is not None and not approval.get("approved", True)
        )

        derived_name = name or _derive_name(curated.problem)

        body = _generate_body_markdown(curated, derived_name, resolved_type, scope)

        return CapabilityProposal(
            proposal_id=proposal_id,
            source_trace_id=curated.source_trace_id,
            proposed_capability_id=capability_id,
            name=derived_name,
            description=_first_sentence(curated.problem, 200),
            type=resolved_type,
            scope=scope,
            maturity="draft",
            status="active",
            risk_level=resolved_risk,
            trust_required="developer",
            required_tools=curated.required_tools,
            required_permissions=[],
            triggers=curated.suggested_triggers,
            tags=curated.suggested_tags,
            body_markdown=body,
            generalization_boundary=curated.generalization_boundary,
            required_approval=required_approval,
        )

    # ── Internal heuristics ────────────────────────────────────────────

    @staticmethod
    def _check_no_action(trace: TraceSummary) -> str | None:
        """Return a reason string if this trace should NOT produce a proposal."""
        # Simple chat: no tools, no commands, no files
        if not trace.tools_used and not trace.commands_run and not trace.files_touched:
            return "simple chat — no tools, commands, or files used"

        # No reusable procedure
        if not trace.successful_steps and not trace.commands_run:
            return "no reusable procedure — no successful steps or commands"

        # Contains obvious secrets (double-check after sanitization)
        sanitized = trace.sanitize()
        if "<REDACTED>" in sanitized.user_request:
            return "contains sensitive secrets that would enter capability text"

        return None

    @staticmethod
    def _determine_risk(trace: TraceSummary) -> str:
        """Determine risk level from trace content."""
        # High risk indicators
        all_text = " ".join(trace.commands_run + [trace.user_request, trace.context or ""]).lower()
        for pat in _DANGEROUS_CMD_PATTERNS:
            if pat.lower() in all_text:
                return "high"

        for err in trace.errors_seen:
            if "permission denied" in err.lower():
                return "high"

        for f in trace.files_touched:
            if f.startswith("/etc/") or f.startswith("/var/") or f.startswith("/usr/"):
                return "high"

        # Medium risk
        if len(trace.commands_run) >= 3 or trace.files_touched or "execute_shell" in trace.tools_used:
            return "medium"

        return "low"


# ── Module-private helpers ──────────────────────────────────────────────────


def _action_to_type(action: str) -> str:
    if "workflow" in action:
        return "workflow"
    if "project_playbook" in action:
        return "project_playbook"
    return "skill"


def _derive_pitfalls(trace: TraceSummary) -> list[str]:
    pitfalls: list[str] = []
    for err in trace.errors_seen[:5]:
        pitfalls.append(f"Error encountered: {err[:200]}")
    for fa in trace.failed_attempts[:3]:
        pitfalls.append(f"Failed approach: {fa[:200]}")
    return pitfalls


def _derive_name(problem: str) -> str:
    """Derive a capability name from the problem description."""
    if not problem:
        return "Unnamed Capability"
    cleaned = problem.strip().rstrip(".").replace("\n", " ")
    if len(cleaned) <= 80:
        return cleaned
    return cleaned[:77] + "..."


def _first_sentence(text: str, max_len: int = 200) -> str:
    """Extract a short description from longer text."""
    if not text:
        return ""
    cleaned = text.strip().replace("\n", " ")
    if len(cleaned) <= max_len:
        return cleaned
    # Try to break at sentence boundary.
    truncated = cleaned[:max_len]
    for sep in (". ", "! ", "? "):
        idx = truncated.rfind(sep)
        if idx > max_len // 2:
            return truncated[:idx + 1]
    return truncated[: max_len - 3] + "..."


def _generate_body_markdown(
    curated: CuratedExperience,
    name: str,
    cap_type: str,
    scope: str,
) -> str:
    """Generate CAPABILITY.md format body with required sections."""

    def _list(items: list[str]) -> str:
        if not items:
            return "_None recorded._"
        return "\n".join(f"- {item}" for item in items)

    return f"""## When to use

{curated.problem or '_No description provided._'}

## Inputs

- **Required tools:** {_list(curated.required_tools) if curated.required_tools else '_None specified._'}
- **Key files:** {_list(curated.key_files) if curated.key_files else '_None specified._'}

## Procedure

{_list(curated.successful_steps) if curated.successful_steps else '_No steps recorded._'}

## Verification

{_list(curated.verification) if curated.verification else '_No verification steps recorded._'}

## Failure handling

{_list(curated.pitfalls) if curated.pitfalls else '_No known pitfalls recorded._'}

## Generalization boundary

{curated.generalization_boundary or '_Not specified._'}

## Notes

- **Capability type:** {cap_type}
- **Scope:** {scope}
- **Context:** {curated.context or '_None provided._'}
- **Key commands:** {_list(curated.key_commands) if curated.key_commands else '_None recorded._'}

## Source trace

{curated.source_trace_id or '_No trace ID recorded._'}
"""
