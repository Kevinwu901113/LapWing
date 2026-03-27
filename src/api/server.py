"""FastAPI 本地服务，供桌面端使用。"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
import uvicorn

from config.settings import DATA_DIR

logger = logging.getLogger("lapwing.api")

_LEARNINGS_DIR = DATA_DIR / "learnings"
_DIST_DIR = Path(__file__).parent.parent.parent / "desktop" / "dist"


class MemoryDeleteRequest(BaseModel):
    chat_id: str
    fact_key: str


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


def create_app(brain, event_bus, task_view_store=None) -> FastAPI:
    app = FastAPI(title="Lapwing Local API", version="0.1.0")
    app.state.brain = brain
    app.state.event_bus = event_bus
    app.state.task_view_store = task_view_store
    app.state.started_at = datetime.now(timezone.utc).isoformat()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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
        items = await asyncio.to_thread(_read_learning_entries, _LEARNINGS_DIR)
        return {"items": items}

    @app.post("/api/evolve")
    async def post_evolve():
        if not hasattr(brain, "prompt_evolver") or brain.prompt_evolver is None:
            return {"success": False, "error": "prompt 进化功能尚未启用。"}

        result = await brain.prompt_evolver.evolve()
        if result.get("success"):
            brain.reload_persona()
        return result

    @app.post("/api/reload")
    async def post_reload():
        brain.reload_persona()
        return {"success": True}

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
        host: str = "0.0.0.0",
        port: int = 8765,
    ) -> None:
        self._brain = brain
        self._event_bus = event_bus
        self._task_view_store = task_view_store
        self._host = host
        self._port = port
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is not None:
            return

        app = create_app(self._brain, self._event_bus, self._task_view_store)
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
