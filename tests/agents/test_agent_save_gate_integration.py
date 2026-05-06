"""Phase 6C — Save gate integration tests.

Tests:
  - save_agent flow with gate enabled/disabled
  - Registry-level atomicity
  - Legacy agent behavior unchanged
  - No regression on existing flows
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.candidate import AgentCandidate, AgentEvalEvidence
from src.agents.candidate_store import AgentCandidateStore
from src.agents.catalog import AgentCatalog
from src.agents.policy import (
    AgentPolicy,
    AgentPolicyViolation,
    CreateAgentInput,
    LintResult,
)
from src.agents.registry import AgentRegistry
from src.agents.spec import AgentSpec


def _safe_lint():
    return LintResult(verdict="safe", reason="ok")


def _make_policy(catalog):
    policy = AgentPolicy(catalog)
    policy._semantic_lint = AsyncMock(return_value=_safe_lint())
    return policy


async def _make_registry(tmp_path):
    db_path = tmp_path / "test.db"
    cat = AgentCatalog(db_path)
    await cat.init()
    policy = _make_policy(cat)
    factory = MagicMock()
    reg = AgentRegistry(cat, factory, policy)
    await reg.init()
    return reg, cat, policy


def _make_store(tmp_path):
    return AgentCandidateStore(tmp_path / "agent_candidates")


async def _create_and_get_spec(registry, name_hint="test", purpose="testing", instructions="do stuff"):
    spec = await registry.create_agent(
        CreateAgentInput(
            name_hint=name_hint,
            purpose=purpose,
            instructions=instructions,
            profile="agent_researcher",
            model_slot="agent_researcher",
            lifecycle="session",
            max_runs=5,
            ttl_seconds=600,
        ),
        ctx=MagicMock(),
    )
    return spec


# ══════════════════════════════════════════════════════════════════════════════
# 1. save_agent gate integration tests
# ══════════════════════════════════════════════════════════════════════════════

class TestSaveAgentGateIntegration:
    @pytest.mark.asyncio
    async def test_save_agent_with_flag_false_works_as_before(self, tmp_path):
        """When flag is false, save_agent works exactly as before."""
        reg, cat, _ = await _make_registry(tmp_path)
        spec = await _create_and_get_spec(reg, "gate_test_1")

        await reg.save_agent(
            spec.name,
            reason="test",
            run_history=["ran_once"],
            require_candidate_approval=False,
        )

        # Verify spec was persisted
        saved = await cat.get_by_name(spec.name)
        assert saved is not None
        assert saved.lifecycle.mode == "persistent"

    @pytest.mark.asyncio
    async def test_save_agent_with_flag_false_capability_backed_saves(self, tmp_path):
        """When flag is false, even capability-backed specs save normally."""
        reg, cat, _ = await _make_registry(tmp_path)
        spec = await _create_and_get_spec(reg, "cap_gate_test")

        # Mutate spec to be capability-backed (simulating Phase 6A metadata)
        spec.bound_capabilities = ["workspace_a1b2c3d4"]

        await reg.save_agent(
            spec.name,
            reason="test",
            run_history=["ran_once"],
            require_candidate_approval=False,
        )

        saved = await cat.get_by_name(spec.name)
        assert saved is not None
        assert saved.lifecycle.mode == "persistent"

    @pytest.mark.asyncio
    async def test_save_agent_with_flag_true_ordinary_agent_saves(self, tmp_path):
        """When flag is true, non-capability-backed specs still save."""
        reg, cat, _ = await _make_registry(tmp_path)
        spec = await _create_and_get_spec(reg, "ordinary_agent")

        await reg.save_agent(
            spec.name,
            reason="test",
            run_history=["ran_once"],
            require_candidate_approval=True,
        )

        saved = await cat.get_by_name(spec.name)
        assert saved is not None
        assert saved.lifecycle.mode == "persistent"

    @pytest.mark.asyncio
    async def test_save_agent_flag_true_capability_backed_no_candidate_denied(self, tmp_path):
        """When flag is true and spec is capability-backed but no candidate,
        save is denied cleanly."""
        reg, cat, _ = await _make_registry(tmp_path)
        spec = await _create_and_get_spec(reg, "cap_blocked_agent")
        spec.bound_capabilities = ["workspace_a1b2c3d4"]

        with pytest.raises(AgentPolicyViolation) as exc_info:
            await reg.save_agent(
                spec.name,
                reason="test",
                run_history=["ran_once"],
                require_candidate_approval=True,
            )

        assert "save_gate_denied" in str(exc_info.value.reason) or "save_gate_denied" == exc_info.value.reason
        # Verify no partial persistent agent was written
        saved = await cat.get_by_name(spec.name)
        if saved is not None:
            # If found, it shouldn't be persistent
            assert saved.lifecycle.mode != "persistent"

    @pytest.mark.asyncio
    async def test_save_agent_flag_true_with_approved_candidate_succeeds(self, tmp_path):
        """When flag is true and an approved matching candidate exists, save
        succeeds."""
        reg, cat, _ = await _make_registry(tmp_path)
        store = _make_store(tmp_path)
        spec = await _create_and_get_spec(reg, "approved_cap_agent")
        spec.bound_capabilities = ["workspace_a1b2c3d4"]

        candidate = AgentCandidate(
            candidate_id="cand_int_approved",
            name=spec.name,
            description="approved candidate",
            proposed_spec=spec,
            reason="integration test",
            approval_state="approved",
            risk_level="low",
        )
        store.create_candidate(candidate)

        await reg.save_agent(
            spec.name,
            reason="test",
            run_history=["ran_once"],
            candidate_id=candidate.candidate_id,
            candidate_store=store,
            require_candidate_approval=True,
        )

        saved = await cat.get_by_name(spec.name)
        assert saved is not None
        assert saved.lifecycle.mode == "persistent"


# ══════════════════════════════════════════════════════════════════════════════
# 2. Atomicity tests at Registry level
# ══════════════════════════════════════════════════════════════════════════════

class TestSaveGateAtomicity:
    @pytest.mark.asyncio
    async def test_denied_save_does_not_write_persistent_agent(self, tmp_path):
        """A denied save must not write any persistent agent file or catalog
        entry."""
        reg, cat, _ = await _make_registry(tmp_path)
        spec = await _create_and_get_spec(reg, "atomic_deny_agent")
        spec.bound_capabilities = ["workspace_a1b2c3d4"]

        count_before = await cat.count(kind="dynamic")

        try:
            await reg.save_agent(
                spec.name,
                reason="test",
                run_history=["ran_once"],
                require_candidate_approval=True,
            )
        except AgentPolicyViolation:
            pass

        count_after = await cat.count(kind="dynamic")
        assert count_after == count_before

    @pytest.mark.asyncio
    async def test_denied_save_does_not_remove_from_session_dict(self, tmp_path):
        """A denied save must not remove the spec from the session/ephemeral
        dicts."""
        reg, cat, _ = await _make_registry(tmp_path)
        spec = await _create_and_get_spec(reg, "keep_session_agent")
        spec.bound_capabilities = ["workspace_a1b2c3d4"]
        name = spec.name

        assert name in reg._session_agents

        try:
            await reg.save_agent(
                name,
                reason="test",
                run_history=["ran_once"],
                require_candidate_approval=True,
            )
        except AgentPolicyViolation:
            pass

        # Session entry should still exist (denied save is atomic)
        assert name in reg._session_agents

    @pytest.mark.asyncio
    async def test_denied_save_does_not_mutate_candidate(self, tmp_path):
        """A denied save must not modify the candidate file."""
        reg, cat, _ = await _make_registry(tmp_path)
        store = _make_store(tmp_path)
        spec = await _create_and_get_spec(reg, "cand_mutate_test")
        spec.bound_capabilities = ["workspace_a1b2c3d4"]

        candidate = AgentCandidate(
            candidate_id="cand_mutate_test",
            name=spec.name,
            description="test",
            proposed_spec=spec,
            reason="integration test",
            approval_state="pending",
            risk_level="low",
        )
        store.create_candidate(candidate)

        before = store.get_candidate(candidate.candidate_id)
        assert before.approval_state == "pending"

        try:
            await reg.save_agent(
                spec.name,
                reason="test",
                run_history=["ran_once"],
                candidate_id=candidate.candidate_id,
                candidate_store=store,
                require_candidate_approval=True,
            )
        except AgentPolicyViolation:
            pass

        after = store.get_candidate(candidate.candidate_id)
        assert after.approval_state == "pending"
        assert after.to_dict() == before.to_dict()

    @pytest.mark.asyncio
    async def test_successful_save_writes_as_expected(self, tmp_path):
        """A successful gated save writes the persistent agent as expected."""
        reg, cat, _ = await _make_registry(tmp_path)
        store = _make_store(tmp_path)
        spec = await _create_and_get_spec(reg, "success_save_agent")
        spec.bound_capabilities = ["workspace_a1b2c3d4"]

        candidate = AgentCandidate(
            candidate_id="cand_success_save",
            name=spec.name,
            description="test",
            proposed_spec=spec,
            reason="integration test",
            approval_state="approved",
            risk_level="low",
        )
        store.create_candidate(candidate)

        await reg.save_agent(
            spec.name,
            reason="test",
            run_history=["ran_once"],
            candidate_id=candidate.candidate_id,
            candidate_store=store,
            require_candidate_approval=True,
        )

        saved = await cat.get_by_name(spec.name)
        assert saved is not None
        assert saved.lifecycle.mode == "persistent"
        assert saved.version == spec.version + 1


# ══════════════════════════════════════════════════════════════════════════════
# 3. Error message quality
# ══════════════════════════════════════════════════════════════════════════════

class TestErrorMessages:
    @pytest.mark.asyncio
    async def test_denied_save_error_is_clean(self, tmp_path):
        """Denied saves return AgentPolicyViolation with structured details,
        not raw exceptions."""
        reg, cat, _ = await _make_registry(tmp_path)
        spec = await _create_and_get_spec(reg, "clean_err_test")
        spec.bound_capabilities = ["workspace_a1b2c3d4"]

        try:
            await reg.save_agent(
                spec.name,
                reason="test",
                run_history=["ran_once"],
                require_candidate_approval=True,
            )
        except AgentPolicyViolation as exc:
            assert exc.reason == "save_gate_denied"
            assert "denials" in exc.details
            # Error message should not contain stack trace markers
            msg = str(exc)
            assert "Traceback" not in msg
            assert "File " not in msg

    @pytest.mark.asyncio
    async def test_candidate_lookup_failure_is_clean(self, tmp_path):
        """When a bad candidate_id causes a lookup failure, the error is clean."""
        reg, cat, _ = await _make_registry(tmp_path)
        store = _make_store(tmp_path)
        spec = await _create_and_get_spec(reg, "lookup_fail_test")
        spec.bound_capabilities = ["workspace_a1b2c3d4"]

        with pytest.raises(AgentPolicyViolation) as exc_info:
            await reg.save_agent(
                spec.name,
                reason="test",
                run_history=["ran_once"],
                candidate_id="cand_nonexistent",
                candidate_store=store,
                require_candidate_approval=True,
            )

        assert exc_info.value.reason == "candidate_lookup_failed"
        assert "candidate_id" in exc_info.value.details


# ══════════════════════════════════════════════════════════════════════════════
# 4. Legacy behavior regression
# ══════════════════════════════════════════════════════════════════════════════

class TestLegacyBehaviorRegression:
    @pytest.mark.asyncio
    async def test_legacy_save_agent_no_flag_unchanged(self, tmp_path):
        """save_agent without gate params works exactly as before."""
        reg, cat, _ = await _make_registry(tmp_path)
        spec = await _create_and_get_spec(reg, "legacy_test")

        await reg.save_agent(spec.name, reason="test", run_history=["ran_once"])

        saved = await cat.get_by_name(spec.name)
        assert saved is not None
        assert saved.lifecycle.mode == "persistent"

    @pytest.mark.asyncio
    async def test_save_agent_requires_run_history(self, tmp_path):
        """Existing save validation (run_history required) still enforced."""
        reg, cat, _ = await _make_registry(tmp_path)
        spec = await _create_and_get_spec(reg, "no_history_test")

        with pytest.raises(AgentPolicyViolation) as exc_info:
            await reg.save_agent(spec.name, reason="test", run_history=[])

        assert "save_requires_run_history" in str(exc_info.value.reason)

    @pytest.mark.asyncio
    async def test_cannot_save_builtin_agent(self, tmp_path):
        """Builtin agents cannot be saved (existing rule)."""
        reg, cat, _ = await _make_registry(tmp_path)

        with pytest.raises(AgentPolicyViolation) as exc_info:
            await reg.save_agent("researcher", reason="test", run_history=["x"])

        assert "cannot_save_builtin" in str(exc_info.value.reason)

    @pytest.mark.asyncio
    async def test_agent_not_found(self, tmp_path):
        """Non-existent agent name raises clean error."""
        reg, cat, _ = await _make_registry(tmp_path)

        with pytest.raises(AgentPolicyViolation) as exc_info:
            await reg.save_agent("nonexistent", reason="test", run_history=["x"])

        assert "agent_not_found" in str(exc_info.value.reason)


# ══════════════════════════════════════════════════════════════════════════════
# 5. No capabilities imports in agent modules
# ══════════════════════════════════════════════════════════════════════════════

class TestImportSanity:
    def test_no_capabilities_import_in_spec(self):
        import ast, inspect
        import src.agents.spec as mod
        src = inspect.getsource(mod)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module_name = None
                if isinstance(node, ast.ImportFrom) and node.module:
                    module_name = node.module
                if module_name and "src.capabilities" in module_name:
                    pytest.fail(f"spec.py imports from src.capabilities: {ast.dump(node)}")

    def test_no_capabilities_import_in_policy(self):
        import ast, inspect
        import src.agents.policy as mod
        src = inspect.getsource(mod)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module_name = None
                if isinstance(node, ast.ImportFrom) and node.module:
                    module_name = node.module
                if module_name and "src.capabilities" in module_name:
                    pytest.fail(f"policy.py imports from src.capabilities: {ast.dump(node)}")

    def test_no_capabilities_import_in_registry(self):
        import ast, inspect
        import src.agents.registry as mod
        src = inspect.getsource(mod)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module_name = None
                if isinstance(node, ast.ImportFrom) and node.module:
                    module_name = node.module
                if module_name and "src.capabilities" in module_name:
                    pytest.fail(f"registry.py imports from src.capabilities: {ast.dump(node)}")

    def test_no_capabilities_import_in_candidate(self):
        import ast, inspect
        import src.agents.candidate as mod
        src = inspect.getsource(mod)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module_name = None
                if isinstance(node, ast.ImportFrom) and node.module:
                    module_name = node.module
                if module_name and "src.capabilities" in module_name:
                    pytest.fail(f"candidate.py imports from src.capabilities: {ast.dump(node)}")

    def test_no_capabilities_import_in_candidate_store(self):
        import ast, inspect
        import src.agents.candidate_store as mod
        src = inspect.getsource(mod)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module_name = None
                if isinstance(node, ast.ImportFrom) and node.module:
                    module_name = node.module
                if module_name and "src.capabilities" in module_name:
                    pytest.fail(f"candidate_store.py imports from src.capabilities: {ast.dump(node)}")
