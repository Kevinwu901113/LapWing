"""MemorySchema unit tests (Phase 1)."""

from __future__ import annotations

import pytest

from src.memory.memory_schema import MemorySchema


@pytest.fixture
def schema() -> MemorySchema:
    return MemorySchema()


def _good_frontmatter() -> dict:
    return {
        "id": "entity.kevin",
        "type": "entity",
        "title": "Kevin",
        "created_at": "2026-04-28T00:00:00+08:00",
        "updated_at": "2026-04-28T00:00:00+08:00",
        "compiler_version": "wiki-compiler-v1",
    }


def test_validate_valid_frontmatter(schema):
    errors = schema.validate_frontmatter(_good_frontmatter())
    assert errors == []


def test_missing_required_field(schema):
    fm = _good_frontmatter()
    del fm["title"]
    errors = schema.validate_frontmatter(fm)
    assert any(e.field == "title" for e in errors)


def test_invalid_type_enum(schema):
    fm = _good_frontmatter()
    fm["type"] = "not_a_type"
    errors = schema.validate_frontmatter(fm)
    assert any(e.field == "type" for e in errors)


def test_invalid_id_format(schema):
    fm = _good_frontmatter()
    fm["id"] = "no_dot_here"
    errors = schema.validate_frontmatter(fm)
    assert any(e.field == "id" for e in errors)


def test_confidence_out_of_range(schema):
    fm = _good_frontmatter()
    fm["confidence"] = 1.5
    errors = schema.validate_frontmatter(fm)
    assert any(e.field == "confidence" for e in errors)


def test_relation_missing_target(schema):
    fm = _good_frontmatter()
    fm["relations"] = [{"type": "creator_of"}]
    errors = schema.validate_frontmatter(fm)
    assert any("relations[0]" in (e.field or "") for e in errors)


def test_validate_text_no_frontmatter(schema):
    errors = schema.validate_text("# Just a heading\n\nbody")
    assert any("frontmatter" in e.message for e in errors)


def test_parse_extract_sections(schema):
    text = (
        "---\nid: entity.x\ntype: entity\ntitle: X\n"
        "created_at: 2026-04-28T00:00:00+08:00\n"
        "updated_at: 2026-04-28T00:00:00+08:00\n"
        "compiler_version: v1\n---\n"
        "# X\n\n## Current summary\n\nHello\n\n## Stable facts\n\n- one\n- two\n"
    )
    fm, body = schema.parse(text)
    assert fm["id"] == "entity.x"
    sections = schema.extract_sections(body)
    assert sections["Current summary"] == "Hello"
    assert "- one" in sections["Stable facts"]


def test_generate_frontmatter_includes_required(schema):
    fm_yaml = schema.generate_frontmatter("entity.x", "entity", "X")
    assert "id: entity.x" in fm_yaml
    assert "type: entity" in fm_yaml
    assert "compiler_version" in fm_yaml


def test_render_page_round_trips(schema):
    page = schema.render_page(
        "entity.x", "entity", "X",
        summary="Hello world",
        stable_facts="- one",
    )
    errors = schema.validate_text(page)
    assert errors == []
    fm, body = schema.parse(page)
    sections = schema.extract_sections(body)
    assert sections["Current summary"].strip() == "Hello world"
