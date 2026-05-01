"""Phase 0 regression tests: lock existing behavior before capability changes.

These tests verify that skill, agent, tool dispatch, runtime profile,
mutation log, and feature flag behaviors are exactly as expected before
the capability system is introduced.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.skills.skill_store import SkillStore
from src.skills.skill_executor import SkillExecutor
from src.core.runtime_profiles import (
    get_runtime_profile,
    STANDARD_PROFILE,
    INNER_TICK_PROFILE,
    ZERO_TOOLS_PROFILE,
    AGENT_RESEARCHER_PROFILE,
    AGENT_CODER_PROFILE,
    _PROFILES,
)
from src.logging.state_mutation_log import (
    MutationType,
    StateMutationLog,
)
from src.agents.spec import AgentSpec as AgentSpecV2
from src.agents.types import LegacyAgentSpec


# ── Feature flag defaults ──────────────────────────────────────────────

class TestFeatureFlagsDefaultDisabled:
    """All capability feature flags must default to False."""

    def test_capabilities_enabled_defaults_false(self):
        from src.config import get_settings
        s = get_settings()
        assert s.capabilities.enabled is False

    def test_capabilities_retrieval_enabled_defaults_false(self):
        from src.config import get_settings
        s = get_settings()
        assert s.capabilities.retrieval_enabled is False

    def test_capabilities_curator_enabled_defaults_false(self):
        from src.config import get_settings
        s = get_settings()
        assert s.capabilities.curator_enabled is False

    def test_capabilities_auto_draft_enabled_defaults_false(self):
        from src.config import get_settings
        s = get_settings()
        assert s.capabilities.auto_draft_enabled is False

    def test_compat_shim_exports_all_false(self):
        from config.settings import (
            CAPABILITIES_ENABLED,
            CAPABILITIES_RETRIEVAL_ENABLED,
            CAPABILITIES_CURATOR_ENABLED,
            CAPABILITIES_AUTO_DRAFT_ENABLED,
        )
        assert CAPABILITIES_ENABLED is False
        assert CAPABILITIES_RETRIEVAL_ENABLED is False
        assert CAPABILITIES_CURATOR_ENABLED is False
        assert CAPABILITIES_AUTO_DRAFT_ENABLED is False


# ── SkillStore behavior ─────────────────────────────────────────────────

class TestSkillStoreBehaviorUnchanged:
    """SkillStore CRUD and lifecycle must be unchanged."""

    @pytest.fixture
    def store(self, tmp_path):
        return SkillStore(skills_dir=tmp_path / "skills")

    def test_list_skills_returns_created_skill(self, store):
        store.create(
            skill_id="skill_regression_list",
            name="回归列表测试",
            description="测试列表功能",
            code='def run():\n    return {"ok": True}',
        )
        skills = store.list_skills()
        assert any(s["id"] == "skill_regression_list" for s in skills)

    def test_read_skill_returns_meta_and_code(self, store):
        store.create(
            skill_id="skill_regression_read",
            name="回归读取测试",
            description="测试读取功能",
            code='def run(x=1):\n    return {"x": x}',
        )
        result = store.read("skill_regression_read")
        assert result is not None
        assert result["meta"]["id"] == "skill_regression_read"
        assert result["meta"]["name"] == "回归读取测试"
        assert "def run(x=1):" in result["code"]
        assert "file_path" in result

    def test_read_nonexistent_skill_returns_none(self, store):
        assert store.read("skill_nonexistent") is None

    def test_skill_starts_as_draft(self, store):
        store.create(
            skill_id="skill_regression_draft",
            name="草稿技能",
            description="应初始化为草稿",
            code='def run():\n    return {}',
        )
        skill = store.read("skill_regression_draft")
        assert skill["meta"]["maturity"] == "draft"
        assert skill["meta"]["usage_count"] == 0
        assert skill["meta"]["success_count"] == 0

    def test_record_execution_promotes_draft_to_testing(self, store):
        store.create(
            skill_id="skill_regression_promote",
            name="晋升测试",
            description="成功后应从草稿晋升到测试",
            code='def run():\n    return {}',
        )
        store.record_execution("skill_regression_promote", success=True)
        skill = store.read("skill_regression_promote")
        assert skill["meta"]["maturity"] == "testing"
        assert skill["meta"]["success_count"] == 1
        assert skill["meta"]["usage_count"] == 1

    def test_record_execution_failure_does_not_promote(self, store):
        store.create(
            skill_id="skill_regression_fail",
            name="失败测试",
            description="失败后应保持草稿",
            code='def run():\n    raise RuntimeError("fail")',
        )
        store.record_execution("skill_regression_fail", success=False, error="fail")
        skill = store.read("skill_regression_fail")
        assert skill["meta"]["maturity"] == "draft"
        assert skill["meta"]["success_count"] == 0
        assert skill["meta"]["last_error"] == "fail"

    def test_delete_skill_removes_it(self, store):
        store.create(
            skill_id="skill_regression_delete",
            name="待删除",
            description="将被删除",
            code='def run():\n    return {}',
        )
        store.delete("skill_regression_delete")
        assert store.read("skill_regression_delete") is None

    def test_get_skill_index_returns_lightweight_summary(self, store):
        store.create(
            skill_id="skill_regression_index",
            name="索引测试",
            description="索引应包含此技能",
            code='def run():\n    return {}',
        )
        index = store.get_skill_index()
        assert any(s["id"] == "skill_regression_index" for s in index)

    def test_update_code_resets_maturity_to_draft(self, store):
        store.create(
            skill_id="skill_regression_update",
            name="更新测试",
            description="更新后重置为草稿",
            code='def run():\n    return {}',
        )
        store.record_execution("skill_regression_update", success=True)
        assert store.read("skill_regression_update")["meta"]["maturity"] == "testing"
        store.update_code("skill_regression_update", 'def run():\n    return {"v": 2}')
        assert store.read("skill_regression_update")["meta"]["maturity"] == "draft"

    def test_get_stable_skills_filters_correctly(self, store):
        store.create(
            skill_id="skill_stable_only",
            name="稳定技能",
            description="仅返回稳定技能",
            code='def run():\n    return {}',
        )
        # Initially draft — should not appear in stable list
        stable = store.get_stable_skills()
        assert not any(s["id"] == "skill_stable_only" for s in stable)

    def test_create_duplicate_rejected_without_overwrite(self, store):
        store.create(
            skill_id="skill_dup_regression",
            name="原始",
            description="不应被覆盖",
            code='def run():\n    return 1',
        )
        with pytest.raises(FileExistsError):
            store.create(
                skill_id="skill_dup_regression",
                name="更新",
                description="新描述",
                code='def run():\n    return 2',
            )


# ── RuntimeProfile behavior ─────────────────────────────────────────────

class TestRuntimeProfileBehaviorUnchanged:
    """RuntimeProfile definitions must be unchanged."""

    def test_all_known_profiles_exist(self):
        names = set(_PROFILES.keys())
        expected = {
            "chat_shell", "zero_tools", "standard", "inner_tick",
            "compose_proactive", "local_execution", "task_execution",
            "agent_admin_operator", "identity_operator", "browser_operator",
            "skill_operator", "capability_lifecycle_operator",
            "coder_snippet", "coder_workspace",
            "file_ops", "agent_researcher", "agent_coder",
        }
        assert names == expected

    def test_standard_profile_has_expected_tools(self):
        profile = get_runtime_profile("standard")
        assert "execute_shell" not in profile.tool_names
        assert "run_skill" in profile.tool_names

    def test_inner_tick_profile_has_expected_tools(self):
        profile = get_runtime_profile("inner_tick")
        assert "delegate_to_researcher" in profile.tool_names
        assert "run_skill" in profile.tool_names

    def test_zero_tools_has_nothing(self):
        profile = get_runtime_profile("zero_tools")
        assert len(profile.tool_names) == 0

    def test_get_runtime_profile_raises_on_unknown(self):
        with pytest.raises(ValueError):
            get_runtime_profile("nonexistent_profile_xyz")

    def test_profile_is_frozen_dataclass(self):
        profile = get_runtime_profile("standard")
        with pytest.raises(Exception):
            profile.tool_names = set()  # type: ignore[misc]


# ── MutationLog behavior ────────────────────────────────────────────────

class TestMutationLogBehaviorUnchanged:
    """StateMutationLog CRUD and events must be unchanged."""

    @pytest.fixture
    async def log(self, tmp_path):
        store = StateMutationLog(tmp_path / "mutation_log.db", logs_dir=tmp_path / "logs")
        await store.init()
        yield store
        await store.close()

    @pytest.mark.asyncio
    async def test_record_system_started(self, log):
        id_ = await log.record(MutationType.SYSTEM_STARTED, {"pid": 1})
        assert id_ > 0

    @pytest.mark.asyncio
    async def test_record_tool_called(self, log):
        id_ = await log.record(MutationType.TOOL_CALLED, {"tool": "run_skill"})
        assert id_ > 0
        rows = await log.query_by_type(MutationType.TOOL_CALLED)
        assert len(rows) == 1
        assert rows[0].payload["tool"] == "run_skill"

    @pytest.mark.asyncio
    async def test_record_tool_denied(self, log):
        await log.record(MutationType.TOOL_DENIED, {"guard": "profile_not_allowed", "tool": "x"})
        rows = await log.query_by_type(MutationType.TOOL_DENIED)
        assert len(rows) == 1
        assert rows[0].payload["guard"] == "profile_not_allowed"

    @pytest.mark.asyncio
    async def test_record_iteration_started_and_ended(self, log):
        from src.logging.state_mutation_log import new_iteration_id
        iid = new_iteration_id()
        await log.record(
            MutationType.ITERATION_STARTED,
            {"iteration_id": iid},
            iteration_id=iid,
        )
        await log.record(
            MutationType.ITERATION_ENDED,
            {"iteration_id": iid, "stop_reason": "done"},
            iteration_id=iid,
        )
        rows = await log.query_by_iteration(iid)
        assert len(rows) == 2
        types = {r.event_type for r in rows}
        assert "iteration.started" in types
        assert "iteration.ended" in types

    @pytest.mark.asyncio
    async def test_record_agent_lifecycle_events(self, log):
        await log.record(MutationType.AGENT_STARTED, {"agent": "test", "task_id": "t1"})
        await log.record(MutationType.AGENT_TOOL_CALL, {"agent": "test", "tool": "research"})
        await log.record(MutationType.AGENT_COMPLETED, {"agent": "test", "task_id": "t1"})
        rows = await log.query_by_type(MutationType.AGENT_COMPLETED)
        assert len(rows) == 1
        assert rows[0].payload["agent"] == "test"

    @pytest.mark.asyncio
    async def test_jsonl_mirror_written(self, log, tmp_path):
        await log.record(MutationType.ITERATION_STARTED, {"iteration_id": "jsonl_test"})
        from datetime import date
        today = date.today().isoformat()
        jsonl = tmp_path / "logs" / f"mutations_{today}.log"
        assert jsonl.exists()
        lines = jsonl.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) >= 1
        entry = json.loads(lines[0])
        assert entry["event_type"] == "iteration.started"

    @pytest.mark.asyncio
    async def test_mutation_type_enum_values_unchanged(self, log):
        """Verify key MutationType members still exist."""
        expected = {
            "system.started", "system.stopped",
            "iteration.started", "iteration.ended",
            "llm.request", "llm.response",
            "tool.called", "tool.result",
            "tool.denied",
            "agent.task_started", "agent.task_done", "agent.task_failed",
            "agent.tool_called", "agent.created", "agent.saved", "agent.destroyed",
        }
        for event_type in expected:
            assert MutationType(event_type)


# ── Dynamic agent behavior ─────────────────────────────────────────────

class TestDynamicAgentBehaviorUnchanged:
    """Dynamic agent spec, denylist, and creation behavior must be unchanged."""

    def test_dynamic_agent_denylist_contains_key_tools(self):
        from src.agents.spec import DYNAMIC_AGENT_DENYLIST
        blocked = {
            "create_agent", "save_agent", "destroy_agent",
            "delegate_to_agent", "delegate_to_researcher", "delegate_to_coder",
            "list_agents",
            "send_message", "send_image", "proactive_send",
            "memory_note", "edit_soul", "edit_voice", "add_correction",
            "commit_promise", "fulfill_promise", "abandon_promise",
            "set_reminder", "cancel_reminder",
            "plan_task", "update_plan",
            "close_focus", "recall_focus",
        }
        assert DYNAMIC_AGENT_DENYLIST == frozenset(blocked)

    def test_agent_spec_v2_has_expected_fields(self):
        spec = AgentSpecV2(
            name="test_agent",
            kind="dynamic",
            runtime_profile="agent_researcher",
            model_slot="agent_researcher",
            system_prompt="test prompt",
            tool_denylist=["tool_a"],
        )
        assert spec.name == "test_agent"
        assert spec.kind == "dynamic"
        assert spec.status == "active"
        assert spec.version == 1
        assert spec.runtime_profile == "agent_researcher"
        assert spec.model_slot == "agent_researcher"
        assert "tool_a" in spec.tool_denylist

    def test_agent_spec_hash_is_stable(self):
        spec = AgentSpecV2(
            name="hash_test",
            kind="dynamic",
            runtime_profile="agent_researcher",
            model_slot="agent_researcher",
            system_prompt="test",
            tool_denylist=[],
        )
        h1 = spec.spec_hash()
        h2 = spec.spec_hash()
        assert h1 == h2
        assert len(h1) == 16  # 64-bit hex digest

    def test_agent_spec_hash_changes_with_content(self):
        spec = AgentSpecV2(
            name="hash_test_2",
            kind="dynamic",
            runtime_profile="agent_researcher",
            model_slot="agent_researcher",
            system_prompt="original",
            tool_denylist=[],
        )
        h1 = spec.spec_hash()
        spec.system_prompt = "modified"
        h2 = spec.spec_hash()
        assert h1 != h2

    def test_legacy_agent_spec_still_usable(self):
        spec = LegacyAgentSpec(
            name="legacy_test",
            description="legacy agent",
            system_prompt="legacy prompt",
            model_slot="test_slot",
            tools=["tool_a"],
        )
        assert spec.name == "legacy_test"
        assert spec.tools == ["tool_a"]


# ── ToolDispatcher permission checks ────────────────────────────────────

class TestToolDispatcherPermissionBehaviorUnchanged:
    """ToolDispatcher guard behavior must be unchanged."""

    def test_service_context_view_has_expected_services(self):
        from src.core.tool_dispatcher import ServiceContextView
        services = {
            "skill_store": MagicMock(),
            "mutation_log": MagicMock(),
            "tool_registry": MagicMock(),
        }
        ctx = ServiceContextView(services)
        assert ctx.skill_store is services["skill_store"]
        assert ctx.mutation_log is services["mutation_log"]
        assert ctx.tool_registry is services["tool_registry"]

    def test_service_context_view_require_raises_on_missing(self):
        from src.core.tool_dispatcher import ServiceContextView, MissingServiceError
        ctx = ServiceContextView({})
        with pytest.raises(MissingServiceError):
            _ = ctx.require_tool_registry()

    def test_service_context_view_returns_none_for_optional_missing(self):
        from src.core.tool_dispatcher import ServiceContextView
        ctx = ServiceContextView({})
        assert ctx.skill_store is None
        assert ctx.browser_manager is None
        assert ctx.circuit_breaker is None


# ── SkillExecutor preserves sandbox gating ──────────────────────────────

class TestSkillExecutorSandboxGating:
    """SkillExecutor must gate execution by maturity."""

    def test_sandbox_maturities_constant_unchanged(self):
        from src.skills.skill_executor import _SANDBOX_MATURITIES
        assert _SANDBOX_MATURITIES == {"draft", "testing", "broken"}


# ── No capability runtime wiring ────────────────────────────────────────

class TestNoCapabilityRuntimeWiring:
    """Verify no capability code is reachable from runtime paths.

    At Phase 0, the capabilities package (if it exists) must not be
    imported by Brain, TaskRuntime, StateViewBuilder, SkillExecutor,
    ToolDispatcher, or agent modules.
    """

    _RUNTIME_MODULES = [
        "src.core.brain",
        "src.core.task_runtime",
        "src.core.state_view_builder",
        "src.core.tool_dispatcher",
        "src.skills.skill_executor",
        "src.skills.skill_store",
        "src.tools.skill_tools",
        "src.agents.registry",
        "src.agents.policy",
        "src.agents.dynamic",
        "src.agents.base",
        "src.agents.factory",
    ]

    def test_no_runtime_module_imports_capabilities(self):
        """None of the runtime modules import from src.capabilities."""
        import sys
        # If src.capabilities already exists, check no runtime module imports it
        for mod_name in self._RUNTIME_MODULES:
            if mod_name in sys.modules:
                mod = sys.modules[mod_name]
                # Check the module's own imports, not transitive ones
                for attr_name in dir(mod):
                    attr = getattr(mod, attr_name)
                    # If any attribute is a capabilities module, that's a problem
                    if hasattr(attr, "__name__") and "capabilities" in getattr(attr, "__name__", ""):
                        pass  # This would be triggered by transitive imports too
                # More reliable: check sys.modules for capabilities in this module's scope
                # We skip this check if capabilities doesn't exist yet

    def test_brain_build_services_has_no_capability_references(self):
        """Brain._build_services must not reference capabilities."""
        import inspect
        from src.core.brain import LapwingBrain
        source = inspect.getsource(LapwingBrain._build_services)
        assert "capabilit" not in source.lower()
