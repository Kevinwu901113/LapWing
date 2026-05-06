"""Phase 6A — AgentSpec capability metadata: defaults, serialization, spec_hash."""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from src.agents.spec import (
    MAX_DELEGATION_DEPTH,
    VALID_APPROVAL_STATES,
    VALID_CAPABILITY_BINDING_MODES,
    VALID_RISK_LEVELS,
    AgentSpec,
)
from src.agents.types import LegacyAgentSpec


# ── Constants ────────────────────────────────────────────────────────────

def test_valid_risk_levels_exact():
    assert VALID_RISK_LEVELS == frozenset({"low", "medium", "high"})


def test_valid_approval_states_exact():
    assert VALID_APPROVAL_STATES == frozenset({
        "not_required", "pending", "approved", "rejected",
    })


def test_valid_capability_binding_modes_exact():
    assert VALID_CAPABILITY_BINDING_MODES == frozenset({
        "metadata_only", "advisory", "enforced",
    })


def test_max_delegation_depth():
    assert MAX_DELEGATION_DEPTH == 3


# ── Defaults ─────────────────────────────────────────────────────────────

def test_new_agent_spec_defaults_are_safe():
    s = AgentSpec(name="x", system_prompt="p", runtime_profile="agent_researcher")
    assert s.bound_capabilities == []
    assert s.memory_scope is None
    assert s.risk_level == "low"
    assert s.eval_tasks == []
    assert s.success_count == 0
    assert s.failure_count == 0
    assert s.approval_state == "not_required"
    assert s.allowed_delegation_depth == 0
    assert s.capability_binding_mode == "metadata_only"


def test_defaults_do_not_change_existing_fields():
    s = AgentSpec(name="x", system_prompt="p")
    assert s.kind == "dynamic"
    assert s.version == 1
    assert s.status == "active"
    assert s.lifecycle.mode == "ephemeral"
    assert s.resource_limits.max_tool_calls == 20


# ── Field round-trip ──────────────────────────────────────────────────────

def test_bound_capabilities_round_trip():
    s = AgentSpec(name="x", system_prompt="p", bound_capabilities=["shell_v1", "web_v1"])
    assert s.bound_capabilities == ["shell_v1", "web_v1"]


def test_runtime_profile_round_trip():
    s = AgentSpec(name="x", system_prompt="p", runtime_profile="agent_coder")
    assert s.runtime_profile == "agent_coder"


def test_risk_level_round_trip():
    s = AgentSpec(name="x", system_prompt="p", risk_level="medium")
    assert s.risk_level == "medium"


def test_approval_state_round_trip():
    s = AgentSpec(name="x", system_prompt="p", approval_state="approved")
    assert s.approval_state == "approved"


def test_allowed_delegation_depth_round_trip():
    s = AgentSpec(name="x", system_prompt="p", allowed_delegation_depth=2)
    assert s.allowed_delegation_depth == 2


def test_capability_binding_mode_round_trip():
    s = AgentSpec(name="x", system_prompt="p", capability_binding_mode="advisory")
    assert s.capability_binding_mode == "advisory"


def test_memory_scope_round_trip():
    s = AgentSpec(name="x", system_prompt="p", memory_scope="workspace")
    assert s.memory_scope == "workspace"


def test_eval_tasks_round_trip():
    tasks = [{"id": "t1", "status": "pending"}]
    s = AgentSpec(name="x", system_prompt="p", eval_tasks=tasks)
    assert s.eval_tasks == tasks


def test_success_and_failure_count_round_trip():
    s = AgentSpec(name="x", system_prompt="p", success_count=5, failure_count=2)
    assert s.success_count == 5
    assert s.failure_count == 2


# ── spec_hash behavior ────────────────────────────────────────────────────

def test_spec_hash_includes_bound_capabilities():
    a = AgentSpec(name="x", system_prompt="p", bound_capabilities=["a"])
    b = AgentSpec(name="x", system_prompt="p", bound_capabilities=["b"])
    assert a.spec_hash() != b.spec_hash()


def test_spec_hash_includes_risk_level():
    a = AgentSpec(name="x", system_prompt="p", risk_level="low")
    b = AgentSpec(name="x", system_prompt="p", risk_level="high")
    assert a.spec_hash() != b.spec_hash()


def test_spec_hash_includes_approval_state():
    a = AgentSpec(name="x", system_prompt="p", approval_state="not_required")
    b = AgentSpec(name="x", system_prompt="p", approval_state="approved")
    assert a.spec_hash() != b.spec_hash()


def test_spec_hash_includes_capability_binding_mode():
    a = AgentSpec(name="x", system_prompt="p", capability_binding_mode="metadata_only")
    b = AgentSpec(name="x", system_prompt="p", capability_binding_mode="advisory")
    assert a.spec_hash() != b.spec_hash()


def test_spec_hash_includes_memory_scope():
    a = AgentSpec(name="x", system_prompt="p", memory_scope=None)
    b = AgentSpec(name="x", system_prompt="p", memory_scope="workspace")
    assert a.spec_hash() != b.spec_hash()


def test_spec_hash_includes_allowed_delegation_depth():
    a = AgentSpec(name="x", system_prompt="p", allowed_delegation_depth=0)
    b = AgentSpec(name="x", system_prompt="p", allowed_delegation_depth=2)
    assert a.spec_hash() != b.spec_hash()


