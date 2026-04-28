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

    def test_write_rejects_md_suffix_in_path(self, note_store):
        # path 是目录；以 .md 结尾会与笔记文件命名冲突，
        # 让 rglob("*.md") 把目录当文件读取。
        with pytest.raises(ValueError, match=r"\.md"):
            note_store.write(content="x", path="consciousness/scratch_pad.md")
        with pytest.raises(ValueError, match=r"\.md"):
            note_store.write(content="x", path="foo.md/bar")


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
