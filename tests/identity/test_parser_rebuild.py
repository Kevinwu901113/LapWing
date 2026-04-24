from __future__ import annotations

# 身份解析器重建 / 差异 / 校验测试
# Identity parser rebuild / diff / validate tests

import pytest

from src.identity.parser import IdentityParser
from src.identity.auth import create_kevin_auth
from src.identity.models import compute_claim_id_from_key


# ---------------------------------------------------------------------------
# Task 13: Rebuild 测试
# ---------------------------------------------------------------------------


class TestRebuildNewClaim:
    """首次重建创建修订"""

    async def test_rebuild_new_claim_creates_revision(self, store, tmp_path):
        md_file = tmp_path / "soul.md"
        md_file.write_text(
            "- [id=honesty][type=value] Lapwing values honesty.",
            encoding="utf-8",
        )
        parser = IdentityParser(store=store, identity_dir=tmp_path)
        report = await parser.rebuild(auth=create_kevin_auth("s1"))
        assert report.created == 1
        assert report.updated == 0

    async def test_rebuild_multiple_claims(self, store, tmp_path):
        md_file = tmp_path / "soul.md"
        md_file.write_text(
            "- [id=a] Claim A.\n- [id=b] Claim B.\n- [id=c] Claim C.",
            encoding="utf-8",
        )
        parser = IdentityParser(store=store, identity_dir=tmp_path)
        report = await parser.rebuild(auth=create_kevin_auth("s1"))
        assert report.created == 3


class TestRebuildUnchanged:
    """无变化重建不产生修订"""

    async def test_unchanged_produces_no_revision(self, store, tmp_path):
        """acceptance #11: no-op edit → 0 revisions"""
        md_file = tmp_path / "soul.md"
        md_file.write_text(
            "- [id=honesty] Lapwing values honesty.", encoding="utf-8"
        )
        parser = IdentityParser(store=store, identity_dir=tmp_path)
        auth = create_kevin_auth("s1")
        await parser.rebuild(auth=auth)
        report2 = await parser.rebuild(auth=auth)
        assert report2.created == 0
        assert report2.updated == 0
        assert report2.deprecated == 0

    async def test_trailing_whitespace_no_revision(self, store, tmp_path):
        """acceptance #11: trailing whitespace/newline → 0 revisions"""
        md_file = tmp_path / "soul.md"
        md_file.write_text(
            "- [id=honesty] Lapwing values honesty.", encoding="utf-8"
        )
        parser = IdentityParser(store=store, identity_dir=tmp_path)
        auth = create_kevin_auth("s1")
        await parser.rebuild(auth=auth)
        md_file.write_text(
            "- [id=honesty] Lapwing values honesty.\n\n", encoding="utf-8"
        )
        report2 = await parser.rebuild(auth=auth)
        assert report2.created == 0 and report2.updated == 0


class TestRebuildUpdated:
    """文本变化产生 UPDATE 修订"""

    async def test_text_change_produces_update(self, store, tmp_path):
        md_file = tmp_path / "soul.md"
        md_file.write_text(
            "- [id=honesty] Lapwing values honesty.", encoding="utf-8"
        )
        parser = IdentityParser(store=store, identity_dir=tmp_path)
        auth = create_kevin_auth("s1")
        await parser.rebuild(auth=auth)
        md_file.write_text(
            "- [id=honesty] Lapwing deeply values honesty.", encoding="utf-8"
        )
        report2 = await parser.rebuild(auth=auth)
        assert report2.updated == 1
        assert report2.created == 0


class TestTombstoneBlocksRebuild:
    """tombstone 阻止重建复活主张"""

    async def test_tombstone_blocks_rebuild(self, store, tmp_path):
        """acceptance A.4: tombstone prevents claim resurrection"""
        md_file = tmp_path / "soul.md"
        md_file.write_text(
            "- [id=secret] Secret info.", encoding="utf-8"
        )
        parser = IdentityParser(store=store, identity_dir=tmp_path)
        auth = create_kevin_auth("s1")
        await parser.rebuild(auth=auth)
        cid = compute_claim_id_from_key("soul.md", "secret")
        await store.erase_claim(cid, auth, "reason")
        report = await parser.rebuild(auth=auth)
        assert report.created == 0


