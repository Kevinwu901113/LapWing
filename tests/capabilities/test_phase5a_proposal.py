"""Phase 5A: CapabilityProposal model + persistence tests."""

from __future__ import annotations

import json
import pytest
from pathlib import Path

from src.capabilities.proposal import (
    CapabilityProposal,
    list_proposals,
    load_proposal,
    mark_applied,
    persist_proposal,
)
from src.capabilities.trace_summary import TraceSummary


def _make_proposal(**overrides) -> CapabilityProposal:
    defaults: dict = {
        "proposal_id": "prop_test001",
        "source_trace_id": "trace-1",
        "proposed_capability_id": "workspace_abc12345",
        "name": "Test Proposal",
        "description": "A test capability proposal",
        "type": "skill",
        "scope": "workspace",
        "maturity": "draft",
        "status": "active",
        "risk_level": "low",
        "trust_required": "developer",
        "required_tools": ["shell", "python"],
        "required_permissions": [],
        "triggers": ["test", "pytest"],
        "tags": ["testing", "automation"],
        "body_markdown": "## Procedure\n\nRun tests and fix issues.",
        "generalization_boundary": "Python projects with pytest",
        "required_approval": False,
        "curator_decision": None,
        "created_at": "2026-05-01T10:00:00Z",
        "applied": False,
        "applied_capability_id": None,
        "applied_at": None,
    }
    defaults.update(overrides)
    return CapabilityProposal(**defaults)


def _make_trace() -> TraceSummary:
    return TraceSummary.from_dict({
        "user_request": "Run test suite",
        "trace_id": "trace-1",
        "tools_used": ["shell", "python"],
        "files_touched": ["tests/test_app.py"],
        "commands_run": ["pytest"],
        "successful_steps": ["Tests pass"],
        "verification": ["All green"],
        "created_at": "2026-05-01T10:00:00Z",
    })


# ── Model tests ─────────────────────────────────────────────────────────


def test_create_minimal_proposal():
    p = CapabilityProposal(
        proposal_id="prop_001",
        source_trace_id=None,
        proposed_capability_id="workspace_test",
        name="Minimal",
        description="Minimal proposal",
        type="skill",
        scope="workspace",
    )
    assert p.proposal_id == "prop_001"
    assert p.maturity == "draft"
    assert p.status == "active"
    assert p.applied is False


def test_to_dict_includes_all_fields():
    p = _make_proposal()
    d = p.to_dict()
    assert d["proposal_id"] == "prop_test001"
    assert d["name"] == "Test Proposal"
    assert d["required_tools"] == ["shell", "python"]
    assert d["triggers"] == ["test", "pytest"]
    assert d["body_markdown"] == "## Procedure\n\nRun tests and fix issues."
    assert d["applied"] is False
    assert d["applied_capability_id"] is None


def test_from_dict_parses_back():
    p = _make_proposal()
    d = p.to_dict()
    p2 = CapabilityProposal.from_dict(d)
    assert p2.proposal_id == p.proposal_id
    assert p2.name == p.name
    assert p2.type == p.type
    assert p2.scope == p.scope
    assert p2.maturity == p.maturity
    assert p2.required_tools == p.required_tools
    assert p2.body_markdown == p.body_markdown


def test_from_dict_minimal():
    p = CapabilityProposal.from_dict({
        "proposal_id": "prop_min",
        "proposed_capability_id": "workspace_xyz",
        "name": "Min",
        "description": "Min",
        "type": "skill",
        "scope": "workspace",
    })
    assert p.proposal_id == "prop_min"
    assert p.risk_level == "low"
    assert p.maturity == "draft"
    assert p.required_tools == []


# ── Persistence tests ───────────────────────────────────────────────────


def test_persist_proposal_creates_files(tmp_path):
    p = _make_proposal()
    ts = _make_trace().sanitize()
    prop_dir = persist_proposal(p, ts, tmp_path)

    assert prop_dir.is_dir()
    assert (prop_dir / "proposal.json").is_file()
    assert (prop_dir / "PROPOSAL.md").is_file()
    assert (prop_dir / "source_trace_summary.json").is_file()


def test_persist_proposal_collision_raises(tmp_path):
    p = _make_proposal()
    ts = _make_trace().sanitize()
    persist_proposal(p, ts, tmp_path)
    with pytest.raises(FileExistsError):
        persist_proposal(p, ts, tmp_path)


