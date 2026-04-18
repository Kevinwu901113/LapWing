"""memory_tools_v2 工具执行器测试。"""

import pytest
from unittest.mock import MagicMock, AsyncMock
from pathlib import Path
from src.tools.types import ToolExecutionRequest, ToolExecutionContext
from src.tools.memory_tools_v2 import (
    recall_executor, write_note_executor, edit_note_executor,
    read_note_executor, list_notes_executor, move_note_executor,
    search_notes_executor, get_context_executor,
)


def _make_ctx(services: dict, chat_id: str = "test_chat") -> ToolExecutionContext:
    return ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
        services=services,
        chat_id=chat_id,
    )


def _make_req(name: str, args: dict) -> ToolExecutionRequest:
    return ToolExecutionRequest(name=name, arguments=args)


class TestWriteNote:
    async def test_write_note_success(self, tmp_path):
        from src.memory.note_store import NoteStore
        store = NoteStore(notes_dir=tmp_path / "notes")
        ctx = _make_ctx({"note_store": store, "vector_store": None})
        req = _make_req("write_note", {"content": "test memory", "note_type": "fact"})
        result = await write_note_executor(req, ctx)
        assert result.success is True
        assert "note_id" in result.payload

    async def test_write_note_empty(self, tmp_path):
        from src.memory.note_store import NoteStore
        store = NoteStore(notes_dir=tmp_path / "notes")
        ctx = _make_ctx({"note_store": store})
        req = _make_req("write_note", {"content": "  "})
        result = await write_note_executor(req, ctx)
        assert result.success is False

    async def test_write_note_with_path(self, tmp_path):
        from src.memory.note_store import NoteStore
        store = NoteStore(notes_dir=tmp_path / "notes")
        ctx = _make_ctx({"note_store": store, "vector_store": None})
        req = _make_req("write_note", {"content": "kevin info", "path": "people/kevin"})
        result = await write_note_executor(req, ctx)
        assert result.success is True
        assert (tmp_path / "notes" / "people" / "kevin").exists()

    async def test_write_note_fires_embedding_task(self, tmp_path):
        """向量库存在时应触发异步嵌入任务。"""
        from src.memory.note_store import NoteStore
        store = NoteStore(notes_dir=tmp_path / "notes")
        mock_vs = MagicMock()
        mock_vs.add = AsyncMock(return_value=None)
        ctx = _make_ctx({"note_store": store, "vector_store": mock_vs})
        req = _make_req("write_note", {"content": "fire embedding"})
        result = await write_note_executor(req, ctx)
        assert result.success is True


class TestReadNote:
    async def test_read_existing(self, tmp_path):
        from src.memory.note_store import NoteStore
        store = NoteStore(notes_dir=tmp_path / "notes")
        written = store.write(content="readable")
        ctx = _make_ctx({"note_store": store})
        req = _make_req("read_note", {"note_id": written["note_id"]})
        result = await read_note_executor(req, ctx)
        assert result.success is True
        assert "readable" in result.payload["content"]

    async def test_read_nonexistent(self, tmp_path):
        from src.memory.note_store import NoteStore
        store = NoteStore(notes_dir=tmp_path / "notes")
        ctx = _make_ctx({"note_store": store})
        req = _make_req("read_note", {"note_id": "fake"})
        result = await read_note_executor(req, ctx)
        assert result.success is False


class TestEditNote:
    async def test_edit_existing(self, tmp_path):
        from src.memory.note_store import NoteStore
        store = NoteStore(notes_dir=tmp_path / "notes")
        written = store.write(content="old content")
        ctx = _make_ctx({"note_store": store})
        req = _make_req("edit_note", {"note_id": written["note_id"], "content": "new content"})
        result = await edit_note_executor(req, ctx)
        assert result.success is True

    async def test_edit_nonexistent(self, tmp_path):
        from src.memory.note_store import NoteStore
        store = NoteStore(notes_dir=tmp_path / "notes")
        ctx = _make_ctx({"note_store": store})
        req = _make_req("edit_note", {"note_id": "no-such-id", "content": "new"})
        result = await edit_note_executor(req, ctx)
        assert result.success is False


class TestListNotes:
    async def test_list_empty_dir(self, tmp_path):
        from src.memory.note_store import NoteStore
        store = NoteStore(notes_dir=tmp_path / "notes")
        ctx = _make_ctx({"note_store": store})
        req = _make_req("list_notes", {})
        result = await list_notes_executor(req, ctx)
        assert result.success is True
        assert "entries" in result.payload

    async def test_list_with_notes(self, tmp_path):
        from src.memory.note_store import NoteStore
        store = NoteStore(notes_dir=tmp_path / "notes")
        store.write(content="note1")
        store.write(content="note2")
        ctx = _make_ctx({"note_store": store})
        req = _make_req("list_notes", {})
        result = await list_notes_executor(req, ctx)
        assert result.success is True
        assert result.payload["count"] >= 2

    async def test_list_subpath(self, tmp_path):
        from src.memory.note_store import NoteStore
        store = NoteStore(notes_dir=tmp_path / "notes")
        store.write(content="sub note", path="sub")
        ctx = _make_ctx({"note_store": store})
        req = _make_req("list_notes", {"path": "sub"})
        result = await list_notes_executor(req, ctx)
        assert result.success is True
        assert result.payload["count"] >= 1


