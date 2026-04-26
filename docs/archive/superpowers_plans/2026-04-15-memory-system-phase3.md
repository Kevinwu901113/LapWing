# Phase 3: Memory System Rebuild — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement RAPTOR-style memory system with Working Set, Note Store (memory tree), Vector Store, conversation archive tiers, and 9 memory tools.

**Architecture:** Three-layer memory: conversation history (active/recent/deep archive via SQLite queries), working set (simple directory at `data/workspace/`), and memory tree (Markdown notes with YAML frontmatter + ChromaDB vector search). Tools let Lapwing read/write/search all layers. An EmbeddingWorker background task processes pending embeddings.

**Tech Stack:** Python, SQLite (existing), ChromaDB (existing dep), PyYAML, sentence-transformers (via ChromaDB default), pytest + pytest-asyncio.

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `src/memory/note_store.py` | NoteStore class — CRUD for Markdown notes with YAML frontmatter under `data/memory/notes/` |
| `src/memory/embedding_worker.py` | Background worker that processes `embedding_version: "pending"` notes |
| `src/tools/memory_tools_v2.py` | 9 tool executor functions (recall, write_note, edit_note, read_note, list_notes, move_note, search_notes, search_archive, get_context) |
| `tests/memory/test_note_store.py` | NoteStore unit tests |
| `tests/memory/test_vector_store_v2.py` | MemoryVectorStore unit tests |
| `tests/memory/test_embedding_worker.py` | EmbeddingWorker unit tests |
| `tests/tools/test_memory_tools_v2.py` | Memory tool executor tests |
| `tests/memory/test_conversation_archive.py` | Conversation archive tier tests |

### Modified Files
| File | Changes |
|------|---------|
| `src/memory/vector_store.py` | Rewrite: new `MemoryVectorStore` class with recall scoring, cluster dedup, access counting |
| `src/memory/conversation.py` | Add 2 archive-tier query methods: `get_active()`, `search_deep_archive()` |
| `src/app/container.py` | Wire NoteStore, new VectorStore, EmbeddingWorker; inject services; register new tools |
| `src/core/brain.py` | Inject note_store + conversation_memory into services dict |
| `src/tools/registry.py` | Replace old memory tool registrations (memory_note, memory_crud block) with new `register_memory_tools_v2()` call |

---

## Task 1: NoteStore — Data Model & CRUD

**Files:**
- Create: `src/memory/note_store.py`
- Test: `tests/memory/test_note_store.py`

- [ ] **Step 1: Write failing tests for NoteStore.write() and NoteStore.read()**

```python
# tests/memory/test_note_store.py
import pytest
from pathlib import Path
from src.memory.note_store import NoteStore


@pytest.fixture
def note_store(tmp_path):
    store = NoteStore(notes_dir=tmp_path / "notes")
    return store


class TestWrite:
    def test_write_default_path(self, note_store):
        result = note_store.write(content="Kevin likes coffee", note_type="observation")
        assert "note_id" in result
        assert "file_path" in result
        # File should exist
        p = Path(result["file_path"])
        assert p.exists()
        raw = p.read_text(encoding="utf-8")
        assert "Kevin likes coffee" in raw
        assert "note_type: observation" in raw

    def test_write_custom_path(self, note_store):
        result = note_store.write(
            content="He prefers dark roast",
            note_type="fact",
            path="people/kevin",
        )
        p = Path(result["file_path"])
        assert p.exists()
        assert "people/kevin" in str(p)

    def test_write_all_note_types(self, note_store):
        for nt in ("observation", "reflection", "fact", "summary"):
            result = note_store.write(content=f"test {nt}", note_type=nt)
            assert result["note_id"].startswith("note_")


class TestRead:
    def test_read_by_note_id(self, note_store):
        written = note_store.write(content="important thing")
        result = note_store.read(written["note_id"])
        assert result is not None
        assert result["content"] == "important thing"
        assert result["meta"]["note_type"] == "observation"

    def test_read_by_path(self, note_store):
        written = note_store.write(content="via path")
        result = note_store.read(written["file_path"])
        assert result is not None
        assert "via path" in result["content"]

    def test_read_nonexistent(self, note_store):
        assert note_store.read("nonexistent_id") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/memory/test_note_store.py -x -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.memory.note_store'`

- [ ] **Step 3: Implement NoteStore with write() and read()**

