"""代理路由器 — 按域名自适应选择代理或直连。

基于试错学习持久化路由规则：失败后切换策略并记录，成功后稳定规则。
规则文件位于 data/proxy/routing_rules.json。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger("lapwing.core.proxy_router")

# ── 种子规则（直连，针对中文网站） ──────────────────────────────────────────

_SEED_DOMAINS: list[str] = [
    "*.cn",
    "*.com.cn",
    "*.baidu.com",
    "*.qq.com",
    "*.weibo.com",
    "*.zhihu.com",
    "*.bilibili.com",
    "*.taobao.com",
    "*.jd.com",
    "*.douyin.com",
    "*.163.com",
    "*.sohu.com",
    "*.sina.cn",
    "*.sina.com.cn",
    "*.csdn.net",
    "*.jianshu.com",
    "*.toutiao.com",
    "*.bytedance.com",
    "*.aliyun.com",
    "*.tencent.com",
    "*.huawei.com",
    "*.xiaomi.com",
    "*.gitee.com",
]

# 双方策略均失败的冷却窗口（秒）
_BOTH_FAILED_WINDOW_SECONDS = 60

# 学习规则需要达到的成功次数才视为"稳定"
_STABLE_SUCCESS_THRESHOLD = 3


# ── 数据结构 ─────────────────────────────────────────────────────────────────


@dataclass
class ProxyRule:
    """单条路由规则。"""

    domain: str          # 如 "zhihu.com" 或 "*.sina.cn"
    strategy: str        # "proxy" | "direct"
    source: str          # "seed" | "learned"
    success_count: int
    last_updated: str    # ISO 时间戳
    last_failure: str    # 上次以"当前策略"失败的时间；"" 表示无记录

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "strategy": self.strategy,
            "source": self.source,
            "success_count": self.success_count,
            "last_updated": self.last_updated,
            "last_failure": self.last_failure,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProxyRule":
        return cls(
            domain=d["domain"],
            strategy=d["strategy"],
            source=d.get("source", "seed"),
            success_count=d.get("success_count", 0),
            last_updated=d.get("last_updated", ""),
            last_failure=d.get("last_failure", ""),
        )


@dataclass
class ProxyDecision:
    """路由决策结果。"""

    strategy: str           # "proxy" | "direct"
    proxy_url: str | None   # 当 strategy=="proxy" 时为代理地址，否则为 None


# ── 辅助函数 ─────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    """返回当前 UTC 时间的 ISO 字符串。"""
    return datetime.now(timezone.utc).isoformat()


def _extract_hostname(url: str) -> str | None:
    """从 URL 提取主机名（小写）。"""
    try:
        parsed = urlparse(url)
        host = parsed.hostname  # 自动去掉端口、转小写
        return host or None
    except Exception:
        return None


def _seconds_since(ts: str) -> float:
    """返回自 ISO 时间戳 ts 以来经过的秒数；ts 为空时返回 inf。"""
    if not ts:
        return float("inf")
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return delta.total_seconds()
    except Exception:
        return float("inf")


def _build_seed_rules() -> list[ProxyRule]:
    """构建初始种子规则列表。"""
    ts = _now_iso()
    return [
        ProxyRule(
            domain=d,
            strategy="direct",
            source="seed",
            success_count=0,
            last_updated=ts,
            last_failure="",
        )
        for d in _SEED_DOMAINS
    ]


# ── 主类 ──────────────────────────────────────────────────────────────────────


class ProxyRouter:
    """按域名自适应选择代理或直连的路由器。

    启动时加载（或初始化）规则文件；通过 resolve() 查询决策；
    通过 report_success / report_failure_and_get_alternative / confirm_alternative
    反馈实际连接结果以持续调优规则。
    """

    def __init__(
        self,
        server: str,
        default_strategy: str = "proxy",
        data_dir: Path = Path("data/proxy"),
    ) -> None:
        self._server = server
        self._default_strategy = default_strategy
        self._data_dir = Path(data_dir)
        self._rules_file = self._data_dir / "routing_rules.json"

        # domain -> ProxyRule（精确匹配表）
        self._rules: dict[str, ProxyRule] = {}
        # 解析缓存 domain -> ProxyDecision
        self._cache: dict[str, ProxyDecision] = {}
        self._dirty: bool = False
        self._disabled: bool = not server
        # 临时失败记录表："{domain}:{strategy}" -> ISO 时间戳
        self._failure_log: dict[str, str] = {}

        self._load_or_init()

    # ── 内部：规则文件 I/O ────────────────────────────────────────────────

    def _load_or_init(self) -> None:
        """加载规则文件；不存在时写入种子规则。"""
        if self._rules_file.exists():
            try:
                data = json.loads(self._rules_file.read_text(encoding="utf-8"))
                for r in data.get("rules", []):
                    rule = ProxyRule.from_dict(r)
                    self._rules[rule.domain] = rule
                # 允许文件覆盖默认策略
                if "default_strategy" in data:
                    self._default_strategy = data["default_strategy"]
                logger.debug(
                    "proxy.route.loaded",
                    extra={"rule_count": len(self._rules)},
                )
                return
            except Exception as exc:
                logger.warning("代理规则文件读取失败，重新初始化: %s", exc)

        # 首次启动：写入种子规则（同步，__init__ 中不依赖事件循环）
        self._data_dir.mkdir(parents=True, exist_ok=True)
        for rule in _build_seed_rules():
            self._rules[rule.domain] = rule
        self._dirty = True
        self._sync_write()

    def _sync_write(self) -> None:
        """同步写入规则文件（初始化时使用）。"""
        if not self._dirty:
            return
        self._data_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "default_strategy": self._default_strategy,
            "rules": [r.to_dict() for r in self._rules.values()],
        }
        tmp = self._rules_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._rules_file)
        self._dirty = False
        logger.debug("proxy.route.persisted", extra={"rule_count": len(self._rules)})

    # ── 内部：域名匹配 ────────────────────────────────────────────────────

    def _match_domain(self, domain: str) -> ProxyRule | None:
        """按优先级匹配域名规则：精确 → 通配子域 → TLD 通配 → None。"""
        # 1. 精确匹配
        if domain in self._rules:
            return self._rules[domain]

        # 2 & 3. 通配符：从最具体到最宽泛
        parts = domain.split(".")
        for i in range(1, len(parts)):
            wildcard = "*." + ".".join(parts[i:])
            if wildcard in self._rules:
                return self._rules[wildcard]

        return None

    def _make_decision(self, strategy: str) -> ProxyDecision:
        return ProxyDecision(
            strategy=strategy,
            proxy_url=self._server if strategy == "proxy" else None,
        )

    def _opposite_strategy(self, strategy: str) -> str:
        return "direct" if strategy == "proxy" else "proxy"

    # ── 公开接口 ──────────────────────────────────────────────────────────

    def resolve(self, url: str) -> ProxyDecision:
        """解析 URL 对应的代理策略，结果缓存。"""
        if self._disabled:
            return ProxyDecision(strategy="direct", proxy_url=None)

        domain = _extract_hostname(url)
        if not domain:
            return self._make_decision(self._default_strategy)

        # 命中缓存
        if domain in self._cache:
            return self._cache[domain]

        rule = self._match_domain(domain)
        strategy = rule.strategy if rule else self._default_strategy
        decision = self._make_decision(strategy)
        self._cache[domain] = decision

        logger.debug(
            "proxy.route.resolved",
            extra={
                "domain": domain,
                "strategy": strategy,
                "matched_rule": rule.domain if rule else None,
            },
        )
        return decision

    def report_success(self, url: str, strategy: str) -> None:
        """请求成功：增加命中规则的 success_count。"""
        if self._disabled:
            return
        domain = _extract_hostname(url)
        if not domain:
            return
        rule = self._match_domain(domain)
        if rule and rule.strategy == strategy:
            rule.success_count += 1
            rule.last_updated = _now_iso()
            self._dirty = True
        elif rule and rule.strategy != strategy:
            logger.debug(
                "proxy.report_success.strategy_mismatch",
                extra={
                    "domain": domain,
                    "reported_strategy": strategy,
                    "rule_strategy": rule.strategy,
                },
            )

    def report_failure_and_get_alternative(
        self, url: str, strategy: str
    ) -> ProxyDecision | None:
        """记录失败，返回备用策略；若双方策略近期均失败则返回 None。"""
        if self._disabled:
            return None

        domain = _extract_hostname(url)
        if not domain:
            return None

        now = _now_iso()
        alt_strategy = self._opposite_strategy(strategy)

        # 查找对应域名的当前规则（可能是通配符匹配）
        rule = self._match_domain(domain)

        # 判断备用策略是否也在近期失败过
        # 我们在 _failed_strategies 临时表中跟踪这个域名的失败记录
        failed_key_current = f"{domain}:{strategy}"
        failed_key_alt = f"{domain}:{alt_strategy}"

        self._failure_log[failed_key_current] = now

        # 检查备用策略是否在冷却窗口内也失败过
        alt_failure_ts = self._failure_log.get(failed_key_alt, "")
        if _seconds_since(alt_failure_ts) < _BOTH_FAILED_WINDOW_SECONDS:
            logger.info(
                "proxy.route.retry",
                extra={
                    "domain": domain,
                    "failed_strategy": strategy,
                    "result": "both_failed",
                },
            )
            return None

        logger.info(
            "proxy.route.retry",
            extra={
                "domain": domain,
                "failed_strategy": strategy,
                "alt_strategy": alt_strategy,
            },
        )
        return self._make_decision(alt_strategy)

    def confirm_alternative(self, url: str, new_strategy: str) -> None:
        """备用策略成功：为此域名创建或更新"learned"规则，并清除缓存。"""
        if self._disabled:
            return

        domain = _extract_hostname(url)
        if not domain:
            return

        now = _now_iso()
        # 若已有精确规则，更新；否则新建
        if domain in self._rules:
            rule = self._rules[domain]
            rule.strategy = new_strategy
            rule.source = "learned"
            rule.success_count = 0
            rule.last_updated = now
            rule.last_failure = ""
        else:
            self._rules[domain] = ProxyRule(
                domain=domain,
                strategy=new_strategy,
                source="learned",
                success_count=0,
                last_updated=now,
                last_failure="",
            )

        # 清除该域名的缓存条目
        self._cache.pop(domain, None)
        self._dirty = True

        logger.info(
            "proxy.route.learned",
            extra={"domain": domain, "strategy": new_strategy},
        )

    async def persist(self) -> None:
        """将规则异步写入 JSON 文件（仅在 dirty 时执行，先备份旧文件）。"""
        if not self._dirty:
            return

        import asyncio

        await asyncio.to_thread(self._sync_persist)

    def _sync_persist(self) -> None:
        """同步写入规则文件（在线程池中执行，避免阻塞事件循环）。"""
        if not self._dirty:
            return

        self._data_dir.mkdir(parents=True, exist_ok=True)

        # 备份旧文件
        if self._rules_file.exists():
            backup = self._rules_file.with_suffix(".bak")
            try:
                import shutil

                shutil.copy2(self._rules_file, backup)
            except Exception as exc:
                logger.warning("代理规则备份失败: %s", exc)

        data = {
            "default_strategy": self._default_strategy,
            "rules": [r.to_dict() for r in self._rules.values()],
        }
        tmp = self._rules_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._rules_file)
        self._dirty = False

        logger.debug(
            "proxy.route.persisted",
            extra={"rule_count": len(self._rules)},
        )
