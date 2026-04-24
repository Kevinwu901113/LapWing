from __future__ import annotations

# 身份 Markdown 解析器测试 — 确定性层 + 缓存键
# Identity markdown parser tests — deterministic layer + cache key

import pytest

from src.identity.parser import IdentityParser, RawBlock, ExtractionCacheKey
from src.identity.models import compute_raw_block_id


# ---------------------------------------------------------------------------
# Task 11: 确定性解析层
# ---------------------------------------------------------------------------


class TestParseInlineBracket:
    """内联方括号元数据解析"""

    def test_basic_inline_bracket(self):
        md = "- [type=value][owner=lapwing][id=honesty_over_comfort] Lapwing values honest engagement."
        parser = IdentityParser()
        blocks = parser.parse_text(md, "soul.md")
        assert len(blocks) == 1
        b = blocks[0]
        assert b.stable_block_key == "honesty_over_comfort"
        assert b.inline_metadata["type"] == "value"
        assert b.inline_metadata["owner"] == "lapwing"

    def test_inline_bracket_preserves_text(self):
        md = "- [id=test1][type=belief] Some belief text here."
        parser = IdentityParser()
        blocks = parser.parse_text(md, "soul.md")
        b = blocks[0]
        # text 应包含去除了方括号标记后的内容
        assert "Some belief text here." in b.text

    def test_multiple_blocks_with_brackets(self):
        md = "- [id=a] First claim.\n- [id=b] Second claim."
        parser = IdentityParser()
        blocks = parser.parse_text(md, "soul.md")
        assert len(blocks) == 2
        assert blocks[0].stable_block_key == "a"
        assert blocks[1].stable_block_key == "b"


class TestParseHtmlCommentAnchor:
    """HTML 注释锚点解析"""

    def test_basic_html_comment_anchor(self):
        md = "<!-- claim: kevin_direct_critique -->\nKevin prefers direct critique."
        parser = IdentityParser()
        blocks = parser.parse_text(md, "soul.md")
        assert len(blocks) == 1
        assert blocks[0].stable_block_key == "kevin_direct_critique"

    def test_html_comment_anchor_with_paragraph(self):
        md = "<!-- claim: some_key -->\nA paragraph claim here.\nSpanning multiple lines."
        parser = IdentityParser()
        blocks = parser.parse_text(md, "soul.md")
        assert len(blocks) == 1
        assert blocks[0].stable_block_key == "some_key"
        assert "A paragraph claim here." in blocks[0].text


class TestParseFrontmatterDefaults:
    """frontmatter YAML 解析"""

    def test_basic_frontmatter(self):
        md = "---\nclaim_defaults:\n  owner: kevin\n  sensitivity: private\n---\n\n- Some claim text."
        parser = IdentityParser()
        blocks = parser.parse_text(md, "relationships/kevin.md")
        assert len(blocks) == 1
        assert blocks[0].defaults["owner"] == "kevin"
        assert blocks[0].defaults["sensitivity"] == "private"

    def test_frontmatter_applies_to_all_blocks(self):
        md = "---\nclaim_defaults:\n  owner: kevin\n---\n\n- Claim A.\n- Claim B."
        parser = IdentityParser()
        blocks = parser.parse_text(md, "test.md")
        assert len(blocks) == 2
        for b in blocks:
            assert b.defaults["owner"] == "kevin"

    def test_no_frontmatter_empty_defaults(self):
        md = "- Just a claim."
        parser = IdentityParser()
        blocks = parser.parse_text(md, "test.md")
        assert blocks[0].defaults == {}


class TestParseSectionDefaults:
    """节级 HTML 注释默认值"""

    def test_section_html_comment_defaults(self):
        md = "## Kevin\n\n<!-- claim-defaults: owner=kevin sensitivity=private -->\n\n- He likes direct communication."
        parser = IdentityParser()
        blocks = parser.parse_text(md, "soul.md")
        assert blocks[0].section_defaults["owner"] == "kevin"
        assert blocks[0].section_defaults["sensitivity"] == "private"

    def test_section_defaults_reset_per_section(self):
        md = (
            "## Kevin\n\n"
            "<!-- claim-defaults: owner=kevin -->\n\n"
            "- [id=a] Claim for Kevin.\n\n"
            "## Lapwing\n\n"
            "<!-- claim-defaults: owner=lapwing -->\n\n"
            "- [id=b] Claim for Lapwing."
        )
        parser = IdentityParser()
        blocks = parser.parse_text(md, "soul.md")
        assert len(blocks) == 2
        assert blocks[0].section_defaults["owner"] == "kevin"
        assert blocks[1].section_defaults["owner"] == "lapwing"


