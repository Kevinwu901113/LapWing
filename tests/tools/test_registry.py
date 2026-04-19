"""ToolRegistry 测试。"""

from unittest.mock import AsyncMock, patch

import pytest

from src.tools.registry import ToolNotRegisteredError, build_default_tool_registry
from src.tools.types import ToolExecutionContext, ToolExecutionRequest
from src.tools.shell_executor import ShellResult


def _registry_with_personal_tools():
    """构建注册了 personal_tools 的 registry（Phase 4）。"""
    registry = build_default_tool_registry()
    from src.tools.personal_tools import register_personal_tools
    register_personal_tools(registry, {})
    return registry


@pytest.mark.asyncio
async def test_default_registry_exports_shell_tools_schema():
    registry = build_default_tool_registry()

    tools = registry.function_tools(capability="shell")
    names = {item["function"]["name"] for item in tools}

    assert {"execute_shell", "read_file", "write_file"}.issubset(names)


@pytest.mark.asyncio
async def test_default_registry_exports_browse_schema():
    """personal_tools 注册的 browse 工具暴露在默认 registry 中。"""
    registry = _registry_with_personal_tools()
    tools = {
        item["function"]["name"]: item["function"]["parameters"]
        for item in registry.function_tools()
    }

    assert "browse" in tools
    assert tools["browse"]["required"] == ["url"]


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
async def test_write_file_tool_payload_compatible(tmp_path):
    registry = build_default_tool_registry()
    execute_shell = AsyncMock(
        return_value=ShellResult(stdout="", stderr="", return_code=0, cwd="/tmp"),
    )
    context = ToolExecutionContext(
        execute_shell=execute_shell,
        shell_default_cwd="/tmp",
    )
    target = tmp_path / "a.txt"

    result = await registry.execute(
        ToolExecutionRequest(
            name="write_file",
            arguments={"path": str(target), "content": "abc"},
        ),
        context=context,
    )

    assert result.success is True
    assert result.payload["path"] == str(target)
    assert result.payload["action"] == "written"
    assert result.payload["return_code"] == 0
    assert target.read_text() == "abc"


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






def test_tool_names_whitelist_raises_on_unknown_tool():
    """Step 1i: whitelist names not in registry must raise, not silently skip."""
    registry = build_default_tool_registry()
    with pytest.raises(ToolNotRegisteredError, match="nonexistent_tool"):
        registry.list_tools(tool_names={"get_time", "nonexistent_tool"})


def test_function_tools_whitelist_raises_on_unknown_tool():
    registry = build_default_tool_registry()
    with pytest.raises(ToolNotRegisteredError):
        registry.function_tools(tool_names={"totally_fake_tool"})


def test_tool_names_whitelist_accepts_only_registered_names():
    """Positive control — registered names resolve cleanly."""
    registry = build_default_tool_registry()
    specs = registry.list_tools(tool_names={"execute_shell", "read_file"})
    assert {s.name for s in specs} == {"execute_shell", "read_file"}
