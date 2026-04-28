import os
import pytest
from unittest.mock import MagicMock
from src.agents.factory import AgentFactory
from src.agents.spec import AgentSpec, AgentLifecyclePolicy, DYNAMIC_AGENT_DENYLIST
from src.agents.dynamic import DynamicAgent
from src.agents.researcher import Researcher
from src.agents.coder import Coder


def _factory():
    return AgentFactory(
        llm_router=MagicMock(),
        tool_registry=MagicMock(),
        mutation_log=MagicMock(),
    )


def test_create_builtin_researcher(monkeypatch):
    """Builtin path: ignores new AgentSpec, calls Researcher.create() directly."""
    fake_cfg = MagicMock(max_rounds=15, max_tokens=30000, timeout_seconds=180)
    fake_settings = MagicMock(agent_team=MagicMock(researcher=fake_cfg))
    monkeypatch.setattr("src.agents.researcher.get_settings", lambda: fake_settings)

    f = _factory()
    spec = AgentSpec(
        id="builtin_researcher", name="researcher",
        kind="builtin", runtime_profile="agent_researcher",
        model_slot="agent_researcher",
    )
    inst = f.create(spec)
    assert isinstance(inst, Researcher)


def test_create_builtin_coder(monkeypatch):
    fake_cfg = MagicMock(max_rounds=15, max_tokens=30000, timeout_seconds=180)
    fake_settings = MagicMock(agent_team=MagicMock(coder=fake_cfg))
    monkeypatch.setattr("src.agents.coder.get_settings", lambda: fake_settings)

    f = _factory()
    spec = AgentSpec(
        id="builtin_coder", name="coder",
        kind="builtin", runtime_profile="agent_coder",
        model_slot="agent_coder",
    )
    inst = f.create(spec)
    assert isinstance(inst, Coder)


def test_create_dynamic():
    f = _factory()
    spec = AgentSpec(
        name="translator", kind="dynamic",
        runtime_profile="agent_researcher",
        model_slot="agent_researcher",
        system_prompt="translate things",
    )
    inst = f.create(spec)
    assert isinstance(inst, DynamicAgent)


def test_resolve_profile_merges_denylist():
    """For dynamic agents, _resolve_profile merges spec.tool_denylist AND
    DYNAMIC_AGENT_DENYLIST into the resulting profile's exclude_tool_names."""
    f = _factory()
    spec = AgentSpec(
        name="x", kind="dynamic",
        runtime_profile="agent_researcher",
        tool_denylist=["browse"],  # blueprint says spec.tool_denylist must be subset of DYNAMIC_AGENT_DENYLIST
                                   # but Factory does NOT validate that — it just merges. The test
                                   # confirms merge behavior; AgentPolicy enforces the subset rule.
    )
    profile = f._resolve_profile(spec)
    # spec.tool_denylist entries appear in exclude_tool_names
    assert "browse" in profile.exclude_tool_names
    # DYNAMIC_AGENT_DENYLIST also merged
    assert "send_message" in profile.exclude_tool_names
    assert "create_agent" in profile.exclude_tool_names


def test_resolve_profile_for_builtin_does_not_merge_dynamic_denylist():
    """Builtins keep their RuntimeProfile unmodified (no DYNAMIC_AGENT_DENYLIST)."""
    f = _factory()
    spec = AgentSpec(
        name="researcher", kind="builtin",
        runtime_profile="agent_researcher",
    )
    profile = f._resolve_profile(spec)
    # send_message must NOT be in exclude_tool_names (builtins are trusted)
    # Note: AGENT_RESEARCHER_PROFILE may have its own exclude_tool_names — test
    # only that DYNAMIC_AGENT_DENYLIST entries are NOT added by Factory.
    assert "send_message" not in profile.exclude_tool_names


def test_dynamic_agent_workspace_dir_created(tmp_path, monkeypatch):
    """Per blueprint §12, dynamic agents get cwd /tmp/lapwing/agents/{id}/."""
    f = _factory()
    spec = AgentSpec(
        name="x", kind="dynamic",
        runtime_profile="agent_researcher",
    )
    f.create(spec)
    # Workspace directory should exist after create
    assert os.path.isdir(f"/tmp/lapwing/agents/{spec.id}")


def test_unknown_runtime_profile_raises():
    """Unknown profile name should raise (caller must provide valid profile)."""
    f = _factory()
    spec = AgentSpec(
        name="x", kind="dynamic",
        runtime_profile="nonexistent_profile",
    )
    with pytest.raises(Exception):  # ValueError from get_runtime_profile
        f.create(spec)
