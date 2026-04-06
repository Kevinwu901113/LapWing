"""SkillGuard — Skill 内容安全扫描器。

对 Skill 内容做 regex 静态分析，拦截四类威胁：
1. 数据外泄（curl/wget + 凭证变量）
2. 敏感路径访问（~/.ssh, ~/.aws 等）
3. Prompt 注入（绕过系统指令的指令）
4. 破坏性命令（rm -rf /, mkfs, dd 写系统分区）
5. Lapwing 特有：宪法与身份篡改意图
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ScanResult:
    passed: bool
    threats: list[str] = field(default_factory=list)


class SkillGuard:
    """Skill 内容安全扫描器。

    扫描方式：regex 静态分析，不调用 LLM。
    在 Skill 创建/更新/加载时使用。
    """

    THREAT_PATTERNS: list[tuple[str, str]] = [
        # ── 数据外泄 ──────────────────────────────────────────────────────
        (
            r"curl\b.*\$\{?\w*(key|token|secret|password|api_key)\w*\}?",
            "检测到可能的凭证外泄命令（curl + 环境变量）",
        ),
        (
            r"wget\b.*\$\{?\w*(key|token|secret|password|api_key)\w*\}?",
            "检测到可能的凭证外泄命令（wget + 环境变量）",
        ),

        # ── 敏感路径 ──────────────────────────────────────────────────────
        (
            r"~/\.(ssh|aws|kube|gnupg|netrc|password[- _]?store)",
            "引用了敏感凭证目录",
        ),
        (
            r"data/config/.*\.json",
            "直接引用了系统配置文件",
        ),
        (
            r"config/\.env\b",
            "直接引用了 .env 文件",
        ),

        # ── Prompt 注入 ───────────────────────────────────────────────────
        (
            r"ignore\s+(previous|all|above|prior)\s+instructions?",
            "检测到 prompt 注入模式（忽略之前的指令）",
        ),
        (
            r"system\s+prompt\s+override",
            "检测到 prompt 注入模式（系统 prompt 覆盖）",
        ),
        (
            r"do\s+not\s+tell\s+(the\s+)?user",
            "检测到信息隐藏指令",
        ),
        (
            r"pretend\s+(you\s+are|to\s+be)\s+(?!lapwing)",
            "检测到伪装指令",
        ),
        (
            r"act\s+as\s+(?!lapwing)",
            "检测到角色替换指令",
        ),

        # ── 破坏性命令 ────────────────────────────────────────────────────
        (
            r"rm\s+-rf\s+[/~]",
            "检测到破坏性文件删除命令",
        ),
        (
            r"\bmkfs\b",
            "检测到磁盘格式化命令",
        ),
        (
            r"\bdd\b.*of=/dev/",
            "检测到向系统分区写入的命令",
        ),
        (
            r":()\{:\|:&\};:",
            "检测到 fork bomb",
        ),

        # ── Lapwing 特有：宪法与身份篡改 ────────────────────────────────
        (
            r"constitution.*\b(delete|remove|modify|overwrite|bypass|ignore)\b",
            "检测到宪法篡改意图",
        ),
        (
            r"identity.*\b(change|replace|reset|overwrite|erase)\b",
            "检测到身份篡改意图",
        ),
        (
            r"soul\.md.*\b(delete|overwrite|replace|corrupt)\b",
            "检测到 soul.md 篡改意图",
        ),
    ]

    def scan(self, content: str) -> ScanResult:
        """对 content 做全量 regex 扫描，返回扫描结果。

        Args:
            content: 要扫描的文本内容（Skill body 或完整文件）

        Returns:
            ScanResult，passed=True 表示安全，threats 列表包含命中的威胁描述。
        """
        threats: list[str] = []
        for pattern, description in self.THREAT_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                threats.append(description)
        return ScanResult(passed=len(threats) == 0, threats=threats)
