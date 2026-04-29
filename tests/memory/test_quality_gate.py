"""FastGate unit tests (Phase 1)."""

from __future__ import annotations

import pytest

from src.memory.quality_gate import FastGate, GateConfig, GateInput


class _StubLLM:
    """Returns a canned JSON string per call. No network."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    async def query_lightweight(self, system: str, user: str, *, slot=None):
        self.calls.append((system, user))
        if not self._responses:
            return ""
        return self._responses.pop(0)


def _gate(responses: list[str], **cfg) -> FastGate:
    return FastGate(_StubLLM(responses), GateConfig(**cfg))


async def test_accept_high_score():
    raw = '{"salience":0.9,"stability":"long_lived","category":"preference","score":0.85,"reason":"strong signal"}'
    g = _gate([raw])
    decision = await g.evaluate(
        "Kevin 偏好 blueprint-first 工作方式", "Kevin", "", source_id="m1",
    )
    assert decision.decision == "accept"
    assert decision.rough_category == "preference"
    assert decision.stability == "long_lived"


async def test_defer_mid_score():
    raw = '{"salience":0.5,"stability":"session","category":"task","score":0.55,"reason":"mid"}'
    g = _gate([raw])
    decision = await g.evaluate("我们今天写测试", "Kevin", "", source_id="m1")
    assert decision.decision == "defer"


async def test_reject_low_score():
    raw = '{"salience":0.1,"stability":"transient","category":"chitchat","score":0.2,"reason":"chitchat"}'
    g = _gate([raw])
    decision = await g.evaluate("早安", "Kevin", "", source_id="m1")
    assert decision.decision == "reject"


async def test_hard_reject_password_pattern_skips_llm():
    g = _gate([])  # no responses available — must short-circuit
    decision = await g.evaluate(
        "我的 password 是 abc123def", "Kevin", "", source_id="m1",
    )
    assert decision.decision == "reject"
    assert decision.reject_reason and "hard_reject" in decision.reject_reason


async def test_hard_reject_api_key_skips_llm():
    g = _gate([])
    decision = await g.evaluate(
        "API_KEY=sk-1234567890abcdefghijklmnop", "Kevin", "", source_id="m1",
    )
    assert decision.decision == "reject"


async def test_unparsable_response_defers():
    g = _gate(["this is not json at all"])
    decision = await g.evaluate("hello", "Kevin", "", source_id="m1")
    assert decision.decision == "defer"
    assert decision.reject_reason == "llm_unparsable"


async def test_disabled_gate_defers_silently():
    g = _gate([], enabled=False)
    decision = await g.evaluate("anything", "Kevin", "", source_id="m1")
    assert decision.decision == "defer"
    assert decision.reject_reason == "gate_disabled"


async def test_batch_evaluate_returns_one_per_input():
    raw_acc = '{"score":0.9,"salience":0.9,"stability":"permanent","category":"identity"}'
    raw_def = '{"score":0.5,"salience":0.5,"stability":"session","category":"task"}'
    g = _gate([raw_acc, raw_def])
    decisions = await g.batch_evaluate([
        GateInput(source_id="m1", content="A"),
        GateInput(source_id="m2", content="B"),
    ])
    assert [d.decision for d in decisions] == ["accept", "defer"]
