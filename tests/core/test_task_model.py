"""tests/core/test_task_model.py — TaskStore CRUD 测试。"""

import pytest

from src.core.task_model import Task, TaskBudget, TaskStore


@pytest.fixture
async def store(tmp_path):
    ts = TaskStore(tmp_path / "test_tasks.db")
    await ts.init()
    yield ts
    await ts.close()


def _make_task(**kwargs) -> Task:
    defaults = {
        "task_id": TaskStore.new_task_id(),
        "parent_task_id": None,
        "source": "kevin_desktop",
        "status": "queued",
        "initiator": "lapwing",
        "assigned_to": None,
        "request": "test request",
        "context": "test context",
    }
    defaults.update(kwargs)
    return Task(**defaults)


class TestTaskStore:
    async def test_create_and_get(self, store):
        task = _make_task()
        await store.create(task)
        retrieved = await store.get(task.task_id)
        assert retrieved is not None
        assert retrieved.task_id == task.task_id
        assert retrieved.request == "test request"
        assert retrieved.status == "queued"

    async def test_update_status(self, store):
        task = _make_task()
        await store.create(task)
        await store.update_status(task.task_id, "done", result="completed successfully")
        retrieved = await store.get(task.task_id)
        assert retrieved.status == "done"
        assert retrieved.result == "completed successfully"

    async def test_list_active(self, store):
        t1 = _make_task(status="queued")
        t2 = _make_task(status="running")
        t3 = _make_task(status="done")
        await store.create(t1)
        await store.create(t2)
        await store.create(t3)
        active = await store.list_active()
        assert len(active) == 2
        assert all(t.status in ("queued", "running") for t in active)

    async def test_list_by_status(self, store):
        t1 = _make_task(status="done")
        t2 = _make_task(status="done")
        t3 = _make_task(status="queued")
        await store.create(t1)
        await store.create(t2)
        await store.create(t3)
        done = await store.list_by_status("done")
        assert len(done) == 2

    async def test_get_nonexistent(self, store):
        result = await store.get("nonexistent")
        assert result is None

    async def test_budget_round_trip(self, store):
        budget = TaskBudget(max_tokens=10000, max_tool_calls=5, max_time_seconds=60)
        task = _make_task(budget=budget)
        await store.create(task)
        retrieved = await store.get(task.task_id)
        assert retrieved.budget.max_tokens == 10000
        assert retrieved.budget.max_tool_calls == 5
