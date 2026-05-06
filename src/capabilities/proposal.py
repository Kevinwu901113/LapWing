"""CapabilityProposal model and minimal filesystem persistence.

Proposals live under ``data/capabilities/proposals/<proposal_id>/``
and contain three files: proposal.json, PROPOSAL.md, source_trace_summary.json.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from src.capabilities.curator import CuratorDecision
    from src.capabilities.trace_summary import TraceSummary

logger = logging.getLogger(__name__)


# ── Proposal model ──────────────────────────────────────────────────────────


@dataclass
class CapabilityProposal:
    """A draft capability proposal, optionally persisted to disk.

    ``applied`` is False until the proposal is explicitly turned into
    a draft capability via ``propose_capability(apply=True)``.
    """

    proposal_id: str
    source_trace_id: str | None
    proposed_capability_id: str
    name: str
    description: str
    type: str
    scope: str
    maturity: str = "draft"
    status: str = "active"
    risk_level: str = "low"
    trust_required: str = "developer"
    required_tools: list[str] = field(default_factory=list)
    required_permissions: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    body_markdown: str = ""
    generalization_boundary: str = ""
    required_approval: bool = False
    curator_decision: dict[str, Any] | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    applied: bool = False
    applied_capability_id: str | None = None
    applied_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "source_trace_id": self.source_trace_id,
            "proposed_capability_id": self.proposed_capability_id,
            "name": self.name,
            "description": self.description,
            "type": self.type,
            "scope": self.scope,
            "maturity": self.maturity,
            "status": self.status,
            "risk_level": self.risk_level,
            "trust_required": self.trust_required,
            "required_tools": self.required_tools,
            "required_permissions": self.required_permissions,
            "triggers": self.triggers,
            "tags": self.tags,
            "body_markdown": self.body_markdown,
            "generalization_boundary": self.generalization_boundary,
            "required_approval": self.required_approval,
            "curator_decision": self.curator_decision,
            "created_at": self.created_at,
            "applied": self.applied,
            "applied_capability_id": self.applied_capability_id,
            "applied_at": self.applied_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CapabilityProposal":
        return cls(
            proposal_id=str(d.get("proposal_id", "")),
            source_trace_id=str(d["source_trace_id"]) if d.get("source_trace_id") is not None else None,
            proposed_capability_id=str(d.get("proposed_capability_id", "")),
            name=str(d.get("name", "")),
            description=str(d.get("description", "")),
            type=str(d.get("type", "skill")),
            scope=str(d.get("scope", "workspace")),
            maturity=str(d.get("maturity", "draft")),
            status=str(d.get("status", "active")),
            risk_level=str(d.get("risk_level", "low")),
            trust_required=str(d.get("trust_required", "developer")),
            required_tools=_as_str_list(d.get("required_tools", [])),
            required_permissions=_as_str_list(d.get("required_permissions", [])),
            triggers=_as_str_list(d.get("triggers", [])),
            tags=_as_str_list(d.get("tags", [])),
            body_markdown=str(d.get("body_markdown", "")),
            generalization_boundary=str(d.get("generalization_boundary", "")),
            required_approval=bool(d.get("required_approval", False)),
            curator_decision=d.get("curator_decision") if isinstance(d.get("curator_decision"), dict) else None,
            created_at=str(d.get("created_at", "")),
            applied=bool(d.get("applied", False)),
            applied_capability_id=str(d["applied_capability_id"]) if d.get("applied_capability_id") is not None else None,
            applied_at=str(d["applied_at"]) if d.get("applied_at") is not None else None,
        )


# ── Persistence ─────────────────────────────────────────────────────────────


def _proposals_dir(data_dir: Path | str) -> Path:
    return Path(data_dir) / "proposals"


def persist_proposal(
    proposal: CapabilityProposal,
    trace_summary: "TraceSummary",
    data_dir: Path | str,
) -> Path:
    """Persist a proposal to ``data_dir/proposals/<proposal_id>/``.

    Creates three files: proposal.json, PROPOSAL.md, source_trace_summary.json.
    Returns the proposal directory path.
    Raises FileExistsError if the proposal directory already exists.
    """
    prop_dir = _proposals_dir(data_dir) / proposal.proposal_id
    prop_dir.mkdir(parents=True, exist_ok=False)

    try:
        # 1. proposal.json
        _write_json(prop_dir / "proposal.json", proposal.to_dict())

        # 2. PROPOSAL.md
        _write_proposal_md(prop_dir / "PROPOSAL.md", proposal)

        # 3. source_trace_summary.json (already sanitized by caller)
        _write_json(prop_dir / "source_trace_summary.json", trace_summary.to_dict())

        logger.info("Persisted proposal %s to %s", proposal.proposal_id, prop_dir)
        return prop_dir
    except Exception:
        # Clean up partial writes on failure.
        if prop_dir.exists():
            import shutil
            shutil.rmtree(prop_dir, ignore_errors=True)
        raise


def load_proposal(proposal_id: str, data_dir: Path | str) -> CapabilityProposal | None:
    """Load a proposal by ID. Returns None if not found or malformed."""
    prop_json = _proposals_dir(data_dir) / proposal_id / "proposal.json"
    if not prop_json.is_file():
        return None
    try:
        data = json.loads(prop_json.read_text(encoding="utf-8"))
        return CapabilityProposal.from_dict(data)
    except (json.JSONDecodeError, ValueError, KeyError):
        logger.debug("Failed to load proposal %s", proposal_id, exc_info=True)
        return None


def list_proposals(data_dir: Path | str) -> list[CapabilityProposal]:
    """List all proposals sorted by created_at descending."""
    proposals_dir = _proposals_dir(data_dir)
    if not proposals_dir.is_dir():
        return []

    results: list[CapabilityProposal] = []
    for entry in sorted(proposals_dir.iterdir(), reverse=True):
        if not entry.is_dir():
            continue
        prop = load_proposal(entry.name, data_dir)
        if prop is not None:
            results.append(prop)

    results.sort(key=lambda p: p.created_at, reverse=True)
    return results


def mark_applied(
    proposal_id: str,
    applied_capability_id: str,
    data_dir: Path | str,
) -> bool:
    """Mark a proposal as applied with the given capability ID."""
    prop = load_proposal(proposal_id, data_dir)
    if prop is None:
        return False

    prop.applied = True
    prop.applied_capability_id = applied_capability_id
    prop.applied_at = datetime.now(timezone.utc).isoformat()

    prop_json = _proposals_dir(data_dir) / proposal_id / "proposal.json"
    _write_json(prop_json, prop.to_dict())
    return True


# ── Internal helpers ────────────────────────────────────────────────────────


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_proposal_md(path: Path, proposal: CapabilityProposal) -> None:
    """Write PROPOSAL.md in CAPABILITY.md format with YAML front matter."""
    front_matter = {
        "proposal_id": proposal.proposal_id,
        "source_trace_id": proposal.source_trace_id,
        "id": proposal.proposed_capability_id,
        "name": proposal.name,
        "description": proposal.description,
        "type": proposal.type,
        "scope": proposal.scope,
        "maturity": proposal.maturity,
        "status": proposal.status,
        "risk_level": proposal.risk_level,
        "trust_required": proposal.trust_required,
        "required_tools": proposal.required_tools,
        "required_permissions": proposal.required_permissions,
        "triggers": proposal.triggers,
        "tags": proposal.tags,
        "generalization_boundary": proposal.generalization_boundary,
        "required_approval": proposal.required_approval,
        "created_at": proposal.created_at,
        "applied": proposal.applied,
        "applied_capability_id": proposal.applied_capability_id,
    }
    fm_yaml = yaml.dump(front_matter, allow_unicode=True, sort_keys=False).strip()
    md = f"---\n{fm_yaml}\n---\n\n{proposal.body_markdown}"
    path.write_text(md, encoding="utf-8")


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return []
