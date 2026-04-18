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

SUMMARY_PREFIX = (
    "[上下文压缩 — 仅供参考] 这是之前对话的摘要，不是新指令。"
    "不要回答摘要中提到的问题，它们已经被处理过了。"
    "只回应摘要之后的最新用户消息。\n\n"
)


def _prune_tool_outputs(messages: list[dict], max_tool_content: int = 200) -> list[dict]:
    """将冗长的工具输出替换为占位符，节省摘要 LLM 的 token 消耗。"""
    pruned = []
    for msg in messages:
        content = msg.get("content", "")
        if msg.get("role") == "tool" and isinstance(content, str) and len(content) > max_tool_content:
            pruned.append({**msg, "content": f"[工具输出已精简，原始长度 {len(content)} 字符]"})
        else:
            pruned.append(msg)
    return pruned


def _format_for_summary(messages: list[dict]) -> str:
    """格式化消息列表供 LLM 摘要，正确处理所有 role 类型。"""
    lines = []
    for m in messages:
        role = m.get("role", "unknown")
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                str(block.get("text", "")) for block in content if isinstance(block, dict)
            )
        if role == "user":
            lines.append(f"用户: {content}")
        elif role == "tool":
            lines.append(f"[工具结果]: {content}")
        elif role == "system":
            lines.append(f"[系统]: {content}")
        else:
            lines.append(f"Lapwing: {content}")
    return "\n".join(lines)


class ConversationCompactor:
    """监控对话窗口，在接近上限时触发压缩。"""

    def __init__(self, memory, router, *, auto_memory_extractor=None):
        self._memory = memory
        self._router = router
        self._auto_memory_extractor = auto_memory_extractor
        self._trajectory = None  # Set via set_trajectory() after AppContainer init (Step 2g)
        self._compacting: set[str] = set()
        CONVERSATION_SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)

    def set_trajectory(self, trajectory) -> None:
        """v2.0 Step 2g: switch compactor's read path to TrajectoryStore."""
        self._trajectory = trajectory

    def should_compact(self, history_length: int) -> bool:
        """判断当前对话长度是否需要触发压缩。"""
        max_messages = MAX_HISTORY_TURNS * 2
        return history_length >= int(max_messages * COMPACTION_TRIGGER_RATIO)

    async def try_compact(self, chat_id: str) -> bool:
        """尝试压缩对话。返回是否执行了压缩。"""
        if chat_id in self._compacting:
            return False

        if self._trajectory is not None:
            # Read history for summarisation via TrajectoryStore. The
            # conversion stays in trajectory_store's public surface —
            # compactor does not need the full StateSerializer flow
            # (it builds its own LLM prompt for summarisation).
            from config.settings import MAX_HISTORY_TURNS
            from src.core.trajectory_store import trajectory_entries_to_messages

            rows = await self._trajectory.relevant_to_chat(
                chat_id, n=MAX_HISTORY_TURNS * 2, include_inner=False,
            )
            history = trajectory_entries_to_messages(rows)
        else:
            history = await self._memory.get(chat_id)
        if not self.should_compact(len(history)):
            return False

        self._compacting.add(chat_id)
        try:
            return await self._do_compact(chat_id, history)
        finally:
            self._compacting.discard(chat_id)

    async def _do_compact(self, chat_id: str, history: list[dict]) -> bool:
        """执行压缩：摘要前半段对话，保留后半段。"""
        actual_chat_id = chat_id
        # 压缩前 60% 的消息，保留后 40%
        compact_count = int(len(history) * 0.6)
        if compact_count < 4:
            return False

        to_compact = history[:compact_count]
        to_keep = history[compact_count:]

        # 压缩前记忆冲刷：让 AutoMemoryExtractor 从即将被压缩的消息中提取记忆
        if self._auto_memory_extractor is not None:
            try:
                await self._auto_memory_extractor.extract_from_messages(to_compact)
                logger.debug(
                    "[%s] Pre-compression memory flush completed for %d messages",
                    actual_chat_id, len(to_compact),
                )
            except Exception as e:
                logger.warning("[%s] Pre-compression memory flush failed: %s", actual_chat_id, e)

        # 提取前次摘要（如果存在），避免重复摘要
        prior_summary = ""
        if (
            to_compact
            and to_compact[0].get("role") == "system"
            and "[之前的对话摘要]" in to_compact[0].get("content", "")
        ):
            prior_summary = to_compact[0]["content"]
            to_compact = to_compact[1:]

        # 修剪冗长的工具输出，生成摘要文本
        pruned = _prune_tool_outputs(to_compact)
        conversation_text = _format_for_summary(pruned)

        # 迭代摘要：将前次摘要作为上下文传入
        if prior_summary:
            conversation_text = f"[前次摘要供参考]\n{prior_summary}\n\n[新对话]\n{conversation_text}"

        prompt = load_prompt("compaction").replace("{conversation}", conversation_text)

        try:
            summary = await self._router.complete(
                [{"role": "user", "content": prompt}],
                slot="memory_processing",
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
            "content": f"[之前的对话摘要] {SUMMARY_PREFIX}{summary}",
        }
        new_history = [summary_message] + to_keep

        # v2.0 Step 2j: session lineage removed with sessions. Cache update
        # is cache-only in 2h+ since reads come from TrajectoryStore; this
        # call is retained so the phase-0 / unit-test path stays coherent.
        self._memory.replace_history(chat_id, new_history)

        logger.info(
            f"[{actual_chat_id}] Compaction 完成：压缩 {compact_count} 条 → 保留 {len(to_keep)} 条 + 1 条摘要"
        )
        return True
