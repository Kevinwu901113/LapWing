"""AuthManager 路由与 binding 兼容性测试。"""

from __future__ import annotations

import sys
from unittest.mock import patch


def _clear_auth_modules() -> None:
    for mod in list(sys.modules.keys()):
        if mod.startswith("src.auth.") or mod in ("config.settings", "src.config", "src.config.settings"):
            del sys.modules[mod]
    try:
        from src.config.settings import get_settings
        get_settings.cache_clear()
    except ImportError:
        pass


def test_resolve_candidates_falls_back_to_env_when_binding_provider_mismatches(tmp_path):
    env = {
        "LLM_API_KEY": "generic-key",
        "LLM_BASE_URL": "https://api.minimaxi.com/v1",
        "LLM_MODEL": "minimax-m2.7",
        "LLM_PROVIDER": "minimax",
        "LLM_CHAT_API_KEY": "chat-key",
        "LLM_CHAT_BASE_URL": "https://api.minimaxi.com/v1",
        "LLM_CHAT_MODEL": "minimax-m2.7",
        "LLM_CHAT_PROVIDER": "minimax",
        "LLM_TOOL_API_KEY": "",
        "LLM_TOOL_BASE_URL": "",
        "LLM_TOOL_MODEL": "",
        "LLM_TOOL_PROVIDER": "",
        "NIM_API_KEY": "",
        "NIM_BASE_URL": "",
        "NIM_MODEL": "",
        "NIM_PROVIDER": "",
        "LLM_HEARTBEAT_PROVIDER": "",
    }
    with patch.dict("os.environ", env, clear=True):
        _clear_auth_modules()
        from src.auth.service import AuthManager
        from src.auth.storage import AuthStore

        store = AuthStore(tmp_path / "auth-profiles.json")
        store.upsert_profile(
            "openai:tester@example.com",
            {
                "provider": "openai",
                "type": "oauth",
                "accessToken": "access-token",
                "refreshToken": "refresh-token",
                "expiresAt": "2099-01-01T00:00:00Z",
            },
        )
        store.set_binding("chat", "openai:tester@example.com")

        auth = AuthManager(store=store)
        candidates = auth.resolve_candidates(
            purpose="chat",
            allow_failover=False,
        )

        assert len(candidates) == 1
        assert candidates[0].source == "env_fallback"
        assert candidates[0].auth_kind == "env"
        assert candidates[0].auth_value == "chat-key"

        status = auth.route_status()
        assert status["chat"]["provider"] == "minimax"
        assert status["chat"]["bindingProvider"] == "openai"
        assert status["chat"]["bindingMismatch"] is True

        _clear_auth_modules()


def test_unbind_profile_returns_clear_result(tmp_path):
    env = {
        "LLM_API_KEY": "generic-key",
        "LLM_BASE_URL": "https://api.minimaxi.com/v1",
        "LLM_MODEL": "minimax-m2.7",
    }
    with patch.dict("os.environ", env, clear=True):
        _clear_auth_modules()
        from src.auth.service import AuthManager
        from src.auth.storage import AuthStore

        store = AuthStore(tmp_path / "auth-profiles.json")
        store.upsert_profile(
            "openai:key",
            {
                "provider": "openai",
                "type": "api_key",
                "secretRef": {"kind": "literal", "value": "sk-test"},
            },
        )
        store.set_binding("chat", "openai:key")

        auth = AuthManager(store=store)
        assert auth.unbind_profile(purpose="chat") is True
        assert auth.unbind_profile(purpose="chat") is False

        _clear_auth_modules()
