"""Tests for src.utils.path_resolver."""

from config.settings import ROOT_DIR
from src.utils.path_resolver import resolve_tool_path


def test_data_prefix_corrected():
    resolved, note = resolve_tool_path("/data/consciousness/scratch_pad.md")
    assert resolved == str(ROOT_DIR / "data" / "consciousness" / "scratch_pad.md")
    assert note is not None
    assert "自动修正" in note


def test_consciousness_prefix_corrected():
    resolved, note = resolve_tool_path("/consciousness/working_memory.md")
    assert resolved == str(ROOT_DIR / "data" / "consciousness" / "working_memory.md")
    assert note is not None
    assert "自动修正" in note


def test_app_prefix_corrected():
    resolved, note = resolve_tool_path("/app/something")
    assert resolved == str(ROOT_DIR / "something")
    assert note is not None
    assert "自动修正" in note


def test_system_path_etc_unchanged():
    resolved, note = resolve_tool_path("/etc/nginx/nginx.conf")
    assert resolved == "/etc/nginx/nginx.conf"
    assert note is None


def test_system_path_tmp_unchanged():
    resolved, note = resolve_tool_path("/tmp/test.txt")
    assert resolved == "/tmp/test.txt"
    assert note is None


def test_relative_path_joined():
    resolved, note = resolve_tool_path("data/memory/facts.md")
    assert resolved == str(ROOT_DIR / "data" / "memory" / "facts.md")
    assert note is None


def test_correct_absolute_path_unchanged():
    full = str(ROOT_DIR / "data" / "xxx")
    resolved, note = resolve_tool_path(full)
    assert resolved == full
    assert note is None


def test_empty_path():
    resolved, note = resolve_tool_path("")
    assert resolved == ""
    assert note is None


def test_bare_data_no_trailing_slash():
    resolved, note = resolve_tool_path("/data")
    assert resolved == str(ROOT_DIR / "data")
    assert note is not None


def test_data_prefix_nested():
    resolved, note = resolve_tool_path("/data/memory/episodic/2026-04-22.md")
    assert resolved == str(ROOT_DIR / "data" / "memory" / "episodic" / "2026-04-22.md")
    assert note is not None


def test_whitespace_stripped():
    resolved, note = resolve_tool_path("  /data/test.md  ")
    assert resolved == str(ROOT_DIR / "data" / "test.md")
    assert note is not None
