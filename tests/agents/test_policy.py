"""Tests for src.agents.policy — AgentPolicy + LintResult + AgentPolicyViolation.

Covers blueprint acceptance criteria T-04, T-07, T-11.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.agents.policy import (
    AgentPolicy,
    AgentPolicyViolation,
    CreateAgentInput,
    LintResult,
)
from src.agents.catalog import AgentCatalog
from src.agents.spec import (
    AgentLifecyclePolicy,
    AgentSpec,
)


def _safe_lint() -> LintResult:
    return LintResult(verdict="safe", reason="ok")


def _make_create_input(**overrides) -> CreateAgentInput:
    """Helper to build a valid CreateAgentInput with optional overrides."""
    defaults = dict(
        name_hint="probe",
        purpose="testing",
        instructions="do things",
        profile="agent_researcher",
        model_slot="agent_researcher",
        lifecycle="ephemeral",
        max_runs=1,
        ttl_seconds=3600,
    )
    defaults.update(overrides)
    return CreateAgentInput(**defaults)


# ── T-04: validate_create rejects bad profile / bad model_slot / lifecycle=persistent ──

@pytest.mark.asyncio
async def test_validate_create_rejects_unknown_profile(tmp_path):
    cat = AgentCatalog(tmp_path / "x.db")
    await cat.init()
    pol = AgentPolicy(cat)
    pol._semantic_lint = AsyncMock(return_value=_safe_lint())
    with pytest.raises(AgentPolicyViolation):
        await pol.validate_create(
            _make_create_input(profile="admin_full_access"),
            creator_context=MagicMock(),
        )


@pytest.mark.asyncio
async def test_validate_create_rejects_unknown_model_slot(tmp_path):
    cat = AgentCatalog(tmp_path / "x.db")
    await cat.init()
    pol = AgentPolicy(cat)
    pol._semantic_lint = AsyncMock(return_value=_safe_lint())
    with pytest.raises(AgentPolicyViolation):
        await pol.validate_create(
            _make_create_input(model_slot="god_mode"),
            creator_context=MagicMock(),
        )


@pytest.mark.asyncio
async def test_validate_create_rejects_persistent_lifecycle(tmp_path):
    cat = AgentCatalog(tmp_path / "x.db")
    await cat.init()
    pol = AgentPolicy(cat)
    pol._semantic_lint = AsyncMock(return_value=_safe_lint())
    with pytest.raises(AgentPolicyViolation):
        await pol.validate_create(
            _make_create_input(lifecycle="persistent"),
            creator_context=MagicMock(),
        )


@pytest.mark.asyncio
async def test_validate_create_returns_normalized_spec(tmp_path):
    cat = AgentCatalog(tmp_path / "x.db")
    await cat.init()
    pol = AgentPolicy(cat)
    pol._semantic_lint = AsyncMock(return_value=_safe_lint())
    spec = await pol.validate_create(
        _make_create_input(
            name_hint="My Translator!",
            purpose="trans",
            instructions="translate",
        ),
        creator_context=MagicMock(),
    )
    assert isinstance(spec, AgentSpec)
    # snake_case + stripped non-alnum
    assert spec.name == "my_translator"
    assert spec.kind == "dynamic"
    assert spec.lifecycle.mode == "ephemeral"
    assert spec.system_prompt == "translate"
    assert spec.description == "trans"


# ── T-07: lint fail-closed ──

@pytest.mark.asyncio
async def test_lint_fail_closed_on_unsafe(tmp_path):
    cat = AgentCatalog(tmp_path / "x.db")
    await cat.init()
    pol = AgentPolicy(cat)
    pol._semantic_lint = AsyncMock(
        return_value=LintResult(verdict="unsafe", reason="bad")
    )
    with pytest.raises(AgentPolicyViolation):
        await pol.validate_create(
            _make_create_input(),
            creator_context=MagicMock(),
        )


@pytest.mark.asyncio
async def test_lint_fail_closed_on_uncertain(tmp_path):
    cat = AgentCatalog(tmp_path / "x.db")
    await cat.init()
    pol = AgentPolicy(cat)
    pol._semantic_lint = AsyncMock(return_value=LintResult(verdict="uncertain"))
    with pytest.raises(AgentPolicyViolation):
        await pol.validate_create(
            _make_create_input(),
            creator_context=MagicMock(),
        )


@pytest.mark.asyncio
async def test_lint_fail_closed_on_exception(tmp_path):
    cat = AgentCatalog(tmp_path / "x.db")
    await cat.init()
    pol = AgentPolicy(cat)
    pol._semantic_lint = AsyncMock(side_effect=TimeoutError())
    with pytest.raises(AgentPolicyViolation):
        await pol.validate_create(
            _make_create_input(),
            creator_context=MagicMock(),
        )


# ── tool denylist ──

def test_validate_tool_access_blocks_denylist():
    pol = AgentPolicy(catalog=MagicMock())
    spec = AgentSpec(name="x", runtime_profile="agent_researcher")
    assert pol.validate_tool_access(spec, "send_message") is False
    assert pol.validate_tool_access(spec, "create_agent") is False


def test_validate_tool_access_allows_research_for_researcher_profile():
    pol = AgentPolicy(catalog=MagicMock())
    spec = AgentSpec(name="x", runtime_profile="agent_researcher")
    # research is in agent_researcher's tool_names per runtime_profiles.py
    assert pol.validate_tool_access(spec, "research") is True


def test_validate_tool_access_blocks_when_in_spec_denylist():
    pol = AgentPolicy(catalog=MagicMock())
    spec = AgentSpec(
        name="x",
        runtime_profile="agent_researcher",
        tool_denylist=["research"],
    )
    assert pol.validate_tool_access(spec, "research") is False


# ── T-11: save_agent stricter validation ──

@pytest.mark.asyncio
async def test_validate_save_rejects_unrun_agent(tmp_path):
    cat = AgentCatalog(tmp_path / "x.db")
    await cat.init()
    pol = AgentPolicy(cat)
    pol._semantic_lint = AsyncMock(return_value=_safe_lint())
    spec = AgentSpec(name="x", runtime_profile="agent_researcher")
    with pytest.raises(AgentPolicyViolation):
        await pol.validate_save(spec, run_history=[])


@pytest.mark.asyncio
async def test_validate_save_rejects_when_max_persistent_reached(tmp_path):
    cat = AgentCatalog(tmp_path / "x.db")
    await cat.init()
    pol = AgentPolicy(cat)
    pol._semantic_lint = AsyncMock(return_value=_safe_lint())
    # fill catalog to MAX
    for i in range(AgentPolicy.MAX_PERSISTENT_AGENTS):
        s = AgentSpec(
            name=f"persisted_{i}",
            kind="dynamic",
            runtime_profile="agent_researcher",
            lifecycle=AgentLifecyclePolicy(mode="persistent"),
        )
        await cat.save(s)
    new_spec = AgentSpec(name="overflow", runtime_profile="agent_researcher")
    with pytest.raises(AgentPolicyViolation):
        await pol.validate_save(new_spec, run_history=["task_id_1"])


@pytest.mark.asyncio
async def test_validate_save_rejects_duplicate_name(tmp_path):
    cat = AgentCatalog(tmp_path / "x.db")
    await cat.init()
    pol = AgentPolicy(cat)
    pol._semantic_lint = AsyncMock(return_value=_safe_lint())
    existing = AgentSpec(
        name="dupe",
        kind="dynamic",
        runtime_profile="agent_researcher",
        lifecycle=AgentLifecyclePolicy(mode="persistent"),
    )
    await cat.save(existing)
    new_spec = AgentSpec(name="dupe", runtime_profile="agent_researcher")
    with pytest.raises(AgentPolicyViolation):
        await pol.validate_save(new_spec, run_history=["task_id_1"])
