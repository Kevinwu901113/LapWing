"""Phase 7D-B tool tests: registration, permissions, dry run, error handling."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.capabilities.evaluator import CapabilityEvaluator
from src.capabilities.index import CapabilityIndex
from src.capabilities.policy import CapabilityPolicy
from src.capabilities.store import CapabilityStore
from src.tools.capability_tools import (
    register_quarantine_activation_apply_tools,
)
from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolSpec


# ── helpers ────────────────────────────────────────────────────────────


def _make_store(tmp_path: Path) -> CapabilityStore:
    return CapabilityStore(data_dir=tmp_path / "capabilities")


def _make_index(tmp_path: Path) -> CapabilityIndex:
    db_path = tmp_path / "index.sqlite"
    cap_index = CapabilityIndex(str(db_path))
    cap_index.init()
    return cap_index


def _make_evaluator() -> CapabilityEvaluator:
    return CapabilityEvaluator()


def _make_policy() -> CapabilityPolicy:
    return CapabilityPolicy()


class _FakeToolRegistry:
    def __init__(self):
        self.tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self.tools[spec.name] = spec


def _setup_quarantine_cap(store: CapabilityStore, cap_id: str):
    qroot = store.data_dir / "quarantine"
    qroot.mkdir(parents=True, exist_ok=True)
    qdir = qroot / cap_id
    qdir.mkdir(parents=True, exist_ok=True)

    import yaml
    fm = {
        "id": cap_id, "name": f"Test {cap_id}",
        "description": "Test.", "type": "skill", "scope": "user",
        "version": "0.1.0",
        "maturity": "draft", "status": "quarantined", "risk_level": "low",
        "triggers": ["when test"], "tags": ["test"],
        "trust_required": "developer", "required_tools": [], "required_permissions": [],
    }
    fm_yaml = yaml.dump(fm, allow_unicode=True, sort_keys=False).strip()
    md = (
        f"---\n{fm_yaml}\n---\n\n"
        "## When to use\nWhen testing.\n\n"
        "## Procedure\n1. Test\n\n"
        "## Verification\nCheck output.\n\n"
        "## Failure handling\nRetry."
    )
    (qdir / "CAPABILITY.md").write_text(md, encoding="utf-8")
    (qdir / "manifest.json").write_text(json.dumps({
        k: v for k, v in fm.items()
    }, indent=2), encoding="utf-8")

    (qdir / "import_report.json").write_text(json.dumps({
        "capability_id": cap_id, "source_type": "local_package",
        "source_path_hash": "abc", "imported_at": "2026-05-01T00:00:00Z",
        "eval_passed": True, "eval_score": 1.0,
        "eval_findings": [], "policy_findings": [],
        "files_summary": {"scripts": [], "tests": [], "examples": []},
        "name": f"Test {cap_id}", "type": "skill", "risk_level": "low",
    }, indent=2), encoding="utf-8")

    rev_dir = qdir / "quarantine_reviews"
    rev_dir.mkdir(exist_ok=True)
    (rev_dir / "review_test001.json").write_text(json.dumps({
        "capability_id": cap_id, "review_id": "review_test001",
        "review_status": "approved_for_testing", "reviewer": "tester",
        "reason": "OK", "created_at": "2026-05-02T10:00:00Z",
    }, indent=2), encoding="utf-8")

    aud_dir = qdir / "quarantine_audit_reports"
    aud_dir.mkdir(exist_ok=True)
    (aud_dir / "audit_test001.json").write_text(json.dumps({
        "capability_id": cap_id, "audit_id": "audit_test001",
        "created_at": "2026-05-02T09:00:00Z", "passed": True,
        "risk_level": "low", "findings": [],
        "recommended_review_status": "approved_for_testing",
    }, indent=2), encoding="utf-8")

    # Pending request
    req_dir = qdir / "quarantine_transition_requests"
    req_dir.mkdir(exist_ok=True)
    req_id = f"qtr_{cap_id[:8]}"
    (req_dir / f"{req_id}.json").write_text(json.dumps({
        "request_id": req_id, "capability_id": cap_id,
        "created_at": "2026-05-03T00:00:00Z",
        "requested_target_scope": "user", "requested_target_maturity": "testing",
        "status": "pending", "reason": "Ready", "risk_level": "low",
        "required_approval": False, "findings_summary": {},
        "content_hash_at_request": "",
        "source_review_id": "review_test001", "source_audit_id": "audit_test001",
    }, indent=2), encoding="utf-8")

    # Allowed plan
    plans_dir = qdir / "quarantine_activation_plans"
    plans_dir.mkdir(exist_ok=True)
    plan_id = f"qap_{cap_id[:8]}"
    (plans_dir / f"{plan_id}.json").write_text(json.dumps({
        "plan_id": plan_id, "capability_id": cap_id,
        "request_id": req_id, "created_at": "2026-05-03T08:00:00Z",
        "created_by": "operator",
        "source_review_id": "review_test001", "source_audit_id": "audit_test001",
        "target_scope": "user", "target_status": "active", "target_maturity": "testing",
        "allowed": True, "required_approval": False,
        "blocking_findings": [], "policy_findings": [], "evaluator_findings": [],
        "copy_plan": {"target_scope": "user", "total_files": 3},
        "content_hash": "", "request_content_hash": "",
        "risk_level": "low", "explanation": "OK.",
        "would_activate": False,
    }, indent=2), encoding="utf-8")


# ── Tool registration tests ───────────────────────────────────────────


class TestApplyActivationToolRegistration:
    """Tool presence, capability tag, and rejection for non-operator profiles."""

    def test_tool_registers_with_capability_import_operator(self):
        registry = _FakeToolRegistry()
        register_quarantine_activation_apply_tools(
            registry,
            store=CapabilityStore(data_dir="/tmp/fake"),
            index=CapabilityIndex("/tmp/fake_idx.sqlite"),
            evaluator=_make_evaluator(),
            policy=_make_policy(),
        )
        assert "apply_quarantine_activation" in registry.tools
        spec = registry.tools["apply_quarantine_activation"]
        assert spec.capability == "capability_import_operator"
        assert spec.risk_level == "high"

    def test_tool_requires_reason_in_schema(self):
        registry = _FakeToolRegistry()
        register_quarantine_activation_apply_tools(
            registry,
            store=CapabilityStore(data_dir="/tmp/fake"),
            index=CapabilityIndex("/tmp/fake_idx.sqlite"),
            evaluator=_make_evaluator(),
            policy=_make_policy(),
        )
        spec = registry.tools["apply_quarantine_activation"]
        required = spec.json_schema.get("required", [])
        assert "capability_id" in required
        assert "reason" in required

    def test_tool_has_dry_run_in_schema(self):
        registry = _FakeToolRegistry()
        register_quarantine_activation_apply_tools(
            registry,
            store=CapabilityStore(data_dir="/tmp/fake"),
            index=CapabilityIndex("/tmp/fake_idx.sqlite"),
            evaluator=_make_evaluator(),
            policy=_make_policy(),
        )
        spec = registry.tools["apply_quarantine_activation"]
        props = spec.json_schema.get("properties", {})
        assert "dry_run" in props
        assert props["dry_run"]["type"] == "boolean"

    def test_tool_not_in_non_operator_capabilities(self):
        registry = _FakeToolRegistry()
        register_quarantine_activation_apply_tools(
            registry,
            store=CapabilityStore(data_dir="/tmp/fake"),
            index=CapabilityIndex("/tmp/fake_idx.sqlite"),
            evaluator=_make_evaluator(),
            policy=_make_policy(),
        )
        spec = registry.tools["apply_quarantine_activation"]
        assert spec.capability == "capability_import_operator"
        # Not available to standard/default profiles
        assert spec.capability not in ("standard", "default", "chat", "local_execution")


# ── Tool executor tests ───────────────────────────────────────────────


class TestApplyActivationToolExecutor:
    """Tool executor behavior: dry run, success, errors."""

    async def _run_tool(self, store, index, evaluator, policy, **args):
        registry = _FakeToolRegistry()
        register_quarantine_activation_apply_tools(
            registry, store=store, index=index, evaluator=evaluator, policy=policy,
        )
        spec = registry.tools["apply_quarantine_activation"]
        req = ToolExecutionRequest(
            name="apply_quarantine_activation",
            arguments=args,
        )
        ctx = ToolExecutionContext(
            execute_shell=lambda cmd: None,
            shell_default_cwd="/tmp",
            adapter="test",
            runtime_profile="capability_import_operator",
        )
        return await spec.executor(req, ctx)

    @pytest.mark.asyncio
    async def test_dry_run_writes_nothing(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _setup_quarantine_cap(store, "test-tool-dry")

        result = await self._run_tool(
            store, idx, _make_evaluator(), _make_policy(),
            capability_id="test-tool-dry",
            reason="Test dry run",
            dry_run=True,
        )

        assert result.success is True
        payload = result.payload
        assert payload.get("applied") is False
        assert payload.get("dry_run") is True
        target_dir = store.data_dir / "user" / "test-tool-dry"
        assert not target_dir.exists()

    @pytest.mark.asyncio
    async def test_successful_tool_call_applies_testing_copy(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _setup_quarantine_cap(store, "test-tool-ok")

        result = await self._run_tool(
            store, idx, _make_evaluator(), _make_policy(),
            capability_id="test-tool-ok",
            reason="Testing activation via tool",
        )

        assert result.success is True
        payload = result.payload
        assert payload.get("applied") is True
        assert payload.get("target_maturity") == "testing"
        assert payload.get("target_status") == "active"

        target_dir = store.data_dir / "user" / "test-tool-ok"
        assert target_dir.is_dir()
        target_manifest = json.loads(
            (target_dir / "manifest.json").read_text(encoding="utf-8")
        )
        assert target_manifest["status"] == "active"
        assert target_manifest["maturity"] == "testing"

    @pytest.mark.asyncio
    async def test_missing_capability_id_returns_error(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)

        result = await self._run_tool(
            store, idx, _make_evaluator(), _make_policy(),
            capability_id="",
            reason="test",
        )

        assert result.success is False
        assert "capability_id" in result.payload.get("error", "")

    @pytest.mark.asyncio
    async def test_missing_reason_returns_error(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _setup_quarantine_cap(store, "test-tool-noreason")

        result = await self._run_tool(
            store, idx, _make_evaluator(), _make_policy(),
            capability_id="test-tool-noreason",
            reason="",
        )

        assert result.success is False
        assert "reason" in result.payload.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_blocked_apply_returns_clean_error(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _setup_quarantine_cap(store, "test-tool-blocked")
        # Delete the plan to cause blocking
        plans_dir = (
            store.data_dir / "quarantine" / "test-tool-blocked"
            / "quarantine_activation_plans"
        )
        for f in plans_dir.glob("*.json"):
            f.unlink()

        result = await self._run_tool(
            store, idx, _make_evaluator(), _make_policy(),
            capability_id="test-tool-blocked",
            reason="Should be blocked",
        )

        assert result.success is True
        payload = result.payload
        assert payload.get("applied") is False
        assert len(payload.get("blocking_findings", [])) > 0

    @pytest.mark.asyncio
    async def test_no_forbidden_tools_exist(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        registry = _FakeToolRegistry()
        register_quarantine_activation_apply_tools(
            registry, store=store, index=idx,
            evaluator=_make_evaluator(), policy=_make_policy(),
        )
        names = set(registry.tools.keys())
        forbidden = {
            "activate_quarantined_capability",
            "promote_quarantined_capability",
            "run_quarantined_capability",
            "run_capability",
            "execute_capability",
            "apply_quarantine_transition",
        }
        assert names.isdisjoint(forbidden)

    @pytest.mark.asyncio
    async def test_error_does_not_emit_stack_trace(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)

        result = await self._run_tool(
            store, idx, _make_evaluator(), _make_policy(),
            capability_id="../../../etc/passwd",
            reason="path traversal test",
        )

        assert result.success is False
        payload_str = json.dumps(result.payload)
        assert "Traceback" not in payload_str
        assert "File " not in payload_str or '"File' not in payload_str
