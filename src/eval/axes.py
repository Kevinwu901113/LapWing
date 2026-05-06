"""Minimal shared evaluation axis types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EvalAxis(str, Enum):
    FUNCTIONAL = "functional"
    SAFETY = "safety"
    PRIVACY = "privacy"
    REVERSIBILITY = "reversibility"


class AxisStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class AxisResult:
    axis: EvalAxis
    status: AxisStatus
    score: float | None = None
    findings: tuple[str, ...] = field(default_factory=tuple)

