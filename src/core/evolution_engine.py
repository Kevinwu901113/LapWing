"""Diff-based 人格进化引擎。"""

import asyncio
import json
import logging
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.settings import (
    CHANGELOG_PATH,
    DATA_DIR,
    JOURNAL_DIR,
    RULES_PATH,
    SOUL_PATH,
)
from src.core.prompt_loader import load_prompt

logger = logging.getLogger("lapwing.evolution_engine")

_BACKUP_DIR = DATA_DIR / "backups" / "soul"


class EvolutionEngine:
    """基于 diff 的人格微进化。"""

    def __init__(self, router, constitution_guard):
        self._router = router
        self._guard = constitution_guard
        _BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    async def evolve(self) -> dict:
        """执行一次进化。

        Returns:
            {"success": bool, "changes": list, "summary": str, "error": str}
        """
        # 1. 收集输入材料
        current_soul = await self._read_soul()
        if not current_soul:
            return {"success": False, "error": "无法读取当前 soul.md"}

        rules = await self._read_file(RULES_PATH)
        recent_journals = await self._read_recent_journals(3)

        if not rules and not recent_journals:
            return {"success": False, "error": "没有规则和日记作为进化依据"}

        # 2. 让 LLM 提出 diff
        prompt = load_prompt("evolution_diff").format(
            current_soul=current_soul,
            rules=rules or "（暂无规则）",
            recent_journals=recent_journals or "（暂无日记）",
        )

        try:
            raw = await self._router.complete(
                [{"role": "user", "content": prompt}],
                purpose="chat",
                max_tokens=2048,
                session_key="system:evolution_engine",
                origin="core.evolution_engine",
            )
        except Exception as exc:
            return {"success": False, "error": f"LLM 调用失败: {exc}"}

        # 3. 解析 diff
        changes = self._parse_diff(raw)
        if not changes.get("diffs"):
            summary = changes.get("summary", "无变更")
            return {"success": False, "error": f"无有效变更: {summary}"}

        diffs = changes["diffs"]
        summary = changes.get("summary", "")

        # 4. 数量检查（宪法：最多5处）
        if len(diffs) > 5:
            return {
                "success": False,
                "error": f"提议了 {len(diffs)} 处变更，超过宪法限制的 5 处",
            }

        # 5. 宪法校验
        validation = await self._guard.validate_evolution(current_soul, diffs)
        if not validation["approved"]:
            reasons = "; ".join(validation["violations"])
            logger.warning(f"[evolution] 宪法校验未通过: {reasons}")
            await self._log_change(f"❌ 进化被宪法拒绝: {reasons}")
            return {"success": False, "error": f"宪法校验未通过: {reasons}"}

        # 6. 应用 diff
        new_soul = self._apply_diffs(current_soul, diffs)

        # 7. 硬约束最终检查
        hard_violations = self._guard.validate_hard_constraints(new_soul)
        if hard_violations:
            reasons = "; ".join(hard_violations)
            return {"success": False, "error": f"硬约束检查未通过: {reasons}"}

        # 8. 备份 + 写入
        await self._backup_soul()
        await asyncio.to_thread(
            SOUL_PATH.write_text, new_soul, encoding="utf-8"
        )

        # 9. 记录变更日志
        diff_descriptions = "\n".join(
            f"  - [{d['action']}] {d['description']}" for d in diffs
        )
        await self._log_change(f"✅ 进化完成\n{diff_descriptions}\n  摘要: {summary}")

        logger.info(f"[evolution] 进化完成: {summary}")
        return {
            "success": True,
            "changes": diffs,
            "summary": summary,
        }

    async def revert(self) -> dict:
        """回滚到最近一次备份。

        Returns:
            {"success": bool, "reverted_to": str, "error": str}
        """
        if not _BACKUP_DIR.exists():
            return {"success": False, "error": "备份目录不存在"}

        backups = sorted(_BACKUP_DIR.glob("soul_*.md"), reverse=True)
        if not backups:
            return {"success": False, "error": "没有可用的备份"}

        latest = backups[0]
        try:
            backup_content = await asyncio.to_thread(latest.read_text, encoding="utf-8")
            # 先备份当前的
            await self._backup_soul()
            # 写入旧版本
            await asyncio.to_thread(SOUL_PATH.write_text, backup_content, encoding="utf-8")
            await self._log_change(f"⏪ 回滚到备份: {latest.name}")
            return {"success": True, "reverted_to": latest.name}
        except Exception as exc:
            return {"success": False, "error": f"回滚失败: {exc}"}

    def _parse_diff(self, text: str) -> dict:
        """解析 LLM 返回的 diff JSON。"""
        try:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                return {"diffs": [], "summary": "无法解析"}
            data = json.loads(match.group())
            return {
                "diffs": data.get("diffs", []),
                "summary": data.get("summary", ""),
            }
        except Exception as exc:
            logger.warning(f"解析进化 diff 失败: {exc}")
            return {"diffs": [], "summary": "解析失败"}

    def _apply_diffs(self, soul: str, diffs: list[dict]) -> str:
        """将 diff 应用到 soul 文本。

        每个 diff: {"action": "add/modify/remove", "description": "...",
                    "location": "section or keyword", "content": "new text"}
        """
        for diff in diffs:
            action = diff.get("action", "")
            content = diff.get("content", "").strip()
            location = diff.get("location", "").strip()

            if action == "add" and content:
                if location and location in soul:
                    idx = soul.index(location) + len(location)
                    soul = soul[:idx] + "\n" + content + soul[idx:]
                else:
                    soul = soul.rstrip() + "\n\n" + content + "\n"

            elif action == "modify" and location and content:
                if location in soul:
                    soul = soul.replace(location, content, 1)

            elif action == "remove" and location:
                if location in soul:
                    soul = soul.replace(location, "", 1)

        return soul

    async def _read_soul(self) -> str:
        if not SOUL_PATH.exists():
            return ""
        return await asyncio.to_thread(SOUL_PATH.read_text, encoding="utf-8")

    async def _read_file(self, path: Path) -> str:
        if not path.exists():
            return ""
        return await asyncio.to_thread(path.read_text, encoding="utf-8")

    async def _read_recent_journals(self, days: int) -> str:
        today = datetime.now(timezone.utc)
        parts = []

        # 新路径优先，旧路径兜底
        dirs_to_check: list[Path] = []
        if JOURNAL_DIR.exists():
            dirs_to_check.append(JOURNAL_DIR)
        old_learnings = DATA_DIR / "learnings"
        if old_learnings.exists():
            dirs_to_check.append(old_learnings)

        seen_dates: set[str] = set()
        for journal_dir in dirs_to_check:
            for i in range(days):
                date_str = (today - timedelta(days=i)).strftime("%Y-%m-%d")
                if date_str in seen_dates:
                    continue
                path = journal_dir / f"{date_str}.md"
                if path.exists():
                    try:
                        text = await asyncio.to_thread(path.read_text, encoding="utf-8")
                        parts.append(text.strip())
                        seen_dates.add(date_str)
                    except Exception:
                        continue

        return "\n\n---\n\n".join(parts)

    async def _backup_soul(self) -> Path:
        if not SOUL_PATH.exists():
            return _BACKUP_DIR / "soul_missing.md"
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_path = _BACKUP_DIR / f"soul_{ts}.md"
        _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copy2, SOUL_PATH, backup_path)
        return backup_path

    async def _log_change(self, text: str) -> None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        entry = f"\n---\n\n## {date_str}\n\n{text}\n"

        def _append():
            CHANGELOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            if CHANGELOG_PATH.exists():
                existing = CHANGELOG_PATH.read_text(encoding="utf-8")
                CHANGELOG_PATH.write_text(existing + entry, encoding="utf-8")
            else:
                CHANGELOG_PATH.write_text(f"# 进化日志\n{entry}", encoding="utf-8")

        await asyncio.to_thread(_append)
