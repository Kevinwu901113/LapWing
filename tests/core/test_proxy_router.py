"""ProxyRouter 单元测试。"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from src.core.proxy_router import ProxyRule, ProxyRouter, _BOTH_FAILED_WINDOW_SECONDS


# ── 辅助工厂 ────────────────────────────────────────────────────────────────


def make_router(
    tmp_path: Path,
    *,
    server: str = "http://proxy.test:7890",
    default_strategy: str = "proxy",
) -> ProxyRouter:
    """在隔离的临时目录创建 ProxyRouter（避免读写 data/ 目录）。"""
    return ProxyRouter(server=server, default_strategy=default_strategy, data_dir=tmp_path)


# ── 测试用例 ─────────────────────────────────────────────────────────────────


def test_resolve_exact_match(tmp_path: Path) -> None:
    """精确域名规则应被优先匹配。"""
    router = make_router(tmp_path)
    # 手动插入精确规则
    router._rules["example.com"] = ProxyRule(
        domain="example.com",
        strategy="direct",
        source="learned",
        success_count=5,
        last_updated="",
        last_failure="",
    )
    router._cache.clear()

    decision = router.resolve("https://example.com/path")
    assert decision.strategy == "direct"
    assert decision.proxy_url is None


def test_resolve_wildcard_subdomain(tmp_path: Path) -> None:
    """*.zhihu.com 应匹配 www.zhihu.com（来自种子规则）。"""
    router = make_router(tmp_path)
    decision = router.resolve("https://www.zhihu.com/question/123")
    assert decision.strategy == "direct"
    assert decision.proxy_url is None


def test_resolve_tld_wildcard(tmp_path: Path) -> None:
    """*.cn 应匹配 example.cn。"""
    router = make_router(tmp_path)
    decision = router.resolve("http://example.cn/page")
    assert decision.strategy == "direct"


def test_resolve_priority_exact_over_wildcard(tmp_path: Path) -> None:
    """精确规则的优先级高于通配符规则。"""
    router = make_router(tmp_path)
    # 种子中有 *.zhihu.com -> direct；添加精确规则 -> proxy
    router._rules["zhihu.com"] = ProxyRule(
        domain="zhihu.com",
        strategy="proxy",
        source="learned",
        success_count=0,
        last_updated="",
        last_failure="",
    )
    router._cache.clear()

    decision = router.resolve("https://zhihu.com/")
    assert decision.strategy == "proxy"
    assert decision.proxy_url == "http://proxy.test:7890"


def test_resolve_default_strategy(tmp_path: Path) -> None:
    """未知域名应回落到默认策略。"""
    router = make_router(tmp_path, default_strategy="proxy")
    decision = router.resolve("https://unknown-foreign-site.io/")
    assert decision.strategy == "proxy"
    assert decision.proxy_url == "http://proxy.test:7890"


def test_resolve_disabled_when_no_server(tmp_path: Path) -> None:
    """server='' 时路由器禁用，所有域名返回 direct。"""
    router = make_router(tmp_path, server="")
    for url in [
        "https://google.com",
        "https://baidu.com",
        "http://example.cn",
    ]:
        decision = router.resolve(url)
        assert decision.strategy == "direct"
        assert decision.proxy_url is None


def test_report_success_increments_count(tmp_path: Path) -> None:
    """report_success 应递增命中规则的 success_count。"""
    router = make_router(tmp_path)
    # zhihu.com 匹配种子 *.zhihu.com
    before = router._rules["*.zhihu.com"].success_count
    router.report_success("https://www.zhihu.com/", "direct")
    after = router._rules["*.zhihu.com"].success_count
    assert after == before + 1


def test_failure_returns_alternative_strategy(tmp_path: Path) -> None:
    """首次失败应返回相反策略。"""
    router = make_router(tmp_path, default_strategy="proxy")
    # 未知域名，default=proxy，失败后应返回 direct
    alt = router.report_failure_and_get_alternative(
        "https://newsite.io/", "proxy"
    )
    assert alt is not None
    assert alt.strategy == "direct"
    assert alt.proxy_url is None


def test_failure_both_tried_returns_none(tmp_path: Path) -> None:
    """双方策略在冷却窗口内均失败时，应返回 None。"""
    router = make_router(tmp_path)
    url = "https://flaky.example.com/"

    # 先报告 proxy 失败
    router.report_failure_and_get_alternative(url, "proxy")
    # 再报告 direct 失败（在同一窗口内）
    result = router.report_failure_and_get_alternative(url, "direct")
    assert result is None


def test_confirm_alternative_creates_learned_rule(tmp_path: Path) -> None:
    """confirm_alternative 应为域名创建 source='learned' 的规则。"""
    router = make_router(tmp_path, default_strategy="proxy")
    url = "https://newsite.io/"
    router.confirm_alternative(url, "direct")

    assert "newsite.io" in router._rules
    rule = router._rules["newsite.io"]
    assert rule.strategy == "direct"
    assert rule.source == "learned"
    assert rule.success_count == 0


def test_seed_rules_loaded_on_init(tmp_path: Path) -> None:
    """初始化后，种子中 *.zhihu.com 应解析为 direct。"""
    router = make_router(tmp_path)
    decision = router.resolve("https://www.zhihu.com/")
    assert decision.strategy == "direct"


async def test_persist_and_reload(tmp_path: Path) -> None:
    """persist 后，新实例应读取相同规则。"""
    router = make_router(tmp_path)
    # 添加一条学习规则
    router.confirm_alternative("https://mysite.com/", "direct")
    await router.persist()

    # 新实例读取同一目录
    router2 = ProxyRouter(
        server="http://proxy.test:7890",
        default_strategy="proxy",
        data_dir=tmp_path,
    )
    assert "mysite.com" in router2._rules
    assert router2._rules["mysite.com"].strategy == "direct"


def test_cache_invalidation_after_confirm(tmp_path: Path) -> None:
    """confirm_alternative 后，旧缓存条目应被清除，resolve 返回新策略。"""
    router = make_router(tmp_path, default_strategy="proxy")
    url = "https://flipping.io/"

    # 第一次解析 -> proxy（默认）
    d1 = router.resolve(url)
    assert d1.strategy == "proxy"

    # 确认切换到 direct
    router.confirm_alternative(url, "direct")

    # 第二次解析 -> direct（缓存已失效）
    d2 = router.resolve(url)
    assert d2.strategy == "direct"


def test_url_parsing_various_formats(tmp_path: Path) -> None:
    """路由器应正确解析多种 URL 格式。"""
    router = make_router(tmp_path, default_strategy="proxy")

    # HTTP + 端口
    d1 = router.resolve("http://example.cn:8080/path?q=1")
    assert d1.strategy == "direct"  # 命中 *.cn 种子

    # HTTPS + 无路径
    d2 = router.resolve("https://www.baidu.com")
    assert d2.strategy == "direct"  # 命中 *.baidu.com 种子

    # 不带协议的 URL 应安全处理（返回默认或 None）
    # urlparse 处理后 hostname 可能为 None，应返回默认策略
    d3 = router.resolve("ftp://gitee.com/repo")
    assert d3.strategy in {"direct", "proxy"}  # 只要不崩溃即可

    # 带用户名密码的 URL（子域名应命中通配符种子）
    d4 = router.resolve("http://user:pass@code.gitee.com/repo")
    assert d4.strategy == "direct"  # code.gitee.com 命中 *.gitee.com 种子
