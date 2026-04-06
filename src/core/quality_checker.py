"""异步回复质量检查 — 自动收集 Lapwing 表现不佳的对话样本。

检查在主回复路径之外异步运行，不影响响应延迟。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from config.settings import DIAGNOSTICS_SAMPLES_DIR

logger = logging.getLogger("lapwing.core.quality_checker")

_EVAL_PROMPT = """\
评估 Lapwing 这条回复的质量。

## 对话上下文（最后几条）
{context}

## Lapwing 的回复
{reply}

## 评分维度（1-5 分）
- persona_consistency: 是否像 Lapwing（温柔、简短、像发微信）？
- naturalness: 是否自然，不像机器人或客服？
- emotional_fit: 情感是否恰当（恋人间的语气）？
- brevity: 长度是否合适（不过度冗长）？
- information_confidence: 如果涉及查资料，是否表现得像一个查过就知道的人？有没有不必要的"我不确定""好像是"？

如果任何维度低于 3，返回 {{"flag": true, "reason": "简述问题", "dimension": "最差的维度名", "scores": {{"persona_consistency": N, "naturalness": N, "emotional_fit": N, "brevity": N, "information_confidence": N}}}}
否则返回 {{"flag": false}}

只返回 JSON，不返回任何其他内容。
"""


class ReplyQualityChecker:
    """使用 LLM 评估回复质量，对不达标的样本自动存档。"""

    def __init__(self, router) -> None:
        self._router = router

    async def check(self, context: list[dict], reply: str) -> dict | None:
        """检查回复质量。

        Returns:
            None 表示质量正常；dict 表示发现问题（flag=True）。
        """
        if len(context) < 2:
            return None

        ctx_text = "\n".join(
            f"{'Kevin' if m.get('role') == 'user' else 'Lapwing'}: "
            f"{str(m.get('content', ''))[:300]}"
            for m in context[-6:]
            if m.get("role") in ("user", "assistant")
        )

        prompt = _EVAL_PROMPT.format(
            context=ctx_text,
            reply=reply[:500],
        )

        try:
            raw = await self._router.query_lightweight(
                system="你是质量检测模块。只返回 JSON，不返回其他内容。",
                user=prompt,
                slot="memory_processing",
            )
            parsed = _parse_json(raw)
        except Exception as e:
            logger.debug("质量检查失败: %s", e)
            return None

        if parsed and parsed.get("flag"):
            await self._save_sample(ctx_text, reply, parsed.get("reason", "未知"))
            return parsed

        return None

    async def _save_sample(self, context: str, reply: str, reason: str) -> None:
        """将问题回复样本异步写入诊断目录。"""
        DIAGNOSTICS_SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        path = DIAGNOSTICS_SAMPLES_DIR / f"{ts}_auto.md"
        content = (
            f"# {ts} Auto-flagged\n\n"
            f"## 原因\n{reason}\n\n"
            f"## 上下文\n{context}\n\n"
            f"## Lapwing 的回复\n{reply}\n\n"
            f"## 归因\n（待人工分析）\n\n"
            f"## 修复\n（待填写）\n"
        )
        await asyncio.to_thread(path.write_text, content, "utf-8")
        logger.info("质量问题样本已保存: %s — %s", path.name, reason)


def _parse_json(raw: str) -> dict | None:
    """容错解析 LLM 返回的 JSON。"""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