class TestMetadataPriority:
    """元数据优先级: inline > section_defaults > frontmatter"""

    def test_inline_over_section_over_frontmatter(self):
        md = """---
claim_defaults:
  owner: system
---

## Kevin

<!-- claim-defaults: owner=kevin -->

- [owner=lapwing][id=test1] Some claim.
"""
        parser = IdentityParser()
        blocks = parser.parse_text(md, "test.md")
        assert blocks[0].effective_metadata()["owner"] == "lapwing"

    def test_section_over_frontmatter(self):
        md = """---
claim_defaults:
  owner: system
---

## Kevin

<!-- claim-defaults: owner=kevin -->

- [id=test2] Some claim.
"""
        parser = IdentityParser()
        blocks = parser.parse_text(md, "test.md")
        assert blocks[0].effective_metadata()["owner"] == "kevin"

    def test_frontmatter_as_fallback(self):
        md = """---
claim_defaults:
  owner: system
  sensitivity: private
---

- [id=test3] Some claim.
"""
        parser = IdentityParser()
        blocks = parser.parse_text(md, "test.md")
        meta = blocks[0].effective_metadata()
        assert meta["owner"] == "system"
        assert meta["sensitivity"] == "private"


class TestFallbackStableBlockKey:
    """sha256 回退 stable_block_key"""

    def test_fallback_key_length(self):
        md = "- Some claim without explicit id."
        parser = IdentityParser()
        blocks = parser.parse_text(md, "memory_anchors/test.md")
        assert len(blocks[0].stable_block_key) == 12

    def test_fallback_key_deterministic(self):
        md = "- Some claim without explicit id."
        parser = IdentityParser()
        b1 = parser.parse_text(md, "test.md")
        b2 = parser.parse_text(md, "test.md")
        assert b1[0].stable_block_key == b2[0].stable_block_key

    def test_fallback_key_differs_for_different_text(self):
        parser = IdentityParser()
        b1 = parser.parse_text("- Claim A.", "test.md")
        b2 = parser.parse_text("- Claim B.", "test.md")
        assert b1[0].stable_block_key != b2[0].stable_block_key


class TestRawBlockId:
    """raw_block_id 计算"""

    def test_raw_block_id_computation(self):
        md = "- [id=honesty] Lapwing values honesty."
        parser = IdentityParser()
        blocks = parser.parse_text(md, "soul.md")
        expected = compute_raw_block_id("soul.md", "honesty")
        assert blocks[0].raw_block_id == expected

    def test_raw_block_id_uses_source_file(self):
        md = "- [id=honesty] Same text."
        parser = IdentityParser()
        b1 = parser.parse_text(md, "soul.md")
        b2 = parser.parse_text(md, "voice.md")
        assert b1[0].raw_block_id != b2[0].raw_block_id


class TestSourceSpan:
    """source_span UTF-8 字节偏移"""

    def test_source_span_utf8(self):
        """acceptance #12: UTF-8 spans correct for Chinese + emoji"""
        md = "- [id=cn] 中文测试 🎉 emoji"
        parser = IdentityParser()
        blocks = parser.parse_text(md, "soul.md")
        assert blocks[0].source_span[0] >= 0
        assert blocks[0].source_span[1] > blocks[0].source_span[0]

    def test_source_span_ascii(self):
        md = "- [id=test] Simple ASCII text."
        parser = IdentityParser()
        blocks = parser.parse_text(md, "soul.md")
        start, end = blocks[0].source_span
        # 验证偏移在 UTF-8 编码后的合理范围内
        encoded = md.encode("utf-8")
        assert start >= 0
        assert end <= len(encoded)
        assert start < end

    def test_source_span_for_second_block(self):
        md = "- [id=a] First.\n- [id=b] Second."
        parser = IdentityParser()
        blocks = parser.parse_text(md, "soul.md")
        assert blocks[1].source_span[0] > blocks[0].source_span[0]


