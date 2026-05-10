"""Agent delegation return contract — typed outcome + serialization.

Ensures ``delegate_to_agent`` / ``delegate_to_researcher`` never return
bare tuples or untyped dicts across the tool boundary.  Internal code
uses ``AgentDelegationOutcome``; the tool executor converts to a
JSON-serializable dict via ``.to_tool_dict()`` at the last mile.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class AgentDelegationOutcome(Generic[T]):
    success: bool
    result: T | None = None
    error: str | None = None
    cache_hit: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_tool_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "result": serialize_agent_result(self.result),
            "error": self.error,
            "cache_hit": self.cache_hit,
            "metadata": self.metadata,
        }


def serialize_agent_result(result: Any) -> Any:
    """Convert an agent result to a JSON-serializable value.

    Handles dataclasses, objects with ``to_dict()``, dicts, NamedTuples,
    and plain values.  Bare tuples are *rejected* — they indicate a
    contract violation that must be fixed at the source.
    """
    if result is None:
        return None
    if hasattr(result, "to_dict"):
        return result.to_dict()
    if hasattr(result, "__dataclass_fields__"):
        return asdict(result)
    if isinstance(result, dict):
        return result
    if isinstance(result, tuple) and hasattr(result, "_asdict"):
        return result._asdict()
    if isinstance(result, (str, int, float, bool)):
        return result
    if isinstance(result, list):
        return [serialize_agent_result(item) for item in result]
    # Bare tuple — contract violation.
    if isinstance(result, tuple):
        raise TypeError(
            "delegate_to_agent received bare tuple result; "
            "expected AgentResult/dataclass/Mapping. "
            f"Got: {type(result).__name__}{result!r}"
        )
    return result


def normalize_research_result(result: Any) -> dict[str, Any]:
    """Normalize any ResearchResult shape into ``{summary, sources}``.

    Supports dict, dataclass/NamedTuple (via attributes), and
    ``ResearchResult.to_dict()`` output.  Bare tuples are rejected.
    """
    if result is None:
        return {"summary": "", "sources": []}

    if isinstance(result, dict):
        return {
            "summary": result.get("summary", ""),
            "sources": result.get("sources", []),
        }

    if hasattr(result, "summary") or hasattr(result, "sources"):
        return {
            "summary": getattr(result, "summary", ""),
            "sources": getattr(result, "sources", []),
        }

    if isinstance(result, tuple) and hasattr(result, "_asdict"):
        data = result._asdict()
        return {
            "summary": data.get("summary", ""),
            "sources": data.get("sources", []),
        }

    if isinstance(result, tuple):
        raise TypeError(
            "delegate_to_researcher received bare tuple result; "
            "expected ResearchResult/dataclass/Mapping. "
            f"Got: {type(result).__name__}{result!r}"
        )

    return {"summary": str(result)[:2000], "sources": []}


def assert_not_bare_tuple(value: Any, context: str) -> None:
    """Debug guard — raises if ``value`` is a bare tuple (not NamedTuple).

    Use at internal boundaries to catch contract violations early.
    """
    if isinstance(value, tuple) and not hasattr(value, "_asdict"):
        raise TypeError(
            f"{context} returned bare tuple; this violates "
            f"AgentDelegationOutcome contract. Got: {value!r}"
        )
