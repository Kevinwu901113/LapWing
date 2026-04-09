"""MemoryGuard — 记忆内容安全扫描器。

在记忆写入前检查内容是否包含注入模式。
设计对齐 SkillGuard：regex 静态分析，ScanResult 返回值。

拦截五类威胁：
1. Prompt 注入（篡改系统指令）
2. 凭证泄露（API key、token、password 模式）
3. 数据外泄（curl/wget + URL）
4. SSH 后门注入
5. Lapwing 特有：宪法与身份篡改意图

额外检查：
- 不可见 Unicode 字符（零宽字符、方向标记等）
"""

from __future__ import annotations

import re

from src.guards.skill_guard import ScanResult

# 不可见 Unicode 字符检测
_INVISIBLE_CHARS = re.compile(
    r"[\u200b-\u200f\u2028-\u202f\u2060-\u2069\ufeff\u00ad]"
)


class MemoryGuard:
    """记忆内容安全扫描器。

    扫描方式：regex 静态分析，不调用 LLM。
    在记忆写入前使用（memory_note、memory_edit、AutoMemoryExtractor、FactExtractor）。
    """

    THREAT_PATTERNS: list[tuple[str, str]] = [
        # ── Prompt 注入 ───────────────────────────────────────────────
        (
            r"(?:ignore|disregard|forget)\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions?|rules?|prompts?)",
            "检测到 prompt 注入模式（忽略之前的指令）",
        ),
        (
            r"you\s+are\s+now\s+",
            "检测到身份覆盖指令",
        ),
        (
            r"(?:^|\n)\s*system\s*:\s*",
            "检测到伪造 system 消息格式",
        ),
        (
            r"new\s+instructions?\s*:",
            "检测到指令注入模式",
        ),
        (
            r"do\s+not\s+tell\s+(?:the\s+)?user",
            "检测到信息隐藏指令",
        ),
        (
            r"pretend\s+(?:you\s+are|to\s+be)\s+(?!lapwing)",
            "检测到伪装指令",
        ),
        (
            r"act\s+as\s+(?!lapwing)",
            "检测到角色替换指令",
        ),

        # ── 凭证模式 ─────────────────────────────────────────────────
        (
            r"(?:api[_-]?key|token|password|secret|credential)\s*[=:]\s*\S{8,}",
            "检测到可能的凭证信息（key/token/password 赋值）",
        ),

        # ── 数据外泄 ─────────────────────────────────────────────────
        (
            r"(?:curl|wget|fetch)\s+https?://",
            "检测到外泄 URL 命令",
        ),

        # ── SSH 后门 ──────────────────────────────────────────────────
        (
            r"authorized_keys",
            "检测到 SSH 密钥注入模式",
        ),
        (
            r"ssh\s+\S+@\S+",
            "检测到 SSH 连接命令",
        ),

        # ── 宪法与身份篡改（Lapwing 特有）──────────────────────────
        (
            r"(?:修改|删除|忽略|覆盖|绕过)\s*(?:宪法|constitution)",
            "检测到宪法篡改意图",
        ),
        (
            r"(?:你不是|你不再是)\s*[Ll]apwing",
            "检测到身份否定意图",
        ),
        (
            r"constitution.*\b(?:delet|remov|modif|overwrit|bypass|ignor)\w*\b",
            "检测到宪法篡改意图（英文）",
        ),
        (
            r"soul\.md.*\b(?:delet|overwrit|replac|corrupt)\w*\b",
            "检测到 soul.md 篡改意图",
        ),
    ]

    def scan(self, content: str) -> ScanResult:
        """对 content 做安全扫描，返回扫描结果。

        Args:
            content: 要扫描的记忆文本内容

        Returns:
            ScanResult，passed=True 表示安全，threats 列表包含命中的威胁描述。
        """
        threats: list[str] = []

        # 不可见 Unicode 字符检查
        if _INVISIBLE_CHARS.search(content):
            threats.append("检测到不可见 Unicode 字符（可能的隐蔽注入）")

        # 威胁模式检查
        for pattern, description in self.THREAT_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                threats.append(description)

        return ScanResult(passed=len(threats) == 0, threats=threats)
