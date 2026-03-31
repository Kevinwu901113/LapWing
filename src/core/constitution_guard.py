"""宪法校验器 — 在进化和规则更新时强制检查是否违反宪法。"""

import json
import logging
import re

from config.settings import CONSTITUTION_PATH
from src.core.prompt_loader import load_prompt

logger = logging.getLogger("lapwing.constitution_guard")

# 硬编码底线（不依赖 LLM，绝对不可绕过）
_HARD_IDENTITY_MARKERS = [
    "Lapwing",
    "白发",
]

_FORBIDDEN_SELF_REFERENCES = [
    "我是AI", "我是一个AI", "作为AI", "作为一个AI",
    "我是程序", "我是机器人", "我是助手", "我是虚拟助手",
    "I am an AI", "As an AI", "I'm an AI",
]


class ConstitutionGuard:
    """运行时宪法校验器。"""

    def __init__(self, router):
        self._router = router
        self._constitution: str | None = None

    @property
    def constitution(self) -> str:
        if self._constitution is None:
            if CONSTITUTION_PATH.exists():
                self._constitution = CONSTITUTION_PATH.read_text(encoding="utf-8")
            else:
                self._constitution = "（宪法文件不存在）"
                logger.warning("宪法文件不存在，校验将使用硬编码规则")
        return self._constitution

    def reload(self) -> None:
        self._constitution = None

    def validate_hard_constraints(self, text: str) -> list[str]:
        """硬编码底线检查，不依赖 LLM。返回违规列表。"""
        violations = []
        for marker in _HARD_IDENTITY_MARKERS:
            if marker not in text:
                violations.append(f"缺少核心身份标识 '{marker}'")
        for phrase in _FORBIDDEN_SELF_REFERENCES:
            if phrase in text:
                violations.append(f"包含禁止的 AI 自我指称: '{phrase}'")
        return violations

    async def validate_evolution(
        self,
        current_soul: str,
        proposed_changes: list[dict],
    ) -> dict:
        """验证提议的进化变更是否违反宪法。

        Args:
            current_soul: 当前 soul.md 内容
            proposed_changes: [{"action": "add/modify/remove", "description": "..."}]

        Returns:
            {"approved": bool, "violations": list[str]}
        """
        changes_text = "\n".join(
            f"- [{c['action']}] {c['description']}" for c in proposed_changes
        )

        prompt = load_prompt("constitution_check").format(
            constitution=self.constitution,
            current_soul=current_soul,
            proposed_changes=changes_text,
        )

        try:
            response = await self._router.complete(
                [{"role": "user", "content": prompt}],
                slot="persona_expression",
                max_tokens=512,
                session_key="system:constitution_guard",
                origin="core.constitution_guard",
            )
            return self._parse_validation(response)
        except Exception as exc:
            logger.error(f"宪法校验 LLM 调用失败: {exc}")
            return {"approved": False, "violations": [f"校验失败: {exc}"]}

    def _parse_validation(self, text: str) -> dict:
        try:
            cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
            cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE).strip()
            data = json.loads(cleaned)
            return {
                "approved": bool(data.get("approved", False)),
                "violations": list(data.get("violations", [])),
            }
        except Exception:
            logger.warning(f"宪法校验结果解析失败: {text[:200]}")
            return {"approved": False, "violations": ["校验结果解析失败"]}
