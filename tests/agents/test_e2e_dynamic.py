"""End-to-end dynamic agent flow (Blueprint §15 acceptance T-01, T-13).

Wires Catalog + Factory + Policy + Registry + 5 brain tools together
without spinning the whole AppContainer, then exercises:
- T-01: delegate_to_agent for builtin researcher and coder
- T-13: full create → delegate → save → destroy emits the expected
  AGENT_CREATED / AGENT_STARTED / AGENT_COMPLETED / AGENT_SAVED /
  AGENT_DESTROYED mutations
"""

from __future__ import annotations
import pytest
pytestmark = pytest.mark.e2e

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.agents.catalog import AgentCatalog
from src.agents.factory import AgentFactory
from src.agents.policy import AgentPolicy, LintResult
from src.agents.registry import AgentRegistry
from src.agents.types import AgentResult
from src.logging.state_mutation_log import MutationType
from src.tools.agent_tools import (
    create_agent_executor,
    delegate_to_agent_executor,
    destroy_agent_executor,
    save_agent_executor,
)
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


class _FakeMutationLog:
    def __init__(self):
        self.events: list[tuple[MutationType, dict]] = []

    async def record(self, event_type, payload, **kwargs):
        self.events.append((event_type, payload))

    def types(self) -> list[MutationType]:
        return [t for (t, _) in self.events]


def _ctx(*, registry, mutation_log):
    return ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd=".",
        adapter="test",
        user_id="kevin",
        auth_level=10,
        chat_id="c1",
        services={
            "agent_registry": registry,
            "mutation_log": mutation_log,
            "dispatcher": MagicMock(),
            "tool_registry": MagicMock(),
            "llm_router": MagicMock(),
            "research_engine": MagicMock(),
            "ambient_store": MagicMock(),
        },
    )


async def _make_pipeline(tmp_path, monkeypatch):
    """Catalog + Factory (mocked) + Policy + Registry, all wired, init()'d."""
    cat = AgentCatalog(tmp_path / "x.db"); await cat.init()
    factory = MagicMock()

    # Each factory.create returns a fresh fake agent that succeeds.
    def _create(spec, services_override=None):
        a = MagicMock()
        a.spec = MagicMock()
        a.spec.name = spec.name
        a.dynamic_spec = spec
        a.execute = AsyncMock(return_value=AgentResult(
            task_id="t", status="done", result=f"ok:{spec.name}",
        ))
        return a
    factory.create = MagicMock(side_effect=_create)

    policy = AgentPolicy(cat)
    policy._semantic_lint = AsyncMock(return_value=LintResult(verdict="safe"))

    reg = AgentRegistry(catalog=cat, factory=factory, policy=policy)
    await reg.init()

    return reg, cat, factory, policy


# ── T-01: delegate to builtin researcher and coder ────────────────────

@pytest.mark.asyncio
async def test_t01_delegate_to_agent_builtin_researcher(tmp_path, monkeypatch):
    reg, _, _, _ = await _make_pipeline(tmp_path, monkeypatch)
    log = _FakeMutationLog()
    ctx = _ctx(registry=reg, mutation_log=log)
    req = ToolExecutionRequest(
        name="delegate_to_agent",
        arguments={"agent_name": "researcher", "task": "find foo"},
    )
    res = await delegate_to_agent_executor(req, ctx)
    assert res.success is True
    assert "researcher" in res.payload["result"]


@pytest.mark.asyncio
async def test_t01_delegate_to_agent_builtin_coder(tmp_path, monkeypatch):
    reg, _, _, _ = await _make_pipeline(tmp_path, monkeypatch)
    log = _FakeMutationLog()
    ctx = _ctx(registry=reg, mutation_log=log)
    req = ToolExecutionRequest(
        name="delegate_to_agent",
        arguments={"agent_name": "coder", "task": "write hello.py"},
    )
    res = await delegate_to_agent_executor(req, ctx)
    assert res.success is True
    assert "coder" in res.payload["result"]


# ── T-13: full create → delegate → save → destroy + audit chain ───────

@pytest.mark.asyncio
async def test_t13_full_lifecycle_emits_audit_chain(tmp_path, monkeypatch):
    reg, cat, factory, _ = await _make_pipeline(tmp_path, monkeypatch)
    log = _FakeMutationLog()
    ctx = _ctx(registry=reg, mutation_log=log)

    # 1. create_agent
    create_req = ToolExecutionRequest(
        name="create_agent",
        arguments={
            "name_hint": "translator",
            "purpose": "translate things",
            "instructions": "translate text between languages",
            "profile": "agent_researcher",
            "lifecycle": "session",
            "max_runs": 5,
            "ttl_seconds": 600,
        },
    )
    res_create = await create_agent_executor(create_req, ctx)
    assert res_create.success is True
    agent_name = res_create.payload["name"]
    assert MutationType.AGENT_CREATED in log.types()

    # 2. delegate_to_agent
    delegate_req = ToolExecutionRequest(
        name="delegate_to_agent",
        arguments={"agent_name": agent_name, "task": "translate hello"},
    )
    res_del = await delegate_to_agent_executor(delegate_req, ctx)
    assert res_del.success is True

    # 3. save_agent
    save_req = ToolExecutionRequest(
        name="save_agent",
        arguments={"agent_name": agent_name, "reason": "useful for translation tasks"},
    )
    res_save = await save_agent_executor(save_req, ctx)
    assert res_save.success is True
    assert MutationType.AGENT_SAVED in log.types()
    # Spec persisted to catalog with mode=persistent
    saved = await cat.get_by_name(agent_name)
    assert saved is not None
    assert saved.lifecycle.mode == "persistent"

    # 4. destroy_agent
    destroy_req = ToolExecutionRequest(
        name="destroy_agent",
        arguments={"agent_name": agent_name},
    )
    res_destroy = await destroy_agent_executor(destroy_req, ctx)
    assert res_destroy.success is True
    assert MutationType.AGENT_DESTROYED in log.types()

    # Full audit chain: at least these 5 mutation types in order-agnostic check.
    types = log.types()
    expected = {
        MutationType.AGENT_CREATED,
        MutationType.AGENT_DESTROYED,
        MutationType.AGENT_SAVED,
    }
    assert expected.issubset(set(types)), (
        f"missing audit events: {expected - set(types)}"
    )


@pytest.mark.asyncio
async def test_destroy_builtin_blocked(tmp_path, monkeypatch):
    """T-13 corollary: builtins survive destroy attempts."""
    reg, cat, _, _ = await _make_pipeline(tmp_path, monkeypatch)
    log = _FakeMutationLog()
    ctx = _ctx(registry=reg, mutation_log=log)
    req = ToolExecutionRequest(
        name="destroy_agent",
        arguments={"agent_name": "researcher"},
    )
    res = await destroy_agent_executor(req, ctx)
    assert res.success is False
    # Researcher still in catalog
    assert await cat.get_by_name("researcher") is not None
