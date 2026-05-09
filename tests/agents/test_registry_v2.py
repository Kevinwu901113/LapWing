"""AgentRegistry v2 — Catalog + Factory + Policy facade (Blueprint §6)."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from src.agents.exceptions import AgentSpawnError
from src.agents.registry import AgentRegistry, _SessionEntry
from src.agents.catalog import AgentCatalog
from src.agents.factory import AgentFactory
from src.agents.policy import AgentPolicy, AgentPolicyViolation, CreateAgentInput, LintResult
from src.agents.spec import AgentSpec, AgentLifecyclePolicy


def _safe_lint():
    return LintResult(verdict="safe", reason="ok")


def _base_services():
    return {
        "dispatcher": object(),
        "tool_dispatcher": object(),
        "tool_registry": object(),
        "llm_router": object(),
    }


def _researcher_services():
    return {
        **_base_services(),
        "research_engine": object(),
        "ambient_store": object(),
    }


async def _make_registry(tmp_path, monkeypatch):
    cat = AgentCatalog(tmp_path / "x.db"); await cat.init()
    factory = MagicMock()
    # Factory returns a fresh MagicMock per call to verify "fresh runtime" behavior.
    factory.create = MagicMock(side_effect=lambda spec, services_override=None: MagicMock(spec_obj=spec, dynamic_spec=spec))
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
    services = _base_services()
    inst1 = await reg.get_or_create_instance(spec.name, services_override=services)
    inst2 = await reg.get_or_create_instance(spec.name, services_override=services)
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
    inst = await reg.get_or_create_instance(
        spec.name,
        services_override=_base_services(),
    )
    assert inst is not None


# ── builtin lookup ──

@pytest.mark.asyncio
async def test_get_or_create_instance_for_builtin(tmp_path, monkeypatch):
    reg, cat, factory, _ = await _make_registry(tmp_path, monkeypatch)
    await reg.init()
    services = _researcher_services()
    inst = await reg.get_or_create_instance("researcher", services_override=services)
    assert inst is not None
    factory.create.assert_called()
    _, kwargs = factory.create.call_args
    assert kwargs["services_override"] is services


@pytest.mark.asyncio
async def test_get_or_create_instance_missing_services_raises(tmp_path, monkeypatch):
    reg, _, factory, _ = await _make_registry(tmp_path, monkeypatch)
    await reg.init()

    with pytest.raises(AgentSpawnError) as exc_info:
        await reg.get_or_create_instance(
            "researcher",
            services_override={"llm_router": object()},
        )

    assert exc_info.value.missing_services == (
        "dispatcher",
        "tool_dispatcher",
        "tool_registry",
        "research_engine",
        "ambient_store",
    )
    factory.create.assert_not_called()


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
        # Compact fields must be present
        for key in ("name", "kind", "lifecycle_mode", "status", "description",
                    "runtime_profile", "model_slot"):
            assert key in it, f"missing compact key '{key}' in {it['name']}"


@pytest.mark.asyncio
async def test_list_agents_full_no_full_prompt(tmp_path, monkeypatch):
    reg, cat, _, _ = await _make_registry(tmp_path, monkeypatch)
    await reg.init()
    items = await reg.list_agents(full=True)
    # Full mode includes a TRUNCATED prompt preview, not the full prompt
    for it in items:
        if "system_prompt_preview" in it:
            assert len(it["system_prompt_preview"]) <= 200


@pytest.mark.asyncio
async def test_list_agents_compact_includes_model_slot(tmp_path, monkeypatch):
    """Compact output must include model_slot for every agent."""
    reg, cat, _, _ = await _make_registry(tmp_path, monkeypatch)
    await reg.init()
    items = await reg.list_agents()
    for it in items:
        assert "model_slot" in it, f"missing model_slot in {it['name']}"
        assert isinstance(it["model_slot"], str)


@pytest.mark.asyncio
async def test_list_agents_include_inactive(tmp_path, monkeypatch):
    """include_inactive=True returns archived agents; default does not."""
    reg, cat, _, _ = await _make_registry(tmp_path, monkeypatch)
    await reg.init()

    # Archive the coder builtin
    coder = await cat.get_by_name("coder")
    assert coder is not None
    await cat.archive(coder.id)

    # Default (include_inactive=False): only active agents
    active_items = await reg.list_agents()
    active_names = {it["name"] for it in active_items}
    assert "researcher" in active_names
    assert "coder" not in active_names

    # include_inactive=True: archived agents appear too
    all_items = await reg.list_agents(include_inactive=True)
    all_names = {it["name"] for it in all_items}
    assert "researcher" in all_names
    assert "coder" in all_names


@pytest.mark.asyncio
async def test_list_agents_catalog_error_fails_closed(tmp_path, monkeypatch):
    """When catalog.list_specs raises, the error propagates (fail-closed)."""
    reg, cat, _, _ = await _make_registry(tmp_path, monkeypatch)
    await reg.init()
    cat.list_specs = AsyncMock(side_effect=RuntimeError("db corruption"))
    with pytest.raises(RuntimeError, match="db corruption"):
        await reg.list_agents()


# ── render_agent_summary_for_stateview (sync) ──

@pytest.mark.asyncio
async def test_render_agent_summary_includes_builtins(tmp_path, monkeypatch):
    reg, cat, _, _ = await _make_registry(tmp_path, monkeypatch)
    await reg.init()
    summary = reg.render_agent_summary_for_stateview()
    assert "researcher" in summary
    assert "coder" in summary
    assert "可用 Agent" in summary or "Available Agents" in summary


@pytest.mark.asyncio
async def test_render_agent_summary_includes_dynamic_session_agent(tmp_path, monkeypatch):
    """Dynamic session agents appear in the summary with name/kind/profile/lifecycle."""
    reg, cat, _, _ = await _make_registry(tmp_path, monkeypatch)
    await reg.init()
    spec = await reg.create_agent(
        CreateAgentInput(name_hint="my-dyn-agent", purpose="does custom research",
                         instructions="你是一个 helper", profile="agent_researcher",
                         model_slot="agent_researcher", lifecycle="session"),
        ctx=MagicMock(),
    )
    summary = reg.render_agent_summary_for_stateview()
    assert spec.name in summary
    assert "dynamic" in summary
    assert "agent_researcher" in summary
    assert "session" in summary


@pytest.mark.asyncio
async def test_render_agent_summary_never_includes_system_prompt(tmp_path, monkeypatch):
    """The summary for StateView must never leak full system_prompt text."""
    reg, cat, _, _ = await _make_registry(tmp_path, monkeypatch)
    await reg.init()
    spec = await reg.create_agent(
        CreateAgentInput(name_hint="secret-agent", purpose="x",
                         instructions="秘密指令 ABC 机密操作 XYZ",
                         profile="agent_researcher", model_slot="agent_researcher",
                         lifecycle="session"),
        ctx=MagicMock(),
    )
    summary = reg.render_agent_summary_for_stateview()
    # Must contain the agent name (metadata OK)
    assert spec.name in summary
    # Must NOT contain the system_prompt/instructions
    assert "秘密指令" not in summary
    assert "ABC" not in summary
    assert "机密操作" not in summary
    assert "XYZ" not in summary
    # Builtin system prompts also must not leak
    assert "# " not in summary or summary.count("# ") < 2  # at most header-like text


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


# ── MAX_SESSION_AGENTS enforcement (registry layer) ──

@pytest.mark.asyncio
async def test_create_five_session_agents_succeed_sixth_fails(tmp_path, monkeypatch):
    """Create 5 session agents ok, 6th → AgentPolicyViolation."""
    import time
    reg, cat, _, _ = await _make_registry(tmp_path, monkeypatch)
    await reg.init()
    for i in range(5):
        await reg.create_agent(
            CreateAgentInput(name_hint=f"sess_{i}", purpose="x", instructions="y",
                             profile="agent_researcher", model_slot="agent_researcher",
                             lifecycle="session", ttl_seconds=600),
            ctx=MagicMock(),
        )
    assert len(reg._session_agents) == 5
    with pytest.raises(AgentPolicyViolation) as exc_info:
        await reg.create_agent(
            CreateAgentInput(name_hint="overflow", purpose="x", instructions="y",
                             profile="agent_researcher", model_slot="agent_researcher",
                             lifecycle="session", ttl_seconds=600),
            ctx=MagicMock(),
        )
    assert exc_info.value.reason == "max_session_agents_reached"


@pytest.mark.asyncio
async def test_create_ephemeral_not_affected_by_session_limit(tmp_path, monkeypatch):
    """Ephemeral agent creation ignores MAX_SESSION_AGENTS."""
    reg, cat, _, _ = await _make_registry(tmp_path, monkeypatch)
    await reg.init()
    # Fill session slots
    for i in range(5):
        await reg.create_agent(
            CreateAgentInput(name_hint=f"sess_{i}", purpose="x", instructions="y",
                             profile="agent_researcher", model_slot="agent_researcher",
                             lifecycle="session", ttl_seconds=600),
            ctx=MagicMock(),
        )
    # Ephemeral should still work
    spec = await reg.create_agent(
        CreateAgentInput(name_hint="eph", purpose="x", instructions="y",
                         profile="agent_researcher", model_slot="agent_researcher",
                         lifecycle="ephemeral", ttl_seconds=3600),
        ctx=MagicMock(),
    )
    assert spec.lifecycle.mode == "ephemeral"
    assert spec.name in reg._ephemeral_agents


@pytest.mark.asyncio
async def test_create_session_after_expired_cleanup_succeeds(tmp_path, monkeypatch):
    """After an expired session is cleaned up, a new one can be created."""
    import time
    reg, cat, _, _ = await _make_registry(tmp_path, monkeypatch)
    await reg.init()
    for i in range(5):
        await reg.create_agent(
            CreateAgentInput(name_hint=f"sess_{i}", purpose="x", instructions="y",
                             profile="agent_researcher", model_slot="agent_researcher",
                             lifecycle="session", ttl_seconds=600),
            ctx=MagicMock(),
        )
    # Expire the first session agent
    name0 = "sess_0"
    entry = reg._session_agents[name0]
    entry.last_used_at = time.monotonic() - 1000  # well past ttl=600

    # 6th create_agent triggers cleanup internally, then should succeed
    spec = await reg.create_agent(
        CreateAgentInput(name_hint="new_sess", purpose="x", instructions="y",
                         profile="agent_researcher", model_slot="agent_researcher",
                         lifecycle="session", ttl_seconds=600),
        ctx=MagicMock(),
    )
    assert spec.lifecycle.mode == "session"
    # The expired one is gone, the new one is present
    assert name0 not in reg._session_agents
    assert spec.name in reg._session_agents
    assert len(reg._session_agents) == 5


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
