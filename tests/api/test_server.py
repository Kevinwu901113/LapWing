"""本地 API 测试。"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.api.event_bus import DesktopEventBus
from src.api.server import create_app
from src.app.task_view import TaskViewStore
from src.core.latency_monitor import LatencyMonitor


@pytest.fixture
def mock_brain():
    brain = MagicMock()
    brain.auth_manager = None
    brain.memory = MagicMock()
    brain.memory.get_all_chat_ids = AsyncMock(return_value=["c2", "c1"])

    async def get_last_interaction(chat_id: str):
        if chat_id == "c2":
            return datetime(2026, 3, 24, 10, 0, tzinfo=timezone.utc)
        return datetime(2026, 3, 24, 9, 0, tzinfo=timezone.utc)

    brain.memory.get_last_interaction = AsyncMock(side_effect=get_last_interaction)
    brain.memory.get_top_interests = AsyncMock(return_value=[
        {"topic": "Python", "weight": 3.0, "last_seen": "2026-03-24T10:00:00+00:00"}
    ])
    brain.memory.get_user_facts = AsyncMock(return_value=[
        {"fact_key": "偏好_语言", "fact_value": "中文", "updated_at": "2026-03-24"},
        {"fact_key": "memory_summary_2026-03-23", "fact_value": "聊了工作。", "updated_at": "2026-03-23"},
    ])
    brain.memory.delete_user_fact = AsyncMock(return_value=True)
    brain.prompt_evolver = MagicMock()
    brain.prompt_evolver.evolve = AsyncMock(return_value={"success": True, "changes_summary": "优化了语气"})
    brain.reload_persona = MagicMock()
    return brain


@pytest.mark.asyncio
class TestLocalApi:
    async def test_status_and_chats_endpoints(self, mock_brain):
        app = create_app(
            mock_brain,
            DesktopEventBus(),
            latency_monitor=LatencyMonitor(window_size=20, min_samples_for_slo=1),
        )
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            chats_response = await client.get("/api/chats")
            status_response = await client.get("/api/status")

        assert chats_response.status_code == 200
        assert chats_response.json()[0]["chat_id"] == "c2"
        assert status_response.status_code == 200
        assert status_response.json()["online"] is True
        assert status_response.json()["chat_count"] == 2
        assert "latency_monitor" in status_response.json()

    async def test_memory_endpoint_filters_summaries(self, mock_brain):
        app = create_app(
            mock_brain,
            DesktopEventBus(),
            latency_monitor=LatencyMonitor(window_size=20, min_samples_for_slo=1),
        )
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/memory", params={"chat_id": "c1"})

        data = response.json()
        assert response.status_code == 200
        assert data["items"] == [
            {
                "index": 1,
                "fact_key": "偏好_语言",
                "fact_value": "中文",
                "updated_at": "2026-03-24",
            }
        ]

    async def test_memory_delete_endpoint(self, mock_brain):
        app = create_app(mock_brain, DesktopEventBus())
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/memory/delete",
                json={"chat_id": "c1", "fact_key": "偏好_语言"},
            )

        assert response.status_code == 200
        assert response.json()["success"] is True
        mock_brain.memory.delete_user_fact.assert_awaited_once_with("c1", "偏好_语言")

    async def test_learnings_endpoint_returns_files(self, mock_brain, monkeypatch, tmp_path):
        learnings_dir = tmp_path / "learnings"
        learnings_dir.mkdir()
        (learnings_dir / "2026-03-24.md").write_text("# note\nhello", encoding="utf-8")
        monkeypatch.setattr("src.api.server._LEARNINGS_DIR", learnings_dir)

        app = create_app(mock_brain, DesktopEventBus())
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/learnings")

        assert response.status_code == 200
        assert response.json()["items"][0]["filename"] == "2026-03-24.md"

    async def test_events_stream_emits_published_event(self, mock_brain):
        event_bus = DesktopEventBus()
        app = create_app(mock_brain, event_bus)
        route = next(route for route in app.routes if getattr(route, "path", "") == "/api/events/stream")
        response = await route.endpoint()

        first_chunk_task = asyncio.create_task(response.body_iterator.__anext__())
        await asyncio.sleep(0.05)
        await event_bus.publish("proactive_message", {"chat_id": "c1", "text": "你好"})
        body = await asyncio.wait_for(first_chunk_task, timeout=1)
        if isinstance(body, bytes):
            body = body.decode("utf-8")

        assert "proactive_message" in body
        assert "你好" in body

    async def test_latency_telemetry_endpoint_updates_status_snapshot(self, mock_brain):
        app = create_app(
            mock_brain,
            DesktopEventBus(),
            latency_monitor=LatencyMonitor(window_size=20, min_samples_for_slo=1),
        )
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            telemetry_resp = await client.post(
                "/api/telemetry/latency",
                json={
                    "metric": "tool_execution_start_to_ui",
                    "samples_ms": [120, 140, 180],
                    "client_timestamp": "2026-03-27T10:00:00+00:00",
                },
            )
            status_resp = await client.get("/api/status")

        assert telemetry_resp.status_code == 200
        telemetry_data = telemetry_resp.json()
        assert telemetry_data["success"] is True
        assert telemetry_data["accepted_samples"] == 3
        status_data = status_resp.json()
        frontend_metric = status_data["latency_monitor"]["frontend"]["tool_execution_start_to_ui"]
        assert frontend_metric["samples"] == 3
        assert frontend_metric["p95_ms"] == 180

    async def test_events_stream_emits_task_event(self, mock_brain):
        event_bus = DesktopEventBus()
        app = create_app(mock_brain, event_bus)
        route = next(route for route in app.routes if getattr(route, "path", "") == "/api/events/stream")
        response = await route.endpoint()

        first_chunk_task = asyncio.create_task(response.body_iterator.__anext__())
        await asyncio.sleep(0.05)
        await event_bus.publish(
            "task.executing",
            {
                "task_id": "task_123",
                "chat_id": "c1",
                "phase": "executing",
                "text": "正在执行工具：execute_shell",
                "tool_name": "execute_shell",
            },
        )
        body = await asyncio.wait_for(first_chunk_task, timeout=1)
        if isinstance(body, bytes):
            body = body.decode("utf-8")

        assert "task.executing" in body
        assert "task_123" in body

    async def test_status_includes_backend_publish_to_sse_latency(self, mock_brain):
        event_bus = DesktopEventBus()
        monitor = LatencyMonitor(window_size=20, min_samples_for_slo=1)
        app = create_app(mock_brain, event_bus, latency_monitor=monitor)
        route = next(route for route in app.routes if getattr(route, "path", "") == "/api/events/stream")
        response = await route.endpoint()

        first_chunk_task = asyncio.create_task(response.body_iterator.__anext__())
        await asyncio.sleep(0.05)
        await event_bus.publish("task.started", {"task_id": "task_1", "chat_id": "c1"})
        _ = await asyncio.wait_for(first_chunk_task, timeout=1)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            status_resp = await client.get("/api/status")

        assert status_resp.status_code == 200
        metric = status_resp.json()["latency_monitor"]["backend"]["event_pipeline"]["publish_to_sse"]
        assert metric["samples"] >= 1

    async def test_tasks_endpoints_return_projected_tasks(self, mock_brain):
        event_bus = DesktopEventBus()
        task_store = TaskViewStore()
        event_bus.add_listener(task_store.ingest_event)
        app = create_app(mock_brain, event_bus, task_store)
        transport = httpx.ASGITransport(app=app)

        await event_bus.publish(
            "task.started",
            {
                "task_id": "task_1",
                "chat_id": "c1",
                "phase": "started",
                "text": "任务开始",
            },
        )
        await event_bus.publish(
            "task.completed",
            {
                "task_id": "task_1",
                "chat_id": "c1",
                "phase": "completed",
                "text": "任务完成",
            },
        )

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            list_resp = await client.get("/api/tasks", params={"chat_id": "c1"})
            detail_resp = await client.get("/api/tasks/task_1")

        assert list_resp.status_code == 200
        list_data = list_resp.json()
        assert list_data["items"][0]["task_id"] == "task_1"
        assert list_data["items"][0]["status"] == "completed"

        assert detail_resp.status_code == 200
        detail_data = detail_resp.json()
        assert detail_data["task_id"] == "task_1"
        assert len(detail_data["events"]) == 2

    async def test_task_detail_returns_404_when_not_found(self, mock_brain):
        event_bus = DesktopEventBus()
        task_store = TaskViewStore()
        app = create_app(mock_brain, event_bus, task_store)
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/tasks/not_exists")

        assert response.status_code == 404

    async def test_task_detail_includes_tool_execution_event_fields(self, mock_brain):
        event_bus = DesktopEventBus()
        task_store = TaskViewStore()
        event_bus.add_listener(task_store.ingest_event)
        app = create_app(mock_brain, event_bus, task_store)
        transport = httpx.ASGITransport(app=app)

        await event_bus.publish(
            "task.started",
            {
                "task_id": "task_2",
                "chat_id": "c1",
                "phase": "started",
                "text": "任务开始",
            },
        )
        await event_bus.publish(
            "task.tool_execution_end",
            {
                "task_id": "task_2",
                "chat_id": "c1",
                "phase": "executing",
                "text": "工具执行结束：execute_shell",
                "tool_name": "execute_shell",
                "toolCallId": "call_1",
                "toolName": "execute_shell",
                "argsHash": "c" * 64,
                "stdoutBytes": 8,
                "stderrBytes": 0,
                "isError": False,
                "durationMs": 12,
            },
        )

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            detail_resp = await client.get("/api/tasks/task_2")

        assert detail_resp.status_code == 200
        detail_data = detail_resp.json()
        tool_event = detail_data["events"][1]
        assert tool_event["type"] == "task.tool_execution_end"
        assert tool_event["toolCallId"] == "call_1"
        assert tool_event["toolName"] == "execute_shell"
        assert tool_event["argsHash"] == "c" * 64
        assert tool_event["stdoutBytes"] == 8
        assert tool_event["stderrBytes"] == 0
        assert tool_event["isError"] is False
        assert tool_event["durationMs"] == 12
