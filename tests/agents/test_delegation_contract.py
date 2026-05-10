"""Tests for src.agents.delegation_contract — P0 contract hardening."""

from __future__ import annotations

from dataclasses import dataclass
from collections import namedtuple

import pytest

from src.agents.delegation_contract import (
    AgentDelegationOutcome,
    assert_not_bare_tuple,
    normalize_research_result,
    serialize_agent_result,
)

# ── serialize_agent_result ────────────────────────────────────────────────


def test_serialize_none():
    assert serialize_agent_result(None) is None


def test_serialize_dict():
    assert serialize_agent_result({"a": 1}) == {"a": 1}


def test_serialize_dataclass():
    @dataclass
    class Dummy:
        x: int = 1
        y: str = "hi"

    assert serialize_agent_result(Dummy()) == {"x": 1, "y": "hi"}


def test_serialize_namedtuple():
    Point = namedtuple("Point", ["x", "y"])
    assert serialize_agent_result(Point(1, 2)) == {"x": 1, "y": 2}


def test_serialize_to_dict():
    class HasToDict:
        def to_dict(self):
            return {"key": "value"}

    assert serialize_agent_result(HasToDict()) == {"key": "value"}


def test_serialize_primitives():
    assert serialize_agent_result("hello") == "hello"
    assert serialize_agent_result(42) == 42


def test_serialize_bare_tuple_raises():
    with pytest.raises(TypeError, match="bare tuple"):
        serialize_agent_result((1, 2, 3))


def test_serialize_list():
    assert serialize_agent_result([1, "a"]) == [1, "a"]


# ── normalize_research_result ─────────────────────────────────────────────


def test_normalize_none():
    assert normalize_research_result(None) == {"summary": "", "sources": []}


def test_normalize_dict():
    assert normalize_research_result({"summary": "A", "sources": [1]}) == {
        "summary": "A",
        "sources": [1],
    }


def test_normalize_object_with_attrs():
    class Obj:
        summary = "B"
        sources = [2]

    assert normalize_research_result(Obj()) == {"summary": "B", "sources": [2]}


def test_normalize_namedtuple():
    NT = namedtuple("NT", ["summary", "sources"])
    assert normalize_research_result(NT("C", [3])) == {
        "summary": "C",
        "sources": [3],
    }


def test_normalize_bare_tuple_raises():
    with pytest.raises(TypeError, match="bare tuple"):
        normalize_research_result((1, 2))


def test_normalize_fallback_str():
    result = normalize_research_result("some text")
    assert result["summary"] == "some text"
    assert result["sources"] == []


# ── assert_not_bare_tuple ─────────────────────────────────────────────────


def test_assert_not_bare_tuple_passes_for_none():
    assert_not_bare_tuple(None, "test")


def test_assert_not_bare_tuple_passes_for_namedtuple():
    NT = namedtuple("NT", ["a"])
    assert_not_bare_tuple(NT(1), "test")


def test_assert_not_bare_tuple_raises_for_bare_tuple():
    with pytest.raises(TypeError, match="test.*bare tuple"):
        assert_not_bare_tuple((1, 2), "test")


def test_assert_not_bare_tuple_passes_for_dict():
    assert_not_bare_tuple({"a": 1}, "test")


# ── AgentDelegationOutcome ────────────────────────────────────────────────


def test_outcome_to_tool_dict_success():
    outcome = AgentDelegationOutcome(
        success=True,
        result={"summary": "ok", "sources": []},
    )
    d = outcome.to_tool_dict()
    assert d["success"] is True
    assert d["result"]["summary"] == "ok"


def test_outcome_to_tool_dict_failure():
    outcome = AgentDelegationOutcome(success=False, error="missing service")
    d = outcome.to_tool_dict()
    assert d["success"] is False
    assert d["error"] == "missing service"
    assert d["result"] is None
