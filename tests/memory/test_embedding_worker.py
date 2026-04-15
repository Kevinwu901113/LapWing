import pytest
from unittest.mock import MagicMock, AsyncMock
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
        mock_note_store.mark_embedded.assert_called_once_with("/tmp/notes/test.md", "v1")

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
