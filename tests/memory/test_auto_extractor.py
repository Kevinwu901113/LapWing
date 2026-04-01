"""tests/memory/test_auto_extractor.py — 自动记忆提取器测试。"""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock


def _make_messages(n: int) -> list[dict]:
    msgs = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": f"消息 {i}"})
    return msgs


def _make_extractor(tmp_path, response_text: str = "[]"):
    router = MagicMock()
    router.query_lightweight = AsyncMock(return_value=response_text)
    with patch("src.memory.auto_extractor.MEMORY_DIR", tmp_path / "memory"):
        (tmp_path / "memory").mkdir(exist_ok=True)
        from src.memory.auto_extractor import AutoMemoryExtractor
        extractor = AutoMemoryExtractor(router=router)
        return extractor, router


# ─── 短对话跳过 ────────────────────────────────────────────────────

class TestShortConversation:
    async def test_fewer_than_4_messages_skipped(self, tmp_path):
        extractor, router = _make_extractor(tmp_path)
        result = await extractor.extract_from_messages(_make_messages(3))
        assert result == []
        router.query_lightweight.assert_not_called()

    async def test_exactly_4_messages_proceeds(self, tmp_path):
        extractor, router = _make_extractor(tmp_path, response_text="[]")
        result = await extractor.extract_from_messages(_make_messages(4))
        router.query_lightweight.assert_called_once()
        assert result == []


# ─── JSON 解析 ────────────────────────────────────────────────────

class TestParseResponse:
    def test_valid_json(self, tmp_path):
        extractor, _ = _make_extractor(tmp_path)
        items = extractor._parse_response(
            '[{"category": "kevin_fact", "content": "喜欢咖啡", "importance": 4}]'
        )
        assert len(items) == 1
        assert items[0]["content"] == "喜欢咖啡"

    def test_empty_array(self, tmp_path):
        extractor, _ = _make_extractor(tmp_path)
        assert extractor._parse_response("[]") == []

    def test_malformed_json(self, tmp_path):
        extractor, _ = _make_extractor(tmp_path)
        assert extractor._parse_response("not json") == []

    def test_markdown_code_block_stripped(self, tmp_path):
        extractor, _ = _make_extractor(tmp_path)
        raw = '```json\n[{"category": "knowledge", "content": "Python 很棒"}]\n```'
        items = extractor._parse_response(raw)
        assert len(items) == 1
        assert items[0]["content"] == "Python 很棒"

    def test_invalid_category_filtered(self, tmp_path):
        extractor, _ = _make_extractor(tmp_path)
        items = extractor._parse_response(
            '[{"category": "invalid_cat", "content": "内容"}]'
        )
        assert items == []

    def test_missing_content_filtered(self, tmp_path):
        extractor, _ = _make_extractor(tmp_path)
        items = extractor._parse_response('[{"category": "kevin_fact"}]')
        assert items == []

    def test_non_list_response(self, tmp_path):
        extractor, _ = _make_extractor(tmp_path)
        assert extractor._parse_response('{"category": "kevin_fact", "content": "x"}') == []


# ─── 去重 ─────────────────────────────────────────────────────────

class TestDeduplication:
    async def test_duplicate_not_stored_twice(self, tmp_path):
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()

        with patch("src.memory.auto_extractor.MEMORY_DIR", mem_dir):
            from src.memory.auto_extractor import AutoMemoryExtractor
            router = MagicMock()
            content = "Kevin 喜欢喝咖啡"
            router.query_lightweight = AsyncMock(
                return_value=f'[{{"category": "kevin_fact", "content": "{content}"}}]'
            )
            extractor = AutoMemoryExtractor(router=router)

            # 第一次提取
            stored1 = await extractor.extract_from_messages(_make_messages(6))
            assert len(stored1) == 1

            # 第二次提取（相同内容）
            stored2 = await extractor.extract_from_messages(_make_messages(6))
            assert len(stored2) == 0

    async def test_new_content_stored(self, tmp_path):
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()

        with patch("src.memory.auto_extractor.MEMORY_DIR", mem_dir):
            from src.memory.auto_extractor import AutoMemoryExtractor
            router = MagicMock()

            call_count = 0

            async def side_effect(system, user, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return '[{"category": "kevin_fact", "content": "第一条记忆"}]'
                return '[{"category": "kevin_fact", "content": "第二条记忆"}]'

            router.query_lightweight = side_effect
            extractor = AutoMemoryExtractor(router=router)

            stored1 = await extractor.extract_from_messages(_make_messages(6))
            stored2 = await extractor.extract_from_messages(_make_messages(6))
            assert len(stored1) == 1
            assert len(stored2) == 1


# ─── 存储路径 ─────────────────────────────────────────────────────

class TestStorePath:
    async def test_stores_to_category_dir(self, tmp_path):
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()

        with patch("src.memory.auto_extractor.MEMORY_DIR", mem_dir):
            from src.memory.auto_extractor import AutoMemoryExtractor
            router = MagicMock()
            router.query_lightweight = AsyncMock(
                return_value='[{"category": "interest", "content": "对 AI 感兴趣"}]'
            )
            extractor = AutoMemoryExtractor(router=router)
            await extractor.extract_from_messages(_make_messages(6))

            interest_dir = mem_dir / "interest"
            assert interest_dir.exists()
            files = list(interest_dir.glob("*.md"))
            assert len(files) == 1
            assert "AI" in files[0].read_text(encoding="utf-8")

    async def test_llm_failure_returns_empty(self, tmp_path):
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()

        with patch("src.memory.auto_extractor.MEMORY_DIR", mem_dir):
            from src.memory.auto_extractor import AutoMemoryExtractor
            router = MagicMock()
            router.query_lightweight = AsyncMock(side_effect=Exception("LLM 超时"))
            extractor = AutoMemoryExtractor(router=router)
            result = await extractor.extract_from_messages(_make_messages(6))
            assert result == []
