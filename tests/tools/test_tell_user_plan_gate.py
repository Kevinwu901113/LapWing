"""tell_user soft gate 测试——当计划有未完成步骤时的行为。"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.plan_state import PlanState
from src.tools.tell_user import tell_user_executor
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


def _make_ctx(*, send_fn=None, services=None):
    return ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
        services=services if services is not None else {},
        adapter="qq",
        user_id="user1",
        auth_level=2,
        chat_id="chat-x",
        send_fn=send_fn,
    )


def _noop_send():
    sent: list[str] = []

    async def fn(text: str) -> None:
        sent.append(text)

    return fn, sent


@pytest.mark.asyncio
class TestTellUserPlanGate:
    async def test_no_plan_delivers_normally(self):
        """没有 plan_state → 正常发送。"""
        send_fn, sent = _noop_send()
        ctx = _make_ctx(send_fn=send_fn, services={})
        result = await tell_user_executor(
            ToolExecutionRequest(name="tell_user", arguments={"text": "hello"}),
            ctx,
        )
        assert result.success is True
        assert result.payload["delivered"] is True
        assert sent == ["hello"]

    async def test_incomplete_plan_blocks_first_attempt(self):
        """计划有未完成步骤 → 首次 tell_user 被拦截。"""
        send_fn, sent = _noop_send()
        plan = PlanState.create([
            {"description": "步骤一"},
            {"description": "步骤二"},
        ])
        ctx = _make_ctx(send_fn=send_fn, services={"plan_state": plan})
        result = await tell_user_executor(
            ToolExecutionRequest(name="tell_user", arguments={"text": "hi"}),
            ctx,
        )
        assert result.success is False
        assert result.payload["reason"] == "plan_incomplete"
        assert sent == []  # 消息未发出

    async def test_disarmed_gate_delivers(self):
        """首次拦截后，第二次 tell_user 正常发送（gate 已解除）。"""
        send_fn, sent = _noop_send()
        plan = PlanState.create([
            {"description": "步骤一"},
            {"description": "步骤二"},
        ])
        ctx = _make_ctx(send_fn=send_fn, services={"plan_state": plan})

        # 第一次：被拦截
        r1 = await tell_user_executor(
            ToolExecutionRequest(name="tell_user", arguments={"text": "first"}),
            ctx,
        )
        assert r1.success is False

        # 第二次：gate 已解除，正常发送
        r2 = await tell_user_executor(
            ToolExecutionRequest(name="tell_user", arguments={"text": "second"}),
            ctx,
        )
        assert r2.success is True
        assert r2.payload["delivered"] is True
        assert sent == ["second"]

    async def test_all_completed_delivers(self):
        """所有步骤已完成 → 首次 tell_user 直接发送，不拦截。"""
        send_fn, sent = _noop_send()
        plan = PlanState.create([
            {"description": "步骤一"},
            {"description": "步骤二"},
        ])
        # 完成所有步骤
        plan.advance(0, "completed")
        plan.advance(1, "completed")

        ctx = _make_ctx(send_fn=send_fn, services={"plan_state": plan})
        result = await tell_user_executor(
            ToolExecutionRequest(name="tell_user", arguments={"text": "done!"}),
            ctx,
        )
        assert result.success is True
        assert result.payload["delivered"] is True
        assert sent == ["done!"]
