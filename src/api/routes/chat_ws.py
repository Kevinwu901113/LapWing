"""WebSocket chat 端点。"""

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.core.authority_gate import AuthLevel
from src.core.events import MessageEvent

logger = logging.getLogger("lapwing.api.routes.chat_ws")

router = APIRouter(tags=["chat"])

# 由 server.py init() 注入
_brain = None
_channel_manager = None
_event_queue = None
_auth_manager = None

# chat_id → WebSocket 映射，用于 Agent 事件推送
_chat_ws_map: dict[str, WebSocket] = {}


async def forward_agent_progress(chat_id: str, emit) -> None:
    """将 AgentEmit 推送到对应的 WebSocket 客户端。"""
    ws = _chat_ws_map.get(chat_id)
    if ws is None:
        return
    try:
        await ws.send_json({
            "type": "agent_emit",
            "agent_name": emit.agent_name,
            "ref_id": emit.ref_id,
            "state": emit.state.value if hasattr(emit.state, "value") else str(emit.state),
            "progress": emit.progress,
            "note": emit.note,
        })
    except Exception:
        pass


async def forward_agent_result(chat_id: str, notify) -> None:
    """将 AgentNotify 推送到对应的 WebSocket 客户端。"""
    ws = _chat_ws_map.get(chat_id)
    if ws is None:
        return
    try:
        await ws.send_json({
            "type": "agent_notify",
            "agent_name": notify.agent_name,
            "kind": notify.kind.value if hasattr(notify.kind, "value") else str(notify.kind),
            "headline": notify.headline,
            "detail": notify.detail,
            "ref_command_id": notify.ref_command_id,
        })
    except Exception:
        pass


def init(brain, channel_manager, event_queue=None, auth_manager=None) -> None:
    global _brain, _channel_manager, _event_queue, _auth_manager
    _brain = brain
    _channel_manager = channel_manager
    _event_queue = event_queue
    _auth_manager = auth_manager


@router.websocket("/ws/chat")
async def websocket_chat(ws: WebSocket):
    """WebSocket endpoint for desktop chat."""
    from config.settings import DESKTOP_DEFAULT_OWNER, DESKTOP_WS_CHAT_ID_PREFIX, OWNER_IDS
    from src.adapters.base import ChannelType
    from src.api.desktop_auth import validate_desktop_token
    token = ws.query_params.get("token", "")
    if DESKTOP_DEFAULT_OWNER:
        logger.info("[ws] auth bypassed by DESKTOP_DEFAULT_OWNER")
    else:
        valid_api_session = bool(
            _auth_manager is not None and _auth_manager.validate_api_session(token)
        )
        if not valid_api_session and not validate_desktop_token(token):
            await ws.close(code=4401, reason="unauthorized")
            return

    await ws.accept()
    connection_id = str(id(ws))

    mgr = _channel_manager

    _desktop_adapter = mgr.adapters.get(ChannelType.DESKTOP) if mgr else None
    if _desktop_adapter is not None:
        _desktop_adapter.add_connection(connection_id, ws)
    _brain._desktop_connected = True

    # 确定 chat_id 并在 presence_ack 中发送给前端
    if DESKTOP_DEFAULT_OWNER and OWNER_IDS:
        chat_id = next(iter(OWNER_IDS))
    else:
        chat_id = f"{DESKTOP_WS_CHAT_ID_PREFIX}:{connection_id}"

    await ws.send_json({"type": "presence_ack", "status": "connected", "chat_id": chat_id})

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

                # 解析图片 segments（前端可通过 segments 字段发送图片）
                images: list[dict] | None = None
                segments = msg.get("segments")
                if isinstance(segments, list):
                    img_list = []
                    for seg in segments:
                        if seg.get("type") == "image":
                            data = seg.get("data", seg)
                            if "base64" in data:
                                img_list.append({
                                    "base64": data["base64"],
                                    "media_type": data.get("media_type", "image/jpeg"),
                                })
                            elif "url" in data:
                                img_list.append({"url": data["url"]})
                    if img_list:
                        images = img_list

                if not content and not images:
                    continue

                _chat_ws_map[chat_id] = ws

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
                    if _event_queue is None:
                        # No queue wired — should not happen at runtime;
                        # fail loudly so the integration gets fixed.
                        raise RuntimeError("event_queue not initialised on chat_ws")

                    done = asyncio.get_running_loop().create_future()
                    images_tuple = tuple(images) if images else ()
                    event_id = None
                    idempotency_key = None
                    try:
                        from config.settings import get_settings
                        flags = get_settings().concurrent_bg_work
                        if flags.enabled and flags.p1_ingress_correctness:
                            digest_source = f"desktop|{chat_id}|owner|{content}"
                            import hashlib
                            event_id = f"evt_{uuid.uuid4().hex}"
                            idempotency_key = "ingress:" + hashlib.sha256(
                                digest_source.encode("utf-8"),
                            ).hexdigest()[:32]
                    except Exception:
                        event_id = None
                        idempotency_key = None
                    event = MessageEvent.from_message(
                        chat_id=chat_id,
                        user_id="owner",
                        text=content,
                        adapter="desktop",
                        send_fn=send_fn,
                        auth_level=int(AuthLevel.OWNER),
                        images=images_tuple,
                        typing_fn=typing_fn,
                        status_callback=status_callback,
                        done_future=done,
                        event_id=event_id,
                        idempotency_key=idempotency_key,
                    )
                    await _event_queue.put(event)
                    await done  # propagate handler exception to except block
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
        # 清理 chat_id → ws 映射
        for cid, w in list(_chat_ws_map.items()):
            if w is ws:
                del _chat_ws_map[cid]
