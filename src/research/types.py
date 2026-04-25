"""Research 子系统的核心数据类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def normalize_confidence(value: Any) -> float:
    """Normalize confidence to a 0.0-1.0 score."""
    if isinstance(value, str):
        mapping = {"high": 0.9, "medium": 0.6, "low": 0.3}
        return mapping.get(value.strip().lower(), 0.6)
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.6
    return min(max(score, 0.0), 1.0)


@dataclass
class Evidence:
    """单条证据：URL + 来源名 + 引文。"""

    source_url: str
    source_name: str
    quote: str

    def to_dict(self) -> dict:
        return {
            "source_url": self.source_url,
            "source_name": self.source_name,
            "quote": self.quote,
        }


@dataclass
class ResearchResult:
    """research(question) 的最终输出。"""

    answer: str
    evidence: list[Evidence] = field(default_factory=list)
    confidence: float = 0.6
    unclear: str = ""
    search_backend_used: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.confidence = normalize_confidence(self.confidence)
