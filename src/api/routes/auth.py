"""Auth 相关 API 端点。"""

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

logger = logging.getLogger("lapwing.api.routes.auth")

router = APIRouter(tags=["auth"])

# 由 server.py init() 注入
_auth_manager = None
_api_session_ttl: int = 0


class ApiSessionRequest(BaseModel):
    bootstrap_token: str | None = None


def init(auth_manager, *, api_session_ttl: int) -> None:
    global _auth_manager, _api_session_ttl
    _auth_manager = auth_manager
    _api_session_ttl = api_session_ttl


@router.post("/api/auth/session")
async def post_api_session(payload: ApiSessionRequest, response: Response, request: Request):
    if _auth_manager is None:
        raise HTTPException(status_code=503, detail="Auth manager not available")

    auth_header = request.headers.get("authorization", "")
    bootstrap_token = payload.bootstrap_token
    if not bootstrap_token and auth_header.lower().startswith("bearer "):
        bootstrap_token = auth_header[7:].strip()
    if not bootstrap_token:
        raise HTTPException(status_code=401, detail="Missing bootstrap token")

    try:
        session_token = _auth_manager.create_api_session(bootstrap_token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    response.set_cookie(
        key=_auth_manager.api_sessions.cookie_name,
        value=session_token,
        httponly=True,
        samesite="strict",
        secure=False,
        max_age=_api_session_ttl,
        path="/",
    )
    return {"success": True}


@router.get("/api/auth/status")
async def get_auth_status():
    if _auth_manager is None:
        raise HTTPException(status_code=503, detail="Auth manager not available")
    return _auth_manager.auth_status()


@router.post("/api/auth/desktop-token")
async def create_desktop_token(request: Request):
    """Generate a long-lived token for the desktop client."""
    import secrets
    from config.settings import API_BOOTSTRAP_TOKEN_PATH, AUTH_DIR
    body = await request.json()
    bootstrap = body.get("bootstrap_token", "")
    if API_BOOTSTRAP_TOKEN_PATH.exists():
        expected = API_BOOTSTRAP_TOKEN_PATH.read_text().strip()
        if bootstrap != expected:
            raise HTTPException(status_code=401, detail="Invalid bootstrap token")
    token = secrets.token_urlsafe(32)
    token_path = AUTH_DIR / "desktop-tokens.json"
    tokens: list = []
    if token_path.exists():
        tokens = json.loads(token_path.read_text(encoding="utf-8"))
    tokens.append({
        "token": token,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "label": body.get("label", "desktop"),
    })
    token_path.write_text(json.dumps(tokens, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"token": token}


@router.get("/api/auth/codex-oauth/status")
async def get_codex_oauth_status():
    """检查 Codex OAuth 认证状态（oauth-codex SDK）。"""
    from src.core.codex_oauth_client import is_available
    if not is_available():
        return {"status": "not_installed", "message": "oauth-codex 未安装"}
    try:
        from src.core.codex_oauth_client import get_client
        await get_client()
        return {"status": "authenticated", "message": "Token 有效"}
    except Exception as exc:
        return {"status": "expired", "message": str(exc)}


@router.post("/api/auth/codex-oauth/reset")
async def post_codex_oauth_reset():
    """重置 Codex OAuth 客户端（强制下次调用重新认证）。"""
    from src.core.codex_oauth_client import reset_client
    await reset_client()
    return {"status": "reset"}
