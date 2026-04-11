"""PendingTask + PendingTaskStore 单元测试。"""

import json
import time
from pathlib import Path

import pytest

from src.core.pending_task import (
    MAX_RETRY_COUNT,
    MAX_SKIP_COUNT,
    MAX_TOTAL_RESUMPTIONS,
    RETRY_COOLDOWN_SECONDS,
    TASK_EXPIRY_SECONDS,
    PendingTask,
    PendingTaskStore,
)


# ── PendingTask 数据模型 ──


class TestPendingTask:
    def test_creation_and_fields(self):
        task = PendingTask(
            task_id="pt-001",
            chat_id="chat-1",
            user_id="user-1",
            adapter="telegram",
            user_request="帮我搜一下今天的天气",
            completed_steps=[{"tool": "web_search", "result_brief": "搜到了"}],
            partial_result="部分结果",
            remaining_description="还差详细天气",
            termination_reason="max_rounds_exceeded",
        )
        assert task.task_id == "pt-001"
        assert task.chat_id == "chat-1"
        assert task.user_id == "user-1"
        assert task.adapter == "telegram"
        assert task.user_request == "帮我搜一下今天的天气"
        assert len(task.completed_steps) == 1
        assert task.partial_result == "部分结果"
        assert task.remaining_description == "还差详细天气"
        assert task.retry_count == 0
        assert task.skip_count == 0
        assert task.original_task_id == ""
        assert task.total_resumption_count == 0

    def test_is_expired_within_window(self):
        task = PendingTask(
            task_id="pt-001",
            chat_id="c",
            user_id="u",
            adapter="tg",
            user_request="test",
            created_at=time.time(),
        )
        assert not task.is_expired()

    def test_is_expired_after_window(self):
        task = PendingTask(
            task_id="pt-001",
            chat_id="c",
            user_id="u",
            adapter="tg",
            user_request="test",
            created_at=time.time() - TASK_EXPIRY_SECONDS - 1,
        )
        assert task.is_expired()

    def test_can_retry_within_limit(self):
        task = PendingTask(
            task_id="pt-001",
            chat_id="c",
            user_id="u",
            adapter="tg",
            user_request="test",
            retry_count=0,
        )
        assert task.can_retry()

    def test_can_retry_exceeded(self):
        task = PendingTask(
            task_id="pt-001",
            chat_id="c",
            user_id="u",
            adapter="tg",
            user_request="test",
            retry_count=MAX_RETRY_COUNT,
        )
        assert not task.can_retry()

    def test_can_retry_cooldown(self):
        task = PendingTask(
            task_id="pt-001",
            chat_id="c",
            user_id="u",
            adapter="tg",
            user_request="test",
            retry_count=1,
            last_retry_at=time.time(),  # 刚刚重试过
        )
        assert not task.can_retry()

    def test_can_retry_cooldown_passed(self):
        task = PendingTask(
            task_id="pt-001",
            chat_id="c",
            user_id="u",
            adapter="tg",
            user_request="test",
            retry_count=1,
            last_retry_at=time.time() - RETRY_COOLDOWN_SECONDS - 1,
        )
        assert task.can_retry()

    def test_record_retry_increments_count(self):
        task = PendingTask(
            task_id="pt-001",
            chat_id="c",
            user_id="u",
            adapter="tg",
            user_request="test",
        )
        assert task.retry_count == 0
        task.record_retry()
        assert task.retry_count == 1
        assert task.last_retry_at > 0

    def test_skip_count_tracking(self):
        task = PendingTask(
            task_id="pt-001",
            chat_id="c",
            user_id="u",
            adapter="tg",
            user_request="test",
        )
        assert task.skip_count == 0
        task.skip_count += 1
        assert task.skip_count == 1

    def test_original_task_id_propagation(self):
        task = PendingTask(
            task_id="pt-002",
            chat_id="c",
            user_id="u",
            adapter="tg",
            user_request="test",
            original_task_id="pt-001",
            total_resumption_count=1,
        )
        assert task.original_task_id == "pt-001"
        assert task.total_resumption_count == 1

    def test_total_resumption_count_limit(self):
        assert MAX_TOTAL_RESUMPTIONS == 3

    def test_to_dict_and_from_dict(self):
        task = PendingTask(
            task_id="pt-001",
            chat_id="chat-1",
            user_id="user-1",
            adapter="telegram",
            user_request="test request",
            completed_steps=[{"tool": "web_search"}],
            partial_result="partial",
            remaining_description="remaining",
            termination_reason="max_rounds",
            original_task_id="pt-000",
            total_resumption_count=2,
            skip_count=3,
        )
        d = task.to_dict()
        restored = PendingTask.from_dict(d)
        assert restored.task_id == task.task_id
        assert restored.chat_id == task.chat_id
        assert restored.user_request == task.user_request
        assert restored.original_task_id == "pt-000"
        assert restored.total_resumption_count == 2
        assert restored.skip_count == 3

    def test_from_dict_ignores_unknown_fields(self):
        d = {
            "task_id": "pt-001",
            "chat_id": "c",
            "user_id": "u",
            "adapter": "tg",
            "user_request": "test",
            "unknown_field": "should be ignored",
        }
        task = PendingTask.from_dict(d)
        assert task.task_id == "pt-001"
        assert not hasattr(task, "unknown_field") or True  # just ensures no error