def test_persist_proposal_json_is_valid(tmp_path):
    p = _make_proposal()
    ts = _make_trace().sanitize()
    prop_dir = persist_proposal(p, ts, tmp_path)
    data = json.loads((prop_dir / "proposal.json").read_text())
    assert data["proposal_id"] == "prop_test001"
    assert data["name"] == "Test Proposal"


def test_proposal_md_has_yaml_front_matter(tmp_path):
    p = _make_proposal()
    ts = _make_trace().sanitize()
    prop_dir = persist_proposal(p, ts, tmp_path)
    md = (prop_dir / "PROPOSAL.md").read_text()
    assert md.startswith("---")
    assert "proposal_id: prop_test001" in md
    assert "id: workspace_abc12345" in md
    assert "## Procedure" in md


def test_source_trace_summary_is_redacted(tmp_path):
    ts = TraceSummary.from_dict({
        "user_request": "Use sk-abcdefghijklmnopqrstuvwxyz123456 for API",
        "commands_run": ["curl -H 'Authorization: Bearer token123' api.example.com"],
    })
    sanitized = ts.sanitize()
    p = _make_proposal()
    prop_dir = persist_proposal(p, sanitized, tmp_path)
    data = json.loads((prop_dir / "source_trace_summary.json").read_text())
    assert "sk-abcdefghij" not in data.get("user_request", "")
    assert "token123" not in str(data.get("commands_run", ""))


def test_load_proposal_valid(tmp_path):
    p = _make_proposal()
    ts = _make_trace().sanitize()
    persist_proposal(p, ts, tmp_path)
    loaded = load_proposal("prop_test001", tmp_path)
    assert loaded is not None
    assert loaded.proposal_id == "prop_test001"
    assert loaded.name == "Test Proposal"


def test_load_proposal_nonexistent(tmp_path):
    loaded = load_proposal("nonexistent", tmp_path)
    assert loaded is None


def test_load_proposal_malformed_json(tmp_path):
    prop_dir = tmp_path / "proposals" / "prop_bad"
    prop_dir.mkdir(parents=True)
    (prop_dir / "proposal.json").write_text("not valid json")
    loaded = load_proposal("prop_bad", tmp_path)
    assert loaded is None


def test_list_proposals_sorted(tmp_path):
    ts = _make_trace().sanitize()
    p1 = _make_proposal(proposal_id="prop_a", created_at="2026-05-01T08:00:00Z")
    p2 = _make_proposal(proposal_id="prop_b", created_at="2026-05-01T10:00:00Z")
    p3 = _make_proposal(proposal_id="prop_c", created_at="2026-05-01T09:00:00Z")
    persist_proposal(p1, ts, tmp_path)
    persist_proposal(p2, ts, tmp_path)
    persist_proposal(p3, ts, tmp_path)
    results = list_proposals(tmp_path)
    assert len(results) == 3
    # Sorted by created_at descending.
    assert results[0].proposal_id == "prop_b"
    assert results[1].proposal_id == "prop_c"
    assert results[2].proposal_id == "prop_a"


def test_list_proposals_empty(tmp_path):
    results = list_proposals(tmp_path)
    assert results == []


def test_mark_applied_updates_fields(tmp_path):
    p = _make_proposal()
    ts = _make_trace().sanitize()
    persist_proposal(p, ts, tmp_path)
    success = mark_applied("prop_test001", "workspace_new123", tmp_path)
    assert success is True
    loaded = load_proposal("prop_test001", tmp_path)
    assert loaded.applied is True
    assert loaded.applied_capability_id == "workspace_new123"
    assert loaded.applied_at is not None


def test_mark_applied_nonexistent(tmp_path):
    success = mark_applied("nonexistent", "workspace_xyz", tmp_path)
    assert success is False


def test_apply_false_does_not_create_capability_dir(tmp_path):
    """apply=false persists proposal only; no capability dir created in store."""
    p = _make_proposal()
    ts = _make_trace().sanitize()
    prop_dir = persist_proposal(p, ts, tmp_path)
    # No capability directories should be created (only proposal dir).
    capability_scope_dirs = list(tmp_path.glob("workspace/*/CAPABILITY.md"))
    assert len(capability_scope_dirs) == 0


def test_proposal_body_contains_required_sections():
    p = _make_proposal(body_markdown="""## When to use
Run tests.

## Procedure
1. Run pytest

## Verification
All tests pass

## Failure handling
Check logs

## Generalization boundary
Any Python project

## Source trace
trace-1
""")
    assert "When to use" in p.body_markdown
    assert "Procedure" in p.body_markdown
    assert "Verification" in p.body_markdown
    assert "Failure handling" in p.body_markdown
    assert "Generalization boundary" in p.body_markdown
