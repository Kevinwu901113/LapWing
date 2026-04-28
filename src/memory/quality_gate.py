"""FastGate — fast quality gate for memory candidates.

Phase 1 §1.4 of the wiki blueprint. The fast gate runs synchronously on
every candidate and decides whether it deserves to enter the wiki
compilation pipeline at all. We keep prompts inline (per Phase 1
note 1) and call the LLM router's lightweight slot.

Three outcomes:

- ``accept`` — score ≥ 0.75. Candidate joins the pending pool for
  asynchronous compilation.
- ``defer``  — 0.45 ≤ score < 0.75. Stay in episodic only, available to
  vector recall but not compiled into a wiki page.
- ``reject`` — score < 0.45 or matched a hard-reject pattern. Original
  log only.

Hard rejects are deterministic. We never let secrets reach the LLM gate,
even if the lightweight model would happily score them.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

logger = logging.getLogger("lapwing.memory.quality_gate")


GateDecisionLiteral = Literal["accept", "reject", "defer"]
StabilityLiteral = Literal["transient", "session", "long_lived", "permanent"]
RoughCategoryLiteral = Literal[
    "preference",
    "identity",
    "project",
    "decision",
    "chitchat",
    "emotion",
    "task",
    "unknown",
]


# ── Hard reject ─────────────────────────────────────────────────────

# Deterministic patterns that must never reach long-term memory.
# Matched against the lower-cased candidate content.
_HARD_REJECT_PATTERNS = [
    re.compile(r"\bpassword\b", re.IGNORECASE),
    re.compile(r"密码"),
    re.compile(r"\btoken[:\s=]+[a-zA-Z0-9_\-./+=]{16,}"),
    re.compile(r"\bapi[_\-]?key[:\s=]+[a-zA-Z0-9_\-./+=]{16,}", re.IGNORECASE),
    re.compile(r"\bsecret[:\s=]+[a-zA-Z0-9_\-./+=]{16,}", re.IGNORECASE),
    re.compile(r"\bsk-[a-zA-Z0-9]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]

ACCEPT_THRESHOLD = 0.75
DEFER_THRESHOLD = 0.45


# ── Models ──────────────────────────────────────────────────────────


class MemoryGateDecision(BaseModel):
    """Result of one fast-gate evaluation."""
    source_id: str
    decision: GateDecisionLiteral
    salience: float = Field(ge=0.0, le=1.0)
    stability: StabilityLiteral
    rough_category: str
    reject_reason: str | None = None


class GateInput(BaseModel):
    source_id: str
    content: str
    speaker: str = ""
    context_summary: str = ""


@dataclass
class GateConfig:
    enabled: bool = True
    accept_threshold: float = ACCEPT_THRESHOLD
    defer_threshold: float = DEFER_THRESHOLD


class _LLMLike(Protocol):
    async def query_lightweight(
        self, system: str, user: str, *, slot: str | None = None
    ) -> str: ...


# ── Prompts (inline per Phase 1 note 1) ─────────────────────────────

_SYSTEM_PROMPT = """你是 Lapwing 的记忆门控官。
任务：判断一段对话内容是否值得写入 Lapwing 的长期 wiki 记忆层。

输出严格 JSON：
{
  "salience": float 0.0-1.0,
  "stability": "transient" | "session" | "long_lived" | "permanent",
  "category": "preference" | "identity" | "project" | "decision" | "chitchat" | "emotion" | "task" | "unknown",
  "score": float 0.0-1.0,
  "reason": str
}

评分依据：
+ salience（对 Kevin/Lapwing 长期关系的重要性）
+ recurrence（类似信息是否多次出现）
+ actionability（是否会影响 Lapwing 未来行为）
+ emotional_importance（是否涉及重要情感事件）
+ long_term_utility（一个月后是否仍有用）
- volatility（是否快速变化，例如临时心情）
- privacy_risk（是否过于敏感）
- redundancy（已有记忆是否覆盖）

阈值参考：
- score ≥ 0.75 → 值得编译进 wiki
- 0.45 ≤ score < 0.75 → 仅留在 episodic
- score < 0.45 → 完全丢弃

