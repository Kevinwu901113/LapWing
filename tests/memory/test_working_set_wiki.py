"""WorkingSet wiki injection tests (Phase 1)."""

from __future__ import annotations

import pytest

from src.memory.memory_schema import MemorySchema
from src.memory.working_set import WorkingSet


def _bootstrap_wiki(wiki_dir):
    schema = MemorySchema()
    (wiki_dir / "entities").mkdir(parents=True, exist_ok=True)
    kevin = schema.render_page(
        "entity.kevin", "entity", "Kevin",
        summary="Kevin is the owner of Lapwing.",
        stable_facts="- prefers blueprint-first work\n- runs the Lapwing project",
        aliases=["kevinwu"],
        status="active",
        confidence=0.9,
        stability="permanent",
        privacy_level="personal",
    )
    lapwing = schema.render_page(
        "entity.lapwing", "entity", "Lapwing",
        summary="Lapwing is a 24/7 AI companion.",
        stable_facts="- created by Kevin\n- runs Python + Tauri stack",
        status="active",
        confidence=0.9,
        stability="permanent",
        privacy_level="personal",
    )
    (wiki_dir / "entities" / "kevin.md").write_text(kevin, encoding="utf-8")
    (wiki_dir / "entities" / "lapwing.md").write_text(lapwing, encoding="utf-8")


@pytest.fixture
def wiki_dir(tmp_path):
    d = tmp_path / "wiki"
    _bootstrap_wiki(d)
    return d


async def test_wiki_injection_when_pages_exist(wiki_dir):
    ws = WorkingSet(wiki_dir=wiki_dir, wiki_enabled=True)
    snippets = await ws.retrieve(
        "anything",
        wiki_entities=["entity.kevin", "entity.lapwing"],
    )
    contents = [s.content for s in snippets.snippets]
    assert any("[wiki / entity.kevin]" in c for c in contents)
    assert any("[wiki / entity.lapwing]" in c for c in contents)


async def test_wiki_injection_skipped_when_disabled(wiki_dir):
    ws = WorkingSet(wiki_dir=wiki_dir, wiki_enabled=False)
    snippets = await ws.retrieve(
        "anything",
        wiki_entities=["entity.kevin", "entity.lapwing"],
    )
    assert snippets.snippets == ()


async def test_wiki_injection_skipped_when_no_entities(wiki_dir):
    ws = WorkingSet(wiki_dir=wiki_dir, wiki_enabled=True)
    snippets = await ws.retrieve("anything", wiki_entities=[])
    assert snippets.snippets == ()


async def test_wiki_injection_only_summary_and_facts(wiki_dir):
    ws = WorkingSet(wiki_dir=wiki_dir, wiki_enabled=True)
    snippets = await ws.retrieve(
        "anything", wiki_entities=["entity.kevin"],
    )
    assert len(snippets.snippets) == 1
    body = snippets.snippets[0].content
    # Phase 1 only injects "Current summary" + "Stable facts"
    assert "Kevin is the owner" in body
    assert "blueprint-first" in body
    # Other sections (Open questions, Recent changes, Evidence...) not injected
    assert "Open questions" not in body
    assert "Recent changes" not in body
    assert "Evidence" not in body


async def test_wiki_score_is_above_floor(wiki_dir):
    ws = WorkingSet(wiki_dir=wiki_dir, wiki_enabled=True)
    snippets = await ws.retrieve(
        "anything", wiki_entities=["entity.kevin"],
    )
    assert snippets.snippets[0].score >= 1000.0


async def test_wiki_token_budget_respected(wiki_dir):
    # tiny budget (200 chars total, 40% = 80) should drop the second page
    ws = WorkingSet(
        wiki_dir=wiki_dir,
        wiki_enabled=True,
        wiki_budget_ratio=0.40,
        total_char_budget=200,
    )
    snippets = await ws.retrieve(
        "anything",
        wiki_entities=["entity.kevin", "entity.lapwing"],
    )
    # only the first one should fit within 80 chars budget
    assert len(snippets.snippets) == 1
    assert snippets.snippets[0].note_id == "entity.kevin"


async def test_missing_wiki_page_skipped_silently(wiki_dir):
    ws = WorkingSet(wiki_dir=wiki_dir, wiki_enabled=True)
    snippets = await ws.retrieve(
        "anything",
        wiki_entities=["entity.kevin", "entity.unknown"],
    )
    assert len(snippets.snippets) == 1
    assert snippets.snippets[0].note_id == "entity.kevin"
