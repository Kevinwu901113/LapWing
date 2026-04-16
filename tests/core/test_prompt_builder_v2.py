"""tests/core/test_prompt_builder_v2.py — Phase 2 PromptBuilder 测试。"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.prompt_builder import PromptBuilder, PromptSnapshotManager


@pytest.fixture
def identity_files(tmp_path):
    """创建临时身份文件。"""
    soul = tmp_path / "soul.md"
    soul.write_text("# Lapwing\n\n我是 Lapwing。", encoding="utf-8")

    constitution = tmp_path / "constitution.md"
    constitution.write_text("# 宪法\n\n不可违反。", encoding="utf-8")

    return soul, constitution


@pytest.fixture
def builder(identity_files):
    soul, constitution = identity_files
    return PromptBuilder(
        soul_path=soul,
        constitution_path=constitution,
        voice_path="lapwing_voice",
    )


class TestFourLayerAssembly:
    """测试 4 层 prompt 组装。"""

    async def test_soul_is_first_layer(self, builder):
        with patch("src.core.prompt_builder._get_period_name", return_value="下午"):
            result = await builder.build_system_prompt(channel="desktop")
        assert result.startswith("# Lapwing")

    async def test_constitution_is_second_layer(self, builder):
        with patch("src.core.prompt_builder._get_period_name", return_value="下午"):
            result = await builder.build_system_prompt(channel="desktop")
        soul_pos = result.index("# Lapwing")
        constitution_pos = result.index("# 宪法")
        assert soul_pos < constitution_pos

    async def test_runtime_state_is_third_layer(self, builder):
        with patch("src.core.prompt_builder._get_period_name", return_value="下午"):
            result = await builder.build_system_prompt(channel="desktop")
        assert "## 当前状态" in result
        constitution_pos = result.index("# 宪法")
        state_pos = result.index("## 当前状态")
        assert constitution_pos < state_pos

    async def test_layers_separated_by_dividers(self, builder):
        with patch("src.core.prompt_builder._get_period_name", return_value="下午"):
            result = await builder.build_system_prompt(channel="desktop")
        assert "\n\n---\n\n" in result

    async def test_missing_soul_file_doesnt_crash(self, tmp_path):
        builder = PromptBuilder(
            soul_path=tmp_path / "nonexistent.md",
            constitution_path=tmp_path / "also_nonexistent.md",
        )
        with patch("src.core.prompt_builder._get_period_name", return_value="下午"):
            result = await builder.build_system_prompt(channel="desktop")
        assert "## 当前状态" in result


class TestRuntimeState:
    """测试运行时状态注入。"""

    async def test_time_injected(self, builder):
        with patch("src.core.prompt_builder._get_period_name", return_value="下午"):
            result = await builder.build_system_prompt(channel="desktop")
        assert "当前时间" in result
        assert "台北时间" in result

    async def test_channel_desktop(self, builder):
        with patch("src.core.prompt_builder._get_period_name", return_value="下午"):
            result = await builder.build_system_prompt(channel="desktop")
        assert "Desktop（面对面）" in result

    async def test_channel_qq(self, builder):
        with patch("src.core.prompt_builder._get_period_name", return_value="下午"):
            result = await builder.build_system_prompt(channel="qq")
        assert "QQ 私聊（和 Kevin）" in result

    async def test_channel_qq_group_with_actor(self, builder):
        with patch("src.core.prompt_builder._get_period_name", return_value="下午"):
            result = await builder.build_system_prompt(
                channel="qq_group",
                actor_id="12345",
                actor_name="小明",
                auth_level=1,
                group_id="67890",
            )
        assert "小明" in result
        assert "GUEST" in result
        assert "群 67890" in result

    async def test_qq_group_without_actor_skips_speaker_info(self, builder):
        with patch("src.core.prompt_builder._get_period_name", return_value="下午"):
            result = await builder.build_system_prompt(
                channel="qq_group", group_id="123"
            )
        assert "当前说话人" not in result

    async def test_reminders_injected(self, tmp_path, identity_files):
        soul, constitution = identity_files
        mock_memory = AsyncMock()
        mock_memory.get_due_reminders = AsyncMock(return_value=[
            {"content": "提醒 Kevin 吃饭", "next_trigger_at": "18:00"},
        ])

        builder = PromptBuilder(
            soul_path=soul,
            constitution_path=constitution,
            reminder_source=mock_memory,
        )
        with patch("src.core.prompt_builder._get_period_name", return_value="下午"):
            result = await builder.build_system_prompt(channel="desktop")
        assert "提醒 Kevin 吃饭" in result

    async def test_tasks_injected(self, tmp_path, identity_files):
        soul, constitution = identity_files

        mock_task = MagicMock()
        mock_task.request = "帮 Kevin 查道奇赛程"
        mock_store = AsyncMock()
        mock_store.list_active = AsyncMock(return_value=[mock_task])

        builder = PromptBuilder(
            soul_path=soul,
            constitution_path=constitution,
            task_store=mock_store,
        )
        with patch("src.core.prompt_builder._get_period_name", return_value="下午"):
            result = await builder.build_system_prompt(channel="desktop")
        assert "帮 Kevin 查道奇赛程" in result

    async def test_reminder_error_doesnt_crash(self, identity_files):
        soul, constitution = identity_files
        mock_memory = AsyncMock()
        mock_memory.get_due_reminders = AsyncMock(side_effect=Exception("DB error"))

        builder = PromptBuilder(
            soul_path=soul,
            constitution_path=constitution,
            reminder_source=mock_memory,
        )
        with patch("src.core.prompt_builder._get_period_name", return_value="下午"):
            result = await builder.build_system_prompt(channel="desktop")
        assert "## 当前状态" in result


class TestVoiceReminder:
    """测试 voice reminder 注入。"""

    def test_short_conversation_appends_to_system(self, builder):
        messages = [
            {"role": "system", "content": "系统提示"},
            {"role": "user", "content": "你好"},
        ]
        with patch("src.core.prompt_builder.load_prompt", return_value="VOICE"):
            with patch("src.core.prompt_builder._get_period_name", return_value="下午"):
                builder.inject_voice_reminder(messages)
        assert "VOICE" in messages[0]["content"]

    def test_medium_conversation_injects_at_depth2(self, builder):
        messages = [
            {"role": "system", "content": "系统"},
            {"role": "user", "content": "1"},
            {"role": "assistant", "content": "2"},
            {"role": "user", "content": "3"},
            {"role": "assistant", "content": "4"},
        ]
        with patch("src.core.prompt_builder.load_prompt", return_value="VOICE"):
            with patch("src.core.prompt_builder._get_period_name", return_value="下午"):
                builder.inject_voice_reminder(messages)
        # 应该在倒数第 2 个位置前插入
        assert len(messages) == 6
        injected = messages[-3]
        assert "VOICE" in injected["content"]

    def test_long_conversation_includes_persona_anchor(self, builder):
        messages = [
            {"role": "system", "content": "系统"},
            {"role": "user", "content": "1"},
            {"role": "assistant", "content": "2"},
            {"role": "user", "content": "3"},
            {"role": "assistant", "content": "4"},
            {"role": "user", "content": "5"},
            {"role": "assistant", "content": "6"},
        ]
        with patch("src.core.prompt_builder.load_prompt", return_value="VOICE"):
            with patch("src.core.prompt_builder._get_period_name", return_value="下午"):
                builder.inject_voice_reminder(messages)
        assert len(messages) == 8
        injected = messages[-3]
        assert "Lapwing" in injected["content"]


class TestPromptSnapshotManager:
    def test_freeze_and_get(self):
        mgr = PromptSnapshotManager()
        mgr.freeze("session1", "prompt_content")
        assert mgr.get("session1") == "prompt_content"

    def test_get_wrong_session(self):
        mgr = PromptSnapshotManager()
        mgr.freeze("session1", "prompt_content")
        assert mgr.get("session2") is None

    def test_invalidate(self):
        mgr = PromptSnapshotManager()
        mgr.freeze("session1", "prompt_content")
        mgr.invalidate()
        assert mgr.get("session1") is None
