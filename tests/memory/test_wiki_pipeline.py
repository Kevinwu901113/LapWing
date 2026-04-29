"""End-to-end pipeline tests (Phase 2)."""

from __future__ import annotations

import json

import pytest

from src.memory.candidate_store import CandidateStore
from src.memory.memory_schema import MemorySchema
from src.memory.quality_gate import FastGate, GateConfig
from src.memory.wiki_compiler import WikiCompiler
from src.memory.wiki_pipeline import gate_and_enqueue, process_pending_candidates
from src.memory.wiki_store import WikiStore


class _StubLLM:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def query_lightweight(self, system, user, *, slot=None):
        self.calls.append((system, user))
        return self._responses.pop(0) if self._responses else ""


@pytest.fixture
async def env(tmp_path):
    wiki_dir = tmp_path / "wiki"
    (wiki_dir / "entities").mkdir(parents=True)
    (wiki_dir / "knowledge").mkdir(parents=True)
    (wiki_dir / "_meta").mkdir(parents=True)

    schema = MemorySchema()
    kevin = schema.render_page(
        "entity.kevin", "entity", "Kevin",
        summary="Kevin is the owner.",
        stable_facts="- prefers blueprint-first work",
        confidence=0.9, stability="permanent", privacy_level="personal",
        status="active",
    )
    (wiki_dir / "entities" / "kevin.md").write_text(kevin, encoding="utf-8")

    candidate_store = CandidateStore(db_path=tmp_path / "candidates.db")
    await candidate_store.init()

    write_enabled = {"v": True}
    wiki_store = WikiStore(
        wiki_dir=wiki_dir,
        db_path=tmp_path / "wiki.db",
        write_enabled_provider=lambda: write_enabled["v"],
    )
    await wiki_store.init()

    yield {
        "candidate_store": candidate_store,
        "wiki_store": wiki_store,
        "wiki_dir": wiki_dir,
        "write_enabled": write_enabled,
    }
    await candidate_store.close()
    await wiki_store.close()


async def test_gate_accept_enqueues_candidate(env):
    gate_response = json.dumps({
        "salience": 0.9, "stability": "long_lived",
        "category": "preference", "score": 0.85, "reason": "strong",
    })
    fast_gate = FastGate(_StubLLM([gate_response]), GateConfig())
    decision, cid = await gate_and_enqueue(
        fast_gate=fast_gate,
        candidate_store=env["candidate_store"],
        source_id="msg-1",
        content="Kevin 偏好 blueprint-first 工作方式",
        speaker="Kevin",
    )
    assert decision.decision == "accept"
    assert cid is not None
    pending = await env["candidate_store"].get_pending()
    assert len(pending) == 1


async def test_gate_reject_does_not_enqueue(env):
    fast_gate = FastGate(
        _StubLLM([]),
        GateConfig(),
    )
    # Hard-rejected by the gate (password pattern) — never reaches LLM
    decision, cid = await gate_and_enqueue(
        fast_gate=fast_gate,
        candidate_store=env["candidate_store"],
        source_id="msg-2",
        content="my password is supersecret123",
        speaker="Kevin",
    )
    assert decision.decision == "reject"
    assert cid is None
    assert await env["candidate_store"].get_pending() == []


async def test_process_pending_short_circuits_when_disabled(env):
    env["write_enabled"]["v"] = False
    counters = await process_pending_candidates(
        candidate_store=env["candidate_store"],
        wiki_compiler=WikiCompiler(_StubLLM([]), env["wiki_store"]),
        wiki_store=env["wiki_store"],
        write_enabled_provider=lambda: env["write_enabled"]["v"],
    )
    assert counters["pending"] == 0
    assert counters["compiled"] == 0


async def test_process_pending_compiles_and_applies(env):
    # 1. Stage a pending candidate via the gate
    gate_response = json.dumps({
        "salience": 0.9, "stability": "long_lived",
        "category": "preference", "score": 0.9, "reason": "strong",
    })
    fast_gate = FastGate(_StubLLM([gate_response]), GateConfig())
    _, cid = await gate_and_enqueue(
        fast_gate=fast_gate,
        candidate_store=env["candidate_store"],
        source_id="msg-1",
        content="Kevin 不喜欢冗长解释",
        speaker="Kevin",
    )
    assert cid

    # 2. Build the compiler with stubbed extract + compile responses
    extract_resp = json.dumps({
        "subject": "entity.kevin",
        "predicate": "dislikes",
        "object": "冗长解释",
        "type": "preference",
        "salience": 0.85, "confidence": 0.9,
        "stability": "long_lived", "privacy_level": "personal",
        "contradiction_risk": 0.0,
        "evidence_quote": "Kevin: 别啰嗦",
        "expires_at": None,
        "relations": [],
    })
    compile_resp = json.dumps({"patches": [{
        "operation": "add_fact",
        "section": "Stable facts",
        "content": "- dislikes 冗长解释",
        "reason": "from candidate",
        "risk": "low",
    }]})
    compiler = WikiCompiler(_StubLLM([extract_resp, compile_resp]), env["wiki_store"])

    counters = await process_pending_candidates(
        candidate_store=env["candidate_store"],
        wiki_compiler=compiler,
        wiki_store=env["wiki_store"],
        write_enabled_provider=lambda: env["write_enabled"]["v"],
    )
    assert counters["pending"] == 1
    assert counters["applied"] == 1
    assert counters["compiled"] == 1

    # 3. The wiki page should now contain the new fact
    page_text = (env["wiki_dir"] / "entities" / "kevin.md").read_text(encoding="utf-8")
    assert "冗长解释" in page_text


async def test_process_pending_high_risk_goes_to_queue(env):
    gate_response = json.dumps({
        "salience": 0.9, "stability": "long_lived",
        "category": "preference", "score": 0.9, "reason": "strong",
    })
    fast_gate = FastGate(_StubLLM([gate_response]), GateConfig())
    await gate_and_enqueue(
        fast_gate=fast_gate,
        candidate_store=env["candidate_store"],
        source_id="msg-1",
        content="Kevin 不再喜欢 blueprint-first",
        speaker="Kevin",
    )

    extract_resp = json.dumps({
        "subject": "entity.kevin",
        "predicate": "no_longer_likes",
        "object": "blueprint-first",
        "type": "preference",
        "salience": 0.9, "confidence": 0.5,
        "stability": "session", "privacy_level": "personal",
        "contradiction_risk": 0.9,
        "evidence_quote": "",
        "expires_at": None,
        "relations": [],
    })
    compile_resp = json.dumps({"patches": [{
        "operation": "supersede_fact",
        "content": "prefers blueprint-first work",
        "reason": "kevin reversed preference",
        "risk": "high",
    }]})
    compiler = WikiCompiler(_StubLLM([extract_resp, compile_resp]), env["wiki_store"])

    counters = await process_pending_candidates(
        candidate_store=env["candidate_store"],
        wiki_compiler=compiler,
        wiki_store=env["wiki_store"],
        write_enabled_provider=lambda: env["write_enabled"]["v"],
    )
    assert counters["queued"] == 1
    assert counters["applied"] == 0
    pending_patches = await env["wiki_store"].list_pending_patches()
    assert len(pending_patches) == 1
    assert pending_patches[0].risk == "high"
