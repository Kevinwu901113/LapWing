"""CLI OAuth 登录 + Codex auth.json 导入测试。"""

from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.auth.openai_codex import OpenAICodexAuthProvider, _decode_jwt_payload
from src.auth.service import AuthManager
from src.auth.storage import AuthStore


@pytest.fixture(autouse=True)
def _isolate_codex_auth_json(tmp_path, monkeypatch):
    """避免测试污染真实 ~/.codex/auth.json。"""
    monkeypatch.setattr("src.auth.openai_codex._CODEX_AUTH_JSON", tmp_path / "codex-auth.json")


def _make_jwt_payload(claims: dict) -> str:
    """构造一个假的 JWT（header.payload.signature）用于测试。"""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"{header}.{payload}.fake-signature"


def _make_auth_json(tmp_path, *, access_token=None, refresh_token="rt_test", account_id="acct_123"):
    """写一个临时 auth.json 并返回路径。"""
    if access_token is None:
        access_token = _make_jwt_payload({
            "exp": int(time.time()) + 86400,
            "https://api.openai.com/profile": {"email": "test@example.com"},
            "https://api.openai.com/auth": {
                "chatgpt_account_id": account_id,
                "chatgpt_plan_type": "plus",
            },
        })
    data = {
        "auth_mode": "chatgpt",
        "tokens": {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "account_id": account_id,
        },
        "last_refresh": datetime.now(timezone.utc).isoformat(),
    }
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


# ── Provider 注册 ──────────────────────────────────────────────────────────


def test_provider_registered_on_init(tmp_path):
    auth = AuthManager(store=AuthStore(tmp_path / "auth.json"))
    assert "openai" in auth.providers
    assert isinstance(auth.providers["openai"], OpenAICodexAuthProvider)


# ── build_authorization_request ────────────────────────────────────────────


def test_build_authorization_request_returns_required_keys():
    provider = OpenAICodexAuthProvider()
    req = provider.build_authorization_request()

    assert "state" in req
    assert "codeVerifier" in req
    assert "authorizeUrl" in req
    assert "redirectUri" in req


def test_build_authorization_request_url_contains_pkce_params():
    provider = OpenAICodexAuthProvider()
    req = provider.build_authorization_request()
    url = req["authorizeUrl"]

    assert "response_type=code" in url
    assert "code_challenge_method=S256" in url
    assert "code_challenge=" in url
    assert "state=" in url


# ── _decode_jwt_payload ────────────────────────────────────────────────────


def test_decode_jwt_payload_extracts_claims():
    token = _make_jwt_payload({"email": "a@b.com", "exp": 9999999999})
    claims = _decode_jwt_payload(token)
    assert claims["email"] == "a@b.com"
    assert claims["exp"] == 9999999999


def test_decode_jwt_payload_handles_invalid_token():
    assert _decode_jwt_payload("not-a-jwt") == {}
    assert _decode_jwt_payload("") == {}


# ── import_codex_auth_json ─────────────────────────────────────────────────


def test_import_codex_auth_json_stores_profile(tmp_path):
    auth_json_path = _make_auth_json(tmp_path)
    auth = AuthManager(store=AuthStore(tmp_path / "profiles.json"))

    profile_id, profile = auth.import_codex_auth_json(path=auth_json_path)

    assert profile_id == "openai:test@example.com"
    assert profile["provider"] == "openai"
    assert profile["type"] == "oauth"
    assert profile["email"] == "test@example.com"
    assert profile["planType"] == "plus"
    assert profile["refreshToken"] == "rt_test"

    stored = auth.store.get_profile(profile_id)
    assert stored is not None
    assert stored["accessToken"] == profile["accessToken"]


def test_import_codex_auth_json_custom_profile_id(tmp_path):
    auth_json_path = _make_auth_json(tmp_path)
    auth = AuthManager(store=AuthStore(tmp_path / "profiles.json"))

    profile_id, _ = auth.import_codex_auth_json(
        path=auth_json_path,
        profile_id="openai:custom",
    )
    assert profile_id == "openai:custom"


def test_import_codex_auth_json_missing_access_token_raises(tmp_path):
    data = {"auth_mode": "chatgpt", "tokens": {"refresh_token": "rt"}, "last_refresh": ""}
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    auth = AuthManager(store=AuthStore(tmp_path / "profiles.json"))
    with pytest.raises(ValueError, match="access_token"):
        auth.import_codex_auth_json(path=str(path))


def test_import_codex_auth_json_missing_tokens_raises(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps({"auth_mode": "chatgpt"}), encoding="utf-8")

    auth = AuthManager(store=AuthStore(tmp_path / "profiles.json"))
    with pytest.raises(ValueError, match="tokens"):
        auth.import_codex_auth_json(path=str(path))


# ── login_oauth ────────────────────────────────────────────────────────────


def test_login_oauth_completes_successfully(tmp_path):
    auth = AuthManager(store=AuthStore(tmp_path / "profiles.json"))

    # 预存一个 profile 让 _profile_from_oauth_result 能找到
    auth.store.upsert_profile("openai:tester@example.com", {
        "provider": "openai",
        "type": "oauth",
        "accessToken": "at",
        "refreshToken": "rt",
        "expiresAt": "2099-01-01T00:00:00Z",
        "email": "tester@example.com",
    })

    mock_session = {
        "loginId": "login-1",
        "authorizeUrl": "https://auth.openai.com/authorize?state=s1",
        "status": "pending",
    }
    completed_session = {
        "loginId": "login-1",
        "status": "completed",
        "resolvedProfileId": "openai:tester@example.com",
    }

    with patch.object(auth.oauth_logins, "start_openai_login", return_value=mock_session), \
         patch.object(auth.oauth_logins, "wait_for_completion", return_value=completed_session), \
         patch("webbrowser.open") as mock_browser:

        profile_id, profile = auth.login_oauth(
            provider="openai",
            method="pkce",
            profile_id=None,
            no_browser=False,
        )

    assert profile_id == "openai:tester@example.com"
    assert profile["type"] == "oauth"
    mock_browser.assert_called_once()


