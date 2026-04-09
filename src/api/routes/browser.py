"""Browser 子系统 API 端点。"""

import logging
from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

logger = logging.getLogger("lapwing.api.routes.browser")

router = APIRouter(tags=["browser"])

# 由 server.py init() 注入
_browser_manager = None


def init(browser_manager) -> None:
    global _browser_manager
    _browser_manager = browser_manager


def _require_manager():
    if _browser_manager is None:
        raise HTTPException(status_code=503, detail="浏览器子系统未启用")
    return _browser_manager


class NavigateRequest(BaseModel):
    url: str


@router.get("/api/browser/status")
async def get_browser_status():
    _require_manager()
    tabs = await _browser_manager.list_tabs()
    return {
        "running": _browser_manager.is_started,
        "tab_count": len(tabs),
    }


@router.get("/api/browser/tabs")
async def get_browser_tabs():
    from src.core.browser_manager import BrowserError
    mgr = _require_manager()
    try:
        tabs = await mgr.list_tabs()
        return [asdict(t) for t in tabs]
    except BrowserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/browser/screenshot/{tab_id}")
async def get_browser_screenshot(tab_id: str):
    from src.core.browser_manager import BrowserError
    mgr = _require_manager()
    try:
        filepath = await mgr.screenshot(tab_id=tab_id)
        return FileResponse(filepath, media_type="image/png")
    except BrowserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/browser/navigate")
async def post_browser_navigate(payload: NavigateRequest):
    from src.core.browser_manager import BrowserError
    mgr = _require_manager()
    try:
        page_state = await mgr.navigate(payload.url)
        return {"page_text": page_state.to_llm_text()}
    except BrowserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/browser/close-tab/{tab_id}")
async def post_close_tab(tab_id: str):
    from src.core.browser_manager import BrowserError
    mgr = _require_manager()
    try:
        await mgr.close_tab(tab_id)
        return {"ok": True}
    except BrowserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
