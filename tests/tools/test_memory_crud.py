"""tests/tools/test_memory_crud.py — Memory CRUD 工具测试。"""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock

from src.tools.types import ToolExecutionContext, ToolExecutionRequest
from src.tools.shell_executor import ShellResult


def _make_context() -> ToolExecutionContext:
    return ToolExecutionContext(
        execute_shell=AsyncMock(return_value=ShellResult(stdout="", stderr="", return_code=0)),
        shell_default_cwd="/tmp",
    )


def _make_request(name: str, **kwargs) -> ToolExecutionRequest:
    return ToolExecutionRequest(name=name, arguments=kwargs)


@pytest.fixture
def mem_dir(tmp_path):
    """重定向 MEMORY_DIR、IDENTITY_DIR、EVOLUTION_DIR 到临时目录。"""
    memory = tmp_path / "memory"
    identity = tmp_path / "identity"
    evolution = tmp_path / "evolution"
    memory.mkdir()
    identity.mkdir()
    evolution.mkdir()

    with (
        patch("src.tools.memory_crud.MEMORY_DIR", memory),
        patch("src.tools.memory_crud.IDENTITY_DIR", identity),
        patch("src.tools.memory_crud.EVOLUTION_DIR", evolution),
        patch("src.tools.memory_crud._ALLOWED_DIRS", (memory.resolve(), evolution.resolve())),
        patch("src.tools.memory_crud._FORBIDDEN_DIRS", (identity.resolve(),)),
    ):
        yield {"memory": memory, "identity": identity, "evolution": evolution}


# ─── _validate_path ───────────────────────────────────────────────

class TestValidatePath:
    def test_allows_memory_dir(self, mem_dir):
        from src.tools.memory_crud import _validate_path
        ok, result = _validate_path(str(mem_dir["memory"] / "KEVIN.md"))
        assert ok is True

    def test_allows_evolution_dir(self, mem_dir):
        from src.tools.memory_crud import _validate_path
        ok, result = _validate_path(str(mem_dir["evolution"] / "rules.md"))
        assert ok is True

    def test_blocks_identity_dir(self, mem_dir):
        from src.tools.memory_crud import _validate_path
        ok, result = _validate_path(str(mem_dir["identity"] / "constitution.md"))
        assert ok is False
        assert "宪法保护" in str(result)

    def test_blocks_arbitrary_path(self, mem_dir, tmp_path):
        from src.tools.memory_crud import _validate_path
        ok, result = _validate_path(str(tmp_path / "outside.txt"))
        assert ok is False

    def test_blocks_traversal_attempt(self, mem_dir):
        from src.tools.memory_crud import _validate_path
        # 尝试用 .. 跳出 memory 目录
        bad = str(mem_dir["memory"] / ".." / ".." / "etc" / "passwd")
        ok, result = _validate_path(bad)
        assert ok is False


# ─── memory_list ──────────────────────────────────────────────────

class TestMemoryList:
    async def test_empty_dir(self, mem_dir):
        from src.tools.memory_crud import _execute_memory_list
        ctx = _make_context()
        req = _make_request("memory_list")
        result = await _execute_memory_list(req, ctx)
        assert result.success is True
        assert "空" in result.payload["output"]

    async def test_lists_files(self, mem_dir):
        from src.tools.memory_crud import _execute_memory_list
        (mem_dir["memory"] / "KEVIN.md").write_text("hello", encoding="utf-8")
        ctx = _make_context()
        req = _make_request("memory_list")
        result = await _execute_memory_list(req, ctx)
        assert result.success is True
        assert "KEVIN.md" in result.payload["output"]

    async def test_invalid_subdirectory(self, mem_dir):
        from src.tools.memory_crud import _execute_memory_list
        ctx = _make_context()
        req = _make_request("memory_list", directory="../../etc")
        result = await _execute_memory_list(req, ctx)
        assert result.success is False


# ─── memory_read ──────────────────────────────────────────────────

