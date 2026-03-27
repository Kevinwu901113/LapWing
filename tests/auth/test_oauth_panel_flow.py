"""Panel 发起的 OAuth 登录流测试。"""

from __future__ import annotations

from src.auth.service import AuthManager
from src.auth.storage import AuthStore


class FakeOpenAIProvider:
    provider_name = "openai"
    redirect_host = "127.0.0.1"
    redirect_port = 1455
    redirect_path = "/auth/callback"

    def build_authorization_request(self):
        return {
            "state": "state-1",
            "codeVerifier": "verifier-1",
            "authorizeUrl": "https://auth.openai.com/oauth/authorize?state=state-1",
            "redirectUri": "http://127.0.0.1:1455/auth/callback",
        }

    def complete_login(self, *, code: str, code_verifier: str, profile_id: str | None = None):
        assert code == "code-1"
        assert code_verifier == "verifier-1"
        resolved_profile_id = profile_id or "openai:tester@example.com"
        return resolved_profile_id, {
            "provider": "openai",
            "type": "oauth",
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "expiresAt": "2099-01-01T00:00:00Z",
            "email": "tester@example.com",
            "accountId": "acct_123",
        }

    def refresh(self, profile):
        return profile


def test_panel_oauth_login_stores_profile_after_callback(tmp_path):
    auth = AuthManager(store=AuthStore(tmp_path / "auth-profiles.json"))
    auth.providers["openai"] = FakeOpenAIProvider()

    session = auth.start_oauth_login(
        provider="openai",
        method="pkce",
        return_to="http://127.0.0.1:1420/",
    )
    status_code, _ = auth.oauth_logins.callback_response(
        provider_name="openai",
        state="state-1",
        code="code-1",
    )
    stored_session = auth.get_oauth_login_session(session["loginId"])
    stored_profile = auth.store.get_profile("openai:tester@example.com")

    assert status_code == 200
    assert stored_session["status"] == "completed"
    assert stored_session["resolvedProfileId"] == "openai:tester@example.com"
    assert stored_profile is not None
    assert stored_profile["type"] == "oauth"


def test_panel_oauth_login_marks_failure_when_provider_returns_error(tmp_path):
    auth = AuthManager(store=AuthStore(tmp_path / "auth-profiles.json"))
    auth.providers["openai"] = FakeOpenAIProvider()

    session = auth.start_oauth_login(provider="openai", method="pkce")
    status_code, _ = auth.oauth_logins.callback_response(
        provider_name="openai",
        state="state-1",
        code=None,
        error="access_denied",
        error_description="user denied access",
    )
    stored_session = auth.get_oauth_login_session(session["loginId"])

    assert status_code == 400
    assert stored_session["status"] == "failed"
    assert "user denied access" in (stored_session["error"] or "")
