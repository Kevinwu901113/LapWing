"""tests/tools/test_memory_note.py — memory_note 工具测试。"""

import pytest
from pathlib import Path
from unittest.mock import patch


@pytest.fixture
def isolated_paths(tmp_path):
    """把 KEVIN_NOTES_PATH 和 SELF_NOTES_PATH 重定向到临时目录。"""
    kevin_path = tmp_path / "KEVIN.md"
    self_path = tmp_path / "SELF.md"
    with patch("src.tools.memory_note.KEVIN_NOTES_PATH", kevin_path), \
         patch("src.tools.memory_note.SELF_NOTES_PATH", self_path), \
         patch("src.tools.memory_note._TARGET_PATHS", {"kevin": kevin_path, "self": self_path}):
        yield {"kevin": kevin_path, "self": self_path}


class TestWriteNote:
    async def test_invalid_target_returns_failure(self, isolated_paths):
        from src.tools.memory_note import write_note
        result = await write_note("unknown", "内容")
        assert result["success"] is False
        assert "无效的 target" in result["reason"]

    async def test_empty_content_returns_failure(self, isolated_paths):
        from src.tools.memory_note import write_note
        result = await write_note("kevin", "   ")
        assert result["success"] is False
        assert "内容为空" in result["reason"]

    async def test_write_to_kevin(self, isolated_paths):
        from src.tools.memory_note import write_note
        result = await write_note("kevin", "喜欢喝咖啡")
        assert result["success"] is True
        assert result["target"] == "kevin"
        content = isolated_paths["kevin"].read_text(encoding="utf-8")
        assert "喜欢喝咖啡" in content

    async def test_write_to_self(self, isolated_paths):
        from src.tools.memory_note import write_note
        result = await write_note("self", "今天心情不错")
        assert result["success"] is True
        assert result["target"] == "self"
        content = isolated_paths["self"].read_text(encoding="utf-8")
        assert "今天心情不错" in content

    async def test_creates_file_if_not_exists(self, isolated_paths):
        from src.tools.memory_note import write_note
        assert not isolated_paths["kevin"].exists()
        await write_note("kevin", "第一条笔记")
        assert isolated_paths["kevin"].exists()

    async def test_appends_to_existing_file(self, isolated_paths):
        from src.tools.memory_note import write_note
        isolated_paths["kevin"].write_text("# 已有内容\n", encoding="utf-8")
        await write_note("kevin", "新笔记")
        content = isolated_paths["kevin"].read_text(encoding="utf-8")
        assert "# 已有内容" in content
        assert "新笔记" in content

    async def test_date_prefix_in_entry(self, isolated_paths):
        from src.tools.memory_note import write_note
        await write_note("self", "有日期的笔记")
        content = isolated_paths["self"].read_text(encoding="utf-8")
        # 格式：> YYYY-MM-DD
        import re
        assert re.search(r"> \d{4}-\d{2}-\d{2}", content)

    async def test_target_is_case_insensitive(self, isolated_paths):
        from src.tools.memory_note import write_note
        result = await write_note("KEVIN", "大写目标")
        assert result["success"] is True
