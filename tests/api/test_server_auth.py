"""本地 API auth 测试。"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

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
        self._oauth_session = {
            "loginId": "login-1",
            "provider": "openai",
            "status": "pending",
            "authorizeUrl": "https://auth.openai.com/oauth/authorize?mock=1",
            "profileIdHint": None,
            "resolvedProfileId": None,
            "error": None,
            "createdAt": "2026-03-27T10:00:00+00:00",
            "updatedAt": "2026-03-27T10:00:00+00:00",
            "completionMessage": None,
            "profile": None,
        }

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

    def import_codex_auth_json(self, *, path: str, profile_id: str | None = None):
        return profile_id or "openai:default", {
            "provider": "openai",
            "type": "oauth",
            "expiresAt": "2026-03-28T00:00:00Z",
        }

    def start_oauth_login(
        self,
        *,
        provider: str,
        method: str,
        profile_id: str | None = None,
        return_to: str | None = None,
    ):
        self._oauth_session["profileIdHint"] = profile_id
        if return_to:
            self._oauth_session["completionMessage"] = f"return_to={return_to}"
        return dict(self._oauth_session)

    def get_oauth_login_session(self, login_id: str):
        if login_id != self._oauth_session["loginId"]:
            raise KeyError(login_id)
        return dict(self._oauth_session)


@pytest.fixture
def protected_brain():
    brain = MagicMock()
    brain.auth_manager = StubAuthManager()
    brain.memory = MagicMock()
    brain.memory.get_all_chat_ids = AsyncMock(return_value=["c1"])
    brain.memory.get_last_interaction = AsyncMock(
        return_value=datetime(2026, 3, 27, 10, 0, tzinfo=timezone.utc)
    )
    return brain


@pytest.mark.asyncio
class TestLocalApiAuth:
    async def test_api_rejects_unauthenticated_requests(self, protected_brain):
        app = create_app(protected_brain, DesktopEventBus())
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/status")

        assert response.status_code == 401

    async def test_api_session_endpoint_sets_cookie_and_unlocks_followup_requests(self, protected_brain):
        app = create_app(protected_brain, DesktopEventBus())
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            session_response = await client.post(
                "/api/auth/session",
                json={"bootstrap_token": "bootstrap-token"},
            )
            status_response = await client.get("/api/status")

        assert session_response.status_code == 200
        assert "lapwing_session=" in session_response.headers.get("set-cookie", "")
        assert status_response.status_code == 200
        assert status_response.json()["online"] is True

    async def test_api_accepts_bootstrap_bearer_for_local_tools(self, protected_brain):
        app = create_app(protected_brain, DesktopEventBus())
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/status",
                headers={"Authorization": "Bearer bootstrap-token"},
            )

        assert response.status_code == 200

    async def test_auth_management_endpoints_require_session_and_return_status(self, protected_brain):
        app = create_app(protected_brain, DesktopEventBus())
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/api/auth/session", json={"bootstrap_token": "bootstrap-token"})
            status_response = await client.get("/api/auth/status")
            import_response = await client.post("/api/auth/import/codex-cache", json={})

        assert status_response.status_code == 200
        assert status_response.json()["serviceAuth"]["protected"] is True
        assert import_response.status_code == 200
        assert import_response.json()["profile_id"] == "openai:default"

    async def test_oauth_panel_endpoints_start_and_query_login_session(self, protected_brain):
        app = create_app(protected_brain, DesktopEventBus())
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/api/auth/session", json={"bootstrap_token": "bootstrap-token"})
            start_response = await client.post(
                "/api/auth/oauth/openai-codex/start",
                json={"return_to": "http://127.0.0.1:1420/"},
            )
            session_response = await client.get("/api/auth/oauth/sessions/login-1")

        assert start_response.status_code == 200
        assert start_response.json()["authorizeUrl"].startswith("https://auth.openai.com/")
        assert session_response.status_code == 200
        assert session_response.json()["status"] == "pending"
