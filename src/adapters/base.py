"""消息通道适配器抽象基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum


class ChannelType(Enum):
    TELEGRAM = "telegram"
    QQ = "qq"


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

    @abstractmethod
    async def send_text(self, chat_id: str, text: str) -> None:
        """发送文本消息到指定 chat_id。"""

    @abstractmethod
    async def is_connected(self) -> bool:
        """检查连接状态。"""
