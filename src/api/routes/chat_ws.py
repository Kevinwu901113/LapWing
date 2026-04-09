"""WebSocket chat 端点。"""

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger("lapwing.api.routes.chat_ws")

router = APIRouter(tags=["chat"])

# 由 server.py init() 注入
_brain = None
_channel_manager = None


def init(brain, channel_manager) -> None:
    global _brain, _channel_manager
    _brain = brain
    _channel_manager = channel_manager


@router.websocket("/ws/chat")
async def websocket_chat(ws: WebSocket):
    """WebSocket endpoint for desktop chat."""
    from config.settings import DESKTOP_DEFAULT_OWNER, DESKTOP_WS_CHAT_ID_PREFIX
    from src.adapters.base import ChannelType
    token = ws.query_params.get("token", "")
    if not DESKTOP_DEFAULT_OWNER and not token:
        await ws.close(code=4001, reason="Authentication required")
        return

    await ws.accept()
    connection_id = str(id(ws))

    mgr = _channel_manager

    _desktop_adapter = mgr.adapters.get(ChannelType.DESKTOP) if mgr else None
    if _desktop_adapter is not None:
        _desktop_adapter.add_connection(connection_id, ws)
    _brain._desktop_connected = True

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

                if mgr is not None:
                    mgr.last_active_channel = ChannelType.DESKTOP

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
                    reply = await _brain.think_conversational(
                        chat_id=chat_id,
                        user_message=content,
                        send_fn=send_fn,
                        typing_fn=typing_fn,
                        status_callback=status_callback,
                        adapter="desktop",
                        user_id="owner",
                    )
                    await ws.send_json({"type": "reply", "content": "", "final": True})
                except Exception as exc:
                    await ws.send_json({
                        "type": "error",
                        "message": f"处理消息失败: {exc}",
                    })

    except WebSocketDisconnect:
        pass
    finally:
        if _desktop_adapter is not None:
            _desktop_adapter.remove_connection(connection_id)
        _brain._desktop_connected = bool(
            _desktop_adapter.connections if _desktop_adapter is not None else False
        )
