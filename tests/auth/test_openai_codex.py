"""OpenAI / Codex OAuth provider 测试。"""

from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs, urlparse

import pytest

from src.auth.openai_codex import OpenAICodexAuthProvider
import src.auth.openai_codex as openai_codex_module


def _fake_jwt(payload: dict[str, object]) -> str:
    header = {"alg": "none", "typ": "JWT"}

    def _segment(data: dict[str, object]) -> str:
        raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")

    return f"{_segment(header)}.{_segment(payload)}."


def test_parse_callback_input_accepts_redirect_url_and_validates_state():
    provider = OpenAICodexAuthProvider()

    code = provider.parse_callback_input(
        "http://127.0.0.1:1455/auth/callback?code=test-code&state=expected-state",
        expected_state="expected-state",
    )

    assert code == "test-code"

    with pytest.raises(ValueError, match="state"):
        provider.parse_callback_input(
            "http://127.0.0.1:1455/auth/callback?code=test-code&state=wrong-state",
            expected_state="expected-state",
        )


def test_import_auth_json_extracts_profile_metadata(tmp_path):
    provider = OpenAICodexAuthProvider()
    access_token = _fake_jwt(
        {
            "exp": 4102444800,
            "chatgpt_account_id": "acct_123",
            "chatgpt_plan_type": "plus",
        }
    )
    id_token = _fake_jwt(
        {
            "email": "tester@example.com",
            "chatgpt_account_id": "acct_123",
        }
    )
    auth_json = tmp_path / "auth.json"
    auth_json.write_text(
        json.dumps(
            {
                "access_token": access_token,
                "refresh_token": "refresh-token",
                "id_token": id_token,
                "account_id": "acct_123",
            }
        ),
        encoding="utf-8",
    )

    profile_id, profile = provider.import_auth_json(auth_json)

    assert profile_id == "openai:tester@example.com"
    assert profile["provider"] == "openai"
    assert profile["type"] == "oauth"
    assert profile["accountId"] == "acct_123"
    assert profile["email"] == "tester@example.com"
    assert profile["planType"] == "plus"


def test_authorization_request_uses_codex_compatible_scope_and_originator():
    provider = OpenAICodexAuthProvider()
    request = provider.build_authorization_request()
    parsed = urlparse(request["authorizeUrl"])
    params = parse_qs(parsed.query)

    scope = str((params.get("scope") or [""])[0])
    originator = str((params.get("originator") or [""])[0])
    simplified_flow = str((params.get("codex_cli_simplified_flow") or [""])[0])

    assert "api.connectors.read" in scope
    assert "api.connectors.invoke" in scope
    assert originator == "codex_cli_rs"
    assert simplified_flow == "true"


def test_post_token_uses_configured_proxy(monkeypatch: pytest.MonkeyPatch):
    provider = OpenAICodexAuthProvider()
    monkeypatch.setattr(openai_codex_module, "OPENAI_CODEX_AUTH_PROXY_URL", "http://127.0.0.1:7890")
    captured: dict[str, object] = {}

    class _FakeResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json() -> dict[str, object]:
            return {"ok": True}

    class _FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, *, content, headers):
            captured["url"] = url
            captured["content"] = content
            captured["headers"] = headers
            return _FakeResponse()

    monkeypatch.setattr(openai_codex_module.httpx, "Client", _FakeClient)
    payload = provider._post_token({"grant_type": "refresh_token", "client_id": "x", "refresh_token": "y"})

    assert payload == {"ok": True}
    assert captured["client_kwargs"] == {"timeout": 30, "proxy": "http://127.0.0.1:7890"}
    assert captured["headers"] == {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    assert "grant_type=refresh_token" in str(captured["content"])
