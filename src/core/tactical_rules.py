"""行为规则管理 — 从对话纠正中提取并积累行为规则。"""

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

from config.settings import RULES_PATH
from src.core.prompt_loader import load_prompt
from src.core.reasoning_tags import strip_think_blocks

logger = logging.getLogger("lapwing.core.tactical_rules")


class TacticalRules:
    """管理从经验中学到的行为规则。"""

    def __init__(self, router, incident_manager=None):
        self._router = router
        self._incident_manager = incident_manager
        RULES_PATH.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def might_be_correction(text: str) -> bool:
        """粗筛是否可能是纠正性反馈。"""
        if len(text) < 3:
            return False
        correction_signals = [
            "不用", "不要", "别这", "别说", "不是这样",
            "不对", "错了", "你说错", "不准确", "我说的是",
            "又", "怎么又", "你每次", "说过了",
            "太长", "太正式", "像机器人", "像客服", "像AI",
            "不要列", "别列", "不要用", "别用",
        ]
        return any(signal in text for signal in correction_signals)

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
            result = strip_think_blocks(result).strip()
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
        from src.logging.event_logger import events
        events.log("evolution", "correction_learned",
            change_type="tactical_rule",
            diff=rule_text[:300],
            file="evolution/rules.md",
        )

    async def process_correction(
        self,
        chat_id: str,
        user_message: str,
        context: list[dict],
    ) -> str | None:
        """完整的纠正处理流程：分析 → 生成规则 → 写入 → 创建 incident。"""
        rule = await self.analyze_correction(user_message, context)
        if rule:
            await self.add_rule(rule)
            # 同时创建 incident，以便后续排查和转化为正向知识
            if self._incident_manager is not None:
                snippet = "\n".join(
                    f"{'用户' if m['role'] == 'user' else 'Lapwing'}: "
                    f"{str(m.get('content', ''))[:200]}"
                    for m in context[-5:]
                    if m.get("role") in ("user", "assistant")
                )
                inc_id = await self._incident_manager.create(
                    source="user_correction",
                    description=f"用户纠正: {rule[:80]}",
                    context={
                        "user_message": user_message[:500],
                        "conversation_snippet": snippet,
                        "chat_id": chat_id,
                    },
                    severity="medium",
                )
                if inc_id:
                    self._incident_manager.link_rule(inc_id, rule)
        return rule

    async def remove_rule(self, rule_text: str) -> bool:
        """移除包含指定文本的规则行。用于 incident resolved 后清理关联规则。"""
        if not RULES_PATH.exists():
            return False

        def _remove():
            content = RULES_PATH.read_text(encoding="utf-8")
            lines = content.split("\n")
            new_lines = [line for line in lines if rule_text not in line]
            if len(new_lines) < len(lines):
                RULES_PATH.write_text("\n".join(new_lines), encoding="utf-8")
                return True
            return False

        removed = await asyncio.to_thread(_remove)
        if removed:
            logger.info("[tactical_rules] 移除规则: %s", rule_text[:60])
        return removed

    async def cleanup_stale_rules(self, max_age_days: int = 60) -> int:
        """清理超过 max_age_days 的旧规则。由 memory_maintenance 每日调用。"""
        if not RULES_PATH.exists():
            return 0

        cutoff = datetime.now() - timedelta(days=max_age_days)
        date_pattern = re.compile(r"\[(\d{4}-\d{2}-\d{2})\]")

        def _cleanup():
            content = RULES_PATH.read_text(encoding="utf-8")
            lines = content.split("\n")
            kept = []
            removed_count = 0
            for line in lines:
                match = date_pattern.search(line)
                if match:
                    try:
                        rule_date = datetime.strptime(match.group(1), "%Y-%m-%d")
                        if rule_date < cutoff:
                            removed_count += 1
                            continue
                    except ValueError:
                        pass
                kept.append(line)
            if removed_count > 0:
                RULES_PATH.write_text("\n".join(kept), encoding="utf-8")
            return removed_count

        count = await asyncio.to_thread(_cleanup)
        if count:
            logger.info("[tactical_rules] 清理�� %d 条过期规则", count)
        return count
