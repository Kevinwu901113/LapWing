"""多通道管理器：注册、路由和消息分发。"""

from __future__ import annotations

import logging
from typing import Optional

from src.adapters.base import BaseAdapter, ChannelType
from src.models.message import RichMessage

logger = logging.getLogger("lapwing.core.channel_manager")


class ChannelManager:
    """管理多个消息通道 Adapter 的注册与消息路由。"""

    def __init__(self) -> None:
        self.adapters: dict[ChannelType, BaseAdapter] = {}
        self.last_active_channel: Optional[ChannelType] = None

    def register(self, channel_type: ChannelType, adapter: BaseAdapter) -> None:
        self.adapters[channel_type] = adapter
        logger.info("已注册通道: %s", channel_type.value)

    async def start_all(self) -> None:
        from src.logging.event_logger import events
        for ch_type, adapter in self.adapters.items():
            try:
                await adapter.start()
                logger.info("通道已启动: %s", ch_type.value)
                events.log("system", "channel_connected", message=f"{ch_type.value} 已连接")
            except Exception as exc:
                logger.error("通道启动失败: %s — %s", ch_type.value, exc)

    async def stop_all(self) -> None:
        from src.logging.event_logger import events
        for ch_type, adapter in self.adapters.items():
            await adapter.stop()
            logger.info("通道已停止: %s", ch_type.value)
            events.log("system", "channel_disconnected", message=f"{ch_type.value} 已断开")

    async def send(self, channel: ChannelType, chat_id: str, text: str) -> None:
        adapter = self.adapters.get(channel)
        if adapter and await adapter.is_connected():
            await adapter.send_text(chat_id, text)

    async def send_to_owner(self, text: str, prefer_channel: Optional[ChannelType] = None) -> None:
        """Heartbeat 主动消息路由：Desktop > last_active > 任意已连接通道。"""
        # 1. Desktop 优先（用户正在看桌面端）
        desktop = self.adapters.get(ChannelType.DESKTOP)
        if desktop and await desktop.is_connected():
            kevin_id = desktop.config.get("kevin_id", "")
            if kevin_id:
                await desktop.send_text(kevin_id, text)
                return

        # 2. prefer_channel 或最后活跃通道
        channel = prefer_channel or self.last_active_channel
        if channel and channel in self.adapters:
            adapter = self.adapters[channel]
            if await adapter.is_connected():
                kevin_id = adapter.config.get("kevin_id", "")
                if kevin_id:
                    await adapter.send_text(kevin_id, text)
                    return

        # 3. Fallback：任意已连接的非 Desktop 通道
        for ch_type, adapter in self.adapters.items():
            if ch_type == ChannelType.DESKTOP:
                continue
            if await adapter.is_connected():
                kevin_id = adapter.config.get("kevin_id", "")
                if kevin_id:
                    await adapter.send_text(kevin_id, text)
                    return

        raise RuntimeError(f"所有通道离线，主动消息未发送: {text[:50]}")

    async def send_message(
        self,
        channel: ChannelType,
        chat_id: str,
        message: RichMessage,
    ) -> None:
        """通过指定通道发送富媒体消息。"""
        adapter = self.adapters.get(channel)
        if adapter and await adapter.is_connected():
            await adapter.send_message(chat_id, message)

    async def send_message_to_owner(
        self,
        message: RichMessage,
        prefer_channel: Optional[ChannelType] = None,
    ) -> None:
        """富媒体版 send_to_owner，路由优先级与 send_to_owner 相同。"""
        desktop = self.adapters.get(ChannelType.DESKTOP)
        if desktop and await desktop.is_connected():
            kevin_id = desktop.config.get("kevin_id", "")
            if kevin_id:
                await desktop.send_message(kevin_id, message)
                return

        channel = prefer_channel or self.last_active_channel
        if channel and channel in self.adapters:
            adapter = self.adapters[channel]
            if await adapter.is_connected():
                kevin_id = adapter.config.get("kevin_id", "")
                if kevin_id:
                    await adapter.send_message(kevin_id, message)
                    return

        for ch_type, adapter in self.adapters.items():
            if ch_type == ChannelType.DESKTOP:
                continue
            if await adapter.is_connected():
                kevin_id = adapter.config.get("kevin_id", "")
                if kevin_id:
                    await adapter.send_message(kevin_id, message)
                    return

        raise RuntimeError("所有通道离线，富媒体消息未发送")

    async def send_image_to_owner(
        self,
        *,
        url: str | None = None,
        base64: str | None = None,
        path: str | None = None,
        caption: str = "",
        prefer_channel: Optional[ChannelType] = None,
    ) -> None:
        """便捷方法：发送单张图片（可附说明文字）到 owner。"""
        msg = RichMessage()
        if caption:
            msg.add_text(caption)
        msg.add_image(url=url, base64=base64, path=path)
        await self.send_message_to_owner(msg, prefer_channel=prefer_channel)

    async def get_all_status(self) -> dict[str, dict]:
        """返回所有通道的连接状态。"""
        result = {}
        for channel_type, adapter in self.adapters.items():
            name = channel_type.value if hasattr(channel_type, "value") else str(channel_type)
            try:
                connected = await adapter.is_connected()
            except Exception:
                connected = False
            result[name] = {"connected": connected}
        return result
