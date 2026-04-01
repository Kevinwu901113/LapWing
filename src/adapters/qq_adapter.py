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
from src.adapters.qq_group_context import GroupContext, GroupMessage
from src.adapters.qq_group_filter import GroupEngagementDecider
from src.core.prompt_loader import load_prompt

logger = logging.getLogger("lapwing.adapters.qq_adapter")

MAX_QQ_MSG_LENGTH = 4000

# QQ 表情 ID 映射（常用子集）
QQ_FACE_MAP: dict[str, str] = {
    "[微笑]": "14", "[撇嘴]": "1", "[色]": "2", "[发呆]": "3",
    "[得意]": "4", "[流泪]": "5", "[害羞]": "6", "[闭嘴]": "7",
    "[大哭]": "9", "[尴尬]": "10", "[发怒]": "11", "[调皮]": "12",
    "[呲牙]": "13", "[惊讶]": "0", "[难过]": "15", "[酷]": "16",
    "[抓狂]": "18", "[吐]": "19", "[偷笑]": "20", "[可爱]": "21",
    "[白眼]": "22", "[傲慢]": "23", "[饥饿]": "24", "[困]": "25",
    "[惊恐]": "26", "[流汗]": "27", "[憨笑]": "28", "[悠闲]": "29",
    "[奋斗]": "30", "[咒骂]": "31", "[疑问]": "32", "[嘘]": "33",
    "[晕]": "34", "[敲打]": "35", "[再见]": "36", "[抠鼻]": "53",
    "[鼓掌]": "47", "[坏笑]": "50", "[右哼哼]": "52",
    "[鄙视]": "49", "[委屈]": "55", "[亲亲]": "57",
    "[可怜]": "58", "[笑哭]": "182", "[doge]": "179",
    "[OK]": "324", "[爱心]": "66", "[心碎]": "67",
    "[强]": "76", "[弱]": "77",
    "[握手]": "78", "[胜利]": "79",
}


