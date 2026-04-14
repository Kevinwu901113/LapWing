"""AuthorityGate 单元测试。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.core.authority_gate import (
    AuthLevel,
    OPERATION_AUTH,
    authorize,
    identify,
)


# ── identify() 测试 ───────────────────────────────────────────────────────────

class TestIdentify:
    def test_desktop_default_owner(self):
        """桌面连接默认为 OWNER。"""
        with patch("src.core.authority_gate.DESKTOP_DEFAULT_OWNER", True):
            assert identify("desktop", "") == AuthLevel.OWNER
            assert identify("desktop", "unknown_user") == AuthLevel.OWNER

    def test_desktop_non_owner_when_disabled(self):
        """DESKTOP_DEFAULT_OWNER=False 时，桌面连接走正常权限查找。"""
        with patch("src.core.authority_gate.DESKTOP_DEFAULT_OWNER", False):
            with patch("src.core.authority_gate.OWNER_IDS", set()):
                with patch("src.core.authority_gate.TRUSTED_IDS", set()):
                    assert identify("desktop", "123") == AuthLevel.GUEST

    def test_owner_by_id_qq_adapter(self):
        with patch("src.core.authority_gate.OWNER_IDS", {"111"}):
            assert identify("qq", "111") == AuthLevel.OWNER

    def test_owner_by_id_qq(self):
        with patch("src.core.authority_gate.OWNER_IDS", {"222"}):
            assert identify("qq", "222") == AuthLevel.OWNER

    def test_trusted_by_id(self):
        with patch("src.core.authority_gate.OWNER_IDS", set()):
            with patch("src.core.authority_gate.TRUSTED_IDS", {"333"}):
                assert identify("qq", "333") == AuthLevel.TRUSTED

    def test_unknown_user_is_guest(self):
        with patch("src.core.authority_gate.OWNER_IDS", {"111"}):
            with patch("src.core.authority_gate.TRUSTED_IDS", {"222"}):
                assert identify("qq", "999") == AuthLevel.GUEST

    def test_owner_takes_priority_over_trusted(self):
        """同一 ID 同时在 OWNER_IDS 和 TRUSTED_IDS → OWNER 优先。"""
        with patch("src.core.authority_gate.OWNER_IDS", {"555"}):
            with patch("src.core.authority_gate.TRUSTED_IDS", {"555"}):
                assert identify("qq", "555") == AuthLevel.OWNER

    def test_empty_user_id_is_guest(self):
        with patch("src.core.authority_gate.OWNER_IDS", set()):
            with patch("src.core.authority_gate.TRUSTED_IDS", set()):
                assert identify("qq", "") == AuthLevel.GUEST


# ── authorize() 测试 ──────────────────────────────────────────────────────────

class TestAuthorize:
    def test_owner_can_execute_shell(self):
        allowed, reason = authorize("execute_shell", AuthLevel.OWNER)
        assert allowed
        assert reason == ""

    def test_owner_can_read_file(self):
        allowed, _ = authorize("read_file", AuthLevel.OWNER)
        assert allowed

    def test_owner_can_web_search(self):
        allowed, _ = authorize("web_search", AuthLevel.OWNER)
        assert allowed

    def test_trusted_can_web_search(self):
        allowed, _ = authorize("web_search", AuthLevel.TRUSTED)
        assert allowed

    def test_trusted_can_web_fetch(self):
        allowed, _ = authorize("web_fetch", AuthLevel.TRUSTED)
        assert allowed

    def test_trusted_can_file_list(self):
        allowed, _ = authorize("file_list_directory", AuthLevel.TRUSTED)
        assert allowed

    def test_trusted_cannot_execute_shell(self):
        allowed, reason = authorize("execute_shell", AuthLevel.TRUSTED)
        assert not allowed
        assert "Kevin" in reason

    def test_trusted_cannot_write_file(self):
        allowed, reason = authorize("write_file", AuthLevel.TRUSTED)
        assert not allowed
        assert "Kevin" in reason

    def test_trusted_cannot_run_python(self):
        allowed, _ = authorize("run_python_code", AuthLevel.TRUSTED)
        assert not allowed

    def test_trusted_cannot_memory_note(self):
        allowed, _ = authorize("memory_note", AuthLevel.TRUSTED)
        assert not allowed

    def test_guest_cannot_web_search(self):
        allowed, reason = authorize("web_search", AuthLevel.GUEST)
        assert not allowed
        assert reason  # 有拒绝理由

    def test_guest_cannot_execute_shell(self):
        allowed, _ = authorize("execute_shell", AuthLevel.GUEST)
        assert not allowed

    def test_unknown_tool_requires_owner(self):
        """未注册的工具默认需要 OWNER 权限。"""
        allowed_owner, _ = authorize("some_unknown_tool", AuthLevel.OWNER)
        allowed_trusted, _ = authorize("some_unknown_tool", AuthLevel.TRUSTED)
        assert allowed_owner
        assert not allowed_trusted

    def test_deny_reason_is_chinese(self):
        """拒绝理由应为中文。"""
        _, reason_owner_required = authorize("execute_shell", AuthLevel.GUEST)
        _, reason_trusted_required = authorize("web_search", AuthLevel.GUEST)
        # 检查包含中文字符
        assert any("\u4e00" <= c <= "\u9fff" for c in reason_owner_required)
        assert any("\u4e00" <= c <= "\u9fff" for c in reason_trusted_required)


# ── OPERATION_AUTH 完整性测试 ─────────────────────────────────────────────────

class TestOperationAuthTable:
    def test_all_high_risk_tools_require_owner(self):
        """高风险工具都应需要 OWNER 权限。"""
        high_risk_tools = [
            "execute_shell", "write_file", "file_write", "file_append",
            "apply_workspace_patch", "run_python_code", "memory_note",
        ]
        for tool in high_risk_tools:
            level = OPERATION_AUTH.get(tool)
            assert level == AuthLevel.OWNER, f"工具 {tool!r} 应需要 OWNER 权限，实际是 {level}"

    def test_info_tools_require_at_most_trusted(self):
        """信息查询工具不应需要 OWNER 权限。"""
        info_tools = ["web_search", "web_fetch", "file_list_directory"]
        for tool in info_tools:
            level = OPERATION_AUTH.get(tool)
            assert level is not None
            assert level <= AuthLevel.TRUSTED, (
                f"工具 {tool!r} 不应需要 OWNER 权限，实际是 {level}"
            )
