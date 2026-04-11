"""OpenAI Codex OAuth provider — PKCE 流程 + token 刷新。"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from config.settings import (
    OPENAI_CODEX_AUTH_AUTHORIZE_URL,
    OPENAI_CODEX_AUTH_CLIENT_ID,
    OPENAI_CODEX_AUTH_PROXY_URL,
    OPENAI_CODEX_AUTH_REDIRECT_HOST,
    OPENAI_CODEX_AUTH_REDIRECT_PATH,
    OPENAI_CODEX_AUTH_REDIRECT_PORT,
    OPENAI_CODEX_AUTH_TOKEN_URL,
)

logger = logging.getLogger("lapwing.auth.openai_codex")


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    """Base64url-decode JWT payload（不验证签名，仅提取 claims）。"""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1]
        # 补齐 base64 padding
        payload += "=" * (4 - len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception:
        return {}


def _extract_profile_fields(claims: dict[str, Any]) -> dict[str, str]:
    """从 JWT claims 中提取 email / account_id / plan_type。"""
    # OpenAI 把用户信息嵌套在自定义 claim 里
    auth_claim = claims.get("https://api.openai.com/auth", {})
    profile_claim = claims.get("https://api.openai.com/profile", {})
    return {
        "email": str(profile_claim.get("email") or claims.get("email") or ""),
        "accountId": str(auth_claim.get("chatgpt_account_id") or ""),
        "planType": str(auth_claim.get("chatgpt_plan_type") or ""),
    }


_CODEX_AUTH_JSON = Path.home() / ".codex" / "auth.json"


def _sync_to_codex_auth_json(
    access_token: str,
    refresh_token: str,
    id_token: str = "",
    account_id: str = "",
) -> None:
    """将 token 同步到 ~/.codex/auth.json（运行时 codex_oauth 路径的 token 来源）。"""
    try:
        _CODEX_AUTH_JSON.parent.mkdir(parents=True, exist_ok=True)
        existing: dict[str, Any] = {}
        if _CODEX_AUTH_JSON.exists():
            existing = json.loads(_CODEX_AUTH_JSON.read_text(encoding="utf-8"))
        tokens = existing.get("tokens", {})
        tokens["access_token"] = access_token
        tokens["refresh_token"] = refresh_token
        if id_token:
            tokens["id_token"] = id_token
        if account_id:
            tokens["account_id"] = account_id
        existing["tokens"] = tokens
        _CODEX_AUTH_JSON.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("已同步 token 到 %s", _CODEX_AUTH_JSON)
    except Exception as exc:
        logger.warning("同步 token 到 %s 失败: %s", _CODEX_AUTH_JSON, exc)


def _build_httpx_client() -> httpx.Client:
    proxy = OPENAI_CODEX_AUTH_PROXY_URL.strip() or None
    return httpx.Client(proxy=proxy, timeout=30)


class OpenAICodexAuthProvider:
    """实现 OAuthLoginManager 所需的 provider 接口。"""

    provider_name = "openai"
    redirect_host = OPENAI_CODEX_AUTH_REDIRECT_HOST
    redirect_port = OPENAI_CODEX_AUTH_REDIRECT_PORT
    redirect_path = OPENAI_CODEX_AUTH_REDIRECT_PATH

    @property
    def _redirect_uri(self) -> str:
        return f"http://{self.redirect_host}:{self.redirect_port}{self.redirect_path}"

    def build_authorization_request(self) -> dict[str, str]:
        code_verifier = secrets.token_urlsafe(48)
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        state = secrets.token_urlsafe(16)

        params = {
            "response_type": "code",
            "client_id": OPENAI_CODEX_AUTH_CLIENT_ID,
            "redirect_uri": self._redirect_uri,
            "scope": "openid profile email offline_access",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        authorize_url = f"{OPENAI_CODEX_AUTH_AUTHORIZE_URL}?{urlencode(params)}"

        return {
            "state": state,
            "codeVerifier": code_verifier,
            "authorizeUrl": authorize_url,
            "redirectUri": self._redirect_uri,
        }

    def complete_login(
        self,
        *,
        code: str,
        code_verifier: str,
        profile_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """用 authorization code 换取 token 并构建 profile。"""
        with _build_httpx_client() as client:
            resp = client.post(
                OPENAI_CODEX_AUTH_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": code_verifier,
                    "redirect_uri": self._redirect_uri,
                    "client_id": OPENAI_CODEX_AUTH_CLIENT_ID,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        access_token = data["access_token"]
        refresh_token = data.get("refresh_token", "")
        expires_in = int(data.get("expires_in", 86400))
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()

        claims = _decode_jwt_payload(access_token)
        fields = _extract_profile_fields(claims)

        if not profile_id:
            identity = fields["email"] or fields["accountId"]
            if not identity:
                raise ValueError("token 中无法提取 email 或 accountId，请手动指定 --profile-id")
            resolved_id = f"openai:{identity}"
        else:
            resolved_id = profile_id
        profile = {
            "provider": "openai",
            "type": "oauth",
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "expiresAt": expires_at,
            **fields,
        }
        # 同步到 ~/.codex/auth.json，使运行时 codex_oauth 路径也能用
        _sync_to_codex_auth_json(
            access_token=access_token,
            refresh_token=refresh_token,
            id_token=data.get("id_token", ""),
            account_id=fields.get("accountId", ""),
        )
        return resolved_id, profile

    def refresh(self, profile: dict[str, Any]) -> dict[str, Any]:
        """用 refresh_token 刷新 access_token。"""
        refresh_token = profile.get("refreshToken", "")
        if not refresh_token:
            raise ValueError("profile 中缺少 refreshToken")

        with _build_httpx_client() as client:
            resp = client.post(
                OPENAI_CODEX_AUTH_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": OPENAI_CODEX_AUTH_CLIENT_ID,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        new_access = data["access_token"]
        new_refresh = data.get("refresh_token", refresh_token)
        expires_in = int(data.get("expires_in", 86400))
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()

        claims = _decode_jwt_payload(new_access)
        fields = _extract_profile_fields(claims)

        updated = dict(profile)
        updated["accessToken"] = new_access
        updated["refreshToken"] = new_refresh
        updated["expiresAt"] = expires_at
        updated.update(fields)
        # 同步到 ~/.codex/auth.json
        _sync_to_codex_auth_json(
            access_token=new_access,
            refresh_token=new_refresh,
            id_token=data.get("id_token", ""),
            account_id=fields.get("accountId", ""),
        )
        return updated
