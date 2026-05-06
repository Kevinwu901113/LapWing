"""消息通道适配器抽象基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from src.models.message import RichMessage


class ChannelType(Enum):
    QQ = "qq"
    DESKTOP = "desktop"


@dataclass(frozen=True)
class AdapterCapabilities:
    can_send_private: bool
    can_send_group: bool
    can_receive_typing_indicator: bool = False
    can_send_rich_media: bool = False
    can_handle_voice: bool = False
    supports_message_edit: bool = False
    supports_reply_reference: bool = False


@dataclass(frozen=True)
class NormalizedInboundMessage:
    channel: str
    chat_id: str
    user_id: str
    text: str
    message_id: str
    message_type: str = "private"
    group_id: str | None = None
    raw_event: dict[str, Any] = field(default_factory=dict)
    image_urls: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class BaseAdapter(ABC):
    """所有消息通道 Adapter 的基类。"""

    channel_type: ChannelType
    capabilities = AdapterCapabilities(
        can_send_private=False,
        can_send_group=False,
    )

    def __init__(self, config: dict) -> None:
        self.config = config

    @abstractmethod
    async def start(self) -> None:
        """启动 Adapter，建立连接，开始监听。"""

    @abstractmethod
    async def stop(self) -> None:
        """停止 Adapter，断开连接。"""

    async def send_text(self, chat_id: str, text: str) -> None:
        """发送纯文本消息（默认 delegate 到 send_message）。子类可 override 以优化。"""
        await self.send_message(chat_id, RichMessage.from_text(text))

    def normalize_inbound(self, raw_event: dict[str, Any]) -> NormalizedInboundMessage | None:
        """Normalize raw channel payloads before trust/command/busy gates.

        Adapters remain event producers: this method only shapes data and must
        never call Brain or enqueue directly.
        """
        return None

    @abstractmethod
    async def send_message(self, chat_id: str, message: RichMessage) -> None:
        """发送富媒体消息到指定 chat_id。各子类必须实现。"""

    @abstractmethod
    async def is_connected(self) -> bool:
        """检查连接状态。"""
