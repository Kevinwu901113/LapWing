"""Phase 7D-A tool tests: registration, feature-gating, permissions, tool surface audit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from src.capabilities.evaluator import CapabilityEvaluator
from src.capabilities.policy import CapabilityPolicy
from src.capabilities.store import CapabilityStore
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


# ── helpers ────────────────────────────────────────────────────────────


class _MockToolRegistry:
    """Minimal tool registry for testing registration."""

    def __init__(self):
        self.tools: list = []

    def register(self, spec):
        self.tools.append(spec)

    def get(self, name: str):
        for t in self.tools:
            if t.name == name:
                return t
        return None

    def list_names(self):
        return [t.name for t in self.tools]


class _MockCapabilityStore:
    """Minimal store for tool executor testing."""

    def __init__(self, tmp_path: Path):
        self.data_dir = tmp_path / "capabilities"
        self.data_dir.mkdir(parents=True, exist_ok=True)


def _create_quarantine_capability(store_data_dir: Path, cap_id: str):
    qdir = store_data_dir / "quarantine" / cap_id
    qdir.mkdir(parents=True, exist_ok=True)

    fm = {
        "id": cap_id,
        "name": f"Test {cap_id}",
        "description": "Quarantined test package.",
        "type": "skill",
        "scope": "user",
        "version": "0.1.0",
        "maturity": "draft",
        "status": "quarantined",
        "risk_level": "low",
        "triggers": [],
        "tags": [],
        "trust_required": "developer",
        "required_tools": [],
        "required_permissions": [],
    }
    fm_yaml = yaml.dump(fm, allow_unicode=True, sort_keys=False).strip()
    md = (
        f"---\n{fm_yaml}\n---\n\n"
        "## When to use\nTest.\n\n"
        "## Procedure\n1. Test\n\n"
        "## Verification\nPass.\n\n"
        "## Failure handling\nRetry."
    )
    (qdir / "CAPABILITY.md").write_text(md, encoding="utf-8")
    (qdir / "manifest.json").write_text(json.dumps(fm, indent=2), encoding="utf-8")

    import_report = {
        "capability_id": cap_id,
        "source_type": "local_package",
        "imported_at": "2026-05-01T00:00:00+00:00",
        "target_scope": "user",
        "eval_passed": True,
        "eval_score": 1.0,
        "risk_level": "low",
        "quarantine_reason": "Test",
    }
    (qdir / "import_report.json").write_text(
        json.dumps(import_report, indent=2), encoding="utf-8",
    )

    rev_dir = qdir / "quarantine_reviews"
    rev_dir.mkdir(parents=True, exist_ok=True)
    review = {
        "capability_id": cap_id,
        "review_id": "review_test001",
        "review_status": "approved_for_testing",
        "created_at": "2026-05-02T10:00:00+00:00",
    }
    (rev_dir / "review_test001.json").write_text(
        json.dumps(review, indent=2), encoding="utf-8",
    )

    aud_dir = qdir / "quarantine_audit_reports"
    aud_dir.mkdir(parents=True, exist_ok=True)
    audit = {
        "capability_id": cap_id,
        "audit_id": "audit_test001",
        "created_at": "2026-05-02T09:00:00+00:00",
        "passed": True,
        "risk_level": "low",
        "findings": [],
        "recommended_review_status": "approved_for_testing",
        "remediation_suggestions": [],
    }
    (aud_dir / "audit_test001.json").write_text(
        json.dumps(audit, indent=2), encoding="utf-8",
    )

    req_dir = qdir / "quarantine_transition_requests"
    req_dir.mkdir(parents=True, exist_ok=True)
    req = {
        "request_id": "qtr_test001",
        "capability_id": cap_id,
        "created_at": "2026-05-03T00:00:00+00:00",
        "requested_target_scope": "user",
        "requested_target_maturity": "testing",
        "status": "pending",
        "reason": "Ready",
        "risk_level": "low",
        "required_approval": False,
        "source_review_id": "review_test001",
        "source_audit_id": "audit_test001",
    }
    (req_dir / "qtr_test001.json").write_text(
        json.dumps(req, indent=2), encoding="utf-8",
    )


# ── Tool registration tests ──────────────────────────────────────────────


class TestPlanActivationToolRegistration:
    """Tool absent by default, registered only when flag enabled."""

    def test_tool_absent_when_not_registered(self):
        registry = _MockToolRegistry()
        names = registry.list_names()
        assert "plan_quarantine_activation" not in names

    def test_tool_registered_only_when_explicitly_called(self):
        registry = _MockToolRegistry()
        from src.tools.capability_tools import register_quarantine_activation_planning_tools
        store = CapabilityStore(data_dir=Path("/tmp/test-reg"))
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        register_quarantine_activation_planning_tools(
            registry, store, evaluator, policy,
        )

        tool = registry.get("plan_quarantine_activation")
        assert tool is not None
        assert tool.name == "plan_quarantine_activation"

    def test_tool_has_capability_import_operator_tag(self):
        registry = _MockToolRegistry()
        from src.tools.capability_tools import register_quarantine_activation_planning_tools
        store = CapabilityStore(data_dir=Path("/tmp/test-tag"))
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        register_quarantine_activation_planning_tools(
            registry, store, evaluator, policy,
        )

        tool = registry.get("plan_quarantine_activation")
        assert tool.capability == "capability_import_operator"

    def test_tool_is_low_risk(self):
        registry = _MockToolRegistry()
        from src.tools.capability_tools import register_quarantine_activation_planning_tools
        store = CapabilityStore(data_dir=Path("/tmp/test-risk"))
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        register_quarantine_activation_planning_tools(
            registry, store, evaluator, policy,
        )

        tool = registry.get("plan_quarantine_activation")
        assert tool.risk_level == "low"

    def test_skip_when_store_is_none(self):
        registry = _MockToolRegistry()
        from src.tools.capability_tools import register_quarantine_activation_planning_tools
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        register_quarantine_activation_planning_tools(
            registry, None, evaluator, policy,
        )

        assert registry.get("plan_quarantine_activation") is None

    def test_skip_when_evaluator_is_none(self):
        registry = _MockToolRegistry()
        from src.tools.capability_tools import register_quarantine_activation_planning_tools
        store = CapabilityStore(data_dir=Path("/tmp/test-ev"))
        policy = CapabilityPolicy()

        register_quarantine_activation_planning_tools(
            registry, store, None, policy,
        )

        assert registry.get("plan_quarantine_activation") is None

    def test_skip_when_policy_is_none(self):
        registry = _MockToolRegistry()
        from src.tools.capability_tools import register_quarantine_activation_planning_tools
        store = CapabilityStore(data_dir=Path("/tmp/test-pol"))
        evaluator = CapabilityEvaluator()

        register_quarantine_activation_planning_tools(
            registry, store, evaluator, None,
        )

        assert registry.get("plan_quarantine_activation") is None


# ── Tool surface audit tests ─────────────────────────────────────────────


class TestToolSurfaceAudit:
    """No apply/activate/promote/run tools exist."""

    def test_no_apply_tool_exists(self, tmp_path):
        store = _MockCapabilityStore(tmp_path)
        _create_quarantine_capability(store.data_dir, "test-tool-surf")
        registry = _MockToolRegistry()

        from src.tools.capability_tools import register_quarantine_activation_planning_tools
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        register_quarantine_activation_planning_tools(
            registry, CapabilityStore(data_dir=store.data_dir), evaluator, policy,
        )

        names = registry.list_names()
        assert "apply_quarantine_activation" not in names
        assert "activate_quarantined_capability" not in names
        assert "promote_quarantined_capability" not in names
        assert "run_quarantined_capability" not in names
        assert "run_capability" not in names

    def test_only_plan_tool_registered(self, tmp_path):
        store = _MockCapabilityStore(tmp_path)
        _create_quarantine_capability(store.data_dir, "test-only-plan")
        registry = _MockToolRegistry()

        from src.tools.capability_tools import register_quarantine_activation_planning_tools
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        register_quarantine_activation_planning_tools(
            registry, CapabilityStore(data_dir=store.data_dir), evaluator, policy,
        )

        names = registry.list_names()
        assert len(names) == 1
        assert names[0] == "plan_quarantine_activation"


# ── Tool executor tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_executor_success(tmp_path):
    store = _MockCapabilityStore(tmp_path)
    _create_quarantine_capability(store.data_dir, "test-exec-ok")
    from src.tools.capability_tools import _make_plan_activation_executor
    evaluator = CapabilityEvaluator()
    policy = CapabilityPolicy()
    cap_store = CapabilityStore(data_dir=store.data_dir)

    executor_fn = _make_plan_activation_executor(cap_store, evaluator, policy)
    req = ToolExecutionRequest(
        name="plan_quarantine_activation",
        arguments={"capability_id": "test-exec-ok"},
    )
    ctx = ToolExecutionContext(
        execute_shell=None,
        shell_default_cwd="/tmp",
    )

    result = await executor_fn(req, ctx)
    assert result.success is True
    assert "plan" in result.payload
    assert result.payload["would_activate"] is False


@pytest.mark.asyncio
async def test_tool_executor_missing_capability_id(tmp_path):
    store = _MockCapabilityStore(tmp_path)
    from src.tools.capability_tools import _make_plan_activation_executor
    evaluator = CapabilityEvaluator()
    policy = CapabilityPolicy()
    cap_store = CapabilityStore(data_dir=store.data_dir)

    executor_fn = _make_plan_activation_executor(cap_store, evaluator, policy)
    req = ToolExecutionRequest(
        name="plan_quarantine_activation",
        arguments={},
    )
    ctx = ToolExecutionContext(
        execute_shell=None,
        shell_default_cwd="/tmp",
    )

    result = await executor_fn(req, ctx)
    assert result.success is False
    assert "capability_id" in result.reason


@pytest.mark.asyncio
async def test_tool_executor_dry_run(tmp_path):
    store = _MockCapabilityStore(tmp_path)
    _create_quarantine_capability(store.data_dir, "test-exec-dry")
    from src.tools.capability_tools import _make_plan_activation_executor
    evaluator = CapabilityEvaluator()
    policy = CapabilityPolicy()
    cap_store = CapabilityStore(data_dir=store.data_dir)

    executor_fn = _make_plan_activation_executor(cap_store, evaluator, policy)
    req = ToolExecutionRequest(
        name="plan_quarantine_activation",
        arguments={"capability_id": "test-exec-dry", "dry_run": True},
    )
    ctx = ToolExecutionContext(
        execute_shell=None,
        shell_default_cwd="/tmp",
    )

    result = await executor_fn(req, ctx)
    assert result.success is True
    assert result.payload["would_activate"] is False


@pytest.mark.asyncio
async def test_tool_executor_persist_writes_plan(tmp_path):
    store = _MockCapabilityStore(tmp_path)
    _create_quarantine_capability(store.data_dir, "test-exec-persist")
    from src.tools.capability_tools import _make_plan_activation_executor
    evaluator = CapabilityEvaluator()
    policy = CapabilityPolicy()
    cap_store = CapabilityStore(data_dir=store.data_dir)

    executor_fn = _make_plan_activation_executor(cap_store, evaluator, policy)
    req = ToolExecutionRequest(
        name="plan_quarantine_activation",
        arguments={"capability_id": "test-exec-persist", "persist_plan": True},
    )
    ctx = ToolExecutionContext(
        execute_shell=None,
        shell_default_cwd="/tmp",
    )

    result = await executor_fn(req, ctx)
    assert result.success is True

    plan_id = result.payload["plan"]["plan_id"]
    plan_path = (
        store.data_dir / "quarantine" / "test-exec-persist"
        / "quarantine_activation_plans" / f"{plan_id}.json"
    )
    assert plan_path.is_file()
