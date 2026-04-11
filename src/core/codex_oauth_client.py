"""
Codex OAuth 客户端管理。

直接用 httpx 调 Codex Responses API，token 从 ~/.codex/auth.json 读取。
处理：
- 单例初始化（整个应用共享一个 client 实例）
- SSE 流式响应解析
- Token 过期时自动刷新（refresh_token）
- Auth 失败时的重置
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

from config.settings import (
    AUTH_PROFILES_PATH,
    OPENAI_CODEX_AUTH_TOKEN_URL,
    OPENAI_CODEX_AUTH_CLIENT_ID,
    OPENAI_CODEX_AUTH_PROXY_URL,
)

log = logging.getLogger(__name__)

_client: "CodexOAuthClient | None" = None
_initialized: bool = False

AUTH_JSON_PATH = Path.home() / ".codex" / "auth.json"
API_URL = "https://chatgpt.com/backend-api/codex/responses"


def _read_auth_json_tokens() -> tuple[str, str, str]:
    try:
        auth_data = json.loads(AUTH_JSON_PATH.read_text(encoding="utf-8"))
    except Exception:
        return "", "", ""
    tokens = auth_data.get("tokens", {})
    if not isinstance(tokens, dict):
        return "", "", ""
    return (
        str(tokens.get("access_token", "") or "").strip(),
        str(tokens.get("refresh_token", "") or "").strip(),
        str(tokens.get("account_id", "") or "").strip(),
    )


def _looks_like_test_token(access_token: str, refresh_token: str) -> bool:
    if not access_token:
        return True
    if ".fake-signature" in access_token or access_token.startswith("eyJhbGciOiJub25lIn0."):
        return True
    if refresh_token in {"rt_test", "rt"}:
        return True
    if refresh_token and len(refresh_token) < 16:
        return True
    return False


def _read_lapwing_oauth_tokens() -> tuple[str, str, str]:
    try:
        store = json.loads(Path(AUTH_PROFILES_PATH).read_text(encoding="utf-8"))
    except Exception:
        return "", "", ""

    profiles = store.get("profiles", {})
    if not isinstance(profiles, dict):
        return "", "", ""

    preferred_ids: list[str] = []
    bindings = store.get("bindings", {})
    if isinstance(bindings, dict):
        for key in ("chat", "default"):
            value = bindings.get(key)
            if isinstance(value, str) and value.strip():
                preferred_ids.append(value.strip())

    def _extract(profile_id: str) -> tuple[str, str, str]:
        p = profiles.get(profile_id)
        if not isinstance(p, dict):
            return "", "", ""
        if str(p.get("provider", "")).strip().lower() != "openai":
            return "", "", ""
        if str(p.get("type", "")).strip().lower() != "oauth":
            return "", "", ""
        access = str(p.get("accessToken", "") or "").strip()
        refresh = str(p.get("refreshToken", "") or "").strip()
        account = str(p.get("accountId", "") or "").strip()
        return access, refresh, account

    for pid in preferred_ids:
        access, refresh, account = _extract(pid)
        if access and refresh and not _looks_like_test_token(access, refresh):
            return access, refresh, account

    for pid, p in profiles.items():
        if not isinstance(pid, str):
            continue
        access, refresh, account = _extract(pid)
        if access and refresh and not _looks_like_test_token(access, refresh):
            return access, refresh, account
    return "", "", ""


def _write_auth_json_tokens(access_token: str, refresh_token: str, account_id: str = "") -> None:
    try:
        auth_data = json.loads(AUTH_JSON_PATH.read_text(encoding="utf-8"))
    except Exception:
        auth_data = {}
    tokens = auth_data.get("tokens")
    if not isinstance(tokens, dict):
        tokens = {}
    tokens["access_token"] = access_token
    tokens["refresh_token"] = refresh_token
    if account_id:
        tokens["account_id"] = account_id
    auth_data["tokens"] = tokens
    AUTH_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUTH_JSON_PATH.write_text(
        json.dumps(auth_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _payload_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """仅记录结构摘要，避免日志泄露完整上下文。"""
    input_items = payload.get("input")
    tools = payload.get("tools")
    input_count = len(input_items) if isinstance(input_items, list) else 0
    tools_count = len(tools) if isinstance(tools, list) else 0
    function_call_output_count = 0
    if isinstance(input_items, list):
        function_call_output_count = sum(
            1
            for item in input_items
            if isinstance(item, dict) and item.get("type") == "function_call_output"
        )
    try:
        payload_chars = len(json.dumps(payload, ensure_ascii=False))
    except Exception:
        payload_chars = -1
    return {
        "model": payload.get("model", ""),
        "input_items": input_count,
        "tools": tools_count,
        "function_call_output_items": function_call_output_count,
        "payload_chars": payload_chars,
    }


class CodexOAuthClient:
    """直接用 httpx 调 Codex Responses API 的客户端。"""

    def __init__(self, access_token: str, refresh_token: str, account_id: str = "") -> None:
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._account_id = account_id
        self._refresh_lock = asyncio.Lock()
        self._http = httpx.AsyncClient(
            proxy=OPENAI_CODEX_AUTH_PROXY_URL or None,
            timeout=httpx.Timeout(connect=15.0, read=120.0, write=15.0, pool=15.0),
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def post_stream(self, payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """POST 到 Responses API，yield 解析后的 SSE 事件 dict。

        401 时自动刷新 token 重试一次。
        """
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }
        if self._account_id:
            headers["ChatGPT-Account-ID"] = self._account_id
        payload_meta = _payload_summary(payload)
        log.debug(
            "[codex_oauth] POST %s (token len=%d payload=%s)",
            API_URL,
            len(self._access_token),
            payload_meta,
        )
        async with self._http.stream("POST", API_URL, json=payload, headers=headers) as resp:
            log.debug("[codex_oauth] 响应 status=%d url=%s", resp.status_code, resp.url)
            if resp.status_code == 401:
                await resp.aclose()
                log.warning("[codex_oauth] API 返回 401，尝试刷新 token...")
                await self._do_refresh()
                # 用新 token 重试
                headers["Authorization"] = f"Bearer {self._access_token}"
                log.debug("[codex_oauth] 重试 POST %s (new token len=%d)", API_URL, len(self._access_token))
                async with self._http.stream("POST", API_URL, json=payload, headers=headers) as retry_resp:
                    log.debug("[codex_oauth] 重试响应 status=%d", retry_resp.status_code)
                    if retry_resp.status_code >= 400:
                        detail = (await retry_resp.aread()).decode("utf-8", errors="replace")
                        log.error(
                            "[codex_oauth] 重试失败 status=%d detail=%s payload=%s",
                            retry_resp.status_code,
                            detail[:1200],
                            payload_meta,
                        )
                    retry_resp.raise_for_status()
                    async for event in self._parse_sse(retry_resp):
                        yield event
                return
            if resp.status_code >= 400:
                detail = (await resp.aread()).decode("utf-8", errors="replace")
                log.error(
                    "[codex_oauth] 请求失败 status=%d detail=%s payload=%s",
                    resp.status_code,
                    detail[:1200],
                    payload_meta,
                )
            resp.raise_for_status()
            async for event in self._parse_sse(resp):
                yield event

    @staticmethod
    async def _parse_sse(resp: httpx.Response) -> AsyncIterator[dict[str, Any]]:
        """解析 SSE 流，yield JSON 事件。"""
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            raw = line[6:]
            if raw == "[DONE]":
                break
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                log.warning("[codex_oauth] SSE JSON 解析失败: %r", raw[:200])

    async def _do_refresh(self) -> None:
        """用 refresh_token 刷新 access_token，写回 auth.json。"""
        async with self._refresh_lock:
            log.info("[codex_oauth] 刷新 access_token (refresh_token len=%d)...", len(self._refresh_token))
            async with httpx.AsyncClient(
                proxy=OPENAI_CODEX_AUTH_PROXY_URL or None,
                timeout=15.0,
            ) as refresh_http:
                resp = await refresh_http.post(
                    OPENAI_CODEX_AUTH_TOKEN_URL,
                    data={
                        "grant_type": "refresh_token",
                        "client_id": OPENAI_CODEX_AUTH_CLIENT_ID,
                        "refresh_token": self._refresh_token,
                    },
                )
                log.debug("[codex_oauth] refresh 响应 status=%d url=%s", resp.status_code, resp.url)
                if resp.status_code == 401:
                    # 尝试从磁盘恢复更新过的 token（例如被其他进程刷新过）。
                    disk_access, disk_refresh, disk_account = _read_auth_json_tokens()
                    if (
                        disk_access
                        and disk_access != self._access_token
                        and disk_refresh
                        and not _looks_like_test_token(disk_access, disk_refresh)
                    ):
                        self._access_token = disk_access
                        self._refresh_token = disk_refresh
                        if disk_account:
                            self._account_id = disk_account
                        log.warning("[codex_oauth] refresh 401，已从 auth.json 重载更新 token。")
                        return

                    profile_access, profile_refresh, profile_account = _read_lapwing_oauth_tokens()
                    if (
                        profile_access
                        and profile_access != self._access_token
                        and profile_refresh
                    ):
                        self._access_token = profile_access
                        self._refresh_token = profile_refresh
                        if profile_account:
                            self._account_id = profile_account
                        _write_auth_json_tokens(profile_access, profile_refresh, profile_account)
                        log.warning("[codex_oauth] refresh 401，已从 Lapwing profile 恢复 token。")
                        return

                    log.error(
                        "[codex_oauth] refresh_token 无效（401）。"
                        "请重新登录: python main.py auth login openai-codex"
                    )
                resp.raise_for_status()
                data = resp.json()

            new_access = data.get("access_token", "")
            new_refresh = data.get("refresh_token", self._refresh_token)
            if not new_access:
                raise ValueError("Token 刷新响应中无 access_token")

            self._access_token = new_access
            self._refresh_token = new_refresh

            # 写回 auth.json
            _write_auth_json_tokens(new_access, new_refresh, self._account_id)
            if "id_token" in data:
                try:
                    auth_data = json.loads(AUTH_JSON_PATH.read_text(encoding="utf-8"))
                except Exception:
                    auth_data = {}
                tokens = auth_data.get("tokens")
                if not isinstance(tokens, dict):
                    tokens = {}
                tokens["id_token"] = data["id_token"]
                auth_data["tokens"] = tokens
                AUTH_JSON_PATH.write_text(
                    json.dumps(auth_data, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            log.info("[codex_oauth] Token 已刷新并写回 auth.json")


async def get_client() -> CodexOAuthClient:
    """
    获取 CodexOAuthClient 单例。

    首次调用时从 ~/.codex/auth.json 读取 token 并初始化。
    后续调用返回已有实例。
    """
    global _client, _initialized

    if _initialized and _client is not None:
        return _client

    if not AUTH_JSON_PATH.exists():
        raise FileNotFoundError(
            f"Codex auth 文件不存在: {AUTH_JSON_PATH}\n"
            "请先运行 codex --full-setup 或手动创建。"
        )

    access_token, refresh_token, account_id = _read_auth_json_tokens()
    if _looks_like_test_token(access_token, refresh_token):
        fallback_access, fallback_refresh, fallback_account = _read_lapwing_oauth_tokens()
        if fallback_access and fallback_refresh:
            access_token, refresh_token, account_id = (
                fallback_access,
                fallback_refresh,
                fallback_account,
            )
            _write_auth_json_tokens(access_token, refresh_token, account_id)
            log.warning("[codex_oauth] 检测到 auth.json 为测试/无效 token，已从 Lapwing profile 自动修复。")

    if not access_token:
        raise ValueError(f"auth.json 中 tokens.access_token 为空: {AUTH_JSON_PATH}")

    client = CodexOAuthClient(access_token, refresh_token, account_id)
    _client = client
    _initialized = True
    log.info("[codex_oauth] CodexOAuthClient 已初始化（token 来自 %s）", AUTH_JSON_PATH)
    return _client


async def reset_client() -> None:
    """重置客户端（用于 auth 失败后重新认证）。"""
    global _client, _initialized
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            pass
    _client = None
    _initialized = False
    log.info("[codex_oauth] 客户端已重置，下次调用将重新初始化")


def is_available() -> bool:
    """检查 ~/.codex/auth.json 是否存在且包含 access_token。"""
    try:
        if not AUTH_JSON_PATH.exists():
            return False
        auth_data = json.loads(AUTH_JSON_PATH.read_text(encoding="utf-8"))
        return bool(auth_data.get("tokens", {}).get("access_token"))
    except Exception:
        return False
