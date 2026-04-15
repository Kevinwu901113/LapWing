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
