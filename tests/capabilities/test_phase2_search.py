"""Phase 2A tests: search/filter/sort helpers.

All functions under test are pure — no I/O, no fixtures needed.
"""

from __future__ import annotations

import pytest

from src.capabilities.schema import CapabilityManifest, CapabilityMaturity, CapabilityScope, CapabilityStatus, CapabilityType
from src.capabilities.search import (
    SCOPE_PRECEDENCE,
    deduplicate_by_precedence,
    filter_active,
    filter_by_scope,
    filter_by_tags,
    filter_by_type,
    filter_stable,
    filter_trust_level,
    resolve_by_scope,
    sort_by_maturity,
    sort_by_name,
    sort_by_updated,
    text_search,
)


def _make_manifest(
    cap_id: str = "test_001",
    name: str = "Test Cap",
    description: str = "A test capability.",
    type: CapabilityType = CapabilityType.SKILL,
    scope: CapabilityScope = CapabilityScope.WORKSPACE,
    maturity: CapabilityMaturity = CapabilityMaturity.DRAFT,
    status: CapabilityStatus = CapabilityStatus.ACTIVE,
    risk_level: str = "low",
    tags: list | None = None,
    triggers: list | None = None,
    trust_required: str = "developer",
    **overrides,
) -> CapabilityManifest:
    from src.capabilities.schema import CapabilityRiskLevel
    return CapabilityManifest(
        id=cap_id,
        name=name,
        description=description,
        type=type,
        scope=scope,
        version="0.1.0",
        maturity=maturity,
        status=status,
        risk_level=CapabilityRiskLevel(risk_level),
        trust_required=trust_required,
        tags=tags or [],
        triggers=triggers or [],
    )


# ── SCOPE_PRECEDENCE ──────────────────────────────────────────

class TestScopePrecedence:
    def test_order_is_session_first(self):
        assert SCOPE_PRECEDENCE[0] == CapabilityScope.SESSION
        assert SCOPE_PRECEDENCE[-1] == CapabilityScope.GLOBAL

    def test_all_scopes_present(self):
        assert len(SCOPE_PRECEDENCE) == 4
        assert set(SCOPE_PRECEDENCE) == {CapabilityScope.SESSION, CapabilityScope.WORKSPACE, CapabilityScope.USER, CapabilityScope.GLOBAL}


# ── filter_active ─────────────────────────────────────────────

class TestFilterActive:
    def test_keeps_active_only(self):
        m1 = _make_manifest(status=CapabilityStatus.ACTIVE)
        m2 = _make_manifest(status=CapabilityStatus.DISABLED)
        m3 = _make_manifest(status=CapabilityStatus.ARCHIVED)
        assert filter_active([m1, m2, m3]) == [m1]

    def test_empty_list(self):
        assert filter_active([]) == []

    def test_all_active(self):
        manifests = [_make_manifest(cap_id=f"t{i}") for i in range(3)]
        assert len(filter_active(manifests)) == 3


# ── filter_by_tags ────────────────────────────────────────────

class TestFilterByTags:
    def test_any_tag_match(self):
        m1 = _make_manifest(cap_id="a", tags=["python", "web"])
        m2 = _make_manifest(cap_id="b", tags=["rust", "cli"])
        m3 = _make_manifest(cap_id="c", tags=["python", "cli"])
        result = filter_by_tags([m1, m2, m3], ["python"])
        assert {m.id for m in result} == {"a", "c"}

    def test_all_tags_match(self):
        m1 = _make_manifest(cap_id="a", tags=["python", "web", "api"])
        m2 = _make_manifest(cap_id="b", tags=["python", "web"])
        result = filter_by_tags([m1, m2], ["python", "web"], match_all=True)
        assert {m.id for m in result} == {"a", "b"}

        result_strict = filter_by_tags([m1, m2], ["python", "web", "api"], match_all=True)
        assert {m.id for m in result_strict} == {"a"}

    def test_case_insensitive(self):
        m = _make_manifest(cap_id="a", tags=["Python", "WEB"])
        result = filter_by_tags([m], ["python"])
        assert len(result) == 1

    def test_empty_tags_returns_all(self):
        manifests = [_make_manifest(cap_id=f"t{i}") for i in range(3)]
        assert len(filter_by_tags(manifests, [])) == 3


# ── filter_by_type ────────────────────────────────────────────

class TestFilterByType:
    def test_filters_by_type(self):
        m1 = _make_manifest(cap_id="a", type=CapabilityType.SKILL)
        m2 = _make_manifest(cap_id="b", type=CapabilityType.WORKFLOW)
        result = filter_by_type([m1, m2], [CapabilityType.SKILL])
        assert [m.id for m in result] == ["a"]

    def test_multiple_types(self):
        m1 = _make_manifest(cap_id="a", type=CapabilityType.SKILL)
        m2 = _make_manifest(cap_id="b", type=CapabilityType.WORKFLOW)
        m3 = _make_manifest(cap_id="c", type=CapabilityType.TOOL_WRAPPER)
        result = filter_by_type([m1, m2, m3], [CapabilityType.SKILL, CapabilityType.WORKFLOW])
        assert {m.id for m in result} == {"a", "b"}

    def test_empty_types_returns_all(self):
        manifests = [_make_manifest(cap_id=f"t{i}") for i in range(3)]
        assert len(filter_by_type(manifests, [])) == 3


# ── filter_by_scope ───────────────────────────────────────────

class TestFilterByScope:
    def test_filters_by_scope(self):
        m1 = _make_manifest(cap_id="a", scope=CapabilityScope.WORKSPACE)
        m2 = _make_manifest(cap_id="b", scope=CapabilityScope.GLOBAL)
        result = filter_by_scope([m1, m2], [CapabilityScope.WORKSPACE])
        assert [m.id for m in result] == ["a"]


