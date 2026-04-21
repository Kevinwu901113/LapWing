"""tests/memory/test_compactor.py — ConversationCompactor 测试。"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.memory.compactor import (
    ConversationCompactor,
    SUMMARY_PREFIX,
    _prune_tool_outputs,
    _format_for_summary,
)


@pytest.fixture
def summaries_dir(tmp_path):
    d = tmp_path / "summaries"
    d.mkdir()
    return d


def _make_trajectory_rows(history: list[dict]):
    """Build mock trajectory entries from a legacy-shape message list.

    ``trajectory_entries_to_messages`` inverts these back into the
    ``[{role, content}]`` shape that ``try_compact`` feeds into
    ``_do_compact``.
    """
    from src.core.trajectory_store import TrajectoryEntry, TrajectoryEntryType

    rows = []
    for i, msg in enumerate(history):
        role = msg.get("role", "user")
        text = msg.get("content", "")
        if role == "user":
            et = TrajectoryEntryType.USER_MESSAGE.value
            actor = "user"
        elif role == "assistant":
            et = TrajectoryEntryType.ASSISTANT_TEXT.value
            actor = "lapwing"
        else:
            et = TrajectoryEntryType.ASSISTANT_TEXT.value
            actor = "system"
        rows.append(TrajectoryEntry(
            id=i + 1, timestamp=float(i),
            entry_type=et, source_chat_id="chat1", actor=actor,
            content={"text": text},
            related_commitment_id=None,
            related_iteration_id=None,
            related_tool_call_id=None,
        ))
    return rows


@pytest.fixture
def mock_trajectory():
    traj = MagicMock()
    traj.relevant_to_chat = AsyncMock(return_value=[])
    return traj


@pytest.fixture
def mock_router():
    router = MagicMock()
    router.complete = AsyncMock(return_value="今天聊了很多。")
    return router


@pytest.fixture
def compactor(mock_trajectory, mock_router, summaries_dir):
    with patch("src.memory.compactor.CONVERSATION_SUMMARIES_DIR", summaries_dir), \
         patch("src.memory.compactor.load_prompt", return_value="压缩提示 {conversation}"):
        c = ConversationCompactor(mock_router, trajectory=mock_trajectory)
        yield c


class TestShouldCompact:
    def test_returns_false_below_threshold(self, compactor):
        assert compactor.should_compact(31) is False

    def test_returns_true_at_threshold(self, compactor):
        assert compactor.should_compact(32) is True

    def test_returns_true_above_threshold(self, compactor):
        assert compactor.should_compact(40) is True


class TestTryCompact:
    async def test_skips_when_already_compacting(self, compactor):
        compactor._compacting.add("chat1")
        result = await compactor.try_compact("chat1")
        assert result is False

    async def test_skips_when_history_too_short(self, compactor, mock_trajectory):
        history = [{"role": "user", "content": "hi"}] * 5
        mock_trajectory.relevant_to_chat = AsyncMock(
            return_value=_make_trajectory_rows(history)
        )
        result = await compactor.try_compact("chat1")
        assert result is False

    async def test_compacting_flag_is_cleared_after_success(self, compactor, mock_trajectory, summaries_dir):
        history = [{"role": "user", "content": f"消息{i}"} for i in range(40)]
        mock_trajectory.relevant_to_chat = AsyncMock(
            return_value=_make_trajectory_rows(history)
        )
        await compactor.try_compact("chat1")
        assert "chat1" not in compactor._compacting

    async def test_compacting_flag_cleared_on_llm_failure(self, compactor, mock_trajectory, mock_router, summaries_dir):
        history = [{"role": "user", "content": f"消息{i}"} for i in range(40)]
        mock_trajectory.relevant_to_chat = AsyncMock(
            return_value=_make_trajectory_rows(history)
        )
        mock_router.complete.side_effect = RuntimeError("LLM 崩溃")
        result = await compactor.try_compact("chat1")
        assert result is False
        assert "chat1" not in compactor._compacting

    async def test_returns_false_without_trajectory(self, mock_router, summaries_dir):
        with patch("src.memory.compactor.CONVERSATION_SUMMARIES_DIR", summaries_dir):
            c = ConversationCompactor(mock_router)
        result = await c.try_compact("chat1")
        assert result is False


class TestDoCompact:
    async def test_writes_summary_file(self, compactor, mock_trajectory, summaries_dir):
        history = [{"role": "user", "content": f"消息{i}"} for i in range(40)]
        mock_trajectory.relevant_to_chat = AsyncMock(
            return_value=_make_trajectory_rows(history)
        )
        await compactor.try_compact("chat1")
        md_files = list(summaries_dir.glob("*.md"))
        assert len(md_files) == 1
        content = md_files[0].read_text(encoding="utf-8")
        assert "对话摘要" in content
        assert "今天聊了很多。" in content

    async def test_skips_when_compact_count_too_small(self, compactor, mock_trajectory):
        history = [{"role": "user", "content": f"消息{i}"} for i in range(5)]
        mock_trajectory.relevant_to_chat = AsyncMock(
            return_value=_make_trajectory_rows(history)
        )
        with patch.object(compactor, "should_compact", return_value=True):
            result = await compactor.try_compact("chat1")
        assert result is False

    async def test_handles_empty_llm_response(self, compactor, mock_trajectory, mock_router):
        mock_router.complete.return_value = "   "
        history = [{"role": "user", "content": f"消息{i}"} for i in range(20)]
        mock_trajectory.relevant_to_chat = AsyncMock(
            return_value=_make_trajectory_rows(history)
        )
        with patch.object(compactor, "should_compact", return_value=True):
            result = await compactor.try_compact("chat1")
        assert result is False

    async def test_compaction_succeeds_with_large_history(self, compactor, mock_trajectory, mock_router, summaries_dir):
        history = [{"role": "user", "content": f"消息{i}"} for i in range(40)]
        mock_trajectory.relevant_to_chat = AsyncMock(
            return_value=_make_trajectory_rows(history)
        )
        result = await compactor.try_compact("chat1")
        assert result is True
        mock_router.complete.assert_awaited_once()


class TestPruneToolOutputs:
    def test_short_tool_output_kept(self):
        msgs = [{"role": "tool", "content": "OK"}]
        result = _prune_tool_outputs(msgs)
        assert result[0]["content"] == "OK"

    def test_long_tool_output_pruned(self):
        long_content = "x" * 500
        msgs = [{"role": "tool", "content": long_content}]
        result = _prune_tool_outputs(msgs)
        assert "工具输出已精简" in result[0]["content"]
        assert "500" in result[0]["content"]

    def test_user_messages_untouched(self):
        msgs = [{"role": "user", "content": "x" * 500}]
        result = _prune_tool_outputs(msgs)
        assert result[0]["content"] == "x" * 500

    def test_custom_threshold(self):
        msgs = [{"role": "tool", "content": "x" * 50}]
        result = _prune_tool_outputs(msgs, max_tool_content=30)
        assert "工具输出已精简" in result[0]["content"]


class TestFormatForSummary:
    def test_user_message(self):
        result = _format_for_summary([{"role": "user", "content": "你好"}])
        assert result == "用户: 你好"

    def test_tool_message(self):
        result = _format_for_summary([{"role": "tool", "content": "结果"}])
        assert result == "[工具结果]: 结果"

    def test_system_message(self):
        result = _format_for_summary([{"role": "system", "content": "系统"}])
        assert result == "[系统]: 系统"

    def test_assistant_message(self):
        result = _format_for_summary([{"role": "assistant", "content": "回复"}])
        assert result == "Lapwing: 回复"

    def test_list_content_blocks(self):
        result = _format_for_summary([{"role": "assistant", "content": [{"text": "hello"}, {"text": "world"}]}])
        assert result == "Lapwing: hello world"
