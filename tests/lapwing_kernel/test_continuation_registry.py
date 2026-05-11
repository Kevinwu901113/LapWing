"""ContinuationRegistry tests — register / resume / cancel / cleanup lifecycle.

Covers blueprint §8.3, §8.4, and the §15.2 I-6 cleanup invariants.
"""
from __future__ import annotations

import asyncio

import pytest

from src.lapwing_kernel.pipeline.continuation_registry import (
    ContinuationRegistry,
    InterruptCancelled,
)


@pytest.fixture(autouse=True)
def fresh_registry():
    ContinuationRegistry.reset_for_tests()
    yield
    ContinuationRegistry.reset_for_tests()


async def test_register_returns_unique_ref():
    reg = ContinuationRegistry.instance()
    r1 = reg.register("task-1")
    r2 = reg.register("task-2")
    assert r1 != r2


async def test_has_after_register_is_true():
    reg = ContinuationRegistry.instance()
    r = reg.register("task-1")
    assert reg.has(r) is True


async def test_has_after_resume_is_false():
    reg = ContinuationRegistry.instance()
    r = reg.register("task-1")
    reg.resume(r, {"ok": True})
    # Future is done → has() returns False
    assert reg.has(r) is False


async def test_get_status_transitions():
    reg = ContinuationRegistry.instance()
    r = reg.register("task-1")
    assert reg.get_status(r) == "active"
    reg.resume(r, {"ok": True})
    assert reg.get_status(r) == "done"


async def test_resume_releases_awaiter():
    reg = ContinuationRegistry.instance()
    r = reg.register("task-1")

    async def waiter():
        return await reg.wait_for_resume(r)

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0)  # let waiter start
    reg.resume(r, {"approved": True})
    result = await asyncio.wait_for(task, timeout=1.0)
    assert result == {"approved": True}


async def test_cancel_raises_interrupt_cancelled():
    reg = ContinuationRegistry.instance()
    r = reg.register("task-1")

    async def waiter():
        return await reg.wait_for_resume(r)

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0)
    reg.cancel(r, reason="denied")
    with pytest.raises(InterruptCancelled, match="denied"):
        await asyncio.wait_for(task, timeout=1.0)


async def test_cancel_status_is_cancelled():
    reg = ContinuationRegistry.instance()
    r = reg.register("task-1")
    reg.cancel(r, reason="expired")
    assert reg.get_status(r) == "cancelled"


async def test_wait_for_resume_unknown_ref_raises():
    reg = ContinuationRegistry.instance()
    with pytest.raises(KeyError):
        await reg.wait_for_resume("never-registered")


async def test_resume_unknown_ref_silent_noop():
    """Per §8.3 contract: race condition where future missing/done after has()
    check returned True is silent noop, not an exception."""
    reg = ContinuationRegistry.instance()
    reg.resume("never-registered", {"ok": True})  # must not raise


# §15.2 I-6 cleanup lifecycle invariants


async def test_cleanup_removes_ref():
    reg = ContinuationRegistry.instance()
    r = reg.register("task-1")
    reg.resume(r, {"done": True})
    reg.cleanup(r)
    assert reg.get_status(r) == "missing"


async def test_cleanup_after_resolved_then_missing():
    reg = ContinuationRegistry.instance()
    r = reg.register("task-1")

    async def worker():
        try:
            await reg.wait_for_resume(r)
        finally:
            reg.cleanup(r)

    task = asyncio.create_task(worker())
    await asyncio.sleep(0)
    reg.resume(r, {"approved": True})
    await asyncio.wait_for(task, timeout=1.0)
    assert reg.get_status(r) == "missing"


async def test_cleanup_after_denied_then_missing():
    reg = ContinuationRegistry.instance()
    r = reg.register("task-1")

    async def worker():
        try:
            await reg.wait_for_resume(r)
        except InterruptCancelled:
            pass
        finally:
            reg.cleanup(r)

    task = asyncio.create_task(worker())
    await asyncio.sleep(0)
    reg.cancel(r, reason="denied")
    await asyncio.wait_for(task, timeout=1.0)
    assert reg.get_status(r) == "missing"


async def test_cleanup_after_expired_then_missing():
    reg = ContinuationRegistry.instance()
    r = reg.register("task-1")

    async def worker():
        try:
            await reg.wait_for_resume(r)
        except InterruptCancelled:
            pass
        finally:
            reg.cleanup(r)

    task = asyncio.create_task(worker())
    await asyncio.sleep(0)
    reg.cancel(r, reason="expired")
    await asyncio.wait_for(task, timeout=1.0)
    assert reg.get_status(r) == "missing"


async def test_no_leak_after_100_cycles():
    """100 complete cycles → registry internal dict size returns to 0."""
    reg = ContinuationRegistry.instance()

    for i in range(100):
        r = reg.register(f"task-{i}")
        reg.resume(r, {"i": i})
        reg.cleanup(r)

    # Direct dict inspection — explicit assertion per §15.2 I-6
    assert len(reg._futures) == 0
    assert len(reg._task_refs) == 0
