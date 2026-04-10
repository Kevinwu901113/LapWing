"""系统管理 API 端点：进化、配置、日志、心跳、人格、知识。"""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger("lapwing.api.routes.system")

router = APIRouter(tags=["system"])

# 由 server.py init() 注入
_brain = None
_event_bus = None
_app = None  # FastAPI app instance（用于 app.state 访问）


class LatencyTelemetryRequest(BaseModel):
    metric: str
    samples_ms: list[float]
    client_timestamp: str | None = None


class ModelTestBody(BaseModel):
    message: str
    slot: str | None = None
    provider_id: str | None = None
    model: str | None = None


class SkillToggleBody(BaseModel):
    enabled: bool


_SENSING_VALID_STATES = {"normal", "gaming", "locked"}


class SensingContextBody(BaseModel):
    summary: str
    state: str = "normal"  # "normal" | "gaming" | "locked"
    timestamp: str | None = None
    current_app: str | None = None
    current_title: str | None = None
    detail: str | None = None


def init(brain, event_bus, app) -> None:
    global _brain, _event_bus, _app
    _brain = brain
    _event_bus = event_bus
    _app = app


@router.get("/api/status")
async def get_status():
    chats_response = await _get_chats()
    last_interaction = chats_response[0]["last_interaction"] if chats_response else None
    return {
        "online": True,
        "started_at": _app.state.started_at,
        "chat_count": len(chats_response),
        "last_interaction": last_interaction,
        "latency_monitor": (
            _app.state.latency_monitor.snapshot()
            if _app.state.latency_monitor is not None
            else None
        ),
    }


async def _get_chats():
    """Helper to fetch chat list (used by status endpoint)."""
    chat_ids = await _brain.memory.get_all_chat_ids()
    items = []
    for chat_id in chat_ids:
        last_interaction = await _brain.memory.get_last_interaction(chat_id)
        items.append({
            "chat_id": chat_id,
            "last_interaction": last_interaction.isoformat() if last_interaction else None,
        })
    items.sort(key=lambda item: item["last_interaction"] or "", reverse=True)
    return items


@router.post("/api/evolve")
async def post_evolve():
    if not hasattr(_brain, "evolution_engine") or _brain.evolution_engine is None:
        return {"success": False, "error": "进化功能尚未启用。"}

    result = await _brain.evolution_engine.evolve()
    if result.get("success"):
        _brain.reload_persona()
    return result


@router.post("/api/reload")
async def post_reload():
    _brain.reload_persona()
    return {"success": True}


@router.post("/api/telemetry/latency")
async def post_latency_telemetry(payload: LatencyTelemetryRequest):
    monitor = _app.state.latency_monitor
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


