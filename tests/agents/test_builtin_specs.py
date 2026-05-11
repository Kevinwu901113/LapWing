"""Builtin AgentSpec definitions (Blueprint §8)."""

from src.agents.builtin_specs import (
    builtin_coder_spec,
    builtin_researcher_spec,
    all_builtin_specs,
)
from src.agents.spec import AgentSpec


def test_builtin_researcher_spec_shape():
    spec = builtin_researcher_spec()
    assert isinstance(spec, AgentSpec)
    assert spec.id == "builtin_researcher"
    assert spec.name == "researcher"
    assert spec.kind == "builtin"
    assert spec.system_prompt == ""  # builtin classmethod generates the prompt
    assert spec.runtime_profile == "agent_researcher"
    assert spec.model_slot == "agent_researcher"
    assert spec.lifecycle.mode == "persistent"
    assert spec.lifecycle.ttl_seconds is None
    assert spec.lifecycle.max_runs is None
    assert spec.resource_limits.max_tool_calls == 30
    assert spec.resource_limits.max_llm_calls == 15
    assert spec.resource_limits.max_wall_time_seconds == 300


def test_builtin_coder_spec_shape():
    spec = builtin_coder_spec()
    assert isinstance(spec, AgentSpec)
    assert spec.id == "builtin_coder"
    assert spec.name == "coder"
    assert spec.kind == "builtin"
    assert spec.system_prompt == ""
    assert spec.runtime_profile == "agent_coder"
    assert spec.model_slot == "agent_coder"
    assert spec.lifecycle.mode == "persistent"
    assert spec.resource_limits.max_tool_calls == 40
    assert spec.resource_limits.max_llm_calls == 20
    assert spec.resource_limits.max_wall_time_seconds == 600


def test_factories_return_fresh_instances():
    """Each call returns a new instance — no shared mutable state across
    consumers (Catalog vs StateView snapshot)."""
    a = builtin_researcher_spec()
    b = builtin_researcher_spec()
    assert a is not b


def test_all_builtin_specs_returns_all_kinds():
    """v1 blueprint §16 O-1 / Slice I.1: resident_operator is now a
    registered builtin agent kind alongside researcher and coder."""
    specs = all_builtin_specs()
    names = {s.name for s in specs}
    assert names == {"researcher", "coder", "resident_operator"}
