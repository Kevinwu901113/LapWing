"""宪法校验器 — 在进化和规则更新时强制检查是否违反宪法。"""

import logging

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

_CONSTITUTION_CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "approved": {
            "type": "boolean",
            "description": "true = 不违反宪法，false = 违反",
        },
        "violations": {
            "type": "array",
            "items": {"type": "string"},
            "description": "被违反的宪法条款及原因，无违反则为空数组",
        },
    },
    "required": ["approved", "violations"],
}


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
            result = await self._router.complete_structured(
                [{"role": "user", "content": prompt}],
                result_schema=_CONSTITUTION_CHECK_SCHEMA,
                result_tool_name="constitution_verdict",
                result_tool_description="提交宪法校验结果",
                slot="persona_expression",
                max_tokens=1536,  # 从 512 提高，防止 thinking 截断
                session_key="system:constitution_guard",
                origin="core.constitution_guard",
            )
            return {
                "approved": bool(result.get("approved", False)),
                "violations": list(result.get("violations", [])),
            }
        except Exception as exc:
            logger.error(f"宪法校验失败: {exc}")
            return {"approved": False, "violations": [f"校验失败: {exc}"]}
