from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.core.task_runtime import TaskRuntime
from src.tools.types import ToolExecutionRequest, ToolExecutionResult


class _Store:
    def __init__(self):
        self.entries = []

    async def put(self, key, entry):
        self.entries.append((key, entry))


def _runtime():
    return TaskRuntime(router=MagicMock(), tool_registry=MagicMock())


def _request(question: str):
    return ToolExecutionRequest(name="research", arguments={"question": question})


def _execution(*, confidence, evidence, unclear="", answer="answer"):
    return ToolExecutionResult(
        success=True,
        payload={
            "answer": answer,
            "confidence": confidence,
            "evidence": evidence,
            "unclear": unclear,
        },
    )


def _ev(url: str):
    return {"source_url": url, "snippet": "s"}


@pytest.mark.asyncio
async def test_confidence_below_threshold_not_written():
    store = _Store()
    await _runtime()._writeback_to_ambient(
        _request("深度学习是什么"),
        _execution(confidence=0.6, evidence=[_ev("https://a.com/1"), _ev("https://b.com/2")]),
        store,
    )
    assert store.entries == []


@pytest.mark.asyncio
async def test_single_domain_evidence_not_written():
    store = _Store()
    await _runtime()._writeback_to_ambient(
        _request("深度学习是什么"),
        _execution(confidence=0.7, evidence=[_ev("https://a.com/1"), _ev("https://a.com/2")]),
        store,
    )
    assert store.entries == []


@pytest.mark.asyncio
async def test_unclear_result_not_written():
    store = _Store()
    await _runtime()._writeback_to_ambient(
        _request("深度学习是什么"),
        _execution(
            confidence=0.7,
            evidence=[_ev("https://a.com/1"), _ev("https://b.com/2")],
            unclear="证据冲突",
        ),
        store,
    )
    assert store.entries == []


@pytest.mark.asyncio
async def test_volatile_question_not_written():
    store = _Store()
    await _runtime()._writeback_to_ambient(
        _request("今天比分"),
        _execution(confidence=0.85, evidence=[_ev("https://a.com/1"), _ev("https://b.com/2")]),
        store,
    )
    assert store.entries == []


@pytest.mark.asyncio
async def test_high_quality_stable_question_written():
    store = _Store()
    await _runtime()._writeback_to_ambient(
        _request("深度学习是什么"),
        _execution(confidence=0.85, evidence=[_ev("https://a.com/1"), _ev("https://b.com/2")]),
        store,
    )
    assert len(store.entries) == 1
    _, entry = store.entries[0]
    assert entry.source == "research_writeback"
    assert entry.confidence == 0.85
