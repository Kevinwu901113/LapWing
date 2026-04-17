"""research 工具单元测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.research.types import Evidence, ResearchResult
from src.tools.research_tool import research_executor
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


def _ctx(engine=None):
    services = {}
    if engine is not None:
        services["research_engine"] = engine
    return ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
        services=services,
    )


async def test_missing_question_returns_error():
    result = await research_executor(
        ToolExecutionRequest(name="research", arguments={"question": "  "}),
        _ctx(engine=MagicMock()),
    )
    assert result.success is False
    assert "question" in result.payload["error"]


async def test_engine_missing_returns_error():
    result = await research_executor(
        ToolExecutionRequest(name="research", arguments={"question": "q"}),
        _ctx(engine=None),
    )
    assert result.success is False
    assert "research_engine" in result.payload["error"]


async def test_normal_path_returns_payload():
    engine = MagicMock()
    engine.research = AsyncMock(return_value=ResearchResult(
        answer="综合答案",
        evidence=[Evidence("https://a.com", "Source A", "quote A")],
        confidence="high",
        unclear="",
        search_backend_used=["tavily"],
    ))
    result = await research_executor(
        ToolExecutionRequest(name="research", arguments={"question": "道奇今天几比几", "scope": "global"}),
        _ctx(engine=engine),
    )
    assert result.success is True
    assert result.payload["answer"] == "综合答案"
    assert result.payload["confidence"] == "high"
    assert result.payload["evidence"][0]["source_url"] == "https://a.com"
    assert result.payload["backends"] == ["tavily"]
    engine.research.assert_awaited_once_with("道奇今天几比几", scope="global")


async def test_default_scope_is_auto():
    engine = MagicMock()
    engine.research = AsyncMock(return_value=ResearchResult(answer="x"))
    await research_executor(
        ToolExecutionRequest(name="research", arguments={"question": "q"}),
        _ctx(engine=engine),
    )
    engine.research.assert_awaited_once_with("q", scope="auto")


async def test_engine_exception_is_caught():
    engine = MagicMock()
    engine.research = AsyncMock(side_effect=RuntimeError("boom"))
    result = await research_executor(
        ToolExecutionRequest(name="research", arguments={"question": "q"}),
        _ctx(engine=engine),
    )
    assert result.success is False
    assert "boom" in result.payload["error"]


async def test_unclear_field_propagated():
    engine = MagicMock()
    engine.research = AsyncMock(return_value=ResearchResult(
        answer="x", confidence="medium", unclear="主队不明",
    ))
    result = await research_executor(
        ToolExecutionRequest(name="research", arguments={"question": "q"}),
        _ctx(engine=engine),
    )
    assert result.payload["unclear"] == "主队不明"
