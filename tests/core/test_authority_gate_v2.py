"""tests/core/test_authority_gate_v2.py — 四级权限测试。"""

from unittest.mock import patch

from src.core.authority_gate import AuthLevel, authorize, identify


class TestIdentify:
    def test_desktop_is_owner(self):
        with patch("src.core.authority_gate.DESKTOP_DEFAULT_OWNER", True):
            assert identify("desktop", "") == AuthLevel.OWNER

    def test_owner_id_recognized(self):
        with patch("src.core.authority_gate.OWNER_IDS", {"12345"}):
            assert identify("qq", "12345") == AuthLevel.OWNER

    def test_trusted_id_recognized(self):
        with patch("src.core.authority_gate.TRUSTED_IDS", {"67890"}):
            assert identify("qq", "67890") == AuthLevel.TRUSTED

    def test_unknown_qq_is_guest(self):
        with patch("src.core.authority_gate.OWNER_IDS", set()), \
             patch("src.core.authority_gate.TRUSTED_IDS", set()):
            assert identify("qq", "99999") == AuthLevel.GUEST

    def test_unknown_adapter_is_ignore(self):
        with patch("src.core.authority_gate.OWNER_IDS", set()), \
             patch("src.core.authority_gate.TRUSTED_IDS", set()):
            assert identify("unknown", "99999") == AuthLevel.IGNORE


class TestAuthorize:
    def test_owner_can_use_shell(self):
        allowed, reason = authorize("execute_shell", AuthLevel.OWNER)
        assert allowed
        assert reason == ""

    def test_guest_cannot_use_shell(self):
        allowed, reason = authorize("execute_shell", AuthLevel.GUEST)
        assert not allowed

    def test_trusted_can_research(self):
        allowed, reason = authorize("research", AuthLevel.TRUSTED)
        assert allowed

    def test_guest_can_chat(self):
        allowed, reason = authorize("chat", AuthLevel.GUEST)
        assert allowed

    def test_ignore_blocked(self):
        allowed, reason = authorize("chat", AuthLevel.IGNORE)
        assert not allowed
