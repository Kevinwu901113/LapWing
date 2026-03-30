"""多通道管理器：注册、路由和消息分发。"""

from __future__ import annotations

import logging
from typing import Optional

from src.adapters.base import BaseAdapter, ChannelType

logger = logging.getLogger("lapwing.channel_manager")


class ChannelManager:
    """管理多个消息通道 Adapter 的注册与消息路由。"""

    def __init__(self) -> None:
        self.adapters: dict[ChannelType, BaseAdapter] = {}
        self.last_active_channel: Optional[ChannelType] = None

    def register(self, channel_type: ChannelType, adapter: BaseAdapter) -> None:
        self.adapters[channel_type] = adapter
        logger.info("已注册通道: %s", channel_type.value)

    async def start_all(self) -> None:
        for ch_type, adapter in self.adapters.items():
            await adapter.start()
            logger.info("通道已启动: %s", ch_type.value)

    async def stop_all(self) -> None:
        for ch_type, adapter in self.adapters.items():
            await adapter.stop()
            logger.info("通道已停止: %s", ch_type.value)

    async def send(self, channel: ChannelType, chat_id: str, text: str) -> None:
        adapter = self.adapters.get(channel)
        if adapter and await adapter.is_connected():
            await adapter.send_text(chat_id, text)

    async def send_to_kevin(self, text: str, prefer_channel: Optional[ChannelType] = None) -> None:
        """Heartbeat 主动消息：优先用指定通道，其次用最后活跃通道，最后 fallback。"""
        channel = prefer_channel or self.last_active_channel

        if channel and channel in self.adapters:
            adapter = self.adapters[channel]
            if await adapter.is_connected():
                kevin_id = adapter.config.get("kevin_id", "")
                if kevin_id:
                    await adapter.send_text(kevin_id, text)
                    return

        for ch_type, adapter in self.adapters.items():
            if await adapter.is_connected():
                kevin_id = adapter.config.get("kevin_id", "")
                if kevin_id:
                    await adapter.send_text(kevin_id, text)
                    return

        logger.warning("所有通道离线，主动消息未发送: %s", text[:50])