def test_login_oauth_no_browser_skips_open(tmp_path):
    auth = AuthManager(store=AuthStore(tmp_path / "profiles.json"))
    auth.store.upsert_profile("openai:x@y.com", {
        "provider": "openai", "type": "oauth", "accessToken": "at",
    })

    mock_session = {"loginId": "l1", "authorizeUrl": "https://auth.openai.com/authorize"}
    completed = {"loginId": "l1", "status": "completed", "resolvedProfileId": "openai:x@y.com"}

    with patch.object(auth.oauth_logins, "start_openai_login", return_value=mock_session), \
         patch.object(auth.oauth_logins, "wait_for_completion", return_value=completed), \
         patch("webbrowser.open") as mock_browser:

        auth.login_oauth(provider="openai", no_browser=True)

    mock_browser.assert_not_called()


def test_login_oauth_failure_raises(tmp_path):
    auth = AuthManager(store=AuthStore(tmp_path / "profiles.json"))

    mock_session = {"loginId": "l1", "authorizeUrl": "https://auth.openai.com/authorize"}
    failed = {"loginId": "l1", "status": "failed", "error": "user denied"}

    with patch.object(auth.oauth_logins, "start_openai_login", return_value=mock_session), \
         patch.object(auth.oauth_logins, "wait_for_completion", return_value=failed), \
         patch("webbrowser.open"):

        with pytest.raises(RuntimeError, match="user denied"):
            auth.login_oauth(provider="openai")


def test_login_oauth_rejects_unsupported_provider(tmp_path):
    auth = AuthManager(store=AuthStore(tmp_path / "profiles.json"))
    with pytest.raises(ValueError, match="暂不支持"):
        auth.login_oauth(provider="google")


# ── 回归测试：precedence / empty identity ──────────────────────────────────


def test_import_codex_auth_json_malformed_jwt_with_no_profile_id_raises(tmp_path):
    """JWT 解码失败且未指定 profile_id 时应报错，不能写入 'openai:' 键。"""
    data = {
        "auth_mode": "chatgpt",
        "tokens": {
            "access_token": "not-a-jwt",
            "refresh_token": "rt",
        },
        "last_refresh": "",
    }
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    auth = AuthManager(store=AuthStore(tmp_path / "profiles.json"))
    with pytest.raises(ValueError, match="email.*accountId|profile-id"):
        auth.import_codex_auth_json(path=str(path))


def test_import_codex_auth_json_malformed_jwt_with_explicit_profile_id_succeeds(tmp_path):
    """即使 JWT 解码失败，只要指定了 profile_id 就能正常存储。"""
    data = {
        "auth_mode": "chatgpt",
        "tokens": {
            "access_token": "not-a-jwt",
            "refresh_token": "rt",
        },
        "last_refresh": "",
    }
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    auth = AuthManager(store=AuthStore(tmp_path / "profiles.json"))
    profile_id, profile = auth.import_codex_auth_json(
        path=str(path), profile_id="openai:manual"
    )
    assert profile_id == "openai:manual"
    assert auth.store.get_profile("openai:manual") is not None


def test_complete_login_profile_id_wins_over_empty_email():
    """即使 token 中没有 email，显式 profile_id 也应优先使用。"""
    provider = OpenAICodexAuthProvider()
    # 构造一个只有 accountId 没有 email 的 token response
    claims = {
        "exp": int(time.time()) + 86400,
        "https://api.openai.com/profile": {},
        "https://api.openai.com/auth": {
            "chatgpt_account_id": "acct_abc",
            "chatgpt_plan_type": "plus",
        },
    }
    fake_token = _make_jwt_payload(claims)
    fake_response = {
        "access_token": fake_token,
        "refresh_token": "rt_test",
        "expires_in": 86400,
    }

    with patch("src.auth.openai_codex._build_httpx_client") as mock_client_factory:
        mock_resp = MagicMock()
        mock_resp.json.return_value = fake_response
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_resp
        mock_client_factory.return_value = mock_client

        # 显式 profile_id 应优先
        resolved_id, profile = provider.complete_login(
            code="code-1",
            code_verifier="verifier-1",
            profile_id="openai:my-custom-id",
        )
        assert resolved_id == "openai:my-custom-id"


def test_complete_login_no_identity_no_profile_id_raises():
    """token 中无 email/accountId 且未指定 profile_id 时应报错。"""
    provider = OpenAICodexAuthProvider()
    claims = {"exp": int(time.time()) + 86400}
    fake_token = _make_jwt_payload(claims)
    fake_response = {
        "access_token": fake_token,
        "refresh_token": "rt_test",
        "expires_in": 86400,
    }

    with patch("src.auth.openai_codex._build_httpx_client") as mock_client_factory:
        mock_resp = MagicMock()
        mock_resp.json.return_value = fake_response
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_resp
        mock_client_factory.return_value = mock_client

        with pytest.raises(ValueError, match="email.*accountId|profile-id"):
            provider.complete_login(code="c", code_verifier="v")
