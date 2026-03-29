"""tests/memory/test_file_memory.py — file_memory.py 测试。"""

import pytest
from pathlib import Path

from src.memory.file_memory import read_memory_file, read_recent_summaries


class TestReadMemoryFile:
    async def test_returns_empty_for_nonexistent_file(self, tmp_path):
        result = await read_memory_file(tmp_path / "nonexistent.md")
        assert result == ""

    async def test_reads_file_content(self, tmp_path):
        f = tmp_path / "notes.md"
        f.write_text("# 笔记\n\n内容在这里。", encoding="utf-8")
        result = await read_memory_file(f)
        assert result == "# 笔记\n\n内容在这里。"

    async def test_strips_leading_trailing_whitespace(self, tmp_path):
        f = tmp_path / "notes.md"
        f.write_text("  \n内容\n  ", encoding="utf-8")
        result = await read_memory_file(f)
        assert result == "内容"

    async def test_truncates_long_content(self, tmp_path):
        f = tmp_path / "long.md"
        long_text = "a" * 3000
        f.write_text(long_text, encoding="utf-8")
        result = await read_memory_file(f, max_chars=100)
        assert len(result) <= 130  # 截断标记会增加一点长度
        assert "截断" in result

    async def test_no_truncation_when_within_limit(self, tmp_path):
        f = tmp_path / "short.md"
        text = "短内容"
        f.write_text(text, encoding="utf-8")
        result = await read_memory_file(f, max_chars=100)
        assert result == text
        assert "截断" not in result


class TestReadRecentSummaries:
    async def test_returns_empty_for_nonexistent_dir(self, tmp_path):
        result = await read_recent_summaries(tmp_path / "nonexistent")
        assert result == ""

    async def test_returns_empty_for_empty_dir(self, tmp_path):
        summaries_dir = tmp_path / "summaries"
        summaries_dir.mkdir()
        result = await read_recent_summaries(summaries_dir)
        assert result == ""

    async def test_reads_single_summary_file(self, tmp_path):
        summaries_dir = tmp_path / "summaries"
        summaries_dir.mkdir()
        (summaries_dir / "2026-03-29_120000.md").write_text("摘要内容", encoding="utf-8")
        result = await read_recent_summaries(summaries_dir)
        assert "摘要内容" in result

    async def test_returns_latest_files_first(self, tmp_path):
        summaries_dir = tmp_path / "summaries"
        summaries_dir.mkdir()
        (summaries_dir / "2026-03-27_000000.md").write_text("旧摘要", encoding="utf-8")
        (summaries_dir / "2026-03-29_000000.md").write_text("新摘要", encoding="utf-8")
        result = await read_recent_summaries(summaries_dir, max_files=5)
        # 新摘要排在前
        assert result.index("新摘要") < result.index("旧摘要")

    async def test_limits_number_of_files(self, tmp_path):
        summaries_dir = tmp_path / "summaries"
        summaries_dir.mkdir()
        for i in range(10):
            (summaries_dir / f"2026-03-{i+1:02d}_000000.md").write_text(f"摘要{i}", encoding="utf-8")
        result = await read_recent_summaries(summaries_dir, max_files=3)
        # 只有 3 个文件的内容
        count = sum(1 for i in range(10) if f"摘要{i}" in result)
        assert count == 3

    async def test_ignores_non_md_files(self, tmp_path):
        summaries_dir = tmp_path / "summaries"
        summaries_dir.mkdir()
        (summaries_dir / "2026-03-29_000000.md").write_text("md内容", encoding="utf-8")
        (summaries_dir / ".gitkeep").write_text("", encoding="utf-8")
        result = await read_recent_summaries(summaries_dir)
        assert "md内容" in result
        assert ".gitkeep" not in result
