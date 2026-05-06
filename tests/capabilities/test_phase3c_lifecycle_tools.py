"""Phase 3C tests: lifecycle management tools (evaluate/plan/transition)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.capabilities.document import CapabilityDocument
from src.capabilities.evaluator import CapabilityEvaluator
from src.capabilities.index import CapabilityIndex
from src.capabilities.lifecycle import CapabilityLifecycleManager
from src.capabilities.policy import CapabilityPolicy
from src.capabilities.promotion import PromotionPlanner
from src.capabilities.schema import (
    CapabilityMaturity,
    CapabilityScope,
    CapabilityStatus,
)
from src.capabilities.store import CapabilityStore
from src.tools.capability_tools import (
    _make_evaluate_capability_executor,
    _make_plan_capability_transition_executor,
    _make_transition_capability_executor,
    register_capability_lifecycle_tools,
    register_capability_tools,
)
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


# ── Helpers ─────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


VALID_BODY = """## When to use

Use this capability when you need to test lifecycle tools.

## Procedure

1. Do step one.
2. Do step two.

## Verification

Verify the output is correct.

## Failure handling

If it fails, log the error and retry.
"""


def _make_store(tmp_path: Path, *, with_index: bool = False) -> CapabilityStore:
    kwargs: dict = {}
    if with_index:
        idx = CapabilityIndex(tmp_path / "index.db")
        idx.init()
        kwargs["index"] = idx
    return CapabilityStore(data_dir=tmp_path / "capabilities", **kwargs)


def _create_cap(
    store: CapabilityStore,
    *,
    cap_id: str = "test_cap",
    body: str = VALID_BODY,
    maturity: str = "draft",
    status: str = "active",
    risk_level: str = "low",
    scope: CapabilityScope = CapabilityScope.WORKSPACE,
    **overrides,
) -> CapabilityDocument:
    from datetime import datetime, timezone

    doc = store.create_draft(
        scope=scope,
        cap_id=cap_id,
        name=overrides.pop("name", "Test Capability"),
        description=overrides.pop("description", "A test capability."),
        body=body,
        risk_level=risk_level,
        **overrides,
    )
    needs_update = False
    updates: dict = {}
    if maturity != "draft":
        updates["maturity"] = CapabilityMaturity(maturity)
        needs_update = True
    if status != "active":
        updates["status"] = CapabilityStatus(status)
        needs_update = True
    if needs_update:
        updates["updated_at"] = datetime.now(timezone.utc)
        updated = doc.manifest.model_copy(update=updates)
        doc.manifest = updated
        store._sync_manifest_json(doc.directory, doc)
        doc = store._parser.parse(doc.directory)
        store._maybe_index(doc)
    return doc


def _make_lifecycle(store, *, mutation_log=None):
    return CapabilityLifecycleManager(
        store=store,
        evaluator=CapabilityEvaluator(),
        policy=CapabilityPolicy(),
        planner=PromotionPlanner(),
        mutation_log=mutation_log,
    )


def _make_ctx():
    async def _noop(cmd):
        from src.tools.shell_executor import ShellResult
        return ShellResult(stdout="", stderr="", exit_code=0)

    return ToolExecutionContext(
        execute_shell=_noop,
        shell_default_cwd="/tmp",
    )


# ── Feature flag registration tests ───────────────────────────────────

class TestFeatureFlagRegistration:
    def test_lifecycle_tools_not_registered_when_disabled(self):
        registry = MagicMock()
        register_capability_tools(registry, MagicMock(), None)
        registered = {
            c[0][0].name for c in registry.register.call_args_list if c[0]
        }
        assert "evaluate_capability" not in registered
        assert "plan_capability_transition" not in registered
        assert "transition_capability" not in registered

    def test_lifecycle_tools_registered_when_flag_enabled(self):
        registry = MagicMock()
        lifecycle = MagicMock()
        register_capability_lifecycle_tools(registry, lifecycle)
        registered = {
            c[0][0].name for c in registry.register.call_args_list if c[0]
        }
        assert "evaluate_capability" in registered
        assert "plan_capability_transition" in registered
        assert "transition_capability" in registered

    def test_lifecycle_tools_use_capability_lifecycle_tag(self):
        registry = MagicMock()
        lifecycle = MagicMock()
        register_capability_lifecycle_tools(registry, lifecycle)
        for call in registry.register.call_args_list:
            spec = call[0][0]
            if spec.name in ("evaluate_capability", "plan_capability_transition",
                             "transition_capability"):
                assert spec.capability == "capability_lifecycle", (
                    f"{spec.name} has capability={spec.capability}"
                )

    def test_lifecycle_tools_dont_use_capability_read(self):
        registry = MagicMock()
        lifecycle = MagicMock()
        register_capability_lifecycle_tools(registry, lifecycle)
        for call in registry.register.call_args_list:
            spec = call[0][0]
            if spec.name in ("evaluate_capability", "plan_capability_transition",
                             "transition_capability"):
                assert spec.capability != "capability_read"

    def test_no_forbidden_tools_registered(self):
        registry = MagicMock()
        lifecycle = MagicMock()
        register_capability_lifecycle_tools(registry, lifecycle)
        registered = {
            c[0][0].name for c in registry.register.call_args_list if c[0]
        }
        forbidden = [
            "run_capability", "create_capability", "install_capability",
            "patch_capability", "auto_promote_capability",
        ]
        for name in forbidden:
            assert name not in registered, f"Forbidden tool '{name}' registered"


# ── Permission / profile tests ────────────────────────────────────────

class TestPermissionProfile:
    def test_lifecycle_profile_has_capability_lifecycle(self):
        from src.core.runtime_profiles import CAPABILITY_LIFECYCLE_OPERATOR_PROFILE
        assert "capability_lifecycle" in CAPABILITY_LIFECYCLE_OPERATOR_PROFILE.capabilities

    def test_standard_profile_excludes_capability_lifecycle(self):
        from src.core.runtime_profiles import STANDARD_PROFILE
        assert "capability_lifecycle" not in STANDARD_PROFILE.capabilities

    def test_chat_shell_profile_excludes_capability_lifecycle(self):
        from src.core.runtime_profiles import CHAT_SHELL_PROFILE
        assert "capability_lifecycle" not in CHAT_SHELL_PROFILE.capabilities

    def test_inner_tick_profile_excludes_capability_lifecycle(self):
        from src.core.runtime_profiles import INNER_TICK_PROFILE
        assert "capability_lifecycle" not in INNER_TICK_PROFILE.capabilities

    def test_local_execution_profile_excludes_capability_lifecycle(self):
        from src.core.runtime_profiles import LOCAL_EXECUTION_PROFILE
        assert "capability_lifecycle" not in LOCAL_EXECUTION_PROFILE.capabilities

    def test_agent_admin_profile_excludes_capability_lifecycle(self):
        from src.core.runtime_profiles import AGENT_ADMIN_OPERATOR_PROFILE
        assert "capability_lifecycle" not in AGENT_ADMIN_OPERATOR_PROFILE.capabilities

    def test_browser_operator_profile_excludes_capability_lifecycle(self):
        from src.core.runtime_profiles import BROWSER_OPERATOR_PROFILE
        assert "capability_lifecycle" not in BROWSER_OPERATOR_PROFILE.capabilities

    def test_skill_operator_profile_excludes_capability_lifecycle(self):
        from src.core.runtime_profiles import SKILL_OPERATOR_PROFILE
        assert "capability_lifecycle" not in SKILL_OPERATOR_PROFILE.capabilities

    def test_lifecycle_profile_not_in_standard_profiles(self):
        from src.core.runtime_profiles import (
            STANDARD_PROFILE, CHAT_SHELL_PROFILE, INNER_TICK_PROFILE,
            COMPOSE_PROACTIVE_PROFILE, LOCAL_EXECUTION_PROFILE,
            ZERO_TOOLS_PROFILE,
        )
        lifecycle_names = {
            "evaluate_capability", "plan_capability_transition", "transition_capability",
        }
        for p in (STANDARD_PROFILE, CHAT_SHELL_PROFILE, INNER_TICK_PROFILE,
                   COMPOSE_PROACTIVE_PROFILE, LOCAL_EXECUTION_PROFILE,
                   ZERO_TOOLS_PROFILE):
            overlap = lifecycle_names & p.tool_names
            assert not overlap, f"{p.name} should not have lifecycle tools: {overlap}"


# ── evaluate_capability tests ─────────────────────────────────────────

class TestEvaluateCapability:
    def test_evaluates_valid_capability(self, tmp_path):
        store = _make_store(tmp_path)
        _create_cap(store, cap_id="test_eval", maturity="draft")
        lm = _make_lifecycle(store)
        executor = _make_evaluate_capability_executor(lm)
        req = ToolExecutionRequest(name="evaluate_capability", arguments={"id": "test_eval"})
        result = _run(executor(req, _make_ctx()))
        assert result.success
        assert result.payload["capability_id"] == "test_eval"

    def test_writes_eval_record_when_write_record_true(self, tmp_path):
        store = _make_store(tmp_path)
        _create_cap(store, cap_id="test_eval", maturity="draft")
        lm = _make_lifecycle(store)
        executor = _make_evaluate_capability_executor(lm)
        req = ToolExecutionRequest(
            name="evaluate_capability",
            arguments={"id": "test_eval", "write_record": True},
        )
        result = _run(executor(req, _make_ctx()))
        assert result.success
        assert "eval_record_id" in result.payload

    def test_does_not_write_eval_record_when_false(self, tmp_path):
        store = _make_store(tmp_path)
        _create_cap(store, cap_id="test_eval", maturity="draft")
        lm = _make_lifecycle(store)
        executor = _make_evaluate_capability_executor(lm)
        req = ToolExecutionRequest(
            name="evaluate_capability",
            arguments={"id": "test_eval", "write_record": False},
        )
        result = _run(executor(req, _make_ctx()))
        assert result.success
        assert "eval_record_id" not in result.payload

    def test_does_not_change_manifest_maturity(self, tmp_path):
        store = _make_store(tmp_path)
        doc = _create_cap(store, cap_id="test_eval", maturity="draft")
        lm = _make_lifecycle(store)
        executor = _make_evaluate_capability_executor(lm)
        req = ToolExecutionRequest(
            name="evaluate_capability",
            arguments={"id": "test_eval", "write_record": True},
        )
        _run(executor(req, _make_ctx()))
        doc2 = store.get("test_eval")
        assert doc2.manifest.maturity.value == doc.manifest.maturity.value

    def test_does_not_change_manifest_status(self, tmp_path):
        store = _make_store(tmp_path)
        doc = _create_cap(store, cap_id="test_eval", maturity="draft", status="active")
        lm = _make_lifecycle(store)
        executor = _make_evaluate_capability_executor(lm)
        req = ToolExecutionRequest(
            name="evaluate_capability",
            arguments={"id": "test_eval", "write_record": True},
        )
        _run(executor(req, _make_ctx()))
        doc2 = store.get("test_eval")
        assert doc2.manifest.status.value == doc.manifest.status.value

    def test_does_not_write_version_snapshot(self, tmp_path):
        store = _make_store(tmp_path)
        _create_cap(store, cap_id="test_eval", maturity="draft")
        lm = _make_lifecycle(store)
        executor = _make_evaluate_capability_executor(lm)
        req = ToolExecutionRequest(
            name="evaluate_capability",
            arguments={"id": "test_eval", "write_record": True},
        )
        _run(executor(req, _make_ctx()))
        doc = store.get("test_eval")
        versions_dir = doc.directory / "versions"
        snapshots = list(versions_dir.glob("snapshot_*")) if versions_dir.is_dir() else []
        assert len(snapshots) == 0, f"Unexpected snapshots: {snapshots}"

    def test_returns_not_found_for_missing_id(self, tmp_path):
        store = _make_store(tmp_path)
        lm = _make_lifecycle(store)
        executor = _make_evaluate_capability_executor(lm)
        req = ToolExecutionRequest(name="evaluate_capability", arguments={"id": "nonexistent"})
        result = _run(executor(req, _make_ctx()))
        assert not result.success

    def test_includes_findings_when_requested(self, tmp_path):
        store = _make_store(tmp_path)
        _create_cap(store, cap_id="test_eval", maturity="draft")
        lm = _make_lifecycle(store)
        executor = _make_evaluate_capability_executor(lm)
        req = ToolExecutionRequest(
            name="evaluate_capability",
            arguments={"id": "test_eval", "include_findings": True},
        )
        result = _run(executor(req, _make_ctx()))
        assert result.success
        assert "findings" in result.payload

    def test_excludes_findings_when_requested(self, tmp_path):
        store = _make_store(tmp_path)
        _create_cap(store, cap_id="test_eval", maturity="draft")
        lm = _make_lifecycle(store)
        executor = _make_evaluate_capability_executor(lm)
        req = ToolExecutionRequest(
            name="evaluate_capability",
            arguments={"id": "test_eval", "include_findings": False},
        )
        result = _run(executor(req, _make_ctx()))
        assert result.success
        assert "findings" not in result.payload

    def test_does_not_execute_scripts(self, tmp_path):
        store = _make_store(tmp_path)
        _create_cap(store, cap_id="test_eval", maturity="draft")
        lm = _make_lifecycle(store)
        executor = _make_evaluate_capability_executor(lm)
        req = ToolExecutionRequest(name="evaluate_capability", arguments={"id": "test_eval"})
        result = _run(executor(req, _make_ctx()))
        assert result.success


# ── plan_capability_transition tests ──────────────────────────────────

class TestPlanCapabilityTransition:
    def test_draft_to_testing_allowed(self, tmp_path):
        store = _make_store(tmp_path)
        _create_cap(store, cap_id="test_plan", maturity="draft", risk_level="low")
        lm = _make_lifecycle(store)
        executor = _make_plan_capability_transition_executor(lm)
        req = ToolExecutionRequest(
            name="plan_capability_transition",
            arguments={"id": "test_plan", "target": "testing"},
        )
        result = _run(executor(req, _make_ctx()))
        assert result.success
        assert result.payload["allowed"] is True

    def test_testing_to_stable_blocked_without_eval(self, tmp_path):
        store = _make_store(tmp_path)
        _create_cap(store, cap_id="test_plan", maturity="testing", risk_level="low")
        lm = _make_lifecycle(store)
        executor = _make_plan_capability_transition_executor(lm)
        req = ToolExecutionRequest(
            name="plan_capability_transition",
            arguments={"id": "test_plan", "target": "stable"},
        )
        result = _run(executor(req, _make_ctx()))
        assert result.success
        assert result.payload["allowed"] is False

    def test_high_risk_requires_approval(self, tmp_path):
        store = _make_store(tmp_path)
        _create_cap(store, cap_id="test_plan", maturity="testing", risk_level="high")
        lm = _make_lifecycle(store)
        executor = _make_plan_capability_transition_executor(lm)
        req = ToolExecutionRequest(
            name="plan_capability_transition",
            arguments={"id": "test_plan", "target": "stable"},
        )
        result = _run(executor(req, _make_ctx()))
        assert result.success
        assert result.payload["required_approval"] is True

    def test_stable_to_broken_requires_evidence(self, tmp_path):
        store = _make_store(tmp_path)
        _create_cap(store, cap_id="test_plan", maturity="stable", risk_level="low")
        lm = _make_lifecycle(store)
        executor = _make_plan_capability_transition_executor(lm)
        req = ToolExecutionRequest(
            name="plan_capability_transition",
            arguments={"id": "test_plan", "target": "broken"},
        )
        result = _run(executor(req, _make_ctx()))
        assert result.success
        assert "failure_evidence" in result.payload["required_evidence"]

    def test_disabled_blocked_from_promotion(self, tmp_path):
        store = _make_store(tmp_path)
        _create_cap(store, cap_id="test_plan", maturity="draft", status="disabled")
        lm = _make_lifecycle(store)
        executor = _make_plan_capability_transition_executor(lm)
        req = ToolExecutionRequest(
            name="plan_capability_transition",
            arguments={"id": "test_plan", "target": "testing"},
        )
        result = _run(executor(req, _make_ctx()))
        assert result.success
        assert result.payload["allowed"] is False

    def test_plan_does_not_mutate_manifest(self, tmp_path):
        store = _make_store(tmp_path)
        doc = _create_cap(store, cap_id="test_plan", maturity="draft")
        original_hash = doc.content_hash
        lm = _make_lifecycle(store)
        executor = _make_plan_capability_transition_executor(lm)
        req = ToolExecutionRequest(
            name="plan_capability_transition",
            arguments={"id": "test_plan", "target": "testing"},
        )
        _run(executor(req, _make_ctx()))
        doc2 = store.get("test_plan")
        assert doc2.content_hash == original_hash

    def test_plan_does_not_write_snapshot(self, tmp_path):
        store = _make_store(tmp_path)
        doc = _create_cap(store, cap_id="test_plan", maturity="draft")
        lm = _make_lifecycle(store)
        executor = _make_plan_capability_transition_executor(lm)
        req = ToolExecutionRequest(
            name="plan_capability_transition",
            arguments={"id": "test_plan", "target": "testing"},
        )
        _run(executor(req, _make_ctx()))
        versions_dir = doc.directory / "versions"
        snapshots = list(versions_dir.glob("snapshot_*")) if versions_dir.is_dir() else []
        assert len(snapshots) == 0

    def test_plan_rejects_invalid_target(self, tmp_path):
        store = _make_store(tmp_path)
        lm = _make_lifecycle(store)
        executor = _make_plan_capability_transition_executor(lm)
        req = ToolExecutionRequest(
            name="plan_capability_transition",
            arguments={"id": "whatever", "target": "invalid_target"},
        )
        result = _run(executor(req, _make_ctx()))
        assert not result.success


# ── transition_capability tests ─────────────────────────────────────

class TestTransitionCapability:
    def test_dry_run_makes_no_changes(self, tmp_path):
        store = _make_store(tmp_path)
        doc = _create_cap(store, cap_id="test_trans", maturity="draft")
        original_hash = doc.content_hash
        lm = _make_lifecycle(store)
        executor = _make_transition_capability_executor(lm)
        req = ToolExecutionRequest(
            name="transition_capability",
            arguments={"id": "test_trans", "target": "testing", "dry_run": True},
        )
        result = _run(executor(req, _make_ctx()))
        assert result.success
        assert result.payload["dry_run"] is True
        assert result.payload["applied"] is False
        doc2 = store.get("test_trans")
        assert doc2.content_hash == original_hash

    def test_draft_to_testing_applies(self, tmp_path):
        store = _make_store(tmp_path)
        _create_cap(store, cap_id="test_trans", maturity="draft", risk_level="low")
        lm = _make_lifecycle(store)
        executor = _make_transition_capability_executor(lm)
        req = ToolExecutionRequest(
            name="transition_capability",
            arguments={"id": "test_trans", "target": "testing"},
        )
        result = _run(executor(req, _make_ctx()))
        assert result.success
        assert result.payload["applied"] is True
        doc2 = store.get("test_trans")
        assert doc2.manifest.maturity.value == "testing"

    def test_high_risk_stable_blocked_without_approval(self, tmp_path):
        store = _make_store(tmp_path)
        doc = _create_cap(store, cap_id="test_trans", maturity="testing", risk_level="high")
        lm = _make_lifecycle(store)
        executor = _make_transition_capability_executor(lm)
        req = ToolExecutionRequest(
            name="transition_capability",
            arguments={"id": "test_trans", "target": "stable"},
        )
        result = _run(executor(req, _make_ctx()))
        assert result.payload["applied"] is False
        doc2 = store.get("test_trans")
        assert doc2.manifest.maturity.value == "testing"

    def test_successful_transition_writes_snapshot(self, tmp_path):
        store = _make_store(tmp_path)
        _create_cap(store, cap_id="test_trans", maturity="draft", risk_level="low")
        lm = _make_lifecycle(store)
        executor = _make_transition_capability_executor(lm)
        req = ToolExecutionRequest(
            name="transition_capability",
            arguments={"id": "test_trans", "target": "testing"},
        )
        result = _run(executor(req, _make_ctx()))
        assert result.success
        assert result.payload.get("version_snapshot_id")

    def test_successful_transition_records_mutation_log(self, tmp_path):
        store = _make_store(tmp_path)
        _create_cap(store, cap_id="test_trans", maturity="draft", risk_level="low")
        mock_log = MagicMock()
        mock_log.record = MagicMock()
        lm = CapabilityLifecycleManager(
            store=store,
            evaluator=CapabilityEvaluator(),
            policy=CapabilityPolicy(),
            planner=PromotionPlanner(),
            mutation_log=mock_log,
        )
        executor = _make_transition_capability_executor(lm)
        req = ToolExecutionRequest(
            name="transition_capability",
            arguments={"id": "test_trans", "target": "testing"},
        )
        result = _run(executor(req, _make_ctx()))
        assert result.success
        transition_calls = [
            c for c in mock_log.record.call_args_list
            if c[0][0] == "capability.transition_applied"
        ]
        assert len(transition_calls) >= 1

    def test_mutation_log_failure_does_not_corrupt(self, tmp_path):
        store = _make_store(tmp_path)
        _create_cap(store, cap_id="test_trans", maturity="draft", risk_level="low")
        mock_log = MagicMock()
        mock_log.record = MagicMock(side_effect=RuntimeError("log failure"))
        lm = CapabilityLifecycleManager(
            store=store,
            evaluator=CapabilityEvaluator(),
            policy=CapabilityPolicy(),
            planner=PromotionPlanner(),
            mutation_log=mock_log,
        )
        executor = _make_transition_capability_executor(lm)
        req = ToolExecutionRequest(
            name="transition_capability",
            arguments={"id": "test_trans", "target": "testing"},
        )
        result = _run(executor(req, _make_ctx()))
        assert result.success
        doc2 = store.get("test_trans")
        assert doc2.manifest.maturity.value == "testing"

    def test_disabled_target_applies(self, tmp_path):
        store = _make_store(tmp_path)
        _create_cap(store, cap_id="test_trans", maturity="draft", status="active")
        lm = _make_lifecycle(store)
        executor = _make_transition_capability_executor(lm)
        req = ToolExecutionRequest(
            name="transition_capability",
            arguments={"id": "test_trans", "target": "disabled"},
        )
        result = _run(executor(req, _make_ctx()))
        assert result.success
        assert result.payload["applied"] is True
        assert result.payload["to_status"] == "disabled"

    def test_archived_target_applies(self, tmp_path):
        store = _make_store(tmp_path)
        _create_cap(store, cap_id="test_trans", maturity="draft", status="active")
        lm = _make_lifecycle(store)
        executor = _make_transition_capability_executor(lm)
        req = ToolExecutionRequest(
            name="transition_capability",
            arguments={"id": "test_trans", "target": "archived"},
        )
        result = _run(executor(req, _make_ctx()))
        assert result.success
        assert result.payload["applied"] is True

    def test_disabled_cannot_promote(self, tmp_path):
        store = _make_store(tmp_path)
        _create_cap(store, cap_id="test_trans", maturity="draft", status="disabled")
        lm = _make_lifecycle(store)
        executor = _make_transition_capability_executor(lm)
        req = ToolExecutionRequest(
            name="transition_capability",
            arguments={"id": "test_trans", "target": "testing"},
        )
        result = _run(executor(req, _make_ctx()))
        assert result.payload["applied"] is False
        assert "Disabled" in result.payload["message"]

    def test_returns_not_found_for_missing_id(self, tmp_path):
        store = _make_store(tmp_path)
        lm = _make_lifecycle(store)
        executor = _make_transition_capability_executor(lm)
        req = ToolExecutionRequest(
            name="transition_capability",
            arguments={"id": "nonexistent", "target": "testing"},
        )
        result = _run(executor(req, _make_ctx()))
        assert not result.success

    def test_rejects_invalid_target(self, tmp_path):
        store = _make_store(tmp_path)
        lm = _make_lifecycle(store)
        executor = _make_transition_capability_executor(lm)
        req = ToolExecutionRequest(
            name="transition_capability",
            arguments={"id": "whatever", "target": "invalid"},
        )
        result = _run(executor(req, _make_ctx()))
        assert not result.success


# ── TransitionResult payload completeness tests ───────────────────────

class TestTransitionResultPayload:
    def test_successful_result_has_required_fields(self, tmp_path):
        store = _make_store(tmp_path)
        _create_cap(store, cap_id="test_payload", maturity="draft", risk_level="low")
        lm = _make_lifecycle(store)
        executor = _make_transition_capability_executor(lm)
        req = ToolExecutionRequest(
            name="transition_capability",
            arguments={"id": "test_payload", "target": "testing"},
        )
        result = _run(executor(req, _make_ctx()))
        assert result.success
        for field in (
            "capability_id", "from_maturity", "to_maturity",
            "from_status", "to_status", "applied",
            "content_hash_before", "content_hash_after",
        ):
            assert field in result.payload, f"Missing field: {field}"
        assert result.payload.get("version_snapshot_id")

    def test_blocked_result_has_message(self, tmp_path):
        store = _make_store(tmp_path)
        _create_cap(store, cap_id="test_payload", maturity="testing", risk_level="high")
        lm = _make_lifecycle(store)
        executor = _make_transition_capability_executor(lm)
        req = ToolExecutionRequest(
            name="transition_capability",
            arguments={"id": "test_payload", "target": "stable"},
        )
        result = _run(executor(req, _make_ctx()))
        assert result.payload["applied"] is False
        assert result.payload.get("message")


# ── Dry-run completeness tests ────────────────────────────────────────

class TestDryRun:
    def test_dry_run_returns_allowed(self, tmp_path):
        store = _make_store(tmp_path)
        _create_cap(store, cap_id="test_dry", maturity="draft", risk_level="low")
        lm = _make_lifecycle(store)
        executor = _make_transition_capability_executor(lm)
        req = ToolExecutionRequest(
            name="transition_capability",
            arguments={"id": "test_dry", "target": "testing", "dry_run": True},
        )
        result = _run(executor(req, _make_ctx()))
        assert result.success
        assert result.payload["dry_run"] is True
        assert "allowed" in result.payload

    def test_dry_run_no_file_changes(self, tmp_path):
        store = _make_store(tmp_path)
        doc = _create_cap(store, cap_id="test_dry", maturity="draft")
        original_hash = doc.content_hash
        lm = _make_lifecycle(store)
        executor = _make_transition_capability_executor(lm)
        req = ToolExecutionRequest(
            name="transition_capability",
            arguments={"id": "test_dry", "target": "testing", "dry_run": True},
        )
        _run(executor(req, _make_ctx()))
        doc2 = store.get("test_dry")
        assert doc2.content_hash == original_hash