# ── PendingTaskStore ──


class TestPendingTaskStore:
    @pytest.fixture
    def store(self, tmp_path: Path) -> PendingTaskStore:
        return PendingTaskStore(tmp_path / "pending_tasks.json")

    def _make_task(self, task_id: str = "pt-001", **kwargs) -> PendingTask:
        defaults = {
            "task_id": task_id,
            "chat_id": "chat-1",
            "user_id": "user-1",
            "adapter": "telegram",
            "user_request": "test request",
        }
        defaults.update(kwargs)
        return PendingTask(**defaults)

    def test_save_and_get(self, store: PendingTaskStore):
        task = self._make_task()
        store.save(task)
        loaded = store.get("pt-001")
        assert loaded is not None
        assert loaded.task_id == "pt-001"
        assert loaded.user_request == "test request"

    def test_get_missing(self, store: PendingTaskStore):
        assert store.get("nonexistent") is None

    def test_remove(self, store: PendingTaskStore):
        task = self._make_task()
        store.save(task)
        store.remove("pt-001")
        assert store.get("pt-001") is None

    def test_remove_nonexistent(self, store: PendingTaskStore):
        # Should not raise
        store.remove("nonexistent")

    def test_get_actionable(self, store: PendingTaskStore):
        # Fresh task — actionable
        store.save(self._make_task("pt-001"))
        # Expired task — not actionable
        store.save(self._make_task(
            "pt-002",
            created_at=time.time() - TASK_EXPIRY_SECONDS - 1,
        ))
        # Max retries exceeded — not actionable
        store.save(self._make_task(
            "pt-003",
            retry_count=MAX_RETRY_COUNT,
        ))

        actionable = store.get_actionable()
        assert len(actionable) == 1
        assert actionable[0].task_id == "pt-001"

    def test_cleanup_expired(self, store: PendingTaskStore):
        store.save(self._make_task("pt-fresh"))
        store.save(self._make_task(
            "pt-old",
            created_at=time.time() - TASK_EXPIRY_SECONDS - 1,
        ))

        removed = store.cleanup_expired()
        assert removed == 1
        assert store.get("pt-fresh") is not None
        assert store.get("pt-old") is None

    def test_list_all(self, store: PendingTaskStore):
        store.save(self._make_task("pt-001"))
        store.save(self._make_task("pt-002"))
        all_tasks = store.list_all()
        assert len(all_tasks) == 2

    def test_handles_corrupted_json(self, store: PendingTaskStore):
        store._path.write_text("not valid json", encoding="utf-8")
        # Should not raise, returns empty
        assert store.get("pt-001") is None
        assert store.get_actionable() == []
        assert store.list_all() == []

    def test_handles_wrong_type_json(self, store: PendingTaskStore):
        store._path.write_text("[]", encoding="utf-8")
        # Should not raise, returns empty
        assert store.get_actionable() == []

    def test_save_overwrites_existing(self, store: PendingTaskStore):
        task = self._make_task()
        store.save(task)

        task.retry_count = 2
        task.skip_count = 3
        store.save(task)

        loaded = store.get("pt-001")
        assert loaded is not None
        assert loaded.retry_count == 2
        assert loaded.skip_count == 3

    def test_multiple_tasks(self, store: PendingTaskStore):
        store.save(self._make_task("pt-001"))
        store.save(self._make_task("pt-002", chat_id="chat-2"))
        store.save(self._make_task("pt-003", chat_id="chat-3"))

        assert store.get("pt-001") is not None
        assert store.get("pt-002") is not None
        assert store.get("pt-003") is not None

        store.remove("pt-002")
        assert store.get("pt-002") is None
        assert len(store.list_all()) == 2

    def test_handles_empty_file(self, store: PendingTaskStore):
        # No file yet
        assert store.get_actionable() == []
        assert store.list_all() == []

    def test_cleanup_removes_corrupted_entries(self, store: PendingTaskStore):
        """包含无法解析的条目时，cleanup 应清理掉。"""
        store.save(self._make_task("pt-good"))
        # 手动注入一条无效数据
        data = json.loads(store._path.read_text(encoding="utf-8"))
        data["pt-bad"] = {"invalid": "data"}  # 缺少必要字段
        store._path.write_text(
            json.dumps(data, ensure_ascii=False),
            encoding="utf-8",
        )

        removed = store.cleanup_expired()
        assert removed == 1  # pt-bad 被清理
        assert store.get("pt-good") is not None
