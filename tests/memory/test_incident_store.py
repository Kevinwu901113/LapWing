import pytest
pytestmark = pytest.mark.integration
"""Tests for src.memory.incident_store."""

import pytest
from pathlib import Path

from src.memory.incident_store import IncidentStore, looks_like_failure


class TestLooksLikeFailure:
    def test_error_keyword(self):
        assert looks_like_failure("工具调用 error: timeout")

    def test_chinese_failure(self):
        assert looks_like_failure("LLM 调用失败")

    def test_timeout(self):
        assert looks_like_failure("Agent 执行超时了")

    def test_circuit_breaker(self):
        assert looks_like_failure("loop detection circuit breaker triggered")

    def test_normal_text(self):
        assert not looks_like_failure("Kevin 问了天气，我查到今天是晴天")

    def test_empty(self):
        assert not looks_like_failure("")


class TestIncidentStore:
    @pytest.fixture
    def store(self, tmp_path):
        return IncidentStore(
            memory_dir=tmp_path / "incidents",
            vector_store=None,
        )

    @pytest.mark.asyncio
    async def test_add_incident_creates_file(self, store, tmp_path):
        entry = await store.add_incident(
            summary="Agent researcher 超时",
            title="Researcher 超时",
            source="episodic_extractor",
        )
        assert entry.incident_id.startswith("inc_")
        assert entry.title == "Researcher 超时"

        files = list((tmp_path / "incidents").glob("*.md"))
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert "异常事件记录" in content
        assert "Researcher 超时" in content
        assert "[episodic_extractor]" in content

    @pytest.mark.asyncio
    async def test_add_incident_empty_summary_raises(self, store):
        with pytest.raises(ValueError, match="summary"):
            await store.add_incident(summary="  ")

    @pytest.mark.asyncio
    async def test_multiple_incidents_same_day(self, store, tmp_path):
        await store.add_incident(summary="Error 1")
        await store.add_incident(summary="Error 2")
        files = list((tmp_path / "incidents").glob("*.md"))
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert "Error 1" in content
        assert "Error 2" in content

    @pytest.mark.asyncio
    async def test_query_without_vector_store_returns_empty(self, store):
        result = await store.query("anything")
        assert result == []
