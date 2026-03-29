"""Tests for TacticalRules."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestMightBeCorrection:
    def test_detects_chinese_dont(self):
        from src.core.tactical_rules import _might_be_correction
        assert _might_be_correction("你不要每次都问我要不要继续") is True

    def test_detects_chinese_remember(self):
        from src.core.tactical_rules import _might_be_correction
        assert _might_be_correction("记住，下次别这样") is True

    def test_detects_english_dont(self):
        from src.core.tactical_rules import _might_be_correction
        assert _might_be_correction("don't ask me that again") is True

    def test_detects_english_stop(self):
        from src.core.tactical_rules import _might_be_correction
        assert _might_be_correction("stop adding emojis") is True

    def test_detects_wrong(self):
        from src.core.tactical_rules import _might_be_correction
        assert _might_be_correction("你说错了") is True

    def test_detects_question_mark(self):
        from src.core.tactical_rules import _might_be_correction
        assert _might_be_correction("为什么你总是这样？") is True

    def test_ignores_normal_message(self):
        from src.core.tactical_rules import _might_be_correction
        assert _might_be_correction("今天天气真好，我们去散步吧") is False

    def test_ignores_simple_greeting(self):
        from src.core.tactical_rules import _might_be_correction
        assert _might_be_correction("你好") is False


class TestAnalyzeCorrection:
    async def test_returns_rule_for_correction(self, tmp_path):
        with patch("src.core.tactical_rules.RULES_PATH", tmp_path / "rules.md"):
            from src.core.tactical_rules import TacticalRules
            router = MagicMock()
            router.complete = AsyncMock(return_value="不要每次都问用户是否要继续")
            rules = TacticalRules(router)
            result = await rules.analyze_correction(
                "你不要每次都问我要不要继续",
                [{"role": "user", "content": "帮我写代码"}, {"role": "assistant", "content": "好的，要继续吗？"}],
            )
        assert result == "不要每次都问用户是否要继续"

    async def test_returns_none_for_non_correction(self, tmp_path):
        with patch("src.core.tactical_rules.RULES_PATH", tmp_path / "rules.md"):
            from src.core.tactical_rules import TacticalRules
            router = MagicMock()
            router.complete = AsyncMock(return_value="（不是纠正）")
            rules = TacticalRules(router)
            result = await rules.analyze_correction("今天天气很好", [])
        assert result is None

    async def test_returns_none_on_llm_failure(self, tmp_path):
        with patch("src.core.tactical_rules.RULES_PATH", tmp_path / "rules.md"):
            from src.core.tactical_rules import TacticalRules
            router = MagicMock()
            router.complete = AsyncMock(side_effect=RuntimeError("fail"))
            rules = TacticalRules(router)
            result = await rules.analyze_correction("你不要这样", [])
        assert result is None

    async def test_returns_none_for_empty_response(self, tmp_path):
        with patch("src.core.tactical_rules.RULES_PATH", tmp_path / "rules.md"):
            from src.core.tactical_rules import TacticalRules
            router = MagicMock()
            router.complete = AsyncMock(return_value="  ")
            rules = TacticalRules(router)
            result = await rules.analyze_correction("你不要这样", [])
        assert result is None


class TestAddRule:
    async def test_creates_file_if_not_exists(self, tmp_path):
        rules_path = tmp_path / "rules.md"
        with patch("src.core.tactical_rules.RULES_PATH", rules_path):
            from src.core.tactical_rules import TacticalRules
            rules = TacticalRules(MagicMock())
            await rules.add_rule("不要每次询问是否继续")

        content = rules_path.read_text(encoding="utf-8")
        assert "不要每次询问是否继续" in content

    async def test_appends_to_existing_file(self, tmp_path):
        rules_path = tmp_path / "rules.md"
        rules_path.write_text("# 行为规则\n\n- [2026-01-01] 第一条规则\n", encoding="utf-8")
        with patch("src.core.tactical_rules.RULES_PATH", rules_path):
            from src.core.tactical_rules import TacticalRules
            rules = TacticalRules(MagicMock())
            await rules.add_rule("第二条规则")

        content = rules_path.read_text(encoding="utf-8")
        assert "第一条规则" in content
        assert "第二条规则" in content

    async def test_strips_placeholder_on_first_real_rule(self, tmp_path):
        rules_path = tmp_path / "rules.md"
        rules_path.write_text(
            "# 行为规则\n\n（暂无规则。规则会在对话中被纠正时自动积累。）\n",
            encoding="utf-8",
        )
        with patch("src.core.tactical_rules.RULES_PATH", rules_path):
            from src.core.tactical_rules import TacticalRules
            rules = TacticalRules(MagicMock())
            await rules.add_rule("新规则")

        content = rules_path.read_text(encoding="utf-8")
        assert "暂无规则" not in content
        assert "新规则" in content

    async def test_includes_date_prefix(self, tmp_path):
        rules_path = tmp_path / "rules.md"
        with patch("src.core.tactical_rules.RULES_PATH", rules_path):
            from src.core.tactical_rules import TacticalRules
            rules = TacticalRules(MagicMock())
            await rules.add_rule("测试规则")

        content = rules_path.read_text(encoding="utf-8")
        # Should have [YYYY-MM-DD] date prefix
        import re
        assert re.search(r"\[\d{4}-\d{2}-\d{2}\]", content)


class TestProcessCorrection:
    async def test_full_pipeline_writes_rule(self, tmp_path):
        rules_path = tmp_path / "rules.md"
        with patch("src.core.tactical_rules.RULES_PATH", rules_path):
            from src.core.tactical_rules import TacticalRules
            router = MagicMock()
            router.complete = AsyncMock(return_value="不要每次都加表情符号")
            rules = TacticalRules(router)
            result = await rules.process_correction(
                "chat1",
                "你别每次都加那么多表情",
                [{"role": "user", "content": "帮我写一封邮件"}],
            )

        assert result == "不要每次都加表情符号"
        assert rules_path.exists()
        content = rules_path.read_text(encoding="utf-8")
        assert "不要每次都加表情符号" in content

    async def test_pipeline_skips_write_when_not_correction(self, tmp_path):
        rules_path = tmp_path / "rules.md"
        with patch("src.core.tactical_rules.RULES_PATH", rules_path):
            from src.core.tactical_rules import TacticalRules
            router = MagicMock()
            router.complete = AsyncMock(return_value="（不是纠正）")
            rules = TacticalRules(router)
            result = await rules.process_correction("chat1", "今天天气好", [])

        assert result is None
        assert not rules_path.exists()