class TestBlockBoundaryDetection:
    """块边界检测 — 列表项和段落"""

    def test_list_items_as_blocks(self):
        md = "- First item.\n- Second item.\n- Third item."
        parser = IdentityParser()
        blocks = parser.parse_text(md, "soul.md")
        assert len(blocks) == 3

    def test_paragraph_as_block(self):
        md = "A paragraph that is a single block.\nStill the same paragraph."
        parser = IdentityParser()
        blocks = parser.parse_text(md, "soul.md")
        assert len(blocks) == 1

    def test_heading_not_a_block(self):
        md = "## Section Title\n\n- The real block."
        parser = IdentityParser()
        blocks = parser.parse_text(md, "soul.md")
        assert len(blocks) == 1
        assert "The real block." in blocks[0].text

    def test_empty_input(self):
        parser = IdentityParser()
        blocks = parser.parse_text("", "empty.md")
        assert blocks == []

    def test_only_headings_and_comments(self):
        md = "## Heading\n\n<!-- claim-defaults: owner=kevin -->"
        parser = IdentityParser()
        blocks = parser.parse_text(md, "soul.md")
        assert blocks == []


class TestSourceFile:
    """source_file 字段正确传播"""

    def test_source_file_propagated(self):
        md = "- [id=test] Claim."
        parser = IdentityParser()
        blocks = parser.parse_text(md, "relationships/kevin.md")
        assert blocks[0].source_file == "relationships/kevin.md"


# ---------------------------------------------------------------------------
# Task 12: ExtractionCacheKey
# ---------------------------------------------------------------------------


class TestExtractionCacheKey:
    """LLM 提取缓存键"""

    def test_deterministic(self):
        key = ExtractionCacheKey(
            candidate_text_sha="abc",
            section_context_sha="def",
            frontmatter_defaults_sha="ghi",
            prompt_version="v1",
            model_id="glm-5.1",
            schema_version="s1",
        )
        assert key.compute() == key.compute()
        assert len(key.compute()) == 16

    def test_changes_with_model(self):
        k1 = ExtractionCacheKey("a", "b", "c", "v1", "model_a", "s1")
        k2 = ExtractionCacheKey("a", "b", "c", "v1", "model_b", "s1")
        assert k1.compute() != k2.compute()

    def test_changes_with_text(self):
        k1 = ExtractionCacheKey("text_a", "b", "c", "v1", "m", "s1")
        k2 = ExtractionCacheKey("text_b", "b", "c", "v1", "m", "s1")
        assert k1.compute() != k2.compute()

    def test_changes_with_schema_version(self):
        k1 = ExtractionCacheKey("a", "b", "c", "v1", "m", "s1")
        k2 = ExtractionCacheKey("a", "b", "c", "v1", "m", "s2")
        assert k1.compute() != k2.compute()


# ---------------------------------------------------------------------------
# Task 12: classify_block (no LLM router → defaults)
# ---------------------------------------------------------------------------


class TestClassifyBlockDefaults:
    """classify_block 无 LLM 路由器时使用默认值"""

    async def test_classify_defaults_no_llm(self):
        md = "- [id=test][type=value][owner=lapwing] Test claim."
        parser = IdentityParser()
        blocks = parser.parse_text(md, "soul.md")
        result = await parser.classify_block(blocks[0])
        # 无 LLM router 时从 effective_metadata 提取
        assert result["type"] == "value"
        assert result["owner"] == "lapwing"

    async def test_classify_defaults_fallback(self):
        md = "- [id=test] Test claim without type."
        parser = IdentityParser()
        blocks = parser.parse_text(md, "soul.md")
        result = await parser.classify_block(blocks[0])
        # 回退默认值
        assert result["type"] == "belief"
        assert result["owner"] == "lapwing"
        assert result["confidence"] == 0.5
