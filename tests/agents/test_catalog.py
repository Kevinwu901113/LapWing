"""Tests for src.agents.catalog — SQLite-backed AgentSpec catalog."""

from __future__ import annotations

import aiosqlite
import pytest

from src.agents.catalog import AgentCatalog, AgentCatalogIntegrityError
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


# ── spec_hash integrity verification ────────────────────────────────────


@pytest.mark.asyncio
async def test_get_by_name_rejects_tampered_spec_json(tmp_path):
    """B: spec_json modified externally, spec_hash not updated → fail-closed."""
    db = tmp_path / "x.db"
    cat = AgentCatalog(db)
    await cat.init()
    s = AgentSpec(name="target", system_prompt="original")
    await cat.save(s)

    # Tamper spec_json directly in SQLite
    async with aiosqlite.connect(str(db)) as conn:
        await conn.execute(
            "UPDATE agent_catalog SET spec_json = ? WHERE id = ?",
            ('{"name":"target","system_prompt":"injected"}', s.id),
        )
        await conn.commit()

    with pytest.raises(AgentCatalogIntegrityError):
        await cat.get_by_name("target")


@pytest.mark.asyncio
async def test_get_rejects_tampered_spec_json(tmp_path):
    """B-2: get() also rejects tampered spec_json."""
    db = tmp_path / "x.db"
    cat = AgentCatalog(db)
    await cat.init()
    s = AgentSpec(name="x", system_prompt="ok")
    await cat.save(s)

    async with aiosqlite.connect(str(db)) as conn:
        await conn.execute(
            "UPDATE agent_catalog SET spec_json = ? WHERE id = ?",
            ('{"name":"x","system_prompt":"evil"}', s.id),
        )
        await conn.commit()

    with pytest.raises(AgentCatalogIntegrityError):
        await cat.get(s.id)


@pytest.mark.asyncio
async def test_get_by_name_rejects_tampered_spec_hash(tmp_path):
    """C: spec_hash modified → fail-closed."""
    db = tmp_path / "x.db"
    cat = AgentCatalog(db)
    await cat.init()
    s = AgentSpec(name="target", system_prompt="ok")
    await cat.save(s)

    async with aiosqlite.connect(str(db)) as conn:
        await conn.execute(
            "UPDATE agent_catalog SET spec_hash = ? WHERE id = ?",
            ("bad_hash", s.id),
        )
        await conn.commit()

    with pytest.raises(AgentCatalogIntegrityError):
        await cat.get_by_name("target")


@pytest.mark.asyncio
async def test_get_by_name_rejects_empty_spec_hash(tmp_path):
    """D: spec_hash is empty string → fail-closed."""
    db = tmp_path / "x.db"
    cat = AgentCatalog(db)
    await cat.init()
    s = AgentSpec(name="target", system_prompt="ok")
    await cat.save(s)

    async with aiosqlite.connect(str(db)) as conn:
        await conn.execute(
            "UPDATE agent_catalog SET spec_hash = '' WHERE id = ?",
            (s.id,),
        )
        await conn.commit()

    with pytest.raises(AgentCatalogIntegrityError):
        await cat.get_by_name("target")


@pytest.mark.asyncio
async def test_get_by_name_rejects_corrupt_json(tmp_path):
    """spec_json is not valid JSON → fail-closed."""
    db = tmp_path / "x.db"
    cat = AgentCatalog(db)
    await cat.init()
    s = AgentSpec(name="target", system_prompt="ok")
    await cat.save(s)

    async with aiosqlite.connect(str(db)) as conn:
        await conn.execute(
            "UPDATE agent_catalog SET spec_json = ? WHERE id = ?",
            ("not valid json {{{", s.id),
        )
        await conn.commit()

    with pytest.raises(AgentCatalogIntegrityError):
        await cat.get_by_name("target")


