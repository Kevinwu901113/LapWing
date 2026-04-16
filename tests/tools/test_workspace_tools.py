"""Workspace 工具沙箱测试。"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.tools.types import ToolExecutionContext, ToolExecutionRequest
from src.tools.workspace_tools import (
    ws_file_list_executor,
    ws_file_read_executor,
    ws_file_write_executor,
)


@pytest.fixture
def tmp_workspace(tmp_path, monkeypatch):
    import src.tools.workspace_tools as mod
    monkeypatch.setattr(mod, "AGENT_WORKSPACE", tmp_path)
    return tmp_path


def _make_ctx():
    return ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd=".",
        adapter="agent",
        user_id="agent:coder",
        auth_level=1,
    )


class TestWsFileWrite:
    async def test_write_in_workspace(self, tmp_workspace):
        req = ToolExecutionRequest(
            name="ws_file_write",
            arguments={"path": "hello.py", "content": "print('hi')"},
        )
        result = await ws_file_write_executor(req, _make_ctx())
        assert result.success
        assert (tmp_workspace / "hello.py").read_text() == "print('hi')"

    async def test_write_nested_path(self, tmp_workspace):
        req = ToolExecutionRequest(
            name="ws_file_write",
            arguments={"path": "sub/dir/file.txt", "content": "nested"},
        )
        result = await ws_file_write_executor(req, _make_ctx())
        assert result.success
        assert (tmp_workspace / "sub" / "dir" / "file.txt").read_text() == "nested"

    async def test_blocks_path_traversal(self, tmp_workspace):
        req = ToolExecutionRequest(
            name="ws_file_write",
            arguments={"path": "../../etc/passwd", "content": "hacked"},
        )
        result = await ws_file_write_executor(req, _make_ctx())
        assert not result.success
        assert "agent_workspace" in result.reason


class TestWsFileRead:
    async def test_read_existing(self, tmp_workspace):
        (tmp_workspace / "test.txt").write_text("hello")
        req = ToolExecutionRequest(
            name="ws_file_read",
            arguments={"path": "test.txt"},
        )
        result = await ws_file_read_executor(req, _make_ctx())
        assert result.success
        assert result.payload["content"] == "hello"

    async def test_read_nonexistent(self, tmp_workspace):
        req = ToolExecutionRequest(
            name="ws_file_read",
            arguments={"path": "nope.txt"},
        )
        result = await ws_file_read_executor(req, _make_ctx())
        assert not result.success


class TestWsFileList:
    async def test_list_files(self, tmp_workspace):
        (tmp_workspace / "a.py").write_text("")
        (tmp_workspace / "b.py").write_text("")
        req = ToolExecutionRequest(
            name="ws_file_list",
            arguments={"path": "."},
        )
        result = await ws_file_list_executor(req, _make_ctx())
        assert result.success
        assert "a.py" in result.payload["files"]
        assert "b.py" in result.payload["files"]
