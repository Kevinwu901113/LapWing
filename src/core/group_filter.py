"""群聊消息预过滤器 — 用轻量 LLM 判断消息是否需要进入 Lapwing 上下文。"""

from __future__ import annotations

import hashlib
import logging
import time
import unicodedata

logger = logging.getLogger("lapwing.core.group_filter")

# 决策结果常量
DECISION_ENTER = "enter"
DECISION_CACHE = "cache"
DECISION_DISCARD = "discard"

_VALID_DECISIONS = {DECISION_ENTER, DECISION_CACHE, DECISION_DISCARD}


def _is_emoji_only(text: str) -> bool:
    """判断文本是否仅由表情符号和空白组成。"""
    stripped = text.strip()
    if not stripped:
        return True
    for ch in stripped:
        cat = unicodedata.category(ch)
        # So: Other Symbol（大多数 emoji）、Cf: Format、Zs: Space Separator
        if cat not in ("So", "Cf", "Zs") and not ch.isspace():
            return False
    return True


class GroupMessageFilter:
    """群聊消息预过滤。用轻量 LLM 判断是否需要进入 Lapwing 上下文。

    决策结果：
      - enter:   进入 Lapwing 上下文让她决定是否回复
      - cache:   缓存，意识循环时可浏览
      - discard: 丢弃（垃圾消息、纯表情等）
    """

    MAX_INPUT_TOKENS = 500
    MAX_OUTPUT_TOKENS = 50

    # 消息内容截断长度
    _MSG_PREVIEW_LEN = 300

    def __init__(self, llm_router) -> None:
        self.llm_router = llm_router
        # 缓存近期决策，避免对相似消息重复调用 LLM
        # 格式：{content_hash: (timestamp, decision)}
        self._cache: dict[str, tuple[float, str]] = {}
        self._cache_ttl: float = 60.0  # 秒

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    async def filter(
        self,
        message: str,
        sender_name: str,
        is_at_me: bool,
        active_tasks: list[str] | None = None,
    ) -> str:
        """判断群聊消息的处理方式。

        Returns:
            "enter" / "cache" / "discard"
        """
        # ── 快速路径（无需 LLM）──────────────────────────────────────
        # 1. @提到 Lapwing → 必须进入上下文
        if is_at_me:
            logger.debug("group_filter: at_me → enter | sender=%s", sender_name)
            return DECISION_ENTER

        # 2. 空消息或纯表情 → 丢弃
        if not message or not message.strip():
            logger.debug("group_filter: empty → discard")
            return DECISION_DISCARD

        if _is_emoji_only(message):
            logger.debug("group_filter: emoji_only → discard | sender=%s", sender_name)
            return DECISION_DISCARD

        # 3. 极短消息（< 3 字符，无 @ 提及）→ 丢弃
        if len(message.strip()) < 3:
            logger.debug("group_filter: too_short → discard | sender=%s", sender_name)
            return DECISION_DISCARD

        # ── 缓存命中检查 ────────────────────────────────────────────
        cache_key = self._make_cache_key(message, sender_name)
        cached = self._get_cached(cache_key)
        if cached is not None:
            logger.debug(
                "group_filter: cache_hit=%s | sender=%s", cached, sender_name
            )
            return cached

        # ── LLM 判断 ─────────────────────────────────────────────────
        decision = await self._call_llm(message, sender_name, active_tasks)
        self._set_cache(cache_key, decision)
        logger.debug(
            "group_filter: llm_decision=%s | sender=%s", decision, sender_name
        )
        return decision

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _make_cache_key(self, message: str, sender_name: str) -> str:
        """生成消息缓存键（内容 + 发送者哈希）。"""
        raw = f"{sender_name}:{message[:200]}"
        return hashlib.md5(raw.encode("utf-8", errors="replace")).hexdigest()

    def _get_cached(self, key: str) -> str | None:
        """返回缓存决策，已过期则清除并返回 None。"""
        entry = self._cache.get(key)
        if entry is None:
            return None
        ts, decision = entry
        if time.monotonic() - ts > self._cache_ttl:
            del self._cache[key]
            return None
        return decision

    def _set_cache(self, key: str, decision: str) -> None:
        """写入缓存，同时做简单的大小限制（最多 200 条）。"""
        if len(self._cache) >= 200:
            # 删除最旧的一半
            sorted_keys = sorted(self._cache, key=lambda k: self._cache[k][0])
            for old_key in sorted_keys[:100]:
                del self._cache[old_key]
        self._cache[key] = (time.monotonic(), decision)

    def _build_prompt(
        self,
        message: str,
        sender_name: str,
        active_tasks: list[str] | None,
    ) -> str:
        """构造发给 LLM 的判断提示词。"""
        preview = message[: self._MSG_PREVIEW_LEN]

        tasks_section = ""
        if active_tasks:
            tasks_list = "、".join(active_tasks[:5])
            tasks_section = f"\nLapwing 当前正在处理的任务：{tasks_list}"

        return (
            "判断这条群聊消息是否需要 Lapwing 关注。\n\n"
            f"消息来自：{sender_name}\n"
            f"内容：{preview}"
            f"{tasks_section}\n\n"
            "回答一个词：enter（需要关注）、cache（可以稍后看）、discard（不相关）"
        )

    async def _call_llm(
        self,
        message: str,
        sender_name: str,
        active_tasks: list[str] | None,
    ) -> str:
        """调用轻量 LLM 做决策，失败时返回安全默认值 "cache"。"""
        prompt = self._build_prompt(message, sender_name, active_tasks)
        try:
            text = await self.llm_router.simple_completion(
                prompt,
                purpose="lightweight_judgment",
                max_tokens=self.MAX_OUTPUT_TOKENS,
            )
            return self._parse_decision(text)
        except Exception:
            logger.warning(
                "group_filter: LLM 调用失败，默认返回 cache | sender=%s",
                sender_name,
                exc_info=True,
            )
            return DECISION_CACHE

    @staticmethod
    def _parse_decision(text: str) -> str:
        """从 LLM 返回文本中提取决策词，无法识别时返回 "cache"。"""
        if not text:
            return DECISION_CACHE
        lower = text.strip().lower()
        for decision in _VALID_DECISIONS:
            if decision in lower:
                return decision
        logger.debug("group_filter: 无法识别 LLM 返回值 %r，默认 cache", text[:80])
        return DECISION_CACHE