Create `src/memory/note_store.py` with:
- `NoteStore.__init__(self, notes_dir)` — accepts configurable notes_dir (default `Path("data/memory/notes")`)
- `NoteStore.write(content, note_type, path, source_refs, trust, parent_note)` — generates YAML frontmatter with id/timestamps/metadata, writes `.md` file, returns `{"note_id", "file_path"}`
- `NoteStore.read(note_id_or_path)` — resolves by path or note_id search, parses frontmatter+content, returns `{"meta", "content", "file_path"}` or `None`
- `NoteStore._resolve_path(note_id_or_path)` — lookup helper
- `NoteStore._parse_note(raw)` — splits `---frontmatter---content`

Key details from blueprint:
- note_id format: `note_{YYYYMMDD_HHMMSS}_{4hex}`
- filename format: `{note_type}_{YYYYMMDD_HHMMSS}_{4hex}.md`
- Timezone: `Asia/Taipei`
- frontmatter fields: id, created_at, updated_at, actor("lapwing"), note_type, source_refs, trust, embedding_version("pending"), parent_note

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/memory/test_note_store.py -x -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/memory/note_store.py tests/memory/test_note_store.py
git commit -m "feat(memory): add NoteStore with write/read and frontmatter parsing"
```

---

## Task 2: NoteStore — edit, list, move, search

**Files:**
- Modify: `src/memory/note_store.py`
- Modify: `tests/memory/test_note_store.py`

- [ ] **Step 1: Write failing tests for edit, list, move, search, embedding helpers**

```python
# Append to tests/memory/test_note_store.py

class TestEdit:
    def test_edit_updates_content(self, note_store):
        written = note_store.write(content="original")
        result = note_store.edit(written["note_id"], "updated content")
        assert result["success"] is True
        read_back = note_store.read(written["note_id"])
        assert read_back["content"] == "updated content"

    def test_edit_updates_timestamp(self, note_store):
        written = note_store.write(content="original")
        read1 = note_store.read(written["note_id"])
        original_updated = read1["meta"]["updated_at"]
        import time; time.sleep(0.01)
        note_store.edit(written["note_id"], "changed")
        read2 = note_store.read(written["note_id"])
        assert read2["meta"]["updated_at"] != original_updated

    def test_edit_sets_embedding_pending(self, note_store):
        written = note_store.write(content="original")
        note_store.mark_embedded(written["file_path"], "v1")
        note_store.edit(written["note_id"], "changed")
        read_back = note_store.read(written["note_id"])
        assert read_back["meta"]["embedding_version"] == "pending"

    def test_edit_nonexistent(self, note_store):
        result = note_store.edit("fake_id", "content")
        assert result["success"] is False


class TestListNotes:
    def test_list_root(self, note_store):
        note_store.write(content="a")
        note_store.write(content="b", path="sub")
        entries = note_store.list_notes()
        names = [e["name"] for e in entries]
        assert any(e["type"] == "file" for e in entries)
        assert "sub" in names

    def test_list_subdir(self, note_store):
        note_store.write(content="in sub", path="people")
        entries = note_store.list_notes("people")
        assert len(entries) == 1
        assert entries[0]["type"] == "file"

    def test_list_empty(self, note_store):
        assert note_store.list_notes("nonexistent") == []


class TestMove:
    def test_move_to_new_dir(self, note_store):
        written = note_store.write(content="movable")
        result = note_store.move(written["note_id"], "archive")
        assert result["success"] is True
        assert "archive" in result["new_path"]
        # Old path gone, readable from new location
        assert note_store.read(result["new_path"]) is not None

    def test_move_nonexistent(self, note_store):
        result = note_store.move("fake_id", "archive")
        assert result["success"] is False


class TestSearchKeyword:
    def test_keyword_found(self, note_store):
        note_store.write(content="Kevin likes dark roast coffee")
        results = note_store.search_keyword("coffee")
        assert len(results) == 1
        assert "coffee" in results[0]["snippet"].lower()

    def test_keyword_case_insensitive(self, note_store):
        note_store.write(content="UPPERCASE WORD")
        results = note_store.search_keyword("uppercase")
        assert len(results) == 1

    def test_keyword_not_found(self, note_store):
        note_store.write(content="something else")
        assert note_store.search_keyword("nonexistent") == []

    def test_keyword_limit(self, note_store):
        for i in range(5):
            note_store.write(content=f"match keyword here {i}")
        results = note_store.search_keyword("keyword", limit=3)
        assert len(results) == 3


