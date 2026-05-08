"""桌面端 WebSocket 通道适配器。"""

from __future__ import annotations

import logging

from src.adapters.base import AdapterCapabilities, BaseAdapter, ChannelType
from src.models.message import RichMessage, SegmentType

logger = logging.getLogger("lapwing.adapters.desktop_adapter")


class DesktopChannelAdapter(BaseAdapter):
    """将活跃的桌面 WebSocket 连接封装为 ChannelManager 可路由的通道。

    连接生命周期由 API server 的 /ws/chat 端点管理：
    连接建立时调用 add_connection()，断开时调用 remove_connection()。
    """

    channel_type = ChannelType.DESKTOP
    capabilities = AdapterCapabilities(
        can_send_private=True,
        can_send_group=False,
        can_receive_typing_indicator=True,
        can_send_rich_media=True,
    )

    def __init__(self) -> None:
        super().__init__(config={"kevin_id": "owner"})
        self.connections: dict[str, object] = {}  # connection_id → WebSocket

    def add_connection(self, connection_id: str, ws: object) -> None:
        self.connections[connection_id] = ws
        logger.info("Desktop 连接已添加: %s（共 %d 个）", connection_id, len(self.connections))

    def remove_connection(self, connection_id: str) -> None:
        self.connections.pop(connection_id, None)
        logger.info("Desktop 连接已移除: %s（剩余 %d 个）", connection_id, len(self.connections))

    async def start(self) -> None:
        pass  # 生命周期由 API server 管理

    async def stop(self) -> None:
        self.connections.clear()

    async def is_connected(self) -> bool:
        return bool(self.connections)

    async def send_text(self, chat_id: str, text: str) -> None:
        """向所有活跃桌面连接推送主动文本消息（保持旧协议格式）。"""
        dead = []
        delivered = 0
        for cid, ws in list(self.connections.items()):
            try:
                await ws.send_json({"type": "proactive", "content": text})
                delivered += 1
            except Exception as exc:
                logger.warning("Desktop 推送失败 [%s]: %s", cid, exc)
                dead.append(cid)
        for cid in dead:
            self.connections.pop(cid, None)
        if delivered == 0:
            raise RuntimeError("Desktop 推送失败：没有连接成功接收消息")

    async def send_message(self, chat_id: str, message: RichMessage) -> None:
        """向所有活跃桌面连接推送富媒体消息（WebSocket 协议 v2）。

        格式：{"type": "message", "segments": [{"type": "text", "content": "..."}, ...]}
        """
        ws_segments = []
        for seg in message.segments:
            if seg.type == SegmentType.TEXT:
                ws_segments.append({"type": "text", "content": seg.data.get("text", "")})
            elif seg.type == SegmentType.IMAGE:
                ws_data: dict = {"type": "image"}
                if seg.data.get("url"):
                    ws_data["url"] = seg.data["url"]
                elif seg.data.get("base64"):
                    ws_data["base64"] = seg.data["base64"]
                elif seg.data.get("path"):
                    ws_data["path"] = seg.data["path"]
                ws_segments.append(ws_data)

        payload = {"type": "message", "segments": ws_segments}
        dead = []
        delivered = 0
        for cid, ws in list(self.connections.items()):
            try:
                await ws.send_json(payload)
                delivered += 1
            except Exception as exc:
                logger.warning("Desktop 推送失败 [%s]: %s", cid, exc)
                dead.append(cid)
        for cid in dead:
            self.connections.pop(cid, None)
        if delivered == 0:
            raise RuntimeError("Desktop 推送失败：没有连接成功接收消息")
