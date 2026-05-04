from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from src.ambient.models import AmbientEntry
from src.core.task_runtime import TaskRuntime
from src.tools.types import ToolExecutionRequest


class _Store:
    def __init__(self, entries):
        self._entries = tuple(entries)

    async def get_all_fresh(self):
        return self._entries


def _runtime():
    return TaskRuntime(router=MagicMock(), tool_registry=MagicMock())


def _request(question: str):
    return ToolExecutionRequest(name="research", arguments={"question": question})


def _entry(*, confidence=0.9, fetched_at=None, topic="深度 学习", key="k1"):
    fetched_at = fetched_at or datetime.now(timezone.utc).isoformat()
    return AmbientEntry(
        key=key,
        category="research",
        topic=topic,
        data=json.dumps({"evidence": [{"source_url": "https://example.com"}]}),
        summary="cached answer",
        fetched_at=fetched_at,
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        source="research_writeback",
        confidence=confidence,
    )


@pytest.mark.asyncio
async def test_low_confidence_entry_does_not_hit():
    result = await _runtime()._try_ambient_cache(
        _request("深度 学习 是什么"),
        _Store([_entry(confidence=0.75)]),
    )
    assert result is None


@pytest.mark.asyncio
async def test_old_entry_does_not_hit():
    old = (datetime.now(timezone.utc) - timedelta(minutes=90)).isoformat()
    result = await _runtime()._try_ambient_cache(
        _request("深度 学习 是什么"),
        _Store([_entry(fetched_at=old)]),
    )
    assert result is None


@pytest.mark.asyncio
async def test_volatile_question_disables_cache():
    result = await _runtime()._try_ambient_cache(
        _request("今天比分 深度 学习"),
        _Store([_entry(confidence=0.95)]),
    )
    assert result is None


@pytest.mark.asyncio
async def test_cache_hit_marks_payload():
    result = await _runtime()._try_ambient_cache(
        _request("深度 学习 是什么"),
        _Store([_entry(confidence=0.95)]),
    )
    assert result is not None
    assert result.success
    assert result.payload["cache_hit"] is True
    assert result.payload["cached_at"]
