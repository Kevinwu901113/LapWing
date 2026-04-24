from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import html
import logging
from pathlib import Path
import secrets
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlparse
import webbrowser

from config.settings import (
    API_ALLOWED_ORIGINS,
    API_HOST,
    API_BOOTSTRAP_TOKEN_PATH,
    API_SESSION_COOKIE_NAME,
    API_SESSION_TTL_SECONDS,
    AUTH_REFRESH_SKEW_SECONDS,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_PROVIDER,
    LLM_CHAT_API_KEY,
    LLM_CHAT_BASE_URL,
    LLM_CHAT_MODEL,
    LLM_CHAT_PROVIDER,
    LLM_HEARTBEAT_PROVIDER,
    LLM_MODEL,
    LLM_TOOL_API_KEY,
    LLM_TOOL_BASE_URL,
    LLM_TOOL_MODEL,
    LLM_TOOL_PROVIDER,
    NIM_API_KEY,
    NIM_BASE_URL,
    NIM_MODEL,
)
from src.auth.models import FailureKind, PurposeConfig, ResolvedAuthCandidate
from src.auth.openai_codex import OpenAICodexAuthProvider, _decode_jwt_payload
from src.auth.resolver import resolve_secret_ref
from src.auth.storage import AuthStore
from src.core.time_utils import now_iso, parse_iso_datetime

logger = logging.getLogger("lapwing.auth.service")

_OAUTH_LOGIN_TTL_SECONDS = 10 * 60


@dataclass(frozen=True)
class ServiceAuthStatus:
    host: str
    protected: bool
    cookie_name: str


@dataclass
class PendingOAuthLogin:
    login_id: str
    provider: str
    state: str
    code_verifier: str
    authorize_url: str
    profile_id_hint: str | None
    return_to: str | None
    created_at: str
    updated_at: str
    expires_at_ts: float
    status: str = "pending"
    resolved_profile_id: str | None = None
    error: str | None = None
    profile_summary: dict[str, Any] | None = None
    completion_message: str | None = None
    wait_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def snapshot(self) -> dict[str, Any]:
        return {
            "loginId": self.login_id,
            "provider": self.provider,
            "status": self.status,
            "authorizeUrl": self.authorize_url,
            "profileIdHint": self.profile_id_hint,
            "resolvedProfileId": self.resolved_profile_id,
            "error": self.error,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "completionMessage": self.completion_message,
            "profile": self.profile_summary,
        }


class ApiSessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, float] = {}
        self._bootstrap_token: str | None = None

    @property
    def cookie_name(self) -> str:
        return API_SESSION_COOKIE_NAME

    def bootstrap_token(self) -> str:
        if self._bootstrap_token is not None:
            return self._bootstrap_token
        token_path = API_BOOTSTRAP_TOKEN_PATH
        token_path.parent.mkdir(parents=True, exist_ok=True)
        if token_path.exists():
            token = token_path.read_text(encoding="utf-8").strip()
            if token:
                self._bootstrap_token = token
                return token
        token = secrets.token_urlsafe(32)
        token_path.write_text(token + "\n", encoding="utf-8")
        token_path.chmod(0o600)
        self._bootstrap_token = token
        return token

    def create_session(self, bootstrap_token: str) -> str:
        if bootstrap_token != self.bootstrap_token():
            raise ValueError("bootstrap token 无效")
        token = secrets.token_urlsafe(32)
        self._sessions[token] = time.time() + API_SESSION_TTL_SECONDS
        return token

    def validate_session(self, token: str | None) -> bool:
        if not token:
            return False
        expires_at = self._sessions.get(token)
        if not expires_at:
            return False
        if expires_at <= time.time():
            self._sessions.pop(token, None)
            return False
        return True


