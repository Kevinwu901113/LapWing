"""ToolRegistry 测试。"""

from unittest.mock import AsyncMock, patch

import pytest

from config.settings import SEARCH_MAX_RESULTS
from src.tools.registry import build_default_tool_registry
from src.tools.types import ToolExecutionContext, ToolExecutionRequest
from src.tools.shell_executor import ShellResult
from src.tools.web_fetcher import FetchResult


@pytest.mark.asyncio
async def test_default_registry_exports_shell_tools_schema():
    registry = build_default_tool_registry()

    tools = registry.function_tools(capability="shell")
    names = {item["function"]["name"] for item in tools}

    assert names == {"execute_shell", "read_file", "write_file"}


@pytest.mark.asyncio
async def test_default_registry_exports_web_tools_schema():
    registry = build_default_tool_registry()
    tools = {
        item["function"]["name"]: item["function"]["parameters"]
        for item in registry.function_tools()
    }

    assert "web_search" in tools
    assert tools["web_search"]["required"] == ["query"]
    assert "max_results" in tools["web_search"]["properties"]

    assert "web_fetch" in tools
    assert tools["web_fetch"]["required"] == ["url"]
    assert "max_chars" in tools["web_fetch"]["properties"]


@pytest.mark.asyncio
async def test_unknown_tool_returns_blocked_payload():
    registry = build_default_tool_registry()
    context = ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
    )

    result = await registry.execute(
        ToolExecutionRequest(name="unknown_tool", arguments={}),
        context=context,
    )

    assert result.success is False
    assert result.payload["blocked"] is True
    assert "未知工具" in result.payload["reason"]


@pytest.mark.asyncio
async def test_read_file_tool_payload_compatible():
    registry = build_default_tool_registry()
    execute_shell = AsyncMock(
        return_value=ShellResult(
            stdout="hello",
            stderr="",
            return_code=0,
            cwd="/tmp",
        )
    )
    context = ToolExecutionContext(
        execute_shell=execute_shell,
        shell_default_cwd="/tmp",
    )

    result = await registry.execute(
        ToolExecutionRequest(name="read_file", arguments={"path": "/tmp/a.txt"}),
        context=context,
    )

    execute_shell.assert_awaited_once_with("cat /tmp/a.txt")
    assert result.payload["path"] == "/tmp/a.txt"
    assert result.payload["return_code"] == 0


@pytest.mark.asyncio
async def test_write_file_tool_payload_compatible():
    registry = build_default_tool_registry()
    execute_shell = AsyncMock(
        side_effect=[
            ShellResult(stdout="", stderr="", return_code=0, cwd="/tmp"),
            ShellResult(stdout="", stderr="", return_code=0, cwd="/tmp"),
        ]
    )
    context = ToolExecutionContext(
        execute_shell=execute_shell,
        shell_default_cwd="/tmp",
    )

    result = await registry.execute(
        ToolExecutionRequest(
            name="write_file",
            arguments={"path": "/tmp/a.txt", "content": "abc"},
        ),
        context=context,
    )

    assert execute_shell.await_count == 2
    assert result.payload["path"] == "/tmp/a.txt"
    assert result.payload["action"] == "written"
    assert result.payload["return_code"] == 0


@pytest.mark.asyncio
async def test_internal_tools_are_hidden_from_function_tools():
    registry = build_default_tool_registry()
    names = {
        item["function"]["name"]
        for item in registry.function_tools(capabilities={"verify"})
    }
    assert names == set()


@pytest.mark.asyncio
async def test_internal_verify_tool_can_execute_when_directly_called():
    registry = build_default_tool_registry()
    context = ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
    )
    result = await registry.execute(
        ToolExecutionRequest(
            name="verify_code_result",
            arguments={
                "stdout": "",
                "stderr": "",
                "exit_code": 0,
                "timed_out": False,
            },
        ),
        context=context,
    )
    assert result.payload["passed"] is True


@pytest.mark.asyncio
async def test_activate_skill_tool_uses_skill_manager_service():
    class FakeSkillManager:
        def activate(self, name: str, user_input: str = ""):
            return {
                "skill_name": name,
                "skill_dir": "/tmp/skills/demo",
                "content": "body",
                "resources": ["scripts/run.sh"],
                "metadata": {"x": 1},
                "wrapped_content": "<skill_content/>",
            }

    registry = build_default_tool_registry()
    context = ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
        services={"skill_manager": FakeSkillManager()},
    )

    result = await registry.execute(
        ToolExecutionRequest(
            name="activate_skill",
            arguments={"name": "demo", "user_input": "test"},
        ),
        context=context,
    )

    assert result.success is True
    assert result.payload["skill_name"] == "demo"
    assert result.payload["content"] == "body"
    assert result.payload["resources"] == ["scripts/run.sh"]


@pytest.mark.asyncio
async def test_web_search_tool_uses_default_max_results_from_settings():
    registry = build_default_tool_registry()
    context = ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
    )

    with patch("src.tools.handlers.web_search.search", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = [
            {"title": "t1", "url": "https://a.example", "snippet": "s1"},
        ]
        result = await registry.execute(
            ToolExecutionRequest(name="web_search", arguments={"query": "lapwing"}),
            context=context,
        )

    mock_search.assert_awaited_once_with("lapwing", max_results=SEARCH_MAX_RESULTS)
    assert result.success is True
    assert result.payload["query"] == "lapwing"
    assert result.payload["count"] == 1
    assert result.payload["results"] == [{"title": "t1", "url": "https://a.example", "snippet": "s1"}]


@pytest.mark.asyncio
async def test_web_search_tool_clamps_max_results_and_returns_failure_payload():
    registry = build_default_tool_registry()
    context = ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
    )

    with patch("src.tools.handlers.web_search.search", new_callable=AsyncMock) as mock_search:
        mock_search.side_effect = RuntimeError("boom")
        result = await registry.execute(
            ToolExecutionRequest(
                name="web_search",
                arguments={"query": "A股 收盘", "max_results": 999},
            ),
            context=context,
        )

    mock_search.assert_awaited_once_with("A股 收盘", max_results=10)
    assert result.success is False
    assert result.payload == {"query": "A股 收盘", "count": 0, "results": []}
    assert "web_search 执行失败" in result.reason


@pytest.mark.asyncio
async def test_web_fetch_tool_returns_standard_payload_and_truncates_text():
    registry = build_default_tool_registry()
    context = ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
    )
    fetched = FetchResult(
        url="https://example.com/post",
        title="Example",
        text="x" * 120,
        success=True,
        error="",
    )

    with patch("src.tools.handlers.web_fetcher.fetch", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = fetched
        result = await registry.execute(
            ToolExecutionRequest(
                name="web_fetch",
                arguments={"url": "https://example.com/post", "max_chars": 50},
            ),
            context=context,
        )

    mock_fetch.assert_awaited_once_with("https://example.com/post")
    assert result.success is True
    assert result.payload["url"] == "https://example.com/post"
    assert result.payload["title"] == "Example"
    assert result.payload["success"] is True
    assert result.payload["error"] == ""
    assert len(result.payload["text"]) == 50


@pytest.mark.asyncio
async def test_web_fetch_tool_missing_url_returns_failure_payload():
    registry = build_default_tool_registry()
    context = ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
    )

    result = await registry.execute(
        ToolExecutionRequest(name="web_fetch", arguments={}),
        context=context,
    )

    assert result.success is False
    assert result.payload == {
        "url": "",
        "title": "",
        "text": "",
        "success": False,
        "error": "缺少 url 参数",
    }
    assert result.reason == "缺少 url 参数"
