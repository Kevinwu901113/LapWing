"""Tests for the TraceRecorder."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.core.trace_recorder import (
    ExecutionDetails,
    ExecutionTrace,
    SkillUsageInfo,
    TraceRecorder,
    _days_ago_str,
)


def _make_recorder(tmp_path: Path) -> TraceRecorder:
    traces_dir = tmp_path / "skill_traces"
    return TraceRecorder(traces_dir)


def _make_trace(recorder: TraceRecorder, category: str = "general") -> ExecutionTrace:
    return recorder.build_trace(
        user_request="帮我做个测试",
        output_summary="已完成测试",
        duration_seconds=1.5,
        category=category,
    )


# ---------------------------------------------------------------------------
# generate_trace_id
# ---------------------------------------------------------------------------


def test_generate_trace_id_format(tmp_path):
    recorder = _make_recorder(tmp_path)
    recorder.ensure_dir()
    trace_id = recorder.generate_trace_id("research")
    today = date.today().strftime("%Y-%m-%d")
    assert trace_id.startswith(f"{today}_research_")
    assert trace_id.endswith("_001")


def test_generate_trace_id_increments(tmp_path):
    recorder = _make_recorder(tmp_path)
    recorder.ensure_dir()

    id1 = recorder.generate_trace_id("general")
    # Write a file to simulate previous trace
    (recorder._traces_dir / f"{id1}.json").write_text("{}", encoding="utf-8")

    id2 = recorder.generate_trace_id("general")
    assert id2.endswith("_002")


def test_generate_trace_id_separate_categories(tmp_path):
    recorder = _make_recorder(tmp_path)
    recorder.ensure_dir()

    id1 = recorder.generate_trace_id("research")
    (recorder._traces_dir / f"{id1}.json").write_text("{}", encoding="utf-8")

    id2 = recorder.generate_trace_id("coding")
    # Different category, starts at 001
    assert id2.endswith("_001")


# ---------------------------------------------------------------------------
# record_trace
# ---------------------------------------------------------------------------


def test_record_trace_creates_file(tmp_path):
    recorder = _make_recorder(tmp_path)
    trace = _make_trace(recorder)
    path = recorder.record_trace(trace)

    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["user_request"] == "帮我做个测试"
    assert data["output_summary"] == "已完成测试"
    assert data["execution"]["total_duration_seconds"] == 1.5
    assert data["skill_used"] is None


def test_record_trace_with_skill_usage(tmp_path):
    recorder = _make_recorder(tmp_path)
    trace = recorder.build_trace(
        user_request="调研最新论文",
        output_summary="找到5篇论文",
        duration_seconds=30.0,
        skill_used=SkillUsageInfo(id="literature_survey", version=3, match_level="quick"),
    )
    path = recorder.record_trace(trace)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["skill_used"]["id"] == "literature_survey"
    assert data["skill_used"]["match_level"] == "quick"
    assert data["skill_used"]["version"] == 3
    assert data["skill_used"]["deviated"] is False


def test_record_trace_output_summary_truncated(tmp_path):
    recorder = _make_recorder(tmp_path)
    long_reply = "很长的回复" * 100
    trace = recorder.build_trace(
        user_request="测试",
        output_summary=long_reply,
        duration_seconds=1.0,
    )
    assert len(trace.output_summary) <= 200


# ---------------------------------------------------------------------------
# get_recent_traces
# ---------------------------------------------------------------------------


def test_get_recent_traces_returns_recent(tmp_path):
    recorder = _make_recorder(tmp_path)
    recorder.ensure_dir()

    # Create a trace from today
    trace = _make_trace(recorder)
    recorder.record_trace(trace)

    results = recorder.get_recent_traces(days=7)
    assert len(results) == 1


def test_get_recent_traces_empty_dir(tmp_path):
    recorder = _make_recorder(tmp_path)
    results = recorder.get_recent_traces(days=7)
    assert results == []


def test_get_recent_traces_filters_old(tmp_path):
    recorder = _make_recorder(tmp_path)
    recorder.ensure_dir()

    # Create an "old" trace file (30 days ago)
    old_date = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")
    old_file = recorder._traces_dir / f"{old_date}_general_001.json"
    old_file.write_text(json.dumps({"user_request": "old"}), encoding="utf-8")

    # Create a trace from today
    trace = _make_trace(recorder)
    recorder.record_trace(trace)

    results = recorder.get_recent_traces(days=7)
    # Only the recent trace should be returned
    assert len(results) == 1
    assert results[0]["user_request"] == "帮我做个测试"


# ---------------------------------------------------------------------------
# _days_ago_str
# ---------------------------------------------------------------------------


def test_days_ago_str():
    result = _days_ago_str(0)
    assert result == date.today().strftime("%Y-%m-%d")

    result_7 = _days_ago_str(7)
    expected = (date.today() - timedelta(days=7)).strftime("%Y-%m-%d")
    assert result_7 == expected
