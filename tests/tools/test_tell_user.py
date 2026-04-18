"""tell_user 工具的单元测试 — Step 5 M1。

验证唯一对外说话路径的契约：
- send_fn 存在 → text 真实发出，trajectory_id 返回，buffer 累计
- send_fn 缺失 → 工具返回 success=False，告诉模型"没有用户通道"
- 空 text → 失败
- send_fn 抛异常 → 失败但不让异常逃出
- 多次调用 → 每次独立发送、独立 trajectory entry
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.tools.tell_user import tell_user_executor
from src.tools.types import (
    ToolExecutionContext,
    ToolExecutionRequest,
)


def _make_ctx(
    *,
    send_fn=None,
    services: dict | None = None,
) -> ToolExecutionContext:
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


@pytest.mark.asyncio
class TestTellUser:
    async def test_sends_to_user_when_send_fn_present(self):
        sent: list[str] = []

        async def send_fn(text: str) -> None:
            sent.append(text)

        ctx = _make_ctx(send_fn=send_fn)
        result = await tell_user_executor(
            ToolExecutionRequest(name="tell_user", arguments={"text": "hello"}),
            ctx,
        )

        assert result.success is True
        assert result.payload["delivered"] is True
        assert result.payload["text"] == "hello"
        assert sent == ["hello"]

    async def test_fails_when_no_send_fn(self):
        ctx = _make_ctx(send_fn=None)
        result = await tell_user_executor(
            ToolExecutionRequest(name="tell_user", arguments={"text": "hi"}),
            ctx,
        )

        assert result.success is False
        assert result.payload["delivered"] is False
        assert "send_fn" in result.reason or "用户通道" in result.reason

    async def test_fails_on_empty_text(self):
        async def send_fn(text: str) -> None:
            pass

        ctx = _make_ctx(send_fn=send_fn)
        result = await tell_user_executor(
            ToolExecutionRequest(name="tell_user", arguments={"text": "   "}),
            ctx,
        )

        assert result.success is False
        assert result.payload["delivered"] is False

    async def test_send_fn_exception_does_not_propagate(self):
        async def send_fn(text: str) -> None:
            raise RuntimeError("network down")

        ctx = _make_ctx(send_fn=send_fn)
        result = await tell_user_executor(
            ToolExecutionRequest(name="tell_user", arguments={"text": "hi"}),
            ctx,
        )

        assert result.success is False
        assert "network down" in result.reason

    async def test_appends_to_buffer(self):
        sent: list[str] = []
        buffer: list[str] = []

        async def send_fn(text: str) -> None:
            sent.append(text)

        ctx = _make_ctx(
            send_fn=send_fn,
            services={"tell_user_buffer": buffer},
        )
        await tell_user_executor(
            ToolExecutionRequest(name="tell_user", arguments={"text": "msg1"}),
            ctx,
        )
        await tell_user_executor(
            ToolExecutionRequest(name="tell_user", arguments={"text": "msg2"}),
            ctx,
        )

        assert sent == ["msg1", "msg2"]
        assert buffer == ["msg1", "msg2"]

    async def test_writes_trajectory_when_store_present(self):
        sent: list[str] = []

        async def send_fn(text: str) -> None:
            sent.append(text)

        trajectory_store = MagicMock()
        trajectory_store.append = AsyncMock(return_value=42)

        ctx = _make_ctx(
            send_fn=send_fn,
            services={"trajectory_store": trajectory_store},
        )
        result = await tell_user_executor(
            ToolExecutionRequest(name="tell_user", arguments={"text": "hi"}),
            ctx,
        )

        assert result.success is True
        assert result.payload["trajectory_id"] == 42
        trajectory_store.append.assert_called_once()
        call_kwargs = trajectory_store.append.call_args
        # 第一个 positional arg 是 entry_type
        from src.core.trajectory_store import TrajectoryEntryType
        assert call_kwargs.args[0] == TrajectoryEntryType.TELL_USER
        assert call_kwargs.args[1] == "chat-x"  # source_chat_id
        assert call_kwargs.args[2] == "lapwing"  # actor
        assert call_kwargs.args[3] == {"text": "hi"}

    async def test_records_mutation_log(self):
        sent: list[str] = []

        async def send_fn(text: str) -> None:
            sent.append(text)

        mutation_log = MagicMock()
        mutation_log.record = AsyncMock(return_value=99)

        ctx = _make_ctx(
            send_fn=send_fn,
            services={"mutation_log": mutation_log},
        )
        await tell_user_executor(
            ToolExecutionRequest(name="tell_user", arguments={"text": "yo"}),
            ctx,
        )

        from src.logging.state_mutation_log import MutationType
        mutation_log.record.assert_called_once()
        args = mutation_log.record.call_args
        assert args.args[0] == MutationType.TELL_USER
        assert args.args[1]["text"] == "yo"
        assert args.args[1]["chat_id"] == "chat-x"
        assert args.args[1]["adapter"] == "qq"

    async def test_continues_when_trajectory_append_fails(self):
        """Trajectory write failure must not break the user-visible send."""
        sent: list[str] = []

        async def send_fn(text: str) -> None:
            sent.append(text)

        trajectory_store = MagicMock()
        trajectory_store.append = AsyncMock(side_effect=RuntimeError("db locked"))

        ctx = _make_ctx(
            send_fn=send_fn,
            services={"trajectory_store": trajectory_store},
        )
        result = await tell_user_executor(
            ToolExecutionRequest(name="tell_user", arguments={"text": "hi"}),
            ctx,
        )

        assert result.success is True  # send was successful
        assert sent == ["hi"]
        assert result.payload["trajectory_id"] is None
