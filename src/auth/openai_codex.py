from __future__ import annotations

import base64
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
from queue import Queue
import secrets
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
import webbrowser

import httpx

from config.settings import (
    OPENAI_CODEX_AUTH_AUTHORIZE_URL,
    OPENAI_CODEX_AUTH_CLIENT_ID,
    OPENAI_CODEX_AUTH_ORIGINATOR,
    OPENAI_CODEX_AUTH_PROXY_URL,
    OPENAI_CODEX_AUTH_REDIRECT_HOST,
    OPENAI_CODEX_AUTH_REDIRECT_PATH,
    OPENAI_CODEX_AUTH_REDIRECT_PORT,
    OPENAI_CODEX_AUTH_TOKEN_URL,
)


class OpenAICodexAuthProvider:
    provider_name = "openai"

    def __init__(self) -> None:
        self._redirect_uri = (
            f"http://{OPENAI_CODEX_AUTH_REDIRECT_HOST}:{OPENAI_CODEX_AUTH_REDIRECT_PORT}"
            f"{OPENAI_CODEX_AUTH_REDIRECT_PATH}"
        )

    @property
    def redirect_host(self) -> str:
        return OPENAI_CODEX_AUTH_REDIRECT_HOST

    @property
    def redirect_port(self) -> int:
        return OPENAI_CODEX_AUTH_REDIRECT_PORT

    @property
    def redirect_path(self) -> str:
        return OPENAI_CODEX_AUTH_REDIRECT_PATH

    def build_authorization_request(self) -> dict[str, str]:
        state = _random_token(24)
        verifier = _random_token(64)
        challenge = _code_challenge(verifier)
        return {
            "state": state,
            "codeVerifier": verifier,
            "authorizeUrl": self._authorize_url(state=state, code_challenge=challenge),
            "redirectUri": self._redirect_uri,
        }

    def complete_login(
        self,
        *,
        code: str,
        code_verifier: str,
        profile_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        token_payload = self.exchange_code(code=code, code_verifier=code_verifier)
        resolved_profile_id = profile_id or self.default_profile_id(token_payload)
        return resolved_profile_id, self.profile_from_token_payload(token_payload)

    def login_interactive(self, *, profile_id: str | None = None, no_browser: bool = False) -> tuple[str, dict[str, Any]]:
        request = self.build_authorization_request()
        state = request["state"]
        verifier = request["codeVerifier"]
        authorize_url = request["authorizeUrl"]

        code: str | None = None
        callback_server: _CallbackServer | None = None
        try:
            callback_server = _CallbackServer(
                host=OPENAI_CODEX_AUTH_REDIRECT_HOST,
                port=OPENAI_CODEX_AUTH_REDIRECT_PORT,
                path=OPENAI_CODEX_AUTH_REDIRECT_PATH,
                state=state,
            )
            callback_server.start()
        except OSError:
            callback_server = None

        print("OpenAI/Codex 登录即将开始。")
        print(f"打开以下地址完成授权：\n\n{authorize_url}\n")
        if callback_server is None:
            print("无法绑定 localhost callback。请在浏览器完成登录后，把 redirect URL 或 code 贴回终端。")
        else:
            print(
                "如果你在远端/无头环境，请在本机执行: "
                f"ssh -L {OPENAI_CODEX_AUTH_REDIRECT_PORT}:localhost:{OPENAI_CODEX_AUTH_REDIRECT_PORT} user@remote"
            )

        if not no_browser:
            webbrowser.open(authorize_url)

        if callback_server is not None:
            callback = callback_server.wait(timeout=300)
            if callback is not None:
                code = callback["code"]

        if not code:
            raw = input("请粘贴 redirect URL 或授权 code：\n").strip()
            code = self.parse_callback_input(raw, expected_state=state)

        return self.complete_login(code=code, code_verifier=verifier, profile_id=profile_id)

    def import_auth_json(self, path: str | Path, *, profile_id: str | None = None) -> tuple[str, dict[str, Any]]:
        payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        normalized = self._extract_auth_json_payload(payload)
        resolved_profile_id = profile_id or self.default_profile_id(normalized)
        return resolved_profile_id, self.profile_from_token_payload(normalized)

    def refresh(self, profile: dict[str, Any]) -> dict[str, Any]:
        refresh_token = str(profile.get("refreshToken") or "").strip()
        if not refresh_token:
            raise ValueError("OAuth profile 缺少 refreshToken，无法刷新。")
        payload = self.refresh_token(refresh_token)
        merged = dict(profile)
        merged.update(self.profile_from_token_payload(payload))
        merged["provider"] = self.provider_name
        merged["type"] = "oauth"
        merged.setdefault("method", "pkce")
        return merged

    def exchange_code(self, *, code: str, code_verifier: str) -> dict[str, Any]:
        body = {
            "grant_type": "authorization_code",
            "client_id": OPENAI_CODEX_AUTH_CLIENT_ID,
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": self._redirect_uri,
        }
        return self._post_token(body)

    def refresh_token(self, refresh_token: str) -> dict[str, Any]:
        body = {
            "grant_type": "refresh_token",
            "client_id": OPENAI_CODEX_AUTH_CLIENT_ID,
            "refresh_token": refresh_token,
        }
        return self._post_token(body)

    def profile_from_token_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        id_token = str(payload.get("id_token") or "").strip()
        access_token = str(payload.get("access_token") or "").strip()
        refresh_token = str(payload.get("refresh_token") or "").strip()
        if not access_token or not refresh_token:
            raise ValueError("OpenAI/Codex OAuth 返回缺少 access_token 或 refresh_token。")

        id_claims = decode_jwt_payload(id_token)
        access_claims = decode_jwt_payload(access_token)

        account_id = (
            payload.get("account_id")
            or payload.get("accountId")
            or access_claims.get("chatgpt_account_id")
            or id_claims.get("chatgpt_account_id")
            or access_claims.get("account_id")
            or id_claims.get("account_id")
        )
        email = id_claims.get("email") or access_claims.get("email")
        plan_type = (
            id_claims.get("chatgpt_plan_type")
            or access_claims.get("chatgpt_plan_type")
            or id_claims.get("plan_type")
        )
        workspace_id = (
            id_claims.get("chatgpt_account_id")
            or access_claims.get("chatgpt_account_id")
            or id_claims.get("org_id")
            or access_claims.get("org_id")
        )
        expires_at = _resolve_expires_at(payload, access_claims, id_claims)

        profile = {
            "provider": self.provider_name,
            "type": "oauth",
            "method": "pkce",
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "expiresAt": expires_at,
            "accountId": str(account_id) if account_id else None,
            "email": str(email) if email else None,
            "workspaceId": str(workspace_id) if workspace_id else None,
            "planType": str(plan_type) if plan_type else None,
        }
        return {key: value for key, value in profile.items() if value is not None}

    def default_profile_id(self, payload: dict[str, Any]) -> str:
        profile = self.profile_from_token_payload(payload)
        email = str(profile.get("email") or "").strip().lower()
        if email:
            return f"{self.provider_name}:{email}"
        account_id = str(profile.get("accountId") or "").strip()
        if account_id:
            return f"{self.provider_name}:{account_id}"
        return f"{self.provider_name}:default"

    def parse_callback_input(self, raw: str, *, expected_state: str) -> str:
        raw = raw.strip()
        if not raw:
            raise ValueError("未提供 redirect URL 或 code。")
        if raw.startswith("http://") or raw.startswith("https://"):
            parsed = urlparse(raw)
            params = parse_qs(parsed.query)
            state = str((params.get("state") or [""])[0]).strip()
            if state and state != expected_state:
                raise ValueError("OAuth state 校验失败。")
            code = str((params.get("code") or [""])[0]).strip()
            if not code:
                raise ValueError("redirect URL 中缺少 code。")
            return code
        return raw

    def _authorize_url(self, *, state: str, code_challenge: str) -> str:
        params = {
            "response_type": "code",
            "client_id": OPENAI_CODEX_AUTH_CLIENT_ID,
            "redirect_uri": self._redirect_uri,
            "scope": "openid profile email offline_access api.connectors.read api.connectors.invoke",
            "prompt": "login",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "originator": OPENAI_CODEX_AUTH_ORIGINATOR,
        }
        return f"{OPENAI_CODEX_AUTH_AUTHORIZE_URL}?{urlencode(params)}"

    def _post_token(self, body: dict[str, Any]) -> dict[str, Any]:
        encoded_body = urlencode({key: str(value) for key, value in body.items()})
        client_kwargs: dict[str, Any] = {"timeout": 30}
        proxy_url = OPENAI_CODEX_AUTH_PROXY_URL.strip()
        if proxy_url:
            client_kwargs["proxy"] = proxy_url
        try:
            with httpx.Client(**client_kwargs) as client:
                response = client.post(
                    OPENAI_CODEX_AUTH_TOKEN_URL,
                    content=encoded_body,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                )
        except httpx.HTTPError as exc:
            detail = str(exc).strip()
            if proxy_url:
                detail = f"{detail} (proxy={proxy_url})"
            raise ValueError(f"OpenAI/Codex token 请求失败: {detail or 'network error'}") from exc
        payload: dict[str, Any] | Any
        try:
            payload = response.json()
        except Exception:
            payload = None
        if response.status_code >= 400:
            detail = ""
            if isinstance(payload, dict):
                detail = str(
                    payload.get("error_description")
                    or payload.get("error")
                    or payload.get("message")
                    or ""
                ).strip()
            if not detail:
                detail = response.text.strip()
            raise ValueError(
                f"OpenAI/Codex token endpoint 返回 {response.status_code}: {detail or 'unknown error'}"
            )
        if not isinstance(payload, dict):
            raise ValueError("OpenAI/Codex token endpoint 返回格式异常。")
        return payload

    def _extract_auth_json_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        tokens = payload.get("tokens") if isinstance(payload.get("tokens"), dict) else {}
        normalized = {
            "access_token": payload.get("access_token") or tokens.get("access_token") or payload.get("accessToken"),
            "refresh_token": payload.get("refresh_token") or tokens.get("refresh_token") or payload.get("refreshToken"),
            "id_token": payload.get("id_token") or tokens.get("id_token") or payload.get("idToken"),
            "account_id": payload.get("account_id") or payload.get("accountId"),
        }
        if not normalized["access_token"] or not normalized["refresh_token"]:
            raise ValueError("auth.json 缺少 access_token 或 refresh_token。")
        return normalized


class _CallbackServer:
    def __init__(self, *, host: str, port: int, path: str, state: str) -> None:
        self._host = host
        self._port = port
        self._path = path
        self._state = state
        self._queue: Queue[dict[str, str]] = Queue(maxsize=1)
        self._server = HTTPServer((host, port), self._handler_class())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _handler_class(self):
        queue = self._queue
        path = self._path
        expected_state = self._state

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path != path:
                    self.send_response(404)
                    self.end_headers()
                    return
                params = parse_qs(parsed.query)
                state = str((params.get("state") or [""])[0]).strip()
                code = str((params.get("code") or [""])[0]).strip()
                if expected_state and state != expected_state:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write("state mismatch".encode("utf-8"))
                    return
                if code:
                    try:
                        queue.put_nowait({"code": code, "state": state})
                    except Exception:
                        pass
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    "<html><body><h1>Lapwing login complete.</h1><p>可以返回终端了。</p></body></html>".encode("utf-8")
                )

            def log_message(self, *args, **kwargs):
                return

        return Handler

    def start(self) -> None:
        self._thread.start()

    def wait(self, timeout: int) -> dict[str, str] | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self._queue.empty():
                return self._queue.get_nowait()
            time.sleep(0.1)
        return None


def decode_jwt_payload(token: str) -> dict[str, Any]:
    if not token or token.count(".") < 2:
        return {}
    parts = token.split(".")
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload + padding)
        data = json.loads(decoded.decode("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _resolve_expires_at(payload: dict[str, Any], access_claims: dict[str, Any], id_claims: dict[str, Any]) -> str:
    expires_in = payload.get("expires_in")
    try:
        if expires_in is not None:
            expires_ts = int(time.time()) + int(expires_in)
            return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expires_ts))
    except (TypeError, ValueError):
        pass

    exp = access_claims.get("exp") or id_claims.get("exp")
    try:
        if exp is not None:
            return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(exp)))
    except (TypeError, ValueError):
        pass

    expires_ts = int(time.time()) + 3600
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expires_ts))


def _random_token(size: int) -> str:
    return secrets.token_urlsafe(size)


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