class TestValidate:
    """校验身份文件"""

    def test_validate_strict_missing_id(self, tmp_path):
        """acceptance A.1: validate --strict fails on missing explicit id in production files"""
        md_file = tmp_path / "soul.md"
        md_file.write_text("- Claim without explicit id.", encoding="utf-8")
        parser = IdentityParser(identity_dir=tmp_path)
        result = parser.validate(strict=True, production_files=["soul.md"])
        assert result.passed is False
        assert len(result.warnings) > 0

    def test_validate_strict_passes_with_id(self, tmp_path):
        md_file = tmp_path / "soul.md"
        md_file.write_text("- [id=ok] Claim with id.", encoding="utf-8")
        parser = IdentityParser(identity_dir=tmp_path)
        result = parser.validate(strict=True, production_files=["soul.md"])
        assert result.passed is True

    def test_validate_non_strict_passes(self, tmp_path):
        md_file = tmp_path / "soul.md"
        md_file.write_text("- Claim without id.", encoding="utf-8")
        parser = IdentityParser(identity_dir=tmp_path)
        result = parser.validate(strict=False, production_files=["soul.md"])
        assert result.passed is True

    def test_validate_subdirectory_production_files(self, tmp_path):
        sub = tmp_path / "relationships"
        sub.mkdir()
        md_file = sub / "kevin.md"
        md_file.write_text("- No id claim.", encoding="utf-8")
        parser = IdentityParser(identity_dir=tmp_path)
        result = parser.validate(
            strict=True, production_files=["relationships/kevin.md"]
        )
        assert result.passed is False


class TestProvenanceUpdateNoRevision:
    """span 变化但文本不变 → 不产生修订，source 表更新"""

    async def test_provenance_update_no_revision(self, store, tmp_path):
        """acceptance A.5: span change without text change → no revision, source table updated"""
        md_file = tmp_path / "soul.md"
        md_file.write_text(
            "- [id=honesty] Lapwing values honesty.", encoding="utf-8"
        )
        parser = IdentityParser(store=store, identity_dir=tmp_path)
        auth = create_kevin_auth("s1")
        await parser.rebuild(auth=auth)
        # 在前面加标题，改变了 span 但不改变文本
        md_file.write_text(
            "# Title\n\n- [id=honesty] Lapwing values honesty.", encoding="utf-8"
        )
        report = await parser.rebuild(auth=auth)
        assert report.updated == 0
        cid = compute_claim_id_from_key("soul.md", "honesty")
        sources = await store.get_claim_sources(cid)
        assert sources[0]["source_span_start"] > 0


class TestRebuildMultipleFiles:
    """多文件重建"""

    async def test_rebuild_scans_all_md_files(self, store, tmp_path):
        (tmp_path / "soul.md").write_text(
            "- [id=soul1] Soul claim.", encoding="utf-8"
        )
        (tmp_path / "voice.md").write_text(
            "- [id=voice1] Voice claim.", encoding="utf-8"
        )
        parser = IdentityParser(store=store, identity_dir=tmp_path)
        report = await parser.rebuild(auth=create_kevin_auth("s1"))
        assert report.created == 2

    async def test_rebuild_skips_non_md_files(self, store, tmp_path):
        (tmp_path / "soul.md").write_text(
            "- [id=soul1] Claim.", encoding="utf-8"
        )
        (tmp_path / "notes.txt").write_text(
            "- [id=txt1] Not a md file.", encoding="utf-8"
        )
        parser = IdentityParser(store=store, identity_dir=tmp_path)
        report = await parser.rebuild(auth=create_kevin_auth("s1"))
        assert report.created == 1


class TestRebuildReport:
    """RebuildReport 准确性"""

    async def test_report_errors_on_bad_file(self, store, tmp_path):
        """如果目录为空，报告 0 创建"""
        parser = IdentityParser(store=store, identity_dir=tmp_path)
        report = await parser.rebuild(auth=create_kevin_auth("s1"))
        assert report.created == 0
        assert report.updated == 0
        assert len(report.errors) == 0