class OAuthLoginManager:
    def __init__(self, auth_manager: AuthManager) -> None:
        self._auth_manager = auth_manager
        self._lock = threading.Lock()
        self._sessions_by_id: dict[str, PendingOAuthLogin] = {}
        self._sessions_by_state: dict[str, PendingOAuthLogin] = {}
        self._callback_server: _OpenAICallbackServer | None = None

    def start_openai_login(
        self,
        *,
        profile_id: str | None = None,
        return_to: str | None = None,
    ) -> dict[str, Any]:
        provider = self._auth_manager._provider("openai")
        request = provider.build_authorization_request()
        session = PendingOAuthLogin(
            login_id=secrets.token_urlsafe(16),
            provider="openai",
            state=str(request["state"]),
            code_verifier=str(request["codeVerifier"]),
            authorize_url=str(request["authorizeUrl"]),
            profile_id_hint=profile_id,
            return_to=self._sanitize_return_to(return_to),
            created_at=now_iso(),
            updated_at=now_iso(),
            expires_at_ts=time.time() + _OAUTH_LOGIN_TTL_SECONDS,
        )
        with self._lock:
            self._purge_expired_locked()
            self._ensure_callback_server_locked(provider)
            self._sessions_by_id[session.login_id] = session
            self._sessions_by_state[session.state] = session
        return session.snapshot()

    def get_session(self, login_id: str) -> dict[str, Any]:
        with self._lock:
            self._purge_expired_locked()
            session = self._sessions_by_id.get(login_id)
            if session is None:
                raise KeyError(login_id)
            return session.snapshot()

    def wait_for_completion(self, login_id: str, timeout: int = 300) -> dict[str, Any]:
        with self._lock:
            session = self._sessions_by_id.get(login_id)
            if session is None:
                raise KeyError(login_id)
            wait_event = session.wait_event
        wait_event.wait(timeout=timeout)
        return self.get_session(login_id)

    def session_state(self, login_id: str) -> str:
        with self._lock:
            session = self._sessions_by_id.get(login_id)
            if session is None:
                raise KeyError(login_id)
            return session.state

    def complete_login_code(self, login_id: str, code: str) -> dict[str, Any]:
        with self._lock:
            self._purge_expired_locked()
            session = self._sessions_by_id.get(login_id)
            if session is None:
                raise KeyError(login_id)
            if session.status not in {"pending", "completing"}:
                return session.snapshot()
        return self._complete_successful_login(session, code)

    def callback_response(
        self,
        *,
        provider_name: str,
        state: str,
        code: str | None,
        error: str | None = None,
        error_description: str | None = None,
    ) -> tuple[int, str]:
        with self._lock:
            self._purge_expired_locked()
            session = self._sessions_by_state.get(state)
            if session is None:
                return 400, self._render_callback_page(
                    title="Lapwing 登录失败",
                    message="这次登录会话不存在或已过期，请回到面板重新发起。",
                )
            if session.status == "completed":
                return 200, self._render_session_page(session)
            if session.status == "failed":
                return 400, self._render_session_page(session)
            session.status = "completing"
            session.updated_at = now_iso()

        if error:
            message = error_description or error
            self._mark_session_failed(session.login_id, message)
            return 400, self._render_session_page_by_id(session.login_id)
        if not code:
            self._mark_session_failed(session.login_id, "缺少 OAuth code。")
            return 400, self._render_session_page_by_id(session.login_id)

        self._complete_successful_login(session, code)
        return 200, self._render_session_page_by_id(session.login_id)

    def _complete_successful_login(self, session: PendingOAuthLogin, code: str) -> dict[str, Any]:
        provider = self._auth_manager._provider(session.provider)
        try:
            resolved_profile_id, profile = provider.complete_login(
                code=code,
                code_verifier=session.code_verifier,
                profile_id=session.profile_id_hint,
            )
            self._auth_manager.store.upsert_profile(resolved_profile_id, profile)
            profile_summary = self._auth_manager.store.list_profiles(session.provider)
            resolved_summary = next(
                (item for item in profile_summary if item["profileId"] == resolved_profile_id),
                None,
            )
            with self._lock:
                current = self._sessions_by_id.get(session.login_id)
                if current is None:
                    raise KeyError(session.login_id)
                current.status = "completed"
                current.updated_at = now_iso()
                current.resolved_profile_id = resolved_profile_id
                current.profile_summary = resolved_summary
                current.completion_message = "OpenAI 登录成功，profile 已写入 Lapwing auth store。"
                current.error = None
                current.wait_event.set()
                self._maybe_shutdown_callback_server_locked()
                return current.snapshot()
        except Exception as exc:
            logger.warning("OpenAI OAuth 登录完成失败: %s", exc)
            self._mark_session_failed(session.login_id, str(exc))
            return self.get_session(session.login_id)

    def _mark_session_failed(self, login_id: str, message: str) -> None:
        with self._lock:
            session = self._sessions_by_id.get(login_id)
            if session is None:
                return
            session.status = "failed"
            session.updated_at = now_iso()
            session.error = message
            session.completion_message = "OpenAI 登录失败。"
            session.wait_event.set()
            self._maybe_shutdown_callback_server_locked()

    def _render_session_page_by_id(self, login_id: str) -> str:
        with self._lock:
            session = self._sessions_by_id.get(login_id)
            if session is None:
                return self._render_callback_page(
                    title="Lapwing 登录结束",
                    message="这次登录会话已结束，请回到面板查看最新状态。",
                )
            return self._render_session_page(session)

    def _render_session_page(self, session: PendingOAuthLogin) -> str:
        if session.status == "completed":
            title = "Lapwing 登录成功"
            message = session.completion_message or "OpenAI 登录成功，请回到 Lapwing 面板继续。"
            return_to = session.return_to
        else:
            title = "Lapwing 登录失败"
            message = session.error or session.completion_message or "OpenAI 登录没有完成。"
            return_to = session.return_to
        return self._render_callback_page(title=title, message=message, return_to=return_to)

    def _render_callback_page(self, *, title: str, message: str, return_to: str | None = None) -> str:
        safe_title = html.escape(title)
        safe_message = html.escape(message)
        safe_return_to = html.escape(return_to) if return_to else None
        return_link = (
            f'<p><a href="{safe_return_to}">返回 Lapwing 面板</a></p>'
            if safe_return_to else "<p>你现在可以回到 Lapwing 面板了。</p>"
        )
        redirect_script = (
            f'<script>setTimeout(function(){{window.location.href={json_string(return_to)};}}, 1200);</script>'
            if return_to else ""
        )
        return (
            "<html><body style=\"font-family: sans-serif; padding: 24px;\">"
            f"<h1>{safe_title}</h1>"
            f"<p>{safe_message}</p>"
            f"{return_link}"
            f"{redirect_script}"
            "</body></html>"
        )

    def _sanitize_return_to(self, return_to: str | None) -> str | None:
        if not return_to:
            return None
        parsed = urlparse(return_to)
        if parsed.scheme not in {"http", "https"}:
            return None
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in API_ALLOWED_ORIGINS:
            return None
        return return_to

    def _purge_expired_locked(self) -> None:
        now = time.time()
        expired_login_ids = [
            login_id
            for login_id, session in self._sessions_by_id.items()
            if session.expires_at_ts <= now and session.status in {"pending", "completing"}
        ]
        for login_id in expired_login_ids:
            session = self._sessions_by_id.get(login_id)
            if session is None:
                continue
            session.status = "expired"
            session.updated_at = now_iso()
            session.error = "OAuth 登录已超时，请重新发起。"
            session.completion_message = "OpenAI 登录已超时。"
            session.wait_event.set()
            self._sessions_by_state.pop(session.state, None)
        self._maybe_shutdown_callback_server_locked()

    def _ensure_callback_server_locked(self, provider: OpenAICodexAuthProvider) -> None:
        if self._callback_server is not None:
            return
        self._callback_server = _OpenAICallbackServer(self, provider)
        self._callback_server.start()

    def _maybe_shutdown_callback_server_locked(self) -> None:
        if self._callback_server is None:
            return
        has_pending = any(
            session.status in {"pending", "completing"}
            for session in self._sessions_by_id.values()
        )
        if has_pending:
            return
        server = self._callback_server
        self._callback_server = None
        server.shutdown()


