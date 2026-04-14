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


@pytest.fixture
def mock_memory():
    memory = MagicMock()
    memory._store = {}
    memory.get = AsyncMock(return_value=[])
    memory.replace_history = MagicMock(side_effect=lambda cid, h: memory._store.update({cid: h}))
    return memory


@pytest.fixture
def mock_router():
    router = MagicMock()
    router.complete = AsyncMock(return_value="今天聊了很多。")
    return router


@pytest.fixture
def compactor(mock_memory, mock_router, summaries_dir):
    with patch("src.memory.compactor.CONVERSATION_SUMMARIES_DIR", summaries_dir), \
         patch("src.memory.compactor.load_prompt", return_value="压缩提示 {conversation}"):
        c = ConversationCompactor(mock_memory, mock_router)
        yield c


class TestShouldCompact:
    def test_returns_false_below_threshold(self, compactor):
        # MAX_HISTORY_TURNS=20, max_messages=40, threshold=80% → 32
        assert compactor.should_compact(31) is False

    def test_returns_true_at_threshold(self, compactor):
        assert compactor.should_compact(32) is True

    def test_returns_true_above_threshold(self, compactor):
        assert compactor.should_compact(40) is True


class TestTryCompact:
    async def test_skips_when_already_compacting(self, compactor):
        """正在压缩时不触发第二次。"""
        compactor._compacting.add("chat1")
        result = await compactor.try_compact("chat1")
        assert result is False

    async def test_skips_when_history_too_short(self, compactor, mock_memory):
        mock_memory.get.return_value = [{"role": "user", "content": "hi"}] * 5
        result = await compactor.try_compact("chat1")
        assert result is False

    async def test_compacting_flag_is_cleared_after_success(self, compactor, mock_memory, summaries_dir):
        history = [{"role": "user", "content": f"消息{i}"} for i in range(40)]
        mock_memory.get.return_value = history
        mock_memory._store["chat1"] = history[:]
        await compactor.try_compact("chat1")
        assert "chat1" not in compactor._compacting

    async def test_compacting_flag_cleared_on_llm_failure(self, compactor, mock_memory, mock_router, summaries_dir):
        history = [{"role": "user", "content": f"消息{i}"} for i in range(40)]
        mock_memory.get.return_value = history
        mock_router.complete.side_effect = RuntimeError("LLM 崩溃")
        result = await compactor.try_compact("chat1")
        assert result is False
        assert "chat1" not in compactor._compacting


class TestDoCompact:
    async def test_replaces_history_with_summary_plus_tail(self, compactor, mock_memory, summaries_dir):
        """压缩后内存中存储：摘要消息 + 后 40% 历史。"""
        # 需要 32+ 条消息才能触发 compaction（80% of 40）
        history = [{"role": "user", "content": f"消息{i}"} for i in range(40)]
        mock_memory.get.return_value = history
        mock_memory._store["chat1"] = history[:]

        result = await compactor.try_compact("chat1")

        assert result is True
        new_history = mock_memory._store["chat1"]
        # 第一条是摘要消息
        assert new_history[0]["role"] == "system"
        assert "[之前的对话摘要]" in new_history[0]["content"]
        assert "今天聊了很多。" in new_history[0]["content"]
        # 后面是保留的原历史（后 40%）
        compact_count = int(len(history) * 0.6)
        kept = history[compact_count:]
        assert new_history[1:] == kept

    async def test_writes_summary_file(self, compactor, mock_memory, summaries_dir):
        """压缩后写入摘要文件。"""
        history = [{"role": "user", "content": f"消息{i}"} for i in range(40)]
        mock_memory.get.return_value = history
        mock_memory._store["chat1"] = history[:]

        await compactor.try_compact("chat1")

        md_files = list(summaries_dir.glob("*.md"))
        assert len(md_files) == 1
        content = md_files[0].read_text(encoding="utf-8")
        assert "对话摘要" in content
        assert "今天聊了很多。" in content

    async def test_skips_when_compact_count_too_small(self, compactor, mock_memory):
        """如果历史太短（compact_count < 4）不压缩。"""
        # 5 条消息，compact_count = int(5 * 0.6) = 3 < 4
        history = [{"role": "user", "content": f"消息{i}"} for i in range(5)]
        mock_memory.get.return_value = history
        # 手动触发 should_compact 通过
        with patch.object(compactor, "should_compact", return_value=True):
            result = await compactor.try_compact("chat1")
        assert result is False

    async def test_handles_empty_llm_response(self, compactor, mock_memory, mock_router):
        """LLM 返回空字符串时不压缩。"""
        mock_router.complete.return_value = "   "
        history = [{"role": "user", "content": f"消息{i}"} for i in range(20)]
        mock_memory.get.return_value = history
        with patch.object(compactor, "should_compact", return_value=True):
            result = await compactor.try_compact("chat1")
        assert result is False

    async def test_summary_includes_prefix(self, compactor, mock_memory, summaries_dir):
        """压缩后摘要消息包含安全前缀。"""
        history = [{"role": "user", "content": f"消息{i}"} for i in range(40)]
        mock_memory.get.return_value = history
        mock_memory._store["chat1"] = history[:]
        await compactor.try_compact("chat1")
        new_history = mock_memory._store["chat1"]
        assert SUMMARY_PREFIX in new_history[0]["content"]

    async def test_iterative_summary_preserves_prior(self, compactor, mock_memory, mock_router, summaries_dir):
        """第二次压缩时将前次摘要作为上下文传入。"""
        prior = {"role": "system", "content": "[之前的对话摘要] 第一次摘要内容"}
        history = [prior] + [{"role": "user", "content": f"消息{i}"} for i in range(39)]
        mock_memory.get.return_value = history
        mock_memory._store["chat1"] = history[:]
        await compactor.try_compact("chat1")
        # 验证 LLM 收到的 prompt 包含前次摘要
        call_args = mock_router.complete.call_args
        prompt_content = call_args[0][0][0]["content"]
        assert "前次摘要供参考" in prompt_content
        assert "第一次摘要内容" in prompt_content


# ── 工具输出修剪测试 ─────────────────────────────────────────────────────────

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
        assert "hello" in result
        assert "world" in result
