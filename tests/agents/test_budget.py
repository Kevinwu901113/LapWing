import pytest
import time
from src.agents.budget import BudgetLedger, BudgetExhausted, BudgetSnapshot


def test_llm_call_limit():
    led = BudgetLedger(max_llm_calls=2)
    led.charge_llm_call()
    led.charge_llm_call()
    with pytest.raises(BudgetExhausted) as ei:
        led.charge_llm_call()
    assert ei.value.dimension == "llm_calls"
    assert ei.value.used == 3
    assert ei.value.limit == 2

def test_tool_call_limit():
    led = BudgetLedger(max_tool_calls=1)
    led.charge_tool_call()
    with pytest.raises(BudgetExhausted) as ei:
        led.charge_tool_call()
    assert ei.value.dimension == "tool_calls"

def test_token_limit():
    led = BudgetLedger(max_total_tokens=100)
    led.charge_llm_call(input_tokens=60, output_tokens=30)
    with pytest.raises(BudgetExhausted) as ei:
        led.charge_llm_call(input_tokens=20)
    assert ei.value.dimension == "tokens"

def test_delegation_depth():
    led = BudgetLedger(max_delegation_depth=1)
    led.enter_delegation()
    with pytest.raises(BudgetExhausted) as ei:
        led.enter_delegation()
    assert ei.value.dimension == "delegation_depth"
    led.exit_delegation()
    led.enter_delegation()  # should now succeed
    assert led.snapshot().delegation_depth == 1

def test_exit_delegation_does_not_underflow():
    led = BudgetLedger(max_delegation_depth=2)
    led.exit_delegation()  # noop when not in delegation
    assert led.snapshot().delegation_depth == 0

def test_snapshot_returns_immutable_view():
    led = BudgetLedger(max_llm_calls=10)
    led.charge_llm_call(input_tokens=50, output_tokens=20)
    led.charge_tool_call()
    snap = led.snapshot()
    assert isinstance(snap, BudgetSnapshot)
    assert snap.llm_calls_used == 1
    assert snap.tool_calls_used == 1
    assert snap.estimated_input_tokens == 50
    assert snap.estimated_output_tokens == 20

def test_exhausted_property():
    led = BudgetLedger(max_llm_calls=1)
    assert led.exhausted is False
    led.charge_llm_call()
    assert led.exhausted is False
    try:
        led.charge_llm_call()
    except BudgetExhausted:
        pass
    assert led.exhausted is True

def test_check_raises_when_any_dimension_over():
    led = BudgetLedger(max_llm_calls=10, max_tool_calls=10,
                       max_total_tokens=100, max_wall_time_seconds=600.0,
                       max_delegation_depth=5)
    # nothing charged yet — check should be silent
    led.check()

def test_wall_time_limit():
    """Wall-time runs from BudgetLedger construction; check() must raise once exceeded.

    We don't actually sleep — we patch the start so the elapsed time exceeds the limit.
    """
    led = BudgetLedger(max_wall_time_seconds=0.001)
    # advance the start time so elapsed is large
    led._start_time = time.perf_counter() - 5.0
    with pytest.raises(BudgetExhausted) as ei:
        led.check()
    assert ei.value.dimension == "wall_time"

def test_charge_methods_record_correctly():
    led = BudgetLedger()
    led.charge_llm_call(input_tokens=10, output_tokens=5)
    led.charge_llm_call(input_tokens=3)
    snap = led.snapshot()
    assert snap.llm_calls_used == 2
    assert snap.estimated_input_tokens == 13
    assert snap.estimated_output_tokens == 5

def test_budget_exhausted_message():
    err = BudgetExhausted("llm_calls", 3, 2)
    assert err.dimension == "llm_calls"
    assert err.used == 3
    assert err.limit == 2
    assert "llm_calls" in str(err)
    assert "3" in str(err)
    assert "2" in str(err)
