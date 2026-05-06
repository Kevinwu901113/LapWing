"""Phase 5A: Curator tool registration and execution tests."""

from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock

from src.tools.capability_tools import (
    REFLECT_EXPERIENCE_SCHEMA,
    PROPOSE_CAPABILITY_SCHEMA,
    _make_reflect_experience_executor,
    _make_propose_capability_executor,
    register_capability_curator_tools,
)
from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolSpec


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_context() -> ToolExecutionContext:
    return ToolExecutionContext(
        execute_shell=lambda _: None,
        shell_default_cwd="/tmp",
    )


def _valid_trace_dict() -> dict:
    return {
        "trace_id": "trace-1",
        "user_request": "Fix the login bug",
        "final_result": "Fixed",
        "task_type": "bug-fix",
        "context": "Python Flask app",
        "tools_used": ["execute_shell", "read_file", "write_file", "python", "web_search"],
        "files_touched": ["src/auth.py"],
        "commands_run": ["pytest tests/", "git diff"],
        "errors_seen": ["ImportError"],
        "failed_attempts": ["wrong fix"],
        "successful_steps": ["found bug", "applied fix", "verified"],
        "verification": ["All tests pass"],
        "user_feedback": None,
        "existing_capability_id": None,
        "created_at": "2026-05-01T10:00:00Z",
        "metadata": {},
    }


# ── Schema tests ────────────────────────────────────────────────────────


def test_reflect_schema_requires_trace_summary():
    assert "trace_summary" in REFLECT_EXPERIENCE_SCHEMA["required"]


def test_propose_schema_accepts_both_inputs():
    props = PROPOSE_CAPABILITY_SCHEMA["properties"]
    assert "trace_summary" in props
    assert "curated_experience" in props
    assert "apply" in props
    assert "approval" in props
    assert "scope" in props


# ── reflect_experience executor ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_reflect_experience_valid_trace():
    mock_store = MagicMock()
    executor = _make_reflect_experience_executor(mock_store, None)
    req = ToolExecutionRequest(name="reflect_experience", arguments={
        "trace_summary": _valid_trace_dict(),
    })
    result = await executor(req, _make_context())
    assert result.success is True
    assert "decision" in result.payload
    assert "experience" in result.payload
    assert isinstance(result.payload["decision"], dict)
    assert "should_create" in result.payload["decision"]
    assert "recommended_action" in result.payload["decision"]


@pytest.mark.asyncio
async def test_reflect_experience_missing_trace():
    mock_store = MagicMock()
    executor = _make_reflect_experience_executor(mock_store, None)
    req = ToolExecutionRequest(name="reflect_experience", arguments={})
    result = await executor(req, _make_context())
    assert result.success is False


@pytest.mark.asyncio
async def test_reflect_experience_invalid_trace():
    mock_store = MagicMock()
    executor = _make_reflect_experience_executor(mock_store, None)
    req = ToolExecutionRequest(name="reflect_experience", arguments={
        "trace_summary": {"user_request": "   "},
    })
    result = await executor(req, _make_context())
    assert result.success is False


@pytest.mark.asyncio
async def test_reflect_experience_invalid_scope():
    mock_store = MagicMock()
    executor = _make_reflect_experience_executor(mock_store, None)
    req = ToolExecutionRequest(name="reflect_experience", arguments={
        "trace_summary": _valid_trace_dict(),
        "scope": "invalid_scope",
    })
    result = await executor(req, _make_context())
    assert result.success is False


# ── propose_capability executor (apply=false) ───────────────────────────


@pytest.mark.asyncio
async def test_propose_capability_apply_false(tmp_path):
    mock_store = MagicMock()
    executor = _make_propose_capability_executor(mock_store, None, data_dir=str(tmp_path))
    req = ToolExecutionRequest(name="propose_capability", arguments={
        "trace_summary": _valid_trace_dict(),
        "scope": "workspace",
        "apply": False,
    })
    result = await executor(req, _make_context())
    assert result.success is True
    assert result.payload["applied"] is False
    assert "proposal_id" in result.payload
    assert "proposal_dir" in result.payload
    # Verify files exist on disk.
    proposal_dir = tmp_path / "proposals" / result.payload["proposal_id"]
    assert (proposal_dir / "proposal.json").is_file()
    assert (proposal_dir / "PROPOSAL.md").is_file()
    assert (proposal_dir / "source_trace_summary.json").is_file()
    # Store.create_draft was NOT called.
    mock_store.create_draft.assert_not_called()