只输出 JSON，不输出多余文字。"""


def _build_user_prompt(item: GateInput) -> str:
    parts = []
    if item.context_summary:
        parts.append(f"最近上下文摘要：\n{item.context_summary}")
    speaker = item.speaker or "未知"
    parts.append(f"发言者：{speaker}")
    parts.append(f"内容：\n{item.content}")
    return "\n\n".join(parts)


def _hard_reject(content: str) -> str | None:
    for pat in _HARD_REJECT_PATTERNS:
        if pat.search(content):
            return f"hard_reject: matched {pat.pattern!r}"
    return None


def _parse_json_loose(raw: str) -> dict[str, Any] | None:
    """Find and parse the first JSON object in ``raw``. Tolerates fences."""
    if not raw:
        return None
    # strip ``` fences if present
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n", "", cleaned)
        cleaned = re.sub(r"\n```\s*$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None


# ── Gate ────────────────────────────────────────────────────────────


class FastGate:
    """Lightweight gate. Hard-reject patterns first, then LLM scoring."""

    def __init__(
        self,
        llm: _LLMLike,
        config: GateConfig | None = None,
        *,
        slot: str = "lightweight_judgment",
    ) -> None:
        self._llm = llm
        self._config = config or GateConfig()
        self._slot = slot

    async def evaluate(
        self,
        content: str,
        speaker: str,
        context_summary: str,
        *,
        source_id: str,
    ) -> MemoryGateDecision:
        return await self._evaluate_one(
            GateInput(
                source_id=source_id,
                content=content,
                speaker=speaker,
                context_summary=context_summary,
            )
        )

    async def batch_evaluate(
        self, items: list[GateInput]
    ) -> list[MemoryGateDecision]:
        results: list[MemoryGateDecision] = []
        for item in items:
            results.append(await self._evaluate_one(item))
        return results

    # ── internals ───────────────────────────────────────────────────

    async def _evaluate_one(self, item: GateInput) -> MemoryGateDecision:
        if not self._config.enabled:
            return MemoryGateDecision(
                source_id=item.source_id,
                decision="defer",
                salience=0.0,
                stability="transient",
                rough_category="unknown",
                reject_reason="gate_disabled",
            )

        hr = _hard_reject(item.content)
        if hr is not None:
            logger.info("[fast_gate] hard reject %s: %s", item.source_id, hr)
            return MemoryGateDecision(
                source_id=item.source_id,
                decision="reject",
                salience=0.0,
                stability="transient",
                rough_category="unknown",
                reject_reason=hr,
            )

        try:
            raw = await self._llm.query_lightweight(
                _SYSTEM_PROMPT,
                _build_user_prompt(item),
                slot=self._slot,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[fast_gate] llm error for %s: %s; defaulting to defer",
                item.source_id,
                exc,
            )
            return MemoryGateDecision(
                source_id=item.source_id,
                decision="defer",
                salience=0.0,
                stability="transient",
                rough_category="unknown",
                reject_reason=f"llm_error:{type(exc).__name__}",
            )

        parsed = _parse_json_loose(raw)
        if parsed is None:
            logger.warning(
                "[fast_gate] unparsable llm response for %s: %r",
                item.source_id,
                raw[:200],
            )
            return MemoryGateDecision(
                source_id=item.source_id,
                decision="defer",
                salience=0.0,
                stability="transient",
                rough_category="unknown",
                reject_reason="llm_unparsable",
            )

        score = _coerce_float(parsed.get("score"), default=0.0)
        salience = _coerce_float(parsed.get("salience"), default=score)
        stability = _coerce_enum(
            parsed.get("stability"),
            ("transient", "session", "long_lived", "permanent"),
            default="transient",
        )
        category = _coerce_str(parsed.get("category"), default="unknown")

        if score >= self._config.accept_threshold:
            decision: GateDecisionLiteral = "accept"
            reason = None
        elif score >= self._config.defer_threshold:
            decision = "defer"
            reason = parsed.get("reason") or None
        else:
            decision = "reject"
            reason = parsed.get("reason") or "low_score"

        return MemoryGateDecision(
            source_id=item.source_id,
            decision=decision,
            salience=max(0.0, min(1.0, salience)),
            stability=stability,
            rough_category=category,
            reject_reason=reason,
        )


def _coerce_float(value: Any, *, default: float) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_str(value: Any, *, default: str) -> str:
    if isinstance(value, str) and value:
        return value
    return default


def _coerce_enum(value: Any, allowed: tuple[str, ...], *, default: str) -> Any:
    if isinstance(value, str) and value in allowed:
        return value
    return default
