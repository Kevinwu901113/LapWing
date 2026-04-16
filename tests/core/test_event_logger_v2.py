"""tests/core/test_event_logger_v2.py — EventLogger 写入、查询测试。"""

import pytest
from datetime import datetime, timezone

from src.core.event_logger_v2 import EventLogger


@pytest.fixture
async def logger(tmp_path):
    el = EventLogger(tmp_path / "test_events.db")
    await el.init()
    yield el
    await el.close()


class TestEventLogger:
    async def test_log_and_query(self, logger):
        event = logger.make_event("test_type", actor="tester", payload={"foo": "bar"})
        await logger.log(event)

        results = await logger.query(event_type="test_type")
        assert len(results) == 1
        assert results[0].event_id == event.event_id
        assert results[0].payload == {"foo": "bar"}
        assert results[0].actor == "tester"

    async def test_query_by_task_id(self, logger):
        e1 = logger.make_event("a", task_id="task_1")
        e2 = logger.make_event("b", task_id="task_2")
        await logger.log(e1)
        await logger.log(e2)

        results = await logger.query(task_id="task_1")
        assert len(results) == 1
        assert results[0].task_id == "task_1"

    async def test_query_limit(self, logger):
        for i in range(5):
            await logger.log(logger.make_event("bulk", payload={"i": i}))
        results = await logger.query(event_type="bulk", limit=3)
        assert len(results) == 3

    async def test_make_event_generates_id(self, logger):
        e1 = logger.make_event("test")
        e2 = logger.make_event("test")
        assert e1.event_id != e2.event_id
