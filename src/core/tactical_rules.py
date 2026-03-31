"""行为规则管理 — 从对话纠正中提取并积累行为规则。"""

import asyncio
import logging
from datetime import datetime, timezone

from config.settings import RULES_PATH
from src.core.prompt_loader import load_prompt
from src.core.reasoning_tags import strip_internal_thinking_tags

logger = logging.getLogger("lapwing.tactical_rules")


class TacticalRules:
    """管理从经验中学到的行为规则。"""

    def __init__(self, router):
        self._router = router
        RULES_PATH.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def might_be_correction(text: str) -> bool:
        """粗粒度判断是否可能是纠正。宁可多触发，不可漏。"""
        indicators = [
            "不要", "别", "不用", "不需要", "停", "够了",
            "错了", "不对", "不是", "搞错",
            "以后", "下次", "记住",
            "don't", "stop", "wrong", "no ",
            "？", "?",
        ]
        text_lower = text.lower()
        return any(ind in text_lower for ind in indicators)

    async def analyze_correction(
        self,
        user_message: str,
        context: list[dict],
    ) -> str | None:
        """分析用户的纠正，生成行为规则。

        返回生成的规则文本，或 None。
        """
        context_text = "\n".join(
            f"{'用户' if m['role'] == 'user' else 'Lapwing'}: {m['content']}"
            for m in context[-8:]
        )

        prompt = load_prompt("correction_analysis").format(
            context=context_text,
            correction=user_message,
        )

        try:
            result = await self._router.complete(
                [{"role": "user", "content": prompt}],
                slot="lightweight_judgment",
                max_tokens=256,
                session_key="system:tactical_rules",
                origin="core.tactical_rules.analyze",
            )
            result = strip_internal_thinking_tags(result).strip()
            if not result or result == "（无）" or "不是纠正" in result:
                return None
            return result
        except Exception as exc:
            logger.warning(f"纠正分析失败: {exc}")
            return None

    async def add_rule(self, rule_text: str) -> None:
        """追加一条规则到 rules.md。"""
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        entry = f"- [{date_str}] {rule_text}"

        def _append():
            if not RULES_PATH.exists():
                RULES_PATH.write_text(
                    f"# 行为规则\n\n从经验中学到的具体行为指导。\n\n{entry}\n",
                    encoding="utf-8",
                )
                return

            existing = RULES_PATH.read_text(encoding="utf-8")

            # 去除占位符行，避免 _build_system_prompt 跳过注入
            lines = existing.splitlines()
            cleaned_lines = [
                line for line in lines
                if "暂无规则" not in line
            ]
            base = "\n".join(cleaned_lines).rstrip()
            RULES_PATH.write_text(base + "\n" + entry + "\n", encoding="utf-8")

        await asyncio.to_thread(_append)
        logger.info(f"[tactical_rules] 新增规则: {rule_text[:60]}")

    async def process_correction(
        self,
        chat_id: str,
        user_message: str,
        context: list[dict],
    ) -> str | None:
        """完整的纠正处理流程：分析 → 生成规则 → 写入。"""
        rule = await self.analyze_correction(user_message, context)
        if rule:
            await self.add_rule(rule)
        return rule
