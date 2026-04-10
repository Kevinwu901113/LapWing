"""BrowserGuard — 浏览器操作安全守卫。

在浏览器操作执行前检查安全性，拦截三类风险：
1. 危险 URL（内网地址、javascript/data 协议、黑名单域名）
2. 敏感页面操作（支付、删除等需要用户确认的动作）
3. 危险 JavaScript 表达式（eval、cookie 篡改、页面跳转等）

设计对齐 SkillGuard/MemoryGuard：静态分析，不调用 LLM。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import config.settings as _settings

logger = logging.getLogger("lapwing.guards.browser_guard")


@dataclass
class GuardResult:
    """浏览器安全检查结果。

    action:
        - "pass"：允许执行
        - "block"：拒绝执行
        - "require_consent"：需要用户确认后才能执行
    reason: 当 action 不是 "pass" 时，说明原因。
    """
    action: str  # "pass" | "block" | "require_consent"
    reason: str | None = None


# ── 内网地址匹配 ──────────────────────────────────────────────────────────

# IPv4 内网/回环/链路本地地址
_PRIVATE_IPV4_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^127\."),                     # 127.0.0.0/8 回环
    re.compile(r"^10\."),                      # 10.0.0.0/8 A 类私有
    re.compile(r"^172\.(1[6-9]|2\d|3[01])\."), # 172.16.0.0/12 B 类私有
    re.compile(r"^192\.168\."),                 # 192.168.0.0/16 C 类私有
    re.compile(r"^0\.0\.0\.0$"),                # 未指定地址
]

# 本机/内网主机名
_PRIVATE_HOSTNAMES: set[str] = {"localhost"}

# IPv6 内网/回环地址前缀
_PRIVATE_IPV6_PREFIXES: list[str] = [
    "[::1]",    # 回环
    "[fc",      # fc00::/7 唯一本地
    "[fd",      # fc00::/7 唯一本地（fd 前缀更常见）
    "[fe80:",   # fe80::/10 链路本地
]

# ── 危险 JS 模式 ─────────────────────────────────────────────────────────

_DANGEROUS_JS_PATTERNS: list[tuple[str, str]] = [
    (r"\beval\s*\(", "eval() 可执行任意代码"),
    (r"\bnew\s+Function\s*\(", "new Function() 可动态构造代码"),
    (r"document\.cookie\s*=", "禁止通过 JS 修改 cookie"),
    (r"window\.location\s*=", "禁止通过 JS 跳转页面（window.location 赋值）"),
    (r"location\.href\s*=", "禁止通过 JS 跳转页面（location.href 赋值）"),
    (r"document\.write\s*\(", "document.write() 会覆盖整个页面"),
]

# ── 结账/支付页面 URL 关键词 ─────────────────────────────────────────────

_CHECKOUT_URL_KEYWORDS: list[str] = [
    "checkout",
    "payment",
    "pay.html",
]


class BrowserGuard:
    """浏览器操作安全守卫。

    提供三个检查方法，分别用于 URL 导航、页面元素操作、JS 执行。
    所有检查均为静态分析，不调用 LLM。
    """

    # ── URL 检查 ─────────────────────────────────────────────────────

    def check_url(self, url: str) -> GuardResult:
        """检查目标 URL 是否安全。

        拦截规则（按优先级）：
        1. BLOCK: javascript: / data: 协议
        2. BLOCK: 内网地址（当 BROWSER_BLOCK_INTERNAL_NETWORK 开启时）
        3. BLOCK: 黑名单域名
        4. BLOCK: 白名单模式下不在白名单中的域名（fail-closed）
        5. PASS: 其他所有 URL

        白名单模式：当 BROWSER_URL_WHITELIST 非空时启用，只允许列出的域名。
        """
        url_lower = url.strip().lower()

        # 1. 危险协议
        if url_lower.startswith("javascript:"):
            logger.warning("拦截 javascript: 协议 URL: %s", url[:80])
            return GuardResult(action="block", reason="禁止访问 javascript: 协议 URL")

        if url_lower.startswith("data:"):
            logger.warning("拦截 data: 协议 URL: %s", url[:80])
            return GuardResult(action="block", reason="禁止访问 data: 协议 URL")

        # 解析 URL 取主机名
        try:
            parsed = urlparse(url)
            hostname = (parsed.hostname or "").lower()
        except Exception:
            logger.warning("URL 解析失败: %s", url[:80])
            return GuardResult(action="block", reason="URL 解析失败，拒绝访问")

        if not hostname:
            return GuardResult(action="pass")

        # 2. 内网地址检查
        if _settings.BROWSER_BLOCK_INTERNAL_NETWORK:
            block_reason = self._check_internal_network(hostname)
            if block_reason:
                logger.warning("拦截内网地址: %s (%s)", hostname, block_reason)
                return GuardResult(action="block", reason=block_reason)

        # 3. 黑名单域名
        if self._is_blacklisted(hostname):
            logger.warning("拦截黑名单域名: %s", hostname)
            return GuardResult(
                action="block",
                reason=f"域名 {hostname} 在黑名单中",
            )

        # 4. 白名单模式（fail-closed：非空白名单时只允许列出的域名）
        whitelist = _settings.BROWSER_URL_WHITELIST
        if whitelist and not self._is_whitelisted(hostname, whitelist):
            logger.warning("白名单模式拦截: %s 不在允许列表中", hostname)
            return GuardResult(
                action="block",
                reason=f"域名 {hostname} 不在白名单中（白名单模式已启用）",
            )

        # 5. 通过
        return GuardResult(action="pass")

    def _check_internal_network(self, hostname: str) -> str | None:
        """检查主机名是否属于内网地址。返回拒绝原因或 None。"""
        # 本机主机名
        if hostname in _PRIVATE_HOSTNAMES:
            return f"禁止访问本地地址 ({hostname})"

        # IPv4 私有/回环地址
        for pattern in _PRIVATE_IPV4_PATTERNS:
            if pattern.match(hostname):
                return f"禁止访问内网 IPv4 地址 ({hostname})"

        # IPv6 地址（带方括号或裸地址）
        # urlparse 对 IPv6 URL 解析后 hostname 不含方括号，需要补上检查
        ipv6_check = f"[{hostname}]" if not hostname.startswith("[") else hostname
        for prefix in _PRIVATE_IPV6_PREFIXES:
            if ipv6_check.startswith(prefix):
                return f"禁止访问内网 IPv6 地址 ({hostname})"

        # 特殊处理 0.0.0.0
        if hostname == "0.0.0.0":
            return f"禁止访问未指定地址 ({hostname})"

        return None

    def _is_blacklisted(self, hostname: str) -> bool:
        """检查域名是否在黑名单中。支持子域名匹配。"""
        for blocked in _settings.BROWSER_URL_BLACKLIST:
            blocked_lower = blocked.lower()
            if hostname == blocked_lower or hostname.endswith("." + blocked_lower):
                return True
        return False

    @staticmethod
    def _is_whitelisted(hostname: str, whitelist: list[str]) -> bool:
        """检查域名是否在白名单中。支持子域名匹配。"""
        for allowed in whitelist:
            allowed_lower = allowed.lower()
            if hostname == allowed_lower or hostname.endswith("." + allowed_lower):
                return True
        return False

    # ── 页面操作检查 ─────────────────────────────────────────────────

    def check_action(
        self, action: str, element_text: str, page_url: str
    ) -> GuardResult:
        """检查页面元素操作是否需要用户确认。

        拦截规则：
        1. REQUIRE_CONSENT: 元素文本包含敏感词（支付、删除等）
        2. REQUIRE_CONSENT: 页面 URL 包含结账/支付关键词
        3. PASS: 其他操作

        Args:
            action: 操作类型（如 "click"、"fill" 等）
            element_text: 目标元素的可见文本
            page_url: 当前页面 URL

        Returns:
            GuardResult
        """
        element_lower = element_text.lower()
        url_lower = page_url.lower()

        # 1. 敏感操作词检查
        for word in _settings.BROWSER_SENSITIVE_ACTION_WORDS:
            if word.lower() in element_lower:
                logger.info(
                    "敏感操作需确认: action=%s, text=%s, word=%s",
                    action, element_text[:40], word,
                )
                return GuardResult(
                    action="require_consent",
                    reason=f"元素文本包含敏感词「{word}」，需要用户确认",
                )

        # 2. 结账/支付页面
        for keyword in _CHECKOUT_URL_KEYWORDS:
            if keyword in url_lower:
                logger.info(
                    "支付页面操作需确认: action=%s, url=%s",
                    action, page_url[:80],
                )
                return GuardResult(
                    action="require_consent",
                    reason=f"当前页面包含支付/结账关键词「{keyword}」，需要用户确认",
                )

        # 3. 通过
        return GuardResult(action="pass")

    # ── JavaScript 表达式检查 ────────────────────────────────────────

    def check_js(self, expression: str) -> GuardResult:
        """检查 JavaScript 表达式是否安全。

        拦截规则：
        - BLOCK: 包含 eval()、new Function()、document.cookie=、
                 window.location=、location.href=、document.write() 等危险模式
        - PASS: 只读 DOM 查询、属性读取等安全操作

        Args:
            expression: 待执行的 JavaScript 表达式

        Returns:
            GuardResult
        """
        for pattern, description in _DANGEROUS_JS_PATTERNS:
            if re.search(pattern, expression, re.IGNORECASE):
                logger.warning(
                    "拦截危险 JS 表达式: %s (原因: %s)",
                    expression[:80], description,
                )
                return GuardResult(
                    action="block",
                    reason=f"JavaScript 表达式包含危险操作: {description}",
                )

        return GuardResult(action="pass")