class TestEmbeddingHelpers:
    def test_get_all_for_embedding(self, note_store):
        note_store.write(content="pending note")
        pending = note_store.get_all_for_embedding()
        assert len(pending) == 1
        assert pending[0]["meta"]["embedding_version"] == "pending"

    def test_mark_embedded(self, note_store):
        written = note_store.write(content="to embed")
        note_store.mark_embedded(written["file_path"], "v1")
        read_back = note_store.read(written["note_id"])
        assert read_back["meta"]["embedding_version"] == "v1"
        assert note_store.get_all_for_embedding() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/memory/test_note_store.py -x -q`
Expected: FAIL — `AttributeError: 'NoteStore' object has no attribute 'edit'`

- [ ] **Step 3: Implement edit, list_notes, move, search_keyword, get_all_for_embedding, mark_embedded**

Add to `src/memory/note_store.py`:
- `edit(note_id_or_path, new_content)` — preserves frontmatter, updates `updated_at` and sets `embedding_version: "pending"`, returns `{"success", "reason"}`
- `list_notes(path=None)` — iterates directory, returns `[{"name", "type", "note_id"}]`, skips dotfiles
- `move(note_id_or_path, new_path)` — creates target dir, renames file, returns `{"success", "reason", "new_path"}`
- `search_keyword(keyword, limit=10)` — case-insensitive rglob search, returns `[{"note_id", "file_path", "snippet"}]`
- `get_all_for_embedding()` — finds all notes with `embedding_version: "pending"`
- `mark_embedded(file_path, version)` — updates frontmatter `embedding_version` field
- `_extract_snippet(content, keyword, context_chars=100)` — snippet around keyword match

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/memory/test_note_store.py -x -q`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/memory/note_store.py tests/memory/test_note_store.py
git commit -m "feat(memory): add NoteStore edit/list/move/search/embedding helpers"
```

---

## Task 3: MemoryVectorStore — Rewrite

**Files:**
- Modify: `src/memory/vector_store.py` (full rewrite, keep old `VectorStore` renamed to `_LegacyVectorStore` temporarily)
- Create: `tests/memory/test_vector_store_v2.py`

- [ ] **Step 1: Write failing tests for MemoryVectorStore**

```python
# tests/memory/test_vector_store_v2.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.memory.vector_store import MemoryVectorStore


@pytest.fixture
def vector_store(tmp_path):
    return MemoryVectorStore(persist_dir=str(tmp_path / "chroma"))


class TestAdd:
    async def test_add_and_count(self, vector_store):
        await vector_store.add(
            note_id="note_001",
            content="Kevin likes coffee",
            metadata={"note_type": "fact", "trust": "self",
                      "created_at": "2026-04-15T10:00:00+08:00",
                      "file_path": "notes/fact.md"},
        )
        # ChromaDB default embedding should work
        count = vector_store.collection.count()
        assert count == 1


class TestRecall:
    async def test_recall_returns_results(self, vector_store):
        await vector_store.add("n1", "Kevin likes dark roast coffee",
                               {"note_type": "fact", "trust": "self",
                                "created_at": "2026-04-15T10:00:00+08:00",
                                "file_path": "a.md"})
        await vector_store.add("n2", "The weather is sunny today",
                               {"note_type": "observation", "trust": "self",
                                "created_at": "2026-04-15T10:00:00+08:00",
                                "file_path": "b.md"})
        results = await vector_store.recall("coffee preference", top_k=2)
        assert len(results) >= 1
        assert any(r.note_id == "n1" for r in results)  # coffee should appear

    async def test_recall_empty_store(self, vector_store):
        results = await vector_store.recall("anything")
        assert results == []

    async def test_recall_cluster_dedup(self, vector_store):
        # Add 3 very similar notes
        for i in range(3):
            await vector_store.add(
                f"dup_{i}", f"Kevin's favorite coffee is dark roast number {i}",
                {"note_type": "fact", "trust": "self",
                 "created_at": "2026-04-15T10:00:00+08:00",
                 "file_path": f"d{i}.md"})
        results = await vector_store.recall("dark roast coffee", top_k=5)
        # MAX_PER_CLUSTER=2, so at most 2 from the same cluster
        assert len(results) <= 3  # at most 2 from cluster + 1 standalone