class TestMemoryRead:
    async def test_missing_path_param(self, mem_dir):
        from src.tools.memory_crud import _execute_memory_read
        ctx = _make_context()
        req = _make_request("memory_read")
        result = await _execute_memory_read(req, ctx)
        assert result.success is False
        assert "缺少 path" in result.reason

    async def test_file_not_found(self, mem_dir):
        from src.tools.memory_crud import _execute_memory_read
        ctx = _make_context()
        req = _make_request("memory_read", path="nonexistent.md")
        result = await _execute_memory_read(req, ctx)
        assert result.success is False

    async def test_reads_with_line_numbers(self, mem_dir):
        from src.tools.memory_crud import _execute_memory_read
        f = mem_dir["memory"] / "notes.md"
        f.write_text("line one\nline two\n", encoding="utf-8")
        ctx = _make_context()
        req = _make_request("memory_read", path="notes.md")
        result = await _execute_memory_read(req, ctx)
        assert result.success is True
        output = result.payload["output"]
        assert "line one" in output
        assert "line two" in output
        assert "1\t" in output  # 行号

    async def test_blocks_identity_path(self, mem_dir):
        from src.tools.memory_crud import _execute_memory_read
        ctx = _make_context()
        req = _make_request("memory_read", path="../../identity/constitution.md")
        result = await _execute_memory_read(req, ctx)
        assert result.success is False


# ─── memory_edit ──────────────────────────────────────────────────

class TestMemoryEdit:
    async def test_successful_edit(self, mem_dir):
        from src.tools.memory_crud import _execute_memory_edit
        f = mem_dir["memory"] / "test.md"
        f.write_text("old content here", encoding="utf-8")
        ctx = _make_context()
        req = _make_request("memory_edit", path="test.md", old_text="old content", new_text="new content")
        result = await _execute_memory_edit(req, ctx)
        assert result.success is True
        assert f.read_text(encoding="utf-8") == "new content here"

    async def test_not_found_text(self, mem_dir):
        from src.tools.memory_crud import _execute_memory_edit
        f = mem_dir["memory"] / "test.md"
        f.write_text("some content", encoding="utf-8")
        ctx = _make_context()
        req = _make_request("memory_edit", path="test.md", old_text="missing text", new_text="x")
        result = await _execute_memory_edit(req, ctx)
        assert result.success is False
        assert "未找到" in result.reason

    async def test_multiple_matches_error(self, mem_dir):
        from src.tools.memory_crud import _execute_memory_edit
        f = mem_dir["memory"] / "test.md"
        f.write_text("abc abc abc", encoding="utf-8")
        ctx = _make_context()
        req = _make_request("memory_edit", path="test.md", old_text="abc", new_text="xyz")
        result = await _execute_memory_edit(req, ctx)
        assert result.success is False
        assert "3 次" in result.reason

    async def test_file_not_found(self, mem_dir):
        from src.tools.memory_crud import _execute_memory_edit
        ctx = _make_context()
        req = _make_request("memory_edit", path="missing.md", old_text="a", new_text="b")
        result = await _execute_memory_edit(req, ctx)
        assert result.success is False

    async def test_blocks_identity(self, mem_dir):
        from src.tools.memory_crud import _execute_memory_edit
        ctx = _make_context()
        req = _make_request("memory_edit", path="../../identity/soul.md", old_text="a", new_text="b")
        result = await _execute_memory_edit(req, ctx)
        assert result.success is False


# ─── memory_delete ────────────────────────────────────────────────

