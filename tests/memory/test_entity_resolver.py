"""EntityResolver unit tests (Phase 1)."""

from __future__ import annotations

import pytest
import yaml

from src.memory.entity_resolver import EntityResolver


@pytest.fixture
def aliases_path(tmp_path):
    p = tmp_path / "aliases.yaml"
    p.write_text(
        yaml.safe_dump({
            "entity.kevin": {
                "canonical": "Kevin",
                "aliases": ["kevinwu", "Kevin Wu", "OWNER"],
            },
            "entity.lapwing": {
                "canonical": "Lapwing",
                "aliases": ["LapWing", "lapwing", "小翅膀"],
            },
        }, allow_unicode=True),
        encoding="utf-8",
    )
    return p


@pytest.fixture
def resolver(aliases_path):
    return EntityResolver(aliases_path)


def test_resolve_canonical_name(resolver):
    assert resolver.resolve("Kevin") == "entity.kevin"


def test_resolve_alias(resolver):
    assert resolver.resolve("kevinwu") == "entity.kevin"
    assert resolver.resolve("OWNER") == "entity.kevin"


def test_resolve_alias_case_insensitive(resolver):
    assert resolver.resolve("KEVIN") == "entity.kevin"


def test_resolve_unknown_returns_none(resolver):
    assert resolver.resolve("Mallory") is None


def test_owner_speaker_wo_resolves_to_kevin(resolver):
    assert resolver.resolve("我", actor_role="owner") == "entity.kevin"


def test_owner_speaker_ni_resolves_to_lapwing(resolver):
    assert resolver.resolve("你", actor_role="owner") == "entity.lapwing"


def test_lapwing_outbound_wo_resolves_to_lapwing(resolver):
    assert (
        resolver.resolve("我", message_direction="outbound", actor_role="lapwing")
        == "entity.lapwing"
    )


def test_lapwing_outbound_ni_resolves_to_kevin(resolver):
    assert (
        resolver.resolve("你", message_direction="outbound", actor_role="lapwing")
        == "entity.kevin"
    )


def test_guest_pronouns_unresolved(resolver):
    assert resolver.resolve("我", actor_role="guest") is None
    assert resolver.resolve("you", actor_role="guest") is None


def test_add_alias_persists(aliases_path):
    r = EntityResolver(aliases_path)
    r.add_alias("entity.kevin", "Boss", reason="test")
    # New resolver instance should see the new alias
    r2 = EntityResolver(aliases_path)
    assert r2.resolve("Boss") == "entity.kevin"
    # Changelog updated
    log = aliases_path.parent / "changelog.md"
    assert log.exists()
    assert "Boss" in log.read_text(encoding="utf-8")


def test_get_canonical(resolver):
    assert resolver.get_canonical("entity.kevin") == "Kevin"
    assert resolver.get_canonical("entity.unknown") is None