class TestRemove:
    async def test_remove(self, vector_store):
        await vector_store.add("rm1", "to remove",
                               {"note_type": "fact", "trust": "self",
                                "created_at": "2026-04-15T10:00:00+08:00",
                                "file_path": "rm.md"})
        await vector_store.remove("rm1")
        assert vector_store.collection.count() == 0

    async def test_remove_nonexistent(self, vector_store):
        # Should not raise
        await vector_store.remove("nonexistent")


class TestRebuild:
    async def test_rebuild_replaces_all(self, vector_store):
        await vector_store.add("old1", "old data",
                               {"note_type": "fact", "trust": "self",
                                "created_at": "2026-04-15T10:00:00+08:00",
                                "file_path": "old.md"})
        await vector_store.rebuild([
            {"note_id": "new1", "content": "new data",
             "meta": {"note_type": "fact", "trust": "self",
                      "created_at": "2026-04-15T10:00:00+08:00",
                      "file_path": "new.md"}},
        ])
        assert vector_store.collection.count() == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/memory/test_vector_store_v2.py -x -q`
Expected: FAIL — `ImportError: cannot import name 'MemoryVectorStore'`

- [ ] **Step 3: Rewrite vector_store.py**

Rewrite `src/memory/vector_store.py`:
- Keep existing `VectorStore` class (rename to nothing — keep as-is for now, the old class stays)
- Add new `MemoryVectorStore` class below it
- `__init__(persist_dir)` — PersistentClient, single collection `lapwing_memory` with cosine space
- `add(note_id, content, metadata)` — upsert with ChromaDB default embeddings (no custom `_generate_embedding`, let ChromaDB handle it via `documents` parameter)
- `recall(query, top_k)` — query with `query_texts`, compute composite score (W_SEMANTIC=0.50, W_RECENCY=0.20, W_TRUST=0.10, W_SUMMARY_DEPTH=0.15, W_ACCESS_COUNT=0.05), cluster dedup via n-gram overlap, return `list[RecallResult]`
- `remove(note_id)` — delete by id
- `rebuild(notes)` — drop collection, recreate, re-add all
- `_increment_access(note_id)` — bump access_count in metadata
- `_content_overlap(a, b, n=3)` — n-gram overlap ratio

`RecallResult` dataclass: note_id, file_path, content, score, semantic_similarity, note_type, trust, created_at, parent_note

Important: Use `documents=[content]` in upsert (not `embeddings=`), so ChromaDB generates embeddings via its default model. Remove the blueprint's `_generate_embedding()` — it's unnecessary when using ChromaDB's built-in embedding function.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/memory/test_vector_store_v2.py -x -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/memory/vector_store.py tests/memory/test_vector_store_v2.py
git commit -m "feat(memory): add MemoryVectorStore with recall scoring and cluster dedup"
```

---

## Task 4: EmbeddingWorker

**Files:**
- Create: `src/memory/embedding_worker.py`
- Create: `tests/memory/test_embedding_worker.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/memory/test_embedding_worker.py
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from src.memory.embedding_worker import EmbeddingWorker


@pytest.fixture
def mock_note_store():
    store = MagicMock()
    store.get_all_for_embedding.return_value = [
        {
            "note_id": "note_001",
            "file_path": "/tmp/notes/test.md",
            "content": "test content",
            "meta": {"note_type": "fact", "trust": "self",
                     "created_at": "2026-04-15T10:00:00+08:00",
                     "file_path": "/tmp/notes/test.md"},
        }
    ]
    return store


@pytest.fixture
def mock_vector_store():
    store = MagicMock()
    store.add = AsyncMock()
    return store


class TestProcessPending:
    async def test_processes_pending_notes(self, mock_note_store, mock_vector_store):
        worker = EmbeddingWorker(mock_note_store, mock_vector_store)
        await worker.process_pending()
        mock_vector_store.add.assert_called_once_with(
            note_id="note_001",
            content="test content",
            metadata=mock_note_store.get_all_for_embedding.return_value[0]["meta"],
        )
        mock_note_store.mark_embedded.assert_called_once()

    async def test_handles_embedding_failure(self, mock_note_store, mock_vector_store):
        mock_vector_store.add = AsyncMock(side_effect=Exception("embedding failed"))
        worker = EmbeddingWorker(mock_note_store, mock_vector_store)
        # Should not raise
        await worker.process_pending()
        mock_note_store.mark_embedded.assert_not_called()

    async def test_empty_pending(self, mock_vector_store):
        note_store = MagicMock()
        note_store.get_all_for_embedding.return_value = []
        worker = EmbeddingWorker(note_store, mock_vector_store)
        await worker.process_pending()
        mock_vector_store.add.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/memory/test_embedding_worker.py -x -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement EmbeddingWorker**

Create `src/memory/embedding_worker.py`:
- `EmbeddingWorker.__init__(note_store, vector_store)` — stores references
- `process_pending()` — calls `note_store.get_all_for_embedding()`, for each: `await vector_store.add(...)`, then `note_store.mark_embedded(file_path, "v1")`. Catches exceptions per-note, logs warning, continues.
- `run_loop(interval=60)` — infinite loop: `process_pending()` then `await asyncio.sleep(interval)`

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/memory/test_embedding_worker.py -x -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/memory/embedding_worker.py tests/memory/test_embedding_worker.py
git commit -m "feat(memory): add EmbeddingWorker for async embedding processing"
```

