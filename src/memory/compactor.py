"""对话压缩引擎 — 在滑动窗口即将溢出时生成摘要。"""

import asyncio
import logging
from datetime import datetime, timezone

from config.settings import (
    COMPACTION_SUMMARY_MAX_TOKENS,
    COMPACTION_TRIGGER_RATIO,
    CONVERSATION_SUMMARIES_DIR,
    MAX_HISTORY_TURNS,
)
from src.core.prompt_loader import load_prompt

logger = logging.getLogger("lapwing.memory.compactor")


class ConversationCompactor:
    """监控对话窗口，在接近上限时触发压缩。"""

    def __init__(self, memory, router):
        self._memory = memory
        self._router = router
        self._compacting: set[str] = set()
        CONVERSATION_SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)

    def should_compact(self, history_length: int) -> bool:
        """判断当前对话长度是否需要触发压缩。"""
        max_messages = MAX_HISTORY_TURNS * 2
        return history_length >= int(max_messages * COMPACTION_TRIGGER_RATIO)

    async def try_compact(self, chat_id: str, *, session_id: str | None = None) -> bool:
        """尝试压缩对话。返回是否执行了压缩。"""
        key = session_id or chat_id
        if key in self._compacting:
            return False

        if session_id is not None:
            history = await self._memory.get_session_messages(session_id)
        else:
            history = await self._memory.get(chat_id)
        if not self.should_compact(len(history)):
            return False

        self._compacting.add(key)
        try:
            return await self._do_compact(key, history, chat_id=chat_id, is_session=bool(session_id))
        finally:
            self._compacting.discard(key)

    async def _do_compact(
        self, key: str, history: list[dict], *, chat_id: str | None = None, is_session: bool = False
    ) -> bool:
        """执行压缩：摘要前半段对话，保留后半段。"""
        actual_chat_id = chat_id or key
        # 压缩前 60% 的消息，保留后 40%
        compact_count = int(len(history) * 0.6)
        if compact_count < 4:
            return False

        to_compact = history[:compact_count]
        to_keep = history[compact_count:]

        # 生成摘要
        conversation_text = "\n".join(
            f"{'用户' if m['role'] == 'user' else 'Lapwing'}: {m['content']}"
            for m in to_compact
        )

        prompt = load_prompt("compaction").replace("{conversation}", conversation_text)

        try:
            summary = await self._router.complete(
                [{"role": "user", "content": prompt}],
                purpose="tool",
                max_tokens=COMPACTION_SUMMARY_MAX_TOKENS,
                session_key=f"chat:{actual_chat_id}",
                origin="memory.compactor",
            )
            summary = summary.strip()
        except Exception as exc:
            logger.warning(f"[{actual_chat_id}] Compaction LLM 调用失败: {exc}")
            return False

        if not summary:
            return False

        # 写入摘要文件
        now = datetime.now(timezone.utc)
        filename = now.strftime("%Y-%m-%d_%H%M%S") + ".md"
        summary_path = CONVERSATION_SUMMARIES_DIR / filename
        await asyncio.to_thread(
            summary_path.write_text,
            f"# 对话摘要 {now.strftime('%Y-%m-%d %H:%M')}\n\n{summary}\n",
            encoding="utf-8",
        )

        # 更新内存中的对话历史：用摘要消息替换被压缩的部分
        summary_message = {
            "role": "system",
            "content": f"[之前的对话摘要] {summary}",
        }
        new_history = [summary_message] + to_keep

        # 替换内存缓存（不删除数据库中的旧记录，只更新缓存）
        if is_session:
            self._memory.replace_session_history(key, new_history)
        else:
            self._memory.replace_history(key, new_history)

        logger.info(
            f"[{actual_chat_id}] Compaction 完成：压缩 {compact_count} 条 → 保留 {len(to_keep)} 条 + 1 条摘要"
        )
        return True
