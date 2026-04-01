"""FastAPI 本地服务，供桌面端使用。"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
import uvicorn

from config.settings import (
    API_ALLOWED_ORIGINS,
    API_HOST,
    API_PORT,
    API_SESSION_TTL_SECONDS,
    JOURNAL_DIR,
)
from src.core.latency_monitor import LatencyMonitor

logger = logging.getLogger("lapwing.api.server")

_DIST_DIR = Path(__file__).parent.parent.parent / "desktop" / "dist"


class MemoryDeleteRequest(BaseModel):
    chat_id: str
    fact_key: str


class LatencyTelemetryRequest(BaseModel):
    metric: str
    samples_ms: list[float]
    client_timestamp: str | None = None


class ApiSessionRequest(BaseModel):
    bootstrap_token: str | None = None


class CodexCacheImportRequest(BaseModel):
    path: str | None = None
    profile_id: str | None = None


class OAuthStartRequest(BaseModel):
    return_to: str | None = None
    profile_id: str | None = None


def _visible_user_facts(facts: list[dict]) -> list[dict]:
    return [
        fact for fact in facts
        if not str(fact.get("fact_key", "")).startswith("memory_summary_")
    ]


def _read_learning_entries(directory: Path) -> list[dict]:
    if not directory.exists():
        return []

    items: list[dict] = []
    for path in sorted(directory.glob("*.md"), reverse=True):
        stat = path.stat()
        items.append(
            {
                "filename": path.name,
                "date": path.stem,
                "updated_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "content": path.read_text(encoding="utf-8"),
            }
        )
    return items


def create_app(
    brain,
    event_bus,
    task_view_store=None,
    latency_monitor: LatencyMonitor | None = None,
) -> FastAPI:
    if latency_monitor is not None and hasattr(event_bus, "set_latency_monitor"):
        event_bus.set_latency_monitor(latency_monitor)

    app = FastAPI(title="Lapwing Local API", version="0.1.0")
    app.state.brain = brain
    app.state.event_bus = event_bus
    app.state.task_view_store = task_view_store
    app.state.latency_monitor = latency_monitor
    app.state.started_at = datetime.now(timezone.utc).isoformat()
    app.state.auth_manager = getattr(brain, "auth_manager", None)

    # Mount model routing API if ModelConfigManager is available
    _model_config = getattr(brain, "_model_config", None)
    if _model_config is not None:
        from src.api import model_routing as _model_routing_api
        _model_routing_api.init(_model_config, getattr(brain, "router", None))
        app.include_router(_model_routing_api.router)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=API_ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def require_local_api_auth(request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        if path == "/api/auth/session":
            return await call_next(request)

        auth_manager = app.state.auth_manager
        if auth_manager is None:
            return await call_next(request)

        session_token = request.cookies.get(auth_manager.api_sessions.cookie_name)
        auth_header = request.headers.get("authorization", "")
        bearer_token = ""
        if auth_header.lower().startswith("bearer "):
            bearer_token = auth_header[7:].strip()

        if auth_manager.validate_api_session(session_token) or (
            bearer_token and bearer_token == auth_manager.bootstrap_token()
        ):
            return await call_next(request)

        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    @app.post("/api/auth/session")
    async def post_api_session(payload: ApiSessionRequest, response: Response, request: Request):
        auth_manager = app.state.auth_manager
        if auth_manager is None:
            raise HTTPException(status_code=503, detail="Auth manager not available")

        auth_header = request.headers.get("authorization", "")
        bootstrap_token = payload.bootstrap_token
        if not bootstrap_token and auth_header.lower().startswith("bearer "):
            bootstrap_token = auth_header[7:].strip()
        if not bootstrap_token:
            raise HTTPException(status_code=401, detail="Missing bootstrap token")

        try:
            session_token = auth_manager.create_api_session(bootstrap_token)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

        response.set_cookie(
            key=auth_manager.api_sessions.cookie_name,
            value=session_token,
            httponly=True,
            samesite="strict",
            secure=False,
            max_age=API_SESSION_TTL_SECONDS,
            path="/",
        )
        return {"success": True}

    @app.get("/api/auth/status")
    async def get_auth_status():
        auth_manager = app.state.auth_manager
        if auth_manager is None:
            raise HTTPException(status_code=503, detail="Auth manager not available")
        return auth_manager.auth_status()

    @app.post("/api/auth/desktop-token")
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

    @app.post("/api/auth/import/codex-cache")
    async def post_import_codex_cache(payload: CodexCacheImportRequest):
        auth_manager = app.state.auth_manager
        if auth_manager is None:
            raise HTTPException(status_code=503, detail="Auth manager not available")
        path = payload.path or str(Path.home() / ".codex" / "auth.json")
        try:
            profile_id, profile = auth_manager.import_codex_auth_json(
                path=path,
                profile_id=payload.profile_id,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "success": True,
            "profile_id": profile_id,
            "profile": profile,
        }

    @app.post("/api/auth/oauth/openai-codex/start")
    async def post_openai_codex_oauth_start(payload: OAuthStartRequest):
        auth_manager = app.state.auth_manager
        if auth_manager is None:
            raise HTTPException(status_code=503, detail="Auth manager not available")
        try:
            session = auth_manager.start_oauth_login(
                provider="openai",
                method="pkce",
                profile_id=payload.profile_id,
                return_to=payload.return_to,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return session

    @app.get("/api/auth/oauth/sessions/{login_id}")
    async def get_oauth_login_session(login_id: str):
        auth_manager = app.state.auth_manager
        if auth_manager is None:
            raise HTTPException(status_code=503, detail="Auth manager not available")
        try:
            return auth_manager.get_oauth_login_session(login_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="OAuth login session not found") from exc

    @app.get("/api/chats")
    async def get_chats():
        chat_ids = await brain.memory.get_all_chat_ids()
        items = []
        for chat_id in chat_ids:
            last_interaction = await brain.memory.get_last_interaction(chat_id)
            items.append(
                {
                    "chat_id": chat_id,
                    "last_interaction": last_interaction.isoformat() if last_interaction else None,
                }
            )
        items.sort(key=lambda item: item["last_interaction"] or "", reverse=True)
        return items

    @app.get("/api/status")
    async def get_status():
        chats = await get_chats()
        last_interaction = chats[0]["last_interaction"] if chats else None
        return {
            "online": True,
            "started_at": app.state.started_at,
            "chat_count": len(chats),
            "last_interaction": last_interaction,
            "latency_monitor": (
                app.state.latency_monitor.snapshot()
                if app.state.latency_monitor is not None
                else None
            ),
        }

    @app.get("/api/interests")
    async def get_interests(chat_id: str = Query(...)):
        items = await brain.memory.get_top_interests(chat_id, limit=10)
        return {"chat_id": chat_id, "items": items}

    @app.get("/api/memory")
    async def get_memory(chat_id: str = Query(...)):
        facts = _visible_user_facts(await brain.memory.get_user_facts(chat_id))
        items = [
            {
                "index": index,
                "fact_key": fact["fact_key"],
                "fact_value": fact["fact_value"],
                "updated_at": fact.get("updated_at"),
            }
            for index, fact in enumerate(facts, start=1)
        ]
        return {"chat_id": chat_id, "items": items}

    @app.post("/api/memory/delete")
    async def delete_memory(payload: MemoryDeleteRequest):
        success = await brain.memory.delete_user_fact(payload.chat_id, payload.fact_key)
        return {"success": success}

    @app.get("/api/learnings")
    async def get_learnings():
        items = await asyncio.to_thread(_read_learning_entries, JOURNAL_DIR)
        return {"items": items}

    @app.post("/api/evolve")
    async def post_evolve():
        if not hasattr(brain, "evolution_engine") or brain.evolution_engine is None:
            return {"success": False, "error": "进化功能尚未启用。"}

        result = await brain.evolution_engine.evolve()
        if result.get("success"):
            brain.reload_persona()
        return result

    @app.post("/api/reload")
    async def post_reload():
        brain.reload_persona()
        return {"success": True}

    @app.post("/api/telemetry/latency")
    async def post_latency_telemetry(payload: LatencyTelemetryRequest):
        monitor = app.state.latency_monitor
        if monitor is None:
            return {"success": False, "reason": "latency_monitor_not_enabled", "accepted_samples": 0}

        accepted_samples = 0
        if payload.metric == "tool_execution_start_to_ui":
            accepted_samples = monitor.record_frontend_start_to_ui_samples(payload.samples_ms)
        return {
            "success": True,
            "accepted_samples": accepted_samples,
            "metric": payload.metric,
        }

    @app.get("/api/events/stream")
    async def stream_events():
        async def event_stream():
            queue = await event_bus.subscribe()
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                        continue

                    if app.state.latency_monitor is not None:
                        try:
                            app.state.latency_monitor.record_event_stream_emitted(event)
                        except Exception as exc:
                            logger.warning("记录 SSE 事件出站延迟失败: %s", exc)

                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            finally:
                await event_bus.unsubscribe(queue)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    @app.get("/api/tasks")
    async def get_tasks(
        chat_id: str | None = Query(default=None),
        status: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=500),
    ):
        store = app.state.task_view_store
        if store is None:
            return {"items": []}
        items = await store.list_tasks(chat_id=chat_id, status=status, limit=limit)
        return {"items": items}

    @app.get("/api/tasks/{task_id}")
    async def get_task(task_id: str):
        store = app.state.task_view_store
        if store is None:
            raise HTTPException(status_code=404, detail="Task not found")
        task = await store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return task

    # ── Log streaming (J3) ──

    @app.get("/api/logs/stream")
    async def stream_logs(
        level: str = Query("INFO", pattern="^(DEBUG|INFO|WARNING|ERROR)$"),
        module: str = Query("", description="Filter by logger name prefix"),
    ):
        """SSE stream of log entries for the frontend log viewer."""
        import queue as _queue

        log_queue: _queue.Queue = _queue.Queue(maxsize=500)

        class _QueueHandler(logging.Handler):
            def emit(self, record):
                try:
                    entry = {
                        "timestamp": self.format(record).split(" [")[0],
                        "level": record.levelname,
                        "logger": record.name,
                        "message": record.getMessage(),
                    }
                    log_queue.put_nowait(entry)
                except _queue.Full:
                    pass

        handler = _QueueHandler()
        handler.setLevel(getattr(logging, level))
        handler.setFormatter(logging.Formatter("%(asctime)s"))
        lapwing_logger = logging.getLogger("lapwing")
        lapwing_logger.addHandler(handler)

        async def event_generator():
            try:
                while True:
                    try:
                        entry = await asyncio.to_thread(log_queue.get, timeout=1.0)
                        if module and not entry["logger"].startswith(f"lapwing.{module}"):
                            continue
                        yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
                    except Exception:
                        yield ": keepalive\n\n"
            finally:
                lapwing_logger.removeHandler(handler)

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @app.get("/api/logs/recent")
    async def get_recent_logs(
        lines: int = Query(200, ge=1, le=2000),
        level: str = Query("INFO"),
    ):
        """Return recent log lines from the log file."""
        from config.settings import LOGS_DIR as _LOGS_DIR
        log_file = _LOGS_DIR / "lapwing.log"
        if not log_file.exists():
            return {"lines": []}
        all_lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        recent = all_lines[-lines:]
        level_priority = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}
        min_priority = level_priority.get(level, 1)
        filtered = []
        for line in recent:
            for lvl, pri in level_priority.items():
                if f" {lvl}: " in line and pri >= min_priority:
                    filtered.append(line)
                    break
        return {"lines": filtered, "total": len(all_lines)}

    # ── Platform config + feature flags (J4) ──

    @app.get("/api/config/platforms")
    async def get_platform_config():
        from config import settings
        return {
            "telegram": {
                "enabled": bool(settings.TELEGRAM_TOKEN),
                "proxy_url": settings.TELEGRAM_PROXY_URL,
                "kevin_id": settings.TELEGRAM_KEVIN_ID,
                "text_mode": settings.TELEGRAM_TEXT_MODE,
            },
            "qq": {
                "enabled": settings.QQ_ENABLED,
                "ws_url": settings.QQ_WS_URL,
                "self_id": settings.QQ_SELF_ID,
                "kevin_id": settings.QQ_KEVIN_ID,
                "group_ids": settings.QQ_GROUP_IDS,
                "group_cooldown": settings.QQ_GROUP_COOLDOWN,
            },
        }

    @app.get("/api/config/features")
    async def get_feature_flags():
        from config import settings
        return {
            "shell_enabled": settings.SHELL_ENABLED,
            "web_tools_enabled": settings.CHAT_WEB_TOOLS_ENABLED,
            "skills_enabled": settings.SKILLS_ENABLED,
            "experience_skills_enabled": settings.EXPERIENCE_SKILLS_ENABLED,
            "session_enabled": settings.SESSION_ENABLED,
            "memory_crud_enabled": settings.MEMORY_CRUD_ENABLED,
            "auto_memory_extract_enabled": settings.AUTO_MEMORY_EXTRACT_ENABLED,
            "self_schedule_enabled": settings.SELF_SCHEDULE_ENABLED,
            "qq_enabled": settings.QQ_ENABLED,
        }

    # ── Persona files (J4) ──

    @app.get("/api/persona/files")
    async def get_persona_files():
        from config.settings import SOUL_PATH, IDENTITY_DIR, PROMPTS_DIR
        files = {}
        for name, path in [
            ("soul", SOUL_PATH),
            ("voice", PROMPTS_DIR / "lapwing_voice.md"),
            ("capabilities", PROMPTS_DIR / "lapwing_capabilities.md"),
            ("constitution", IDENTITY_DIR / "constitution.md"),
        ]:
            if path.exists():
                files[name] = {
                    "path": str(path),
                    "content": path.read_text(encoding="utf-8"),
                }
        return {"files": files}

    @app.post("/api/persona/files/{file_name}")
    async def update_persona_file(file_name: str, request: Request):
        body = await request.json()
        content = body.get("content", "")
        from config.settings import SOUL_PATH, IDENTITY_DIR, PROMPTS_DIR
        path_map = {
            "soul": SOUL_PATH,
            "voice": PROMPTS_DIR / "lapwing_voice.md",
            "capabilities": PROMPTS_DIR / "lapwing_capabilities.md",
            "constitution": IDENTITY_DIR / "constitution.md",
        }
        path = path_map.get(file_name)
        if path is None:
            raise HTTPException(status_code=404, detail=f"Unknown persona file: {file_name}")
        path.write_text(content, encoding="utf-8")
        brain = app.state.brain
        if hasattr(brain, "reload_persona"):
            brain.reload_persona()
        return {"success": True, "file": file_name}

    # ── Scheduled tasks CRUD (J4) ──

    @app.get("/api/scheduled-tasks")
    async def get_scheduled_tasks():
        from config.settings import SCHEDULED_TASKS_PATH
        if not SCHEDULED_TASKS_PATH.exists():
            return {"tasks": []}
        data = json.loads(SCHEDULED_TASKS_PATH.read_text(encoding="utf-8"))
        return {"tasks": data if isinstance(data, list) else []}

    @app.delete("/api/scheduled-tasks/{task_id}")
    async def delete_scheduled_task(task_id: str):
        from config.settings import SCHEDULED_TASKS_PATH
        if not SCHEDULED_TASKS_PATH.exists():
            raise HTTPException(status_code=404, detail="No tasks found")
        data = json.loads(SCHEDULED_TASKS_PATH.read_text(encoding="utf-8"))
        updated = [t for t in data if t.get("id") != task_id]
        if len(updated) == len(data):
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
        SCHEDULED_TASKS_PATH.write_text(
            json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {"success": True, "task_id": task_id}

    # ── WebSocket chat (K1) ──

    _active_desktop_ws: dict[str, WebSocket] = {}

    def _notify_desktop_presence(b, *, connected: bool) -> None:
        b._desktop_connected = connected
        logger.info("Desktop presence: %s", "connected" if connected else "disconnected")

    @app.websocket("/ws/chat")
    async def websocket_chat(ws: WebSocket):
        """WebSocket endpoint for desktop chat."""
        from config.settings import DESKTOP_DEFAULT_OWNER, DESKTOP_WS_CHAT_ID_PREFIX
        token = ws.query_params.get("token", "")
        if not DESKTOP_DEFAULT_OWNER and not token:
            await ws.close(code=4001, reason="Authentication required")
            return

        await ws.accept()
        connection_id = str(id(ws))
        _active_desktop_ws[connection_id] = ws

        b = app.state.brain
        _notify_desktop_presence(b, connected=True)
        await ws.send_json({"type": "presence_ack", "status": "connected"})

        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await ws.send_json({"type": "error", "message": "Invalid JSON"})
                    continue

                msg_type = msg.get("type", "")

                if msg_type == "ping":
                    await ws.send_json({"type": "pong"})
                    continue

                if msg_type == "message":
                    content = str(msg.get("content", "")).strip()
                    if not content:
                        continue

                    chat_id = f"{DESKTOP_WS_CHAT_ID_PREFIX}:{connection_id}"

                    async def send_fn(text: str) -> None:
                        try:
                            await ws.send_json({"type": "interim", "content": text})
                        except Exception:
                            pass

                    async def typing_fn() -> None:
                        try:
                            await ws.send_json({"type": "typing"})
                        except Exception:
                            pass

                    async def status_callback(cid: str, status_text: str) -> None:
                        try:
                            await ws.send_json({
                                "type": "status",
                                "phase": "executing",
                                "text": status_text,
                            })
                        except Exception:
                            pass

                    try:
                        await ws.send_json({"type": "status", "phase": "thinking", "text": ""})
                        reply = await b.think_conversational(
                            chat_id=chat_id,
                            user_message=content,
                            send_fn=send_fn,
                            typing_fn=typing_fn,
                            status_callback=status_callback,
                            adapter="desktop",
                            user_id="owner",
                        )
                        await ws.send_json({"type": "reply", "content": reply, "final": True})
                    except Exception as exc:
                        await ws.send_json({
                            "type": "error",
                            "message": f"处理消息失败: {exc}",
                        })

        except WebSocketDisconnect:
            pass
        finally:
            _active_desktop_ws.pop(connection_id, None)
            _notify_desktop_presence(b, connected=bool(_active_desktop_ws))

    # 托管构建好的前端静态文件（SPA 回退到 index.html）
    if _DIST_DIR.exists():
        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(full_path: str):
            file_path = _DIST_DIR / full_path
            if file_path.is_file():
                return FileResponse(file_path)
            return FileResponse(_DIST_DIR / "index.html")

    return app


class LocalApiServer:
    """管理 uvicorn 生命周期。"""

    def __init__(
        self,
        brain,
        event_bus,
        task_view_store=None,
        latency_monitor: LatencyMonitor | None = None,
        host: str = API_HOST,
        port: int = API_PORT,
    ) -> None:
        self._brain = brain
        self._event_bus = event_bus
        self._task_view_store = task_view_store
        self._latency_monitor = latency_monitor
        self._host = host
        self._port = port
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is not None:
            return

        app = create_app(
            self._brain,
            self._event_bus,
            self._task_view_store,
            self._latency_monitor,
        )
        config = uvicorn.Config(
            app,
            host=self._host,
            port=self._port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._task = asyncio.create_task(self._server.serve())

        for _ in range(50):
            if self._server.started:
                logger.info(f"本地 API 已启动：http://{self._host}:{self._port}")
                return
            if self._task.done():
                break
            await asyncio.sleep(0.1)

        logger.warning("本地 API 启动状态未知，继续运行")

    async def shutdown(self) -> None:
        if self._server is not None:
            self._server.should_exit = True

        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except asyncio.TimeoutError:
                logger.warning("等待本地 API 关闭超时")
            finally:
                self._task = None
                self._server = None