---

## Task 5: Conversation Archive Tiers

**Files:**
- Modify: `src/memory/conversation.py`
- Create: `tests/memory/test_conversation_archive.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/memory/test_conversation_archive.py
import pytest
from datetime import datetime, timedelta, timezone
from src.memory.conversation import ConversationMemory


@pytest.fixture
async def memory(tmp_path):
    m = ConversationMemory(db_path=tmp_path / "test.db")
    await m.init_db()
    return m


class TestArchiveTiers:
    async def test_get_active_returns_recent(self, memory):
        """Messages from last 1 day should be returned."""
        await memory.append("chat1", "user", "recent message")
        results = await memory.get_active("chat1", limit=30)
        assert len(results) >= 1
        assert any("recent message" in r.get("content", "") for r in results)

    async def test_get_active_limit(self, memory):
        for i in range(10):
            await memory.append("chat1", "user", f"msg {i}")
        results = await memory.get_active("chat1", limit=5)
        assert len(results) <= 5

    async def test_search_deep_archive_keyword(self, memory):
        """Keyword search in deep archive (>7 days) — inserting old data directly."""
        # Insert a message with old timestamp directly via SQL
        old_ts = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
        await memory._db.execute(
            "INSERT INTO conversations (chat_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
            ("chat1", "user", "ancient conversation about dragons", old_ts),
        )
        await memory._db.commit()
        results = await memory.search_deep_archive("chat1", "dragons", limit=10)
        assert len(results) >= 1
        assert any("dragons" in r.get("content", "") for r in results)

    async def test_search_deep_archive_no_match(self, memory):
        results = await memory.search_deep_archive("chat1", "nonexistent_xyz", limit=10)
        assert results == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/memory/test_conversation_archive.py -x -q`
Expected: FAIL — `AttributeError: 'ConversationMemory' object has no attribute 'get_active'`

- [ ] **Step 3: Add archive methods to ConversationMemory**

Add to `src/memory/conversation.py` (class `ConversationMemory`):

```python
ACTIVE_WINDOW_DAYS = 1
RECENT_ARCHIVE_DAYS = 7

async def get_active(self, chat_id: str, limit: int = 30) -> list[dict]:
    """Get active conversations (last 1 day) for context injection."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=self.ACTIVE_WINDOW_DAYS)).isoformat()
    async with self._db.execute(
        "SELECT role, content, timestamp FROM conversations "
        "WHERE chat_id = ? AND timestamp > ? ORDER BY timestamp DESC LIMIT ?",
        (chat_id, cutoff, limit),
    ) as cursor:
        rows = await cursor.fetchall()
    return [{"role": r[0], "content": r[1], "timestamp": r[2]} for r in reversed(rows)]

async def search_deep_archive(self, chat_id: str, query: str, limit: int = 10) -> list[dict]:
    """Search conversations older than 7 days by keyword, scoped to chat_id."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=self.RECENT_ARCHIVE_DAYS)).isoformat()
    pattern = f"%{query}%"
    async with self._db.execute(
        "SELECT role, content, timestamp FROM conversations "
        "WHERE chat_id = ? AND timestamp < ? AND content LIKE ? ORDER BY timestamp DESC LIMIT ?",
        (chat_id, cutoff, pattern, limit),
    ) as cursor:
        rows = await cursor.fetchall()
    return [{"role": r[0], "content": r[1], "timestamp": r[2]} for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/memory/test_conversation_archive.py -x -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/memory/conversation.py tests/memory/test_conversation_archive.py
git commit -m "feat(memory): add conversation archive tier queries (active/deep)"
```