class _OpenAICallbackServer:
    def __init__(self, login_manager: OAuthLoginManager, provider: OpenAICodexAuthProvider) -> None:
        self._login_manager = login_manager
        self._provider = provider
        self._server = _ReusableThreadingHTTPServer(
            (provider.redirect_host, provider.redirect_port),
            self._handler_class(),
        )
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _handler_class(self):
        login_manager = self._login_manager
        provider = self._provider

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path != provider.redirect_path:
                    self.send_response(404)
                    self.end_headers()
                    return
                params = parse_qs(parsed.query)
                state = str((params.get("state") or [""])[0]).strip()
                code = str((params.get("code") or [""])[0]).strip() or None
                error = str((params.get("error") or [""])[0]).strip() or None
                error_description = str((params.get("error_description") or [""])[0]).strip() or None
                status_code, body = login_manager.callback_response(
                    provider_name=provider.provider_name,
                    state=state,
                    code=code,
                    error=error,
                    error_description=error_description,
                )
                payload = body.encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args, **kwargs):
                return

        return Handler

    def start(self) -> None:
        self._thread.start()

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=1)


class _ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


class AuthManager:
    def __init__(self, store: AuthStore | None = None) -> None:
        self.store = store or AuthStore()
        self.store.ensure_exists()
        self.providers: dict[str, Any] = {"openai": OpenAICodexAuthProvider()}
        self.api_sessions = ApiSessionManager()
        # Eagerly materialize the bootstrap token so remote/browser access has a stable file to read.
        self.api_sessions.bootstrap_token()
        self.oauth_logins = OAuthLoginManager(self)
        self._session_pins: dict[tuple[str, str], str] = {}
        self._provider_mismatch_warnings: set[tuple[str, str, str, str]] = set()
        self._purpose_configs = self._build_purpose_configs()
        # Slot-level config overrides (populated by LLMRouter._setup_routing)
        self._slot_configs: dict[str, PurposeConfig] = {}
        self._slot_api_keys: dict[str, str] = {}

    def register_slot_config(
        self,
        slot_id: str,
        purpose: str,
        *,
        base_url: str,
        model: str,
        api_type: str,
        api_key: str | None = None,
        provider_id: str | None = None,
    ) -> None:
        """Register a slot-level routing config.

        Called by LLMRouter._setup_routing() so that each slot
        (e.g. 'lightweight_judgment') can have its own provider/model
        even when multiple slots share the same purpose ('tool').
        """
        inferred_provider = provider_id or _infer_provider_from_route(base_url, model)
        self._slot_configs[slot_id] = PurposeConfig(
            purpose=purpose,
            base_url=base_url,
            model=model,
            api_type=api_type,
            source="model_routing_config",
            provider=inferred_provider or None,
        )
        if api_key:
            self._slot_api_keys[slot_id] = api_key

    def list_profiles(self, provider: str | None = None) -> list[dict[str, Any]]:
        return self.store.list_profiles(provider)

    def set_api_key_profile(
        self,
        *,
        provider: str,
        profile_id: str | None = None,
        literal: str | None = None,
        env_name: str | None = None,
        command: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        choices = [bool(literal), bool(env_name), bool(command)]
        if sum(1 for value in choices if value) != 1:
            raise ValueError("必须且只能选择 literal/env/command 三者之一。")
        if literal is not None:
            secret_ref = {"kind": "literal", "value": literal}
        elif env_name is not None:
            secret_ref = {"kind": "env", "name": env_name}
        else:
            secret_ref = {"kind": "command", "command": command}
        resolved_profile_id = profile_id or f"{provider}:default"
        profile = {
            "provider": provider,
            "type": "api_key",
            "secretRef": secret_ref,
        }
        self.store.upsert_profile(resolved_profile_id, profile)
        return resolved_profile_id, profile

    def set_api_key(
        self,
        *,
        provider: str,
        profile_id: str | None = None,
        literal: str | None = None,
        env_name: str | None = None,
        command: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        return self.set_api_key_profile(
            provider=provider,
            profile_id=profile_id,
            literal=literal,
            env_name=env_name,
            command=command,
        )

    def bind_profile(self, *, purpose: str, profile_id: str) -> str:
        return self.store.set_binding(purpose, profile_id)

    def unbind_profile(self, *, purpose: str) -> bool:
        return self.store.clear_binding(purpose)

    def get_token(self, *, provider: str, profile_id: str) -> str:
        profile = self.store.get_profile(profile_id)
        if not profile:
            raise ValueError(f"auth profile 不存在: {profile_id}")
        if str(profile.get("provider") or "") != provider:
            raise ValueError(f"profile `{profile_id}` 不属于 provider `{provider}`")
        return self._resolve_profile(profile_id, profile)

    def start_oauth_login(
        self,
        *,
        provider: str,
        method: str = "pkce",
        profile_id: str | None = None,
        return_to: str | None = None,
    ) -> dict[str, Any]:
        if provider != "openai":
            raise ValueError(f"暂不支持的 OAuth provider: {provider}")
        if method != "pkce":
            raise ValueError(f"暂不支持的 OAuth method: {method}")
        return self.oauth_logins.start_openai_login(profile_id=profile_id, return_to=return_to)

    def get_oauth_login_session(self, login_id: str) -> dict[str, Any]:
        return self.oauth_logins.get_session(login_id)

    def login_oauth(
        self,
        provider: str = "openai",
        method: str = "pkce",
        profile_id: str | None = None,
        no_browser: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        """CLI 同步 OAuth 登录：启动回调服务器 → 打开浏览器 → 等待完成。"""
        if provider != "openai":
            raise ValueError(f"暂不支持的 OAuth provider: {provider}")
        if method != "pkce":
            raise ValueError(f"暂不支持的 OAuth method: {method}")

        session = self.oauth_logins.start_openai_login(profile_id=profile_id)
        authorize_url = session.get("authorizeUrl", "")

        print(f"\n请在浏览器中完成登录:\n  {authorize_url}\n")
        if not no_browser:
            webbrowser.open(authorize_url)

        print("等待浏览器回调...")
        completed = self.oauth_logins.wait_for_completion(session["loginId"], timeout=300)

        if completed.get("status") == "completed":
            return self._profile_from_oauth_result(completed)

        error = completed.get("error") or "OAuth 登录失败或超时"
        raise RuntimeError(error)

    def import_codex_auth_json(
        self,
        path: str = "~/.codex/auth.json",
        profile_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """从 Codex CLI 的 auth.json 导入 OAuth token。"""
        import json as _json

        file_path = Path(path).expanduser().resolve()
        data = _json.loads(file_path.read_text(encoding="utf-8"))

        tokens = data.get("tokens")
        if not isinstance(tokens, dict):
            raise ValueError(f"auth.json 格式无法识别: 缺少 tokens 字段")

        access_token = tokens.get("access_token", "")
        if not access_token:
            raise ValueError("auth.json 中未找到 access_token")

        refresh_token = tokens.get("refresh_token", "")
        account_id = tokens.get("account_id", "")

        # 从 JWT 中提取用户信息和过期时间
        claims = _decode_jwt_payload(access_token)
        auth_claim = claims.get("https://api.openai.com/auth", {})
        profile_claim = claims.get("https://api.openai.com/profile", {})

        email = str(profile_claim.get("email") or claims.get("email") or "")
        chatgpt_account_id = str(auth_claim.get("chatgpt_account_id") or account_id or "")
        plan_type = str(auth_claim.get("chatgpt_plan_type") or "")

        exp = claims.get("exp")
        if exp:
            expires_at = datetime.fromtimestamp(int(exp), tz=timezone.utc).isoformat()
        else:
            expires_at = ""

        if not profile_id:
            identity = email or chatgpt_account_id
            if not identity:
                raise ValueError("auth.json 中无法提取 email 或 accountId，请手动指定 --profile-id")
            resolved_id = f"openai:{identity}"
        else:
            resolved_id = profile_id
        profile = {
            "provider": "openai",
            "type": "oauth",
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "expiresAt": expires_at,
            "email": email,
            "accountId": chatgpt_account_id,
            "planType": plan_type,
        }
        self.store.upsert_profile(resolved_id, profile)
        return resolved_id, profile

    def auth_status(self) -> dict[str, Any]:
        return {
            "profiles": self.list_profiles(),
            "bindings": self.store.read().get("bindings", {}),
            "routes": self.route_status(),
            "serviceAuth": {
                "protected": True,
                "host": API_HOST,
                "cookieName": self.api_sessions.cookie_name,
            },
        }

    def route_status(self) -> dict[str, dict[str, Any]]:
        store_data = self.store.read()
        routes: dict[str, dict[str, Any]] = {}
        for purpose, config in self._purpose_configs.items():
            binding_purpose, binding_profile_id = self._resolve_binding(purpose)
            binding_provider: str | None = None
            if binding_profile_id:
                binding_profile = store_data["profiles"].get(binding_profile_id)
                if isinstance(binding_profile, dict):
                    provider_raw = str(binding_profile.get("provider") or "").strip().lower()
                    binding_provider = provider_raw or None
            expected_provider = (config.provider or "").strip().lower() or None
            binding_mismatch = bool(
                expected_provider
                and binding_provider
                and binding_provider != expected_provider
            )
            routes[purpose] = {
                "provider": expected_provider,
                "baseUrl": config.base_url,
                "model": config.model,
                "apiType": config.api_type,
                "source": config.source,
                "bindingPurpose": binding_purpose,
                "bindingProfileId": binding_profile_id,
                "bindingProvider": binding_provider,
                "bindingMismatch": binding_mismatch,
            }
        return routes

    def resolve_candidates(
        self,
        *,
        purpose: str,
        slot: str | None = None,
        session_key: str | None = None,
        allow_failover: bool = True,
        exclude_profiles: set[str] | None = None,
        origin: str | None = None,
    ) -> list[ResolvedAuthCandidate]:
        exclude_profiles = exclude_profiles or set()
        # Use slot-level config if available (allows different providers per slot)
        if slot and slot in self._slot_configs:
            purpose_config = self._slot_configs[slot]
        else:
            purpose_config = self._purpose_configs[purpose]
        binding_purpose, binding_profile_id = self._resolve_binding(purpose)
        if binding_profile_id:
            store_data = self.store.read()
            profile = store_data["profiles"].get(binding_profile_id)
            if profile is None:
                raise ValueError(f"绑定的 auth profile 不存在: {binding_profile_id}")
            provider_name = str(profile.get("provider") or "").strip()
            expected_provider = str(purpose_config.provider or "").strip().lower()
            bound_provider = provider_name.lower()
            provider_mismatch = bool(
                expected_provider
                and bound_provider
                and bound_provider != expected_provider
            )
            if provider_mismatch:
                warning_key = (
                    purpose,
                    binding_profile_id,
                    bound_provider,
                    expected_provider,
                )
                if warning_key not in self._provider_mismatch_warnings:
                    self._provider_mismatch_warnings.add(warning_key)
                    logger.warning(
                        "[%s] binding `%s` 的 provider=%s 与路由 provider=%s 不一致，跳过该 binding 并回退到 .env。",
                        purpose,
                        binding_profile_id,
                        bound_provider,
                        expected_provider,
                    )
            else:
                preferred = self._session_pins.get((purpose, session_key)) if session_key else None
                ordered = self.store.ordered_profiles(
                    provider_name,
                    preferred_profile_id=preferred if allow_failover else binding_profile_id,
                    include_unavailable=False,
                )
                if binding_profile_id in ordered:
                    ordered.remove(binding_profile_id)
                ordered.insert(0, binding_profile_id)
                if preferred and preferred in ordered:
                    ordered.remove(preferred)
                    ordered.insert(0, preferred)
                if not allow_failover:
                    ordered = ordered[:1]

                candidates: list[ResolvedAuthCandidate] = []
                for current_profile_id in ordered:
                    if current_profile_id in exclude_profiles:
                        continue
                    profile_payload = store_data["profiles"].get(current_profile_id)
                    if not isinstance(profile_payload, dict):
                        continue
                    try:
                        hydrated = self._resolve_profile(current_profile_id, profile_payload)
                    except Exception as exc:
                        logger.warning("解析 auth profile %s 失败: %s", current_profile_id, exc)
                        continue
                    candidates.append(
                        ResolvedAuthCandidate(
                            purpose=purpose,
                            base_url=purpose_config.base_url,
                            model=purpose_config.model,
                            api_type=purpose_config.api_type,
                            auth_value=hydrated,
                            auth_kind=str(profile_payload.get("type") or ""),
                            source="auth_profile",
                            provider=provider_name,
                            profile_id=current_profile_id,
                            profile_type=str(profile_payload.get("type") or ""),
                            binding_purpose=binding_purpose,
                            session_key=session_key,
                            metadata=self._candidate_metadata(
                                profile=profile_payload,
                                origin=origin,
                            ),
                        )
                    )
                if candidates:
                    return candidates

        return [
            ResolvedAuthCandidate(
                purpose=purpose,
                base_url=purpose_config.base_url,
                model=purpose_config.model,
                api_type=purpose_config.api_type,
                auth_value=self._env_api_key_for_purpose(purpose, slot=slot),
                auth_kind="env",
                source="env_fallback",
                session_key=session_key,
                metadata=self._candidate_metadata(origin=origin),
            )
        ]

    def mark_success(self, candidate: ResolvedAuthCandidate) -> None:
        if candidate.profile_id:
            self.store.mark_success(candidate.profile_id)
            if candidate.session_key:
                self._session_pins[(candidate.purpose, candidate.session_key)] = candidate.profile_id

    def mark_failure(self, candidate: ResolvedAuthCandidate, kind: FailureKind) -> None:
        if candidate.profile_id:
            self.store.mark_failure(candidate.profile_id, kind)

    def refresh_candidate(self, candidate: ResolvedAuthCandidate) -> ResolvedAuthCandidate:
        if not candidate.profile_id:
            return candidate
        profile = self.store.get_profile(candidate.profile_id)
        if not profile:
            raise ValueError(f"auth profile 不存在: {candidate.profile_id}")
        if profile.get("type") != "oauth":
            return candidate
        provider_name = str(profile.get("provider") or "")
        provider = self._provider(provider_name)
        refreshed = provider.refresh(profile)
        self.store.upsert_profile(candidate.profile_id, refreshed)
        refreshed_metadata = dict(candidate.metadata)
        refreshed_metadata.update(self._candidate_metadata(profile=refreshed))
        return ResolvedAuthCandidate(
            purpose=candidate.purpose,
            base_url=candidate.base_url,
            model=candidate.model,
            api_type=candidate.api_type,
            auth_value=str(refreshed.get("accessToken") or ""),
            auth_kind="oauth",
            source=candidate.source,
            provider=candidate.provider,
            profile_id=candidate.profile_id,
            profile_type="oauth",
            binding_purpose=candidate.binding_purpose,
            session_key=candidate.session_key,
            metadata=refreshed_metadata,
        )

    def bootstrap_token(self) -> str:
        return self.api_sessions.bootstrap_token()

    def create_api_session(self, bootstrap_token: str) -> str:
        return self.api_sessions.create_session(bootstrap_token)

    def validate_api_session(self, token: str | None) -> bool:
        return self.api_sessions.validate_session(token)

    def _resolve_binding(self, purpose: str) -> tuple[str | None, str | None]:
        bound = self.store.get_binding(purpose)
        if bound:
            return purpose, bound
        default_bound = self.store.get_binding("default")
        if default_bound:
            return "default", default_bound
        return None, None

    def _resolve_profile(self, profile_id: str, profile: dict[str, Any]) -> str:
        profile_type = str(profile.get("type") or "")
        if profile_type == "api_key":
            secret_ref = dict(profile.get("secretRef") or {})
            return resolve_secret_ref(secret_ref)
        if profile_type != "oauth":
            raise ValueError(f"未知 profile type: {profile_type}")

        expires_at = parse_iso_datetime(profile.get("expiresAt"))
        refresh_deadline = datetime.now(timezone.utc) + timedelta(seconds=AUTH_REFRESH_SKEW_SECONDS)
        if expires_at is not None and expires_at <= refresh_deadline:
            provider_name = str(profile.get("provider") or "")
            provider = self._provider(provider_name)
            refreshed = provider.refresh(profile)
            self.store.upsert_profile(profile_id, refreshed)
            profile = refreshed
        access_token = str(profile.get("accessToken") or "").strip()
        if not access_token:
            raise ValueError("OAuth profile 缺少 accessToken")
        return access_token

    def _build_purpose_configs(self) -> dict[str, PurposeConfig]:
        return {
            "chat": self._purpose_config(
                purpose="chat",
                purpose_api_key=LLM_CHAT_API_KEY,
                purpose_base_url=LLM_CHAT_BASE_URL,
                purpose_model=LLM_CHAT_MODEL,
                purpose_provider=LLM_CHAT_PROVIDER,
            ),
            "tool": self._purpose_config(
                purpose="tool",
                purpose_api_key=LLM_TOOL_API_KEY,
                purpose_base_url=LLM_TOOL_BASE_URL,
                purpose_model=LLM_TOOL_MODEL,
                purpose_provider=LLM_TOOL_PROVIDER,
            ),
            "heartbeat": self._purpose_config(
                purpose="heartbeat",
                purpose_api_key=NIM_API_KEY,
                purpose_base_url=NIM_BASE_URL,
                purpose_model=NIM_MODEL,
                purpose_provider=LLM_HEARTBEAT_PROVIDER,
            ),
        }

    def _purpose_config(
        self,
        *,
        purpose: str,
        purpose_api_key: str,
        purpose_base_url: str,
        purpose_model: str,
        purpose_provider: str,
    ) -> PurposeConfig:
        use_purpose_route = bool(purpose_base_url and purpose_model)
        # heartbeat 默认提供了 NIM base/model；仍要求显式 key 才走该路由，
        # 避免在未配置 NIM key 时错误使用通用 provider 凭证请求 NIM。
        if purpose == "heartbeat":
            use_purpose_route = bool(purpose_api_key and purpose_base_url and purpose_model)

        if use_purpose_route:
            base_url = purpose_base_url
            model = purpose_model
            source = "purpose_env"
        else:
            base_url = LLM_BASE_URL
            model = LLM_MODEL
            source = "generic_env"
        configured_provider = (
            purpose_provider if use_purpose_route else LLM_PROVIDER
        ).strip().lower()
        inferred_provider = _infer_provider_from_route(base_url, model)
        provider = configured_provider or inferred_provider
        api_type = "anthropic" if "/anthropic" in base_url.lower() else "openai"
        return PurposeConfig(
            purpose=purpose,
            base_url=base_url,
            model=model,
            api_type=api_type,
            source=source,
            provider=provider or None,
        )

    def _env_api_key_for_purpose(self, purpose: str, slot: str | None = None) -> str:
        # Check slot-level api_key override first (from model_routing.json)
        if slot:
            slot_key = self._slot_api_keys.get(slot, "").strip()
            if slot_key:
                return slot_key
        mapping = {
            "chat": LLM_CHAT_API_KEY or LLM_API_KEY,
            "tool": LLM_TOOL_API_KEY or LLM_API_KEY,
            "heartbeat": NIM_API_KEY or LLM_API_KEY,
        }
        value = str(mapping.get(purpose) or "").strip()
        if not value:
            raise ValueError(f"purpose `{purpose}` 缺少可用 API key")
        return value

    def _provider(self, provider_name: str) -> Any:
        provider = self.providers.get(provider_name)
        if provider is not None:
            return provider
        raise ValueError(f"未实现 OAuth provider: {provider_name}")

    def _candidate_metadata(
        self,
        *,
        profile: dict[str, Any] | None = None,
        origin: str | None = None,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        if origin:
            metadata["origin"] = origin
        if not isinstance(profile, dict):
            return metadata

        for field in ("accountId", "workspaceId", "email", "planType"):
            value = profile.get(field)
            if isinstance(value, str) and value.strip():
                metadata[field] = value.strip()
        return metadata

    def _profile_from_oauth_result(self, result: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        resolved_profile_id = str(result.get("resolvedProfileId") or "").strip()
        if not resolved_profile_id:
            raise ValueError("OAuth 登录结果缺少 profile id")
        profile = self.store.get_profile(resolved_profile_id)
        if profile is None:
            raise ValueError(f"OAuth profile 不存在: {resolved_profile_id}")
        return resolved_profile_id, profile


def _infer_provider_from_route(base_url: str, model: str) -> str:
    host = urlparse(str(base_url or "")).netloc.lower()
    if not host:
        return ""
    if "openai.com" in host or "chatgpt.com" in host:
        return "openai"
    if "minimax" in host:
        return "minimax"
    if "volces.com" in host or "volcengine" in host:
        return "volcengine"
    if "nvidia.com" in host:
        return "nvidia"
    if "anthropic.com" in host:
        return "anthropic"
    return ""




def json_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
