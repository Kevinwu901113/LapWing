"""Tests for src.agents.catalog — SQLite-backed AgentSpec catalog."""

from __future__ import annotations

import aiosqlite
import pytest

from src.agents.catalog import AgentCatalog
from src.agents.spec import (
    AgentLifecyclePolicy,
    AgentResourceLimits,
    AgentSpec,
)


@pytest.mark.asyncio
async def test_init_idempotent(tmp_path):
    db = tmp_path / "x.db"
    cat = AgentCatalog(db)
    await cat.init()
    await cat.init()  # idempotent


@pytest.mark.asyncio
async def test_save_get_roundtrip(tmp_path):
    cat = AgentCatalog(tmp_path / "x.db")
    await cat.init()
    s = AgentSpec(
        name="alpha",
        system_prompt="hi",
        runtime_profile="agent_researcher",
        lifecycle=AgentLifecyclePolicy(mode="persistent"),
        resource_limits=AgentResourceLimits(max_tool_calls=42),
    )
    await cat.save(s)
    got = await cat.get(s.id)
    assert got is not None
    assert got.name == "alpha"
    assert got.lifecycle.mode == "persistent"
    assert got.resource_limits.max_tool_calls == 42
    by_name = await cat.get_by_name("alpha")
    assert by_name is not None
    assert by_name.id == s.id


@pytest.mark.asyncio
async def test_get_returns_none_when_missing(tmp_path):
    cat = AgentCatalog(tmp_path / "x.db")
    await cat.init()
    assert await cat.get("nonexistent") is None
    assert await cat.get_by_name("nonexistent") is None


@pytest.mark.asyncio
async def test_save_overwrites_by_id(tmp_path):
    cat = AgentCatalog(tmp_path / "x.db")
    await cat.init()
    s = AgentSpec(name="z", system_prompt="v1")
    await cat.save(s)
    s.system_prompt = "v2"
    await cat.save(s)
    rows = await cat.list_specs()
    assert len(rows) == 1
    assert rows[0].system_prompt == "v2"


@pytest.mark.asyncio
async def test_list_filters(tmp_path):
    cat = AgentCatalog(tmp_path / "x.db")
    await cat.init()
    await cat.save(AgentSpec(name="b1", kind="builtin"))
    await cat.save(AgentSpec(name="d1", kind="dynamic"))
    builtins = await cat.list_specs(kind="builtin")
    assert {s.name for s in builtins} == {"b1"}
    dynamics = await cat.list_specs(kind="dynamic")
    assert {s.name for s in dynamics} == {"d1"}


@pytest.mark.asyncio
async def test_archive_keeps_row(tmp_path):
    cat = AgentCatalog(tmp_path / "x.db")
    await cat.init()
    s = AgentSpec(name="x")
    await cat.save(s)
    assert await cat.count(status="active") == 1
    await cat.archive(s.id)
    got = await cat.get(s.id)
    assert got is not None
    assert got.status == "archived"
    assert await cat.count(status="active") == 0
    assert await cat.count(status="archived") == 1
    archived = await cat.list_specs(status="archived")
    assert len(archived) == 1


@pytest.mark.asyncio
async def test_delete_hard_deletes(tmp_path):
    cat = AgentCatalog(tmp_path / "x.db")
    await cat.init()
    s = AgentSpec(name="x")
    await cat.save(s)
    await cat.delete(s.id)
    assert await cat.get(s.id) is None
    assert await cat.count() == 0


@pytest.mark.asyncio
async def test_count_with_filters(tmp_path):
    cat = AgentCatalog(tmp_path / "x.db")
    await cat.init()
    await cat.save(AgentSpec(name="b1", kind="builtin", status="active"))
    await cat.save(AgentSpec(name="d1", kind="dynamic", status="active"))
    await cat.save(AgentSpec(name="d2", kind="dynamic", status="archived"))
    assert await cat.count() == 3
    assert await cat.count(kind="dynamic") == 2
    assert await cat.count(status="active") == 2
    assert await cat.count(kind="dynamic", status="active") == 1


@pytest.mark.asyncio
async def test_spec_hash_persisted(tmp_path):
    """spec_hash must be stored as a separate column for audit purposes."""
    db = tmp_path / "x.db"
    cat = AgentCatalog(db)
    await cat.init()
    s = AgentSpec(name="x", system_prompt="hi")
    await cat.save(s)
    async with aiosqlite.connect(str(db)) as conn:
        async with conn.execute(
            "SELECT spec_hash FROM agent_catalog WHERE id = ?", (s.id,)
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row[0] == s.spec_hash()