---

## Task 6: Memory Tool Executors

**Files:**
- Create: `src/tools/memory_tools_v2.py`
- Create: `tests/tools/test_memory_tools_v2.py`

- [ ] **Step 1: Write failing tests for tool executors**

```python
# tests/tools/test_memory_tools_v2.py
import pytest
from unittest.mock import MagicMock, AsyncMock
from pathlib import Path
from src.tools.types import ToolExecutionRequest, ToolExecutionContext
from src.tools.memory_tools_v2 import (
    recall_executor, write_note_executor, edit_note_executor,
    read_note_executor, list_notes_executor, move_note_executor,
    search_notes_executor, search_archive_executor, get_context_executor,
)


def _make_ctx(services: dict) -> ToolExecutionContext:
    return ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
        services=services,
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


class TestSearchArchive:
    async def test_archive_no_memory(self):
        ctx = _make_ctx({"conversation_memory": None})
        req = _make_req("search_archive", {"query": "test"})
        result = await search_archive_executor(req, ctx)
        assert result.success is False


class TestGetContext:
    async def test_get_context_empty(self, tmp_path):
        # Minimal test — workspace dir doesn't exist
        ctx = _make_ctx({})
        ctx = ToolExecutionContext(
            execute_shell=AsyncMock(),
            shell_default_cwd="/tmp",
            services={},
            chat_id="test_chat",
        )
        req = _make_req("get_context", {})
        result = await get_context_executor(req, ctx)
        assert result.success is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/tools/test_memory_tools_v2.py -x -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement all 9 tool executors**

Create `src/tools/memory_tools_v2.py` with:
- `recall_executor(req, ctx)` — gets vector_store from `ctx.services`, calls `recall()`, formats results with 500-char/2000-char budget
- `write_note_executor(req, ctx)` — gets note_store, calls `write()`, fires async embedding via `asyncio.create_task` if vector_store available
- `edit_note_executor(req, ctx)` — calls `note_store.edit()`, triggers re-embedding
- `read_note_executor(req, ctx)` — calls `note_store.read()`
- `list_notes_executor(req, ctx)` — calls `note_store.list_notes()`
- `move_note_executor(req, ctx)` — calls `note_store.move()`
- `search_notes_executor(req, ctx)` — calls `note_store.search_keyword()`
- `search_archive_executor(req, ctx)` — gets conversation_memory from services, calls `search_deep_archive(ctx.chat_id, query)`
- `get_context_executor(req, ctx)` — reads `data/workspace/` files, active tasks, recent conversation

Also add `register_memory_tools_v2(registry)` function that registers all 9 `ToolSpec` entries with their json_schema and executors.

Import types from `src.tools.types`: `ToolExecutionRequest`, `ToolExecutionContext`, `ToolExecutionResult`, `ToolSpec`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/tools/test_memory_tools_v2.py -x -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/tools/memory_tools_v2.py tests/tools/test_memory_tools_v2.py
git commit -m "feat(tools): add 9 memory tool executors with register function"
```

---

## Task 7: Container Wiring & Tool Registration

**Files:**
- Modify: `src/app/container.py`
- Modify: `src/core/brain.py`
- Modify: `src/tools/registry.py`

- [ ] **Step 1: Wire NoteStore and new VectorStore in container.py**

In `src/app/container.py`, inside `_configure_brain_dependencies()` (after existing vector store init around line 201):

```python
# After existing: self.brain.vector_store = VectorStore(self._data_dir / "chroma")

# Phase 3: NoteStore + MemoryVectorStore
from src.memory.note_store import NoteStore
note_store = NoteStore()  # uses default data/memory/notes/
self.brain._note_store = note_store

from src.memory.vector_store import MemoryVectorStore
memory_vector_store = MemoryVectorStore(persist_dir=str(self._data_dir / "chroma_memory"))
self.brain._memory_vector_store = memory_vector_store

# EmbeddingWorker (background task — stored for shutdown cleanup)
from src.memory.embedding_worker import EmbeddingWorker
import asyncio
embedding_worker = EmbeddingWorker(note_store, memory_vector_store)
self._embedding_task = asyncio.create_task(embedding_worker.run_loop(interval=60))

# Register Phase 3 memory tools
from src.tools.memory_tools_v2 import register_memory_tools_v2
register_memory_tools_v2(self.brain.tool_registry)
```