@pytest.mark.asyncio
async def test_list_specs_rejects_tampered_row(tmp_path):
    """list_specs() also fails when any row is tampered."""
    db = tmp_path / "x.db"
    cat = AgentCatalog(db)
    await cat.init()
    await cat.save(AgentSpec(name="ok1", system_prompt="a"))
    s2 = AgentSpec(name="ok2", system_prompt="b")
    await cat.save(s2)

    async with aiosqlite.connect(str(db)) as conn:
        await conn.execute(
            "UPDATE agent_catalog SET spec_hash = 'bad' WHERE name = 'ok2'"
        )
        await conn.commit()

    with pytest.raises(AgentCatalogIntegrityError):
        await cat.list_specs()


@pytest.mark.asyncio
async def test_roundtrip_preserves_hash_consistency(tmp_path):
    """A: Normal save → get_by_name returns spec with matching hash."""
    cat = AgentCatalog(tmp_path / "x.db")
    await cat.init()
    s = AgentSpec(
        name="alpha",
        system_prompt="hello",
        runtime_profile="agent_researcher",
        lifecycle=AgentLifecyclePolicy(mode="persistent"),
        resource_limits=AgentResourceLimits(max_tool_calls=10),
        tool_denylist=["b", "a"],  # unsorted → spec_hash sorts it
    )
    await cat.save(s)
    got = await cat.get_by_name("alpha")
    assert got is not None
    assert got.name == "alpha"
    assert got.system_prompt == "hello"
    assert got.tool_denylist == ["b", "a"]  # preserved as-is
    assert got.spec_hash() == s.spec_hash()


@pytest.mark.asyncio
async def test_update_save_still_consistent(tmp_path):
    """Saving the same spec twice (update) produces consistent hash."""
    cat = AgentCatalog(tmp_path / "x.db")
    await cat.init()
    s = AgentSpec(name="x", system_prompt="v1")
    await cat.save(s)
    s.system_prompt = "v2"
    await cat.save(s)
    got = await cat.get_by_name("x")
    assert got.system_prompt == "v2"
    assert got.spec_hash() == s.spec_hash()


# ── builtin spec integrity ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_builtin_specs_pass_integrity_check(tmp_path):
    """E: builtin specs (saved via registry init) read without error."""
    cat = AgentCatalog(tmp_path / "x.db")
    await cat.init()
    from src.agents.builtin_specs import (
        builtin_researcher_spec,
        builtin_coder_spec,
    )
    for spec_factory in (builtin_researcher_spec, builtin_coder_spec):
        spec = spec_factory()
        await cat.save(spec)

    researcher = await cat.get_by_name("researcher")
    assert researcher is not None
    assert researcher.kind == "builtin"

    coder = await cat.get_by_name("coder")
    assert coder is not None
    assert coder.kind == "builtin"


@pytest.mark.asyncio
async def test_tampered_builtin_rejected(tmp_path):
    """E-2: Tampered builtin row → fail-closed."""
    cat = AgentCatalog(tmp_path / "x.db")
    await cat.init()
    from src.agents.builtin_specs import builtin_researcher_spec
    spec = builtin_researcher_spec()
    await cat.save(spec)

    db = tmp_path / "x.db"
    async with aiosqlite.connect(str(db)) as conn:
        await conn.execute(
            "UPDATE agent_catalog SET spec_json = ? WHERE name = 'researcher'",
            ('{"name":"researcher","system_prompt":"pwned"}',),
        )
        await conn.commit()

    with pytest.raises(AgentCatalogIntegrityError):
        await cat.get_by_name("researcher")


# ── registry behaviour ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_registry_propagates_catalog_integrity_error(tmp_path, monkeypatch):
    """F: registry.get_or_create_instance should not swallow integrity errors."""
    from unittest.mock import MagicMock
    from src.agents.registry import AgentRegistry
    from src.agents.catalog import AgentCatalog

    db = tmp_path / "cat.db"
    cat = AgentCatalog(db)
    await cat.init()
    factory = MagicMock()
    policy = MagicMock()
    reg = AgentRegistry(cat, factory, policy)
    await reg.init()

    # Tamper the builtin researcher row
    async with aiosqlite.connect(str(db)) as conn:
        await conn.execute(
            "UPDATE agent_catalog SET spec_hash = 'bad' WHERE name = 'researcher'"
        )
        await conn.commit()

    with pytest.raises(AgentCatalogIntegrityError):
        await reg.get_or_create_instance("researcher")
