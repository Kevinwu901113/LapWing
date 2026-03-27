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


@pytest.mark.asyncio
async def test_task_view_keeps_tool_execution_event_details():
    store = TaskViewStore()
    await store.ingest_event(
        {
            "type": "task.started",
            "timestamp": "2026-03-27T10:00:00+00:00",
            "payload": {
                "task_id": "task_3",
                "chat_id": "c1",
                "phase": "started",
                "text": "开始",
            },
        }
    )
    await store.ingest_event(
        {
            "type": "task.tool_execution_end",
            "timestamp": "2026-03-27T10:00:01+00:00",
            "payload": {
                "task_id": "task_3",
                "chat_id": "c1",
                "phase": "executing",
                "text": "工具执行结束：execute_shell",
                "tool_name": "execute_shell",
                "toolCallId": "call_1",
                "toolName": "execute_shell",
                "argsHash": "a" * 64,
                "stdoutBytes": 12,
                "stderrBytes": 0,
                "isError": False,
                "durationMs": 23,
            },
        }
    )

    detail = await store.get_task("task_3")
    assert detail is not None
    tool_event = detail["events"][-1]
    assert tool_event["type"] == "task.tool_execution_end"
    assert tool_event["toolCallId"] == "call_1"
    assert tool_event["toolName"] == "execute_shell"
    assert tool_event["argsHash"] == "a" * 64
    assert tool_event["stdoutBytes"] == 12
    assert tool_event["stderrBytes"] == 0
    assert tool_event["isError"] is False
    assert tool_event["durationMs"] == 23


@pytest.mark.asyncio
async def test_task_view_tool_execution_event_does_not_downgrade_final_status():
    store = TaskViewStore()
    await store.ingest_event(
        {
            "type": "task.completed",
            "timestamp": "2026-03-27T10:00:02+00:00",
            "payload": {
                "task_id": "task_4",
                "chat_id": "c1",
                "phase": "completed",
                "text": "完成",
            },
        }
    )
    await store.ingest_event(
        {
            "type": "task.tool_execution_update",
            "timestamp": "2026-03-27T10:00:01+00:00",
            "payload": {
                "task_id": "task_4",
                "chat_id": "c1",
                "phase": "executing",
                "text": "工具执行进度",
                "toolCallId": "call_1",
                "toolName": "execute_shell",
                "argsHash": "b" * 64,
                "stdoutBytes": 5,
                "stderrBytes": 1,
                "isError": True,
                "durationMs": 10,
            },
        }
    )

    detail = await store.get_task("task_4")
    assert detail is not None
    assert detail["status"] == "completed"
    assert len(detail["events"]) == 2
