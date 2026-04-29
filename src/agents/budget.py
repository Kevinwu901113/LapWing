"""src/agents/budget.py — Turn-shared budget tracking for Brain + delegated agents.

Per blueprint §5: a single BudgetLedger is created at the start of each Brain turn
and stored in ToolExecutionContext.services["budget_ledger"]. All delegated agents
charge against the same ledger. Agents cannot create independent budgets or refresh.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class BudgetSnapshot:
    llm_calls_used: int = 0
    tool_calls_used: int = 0
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    wall_time_seconds: float = 0.0
    delegation_depth: int = 0


class BudgetExhausted(Exception):
    """Raised when any budget dimension is exceeded.

    Caught by BaseAgent.execute(); converted to AgentResult(budget_status="budget_exhausted").
    """

    def __init__(self, dimension: str, used: int | float, limit: int | float):
        self.dimension = dimension
        self.used = used
        self.limit = limit
        super().__init__(f"Budget exhausted: {dimension} ({used}/{limit})")


class BudgetLedger:
    """Turn-shared budget across Brain + all delegated agents.

    Construct once per Brain turn; pass via ctx.services["budget_ledger"].
    Every charge_* call may raise BudgetExhausted; callers must handle.
    """

    def __init__(
        self,
        max_llm_calls: int = 50,
        max_tool_calls: int = 100,
        max_total_tokens: int = 200000,
        max_wall_time_seconds: float = 600.0,
        max_delegation_depth: int = 1,
    ) -> None:
        self._max_llm_calls = max_llm_calls
        self._max_tool_calls = max_tool_calls
        self._max_total_tokens = max_total_tokens
        self._max_wall_time_seconds = max_wall_time_seconds
        self._max_delegation_depth = max_delegation_depth
        self._llm_calls = 0
        self._tool_calls = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._delegation_depth = 0
        self._start_time = time.perf_counter()
        self._exhausted = False

    def charge_llm_call(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        # Increment first, then check. This way `used` in the exception
        # reflects the count after the failed attempt.
        self._llm_calls += 1
        self._input_tokens += input_tokens
        self._output_tokens += output_tokens
        if self._llm_calls > self._max_llm_calls:
            self._exhausted = True
            raise BudgetExhausted("llm_calls", self._llm_calls, self._max_llm_calls)
        total = self._input_tokens + self._output_tokens
        if total > self._max_total_tokens:
            self._exhausted = True
            raise BudgetExhausted("tokens", total, self._max_total_tokens)
        self._check_wall_time()

    def charge_tool_call(self) -> None:
        self._tool_calls += 1
        if self._tool_calls > self._max_tool_calls:
            self._exhausted = True
            raise BudgetExhausted("tool_calls", self._tool_calls, self._max_tool_calls)
        self._check_wall_time()

    def enter_delegation(self) -> None:
        if self._delegation_depth + 1 > self._max_delegation_depth:
            self._exhausted = True
            raise BudgetExhausted(
                "delegation_depth",
                self._delegation_depth + 1,
                self._max_delegation_depth,
            )
        self._delegation_depth += 1

    def exit_delegation(self) -> None:
        if self._delegation_depth > 0:
            self._delegation_depth -= 1

    def check(self) -> None:
        """Re-validate every dimension. Useful before yielding to LLM."""
        if self._llm_calls > self._max_llm_calls:
            self._exhausted = True
            raise BudgetExhausted("llm_calls", self._llm_calls, self._max_llm_calls)
        if self._tool_calls > self._max_tool_calls:
            self._exhausted = True
            raise BudgetExhausted("tool_calls", self._tool_calls, self._max_tool_calls)
        total = self._input_tokens + self._output_tokens
        if total > self._max_total_tokens:
            self._exhausted = True
            raise BudgetExhausted("tokens", total, self._max_total_tokens)
        self._check_wall_time()

    def snapshot(self) -> BudgetSnapshot:
        return BudgetSnapshot(
            llm_calls_used=self._llm_calls,
            tool_calls_used=self._tool_calls,
            estimated_input_tokens=self._input_tokens,
            estimated_output_tokens=self._output_tokens,
            wall_time_seconds=self._elapsed(),
            delegation_depth=self._delegation_depth,
        )

    @property
    def exhausted(self) -> bool:
        return self._exhausted

    def _elapsed(self) -> float:
        return time.perf_counter() - self._start_time

    def _check_wall_time(self) -> None:
        elapsed = self._elapsed()
        if elapsed > self._max_wall_time_seconds:
            self._exhausted = True
            raise BudgetExhausted("wall_time", elapsed, self._max_wall_time_seconds)
