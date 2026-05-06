"""src/agents/candidate.py — AgentCandidate + evidence models for Phase 6B.

Agent candidates are a future-promotion staging area for dynamic agents.
They are NOT active agents, do NOT run, and do NOT affect ToolDispatcher.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field, asdict
from typing import Literal

from src.agents.spec import (
    VALID_APPROVAL_STATES,
    VALID_RISK_LEVELS,
    AgentSpec,
)
from src.core.time_utils import now as local_now

# ── Candidate ID safety ──
# Same spirit as _CAPABILITY_ID_RE but with hyphens allowed so candidate IDs
# can be more readable (e.g. "cand-a1b2c3d4").
_CANDIDATE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")

_VALID_EVIDENCE_TYPES: frozenset[str] = frozenset({
    "task_success",
    "task_failure",
    "manual_review",
    "policy_lint",
    "dry_run",
    "regression_test",
})

_VALID_SEVERITIES: frozenset[str] = frozenset({"info", "warning", "error"})


def validate_candidate_id(candidate_id: str) -> str:
    """Return candidate_id if safe; raise ValueError otherwise."""
    if not candidate_id or not isinstance(candidate_id, str):
        raise ValueError(f"candidate_id must be a non-empty string, got {candidate_id!r}")
    # Check path traversal first — gives a clearer error than regex mismatch
    # for obviously-malicious strings like "../../etc/passwd".
    if ".." in candidate_id or "/" in candidate_id or "\\" in candidate_id:
        raise ValueError(f"candidate_id {candidate_id!r} contains path traversal")
    if not _CANDIDATE_ID_RE.match(candidate_id):
        raise ValueError(
            f"candidate_id {candidate_id!r} must match [a-z][a-z0-9_-]{{2,63}}"
        )
    return candidate_id


def validate_evidence_id(evidence_id: str) -> str:
    """Return evidence_id if safe; raise ValueError otherwise."""
    if not evidence_id or not isinstance(evidence_id, str):
        raise ValueError(f"evidence_id must be a non-empty string, got {evidence_id!r}")
    if ".." in evidence_id or "/" in evidence_id or "\\" in evidence_id:
        raise ValueError(f"evidence_id {evidence_id!r} contains path traversal")
    if len(evidence_id) > 128:
        raise ValueError(f"evidence_id too long ({len(evidence_id)} > 128)")
    return evidence_id


# ── Evidence ──


@dataclass
class AgentEvalEvidence:
    """An evaluation data point for an agent candidate."""

    evidence_id: str = field(default_factory=lambda: f"ev_{uuid.uuid4().hex[:12]}")
    created_at: str = field(default_factory=lambda: local_now().isoformat())
    evidence_type: str = "task_success"
    summary: str = ""
    passed: bool = True
    score: float | None = None
    trace_id: str | None = None
    details: dict = field(default_factory=dict)

    def __post_init__(self):
        validate_evidence_id(self.evidence_id)
        if self.evidence_type not in _VALID_EVIDENCE_TYPES:
            raise ValueError(
                f"evidence_type {self.evidence_type!r} not in {sorted(_VALID_EVIDENCE_TYPES)}"
            )
        if self.score is not None and not (0.0 <= self.score <= 1.0):
            raise ValueError(f"score {self.score} out of [0.0, 1.0]")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AgentEvalEvidence":
        return cls(**data)


# ── Finding ──


@dataclass
class AgentCandidateFinding:
    """A policy lint finding recorded against a candidate."""

    severity: str = "info"
    code: str = ""
    message: str = ""
    details: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.severity not in _VALID_SEVERITIES:
            raise ValueError(
                f"severity {self.severity!r} not in {sorted(_VALID_SEVERITIES)}"
            )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AgentCandidateFinding":
        return cls(**data)


# ── Candidate ──


@dataclass
class AgentCandidate:
    """A proposed agent that may be promoted to persistent in a future phase.

    Candidates live in filesystem storage (data/agent_candidates/), separate
    from the active AgentCatalog. They cannot run, cannot be looked up by
    AgentRegistry.get_or_create_instance, and do not affect ToolDispatcher.
    """

    candidate_id: str = field(default_factory=lambda: f"cand_{uuid.uuid4().hex[:12]}")
    name: str = ""
    description: str = ""
    proposed_spec: AgentSpec = field(default_factory=AgentSpec)
    created_at: str = field(default_factory=lambda: local_now().isoformat())
    created_by: str | None = None
    source_trace_id: str | None = None
    source_task_summary: str | None = None
    reason: str = ""
    approval_state: str = "pending"
    risk_level: str = "low"
    requested_runtime_profile: str | None = None
    requested_tools: list[str] = field(default_factory=list)
    bound_capabilities: list[str] = field(default_factory=list)
    eval_evidence: list[AgentEvalEvidence] = field(default_factory=list)
    policy_findings: list[AgentCandidateFinding] = field(default_factory=list)
    version: str = "1"
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        validate_candidate_id(self.candidate_id)
        if self.approval_state not in VALID_APPROVAL_STATES:
            raise ValueError(
                f"approval_state {self.approval_state!r} not in {sorted(VALID_APPROVAL_STATES)}"
            )
        if self.risk_level not in VALID_RISK_LEVELS:
            raise ValueError(
                f"risk_level {self.risk_level!r} not in {sorted(VALID_RISK_LEVELS)}"
            )
        # Derive risk_level conservatively: if high-risk tools are requested
        # but risk_level is still the default "low", flag it.
        if self.risk_level == "low" and self.requested_runtime_profile:
            # Profiles that imply broader access warrant medium risk at minimum.
            _profiles_meriting_medium = {
                "chat_shell", "operator", "agent_manager",
            }
            if self.requested_runtime_profile in _profiles_meriting_medium:
                # Don't mutate — caller should set risk_level explicitly.
                pass

    def to_dict(self) -> dict:
        data: dict = {}
        data["candidate_id"] = self.candidate_id
        data["name"] = self.name
        data["description"] = self.description
        data["proposed_spec"] = asdict(self.proposed_spec)
        data["created_at"] = self.created_at
        data["created_by"] = self.created_by
        data["source_trace_id"] = self.source_trace_id
        data["source_task_summary"] = self.source_task_summary
        data["reason"] = self.reason
        data["approval_state"] = self.approval_state
        data["risk_level"] = self.risk_level
        data["requested_runtime_profile"] = self.requested_runtime_profile
        data["requested_tools"] = self.requested_tools
        data["bound_capabilities"] = self.bound_capabilities
        data["eval_evidence"] = [e.to_dict() for e in self.eval_evidence]
        data["policy_findings"] = [f.to_dict() for f in self.policy_findings]
        data["version"] = self.version
        data["metadata"] = self.metadata
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str, ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data: dict) -> "AgentCandidate":
        # Reconstruct proposed_spec from its dict form.
        spec_data = data.get("proposed_spec", {})
        if isinstance(spec_data, dict):
            from src.agents.spec import AgentLifecyclePolicy, AgentResourceLimits
            # Copy to avoid mutating caller's dict
            sd = dict(spec_data)
            lifecycle = AgentLifecyclePolicy(**sd.pop("lifecycle", {}))
            limits = AgentResourceLimits(**sd.pop("resource_limits", {}))
            created_at = sd.pop("created_at", local_now().isoformat())
            updated_at = sd.pop("updated_at", created_at)
            from datetime import datetime
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at)
            if isinstance(updated_at, str):
                updated_at = datetime.fromisoformat(updated_at)
            spec = AgentSpec(
                **sd,
                lifecycle=lifecycle,
                resource_limits=limits,
                created_at=created_at,
                updated_at=updated_at,
            )
        elif isinstance(spec_data, AgentSpec):
            spec = spec_data
        else:
            raise ValueError(
                f"proposed_spec must be a dict or AgentSpec, got {type(spec_data).__name__}"
            )

        # Reconstruct evidence list
        evidence = [
            AgentEvalEvidence.from_dict(e) for e in data.get("eval_evidence", [])
        ]
        findings = [
            AgentCandidateFinding.from_dict(f) for f in data.get("policy_findings", [])
        ]

        return cls(
            candidate_id=data.get("candidate_id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            proposed_spec=spec,
            created_at=data.get("created_at", local_now().isoformat()),
            created_by=data.get("created_by"),
            source_trace_id=data.get("source_trace_id"),
            source_task_summary=data.get("source_task_summary"),
            reason=data.get("reason", ""),
            approval_state=data.get("approval_state", "pending"),
            risk_level=data.get("risk_level", "low"),
            requested_runtime_profile=data.get("requested_runtime_profile"),
            requested_tools=data.get("requested_tools", []),
            bound_capabilities=data.get("bound_capabilities", []),
            eval_evidence=evidence,
            policy_findings=findings,
            version=data.get("version", "1"),
            metadata=data.get("metadata", {}),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "AgentCandidate":
        return cls.from_dict(json.loads(json_str))


def redact_secrets_in_summary(summary: str | None) -> str | None:
    """Strip obvious secrets from a user-provided task summary.

    This is a best-effort defense-in-depth measure. It looks for common
    patterns (API keys, tokens, passwords) and replaces them.
    """
    if summary is None:
        return None
    patterns = [
        (r'sk-[a-zA-Z0-9]{20,}', '[REDACTED_API_KEY]'),
        (r'Bearer\s+[a-zA-Z0-9_\-\.]{20,}', 'Bearer [REDACTED_TOKEN]'),
        (r'password\s*[:=]\s*\S+', 'password=[REDACTED]'),
        (r'api_key\s*[:=]\s*\S+', 'api_key=[REDACTED]'),
        (r'[A-Za-z0-9+/]{40,}={0,2}', '[REDACTED_BASE64]'),
    ]
    import re as _re
    result = summary
    for pattern, replacement in patterns:
        result = _re.sub(pattern, replacement, result)
    return result