class QQAdapter(BaseAdapter):
    """OneBot v11 WebSocket 客户端适配器。"""

    channel_type = ChannelType.QQ
    _FACE_ID_TO_NAME: dict[str, str] | None = None

    @classmethod
    def _face_id_to_name(cls, face_id: str) -> str:
        if cls._FACE_ID_TO_NAME is None:
            cls._FACE_ID_TO_NAME = {v: k for k, v in QQ_FACE_MAP.items()}
        return cls._FACE_ID_TO_NAME.get(face_id, "")

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

        # Group chat
        self._allowed_groups: set[str] = set(config.get("group_ids", []))
        self._group_contexts: dict[str, GroupContext] = {}
        self._decider: GroupEngagementDecider | None = None
        if self._allowed_groups:
            self._decider = GroupEngagementDecider(
                self_id=self.self_id,
                self_names=config.get("self_names", ["Lapwing", "lapwing"]),
                kevin_id=self.kevin_id,
                cooldown_seconds=config.get("group_cooldown", 30),
            )
        self._group_context_size: int = config.get("group_context_size", 30)
        self.router = None  # Injected by main.py for group engagement decisions

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
        message_type = event.get("message_type")

        if user_id == self.self_id:
            return

        # 消息去重
        dedup_key = f"{user_id}:{message_id}"
        now = time.time()
        if dedup_key in self._message_dedup:
            return
        self._message_dedup[dedup_key] = now
        self._message_dedup = {k: v for k, v in self._message_dedup.items() if now - v < 60}

        text = self._extract_text(event)

        if message_type == "private":
            # Private: Kevin only (existing logic)
            if self.kevin_id and user_id != self.kevin_id:
                return
            if not text:
                return
            asyncio.create_task(self._mark_as_read(user_id))
            if self.on_message:
                # 必须用 create_task 而非 await：on_message 内部会调用
                # send_text → _call_api，后者需要 _listen 循环继续运转
                # 来接收 echo 响应；若 await 则会死锁。
                asyncio.create_task(self.on_message(
                    chat_id=user_id,
                    text=text,
                    channel=ChannelType.QQ,
                    raw_event=event,
                ))

        elif message_type == "group":
            group_id = str(event.get("group_id", ""))
            if group_id not in self._allowed_groups:
                return

            ctx = self._get_group_context(group_id)
            is_at_self = self._check_at_self(event)
            is_reply_to_self = self._check_reply_to_self(event, ctx)
            nickname = self._get_sender_nickname(event)

            group_msg = GroupMessage(
                message_id=message_id,
                user_id=user_id,
                nickname=nickname,
                text=text or "(非文本消息)",
                timestamp=now,
                is_at_self=is_at_self,
                is_reply_to_self=is_reply_to_self,
            )
            ctx.add_message(group_msg)

            if not text:
                return
            if self._decider is None:
                return

            asyncio.create_task(self._evaluate_and_engage(ctx, group_msg))

    async def _mark_as_read(self, user_id: str) -> None:
        """标记私聊消息已读。"""
        try:
            await self._call_api("mark_private_msg_as_read", {
                "user_id": int(user_id),
            })
        except Exception:
            pass  # 非关键操作，失败不影响主流程

    # ── 群聊辅助 ─────────────────────────────────────────

    def _get_group_context(self, group_id: str) -> GroupContext:
        if group_id not in self._group_contexts:
            self._group_contexts[group_id] = GroupContext(group_id=group_id)
        return self._group_contexts[group_id]

    def _check_at_self(self, event: dict) -> bool:
        message = event.get("message", [])
        if isinstance(message, list):
            for seg in message:
                if seg.get("type") == "at" and str(seg.get("data", {}).get("qq", "")) == self.self_id:
                    return True
        return False

    def _check_reply_to_self(self, event: dict, ctx: GroupContext) -> bool:
        message = event.get("message", [])
        if isinstance(message, list):
            for seg in message:
                if seg.get("type") == "reply":
                    reply_id = str(seg.get("data", {}).get("id", ""))
                    if reply_id in ctx.my_recent_message_ids:
                        return True
        return False

    def _get_sender_nickname(self, event: dict) -> str:
        sender = event.get("sender", {})
        return sender.get("card", "") or sender.get("nickname", "") or str(event.get("user_id", ""))

    # ── 群聊参与决策与执行 ───────────────────────────────

    async def _evaluate_and_engage(self, ctx: "GroupContext", msg: "GroupMessage") -> None:
        """Run LLM engagement decision, then handle if appropriate."""
        if self._decider is None:
            return
        should_engage, reason = await self._decider.should_engage(msg, ctx)
        if not should_engage:
            return
        await self._handle_group_engagement(ctx, msg, reason)

    async def _handle_group_engagement(
        self, ctx: GroupContext, msg: GroupMessage, reason: str
    ) -> None:
        """Tier-2: ask Brain whether and how to participate."""
        action, content = await self._decide_group_engagement(ctx, reason)

        if action == "SKIP":
            return

        if action == "REACT":
            await self._react_to_message(msg.message_id, content)

        elif action == "REPLY":
            if msg.is_at_self or msg.is_reply_to_self:
                result = await self._send_group_reply(ctx.group_id, content, msg.message_id)
            else:
                result = await self._send_group_msg(ctx.group_id, content)

            resp_msg_id = str(result.get("data", {}).get("message_id", ""))
            if resp_msg_id:
                ctx.record_my_message(resp_msg_id)
            ctx.last_reply_time = time.time()

    async def _decide_group_engagement(
        self, ctx: GroupContext, trigger_reason: str
    ) -> tuple[str, str]:
        """Call LLM to decide group participation. Returns (action, content)."""
        if self.router is None:
            return "SKIP", ""

        prompt_template = load_prompt("group_engage_decision")
        prompt = prompt_template.format(
            group_context=ctx.format_for_prompt(n=self._group_context_size),
            trigger_reason=trigger_reason,
        )

        try:
            response = await self.router.complete(
                messages=[{"role": "user", "content": prompt}],
                slot="lightweight_judgment",
                max_tokens=200,
            )
        except Exception as exc:
            logger.warning("Group engagement decision failed: %s", exc)
            return "SKIP", ""

        text = response.strip()
        if text.startswith("SKIP"):
            return "SKIP", ""
        if text.startswith("REACT"):
            emoji_id = text.replace("REACT", "", 1).strip()
            return "REACT", emoji_id
        if text.startswith("REPLY"):
            reply = text.replace("REPLY", "", 1).strip()
            return "REPLY", reply
        return "SKIP", ""

    # ── 群聊发送 ────────────────────────────────────────

    async def _send_group_msg(self, group_id: str, text: str) -> dict:
        text_plain = self._markdown_to_plain(text)
        segments = self._build_message_segments(text_plain)
        return await self._call_api("send_group_msg", {
            "group_id": int(group_id),
            "message": segments,
        })

    async def _send_group_reply(self, group_id: str, text: str, reply_to_id: str) -> dict:
        text_plain = self._markdown_to_plain(text)
        segments: list[dict] = [{"type": "reply", "data": {"id": reply_to_id}}]
        segments.extend(self._build_message_segments(text_plain))
        return await self._call_api("send_group_msg", {
            "group_id": int(group_id),
            "message": segments,
        })

    async def _react_to_message(self, message_id: str, emoji_id: str) -> None:
        try:
            await self._call_api("set_msg_emoji_like", {
                "message_id": message_id,
                "emoji_id": emoji_id,
            })
        except Exception:
            pass  # Non-critical

    # ── 消息解析 ────────────────────────────────────────

    def _extract_text(self, event: dict) -> str:
        message = event.get("message", "")
        if isinstance(message, str):
            return message.strip()
        if isinstance(message, list):
            parts: list[str] = []
            for seg in message:
                seg_type = seg.get("type")
                data = seg.get("data", {})
                if seg_type == "text":
                    parts.append(data.get("text", ""))
                elif seg_type == "face":
                    face_name = self._face_id_to_name(str(data.get("id", "")))
                    if face_name:
                        parts.append(face_name)
            return "".join(parts).strip()
        return str(message).strip()

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

    async def _send_private_msg_segments(self, user_id: str, segments: list) -> dict:
        """发送由调用方预构建的消息段到私聊。"""
        try:
            numeric_id = int(user_id)
        except ValueError:
            logger.warning("QQ user_id 非数字: %s", user_id)
            return {"status": "failed", "retcode": -3}
        return await self._call_api("send_private_msg", {
            "user_id": numeric_id,
            "message": segments,
        })

    async def send_reply(self, chat_id: str, text: str, reply_to_message_id: str) -> None:
        """发送带引用的回复消息。"""
        text_plain = self._markdown_to_plain(text)
        segments: list[dict] = [{"type": "reply", "data": {"id": reply_to_message_id}}]
        segments.extend(self._build_message_segments(text_plain))
        await self._send_private_msg_segments(chat_id, segments)

    async def poke(self, user_id: str) -> None:
        """好友戳一戳 (friend poke)。"""
        try:
            await self._call_api("friend_poke", {"user_id": int(user_id)})
        except Exception:
            pass

    def _build_message_segments(self, text: str, image_base64: str | None = None) -> list:
        segments: list[dict] = []
        if text:
            parts = re.split(r'(\[[^\[\]]+\])', text)
            for part in parts:
                if not part:
                    continue
                face_id = QQ_FACE_MAP.get(part)
                if face_id is not None:
                    segments.append({"type": "face", "data": {"id": face_id}})
                else:
                    segments.append({"type": "text", "data": {"text": part}})
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
        # 清理连续空行（3+ 个换行 → 2 个）并 strip 首尾空白
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

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
