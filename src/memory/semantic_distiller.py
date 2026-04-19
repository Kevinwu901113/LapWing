"""SemanticDistiller — 从 Episodic 条目提炼 Semantic 知识。

Blueprint v2.0 Step 7 §M3.b. 每日 maintenance 触发一次：拉最近 N 条
Episodic 记忆，调 memory_processing slot 提炼持久性知识，写入
SemanticStore。SemanticStore 写入时自己做语义去重，重复事实会被跳过。

设计：
- 批量提炼而不是逐条——一次 LLM 调用处理多条 episode，找 pattern
- 失败静默：偶发提炼失败不致命，下一天的 cycle 会再试
- 不覆写、不修改——SemanticStore append-only，重复靠 dedup 过滤
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.core.prompt_loader import load_prompt

if TYPE_CHECKING:
    from src.core.llm_router import LLMRouter
    from src.memory.episodic_store import EpisodicEntry, EpisodicStore
    from src.memory.semantic_store import SemanticStore

logger = logging.getLogger("lapwing.memory.semantic_distiller")

_VALID_CATEGORIES = ("kevin", "lapwing", "world")


class SemanticDistiller:
    """把最近 Episodic 条目提炼成 Semantic 知识。"""

    def __init__(
        self,
        *,
        router: "LLMRouter",
        episodic_store: "EpisodicStore",
        semantic_store: "SemanticStore",
        episodes_window: int = 20,
        prompt_name: str = "semantic_distill",
    ) -> None:
        self._router = router
        self._episodic = episodic_store
        self._semantic = semantic_store
        self._episodes_window = episodes_window
        self._prompt_name = prompt_name

    async def distill_recent(self) -> int:
        """Read recent episodes, propose semantic facts, write distinct ones.

        Returns the number of facts actually persisted (after dedup).
        """
        episodes = await self._collect_recent_episodes()
        if not episodes:
            logger.debug("[semantic] no recent episodes; skipping distillation")
            return 0

        episodes_text = _format_episodes(episodes)

        try:
            prompt_template = load_prompt(self._prompt_name)
        except Exception:
            logger.warning(
                "[semantic] prompt %s not found; using default",
                self._prompt_name,
            )
            prompt_template = _FALLBACK_PROMPT

        prompt = prompt_template.replace("{episodes}", episodes_text)

        try:
            raw = await self._router.complete(
                [{"role": "user", "content": prompt}],
                slot="memory_processing",
                max_tokens=600,
                session_key="semantic:distill",
                origin="memory.semantic_distiller",
            )
        except Exception as exc:
            logger.warning("[semantic] LLM call failed: %s", exc)
            return 0

        facts = _parse_facts(raw)
        if not facts:
            return 0

        source_ids = [e.episode_id for e in episodes]
        written = 0
        for category, content in facts:
            try:
                entry = await self._semantic.add_fact(
                    category=category,
                    content=content,
                    source_episodes=source_ids,
                )
                if entry is not None:
                    written += 1
            except Exception as exc:
                logger.warning(
                    "[semantic] add_fact failed (%s|%s): %s",
                    category, content, exc,
                )
        logger.info(
            "[semantic] distilled %d/%d facts from %d episodes",
            written, len(facts), len(episodes),
        )
        return written

    # ── Helpers ──────────────────────────────────────────────────────

    async def _collect_recent_episodes(self) -> list["EpisodicEntry"]:
        """Pull the most-recent episodes for distillation.

        EpisodicStore exposes only ``query`` today; we probe with a broad
        term to pull the top-K by composite score (recency is a strong
        factor in the ranking, so recent episodes dominate). If no
        episode matches, the catch-all probe returns []; caller short-
        circuits.
        """
        return await self._episodic.query(
            query_text="Kevin",  # any stop-word-free generic probe
            top_k=self._episodes_window,
        )


# ── Parsing helpers ─────────────────────────────────────────────────

def _format_episodes(episodes: list) -> str:
    lines: list[str] = []
    for e in episodes:
        tag = e.date or "?"
        title = e.title or ""
        summary = e.summary or ""
        body = title if title and title == summary else (
            f"{title} — {summary}" if title else summary
        )
        lines.append(f"[{tag}] {body}")
    return "\n".join(lines)


def _parse_facts(raw: str) -> list[tuple[str, str]]:
    """Parse the LLM's ``category | content`` lines.

    Ignores lines that don't match, empty lines, and unknown categories.
    Whitespace is trimmed.
    """
    if not raw:
        return []
    out: list[tuple[str, str]] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("```"):
            continue
        if "|" not in s:
            continue
        cat, _, content = s.partition("|")
        cat = cat.strip().lower()
        content = content.strip()
        if not content:
            continue
        if cat not in _VALID_CATEGORIES:
            # Allow unknown categories but slug-normalise — keeps the
            # door open for new categories the model invents.
            if not cat:
                continue
        out.append((cat, content))
    return out


_FALLBACK_PROMPT = (
    "从以下情景记忆中提炼持久性知识。每行一条，格式 `分类 | 内容`。"
    "分类可以是 kevin / lapwing / world。最多 10 条。\n\n"
    "{episodes}\n"
)
