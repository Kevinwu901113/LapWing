"""Tests for src.agents.spec — AgentSpec data model + denylist constants."""

from __future__ import annotations

from datetime import datetime

from src.agents.spec import (
    ALLOWED_DYNAMIC_PROFILES,
    ALLOWED_MODEL_SLOTS,
    DYNAMIC_AGENT_DENYLIST,
    AgentLifecyclePolicy,
    AgentResourceLimits,
    AgentSpec,
)


def test_defaults():
    s = AgentSpec(name="x", system_prompt="p", runtime_profile="agent_researcher")
    assert s.kind == "dynamic"
    assert s.lifecycle.mode == "ephemeral"
    assert s.lifecycle.ttl_seconds == 3600
    assert s.lifecycle.max_runs == 1
    assert s.lifecycle.reusable is False
    assert s.resource_limits.max_tool_calls == 20
    assert s.resource_limits.max_llm_calls == 8
    assert s.resource_limits.max_tokens == 30000
    assert s.resource_limits.max_wall_time_seconds == 180
    assert s.resource_limits.max_child_agents == 0
    assert s.id.startswith("agent_") and len(s.id) == 18
    assert s.version == 1
    assert s.status == "active"
    assert s.created_by == "brain"
    assert isinstance(s.created_at, datetime)
    assert isinstance(s.updated_at, datetime)
    # tz-aware (from src.core.time_utils.now)
    assert s.created_at.tzinfo is not None


def test_id_uniqueness():
    a = AgentSpec(name="x")
    b = AgentSpec(name="x")
    assert a.id != b.id
    assert a.id.startswith("agent_") and len(a.id) == 18
    assert b.id.startswith("agent_") and len(b.id) == 18


def test_spec_hash_deterministic_and_order_invariant():
    a = AgentSpec(name="x", tool_denylist=["a", "b"])
    b = AgentSpec(name="x", tool_denylist=["b", "a"])
    # name + tool_denylist contribute; everything else default
    assert a.spec_hash() == b.spec_hash()


def test_spec_hash_changes_on_prompt_change():
    a = AgentSpec(name="x", system_prompt="p1")
    b = AgentSpec(name="x", system_prompt="p2")
    assert a.spec_hash() != b.spec_hash()


def test_spec_hash_changes_on_model_slot_change():
    a = AgentSpec(name="x", model_slot="agent_researcher")
    b = AgentSpec(name="x", model_slot="agent_coder")
    assert a.spec_hash() != b.spec_hash()


def test_spec_hash_changes_on_runtime_profile_change():
    a = AgentSpec(name="x", runtime_profile="agent_researcher")
    b = AgentSpec(name="x", runtime_profile="agent_coder")
    assert a.spec_hash() != b.spec_hash()


def test_spec_hash_changes_on_tool_denylist_membership():
    a = AgentSpec(name="x", tool_denylist=["a", "b"])
    b = AgentSpec(name="x", tool_denylist=["a", "c"])
    assert a.spec_hash() != b.spec_hash()


def test_spec_hash_changes_on_resource_limits():
    base = AgentSpec(name="x")
    for field_name, new_value in [
        ("max_tool_calls", 999),
        ("max_llm_calls", 999),
        ("max_tokens", 999),
        ("max_wall_time_seconds", 999),
        ("max_child_agents", 999),
    ]:
        limits = AgentResourceLimits()
        setattr(limits, field_name, new_value)
        other = AgentSpec(name="x", resource_limits=limits)
        assert base.spec_hash() != other.spec_hash(), field_name


def test_spec_hash_stable_across_unrelated_field_changes():
    # Fields that should NOT contribute to spec_hash:
    # id, display_name, description, kind, version, status,
    # lifecycle, created_by, created_reason, created_at, updated_at
    a = AgentSpec(
        name="x",
        system_prompt="p",
        display_name="Display A",
        description="desc A",
        created_by="brain",
        created_reason="reason A",
    )
    b = AgentSpec(
        name="x",
        system_prompt="p",
        display_name="Display B",
        description="desc B",
        created_by="user",
        created_reason="reason B",
    )
    assert a.spec_hash() == b.spec_hash()


def test_constants_exact_members():
    assert ALLOWED_MODEL_SLOTS == frozenset({
        "agent_researcher", "agent_coder", "lightweight_judgment",
    })
    assert ALLOWED_DYNAMIC_PROFILES == frozenset({
        "agent_researcher", "agent_coder",
    })
    # spot-check denylist coverage required by T-14
    for t in [
        "create_agent", "destroy_agent", "save_agent", "delegate_to_agent",
        "delegate_to_researcher", "delegate_to_coder",
        "send_message", "send_image", "proactive_send",
        "memory_note", "edit_soul", "edit_voice", "add_correction",
        "commit_promise", "fulfill_promise", "abandon_promise",
        "set_reminder", "cancel_reminder",
        "plan_task", "update_plan",
        "close_focus", "recall_focus",
    ]:
        assert t in DYNAMIC_AGENT_DENYLIST, t


def test_constants_are_frozensets():
    assert isinstance(ALLOWED_MODEL_SLOTS, frozenset)
    assert isinstance(ALLOWED_DYNAMIC_PROFILES, frozenset)
    assert isinstance(DYNAMIC_AGENT_DENYLIST, frozenset)


def test_lifecycle_and_limits_defaults_standalone():
    lp = AgentLifecyclePolicy()
    assert lp.mode == "ephemeral"
    assert lp.ttl_seconds == 3600
    assert lp.max_runs == 1
    assert lp.reusable is False

    rl = AgentResourceLimits()
    assert rl.max_tool_calls == 20
    assert rl.max_llm_calls == 8
    assert rl.max_tokens == 30000
    assert rl.max_wall_time_seconds == 180
    assert rl.max_child_agents == 0
