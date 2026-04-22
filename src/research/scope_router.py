"""ScopeRouter — 纯规则的查询分类器。

根据问题里的关键词和语种特征，决定走 Tavily（global）、博查（cn）还是两个都走。
"""

from __future__ import annotations

import re


class ScopeRouter:
    """决定 research 查询应该走哪个搜索后端。"""

    # 中文/国内平台、媒体、电商等关键词
    CN_PLATFORMS: tuple[str, ...] = (
        "B站", "bilibili", "哔哩哔哩", "知乎", "微博", "小红书", "抖音",
        "贴吧", "百度", "淘宝", "天猫", "京东", "拼多多", "美团", "饿了么",
        "微信", "QQ", "支付宝", "36氪", "虎嗅", "少数派",
        "澎湃", "新华社", "人民日报", "CCTV", "央视",
    )

    # 海外平台、媒体、体育联盟
    GLOBAL_PLATFORMS: tuple[str, ...] = (
        "Twitter", "X.com", "Reddit", "YouTube", "Instagram", "Facebook",
        "TikTok", "GitHub", "Stack Overflow", "Medium", "Substack",
        "ESPN", "MLB", "NBA", "NFL", "NHL", "FIFA", "UEFA",
    )

    _CN_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")
    _EN_WORD_RE = re.compile(r"[a-zA-Z]{3,}")

    _WHITESPACE_RE = re.compile(r"\s+")

    async def decide(self, question: str) -> str:
        """返回 'global' / 'cn' / 'both'。

        路由策略：
        - 含中文平台关键词 → 'cn'（除非同时含海外关键词 → 'both'）
        - 含海外平台关键词 → 'global'（除非同时含中文关键词 → 'both'）
        - 纯英文（无中文字符）→ 'global'
        - 纯中文或中英混合 → 'both'（偏向不遗漏中文搜索引擎）
        """
        q_lower = question.lower()
        # 平台关键词匹配时去空白，让 "B 站" / "B站" / "Stack Overflow" / "stackoverflow" 都能命中
        q_compact = self._WHITESPACE_RE.sub("", q_lower)

        def _matches(kws: tuple[str, ...]) -> bool:
            for kw in kws:
                kw_lower = kw.lower()
                if kw_lower in q_lower:
                    return True
                kw_compact = self._WHITESPACE_RE.sub("", kw_lower)
                if kw_compact and kw_compact in q_compact:
                    return True
            return False

        has_cn_kw = _matches(self.CN_PLATFORMS)
        has_global_kw = _matches(self.GLOBAL_PLATFORMS)

        if has_cn_kw and not has_global_kw:
            return "cn"
        if has_global_kw and not has_cn_kw:
            return "global"
        if has_cn_kw and has_global_kw:
            return "both"

        # 没有平台关键词时：按语种判断
        cn_chars = len(self._CN_CHAR_RE.findall(question))
        en_words = len(self._EN_WORD_RE.findall(question))

        if en_words > 0 and cn_chars == 0:
            return "global"
        if cn_chars > 0 and en_words == 0:
            return "both"
        # 中英混合（如"帮我搜 FastAPI middleware"）→ 双引擎覆盖
        return "both"
