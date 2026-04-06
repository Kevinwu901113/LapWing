"""tests/core/test_task_flow.py — TaskFlow + TaskFlowManager 单元测试。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from src.core.task_flow import TaskFlow, TaskFlowManager, TaskStep


@pytest.fixture
def manager(tmp_path, monkeypatch):
    """使用 tmp_path 隔离 FLOWS_DIR。"""
    monkeypatch.setattr("src.core.task_flow.FLOWS_DIR", tmp_path)
    return TaskFlowManager()


def _steps() -> list[dict]:
    return [
        {"description": "步骤一", "tool_name": "echo", "tool_args": {"text": "hello"}},
        {"description": "步骤二", "tool_name": "echo", "tool_args": {"text": "world"}},
    ]


# ── 创建 / 查询 ───────────────────────────────────────────────────────────────

def test_create_flow(manager):
    flow = manager.create_flow(title="测试流", chat_id="chat_1", steps=_steps())
    assert flow.flow_id is not None
    assert flow.title == "测试流"
    assert len(flow.steps) == 2
    assert flow.status == "pending"


def test_get_flow(manager):
    flow = manager.create_flow(title="测试流", chat_id="chat_1", steps=_steps())
    assert manager.get_flow(flow.flow_id) is flow


def test_list_active_filters_by_chat(manager):
    f1 = manager.create_flow(title="流 A", chat_id="chat_1", steps=_steps())
    manager.create_flow(title="流 B", chat_id="chat_2", steps=_steps())

    chat1_flows = manager.list_active("chat_1")
    assert len(chat1_flows) == 1
    assert chat1_flows[0].flow_id == f1.flow_id


def test_progress_pct_calculation(manager):
    flow = manager.create_flow(title="流", chat_id="c", steps=_steps())
    assert flow.progress_pct == 0
    flow.steps[0].status = "completed"
    assert flow.progress_pct == 50
    flow.steps[1].status = "completed"
    assert flow.progress_pct == 100


# ── 执行 ──────────────────────────────────────────────────────────────────────

async def test_execute_flow_all_succeed(manager):
    flow = manager.create_flow(title="成功流", chat_id="chat_1", steps=_steps())
    results = []

    async def tool_executor(tool_name: str, tool_args: dict) -> str:
        results.append(tool_name)
        return f"ok:{tool_name}"

    await manager.execute_flow(flow, tool_executor)

    assert flow.status == "completed"
    assert all(s.status == "completed" for s in flow.steps)
    assert len(results) == 2
    assert not manager.notification_queue.empty()


async def test_execute_flow_step_fails(manager):
    flow = manager.create_flow(title="失败流", chat_id="chat_1", steps=_steps())
    call_count = 0

    async def tool_executor(tool_name: str, tool_args: dict) -> str:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("工具报错")
        return "ok"

    await manager.execute_flow(flow, tool_executor)

    assert flow.status == "failed"
    assert flow.steps[0].status == "failed"
    assert call_count == 1  # 失败后停止


# ── 取消 ──────────────────────────────────────────────────────────────────────

def test_cancel_flow_sticky(manager):
    flow = manager.create_flow(title="取消流", chat_id="chat_1", steps=_steps())
    assert manager.cancel_flow(flow.flow_id) is True
    assert flow.cancel_intent is True
    # 已完成的流无法取消
    flow.status = "completed"
    assert manager.cancel_flow(flow.flow_id) is False


async def test_cancel_respected_during_execute(manager):
    """cancel_intent 在步骤开始前被检测到，流被取消。"""
    flow = manager.create_flow(title="取消执行", chat_id="chat_1", steps=_steps())
    flow.cancel_intent = True  # 提前设置

    executed = []

    async def tool_executor(tool_name: str, tool_args: dict) -> str:
        executed.append(tool_name)
        return "ok"

    await manager.execute_flow(flow, tool_executor)

    assert flow.status == "cancelled"
    assert len(executed) == 0


# ── checkpoint ────────────────────────────────────────────────────────────────

async def test_checkpoint_persistence(manager, tmp_path):
    flow = manager.create_flow(title="持久化", chat_id="chat_1", steps=_steps())

    async def tool_executor(tool_name: str, tool_args: dict) -> str:
        return "ok"

    await manager.execute_flow(flow, tool_executor)

    # checkpoint 文件应存在
    cp = tmp_path / f"{flow.flow_id}.json"
    assert cp.exists()
    data = json.loads(cp.read_text())
    assert data["status"] == "completed"
    assert data["flow_id"] == flow.flow_id


def test_load_pending_flows(manager, tmp_path):
    """load_pending_flows 恢复 running 状态的任务流。"""
    flow_data = {
        "flow_id": "abcd1234",
        "title": "恢复测试",
        "chat_id": "chat_1",
        "steps": [{"step_id": "step_1", "description": "步骤一", "status": "running",
                    "tool_name": None, "tool_args": None, "result": None,
                    "started_at": None, "completed_at": None}],
        "current_step_index": 0,
        "status": "running",
        "cancel_intent": False,
        "state_revision": 1,
        "created_at": "2026-04-05T10:00:00",
        "updated_at": "2026-04-05T10:01:00",
        "progress_pct": 0,
    }
    (tmp_path / "abcd1234.json").write_text(
        json.dumps(flow_data, ensure_ascii=False), encoding="utf-8"
    )

    recovered = manager.load_pending_flows()
    assert len(recovered) == 1
    assert recovered[0].flow_id == "abcd1234"
    assert recovered[0].title == "恢复测试"


# ── 通知队列 ──────────────────────────────────────────────────────────────────

async def test_notification_queue_populated(manager):
    flow = manager.create_flow(title="通知测试", chat_id="chat_1", steps=_steps())

    async def tool_executor(tool_name: str, tool_args: dict) -> str:
        return "ok"

    await manager.execute_flow(flow, tool_executor)

    notifs = []
    while not manager.notification_queue.empty():
        notifs.append(manager.notification_queue.get_nowait())

    assert len(notifs) > 0
    assert any(n["type"] == "task_progress" for n in notifs)
    assert any(n["status"] == "completed" for n in notifs)
