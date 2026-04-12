"""Tests for AgentRegistry."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.core.agent_registry import AgentCapability, AgentRegistration, AgentRegistry


def make_agent(name: str) -> MagicMock:
    agent = MagicMock()
    agent.name = name
    return agent


def make_cap(name: str, tools: list[str] | None = None) -> AgentCapability:
    return AgentCapability(name=name, description=f"{name} capability", tools_required=tools or [])


# ── 1. Register and get ──────────────────────────────────────────────────────

def test_register_and_get():
    registry = AgentRegistry()
    agent = make_agent("alpha")
    caps = [make_cap("search")]
    registry.register(agent, caps)

    reg = registry.get("alpha")
    assert reg is not None
    assert reg.agent is agent
    assert reg.status == "idle"
    assert len(reg.capabilities) == 1


# ── 2. Register replaces existing ────────────────────────────────────────────

def test_register_replaces_existing():
    registry = AgentRegistry()
    a1 = make_agent("alpha")
    a2 = make_agent("alpha")
    registry.register(a1, [make_cap("cap1")])
    registry.register(a2, [make_cap("cap2")])

    reg = registry.get("alpha")
    assert reg.agent is a2
    assert reg.capabilities[0].name == "cap2"


# ── 3. Get nonexistent returns None ──────────────────────────────────────────

def test_get_nonexistent_returns_none():
    registry = AgentRegistry()
    assert registry.get("ghost") is None


# ── 4. Unregister existing ────────────────────────────────────────────────────

def test_unregister_existing():
    registry = AgentRegistry()
    registry.register(make_agent("alpha"), [make_cap("cap1")])
    registry.unregister("alpha")
    assert registry.get("alpha") is None


# ── 5. Unregister nonexistent does not raise ──────────────────────────────────

def test_unregister_nonexistent_no_error():
    registry = AgentRegistry()
    # Should not raise
    registry.unregister("ghost")


# ── 6. find_by_capability ─────────────────────────────────────────────────────

def test_find_by_capability():
    registry = AgentRegistry()
    registry.register(make_agent("alpha"), [make_cap("search"), make_cap("browse")])
    registry.register(make_agent("beta"), [make_cap("code")])

    results = registry.find_by_capability("search")
    assert len(results) == 1
    assert results[0].agent.name == "alpha"


# ── 7. find_by_capability skips disabled ──────────────────────────────────────

def test_find_by_capability_skips_disabled():
    registry = AgentRegistry()
    registry.register(make_agent("alpha"), [make_cap("search")])
    registry.set_status("alpha", "disabled")

    results = registry.find_by_capability("search")
    assert results == []


# ── 8. find_best_for_task prefers idle ────────────────────────────────────────

def test_find_best_for_task_prefers_idle():
    registry = AgentRegistry()
    registry.register(make_agent("busy"), [make_cap("search")])
    registry.register(make_agent("idle_one"), [make_cap("search")])
    registry.set_status("busy", "busy", command_id="cmd-1")

    best = registry.find_best_for_task("search something")
    assert best is not None
    assert best.agent.name == "idle_one"


# ── 9. find_best_for_task skips disabled and error ────────────────────────────

def test_find_best_for_task_skips_disabled_and_error():
    registry = AgentRegistry()
    registry.register(make_agent("alpha"), [make_cap("search")])
    registry.register(make_agent("beta"), [make_cap("search")])
    registry.set_status("alpha", "disabled")
    registry.set_status("beta", "error")

    best = registry.find_best_for_task("any task")
    assert best is None


# ── 10. find_best_for_task filters by required_tools ──────────────────────────

def test_find_best_for_task_filters_by_required_tools():
    registry = AgentRegistry()
    registry.register(make_agent("alpha"), [make_cap("search", tools=["web_search"])])
    registry.register(make_agent("beta"), [make_cap("code", tools=["shell_exec", "file_write"])])

    best = registry.find_best_for_task("run code", required_tools=["shell_exec"])
    assert best is not None
    assert best.agent.name == "beta"


# ── 11. find_best_for_task returns None when no match ─────────────────────────

def test_find_best_for_task_returns_none_when_no_match():
    registry = AgentRegistry()
    registry.register(make_agent("alpha"), [make_cap("search", tools=["web_search"])])

    best = registry.find_best_for_task("task", required_tools=["shell_exec"])
    assert best is None


# ── 12. set_status updates ────────────────────────────────────────────────────

def test_set_status_updates():
    registry = AgentRegistry()
    registry.register(make_agent("alpha"), [make_cap("search")])
    registry.set_status("alpha", "busy", command_id="cmd-42")

    reg = registry.get("alpha")
    assert reg.status == "busy"
    assert reg.current_command_id == "cmd-42"


# ── 13. set_status nonexistent does not raise ─────────────────────────────────

def test_set_status_nonexistent_no_error():
    registry = AgentRegistry()
    # Should not raise
    registry.set_status("ghost", "busy", command_id="x")


# ── 14. list_agents structure ─────────────────────────────────────────────────

def test_list_agents():
    registry = AgentRegistry()
    registry.register(make_agent("alpha"), [make_cap("search"), make_cap("browse")])
    registry.register(make_agent("beta"), [make_cap("code")])
    registry.set_status("beta", "busy", command_id="cmd-7")

    agents = registry.list_agents()
    assert len(agents) == 2

    by_name = {a["name"]: a for a in agents}
    assert set(by_name["alpha"]["capabilities"]) == {"search", "browse"}
    assert by_name["alpha"]["status"] == "idle"
    assert by_name["alpha"]["current_command_id"] is None

    assert by_name["beta"]["status"] == "busy"
    assert by_name["beta"]["current_command_id"] == "cmd-7"


# ── 15. available_count ───────────────────────────────────────────────────────

def test_available_count():
    registry = AgentRegistry()
    registry.register(make_agent("a"), [make_cap("cap")])
    registry.register(make_agent("b"), [make_cap("cap")])
    registry.register(make_agent("c"), [make_cap("cap")])
    registry.set_status("b", "disabled")
    registry.set_status("c", "error")

    assert registry.available_count == 1
