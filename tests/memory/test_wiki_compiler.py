"""WikiCompiler unit tests (Phase 2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.memory.candidate import MemoryCandidate
from src.memory.candidate_store import PendingRecord
from src.memory.memory_schema import MemorySchema
from src.memory.wiki_compiler import WikiCompiler
from src.memory.wiki_store import WikiStore


class _StubLLM:
    """Stub LLM that returns canned JSON per call."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    async def query_lightweight(self, system: str, user: str, *, slot=None):
        self.calls.append((system, user))
        if not self._responses:
            return ""
        return self._responses.pop(0)


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

    write_enabled = {"v": True}
    store = WikiStore(
        wiki_dir=wiki_dir,
        db_path=tmp_path / "lapwing.db",
        write_enabled_provider=lambda: write_enabled["v"],
    )
    await store.init()
    yield store, wiki_dir, write_enabled
    await store.close()


def _candidate(**overrides) -> MemoryCandidate:
    base = dict(
        source_ids=["traj:1"],
        subject="entity.kevin",
        predicate="dislikes",
        object="冗长解释",
        type="preference",
        salience=0.85,
        confidence=0.9,
        stability="long_lived",
        privacy_level="personal",
    )
    base.update(overrides)
    return MemoryCandidate(**base)


def _compile_response(patches: list[dict]) -> str:
    return json.dumps({"patches": patches})


async def test_compile_existing_page_emits_patch(env):
    store, wiki_dir, _ = env
    raw = _compile_response([{
        "operation": "add_fact",
        "section": "Stable facts",
        "content": "- dislikes 冗长解释",
        "reason": "from new candidate",
        "risk": "low",
    }])
    compiler = WikiCompiler(_StubLLM([raw]), store)
    patches = await compiler.compile([_candidate()])
    assert len(patches) == 1
    p = patches[0]
    assert p.target_page_id == "entity.kevin"
    assert p.operation == "add_fact"
    assert p.before_hash is not None  # captured from existing page


async def test_compile_skips_out_of_mvp_subject(env):
    store, *_ = env
    compiler = WikiCompiler(_StubLLM([]), store)
    candidates = [_candidate(subject="entity.someone-else", type="relationship")]
    patches = await compiler.compile(candidates)
    assert patches == []


async def test_create_requires_three_long_lived_candidates(env):
    store, wiki_dir, _ = env
    compiler = WikiCompiler(_StubLLM([]), store)
    # Only 1 candidate for a non-existent decision page → no patch
    candidates = [
        _candidate(
            type="decision",
            subject="entity.unknown",
            predicate="adopt",
            object="strategy-x",
            stability="long_lived",
        ),
    ]
    patches = await compiler.compile(candidates)
    assert patches == []


async def test_create_with_three_long_lived_candidates(env):
    store, wiki_dir, _ = env
    compiler = WikiCompiler(_StubLLM([]), store)
    candidates = [
        _candidate(
            type="decision",
            subject="entity.unknown",
            predicate="adopt-strategy-x",
            object="for-perf",
            stability="permanent",
        )
        for _ in range(3)
    ]
    patches = await compiler.compile(candidates)
    assert len(patches) == 1
    assert patches[0].operation == "create"
    assert patches[0].target_page_id.startswith("knowledge.decision-")


async def test_high_risk_patch_passes_through_compiler(env):
    store, *_ = env
    raw = _compile_response([{
        "operation": "supersede_fact",
        "content": "prefers blueprint-first work",
        "reason": "kevin changed his mind",
        "risk": "high",
    }])
    compiler = WikiCompiler(_StubLLM([raw]), store)
    patches = await compiler.compile([_candidate()])
    assert len(patches) == 1
    assert patches[0].risk == "high"


async def test_extract_candidate_parses_llm_output(env):
    store, *_ = env
    extract_response = json.dumps({
        "subject": "entity.kevin",
        "predicate": "prefers",
        "object": "concise responses",
        "type": "preference",
        "salience": 0.8,
        "confidence": 0.85,
        "stability": "long_lived",
        "privacy_level": "personal",
        "contradiction_risk": 0.05,
        "evidence_quote": "Kevin: 别啰嗦",
        "expires_at": None,
        "relations": [],
    })
    compiler = WikiCompiler(_StubLLM([extract_response]), store)
    record = PendingRecord(
        id="candidate:m1",
        source_ids=["traj:1"],
        source_hash="h",
        status="pending",
        gate_score=0.85,
        rough_category="preference",
        candidate_json=None,
        created_at="2026-04-29T00:00:00+00:00",
        updated_at="2026-04-29T00:00:00+00:00",
    )
    candidate = await compiler.extract_candidate(record)
    assert candidate.subject == "entity.kevin"
    assert candidate.type == "preference"
    assert candidate.confidence == pytest.approx(0.85)


async def test_extract_candidate_drops_invalid_relation_types(env):
    store, *_ = env
    extract_response = json.dumps({
        "subject": "entity.kevin",
        "predicate": "x",
        "object": "y",
        "type": "preference",
        "salience": 0.8,
        "confidence": 0.8,
        "stability": "long_lived",
        "privacy_level": "personal",
        "contradiction_risk": 0.0,
        "evidence_quote": "",
        "expires_at": None,
        "relations": [
            {"type": "owns", "target": "entity.lapwing"},          # invalid
            {"type": "creator_of", "target": "entity.lapwing"},    # valid
        ],
    })
    compiler = WikiCompiler(_StubLLM([extract_response]), store)
    record = PendingRecord(
        id="candidate:m1",
        source_ids=[], source_hash="",
        status="pending", gate_score=0.8, rough_category="preference",
        candidate_json=None,
        created_at="2026-04-29T00:00:00+00:00",
        updated_at="2026-04-29T00:00:00+00:00",
    )
    candidate = await compiler.extract_candidate(record)
    assert len(candidate.relations) == 1
    assert candidate.relations[0].type == "creator_of"


async def test_compile_loads_policy_into_prompt(env, tmp_path):
    store, *_ = env
    policy = tmp_path / "policy.md"
    policy.write_text("# POLICY\nkevin requires concise patches", encoding="utf-8")
    raw = _compile_response([{
        "operation": "add_fact",
        "section": "Stable facts",
        "content": "- new",
        "reason": "test",
        "risk": "low",
    }])
    llm = _StubLLM([raw])
    compiler = WikiCompiler(llm, store, policy_path=policy)
    await compiler.compile([_candidate()])
    # The compile prompt should include the policy text
    user_prompt = llm.calls[0][1]
    assert "kevin requires concise patches" in user_prompt
