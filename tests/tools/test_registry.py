"""ToolRegistry 测试。"""

from unittest.mock import AsyncMock

import pytest

from src.tools.registry import build_default_tool_registry
from src.tools.types import ToolExecutionContext, ToolExecutionRequest
from src.tools.shell_executor import ShellResult


@pytest.mark.asyncio
async def test_default_registry_exports_shell_tools_schema():
    registry = build_default_tool_registry()

    tools = registry.function_tools(capability="shell")
    names = {item["function"]["name"] for item in tools}

    assert names == {"execute_shell", "read_file", "write_file"}


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
