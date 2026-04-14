"""TaskResumptionAction 测试（v2 零模板设计）。"""

import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.heartbeat import SenseContext
from src.core.pending_task import (
    MAX_RETRY_COUNT,
    MAX_SKIP_COUNT,
    PendingTask,
    PendingTaskStore,
)
from src.heartbeat.actions.task_resumption import (
    CONTINUE_MARKER,
    SKIP_MARKER,
    TaskResumptionAction,
)


@pytest.fixture
def ctx():
    return SenseContext(
        beat_type="minute",
        now=datetime.now(timezone.utc),
        last_interaction=None,
        silence_hours=0.1,
        user_facts_summary="",
        recent_memory_summary="",
        chat_id="c1",
    )


@pytest.fixture
def mock_brain():
    b = MagicMock()
    b.memory = MagicMock()
    b.memory.get = AsyncMock(return_value=[
        {"role": "user", "content": "帮我搜一下终末地的前瞻"},
        {"role": "assistant", "content": "找到了一些信息"},
    ])
    b.memory.append = AsyncMock()
    b.router = MagicMock()
    b.router.query_lightweight = AsyncMock(
        return_value=f"对了刚才终末地的角色信息没找全 我再找找\n{CONTINUE_MARKER}"
    )
    b.channel_manager = None
    b.think_conversational = AsyncMock(return_value="补上了 角色信息...")
    b.pending_task_store = None
    return b


@pytest.fixture
def mock_send_fn():
    return AsyncMock()


@pytest.fixture
def store(tmp_path) -> PendingTaskStore:
    return PendingTaskStore(tmp_path / "pending_tasks.json")


def _make_task(task_id: str = "pt-001", **kwargs) -> PendingTask:
    defaults = {
        "task_id": task_id,
        "chat_id": "c1",
        "user_id": "kevin",
        "adapter": "qq",
        "user_request": "帮我搜一下终末地1.2的前瞻内容",
        "completed_steps": [
            {"tool": "web_search", "result_brief": "搜到了部分"},
        ],
        "partial_result": "部分结果",
        "remaining_description": "角色信息还没整理完",
        "termination_reason": "max_rounds_exceeded",
        "original_task_id": task_id,
    }
    defaults.update(kwargs)
    return PendingTask(**defaults)


