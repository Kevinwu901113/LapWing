"""URL 安全检查：字符串级 + DNS 解析级。

已知限制（TOCTOU）：DNS 解析结果和实际连接之间存在时间窗口，
攻击者可在检查后切换 DNS 记录。完整方案需要将解析到的 IP 钉入
httpx transport（参考 agent-fetch 的原子 DNS+连接模式），
当前阶段先做解析检查。
"""

import ipaddress
import logging
import socket
import urllib.parse
from dataclasses import dataclass

logger = logging.getLogger("lapwing.utils.url_safety")


@dataclass
class SafetyResult:
    safe: bool
    reason: str = ""


def check_url_safety(url: str) -> SafetyResult:
    """URL 安全检查：协议 + 字符串 IP + DNS 解析。"""
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception as exc:
        return SafetyResult(False, f"URL 解析失败: {exc}")

    if parsed.scheme not in ("http", "https"):
        return SafetyResult(False, f"不支持的协议 '{parsed.scheme}'，只允许 http/https")

    hostname = parsed.hostname
    if not hostname:
        return SafetyResult(False, "无法解析 hostname")

    # 1. 字符串级检查（IP 字面量）
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return SafetyResult(False, f"直接 IP {ip} 为内网地址")
    except ValueError:
        pass  # 不是 IP 字面量，继续 DNS 检查

    # 2. DNS 解析检查
    try:
        addrinfos = socket.getaddrinfo(hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return SafetyResult(False, f"DNS 解析失败（fail-closed）: {hostname}")

    for family, _, _, _, sockaddr in addrinfos:
        ip = ipaddress.ip_address(sockaddr[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return SafetyResult(False, f"{hostname} 解析到内网地址 {ip}")

    return SafetyResult(True)


async def safe_fetch(url: str, max_redirects: int = 5) -> "httpx.Response":
    """跟随重定向并在每跳验证 URL 安全性。"""
    import httpx
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
        for _ in range(max_redirects):
            safety = check_url_safety(url)
            if not safety.safe:
                raise ValueError(f"重定向目标不安全: {safety.reason}")
            response = await client.get(url, follow_redirects=False)
            if response.is_redirect:
                url = str(response.next_request.url)
                continue
            return response
    raise ValueError(f"重定向次数超过 {max_redirects}")