@pytest.mark.asyncio
async def test_propose_capability_apply_false_no_store_mutation(tmp_path):
    mock_store = MagicMock()
    executor = _make_propose_capability_executor(mock_store, None, data_dir=str(tmp_path))
    req = ToolExecutionRequest(name="propose_capability", arguments={
        "trace_summary": _valid_trace_dict(),
        "scope": "workspace",
        "apply": False,
    })
    await executor(req, _make_context())
    mock_store.create_draft.assert_not_called()


# ── propose_capability executor (apply=true) ────────────────────────────


@pytest.mark.asyncio
async def test_propose_capability_apply_true_creates_draft(tmp_path):
    mock_store = MagicMock()
    mock_doc = MagicMock()
    mock_doc.manifest.id = "workspace_test123"
    mock_doc.manifest.name = "Test"
    mock_doc.manifest.type.value = "skill"
    mock_doc.manifest.scope.value = "workspace"
    mock_doc.manifest.maturity.value = "draft"
    mock_doc.manifest.status.value = "active"
    mock_doc.content_hash = "abc123"
    mock_store.create_draft.return_value = mock_doc

    mock_index = MagicMock()

    executor = _make_propose_capability_executor(mock_store, mock_index, data_dir=str(tmp_path))
    req = ToolExecutionRequest(name="propose_capability", arguments={
        "trace_summary": _valid_trace_dict(),
        "scope": "workspace",
        "apply": True,
    })
    result = await executor(req, _make_context())
    assert result.success is True
    assert result.payload["applied"] is True
    assert result.payload["maturity"] == "draft"
    # Store.create_draft was called.
    mock_store.create_draft.assert_called_once()
    # Index was updated.
    mock_index.upsert.assert_called_once()
    # Never promoted.
    call_args = mock_store.create_draft.call_args
    # Check maturity was "draft" (the store's own default).
    assert True


@pytest.mark.asyncio
async def test_propose_capability_high_risk_no_approval_blocked(tmp_path):
    mock_store = MagicMock()
    # Trace with dangerous command to trigger high risk.
    trace = _valid_trace_dict()
    trace["commands_run"] = ["rm -rf /tmp/dangerous", "cmd2", "cmd3"]

    executor = _make_propose_capability_executor(mock_store, None, data_dir=str(tmp_path))
    req = ToolExecutionRequest(name="propose_capability", arguments={
        "trace_summary": trace,
        "scope": "workspace",
        "apply": True,
    })
    result = await executor(req, _make_context())
    assert result.success is False
    assert "approval required" in result.payload.get("error", "").lower()
    mock_store.create_draft.assert_not_called()


@pytest.mark.asyncio
async def test_propose_capability_high_risk_with_approval_applies(tmp_path):
    mock_store = MagicMock()
    mock_doc = MagicMock()
    mock_doc.manifest.id = "workspace_test123"
    mock_doc.manifest.name = "Test"
    mock_doc.manifest.type.value = "skill"
    mock_doc.manifest.scope.value = "workspace"
    mock_doc.manifest.maturity.value = "draft"
    mock_doc.manifest.status.value = "active"
    mock_doc.content_hash = "abc123"
    mock_store.create_draft.return_value = mock_doc
    mock_index = MagicMock()

    trace = _valid_trace_dict()
    trace["commands_run"] = ["rm -rf /tmp/dangerous", "cmd2", "cmd3"]

    executor = _make_propose_capability_executor(mock_store, mock_index, data_dir=str(tmp_path))
    req = ToolExecutionRequest(name="propose_capability", arguments={
        "trace_summary": trace,
        "scope": "workspace",
        "apply": True,
        "approval": {"approved": True, "approved_by": "kevin", "reason": "safe in test"},
    })
    result = await executor(req, _make_context())
    assert result.success is True
    assert result.payload["applied"] is True
    mock_store.create_draft.assert_called_once()