@router.get("/api/events/stream")
async def stream_events():
    async def event_stream():
        queue = await _event_bus.subscribe()
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue

                if _app.state.latency_monitor is not None:
                    try:
                        _app.state.latency_monitor.record_event_stream_emitted(event)
                    except Exception as exc:
                        logger.warning("记录 SSE 事件出站延迟失败: %s", exc)

                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            await _event_bus.unsubscribe(queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@router.get("/api/tasks")
async def get_tasks(
    chat_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
):
    store = _app.state.task_view_store
    if store is None:
        return {"items": []}
    items = await store.list_tasks(chat_id=chat_id, status=status, limit=limit)
    return {"items": items}


@router.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    store = _app.state.task_view_store
    if store is None:
        raise HTTPException(status_code=404, detail="Task not found")
    task = await store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("/api/logs/stream")
async def stream_logs(
    level: str = Query("INFO", pattern="^(DEBUG|INFO|WARNING|ERROR)$"),
    module: str = Query("", description="Filter by logger name prefix"),
):
    """SSE stream of log entries for the frontend log viewer."""
    import queue as _queue
    import uuid as _uuid

    conn_id = _uuid.uuid4().hex
    log_queue: _queue.Queue = _queue.Queue(maxsize=500)
    _app.state._log_broadcast_queues[conn_id] = log_queue
    min_level = getattr(logging, level)

    async def event_generator():
        try:
            while True:
                try:
                    entry = await asyncio.to_thread(log_queue.get, timeout=1.0)
                    entry_level = getattr(logging, entry.get("level", "INFO"), 20)
                    if entry_level < min_level:
                        continue
                    if module and not entry["logger"].startswith(f"lapwing.{module}"):
                        continue
                    yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
                except Exception:
                    yield ": keepalive\n\n"
        finally:
            _app.state._log_broadcast_queues.pop(conn_id, None)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/api/logs/recent")
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


@router.get("/api/config/platforms")
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


@router.get("/api/config/features")
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


@router.get("/api/persona/files")
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


@router.post("/api/persona/files/{file_name}")
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
    if hasattr(_brain, "reload_persona"):
        _brain.reload_persona()
    return {"success": True, "file": file_name}


@router.get("/api/scheduled-tasks")
async def get_scheduled_tasks():
    from config.settings import SCHEDULED_TASKS_PATH
    if not SCHEDULED_TASKS_PATH.exists():
        return {"tasks": []}
    data = json.loads(SCHEDULED_TASKS_PATH.read_text(encoding="utf-8"))
    return {"tasks": data if isinstance(data, list) else []}


@router.delete("/api/scheduled-tasks/{task_id}")
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


@router.get("/api/system/stats")
async def get_system_stats():
    import psutil
    cpu_pct = await asyncio.to_thread(psutil.cpu_percent, 0.5)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage('/')

    cpu_model = "Unknown"
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    cpu_model = line.split(":")[1].strip()
                    break
    except Exception:
        pass

    return {
        "cpu_percent": cpu_pct,
        "cpu_model": cpu_model,
        "memory_total_gb": round(mem.total / 1024**3, 2),
        "memory_used_gb": round(mem.used / 1024**3, 2),
        "memory_percent": mem.percent,
        "disk_total_gb": round(disk.total / 1024**3, 2),
        "disk_used_gb": round(disk.used / 1024**3, 2),
        "disk_percent": disk.percent,
    }


@router.get("/api/system/api-usage")
async def get_api_usage():
    return {"providers": []}


@router.get("/api/heartbeat/status")
async def get_heartbeat_status():
    heartbeat = getattr(_app.state, "heartbeat", None)
    if heartbeat is None:
        return {"actions": [], "interval_seconds": 0}

    actions = []
    for action in heartbeat.registry._actions.values():
        actions.append({
            "name": action.name,
            "beat_types": getattr(action, "beat_types", []),
            "selection_mode": getattr(action, "selection_mode", "decide"),
            "enabled": getattr(action, "enabled", True),
            "last_run": getattr(action, "last_run", None),
            "history_24h": getattr(action, "history_24h", []),
        })

    return {
        "actions": actions,
        "interval_seconds": getattr(heartbeat, "interval_seconds", 0),
    }


@router.get("/api/persona/changelog")
async def get_persona_changelog():
    from config.settings import CHANGELOG_PATH
    entries = []
    try:
        text = CHANGELOG_PATH.read_text(encoding="utf-8")
        sections = re.split(r'^## ', text, flags=re.MULTILINE)
        for section in sections:
            if not section.strip():
                continue
            lines = section.strip().split('\n')
            header = lines[0].strip()
            parts = header.split('—', 1)
            date = parts[0].strip() if parts else header
            summary = parts[1].strip() if len(parts) > 1 else ""
            content = '\n'.join(lines[1:]).strip()
            entries.append({"date": date, "summary": summary, "content": content})
    except Exception:
        pass
    return {"entries": entries}


@router.get("/api/memory/summaries")
async def get_memory_summaries():
    from config.settings import CONVERSATION_SUMMARIES_DIR
    items = []
    try:
        files = sorted(CONVERSATION_SUMMARIES_DIR.glob("*.md"), reverse=True)[:50]
        for f in files:
            try:
                content = f.read_text(encoding="utf-8")
                items.append({
                    "filename": f.name,
                    "date": f.stem,
                    "content": content,
                })
            except Exception:
                continue
    except Exception:
        pass
    return {"items": items}


@router.get("/api/knowledge/notes")
async def get_knowledge_notes():
    from config.settings import DATA_DIR as _DATA_DIR
    knowledge_dir = _DATA_DIR / "knowledge"
    items = []
    try:
        for f in sorted(knowledge_dir.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                content = f.read_text(encoding="utf-8")
                items.append({
                    "topic": f.stem,
                    "content": content,
                    "updated_at": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                })
            except Exception:
                continue
    except Exception:
        pass
    return {"items": items}


@router.delete("/api/knowledge/notes/{topic}")
async def delete_knowledge_note(topic: str):
    from config.settings import DATA_DIR as _DATA_DIR
    knowledge_dir = _DATA_DIR / "knowledge"
    note_path = knowledge_dir / f"{topic}.md"
    if not note_path.exists():
        raise HTTPException(status_code=404, detail="笔记不存在")
    note_path.unlink()
    return {"success": True}


# ── 桌面端环境感知 ──────────────────────────────────────────────────

@router.post("/api/sensing/context")
async def receive_sensing_context(body: SensingContextBody):
    """接收桌面端推送的环境感知摘要。"""
    from config.settings import OWNER_IDS
    from src.core import vitals

    # 输入校验
    if body.state not in _SENSING_VALID_STATES:
        raise HTTPException(status_code=422, detail=f"state 必须是 {_SENSING_VALID_STATES} 之一")
    if len(body.summary) > 2000:
        raise HTTPException(status_code=422, detail="summary 超过长度限制")

    owner_id = next(iter(OWNER_IDS)) if OWNER_IDS else "default"

    vitals.update_desktop_sensing(
        owner_id=owner_id,
        summary=body.summary,
        state=body.state,
        current_app=body.current_app,
    )

    # 游戏结束时通过 ChannelManager 发送主动消息
    if (
        body.state == "normal"
        and body.detail
        and body.detail.startswith("game_end:")
    ):
        parts = body.detail.split(":", 2)
        game = parts[1] if len(parts) > 1 else "unknown"
        duration = parts[2] if len(parts) > 2 else "?"

        channel_mgr = getattr(_app.state, "channel_manager", None) if _app else None
        if owner_id != "default" and _brain is not None and channel_mgr is not None:
            async def _send_game_end():
                try:
                    async def send_fn(text: str) -> None:
                        await channel_mgr.send_to_owner(text)

                    await _brain.think_conversational(
                        chat_id=owner_id,
                        user_message=f"[系统通知] Kevin 刚打完 {game}，玩了 {duration}。自然地回应他。",
                        send_fn=send_fn,
                        adapter="desktop",
                        user_id="owner",
                    )
                except Exception as exc:
                    logger.warning(f"游戏结束主动消息触发失败: {exc}")

            asyncio.ensure_future(_send_game_end())

    return {"ok": True}


# ── 模型测试 (M-SERVER-3) ──────────────────────────────────────────────

@router.post("/api/model-routing/test")
async def test_model(body: ModelTestBody):
    """发送测试消息到指定模型/slot。"""
    import time as _time
    start = _time.time()
    try:
        reply = await _brain.router.chat_turn(
            messages=[{"role": "user", "content": body.message}],
            purpose=body.slot or "lightweight_judgment",
        )
        elapsed = _time.time() - start
        return {
            "reply": reply,
            "elapsed_ms": int(elapsed * 1000),
            "model": body.model or body.slot or "default",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── 技能管理 (M-SERVER-4) ──────────────────────────────────────────────

@router.get("/api/skills")
async def get_skills():
    """返回所有技能（插件 + 经验）。"""
    plugin_skills = []
    experience_skills = []

    sm = getattr(_brain, "skill_manager", None)
    if sm is not None and hasattr(sm, "list_skills"):
        plugin_skills = sm.list_skills()

    esm = getattr(_brain, "experience_skill_manager", None)
    if esm is not None and hasattr(esm, "list_skills"):
        experience_skills = esm.list_skills()

    return {"plugin_skills": plugin_skills, "experience_skills": experience_skills}


@router.post("/api/skills/{skill_id}/toggle")
async def toggle_skill(skill_id: str, body: SkillToggleBody):
    """启用/禁用技能。"""
    sm = getattr(_brain, "skill_manager", None)
    esm = getattr(_brain, "experience_skill_manager", None)

    toggled = False
    if sm is not None and hasattr(sm, "toggle_skill"):
        toggled = sm.toggle_skill(skill_id, body.enabled)
    if not toggled and esm is not None and hasattr(esm, "toggle_skill"):
        toggled = esm.toggle_skill(skill_id, body.enabled)

    if not toggled:
        raise HTTPException(status_code=404, detail=f"技能 {skill_id} 不存在")
    return {"ok": True}