# ── filter_stable ─────────────────────────────────────────────

class TestFilterStable:
    def test_keeps_stable_active_only(self):
        m1 = _make_manifest(cap_id="a", maturity=CapabilityMaturity.STABLE, status=CapabilityStatus.ACTIVE)
        m2 = _make_manifest(cap_id="b", maturity=CapabilityMaturity.STABLE, status=CapabilityStatus.DISABLED)
        m3 = _make_manifest(cap_id="c", maturity=CapabilityMaturity.DRAFT, status=CapabilityStatus.ACTIVE)
        assert [m.id for m in filter_stable([m1, m2, m3])] == ["a"]


# ── filter_trust_level ────────────────────────────────────────

class TestFilterTrustLevel:
    def test_below_max_trust(self):
        m1 = _make_manifest(cap_id="a", trust_required="guest")
        m2 = _make_manifest(cap_id="b", trust_required="developer")
        m3 = _make_manifest(cap_id="c", trust_required="admin")
        result = filter_trust_level([m1, m2, m3], "developer")
        assert {m.id for m in result} == {"a", "b"}

    def test_admin_sees_all(self):
        manifests = [_make_manifest(cap_id=f"t{i}", trust_required=f"trust_{i}") for i in range(3)]
        # Unknown trust levels default to level 0 in _TRUST_ORDER, but "admin" is level 3
        # guest=0, developer=1, trusted=2, admin=3
        # admin >= all
        m1 = _make_manifest(cap_id="a", trust_required="guest")
        m2 = _make_manifest(cap_id="b", trust_required="admin")
        assert len(filter_trust_level([m1, m2], "admin")) == 2


# ── text_search ───────────────────────────────────────────────

class TestTextSearch:
    def test_matches_name(self):
        m = _make_manifest(name="Python Skill")
        assert len(text_search([m], "python")) == 1

    def test_matches_description(self):
        m = _make_manifest(description="Handles HTTP requests")
        assert len(text_search([m], "http")) == 1

    def test_case_insensitive(self):
        m = _make_manifest(name="Python Skill")
        assert len(text_search([m], "PYTHON")) == 1

    def test_no_match(self):
        m = _make_manifest(name="Python Skill")
        assert len(text_search([m], "ruby")) == 0

    def test_empty_query_returns_all(self):
        manifests = [_make_manifest(cap_id=f"t{i}") for i in range(3)]
        assert len(text_search(manifests, "")) == 3

    def test_whitespace_query_returns_all(self):
        manifests = [_make_manifest(cap_id=f"t{i}") for i in range(3)]
        assert len(text_search(manifests, "   ")) == 3


# ── deduplicate_by_precedence ─────────────────────────────────

class TestDeduplicateByPrecedence:
    def test_keeps_highest_scope(self):
        m_session = _make_manifest(cap_id="dup", scope=CapabilityScope.SESSION)
        m_workspace = _make_manifest(cap_id="dup", scope=CapabilityScope.WORKSPACE)
        m_global = _make_manifest(cap_id="dup", scope=CapabilityScope.GLOBAL)
        result = deduplicate_by_precedence([m_global, m_session, m_workspace])
        assert len(result) == 1
        assert result[0].scope == CapabilityScope.SESSION

    def test_unique_ids_preserved(self):
        m1 = _make_manifest(cap_id="a", scope=CapabilityScope.WORKSPACE)
        m2 = _make_manifest(cap_id="b", scope=CapabilityScope.WORKSPACE)
        result = deduplicate_by_precedence([m1, m2])
        assert len(result) == 2

    def test_idempotent(self):
        m1 = _make_manifest(cap_id="a", scope=CapabilityScope.WORKSPACE)
        m2 = _make_manifest(cap_id="a", scope=CapabilityScope.GLOBAL)
        result1 = deduplicate_by_precedence([m1, m2])
        result2 = deduplicate_by_precedence(result1)
        assert len(result1) == len(result2)


# ── resolve_by_scope ──────────────────────────────────────────

class TestResolveByScope:
    def test_keeps_only_specified_scope(self):
        m1 = _make_manifest(cap_id="a", scope=CapabilityScope.WORKSPACE)
        m2 = _make_manifest(cap_id="b", scope=CapabilityScope.GLOBAL)
        result = resolve_by_scope([m1, m2], CapabilityScope.WORKSPACE)
        assert [m.id for m in result] == ["a"]


# ── sort functions ────────────────────────────────────────────

class TestSortByName:
    def test_alphabetical(self):
        m1 = _make_manifest(cap_id="a", name="Zebra")
        m2 = _make_manifest(cap_id="b", name="Alpha")
        result = sort_by_name([m1, m2])
        assert result[0].name == "Alpha"
        assert result[1].name == "Zebra"


class TestSortByMaturity:
    def test_stable_first(self):
        m1 = _make_manifest(cap_id="a", maturity=CapabilityMaturity.DRAFT, name="A")
        m2 = _make_manifest(cap_id="b", maturity=CapabilityMaturity.STABLE, name="B")
        result = sort_by_maturity([m1, m2])
        assert result[0].maturity == CapabilityMaturity.STABLE
        assert result[1].maturity == CapabilityMaturity.DRAFT


class TestSortByUpdated:
    def test_most_recent_first(self):
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        m1 = _make_manifest(cap_id="a")
        m2 = _make_manifest(cap_id="b")
        m1.updated_at = now
        m2.updated_at = now - timedelta(hours=1)
        result = sort_by_updated([m2, m1])
        assert result[0].id == "a"
        assert result[1].id == "b"
