"""Researcher Agent 测试。"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.researcher import Researcher
from src.agents.types import AgentMessage, ResearchResult, SourceRef
from src.core.runtime_profiles import AGENT_RESEARCHER_PROFILE
from src.logging.state_mutation_log import MutationType


def _make_deps():
    router = MagicMock()
    registry = MagicMock()
    mutation_log = AsyncMock()
    mutation_log.record = AsyncMock(return_value=1)
    return router, registry, mutation_log


class TestResearcherCreate:
    def test_creates_with_correct_spec(self):
        router, registry, mutation_log = _make_deps()
        r = Researcher.create(router, registry, mutation_log)
        assert r.spec.name == "researcher"
        assert r.spec.runtime_profile is AGENT_RESEARCHER_PROFILE
        assert "research" in r.spec.runtime_profile.tool_names
        assert "browse" in r.spec.runtime_profile.tool_names
        assert r.spec.model_slot == "agent_researcher"

    def test_system_prompt_mentions_sources(self):
        router, registry, mutation_log = _make_deps()
        r = Researcher.create(router, registry, mutation_log)
        assert "来源" in r.spec.system_prompt or "URL" in r.spec.system_prompt

    def test_no_tell_user_capability(self):
        """Step 6 改动 3：Researcher profile 不含 communication 能力。"""
        router, registry, mutation_log = _make_deps()
        r = Researcher.create(router, registry, mutation_log)
        assert "communication" not in r.spec.runtime_profile.capabilities
        assert "tell_user" not in r.spec.runtime_profile.tool_names


class TestResearcherConfig:
    """修复 C：max_rounds / timeout / tokens 来自 config，不再硬编码。"""

    def test_reads_overrides_from_settings(self, monkeypatch):
        from src.agents import researcher as researcher_module
        from src.config.settings import (
            AgentRoleConfig, AgentTeamConfig, LapwingSettings,
        )

        def _fake_get_settings():
            base = LapwingSettings()
            base.agent_team = AgentTeamConfig(
                enabled=True,
                researcher=AgentRoleConfig(
                    max_rounds=5, timeout_seconds=42, max_tokens=1024,
                ),
            )
            return base

        monkeypatch.setattr(researcher_module, "get_settings", _fake_get_settings)

        router, registry, mutation_log = _make_deps()
        r = Researcher.create(router, registry, mutation_log)
        assert r.spec.max_rounds == 5
        assert r.spec.timeout_seconds == 42
        assert r.spec.max_tokens == 1024

    def test_defaults_when_no_override(self):
        """没有覆盖时，spec 的值与 AgentRoleConfig 默认值一致。"""
        router, registry, mutation_log = _make_deps()
        r = Researcher.create(router, registry, mutation_log)
        # 与 config.toml [agent_team.researcher] 默认值一致
        assert r.spec.max_rounds == 15
        assert r.spec.timeout_seconds == 300
        assert r.spec.max_tokens == 40000


class TestResearcherStructuredResult:
    """Researcher returns ``{summary, sources}`` so consumers can read
    the structured shape directly without re-parsing JSON.
    """

    def test_postprocess_wraps_text_with_sources_from_evidence(self):
        router, registry, mutation_log = _make_deps()
        r = Researcher.create(router, registry, mutation_log)
        evidence = [
            {"tool": "research", "source_url": "https://a.com", "snippet": "title A"},
            {"tool": "research", "source_url": "https://b.com", "snippet": "title B"},
            # duplicate URL is deduped
            {"tool": "research", "source_url": "https://a.com", "snippet": "again"},
        ]
        result_text, structured = r._postprocess_result(
            "今天 LA 多云，最高 22 度。", evidence,
        )
        parsed = json.loads(result_text)
        assert parsed == structured
        assert structured["summary"] == "今天 LA 多云，最高 22 度。"
        sources = structured["sources"]
        assert len(sources) == 2
        assert sources[0]["ref"] == "https://a.com"
        assert sources[1]["ref"] == "https://b.com"

    def test_postprocess_with_no_evidence_returns_empty_sources(self):
        router, registry, mutation_log = _make_deps()
        r = Researcher.create(router, registry, mutation_log)
        result_text, structured = r._postprocess_result("没找到结果。", [])
        assert structured == {"summary": "没找到结果。", "sources": []}
        assert json.loads(result_text) == structured

    def test_extract_sources_handles_file_path(self):
        evidence = [
            {"tool": "research", "file_path": "/tmp/a.json", "snippet": "local"},
        ]
        sources = Researcher._extract_sources(evidence)
        assert len(sources) == 1
        assert sources[0].ref == "/tmp/a.json"


class TestResearcherFastPathStub:
    @pytest.mark.asyncio
    async def test_fast_path_always_returns_none_in_mvp(self):
        router, registry, mutation_log = _make_deps()
        r = Researcher.create(router, registry, mutation_log)
        for task in ("现在天气怎么样", "道奇比分", "USDCNY"):
            assert await r._try_fast_path(task, "realtime") is None

    def test_closed_form_candidate_classifier(self):
        assert Researcher._is_closed_form_candidate("今天天气怎么样") is True
        assert Researcher._is_closed_form_candidate("dodgers score") is True
        assert Researcher._is_closed_form_candidate("解释一下 RAG") is False
        assert Researcher._is_closed_form_candidate("") is False


class TestResearcherTelemetry:
    @pytest.mark.asyncio
    async def test_records_task_received_with_freshness_hint(self):
        router, registry, mutation_log = _make_deps()
        r = Researcher.create(router, registry, mutation_log)
        msg = AgentMessage(
            from_agent="lapwing",
            to_agent="researcher",
            task_id="t1",
            content="今天天气",
            message_type="request",
            freshness_hint="realtime",
        )
        await r._record_task_received(msg)
        mutation_log.record.assert_awaited_once()
        call = mutation_log.record.call_args
        assert call.args[0] == MutationType.RESEARCHER_TASK_RECEIVED
        payload = call.kwargs.get("payload") or (call.args[1] if len(call.args) > 1 else None)
        assert payload["task"] == "今天天气"
        assert payload["freshness_hint"] == "realtime"
        assert payload["fast_path_candidate"] is True


def test_research_result_to_dict_round_trip():
    result = ResearchResult(
        summary="hi",
        sources=[SourceRef(ref="https://x", title="T")],
    )
    d = result.to_dict()
    assert d == {"summary": "hi", "sources": [{"ref": "https://x", "title": "T"}]}
