"""Tests for the TEMPORARY hallucination observation patch.

Blueprint v2.0 §5 isolation clause — Step 1 → Step 5 debt. These tests
(and the patch module) must be removed together in Step 5.
"""

from __future__ import annotations

import pytest

from src.logging.hallucination_patch import (
    HALLUCINATION_PHRASES_SOFT,
    HALLUCINATION_PHRASES_STRICT,
    _soft_phrase_is_genuine,
    check_and_record,
)
from src.logging.state_mutation_log import (
    MutationType,
    StateMutationLog,
    iteration_context,
    new_iteration_id,
)


@pytest.fixture
async def log(tmp_path):
    store = StateMutationLog(tmp_path / "ml.db", logs_dir=tmp_path / "logs")
    await store.init()
    yield store
    await store.close()


class TestStrictPhrases:
    @pytest.mark.parametrize("phrase", HALLUCINATION_PHRASES_STRICT)
    async def test_strict_phrase_recorded_with_zero_tool_calls(self, log, phrase):
        iid = new_iteration_id()
        with iteration_context(iid):
            matched = await check_and_record(f"嗯{phrase}呢。", log)
        assert matched == phrase
        rows = await log.query_by_type(MutationType.LLM_HALLUCINATION_SUSPECTED)
        assert len(rows) == 1
        assert rows[0].payload["matched_phrase"] == phrase
        assert rows[0].iteration_id == iid

    async def test_zero_match_records_nothing(self, log):
        iid = new_iteration_id()
        with iteration_context(iid):
            matched = await check_and_record("收到，我去查一下。", log)
        assert matched is None
        assert await log.query_by_type(MutationType.LLM_HALLUCINATION_SUSPECTED) == []


class TestToolCallSuppression:
    async def test_skipped_when_iteration_had_tool_call(self, log):
        iid = new_iteration_id()
        # Pre-insert a TOOL_CALLED mutation in this iteration
        await log.record(
            MutationType.TOOL_CALLED,
            {"tool_name": "read_file", "tool_call_id": "c1"},
            iteration_id=iid,
        )
        with iteration_context(iid):
            matched = await check_and_record("我刚才走神了", log)
        assert matched is None
        # Only TOOL_CALLED present, no LLM_HALLUCINATION_SUSPECTED
        assert await log.query_by_type(MutationType.LLM_HALLUCINATION_SUSPECTED) == []


class TestSoftPhraseDisambiguation:
    async def test_soft_phrase_genuine_is_recorded(self, log):
        iid = new_iteration_id()
        with iteration_context(iid):
            matched = await check_and_record("我在看了一下，其实找到了", log)
        # "在看了" matched; "查到了" also in text but earlier match wins.
        assert matched in {"在看了", "查到了"}
        assert len(await log.query_by_type(MutationType.LLM_HALLUCINATION_SUSPECTED)) == 1

    async def test_soft_phrase_quoted_is_ignored(self, log):
        iid = new_iteration_id()
        with iteration_context(iid):
            matched = await check_and_record(
                '他说"在看了"什么，我猜是股市。', log
            )
        assert matched is None

    async def test_soft_phrase_with_只是_is_ignored(self, log):
        iid = new_iteration_id()
        with iteration_context(iid):
            matched = await check_and_record("我只是在看了一眼日历", log)
        assert matched is None

    def test_soft_phrase_helper_handles_all_dismisses(self):
        assert _soft_phrase_is_genuine("查到了新闻", "查到了") is True
        assert _soft_phrase_is_genuine('"查到了"', "查到了") is False
        assert _soft_phrase_is_genuine("我只是查到了", "查到了") is False


class TestMissingContext:
    async def test_no_mutation_log_is_silent(self):
        matched = await check_and_record("我刚才走神了", mutation_log=None)
        assert matched is None

    async def test_no_iteration_id_is_silent(self, log):
        # Not entering iteration_context → current_iteration_id() returns None
        matched = await check_and_record("我刚才走神了", log)
        assert matched is None
        assert await log.query_by_type(MutationType.LLM_HALLUCINATION_SUSPECTED) == []