- [ ] **Step 2: Inject note_store and conversation_memory into brain services**

In `src/core/brain.py`, inside `_complete_chat()` where `services = {}` is built (around line 251), add:

```python
if hasattr(self, "_note_store") and self._note_store is not None:
    services["note_store"] = self._note_store
if hasattr(self, "_memory_vector_store") and self._memory_vector_store is not None:
    services["vector_store"] = self._memory_vector_store
services["conversation_memory"] = self.memory
```

- [ ] **Step 3: Replace old memory tool registrations in registry.py**

In `src/tools/registry.py`, inside `build_default_tool_registry()`:
- Remove the `memory_note` ToolSpec registration (lines ~626-654)
- Remove the `if MEMORY_CRUD_ENABLED:` block (lines ~676-756) — all 5 memory_crud tools
- Keep `session_search` as-is (it's conversation FTS, complementary)
- Update `report_incident` description (~line 1008): change "memory_note" reference to "write_note"

The new tools are registered via `register_memory_tools_v2()` called from container.py, so no new registration code needed in registry.py.

**Note on PHASE0_MODE:** `_configure_brain_dependencies()` returns early in Phase0, so Phase 3 tools won't be registered. The old `memory_note` removal from `registry.py` is fine because Phase0 uses `phase0_tools.py` which builds its own registry. The old `memory_crud` tools are gated behind `MEMORY_CRUD_ENABLED` which is off in Phase0.

- [ ] **Step 3b: Add embedding task cleanup to shutdown()**

In `src/app/container.py`, inside `shutdown()`, before `await self.brain.memory.close()`:

```python
# Cancel embedding worker
if hasattr(self, "_embedding_task") and self._embedding_task is not None:
    self._embedding_task.cancel()
    try:
        await self._embedding_task
    except asyncio.CancelledError:
        pass
```

- [ ] **Step 4: Create workspace directory**

```bash
mkdir -p data/workspace
```

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest tests/ -x -q`
Expected: PASS (some old tests referencing memory_note/memory_crud may need adjustment — see step 6)

- [ ] **Step 6: Fix any broken tests**

If tests reference `memory_note` or `memory_list/read/edit/delete/search` tools by name, update them to use the new tool names or remove the tests if they test the old tools directly.

- [ ] **Step 7: Commit**

```bash
git add src/app/container.py src/core/brain.py src/tools/registry.py
git commit -m "feat(memory): wire Phase 3 memory system in container and replace old tools"
```

---

## Task 8: Verify Compaction & Cleanup

**Files:**
- Verify: `src/core/brain.py` (compaction calls — already confirmed at lines 610, 672)
- Optional cleanup: old files

- [ ] **Step 1: Verify compaction trigger**

Confirm `brain.py` line 610 has `await self.compactor.try_compact(chat_id, session_id=session_id)`. This was confirmed during research — it exists and works.

No changes needed.

- [ ] **Step 2: Clear old ChromaDB data (if exists)**

```bash
rm -rf data/chroma/
```

Note: The new MemoryVectorStore uses `data/chroma_memory/` (separate from legacy `data/chroma/`), so this is safe. The legacy `VectorStore` class still uses `data/chroma/` for per-chat vector memory — it can coexist.

- [ ] **Step 3: Run full test suite**

Run: `python -m pytest tests/ -x -q`
Expected: All PASS

- [ ] **Step 4: Commit if any changes**

Only commit if there are actual file changes (e.g., config fixes). Stage specific files.

---

## Task 9: Integration Smoke Test

**Files:** No code changes — manual verification

- [ ] **Step 1: Start the application**

```bash
bash scripts/deploy.sh
```

- [ ] **Step 2: Verify startup logs**

Check logs for:
- `NoteStore` directory creation
- `MemoryVectorStore` initialization
- `EmbeddingWorker` loop start
- No import errors or crashes

- [ ] **Step 3: Test via QQ conversation**

1. Tell Lapwing something memorable: "我最近开始学弹吉他了"
2. Check if she calls `write_note` (check logs)
3. Ask: "你还记得我最近开始学什么吗？"
4. Check if she calls `recall` (check logs)
5. Ask: "看看你的笔记本" → should trigger `list_notes`

- [ ] **Step 4: Verify workspace directory**

```bash
ls -la data/workspace/
ls -la data/memory/notes/
```

- [ ] **Step 5: Final commit if needed**

Any config or minor fixes from smoke testing.
