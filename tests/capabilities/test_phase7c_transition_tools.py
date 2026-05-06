"""Phase 7C tools tests: registration, permission gating, feature flag behaviour."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from src.capabilities.evaluator import CapabilityEvaluator
from src.capabilities.policy import CapabilityPolicy
from src.capabilities.store import CapabilityStore
from src.tools.capability_tools import (
    register_quarantine_review_tools,
    register_quarantine_transition_tools,
)
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


class _FakeRegistry:
    def __init__(self):
        self._t: dict[str, object] = {}

    def register(self, spec):
        self._t[spec.name] = spec

    def get(self, name: str):
        return self._t.get(name)

    @property
    def names(self) -> list[str]:
        return sorted(self._t.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._t


def _make_store(tmp_path: Path) -> CapabilityStore:
    return CapabilityStore(data_dir=tmp_path / "capabilities")


def _ctx():
    return ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp")


def _req(args: dict) -> ToolExecutionRequest:
    return ToolExecutionRequest(name="test", arguments=args)


def _create_quarantine_dir(
    store: CapabilityStore, cap_id: str,
    *,
    review_status: str = "approved_for_testing",
    risk_level: str = "low",
    status: str = "quarantined",
    maturity: str = "draft",
) -> Path:
    qroot = store.data_dir / "quarantine"
    qroot.mkdir(parents=True, exist_ok=True)
    qdir = qroot / cap_id
    qdir.mkdir(parents=True, exist_ok=True)

    fm = {
        "id": cap_id, "name": f"Test {cap_id}",
        "description": "Quarantined test package.",
        "type": "skill", "scope": "user", "version": "0.1.0",
        "maturity": maturity, "status": status, "risk_level": risk_level,
        "triggers": [], "tags": [], "trust_required": "developer",
        "required_tools": [], "required_permissions": [],
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
    (qdir / "manifest.json").write_text(json.dumps({
        k: v for k, v in fm.items() if k not in ("version",)
    }, indent=2), encoding="utf-8")

    import_report = {
        "capability_id": cap_id, "source_type": "local_package",
        "source_path_hash": "abc", "imported_at": "2026-05-01T00:00:00+00:00",
        "original_content_hash": "hash", "target_scope": "user",
        "eval_passed": True, "eval_score": 1.0,
        "eval_findings": [], "policy_findings": [],
        "files_summary": {"scripts": [], "tests": [], "examples": []},
        "name": f"Test {cap_id}", "type": "skill",
        "risk_level": risk_level, "quarantine_reason": "Test",
    }
    (qdir / "import_report.json").write_text(
        json.dumps(import_report, indent=2), encoding="utf-8")

    rev_dir = qdir / "quarantine_reviews"
    rev_dir.mkdir(parents=True, exist_ok=True)
    (rev_dir / "review_t001.json").write_text(json.dumps({
        "capability_id": cap_id, "review_id": "review_t001",
        "review_status": review_status, "reviewer": "tester",
        "reason": "OK", "created_at": "2026-05-02T10:00:00+00:00",
    }, indent=2), encoding="utf-8")

    aud_dir = qdir / "quarantine_audit_reports"
    aud_dir.mkdir(parents=True, exist_ok=True)
    (aud_dir / "audit_t001.json").write_text(json.dumps({
        "capability_id": cap_id, "audit_id": "audit_t001",
        "created_at": "2026-05-02T09:00:00+00:00", "passed": True,
        "risk_level": risk_level, "findings": [],
        "recommended_review_status": "approved_for_testing",
        "remediation_suggestions": [],
    }, indent=2), encoding="utf-8")

    return qdir


# ── Registration tests ─────────────────────────────────────────────────


class TestToolRegistration:

    def test_transition_tools_not_registered_by_default(self):
        reg = _FakeRegistry()
        register_quarantine_review_tools(
            reg, _make_store(Path("/tmp")),
            CapabilityEvaluator(), CapabilityPolicy(),
        )
        assert "request_quarantine_testing_transition" not in reg
        assert "list_quarantine_transition_requests" not in reg
        assert "view_quarantine_transition_request" not in reg
        assert "cancel_quarantine_transition_request" not in reg

    def test_transition_tools_registered_explicitly(self, tmp_path):
        store = _make_store(tmp_path)
        reg = _FakeRegistry()
        register_quarantine_transition_tools(
            reg, store, CapabilityEvaluator(), CapabilityPolicy())
        assert "request_quarantine_testing_transition" in reg
        assert "list_quarantine_transition_requests" in reg
        assert "view_quarantine_transition_request" in reg
        assert "cancel_quarantine_transition_request" in reg

    def test_all_transition_tools_operator_tagged(self, tmp_path):
        store = _make_store(tmp_path)
        reg = _FakeRegistry()
        register_quarantine_transition_tools(
            reg, store, CapabilityEvaluator(), CapabilityPolicy())
        for name in (
            "request_quarantine_testing_transition",
            "list_quarantine_transition_requests",
            "view_quarantine_transition_request",
            "cancel_quarantine_transition_request",
        ):
            spec = reg.get(name)
            assert spec is not None, f"Missing tool: {name}"
            assert spec.capability == "capability_import_operator", (
                f"{name} tag: {spec.capability}")

    def test_no_activate_promote_apply_run_tools(self, tmp_path):
        store = _make_store(tmp_path)
        reg = _FakeRegistry()
        register_quarantine_transition_tools(
            reg, store, CapabilityEvaluator(), CapabilityPolicy())
        for name in (
            "activate_quarantined_capability", "promote_quarantined_capability",
            "apply_quarantine_transition", "run_quarantined_capability",
            "run_capability", "install_capability",
        ):
            assert name not in reg, f"Forbidden tool registered: {name}"

    def test_registration_skips_with_none_store(self):
        reg = _FakeRegistry()
        register_quarantine_transition_tools(
            reg, None, CapabilityEvaluator(), CapabilityPolicy())
        assert "request_quarantine_testing_transition" not in reg

    def test_registration_skips_with_none_evaluator(self, tmp_path):
        store = _make_store(tmp_path)
        reg = _FakeRegistry()
        register_quarantine_transition_tools(reg, store, None, CapabilityPolicy())
        assert "request_quarantine_testing_transition" not in reg

    def test_registration_skips_with_none_policy(self, tmp_path):
        store = _make_store(tmp_path)
        reg = _FakeRegistry()
        register_quarantine_transition_tools(reg, store, CapabilityEvaluator(), None)
        assert "request_quarantine_testing_transition" not in reg


# ── Tool execution tests ───────────────────────────────────────────────


class TestRequestTransitionTool:
    async def test_dry_run_writes_nothing(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "dry-cap")
        reg = _FakeRegistry()
        register_quarantine_transition_tools(
            reg, store, CapabilityEvaluator(), CapabilityPolicy())

        spec = reg.get("request_quarantine_testing_transition")
        result = await spec.executor(_req({
            "capability_id": "dry-cap", "reason": "Test dry run", "dry_run": True,
        }), _ctx())
        assert result.success is True
        assert result.payload["would_create"] is True

    async def test_request_writes_only_request_json(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "write-cap")
        reg = _FakeRegistry()
        register_quarantine_transition_tools(
            reg, store, CapabilityEvaluator(), CapabilityPolicy())

        spec = reg.get("request_quarantine_testing_transition")
        result = await spec.executor(_req({
            "capability_id": "write-cap", "reason": "Write only request",
        }), _ctx())
        assert result.success is True
        assert "request" in result.payload

        manifest = json.loads(
            (store.data_dir / "quarantine" / "write-cap" / "manifest.json").read_text())
        assert manifest["status"] == "quarantined"
        assert manifest["maturity"] == "draft"

    async def test_missing_capability_id_returns_error(self, tmp_path):
        store = _make_store(tmp_path)
        reg = _FakeRegistry()
        register_quarantine_transition_tools(
            reg, store, CapabilityEvaluator(), CapabilityPolicy())

        spec = reg.get("request_quarantine_testing_transition")
        result = await spec.executor(_req({"reason": "no id"}), _ctx())
        assert result.success is False

    async def test_blocked_request_returns_failure(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "blocked-cap", review_status="rejected")
        reg = _FakeRegistry()
        register_quarantine_transition_tools(
            reg, store, CapabilityEvaluator(), CapabilityPolicy())

        spec = reg.get("request_quarantine_testing_transition")
        result = await spec.executor(_req({
            "capability_id": "blocked-cap", "reason": "Should fail",
        }), _ctx())
        assert result.success is False


class TestListTransitionRequestsTool:
    async def test_list_returns_empty_for_none(self, tmp_path):
        store = _make_store(tmp_path)
        reg = _FakeRegistry()
        register_quarantine_transition_tools(
            reg, store, CapabilityEvaluator(), CapabilityPolicy())

        spec = reg.get("list_quarantine_transition_requests")
        result = await spec.executor(_req({}), _ctx())
        assert result.success is True
        assert result.payload["requests"] == []

    async def test_list_returns_requests(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "list-cap")
        reg = _FakeRegistry()
        register_quarantine_transition_tools(
            reg, store, CapabilityEvaluator(), CapabilityPolicy())

        req_spec = reg.get("request_quarantine_testing_transition")
        await req_spec.executor(_req({
            "capability_id": "list-cap", "reason": "For listing",
        }), _ctx())

        list_spec = reg.get("list_quarantine_transition_requests")
        result = await list_spec.executor(_req({}), _ctx())
        assert result.success is True
        assert len(result.payload["requests"]) >= 1


class TestViewTransitionRequestTool:
    async def test_view_returns_details(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "v-cap")
        reg = _FakeRegistry()
        register_quarantine_transition_tools(
            reg, store, CapabilityEvaluator(), CapabilityPolicy())

        req_spec = reg.get("request_quarantine_testing_transition")
        create_res = await req_spec.executor(_req({
            "capability_id": "v-cap", "reason": "To view",
        }), _ctx())
        req_id = create_res.payload["request"]["request_id"]

        view_spec = reg.get("view_quarantine_transition_request")
        result = await view_spec.executor(_req({
            "capability_id": "v-cap", "request_id": req_id,
        }), _ctx())
        assert result.success is True
        assert result.payload["request_id"] == req_id

    async def test_view_missing_request_returns_error(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "v2-cap")
        reg = _FakeRegistry()
        register_quarantine_transition_tools(
            reg, store, CapabilityEvaluator(), CapabilityPolicy())

        view_spec = reg.get("view_quarantine_transition_request")
        result = await view_spec.executor(_req({
            "capability_id": "v2-cap", "request_id": "nonexistent",
        }), _ctx())
        assert result.success is False


class TestCancelTransitionRequestTool:
    async def test_cancel_changes_status_only(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "c-cap")
        reg = _FakeRegistry()
        register_quarantine_transition_tools(
            reg, store, CapabilityEvaluator(), CapabilityPolicy())

        req_spec = reg.get("request_quarantine_testing_transition")
        create_res = await req_spec.executor(_req({
            "capability_id": "c-cap", "reason": "To cancel",
        }), _ctx())
        req_id = create_res.payload["request"]["request_id"]

        cancel_spec = reg.get("cancel_quarantine_transition_request")
        result = await cancel_spec.executor(_req({
            "capability_id": "c-cap", "request_id": req_id,
            "reason": "Cancelled by test",
        }), _ctx())
        assert result.success is True
        assert result.payload["status"] == "cancelled"

        manifest = json.loads(
            (store.data_dir / "quarantine" / "c-cap" / "manifest.json").read_text())
        assert manifest["status"] == "quarantined"

    async def test_cancel_missing_request_returns_error(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "c2-cap")
        reg = _FakeRegistry()
        register_quarantine_transition_tools(
            reg, store, CapabilityEvaluator(), CapabilityPolicy())

        cancel_spec = reg.get("cancel_quarantine_transition_request")
        result = await cancel_spec.executor(_req({
            "capability_id": "c2-cap", "request_id": "nonexistent", "reason": "Test",
        }), _ctx())
        assert result.success is False
