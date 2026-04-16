"""ConsciousnessEngine (Phase 4) 单元测试。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.consciousness import ConsciousnessEngine, TickBudget


@pytest.fixture
def engine():
    mock_brain = MagicMock()
    mock_brain.think = AsyncMock(return_value="无事")
    eng = ConsciousnessEngine(
        brain=mock_brain,
        send_fn=AsyncMock(),
        reminder_scheduler=None,
        incident_manager=None,
    )
    return eng


# ── 1. urgency queue ──

async def test_urgency_push_and_drain(engine: ConsciousnessEngine):
    items = [
        {"type": "reminder", "content": "吃药"},
        {"type": "system", "content": "磁盘空间不足"},
        {"type": "agent_done", "content": "搜索任务完成"},
    ]
    for item in items:
        engine.push_urgency(item)

    drained = engine._drain_urgency()
    assert len(drained) == 3
    assert drained[0]["content"] == "吃药"
    assert drained[1]["type"] == "system"
    assert drained[2]["type"] == "agent_done"

    # 队列应已清空
    assert engine.urgency_queue.empty()
    assert engine._drain_urgency() == []


# ── 2. interrupt flag ──

async def test_interrupt_sets_flag(engine: ConsciousnessEngine):
    assert not engine._interrupt_flag.is_set()
    engine.interrupt()
    assert engine._interrupt_flag.is_set()


# ── 3. on_conversation_start ──

async def test_on_conversation_start_interrupts(engine: ConsciousnessEngine):
    # 模拟一个正在运行的 thinking task
    mock_task = MagicMock()
    mock_task.done.return_value = False
    engine._thinking_task = mock_task

    engine.on_conversation_start()

    assert engine._interrupt_flag.is_set()
    assert engine._in_conversation is True
    mock_task.cancel.assert_called_once()


# ── 4. backoff on idle streak ──

async def test_backoff_idle_streak(engine: ConsciousnessEngine):
    original_interval = engine._next_interval

    # 连续空闲 tick，间隔应逐渐增大
    engine._adjust_interval_after_tick(False)
    assert engine.idle_streak == 1
    interval_1 = engine._next_interval

    engine._adjust_interval_after_tick(False)
    assert engine.idle_streak == 2
    interval_2 = engine._next_interval

    assert interval_1 > 0
    assert interval_2 > interval_1
    # 不应超过 MAX_INTERVAL
    assert interval_2 <= engine.MAX_INTERVAL


# ── 5. backoff reset on activity ──

async def test_backoff_reset_on_activity(engine: ConsciousnessEngine):
    # 先累积一些空闲
    engine._adjust_interval_after_tick(False)
    engine._adjust_interval_after_tick(False)
    engine._adjust_interval_after_tick(False)
    assert engine.idle_streak == 3
    high_interval = engine._next_interval

    # 有活动 → 重置
    engine._adjust_interval_after_tick(True)
    assert engine.idle_streak == 0
    # 活跃后如果之前间隔比 BASE_INTERVAL 大，应该被拉回
    assert engine._next_interval <= engine.BASE_INTERVAL


# ── 6. TickBudget defaults ──

async def test_tick_budget_exists(engine: ConsciousnessEngine):
    budget = engine.tick_budget
    assert isinstance(budget, TickBudget)
    assert budget.max_tokens == 10000
    assert budget.max_tool_calls == 10
    assert budget.max_time_seconds == 120


# ── 7. consciousness prompt with urgent items ──

async def test_build_consciousness_prompt_with_urgent(engine: ConsciousnessEngine):
    urgent = [
        {"type": "reminder", "content": "提醒Kevin开会"},
        {"type": "system", "content": "CPU 过高"},
    ]
    prompt = await engine._build_consciousness_prompt(urgent)

    assert "紧急事件" in prompt
    assert "提醒Kevin开会" in prompt
    assert "CPU 过高" in prompt
    assert "[reminder]" in prompt
    assert "[system]" in prompt
    assert "请优先处理" in prompt or "立即响应" in prompt


# ── 8. consciousness prompt normal (no urgent) ──

async def test_build_consciousness_prompt_normal(engine: ConsciousnessEngine):
    prompt = await engine._build_consciousness_prompt(None)

    assert "内部意识 tick" in prompt
    assert "自由时间" in prompt
    assert "紧急事件" not in prompt
    assert "[NEXT:" in prompt
    assert "无事" in prompt