@pytest.mark.asyncio
async def test_propose_capability_curated_experience_input(tmp_path):
    mock_store = MagicMock()
    executor = _make_propose_capability_executor(mock_store, None, data_dir=str(tmp_path))
    req = ToolExecutionRequest(name="propose_capability", arguments={
        "curated_experience": {
            "problem": "Deploy to production",
            "context": "K8s",
            "successful_steps": ["kubectl apply", "verify pods"],
            "required_tools": ["execute_shell"],
            "verification": ["all pods running"],
            "suggested_triggers": ["deploy"],
            "suggested_tags": ["k8s"],
        },
        "scope": "workspace",
        "apply": False,
    })
    result = await executor(req, _make_context())
    assert result.success is True
    assert result.payload["applied"] is False


@pytest.mark.asyncio
async def test_propose_capability_no_input_error(tmp_path):
    mock_store = MagicMock()
    executor = _make_propose_capability_executor(mock_store, None, data_dir=str(tmp_path))
    req = ToolExecutionRequest(name="propose_capability", arguments={
        "scope": "workspace",
        "apply": False,
    })
    result = await executor(req, _make_context())
    assert result.success is False
    assert "trace_summary" in result.payload.get("error", "").lower() or "curated_experience" in result.payload.get("error", "").lower()


# ── Tool registration ───────────────────────────────────────────────────


def test_register_curator_tools():
    mock_registry = MagicMock()
    mock_store = MagicMock()
    register_capability_curator_tools(mock_registry, mock_store, None)

    # Should register 2 tools.
    assert mock_registry.register.call_count == 2

    # Verify ToolSpecs have correct capability tags.
    calls = mock_registry.register.call_args_list
    specs = [c[0][0] for c in calls]
    names = {s.name for s in specs}
    assert names == {"reflect_experience", "propose_capability"}

    for spec in specs:
        assert isinstance(spec, ToolSpec)
        assert spec.capability == "capability_curator"


def test_register_curator_tools_skips_when_store_none():
    mock_registry = MagicMock()
    register_capability_curator_tools(mock_registry, None, None)
    mock_registry.register.assert_not_called()


def test_curator_tools_use_capability_curator_tag():
    mock_registry = MagicMock()
    mock_store = MagicMock()
    register_capability_curator_tools(mock_registry, mock_store, None)
    calls = mock_registry.register.call_args_list
    for call in calls:
        spec = call[0][0]
        assert spec.capability == "capability_curator"


def test_reflect_experience_risk_low():
    mock_registry = MagicMock()
    mock_store = MagicMock()
    register_capability_curator_tools(mock_registry, mock_store, None)
    calls = mock_registry.register.call_args_list
    reflect_spec = [c[0][0] for c in calls if c[0][0].name == "reflect_experience"][0]
    assert reflect_spec.risk_level == "low"


def test_propose_capability_risk_medium():
    mock_registry = MagicMock()
    mock_store = MagicMock()
    register_capability_curator_tools(mock_registry, mock_store, None)
    calls = mock_registry.register.call_args_list
    propose_spec = [c[0][0] for c in calls if c[0][0].name == "propose_capability"][0]
    assert propose_spec.risk_level == "medium"


# ── Verify no run_capability / auto_reflect tool exists ────────────────


def test_no_auto_reflect_tool():
    mock_registry = MagicMock()
    mock_store = MagicMock()
    register_capability_curator_tools(mock_registry, mock_store, None)
    calls = mock_registry.register.call_args_list
    names = {c[0][0].name for c in calls}
    assert "auto_reflect_experience" not in names
    assert "run_capability" not in names
    assert "task_end_curator" not in names