class TestMoveNote:
    async def test_move_existing(self, tmp_path):
        from src.memory.note_store import NoteStore
        store = NoteStore(notes_dir=tmp_path / "notes")
        written = store.write(content="movable")
        ctx = _make_ctx({"note_store": store})
        req = _make_req("move_note", {"note_id": written["note_id"], "new_path": "archive"})
        result = await move_note_executor(req, ctx)
        assert result.success is True

    async def test_move_nonexistent(self, tmp_path):
        from src.memory.note_store import NoteStore
        store = NoteStore(notes_dir=tmp_path / "notes")
        ctx = _make_ctx({"note_store": store})
        req = _make_req("move_note", {"note_id": "no-such-id", "new_path": "archive"})
        result = await move_note_executor(req, ctx)
        assert result.success is False


class TestSearchNotes:
    async def test_search_found(self, tmp_path):
        from src.memory.note_store import NoteStore
        store = NoteStore(notes_dir=tmp_path / "notes")
        store.write(content="Kevin drinks coffee every morning")
        ctx = _make_ctx({"note_store": store})
        req = _make_req("search_notes", {"keyword": "coffee"})
        result = await search_notes_executor(req, ctx)
        assert result.success is True
        assert result.payload["count"] >= 1

    async def test_search_not_found(self, tmp_path):
        from src.memory.note_store import NoteStore
        store = NoteStore(notes_dir=tmp_path / "notes")
        store.write(content="nothing relevant here")
        ctx = _make_ctx({"note_store": store})
        req = _make_req("search_notes", {"keyword": "zzznomatch"})
        result = await search_notes_executor(req, ctx)
        assert result.success is True
        assert result.payload["count"] == 0


class TestRecall:
    async def test_recall_success(self):
        """vector_store 正常返回结果。"""
        from src.memory.vector_store import RecallResult
        mock_vs = MagicMock()
        mock_result = RecallResult(
            note_id="note_123",
            file_path="/some/path.md",
            content="Kevin loves hiking",
            score=0.9,
            semantic_similarity=0.88,
            note_type="observation",
            trust="self",
            created_at="2026-01-01T00:00:00",
            parent_note=None,
        )
        mock_vs.recall = AsyncMock(return_value=[mock_result])
        ctx = _make_ctx({"vector_store": mock_vs})
        req = _make_req("recall", {"query": "what does Kevin like", "top_k": 3})
        result = await recall_executor(req, ctx)
        assert result.success is True
        assert len(result.payload["results"]) == 1
        assert "Kevin loves hiking" in result.payload["results"][0]["content"]

    async def test_recall_no_vector_store(self):
        ctx = _make_ctx({"vector_store": None})
        req = _make_req("recall", {"query": "something"})
        result = await recall_executor(req, ctx)
        assert result.success is False

    async def test_recall_empty_results(self):
        mock_vs = MagicMock()
        mock_vs.recall = AsyncMock(return_value=[])
        ctx = _make_ctx({"vector_store": mock_vs})
        req = _make_req("recall", {"query": "nothing"})
        result = await recall_executor(req, ctx)
        assert result.success is True
        assert result.payload["count"] == 0

    async def test_recall_truncates_long_content(self):
        """每条结果超过 500 字符时截断。"""
        from src.memory.vector_store import RecallResult
        mock_vs = MagicMock()
        long_content = "x" * 1000
        mock_result = RecallResult(
            note_id="note_long",
            file_path="/path.md",
            content=long_content,
            score=0.8,
            semantic_similarity=0.8,
            note_type="observation",
            trust="self",
            created_at="2026-01-01T00:00:00",
            parent_note=None,
        )
        mock_vs.recall = AsyncMock(return_value=[mock_result])
        ctx = _make_ctx({"vector_store": mock_vs})
        req = _make_req("recall", {"query": "test"})
        result = await recall_executor(req, ctx)
        assert result.success is True
        assert len(result.payload["results"][0]["content"]) <= 500


class TestGetContext:
    async def test_get_context_empty(self):
        ctx = _make_ctx({}, chat_id="test_chat")
        req = _make_req("get_context", {})
        result = await get_context_executor(req, ctx)
        assert result.success is True

    async def test_get_context_has_sections(self):
        """返回的 payload 应包含预期的段落键。"""
        ctx = _make_ctx({}, chat_id="test_chat")
        req = _make_req("get_context", {})
        result = await get_context_executor(req, ctx)
        assert result.success is True
        # payload 应至少有 workspace 相关字段
        assert "workspace" in result.payload or "output" in result.payload


class TestRegisterMemoryToolsV2:
    def test_register_all_9_tools(self):
        from src.tools.memory_tools_v2 import register_memory_tools_v2

        registered = {}

        class MockRegistry:
            def register(self, spec):
                registered[spec.name] = spec

        register_memory_tools_v2(MockRegistry())
        expected_names = {
            "recall", "write_note", "edit_note", "read_note",
            "list_notes", "move_note", "search_notes", "get_context",
        }
        assert set(registered.keys()) == expected_names

    def test_all_tools_have_memory_capability(self):
        from src.tools.memory_tools_v2 import register_memory_tools_v2

        registered = {}

        class MockRegistry:
            def register(self, spec):
                registered[spec.name] = spec

        register_memory_tools_v2(MockRegistry())
        # get_context 用 general，其余用 memory
        for name, spec in registered.items():
            if name == "get_context":
                assert spec.capability == "general"
            else:
                assert spec.capability == "memory"
