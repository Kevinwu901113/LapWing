"""Research 子系统的核心数据类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


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
    confidence: Literal["high", "medium", "low"] = "medium"
    unclear: str = ""
    search_backend_used: list[str] = field(default_factory=list)
