"""本地 API auth 测试。"""

from unittest.mock import MagicMock
import json

import httpx
import pytest

from src.api.event_bus import DesktopEventBus
from src.api.server import create_app


class _StubApiSessions:
    cookie_name = "lapwing_session"


class StubAuthManager:
    def __init__(self) -> None:
        self.api_sessions = _StubApiSessions()
        self._bootstrap_token = "bootstrap-token"
        self._valid_sessions: set[str] = set()

    def validate_api_session(self, token: str | None) -> bool:
        return bool(token and token in self._valid_sessions)

    def bootstrap_token(self) -> str:
        return self._bootstrap_token

    def create_api_session(self, bootstrap_token: str) -> str:
        if bootstrap_token != self._bootstrap_token:
            raise ValueError("bootstrap token 无效")
        session_token = "session-token"
        self._valid_sessions.add(session_token)
        return session_token

    def auth_status(self):
        return {
            "profiles": [],
            "bindings": {},
            "serviceAuth": {
                "protected": True,
                "host": "127.0.0.1",
                "cookieName": self.api_sessions.cookie_name,
            },
        }



@pytest.fixture
def protected_brain():
    brain = MagicMock()
    brain.auth_manager = StubAuthManager()
    return brain


@pytest.mark.asyncio
class TestLocalApiAuth:
    async def test_api_rejects_unauthenticated_requests(self, protected_brain):
        app = create_app(protected_brain, DesktopEventBus())
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v2/status")

        assert response.status_code == 401

    async def test_api_session_endpoint_sets_cookie_and_unlocks_followup_requests(self, protected_brain):
        app = create_app(protected_brain, DesktopEventBus())
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            session_response = await client.post(
                "/api/auth/session",
                json={"bootstrap_token": "bootstrap-token"},
            )
            status_response = await client.get("/api/v2/status")

        assert session_response.status_code == 200
        assert "lapwing_session=" in session_response.headers.get("set-cookie", "")
        assert status_response.status_code == 200

    async def test_api_accepts_bootstrap_bearer_for_local_tools(self, protected_brain):
        app = create_app(protected_brain, DesktopEventBus())
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/v2/status",
                headers={"Authorization": "Bearer bootstrap-token"},
            )

        assert response.status_code == 200

    async def test_api_accepts_desktop_bearer_token(self, protected_brain, tmp_path, monkeypatch):
        token_path = tmp_path / "desktop-tokens.json"
        token_path.write_text(json.dumps([{"token": "desktop-token"}]), encoding="utf-8")
        from src.api import desktop_auth
        monkeypatch.setattr(desktop_auth, "DESKTOP_AUTH_TOKENS_PATH", token_path)

        app = create_app(protected_brain, DesktopEventBus())
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/v2/status",
                headers={"Authorization": "Bearer desktop-token"},
            )

        assert response.status_code == 200

    async def test_api_accepts_desktop_query_token(self, protected_brain, tmp_path, monkeypatch):
        token_path = tmp_path / "desktop-tokens.json"
        token_path.write_text(json.dumps([{"token": "desktop-token"}]), encoding="utf-8")
        from src.api import desktop_auth
        monkeypatch.setattr(desktop_auth, "DESKTOP_AUTH_TOKENS_PATH", token_path)

        app = create_app(protected_brain, DesktopEventBus(), mutation_log=None)
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v2/status?token=desktop-token")

        assert response.status_code == 200

    async def test_auth_management_endpoints_require_session_and_return_status(self, protected_brain):
        app = create_app(protected_brain, DesktopEventBus())
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/api/auth/session", json={"bootstrap_token": "bootstrap-token"})
            status_response = await client.get("/api/auth/status")

        assert status_response.status_code == 200
        assert status_response.json()["serviceAuth"]["protected"] is True