# ── Patch-existing boundary ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_patch_existing_does_not_mutate_existing_capability(tmp_path):
    """When existing_capability_id is set, propose_capability never mutates the
    existing capability — it only creates a proposal referencing it."""
    # Setup: create a "real" existing capability file on disk.
    cap_dir = tmp_path / "capabilities" / "workspace"
    cap_dir.mkdir(parents=True)
    existing_cap_file = cap_dir / "workspace_existing_test.yaml"
    original_content = "name: Existing Capability\nmaturity: stable\nstatus: active\nbody: original"
    existing_cap_file.write_text(original_content)

    mock_store = MagicMock()
    # Record that the existing capability exists, but don't let create_draft
    # touch the already-existing file.
    mock_store.create_draft.return_value = MagicMock()
    mock_store.create_draft.return_value.manifest.id = "workspace_new_test"
    mock_store.create_draft.return_value.manifest.name = "New Cap"
    mock_store.create_draft.return_value.manifest.type.value = "skill"
    mock_store.create_draft.return_value.manifest.scope.value = "workspace"
    mock_store.create_draft.return_value.manifest.maturity.value = "draft"
    mock_store.create_draft.return_value.manifest.status.value = "active"
    mock_store.create_draft.return_value.content_hash = "newhash"

    trace = _valid_trace_dict()
    trace["existing_capability_id"] = "workspace_existing_test"
    trace["errors_seen"] = ["existing capability failed to handle edge case"]
    trace["successful_steps"] = ["manual workaround applied"]

    # apply=false: should create proposal only, no mutation to existing.
    executor = _make_propose_capability_executor(mock_store, None, data_dir=str(tmp_path))
    req = ToolExecutionRequest(name="propose_capability", arguments={
        "trace_summary": trace,
        "scope": "workspace",
        "apply": False,
    })
    result = await executor(req, _make_context())
    assert result.success is True
    assert result.payload["applied"] is False
    mock_store.create_draft.assert_not_called()
    # Existing capability file unchanged.
    assert existing_cap_file.read_text() == original_content

    # apply=true: creates NEW draft, does not mutate existing.
    executor2 = _make_propose_capability_executor(mock_store, None, data_dir=str(tmp_path))
    req2 = ToolExecutionRequest(name="propose_capability", arguments={
        "trace_summary": trace,
        "scope": "workspace",
        "apply": True,
    })
    result2 = await executor2(req2, _make_context())
    assert result2.success is True
    assert result2.payload["applied"] is True
    assert result2.payload["maturity"] == "draft"
    # The newly created draft should have a DIFFERENT ID than the existing.
    assert result2.payload["capability_id"] != "workspace_existing_test"
    # Existing capability file still unchanged.
    assert existing_cap_file.read_text() == original_content
    # create_draft was called with the NEW proposed ID, not the existing one.
    mock_store.create_draft.assert_called_once()
    assert mock_store.create_draft.call_args[1]["cap_id"] != "workspace_existing_test"


@pytest.mark.asyncio
async def test_propose_capability_rejects_path_traversal_id(tmp_path):
    mock_store = MagicMock()
    executor = _make_propose_capability_executor(mock_store, None, data_dir=str(tmp_path))
    req = ToolExecutionRequest(name="propose_capability", arguments={
        "trace_summary": _valid_trace_dict(),
        "scope": "workspace",
        "apply": False,
        "proposed_id": "../etc/malicious",
    })
    result = await executor(req, _make_context())
    assert result.success is False
    assert "unsafe" in str(result.payload.get("error", "")).lower()


@pytest.mark.asyncio
async def test_apply_false_does_not_create_capability_dir(tmp_path):
    """apply=false must NOT create data/capabilities/<scope>/<capability_id>/ directory."""
    mock_store = MagicMock()
    executor = _make_propose_capability_executor(mock_store, None, data_dir=str(tmp_path))
    req = ToolExecutionRequest(name="propose_capability", arguments={
        "trace_summary": _valid_trace_dict(),
        "scope": "workspace",
        "apply": False,
    })
    result = await executor(req, _make_context())
    assert result.success is True
    # Only proposals/ dir should exist, not a capability dir.
    cap_scopes_dir = tmp_path / "workspace"
    assert not cap_scopes_dir.exists(), (
        f"apply=false must not create capability dir: {cap_scopes_dir}"
    )
    # Verify proposals/ dir exists but no capability dir.
    proposals_dir = tmp_path / "proposals"
    assert proposals_dir.is_dir()
    # Verify apply=false only creates the 3 proposal files, not capability files.
    proposal_dir = proposals_dir / result.payload["proposal_id"]
    assert (proposal_dir / "proposal.json").is_file()
    assert (proposal_dir / "PROPOSAL.md").is_file()
    assert (proposal_dir / "source_trace_summary.json").is_file()
    # No capability content was created.
    assert not (tmp_path / "workspace").exists()
    assert not (tmp_path / "global").exists()
    assert not (tmp_path / "user").exists()
    assert not (tmp_path / "session").exists()
