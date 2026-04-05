"""Lapwing 统一消息模型。

一条消息由一个或多个 MessageSegment 组成。
各平台 adapter 负责把 MessageSegment 翻译成平台特定的格式。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SegmentType(str, Enum):
    TEXT = "text"
    IMAGE = "image"


@dataclass(frozen=True)
class MessageSegment:
    type: SegmentType
    data: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def text(content: str) -> MessageSegment:
        return MessageSegment(type=SegmentType.TEXT, data={"text": content})

    @staticmethod
    def image(
        *,
        url: str | None = None,
        base64: str | None = None,
        path: str | None = None,
    ) -> MessageSegment:
        """创建图片消息段。

        Args:
            url:    网络 URL（http/https），所有平台通用。
            base64: Base64 编码的图片数据（不含 data: 前缀）。
            path:   服务器本地绝对路径。
        """
        d: dict[str, Any] = {}
        if url:
            d["url"] = url
        if base64:
            d["base64"] = base64
        if path:
            d["path"] = path
        return MessageSegment(type=SegmentType.IMAGE, data=d)


@dataclass
class RichMessage:
    """一条完整的富媒体消息，由多个 segment 组成。"""

    segments: list[MessageSegment] = field(default_factory=list)

    def add_text(self, content: str) -> RichMessage:
        self.segments.append(MessageSegment.text(content))
        return self

    def add_image(
        self,
        *,
        url: str | None = None,
        base64: str | None = None,
        path: str | None = None,
    ) -> RichMessage:
        self.segments.append(MessageSegment.image(url=url, base64=base64, path=path))
        return self

    @staticmethod
    def from_text(content: str) -> RichMessage:
        """从纯文本创建（向后兼容）。"""
        return RichMessage(segments=[MessageSegment.text(content)])

    @property
    def plain_text(self) -> str:
        """提取所有文本段拼接，用于日志、记忆等只需文字的场景。"""
        return "".join(
            seg.data.get("text", "")
            for seg in self.segments
            if seg.type == SegmentType.TEXT
        )

    @property
    def has_media(self) -> bool:
        return any(seg.type != SegmentType.TEXT for seg in self.segments)
