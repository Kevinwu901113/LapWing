"""TaskViewStore 测试。"""

import pytest

from src.app.task_view import TaskViewStore


@pytest.mark.asyncio
async def test_task_view_tracks_lifecycle_in_order():
    store = TaskViewStore()

    await store.ingest_event(
        {
            "type": "task.started",
            "timestamp": "2026-03-27T10:00:00+00:00",
            "payload": {
                "task_id": "task_1",
                "chat_id": "c1",
                "phase": "started",
                "text": "开始",
            },
        }
    )
    await store.ingest_event(
        {
            "type": "task.executing",
            "timestamp": "2026-03-27T10:00:01+00:00",
            "payload": {
                "task_id": "task_1",
                "chat_id": "c1",
                "phase": "executing",
                "text": "执行中",
                "tool_name": "execute_shell",
                "round": 1,
            },
        }
    )
    await store.ingest_event(
        {
            "type": "task.completed",
            "timestamp": "2026-03-27T10:00:02+00:00",
            "payload": {
                "task_id": "task_1",
                "chat_id": "c1",
                "phase": "completed",
                "text": "完成",
            },
        }
    )

    summary = await store.get_task("task_1")
    assert summary is not None
    assert summary["status"] == "completed"
    assert summary["started_at"] == "2026-03-27T10:00:00+00:00"
    assert summary["completed_at"] == "2026-03-27T10:00:02+00:00"
    assert len(summary["events"]) == 3


@pytest.mark.asyncio
async def test_task_view_tolerates_out_of_order_events_without_downgrade():
    store = TaskViewStore()

    await store.ingest_event(
        {
            "type": "task.completed",
            "timestamp": "2026-03-27T10:00:02+00:00",
            "payload": {
                "task_id": "task_2",
                "chat_id": "c1",
                "phase": "completed",
                "text": "完成",
            },
        }
    )
    await store.ingest_event(
        {
            "type": "task.executing",
            "timestamp": "2026-03-27T10:00:01+00:00",
            "payload": {
                "task_id": "task_2",
                "chat_id": "c1",
                "phase": "executing",
                "text": "执行中",
            },
        }
    )

    summary = await store.get_task("task_2")
    assert summary is not None
    assert summary["status"] == "completed"
    assert len(summary["events"]) == 2


@pytest.mark.asyncio
async def test_task_view_filters_and_sorts():
    store = TaskViewStore()
    await store.ingest_event(
        {
            "type": "task.started",
            "timestamp": "2026-03-27T10:00:00+00:00",
            "payload": {"task_id": "task_old", "chat_id": "c1", "phase": "started", "text": "old"},
        }
    )
    await store.ingest_event(
        {
            "type": "task.failed",
            "timestamp": "2026-03-27T10:01:00+00:00",
            "payload": {"task_id": "task_new", "chat_id": "c2", "phase": "failed", "text": "new"},
        }
    )

    all_items = await store.list_tasks(limit=10)
    assert [item["task_id"] for item in all_items] == ["task_new", "task_old"]

    c1_items = await store.list_tasks(chat_id="c1", limit=10)
    assert [item["task_id"] for item in c1_items] == ["task_old"]

    failed_items = await store.list_tasks(status="failed", limit=10)
    assert [item["task_id"] for item in failed_items] == ["task_new"]
