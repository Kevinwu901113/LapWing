"""消息通道适配器抽象基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum

from src.models.message import RichMessage


class ChannelType(Enum):
    TELEGRAM = "telegram"
    QQ = "qq"
    DESKTOP = "desktop"


class BaseAdapter(ABC):
    """所有消息通道 Adapter 的基类。"""

    channel_type: ChannelType

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

    @abstractmethod
    async def send_message(self, chat_id: str, message: RichMessage) -> None:
        """发送富媒体消息到指定 chat_id。各子类必须实现。"""

    @abstractmethod
    async def is_connected(self) -> bool:
        """检查连接状态。"""
