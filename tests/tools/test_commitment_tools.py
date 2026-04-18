"""commit_promise / fulfill_promise / abandon_promise 工具测试 — Step 5 M2。

验证三个承诺工具的契约：
- commit_promise: deadline 计算 + reasoning + 缺 store 失败
- fulfill_promise: status → fulfilled，closing_note = result_summary
- abandon_promise: status → abandoned，closing_note = reason
- 工具失败模式：空参数、未挂载 store、不存在的 promise_id
- list_overdue: deadline 过去 + status open
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

from src.core.commitments import CommitmentStatus, CommitmentStore
from src.logging.state_mutation_log import StateMutationLog
from src.tools.commitments import (
    abandon_promise_executor,
    commit_promise_executor,
    fulfill_promise_executor,
)
from src.tools.types import (
    ToolExecutionContext,
    ToolExecutionRequest,
)


def _make_ctx(
    *, services: dict | None = None, chat_id: str = "chat-x",
) -> ToolExecutionContext:
    return ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
        services=services if services is not None else {},
        adapter="qq",
        user_id="user1",
        auth_level=2,
        chat_id=chat_id,
    )


@pytest.fixture
async def store(tmp_path):
    log = StateMutationLog(
        tmp_path / "mutation_log.db", logs_dir=tmp_path / "logs"
    )
    await log.init()
    s = CommitmentStore(tmp_path / "lapwing.db", log)
    await s.init()
    yield s
    await s.close()
    await log.close()


@pytest.mark.asyncio
class TestCommitPromise:
    async def test_creates_with_default_deadline(self, store):
        ctx = _make_ctx(services={"commitment_store": store})
        before = time.time()
        result = await commit_promise_executor(
            ToolExecutionRequest(
                name="commit_promise",
                arguments={"description": "查道奇下一场"},
            ),
            ctx,
        )

        assert result.success is True
        promise_id = result.payload["promise_id"]
        assert isinstance(promise_id, str)
        assert result.payload["deadline_minutes"] == 10

        row = await store.get(promise_id)
        assert row is not None
        assert row.content == "查道奇下一场"
        assert row.target_chat_id == "chat-x"
        assert row.status == CommitmentStatus.PENDING.value
        assert row.deadline is not None
        # ~10 分钟（600s）后
        assert before + 599 <= row.deadline <= before + 601

    async def test_custom_deadline_minutes(self, store):
        ctx = _make_ctx(services={"commitment_store": store})
        result = await commit_promise_executor(
            ToolExecutionRequest(
                name="commit_promise",
                arguments={"description": "搜东西", "deadline_minutes": 3},
            ),
            ctx,
        )
        assert result.success is True
        assert result.payload["deadline_minutes"] == 3
        row = await store.get(result.payload["promise_id"])
        assert row is not None
        assert row.deadline is not None
        # 3 分钟（180s）
        assert row.deadline - row.created_at == pytest.approx(180.0, abs=2)

    async def test_deadline_clamped(self, store):
        ctx = _make_ctx(services={"commitment_store": store})
        # 输入 99999 分钟 → clamp 到 1440（24 小时）
        result = await commit_promise_executor(
            ToolExecutionRequest(
                name="commit_promise",
                arguments={"description": "x", "deadline_minutes": 99999},
            ),
            ctx,
        )
        assert result.payload["deadline_minutes"] == 1440

    async def test_persists_reasoning(self, store):
        ctx = _make_ctx(services={"commitment_store": store})
        result = await commit_promise_executor(
            ToolExecutionRequest(
                name="commit_promise",
                arguments={
                    "description": "查比赛",
                    "reasoning": "用户问我棒球",
                },
            ),
            ctx,
        )
        row = await store.get(result.payload["promise_id"])
        assert row is not None
        assert row.reasoning == "用户问我棒球"

    async def test_fails_without_description(self, store):
        ctx = _make_ctx(services={"commitment_store": store})
        result = await commit_promise_executor(
            ToolExecutionRequest(name="commit_promise", arguments={"description": "  "}),
            ctx,
        )
        assert result.success is False
        assert result.payload["created"] is False

    async def test_fails_without_store(self):
        ctx = _make_ctx(services={})
        result = await commit_promise_executor(
            ToolExecutionRequest(name="commit_promise", arguments={"description": "x"}),
            ctx,
        )
        assert result.success is False
        assert "CommitmentStore" in result.payload["reason"]


@pytest.mark.asyncio
class TestFulfillPromise:
    async def test_marks_fulfilled_with_summary(self, store):
        ctx = _make_ctx(services={"commitment_store": store})
        # 先创建
        created = await commit_promise_executor(
            ToolExecutionRequest(
                name="commit_promise", arguments={"description": "查比赛"},
            ),
            ctx,
        )
        promise_id = created.payload["promise_id"]

        # 然后 fulfill
        result = await fulfill_promise_executor(
            ToolExecutionRequest(
                name="fulfill_promise",
                arguments={
                    "promise_id": promise_id,
                    "result_summary": "明晚十点对教士",
                },
            ),
            ctx,
        )
        assert result.success is True
        assert result.payload["status"] == "fulfilled"

        row = await store.get(promise_id)
        assert row is not None
        assert row.status == CommitmentStatus.FULFILLED.value
        assert row.closing_note == "明晚十点对教士"

    async def test_unknown_id_returns_failure(self, store):
        ctx = _make_ctx(services={"commitment_store": store})
        result = await fulfill_promise_executor(
            ToolExecutionRequest(
                name="fulfill_promise",
                arguments={
                    "promise_id": "nonexistent_hex_id",
                    "result_summary": "done",
                },
            ),
            ctx,
        )
        assert result.success is False
        assert "找不到" in result.payload["reason"]

    async def test_requires_summary(self, store):
        ctx = _make_ctx(services={"commitment_store": store})
        created = await commit_promise_executor(
            ToolExecutionRequest(
                name="commit_promise", arguments={"description": "x"},
            ),
            ctx,
        )
        result = await fulfill_promise_executor(
            ToolExecutionRequest(
                name="fulfill_promise",
                arguments={
                    "promise_id": created.payload["promise_id"],
                    "result_summary": "  ",
                },
            ),
            ctx,
        )
        assert result.success is False


@pytest.mark.asyncio
class TestAbandonPromise:
    async def test_marks_abandoned_with_reason(self, store):
        ctx = _make_ctx(services={"commitment_store": store})
        created = await commit_promise_executor(
            ToolExecutionRequest(
                name="commit_promise", arguments={"description": "搜某事"},
            ),
            ctx,
        )
        promise_id = created.payload["promise_id"]

        result = await abandon_promise_executor(
            ToolExecutionRequest(
                name="abandon_promise",
                arguments={
                    "promise_id": promise_id,
                    "reason": "搜不到，已告诉用户",
                },
            ),
            ctx,
        )
        assert result.success is True
        assert result.payload["status"] == "abandoned"

        row = await store.get(promise_id)
        assert row is not None
        assert row.status == CommitmentStatus.ABANDONED.value
        assert row.closing_note == "搜不到，已告诉用户"

    async def test_requires_reason(self, store):
        ctx = _make_ctx(services={"commitment_store": store})
        created = await commit_promise_executor(
            ToolExecutionRequest(
                name="commit_promise", arguments={"description": "x"},
            ),
            ctx,
        )
        result = await abandon_promise_executor(
            ToolExecutionRequest(
                name="abandon_promise",
                arguments={
                    "promise_id": created.payload["promise_id"],
                    "reason": "",
                },
            ),
            ctx,
        )
        assert result.success is False


@pytest.mark.asyncio
class TestListOverdue:
    async def test_overdue_deadline_appears(self, store):
        # 创建一个已经过期的承诺（deadline 在过去）
        cid = await store.create(
            "chat1", "已过期", source_trajectory_entry_id=0,
            deadline=time.time() - 60.0,
        )
        # 一个还没到期的
        await store.create(
            "chat1", "还有时间", source_trajectory_entry_id=0,
            deadline=time.time() + 600.0,
        )
        # 一个无 deadline（永不超时）
        await store.create(
            "chat1", "无期限", source_trajectory_entry_id=0,
        )

        overdue = await store.list_overdue(time.time())
        ids = [c.id for c in overdue]
        assert ids == [cid]

    async def test_overdue_excludes_closed(self, store):
        cid = await store.create(
            "chat1", "已完成且已过期", source_trajectory_entry_id=0,
            deadline=time.time() - 60.0,
        )
        await store.set_status(cid, CommitmentStatus.FULFILLED.value)

        overdue = await store.list_overdue(time.time())
        assert overdue == []

    async def test_overdue_chat_filter(self, store):
        a = await store.create(
            "chatA", "x", source_trajectory_entry_id=0,
            deadline=time.time() - 10.0,
        )
        b = await store.create(
            "chatB", "y", source_trajectory_entry_id=0,
            deadline=time.time() - 10.0,
        )

        only_a = await store.list_overdue(time.time(), chat_id="chatA")
        only_b = await store.list_overdue(time.time(), chat_id="chatB")
        assert [c.id for c in only_a] == [a]
        assert [c.id for c in only_b] == [b]

    async def test_overdue_ordered_by_deadline_asc(self, store):
        c1 = await store.create(
            "c", "early", source_trajectory_entry_id=0,
            deadline=time.time() - 100.0,
        )
        c2 = await store.create(
            "c", "later", source_trajectory_entry_id=0,
            deadline=time.time() - 10.0,
        )

        overdue = await store.list_overdue(time.time())
        assert [c.id for c in overdue] == [c1, c2]
