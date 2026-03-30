"""QQ 通道适配器 — 通过 NapCat (OneBot v11) WebSocket 收发消息。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Awaitable, Callable, Optional

import websockets
from websockets.protocol import State as WsState

from src.adapters.base import BaseAdapter, ChannelType

logger = logging.getLogger("lapwing.adapter.qq")

MAX_QQ_MSG_LENGTH = 4000


class QQAdapter(BaseAdapter):
    """OneBot v11 WebSocket 客户端适配器。"""

    channel_type = ChannelType.QQ

    def __init__(
        self,
        config: dict,
        on_message: Optional[Callable[..., Awaitable[None]]] = None,
    ) -> None:
        super().__init__(config)
        self.on_message = on_message
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.ws_url: str = config.get("ws_url", "ws://127.0.0.1:3001")
        self.access_token: str = config.get("access_token", "")
        self.self_id: str = str(config.get("self_id", ""))
        self.kevin_id: str = str(config.get("kevin_id", ""))
        self._reconnect_delay = 5
        self._max_reconnect_delay = 300
        self._running = False
        self._echo_futures: dict[str, asyncio.Future] = {}
        self._message_dedup: dict[str, float] = {}
        self._connection_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        self._running = True
        self._connection_task = asyncio.create_task(self._connection_loop())
        logger.info("QQ adapter 启动中，连接 %s", self.ws_url)

    async def stop(self) -> None:
        self._running = False
        if self.ws:
            await self.ws.close()
        if self._connection_task and not self._connection_task.done():
            self._connection_task.cancel()
            try:
                await self._connection_task
            except asyncio.CancelledError:
                pass
        logger.info("QQ adapter 已停止")

    async def is_connected(self) -> bool:
        return self.ws is not None and self.ws.state == WsState.OPEN

    async def send_text(self, chat_id: str, text: str) -> None:
        text = self._markdown_to_plain(text)
        if len(text) <= MAX_QQ_MSG_LENGTH:
            await self._send_private_msg(chat_id, text)
        else:
            chunks = self._split_text(text, MAX_QQ_MSG_LENGTH)
            for chunk in chunks:
                await self._send_private_msg(chat_id, chunk)
                await asyncio.sleep(0.5)

    # ── WebSocket 连接管理 ──────────────────────────────

    async def _connection_loop(self) -> None:
        delay = self._reconnect_delay
        while self._running:
            try:
                headers = {}
                if self.access_token:
                    headers["Authorization"] = f"Bearer {self.access_token}"
                async with websockets.connect(
                    self.ws_url,
                    additional_headers=headers,
                    ping_interval=30,
                    ping_timeout=10,
                    close_timeout=5,
                    proxy=None,
                ) as ws:
                    self.ws = ws
                    delay = self._reconnect_delay
                    logger.info("QQ adapter 已连接")
                    await self._listen(ws)
            except (websockets.ConnectionClosed, ConnectionRefusedError, OSError) as exc:
                self.ws = None
                # Cancel orphaned API call futures
                for future in self._echo_futures.values():
                    if not future.done():
                        future.cancel()
                self._echo_futures.clear()
                if self._running:
                    logger.warning("QQ 连接断开 (%s)，%ds 后重连", exc, delay)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, self._max_reconnect_delay)

    async def _listen(self, ws) -> None:
        async for raw_msg in ws:
            try:
                data = json.loads(raw_msg)
                if "echo" in data:
                    echo = data["echo"]
                    future = self._echo_futures.get(echo)
                    if future and not future.done():
                        future.set_result(data)
                elif "post_type" in data:
                    await self._handle_event(data)
            except json.JSONDecodeError:
                logger.warning("QQ 收到无效 JSON: %s", raw_msg[:200])

    # ── 事件处理 ────────────────────────────────────────

    async def _handle_event(self, event: dict) -> None:
        post_type = event.get("post_type")
        if post_type == "meta_event":
            return
        if post_type == "message":
            await self._handle_message_event(event)

    async def _handle_message_event(self, event: dict) -> None:
        user_id = str(event.get("user_id", ""))
        message_id = str(event.get("message_id", ""))

        if user_id == self.self_id:
            return

        # 消息去重
        dedup_key = f"{user_id}:{message_id}"
        now = time.time()
        if dedup_key in self._message_dedup:
            return
        self._message_dedup[dedup_key] = now
        self._message_dedup = {k: v for k, v in self._message_dedup.items() if now - v < 60}

        # 只处理 Kevin 的消息
        if self.kevin_id and user_id != self.kevin_id:
            return

        text = self._extract_text(event)
        if not text:
            return

        if self.on_message:
            await self.on_message(
                chat_id=user_id,
                text=text,
                channel=ChannelType.QQ,
                raw_event=event,
            )

    # ── 消息解析 ────────────────────────────────────────

    def _extract_text(self, event: dict) -> str:
        message = event.get("message", "")
        if isinstance(message, str):
            return message
        if isinstance(message, list):
            parts = []
            for seg in message:
                if seg.get("type") == "text":
                    parts.append(seg.get("data", {}).get("text", ""))
            return "".join(parts).strip()
        return str(message)

    def _extract_image(self, event: dict) -> Optional[str]:
        message = event.get("message", [])
        if isinstance(message, list):
            for seg in message:
                if seg.get("type") == "image":
                    return seg.get("data", {}).get("url", "")
        return None

    # ── 发送消息 ────────────────────────────────────────

    async def _send_private_msg(self, user_id: str, text: str) -> dict:
        try:
            numeric_id = int(user_id)
        except ValueError:
            logger.warning("QQ user_id 非数字: %s", user_id)
            return {"status": "failed", "retcode": -3}
        return await self._call_api("send_private_msg", {
            "user_id": numeric_id,
            "message": self._build_message_segments(text, None),
        })

    def _build_message_segments(self, text: str, image_base64: Optional[str] = None) -> list:
        segments = []
        if text:
            segments.append({"type": "text", "data": {"text": text}})
        if image_base64:
            segments.append({"type": "image", "data": {"file": f"base64://{image_base64}"}})
        return segments

    async def _call_api(self, action: str, params: dict, timeout: float = 30.0) -> dict:
        if not self.ws or not self.ws.state == WsState.OPEN:
            return {"status": "failed", "retcode": -1}

        echo = f"{action}_{time.time()}"
        request = {"action": action, "params": params, "echo": echo}
        future = asyncio.get_running_loop().create_future()
        self._echo_futures[echo] = future

        try:
            await self.ws.send(json.dumps(request))
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            logger.warning("QQ API 超时: %s", action)
            return {"status": "failed", "retcode": -2}
        finally:
            self._echo_futures.pop(echo, None)

    # ── 格式转换 ────────────────────────────────────────

    def _markdown_to_plain(self, text: str) -> str:
        # 代码块必须在行内代码之前处理
        text = re.sub(r'```\w*\n?', '', text)
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'__(.+?)__', r'\1', text)
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        text = re.sub(r'_(.+?)_', r'\1', text)
        text = re.sub(r'`(.+?)`', r'\1', text)
        text = re.sub(r'\[(.+?)\]\((.+?)\)', r'\1 (\2)', text)
        return text

    def _split_text(self, text: str, max_length: int) -> list[str]:
        if len(text) <= max_length:
            return [text]
        chunks = []
        remaining = text
        while remaining:
            if len(remaining) <= max_length:
                chunks.append(remaining)
                break
            split_at = remaining.rfind("\n", 0, max_length)
            if split_at <= 0:
                split_at = max_length
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:].lstrip("\n")
        return chunks