def test_spec_hash_excludes_success_count():
    a = AgentSpec(name="x", system_prompt="p", success_count=0)
    b = AgentSpec(name="x", system_prompt="p", success_count=999)
    assert a.spec_hash() == b.spec_hash()


def test_spec_hash_excludes_failure_count():
    a = AgentSpec(name="x", system_prompt="p", failure_count=0)
    b = AgentSpec(name="x", system_prompt="p", failure_count=999)
    assert a.spec_hash() == b.spec_hash()


def test_spec_hash_excludes_eval_tasks():
    a = AgentSpec(name="x", system_prompt="p", eval_tasks=[])
    b = AgentSpec(name="x", system_prompt="p", eval_tasks=[{"id": "t1"}])
    assert a.spec_hash() == b.spec_hash()


def test_spec_hash_deterministic_with_new_fields():
    a = AgentSpec(
        name="x", system_prompt="p",
        bound_capabilities=["b", "a"],
        risk_level="medium",
    )
    b = AgentSpec(
        name="x", system_prompt="p",
        bound_capabilities=["a", "b"],  # sorted → same
        risk_level="medium",
    )
    assert a.spec_hash() == b.spec_hash()


# ── Legacy compatibility ──────────────────────────────────────────────────

def test_legacy_agent_spec_still_usable():
    spec = LegacyAgentSpec(
        name="legacy_test",
        description="legacy agent",
        system_prompt="legacy prompt",
        model_slot="test_slot",
        tools=["tool_a"],
    )
    assert spec.name == "legacy_test"
    assert spec.tools == ["tool_a"]


def test_legacy_spec_has_no_capability_fields():
    """LegacyAgentSpec does not have Phase 6A fields — it's a separate class."""
    spec = LegacyAgentSpec(
        name="test",
        description="d",
        system_prompt="p",
        model_slot="ms",
    )
    assert not hasattr(spec, "bound_capabilities")


# ── JSON serialization ────────────────────────────────────────────────────

def test_json_round_trip_preserves_new_fields():
    s = AgentSpec(
        name="x",
        system_prompt="p",
        runtime_profile="agent_researcher",
        bound_capabilities=["shell_v1"],
        risk_level="medium",
        approval_state="pending",
        allowed_delegation_depth=1,
        capability_binding_mode="advisory",
        memory_scope="session",
        success_count=3,
        failure_count=1,
        eval_tasks=[{"id": "t1"}],
    )
    data = asdict(s)
    js = json.dumps(data, default=str, ensure_ascii=False)
    raw = json.loads(js)

    assert raw["bound_capabilities"] == ["shell_v1"]
    assert raw["risk_level"] == "medium"
    assert raw["approval_state"] == "pending"
    assert raw["allowed_delegation_depth"] == 1
    assert raw["capability_binding_mode"] == "advisory"
    assert raw["memory_scope"] == "session"
    assert raw["success_count"] == 3
    assert raw["failure_count"] == 1
    assert raw["eval_tasks"] == [{"id": "t1"}]


def test_json_deserialization_with_missing_new_fields_uses_defaults():
    """Simulate loading a legacy spec JSON that lacks Phase 6A fields."""
    legacy_json = {
        "id": "agent_test1234",
        "name": "legacy_agent",
        "display_name": "",
        "description": "",
        "kind": "dynamic",
        "version": 1,
        "status": "active",
        "system_prompt": "hello",
        "model_slot": "agent_researcher",
        "runtime_profile": "agent_researcher",
        "tool_denylist": [],
        "lifecycle": {"mode": "ephemeral", "ttl_seconds": 3600, "max_runs": 1, "reusable": False},
        "resource_limits": {
            "max_tool_calls": 20, "max_llm_calls": 8,
            "max_tokens": 30000, "max_wall_time_seconds": 180,
            "max_child_agents": 0,
        },
        "created_by": "brain",
        "created_reason": "",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    from src.agents.spec import AgentLifecyclePolicy, AgentResourceLimits
    from datetime import datetime

    lifecycle = AgentLifecyclePolicy(**legacy_json.pop("lifecycle"))
    limits = AgentResourceLimits(**legacy_json.pop("resource_limits"))
    created_at = datetime.fromisoformat(legacy_json.pop("created_at"))
    updated_at = datetime.fromisoformat(legacy_json.pop("updated_at"))
    legacy_json.pop("updated_at", None)

    spec = AgentSpec(
        **legacy_json,
        lifecycle=lifecycle,
        resource_limits=limits,
        created_at=created_at,
        updated_at=updated_at,
    )

    assert spec.name == "legacy_agent"
    assert spec.bound_capabilities == []
    assert spec.memory_scope is None
    assert spec.risk_level == "low"
    assert spec.approval_state == "not_required"
    assert spec.allowed_delegation_depth == 0
    assert spec.capability_binding_mode == "metadata_only"


# ── Unknown extra field behavior ──────────────────────────────────────────

def test_unknown_extra_field_raises_type_error():
    """Extra unknown keys in kwargs raise TypeError — unchanged behavior."""
    with pytest.raises(TypeError):
        AgentSpec(name="x", system_prompt="p", nonexistent_field="boom")
