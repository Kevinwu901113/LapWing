"""AgentRegistry v2 — Catalog + Factory + Policy facade (Blueprint §6)."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from src.agents.registry import AgentRegistry, _SessionEntry
from src.agents.catalog import AgentCatalog
from src.agents.factory import AgentFactory
from src.agents.policy import AgentPolicy, CreateAgentInput, LintResult
from src.agents.spec import AgentSpec, AgentLifecyclePolicy


def _safe_lint():
    return LintResult(verdict="safe", reason="ok")


async def _make_registry(tmp_path, monkeypatch):
    cat = AgentCatalog(tmp_path / "x.db"); await cat.init()
    factory = MagicMock()
    # Factory returns a fresh MagicMock per call to verify "fresh runtime" behavior.
    factory.create = MagicMock(side_effect=lambda spec: MagicMock(spec_obj=spec, dynamic_spec=spec))
    policy = AgentPolicy(cat)
    policy._semantic_lint = AsyncMock(return_value=_safe_lint())
    reg = AgentRegistry(cat, factory, policy)
    return reg, cat, factory, policy


# ── init: builtin specs upserted ──

@pytest.mark.asyncio
async def test_init_upserts_builtin_specs(tmp_path, monkeypatch):
    reg, cat, _, _ = await _make_registry(tmp_path, monkeypatch)
    await reg.init()
    researcher = await cat.get_by_name("researcher")
    coder = await cat.get_by_name("coder")
    assert researcher is not None and researcher.kind == "builtin"
    assert coder is not None and coder.kind == "builtin"


# ── create_agent: ephemeral and session paths ──

@pytest.mark.asyncio
async def test_create_ephemeral_agent_not_persisted(tmp_path, monkeypatch):
    reg, cat, _, _ = await _make_registry(tmp_path, monkeypatch)
    await reg.init()
    spec = await reg.create_agent(
        CreateAgentInput(name_hint="probe", purpose="x", instructions="y",
                         profile="agent_researcher", model_slot="agent_researcher",
                         lifecycle="ephemeral", max_runs=1, ttl_seconds=3600),
        ctx=MagicMock(),
    )
    assert spec.lifecycle.mode == "ephemeral"
    # NOT in catalog
    assert await cat.get_by_name(spec.name) is None
    # IS in registry's ephemeral dict
    assert spec.name in reg._ephemeral_agents


@pytest.mark.asyncio
async def test_create_session_agent_in_session_dict(tmp_path, monkeypatch):
    reg, cat, _, _ = await _make_registry(tmp_path, monkeypatch)
    await reg.init()
    spec = await reg.create_agent(
        CreateAgentInput(name_hint="sess", purpose="x", instructions="y",
                         profile="agent_researcher", model_slot="agent_researcher",
                         lifecycle="session", max_runs=5, ttl_seconds=600),
        ctx=MagicMock(),
    )
    assert spec.lifecycle.mode == "session"
    assert await cat.get_by_name(spec.name) is None
    assert spec.name in reg._session_agents


# ── T-09: session agent fresh runtime ──

@pytest.mark.asyncio
async def test_t09_session_agent_fresh_runtime(tmp_path, monkeypatch):
    reg, cat, factory, _ = await _make_registry(tmp_path, monkeypatch)
    await reg.init()
    spec = await reg.create_agent(
        CreateAgentInput(name_hint="sess", purpose="x", instructions="y",
                         profile="agent_researcher", model_slot="agent_researcher",
                         lifecycle="session"),
        ctx=MagicMock(),
    )
    inst1 = await reg.get_or_create_instance(spec.name)
    inst2 = await reg.get_or_create_instance(spec.name)
    # Two distinct instances (fresh runtime per delegation)
    assert inst1 is not inst2
    # Factory called twice
    assert factory.create.call_count == 2


# ── T-10: persistent agent only spec persisted ──

@pytest.mark.asyncio
async def test_t10_save_agent_persists_spec_only(tmp_path, monkeypatch):
    reg, cat, factory, _ = await _make_registry(tmp_path, monkeypatch)
    await reg.init()
    spec = await reg.create_agent(
        CreateAgentInput(name_hint="keeper", purpose="x", instructions="y",
                         profile="agent_researcher", model_slot="agent_researcher",
                         lifecycle="session"),
        ctx=MagicMock(),
    )
    await reg.save_agent(spec.name, reason="useful", run_history=["t1"])
    # In catalog now
    saved = await cat.get_by_name(spec.name)
    assert saved is not None
    assert saved.lifecycle.mode == "persistent"
    # Removed from session/ephemeral dicts
    assert spec.name not in reg._session_agents
    assert spec.name not in reg._ephemeral_agents
    # Re-fetching builds a fresh instance from catalog spec
    inst = await reg.get_or_create_instance(spec.name)
    assert inst is not None


# ── builtin lookup ──

@pytest.mark.asyncio
async def test_get_or_create_instance_for_builtin(tmp_path, monkeypatch):
    reg, cat, factory, _ = await _make_registry(tmp_path, monkeypatch)
    await reg.init()
    inst = await reg.get_or_create_instance("researcher")
    assert inst is not None
    factory.create.assert_called()


@pytest.mark.asyncio
async def test_get_or_create_returns_none_when_unknown(tmp_path, monkeypatch):
    reg, cat, factory, _ = await _make_registry(tmp_path, monkeypatch)
    await reg.init()
    inst = await reg.get_or_create_instance("nonexistent")
    assert inst is None


# ── destroy_agent ──

@pytest.mark.asyncio
async def test_destroy_dynamic_agent(tmp_path, monkeypatch):
    reg, cat, _, _ = await _make_registry(tmp_path, monkeypatch)
    await reg.init()
    spec = await reg.create_agent(
        CreateAgentInput(name_hint="goner", purpose="x", instructions="y",
                         profile="agent_researcher", model_slot="agent_researcher",
                         lifecycle="ephemeral"),
        ctx=MagicMock(),
    )
    ok = await reg.destroy_agent(spec.name)
    assert ok is True
    assert spec.name not in reg._ephemeral_agents


@pytest.mark.asyncio
async def test_destroy_builtin_forbidden(tmp_path, monkeypatch):
    reg, cat, _, _ = await _make_registry(tmp_path, monkeypatch)
    await reg.init()
    ok = await reg.destroy_agent("researcher")
    assert ok is False


# ── list_agents ──

@pytest.mark.asyncio
async def test_list_agents_compact(tmp_path, monkeypatch):
    reg, cat, _, _ = await _make_registry(tmp_path, monkeypatch)
    await reg.init()
    items = await reg.list_agents()
    names = {it["name"] for it in items}
    assert "researcher" in names
    assert "coder" in names
    # Compact mode does NOT include system_prompt
    for it in items:
        assert "system_prompt" not in it


@pytest.mark.asyncio
async def test_list_agents_full_no_full_prompt(tmp_path, monkeypatch):
    reg, cat, _, _ = await _make_registry(tmp_path, monkeypatch)
    await reg.init()
    items = await reg.list_agents(full=True)
    # Full mode includes a TRUNCATED prompt preview, not the full prompt
    for it in items:
        if "system_prompt_preview" in it:
            assert len(it["system_prompt_preview"]) <= 200


# ── render_agent_summary_for_stateview (sync) ──

@pytest.mark.asyncio
async def test_render_agent_summary_includes_builtins(tmp_path, monkeypatch):
    reg, cat, _, _ = await _make_registry(tmp_path, monkeypatch)
    await reg.init()
    summary = reg.render_agent_summary_for_stateview()
    assert "researcher" in summary
    assert "coder" in summary
    assert "可用 Agent" in summary or "Available Agents" in summary


# ── cleanup_expired_sessions ──

@pytest.mark.asyncio
async def test_cleanup_removes_expired_sessions(tmp_path, monkeypatch):
    import time
    reg, cat, _, _ = await _make_registry(tmp_path, monkeypatch)
    await reg.init()
    spec = await reg.create_agent(
        CreateAgentInput(name_hint="ephemerald", purpose="x", instructions="y",
                         profile="agent_researcher", model_slot="agent_researcher",
                         lifecycle="session", ttl_seconds=1),
        ctx=MagicMock(),
    )
    # backdate the entry
    entry = reg._session_agents[spec.name]
    entry.last_used_at = time.monotonic() - 10  # 10s ago, ttl=1s → expired
    cleaned = await reg.cleanup_expired_sessions()
    assert cleaned == 1
    assert spec.name not in reg._session_agents


# ── Backwards-compat: legacy zero-arg constructor + register/get/list_names ──

def test_backwards_compat_legacy_register_and_get():
    """The 4 existing tests in tests/agents/test_registry.py rely on this API."""
    reg = AgentRegistry()  # zero-arg
    fake_agent = MagicMock()
    fake_agent.spec.name = "x"
    fake_agent.spec.description = "x agent"
    reg.register("x", fake_agent)
    assert reg.get("x") is fake_agent
    assert "x" in reg.list_names()
    assert any(s["name"] == "x" for s in reg.list_specs())
