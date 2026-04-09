"""FastAPI 本地服务，供桌面端使用。"""

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
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


def create_app(
    brain,
    event_bus,
    task_view_store=None,
    latency_monitor: LatencyMonitor | None = None,
    channel_manager=None,
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
    app.state.channel_manager = channel_manager
    app.state.heartbeat = None

    # Mount model routing API if ModelConfigManager is available
    _model_config = getattr(brain, "_model_config", None)
    if _model_config is not None:
        from src.api import model_routing as _model_routing_api
        _model_routing_api.init(_model_config, getattr(brain, "router", None))
        app.include_router(_model_routing_api.router)

    # Mount route modules
    from src.api.routes import auth as _auth_routes
    from src.api.routes import data as _data_routes
    from src.api.routes import system as _system_routes
    from src.api.routes import chat_ws as _chat_ws_routes

    _auth_routes.init(app.state.auth_manager, api_session_ttl=API_SESSION_TTL_SECONDS)
    _data_routes.init(brain, journal_dir=JOURNAL_DIR)
    _system_routes.init(brain, event_bus, app)
    _chat_ws_routes.init(brain, channel_manager)

    app.include_router(_auth_routes.router)
    app.include_router(_data_routes.router)
    app.include_router(_system_routes.router)
    app.include_router(_chat_ws_routes.router)

    # 浏览器子系统路由（可选，仅在 BROWSER_ENABLED 时挂载）
    _browser_manager = getattr(brain, "browser_manager", None)
    if _browser_manager is not None:
        from src.api.routes import browser as _browser_routes
        _browser_routes.init(_browser_manager)
        app.include_router(_browser_routes.router)

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

    # Log broadcast handler for SSE log streaming
    if not hasattr(app.state, "_log_broadcast_handler"):
        import queue as _queue

        class _BroadcastHandler(logging.Handler):
            def emit(self, record):
                entry = {
                    "timestamp": self.format(record).split(" [")[0],
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                }
                for q in list(app.state._log_broadcast_queues.values()):
                    try:
                        q.put_nowait(entry)
                    except _queue.Full:
                        pass

        bh = _BroadcastHandler()
        bh.setLevel(logging.DEBUG)
        bh.setFormatter(logging.Formatter("%(asctime)s"))
        logging.getLogger("lapwing").addHandler(bh)
        app.state._log_broadcast_handler = bh
        app.state._log_broadcast_queues = {}

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
        channel_manager=None,
    ) -> None:
        self._brain = brain
        self._event_bus = event_bus
        self._task_view_store = task_view_store
        self._latency_monitor = latency_monitor
        self._host = host
        self._port = port
        self._channel_manager = channel_manager
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None
        self._app: FastAPI | None = None

    async def start(self) -> None:
        if self._task is not None:
            return

        app = create_app(
            self._brain,
            self._event_bus,
            self._task_view_store,
            self._latency_monitor,
            channel_manager=self._channel_manager,
        )
        self._app = app
        config = uvicorn.Config(
            app,
            host=self._host,
            port=self._port,
            log_level="warning",
            access_log=False,
            log_config=None,
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