class TestMemoryDelete:
    async def test_delete_entire_file(self, mem_dir):
        from src.tools.memory_crud import _execute_memory_delete
        f = mem_dir["memory"] / "temp.md"
        f.write_text("some content", encoding="utf-8")
        ctx = _make_context()
        req = _make_request("memory_delete", path="temp.md")
        result = await _execute_memory_delete(req, ctx)
        assert result.success is True
        assert not f.exists()

    async def test_delete_text_from_file(self, mem_dir):
        from src.tools.memory_crud import _execute_memory_delete
        f = mem_dir["memory"] / "notes.md"
        f.write_text("keep this\nremove this line\nkeep this too", encoding="utf-8")
        ctx = _make_context()
        req = _make_request("memory_delete", path="notes.md", text_to_remove="\nremove this line")
        result = await _execute_memory_delete(req, ctx)
        assert result.success is True
        content = f.read_text(encoding="utf-8")
        assert "remove this line" not in content
        assert "keep this" in content

    async def test_delete_file_becomes_empty(self, mem_dir):
        from src.tools.memory_crud import _execute_memory_delete
        f = mem_dir["memory"] / "tiny.md"
        f.write_text("only text", encoding="utf-8")
        ctx = _make_context()
        req = _make_request("memory_delete", path="tiny.md", text_to_remove="only text")
        result = await _execute_memory_delete(req, ctx)
        assert result.success is True
        assert not f.exists()

    async def test_delete_text_not_found(self, mem_dir):
        from src.tools.memory_crud import _execute_memory_delete
        f = mem_dir["memory"] / "notes.md"
        f.write_text("some content", encoding="utf-8")
        ctx = _make_context()
        req = _make_request("memory_delete", path="notes.md", text_to_remove="not here")
        result = await _execute_memory_delete(req, ctx)
        assert result.success is False

    async def test_file_not_found(self, mem_dir):
        from src.tools.memory_crud import _execute_memory_delete
        ctx = _make_context()
        req = _make_request("memory_delete", path="ghost.md")
        result = await _execute_memory_delete(req, ctx)
        assert result.success is False

    async def test_blocks_identity(self, mem_dir):
        from src.tools.memory_crud import _execute_memory_delete
        ctx = _make_context()
        req = _make_request("memory_delete", path="../../identity/constitution.md")
        result = await _execute_memory_delete(req, ctx)
        assert result.success is False


# ─── memory_search ────────────────────────────────────────────────

class TestMemorySearch:
    async def test_missing_keyword(self, mem_dir):
        from src.tools.memory_crud import _execute_memory_search
        ctx = _make_context()
        req = _make_request("memory_search")
        result = await _execute_memory_search(req, ctx)
        assert result.success is False

    async def test_no_matches(self, mem_dir):
        from src.tools.memory_crud import _execute_memory_search
        (mem_dir["memory"] / "notes.md").write_text("hello world", encoding="utf-8")
        ctx = _make_context()
        req = _make_request("memory_search", keyword="xyz_not_here")
        result = await _execute_memory_search(req, ctx)
        assert result.success is True
        assert "未找到" in result.payload["output"]

    async def test_finds_matches(self, mem_dir):
        from src.tools.memory_crud import _execute_memory_search
        (mem_dir["memory"] / "KEVIN.md").write_text("Kevin 喜欢咖啡\n另一行", encoding="utf-8")
        ctx = _make_context()
        req = _make_request("memory_search", keyword="咖啡")
        result = await _execute_memory_search(req, ctx)
        assert result.success is True
        assert "咖啡" in result.payload["output"]
        assert "KEVIN.md" in result.payload["output"]

    async def test_case_insensitive(self, mem_dir):
        from src.tools.memory_crud import _execute_memory_search
        (mem_dir["memory"] / "notes.md").write_text("Hello World", encoding="utf-8")
        ctx = _make_context()
        req = _make_request("memory_search", keyword="hello")
        result = await _execute_memory_search(req, ctx)
        assert result.success is True
        assert "未找到" not in result.payload["output"]

    async def test_empty_memory_dir(self, mem_dir):
        from src.tools.memory_crud import _execute_memory_search
        ctx = _make_context()
        req = _make_request("memory_search", keyword="anything")
        result = await _execute_memory_search(req, ctx)
        assert result.success is True