class TestTaskResumptionAction:
    def test_name(self):
        assert TaskResumptionAction().name == "task_resumption"

    def test_beat_types(self):
        assert "minute" in TaskResumptionAction().beat_types

    def test_selection_mode_always(self):
        assert TaskResumptionAction().selection_mode == "always"

    async def test_no_store_does_nothing(self, ctx, mock_brain, mock_send_fn):
        mock_brain.pending_task_store = None
        await TaskResumptionAction().execute(ctx, mock_brain, mock_send_fn)
        mock_send_fn.assert_not_called()

    async def test_no_tasks_does_nothing(self, ctx, mock_brain, mock_send_fn, store):
        mock_brain.pending_task_store = store
        await TaskResumptionAction().execute(ctx, mock_brain, mock_send_fn)
        mock_send_fn.assert_not_called()

    async def test_full_resumption_flow(self, ctx, mock_brain, mock_send_fn, store):
        """正常恢复流程：LLM 生成通知 → 发送 → think_conversational。"""
        task = _make_task()
        store.save(task)
        mock_brain.pending_task_store = store

        await TaskResumptionAction().execute(ctx, mock_brain, mock_send_fn)

        # 通知消息被发送
        mock_send_fn.assert_awaited_once()
        sent_text = mock_send_fn.call_args[0][0]
        assert "终末地" in sent_text or "角色" in sent_text or "找找" in sent_text

        # 通知写入对话历史
        mock_brain.memory.append.assert_awaited_once()
        append_args = mock_brain.memory.append.call_args
        assert append_args[0][1] == "assistant"

        # 触发 think_conversational（空文本 + metadata）
        mock_brain.think_conversational.assert_awaited_once()
        call_kwargs = mock_brain.think_conversational.call_args.kwargs
        assert call_kwargs.get("user_message") == "" or mock_brain.think_conversational.call_args[0][1] == ""
        metadata = call_kwargs.get("metadata", {})
        assert metadata["source"] == "task_resumption"
        assert "resumption_context" in metadata

        # 旧任务被移除
        assert store.get("pt-001") is None

    async def test_skip_when_topic_conflict(self, ctx, mock_brain, mock_send_fn, store):
        """Lapwing 判断不合适时跳过。"""
        task = _make_task()
        store.save(task)
        mock_brain.pending_task_store = store
        mock_brain.router.query_lightweight = AsyncMock(return_value=SKIP_MARKER)

        await TaskResumptionAction().execute(ctx, mock_brain, mock_send_fn)

        # 不发送消息
        mock_send_fn.assert_not_called()
        # 不触发 think_conversational
        mock_brain.think_conversational.assert_not_called()
        # 任务仍在，skip_count +1
        remaining = store.get("pt-001")
        assert remaining is not None
        assert remaining.skip_count == 1

    async def test_skip_count_accumulation(self, ctx, mock_brain, mock_send_fn, store):
        """多次跳过 skip_count 累加。"""
        task = _make_task(skip_count=3)
        store.save(task)
        mock_brain.pending_task_store = store
        mock_brain.router.query_lightweight = AsyncMock(return_value=SKIP_MARKER)

        await TaskResumptionAction().execute(ctx, mock_brain, mock_send_fn)

        remaining = store.get("pt-001")
        assert remaining is not None
        assert remaining.skip_count == 4

    async def test_skip_notice_injected_after_max_skips(
        self, ctx, mock_brain, mock_send_fn, store
    ):
        """skip_count >= MAX_SKIP_COUNT 时 prompt 中包含催促。"""
        task = _make_task(skip_count=MAX_SKIP_COUNT)
        store.save(task)
        mock_brain.pending_task_store = store

        await TaskResumptionAction().execute(ctx, mock_brain, mock_send_fn)

        # 检查 LLM 调用时 user 参数包含催促文本
        call_kwargs = mock_brain.router.query_lightweight.call_args.kwargs
        user_text = call_kwargs.get("user", "")
        assert "推迟" in user_text or "过去" in user_text

    async def test_notification_written_to_conversation_memory(
        self, ctx, mock_brain, mock_send_fn, store
    ):
        """通知消息写入对话历史。"""
        task = _make_task()
        store.save(task)
        mock_brain.pending_task_store = store

        await TaskResumptionAction().execute(ctx, mock_brain, mock_send_fn)

        mock_brain.memory.append.assert_awaited_once()
        args = mock_brain.memory.append.call_args[0]
        assert args[0] == "c1"  # chat_id
        assert args[1] == "assistant"  # role

    async def test_resumption_context_propagation(
        self, ctx, mock_brain, mock_send_fn, store
    ):
        """resumption_context 正确传递到 think_conversational。"""
        task = _make_task(
            original_task_id="pt-000",
            total_resumption_count=1,
        )
        store.save(task)
        mock_brain.pending_task_store = store

        await TaskResumptionAction().execute(ctx, mock_brain, mock_send_fn)

        call_kwargs = mock_brain.think_conversational.call_args.kwargs
        rc = call_kwargs["metadata"]["resumption_context"]
        assert rc["original_task_id"] == "pt-000"
        assert rc["total_resumption_count"] == 1
        assert rc["user_request"] == task.user_request
        assert rc["remaining_description"] == task.remaining_description

    async def test_channel_unavailable_no_retry_consumed(
        self, ctx, mock_brain, mock_send_fn, store
    ):
        """send_fn 为 None 时不消耗重试次数。"""
        task = _make_task()
        store.save(task)
        mock_brain.pending_task_store = store

        await TaskResumptionAction().execute(ctx, mock_brain, None)

        # 任务仍在，retry_count 没变
        remaining = store.get("pt-001")
        assert remaining is not None
        assert remaining.retry_count == 0

    async def test_llm_failure_graceful_degradation(
        self, ctx, mock_brain, mock_send_fn, store
    ):
        """LLM 调用失败时不崩溃，记录重试。"""
        task = _make_task()
        store.save(task)
        mock_brain.pending_task_store = store
        mock_brain.router.query_lightweight = AsyncMock(
            side_effect=Exception("API timeout")
        )

        await TaskResumptionAction().execute(ctx, mock_brain, mock_send_fn)

        # 不发送消息
        mock_send_fn.assert_not_called()
        # 重试计数 +1
        remaining = store.get("pt-001")
        assert remaining is not None
        assert remaining.retry_count == 1

    async def test_max_retries_removes_task(
        self, ctx, mock_brain, mock_send_fn, store
    ):
        """达到最大重试次数后任务被移除。"""
        task = _make_task(
            retry_count=MAX_RETRY_COUNT - 1,
            last_retry_at=time.time() - 120,  # 超过 cooldown
        )
        store.save(task)
        mock_brain.pending_task_store = store
        mock_brain.router.query_lightweight = AsyncMock(
            side_effect=Exception("API timeout")
        )

        await TaskResumptionAction().execute(ctx, mock_brain, mock_send_fn)

        # 任务被移除（max retries）
        assert store.get("pt-001") is None

    async def test_expired_tasks_cleaned_up(
        self, ctx, mock_brain, mock_send_fn, store
    ):
        """过期任务在执行前被清理。"""
        expired = _make_task(
            "pt-old",
            created_at=time.time() - 7200,
        )
        store.save(expired)
        mock_brain.pending_task_store = store

        await TaskResumptionAction().execute(ctx, mock_brain, mock_send_fn)

        # 过期任务被清理
        assert store.get("pt-old") is None
        # 没有可执行的任务
        mock_send_fn.assert_not_called()

    async def test_continue_marker_stripped_from_notification(
        self, ctx, mock_brain, mock_send_fn, store
    ):
        """WILL_CONTINUE 标记不发送给用户。"""
        task = _make_task()
        store.save(task)
        mock_brain.pending_task_store = store
        mock_brain.router.query_lightweight = AsyncMock(
            return_value=f"我继续查\n{CONTINUE_MARKER}"
        )

        await TaskResumptionAction().execute(ctx, mock_brain, mock_send_fn)

        sent_text = mock_send_fn.call_args[0][0]
        assert CONTINUE_MARKER not in sent_text
        assert "我继续查" in sent_text

    async def test_response_without_continue_marker(
        self, ctx, mock_brain, mock_send_fn, store
    ):
        """LLM 没写 WILL_CONTINUE 时整条消息当作通知。"""
        task = _make_task()
        store.save(task)
        mock_brain.pending_task_store = store
        mock_brain.router.query_lightweight = AsyncMock(
            return_value="对了 刚才那个还没做完 我接着来"
        )

        await TaskResumptionAction().execute(ctx, mock_brain, mock_send_fn)

        sent_text = mock_send_fn.call_args[0][0]
        assert "刚才" in sent_text

    async def test_only_one_task_resumed_per_tick(
        self, ctx, mock_brain, mock_send_fn, store
    ):
        """每轮最多恢复 1 个任务。"""
        store.save(_make_task("pt-001", created_at=time.time() - 100))
        store.save(_make_task("pt-002", created_at=time.time() - 50))
        mock_brain.pending_task_store = store

        await TaskResumptionAction().execute(ctx, mock_brain, mock_send_fn)

        # 只发送了 1 条消息
        assert mock_send_fn.call_count == 1
        # think_conversational 只调用了 1 次
        assert mock_brain.think_conversational.call_count == 1

    async def test_format_steps(self):
        """步骤格式化。"""
        action = TaskResumptionAction()
        steps = [
            {"tool": "web_search", "result_brief": "搜到了3条结果"},
            {"tool": "web_fetch", "result_brief": "获取了页面内容"},
        ]
        formatted = action._format_steps(steps)
        assert "web_search" in formatted
        assert "web_fetch" in formatted
        assert "1." in formatted
        assert "2." in formatted

    async def test_format_steps_empty(self):
        action = TaskResumptionAction()
        assert "没有记录" in action._format_steps([])

    async def test_get_recent_messages_no_memory(self, mock_brain):
        mock_brain.memory = None
        action = TaskResumptionAction()
        result = await action._get_recent_messages(mock_brain, "c1")
        assert "无法获取" in result

    async def test_get_recent_messages_empty_history(self, mock_brain):
        mock_brain.memory.get = AsyncMock(return_value=[])
        action = TaskResumptionAction()
        result = await action._get_recent_messages(mock_brain, "c1")
        assert "没有对话" in result

    async def test_get_recent_messages_truncates_long_content(self, mock_brain):
        mock_brain.memory.get = AsyncMock(return_value=[
            {"role": "user", "content": "x" * 200},
        ])
        action = TaskResumptionAction()
        result = await action._get_recent_messages(mock_brain, "c1")
        assert "..." in result
        assert len(result) < 200
