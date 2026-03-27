"""Prompt 自我优化引擎 — 基于学习日志定期更新 Lapwing 人格 prompt。"""

import asyncio
import json
import logging
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.settings import DATA_DIR, ROOT_DIR
from src.core.prompt_loader import load_prompt, reload_prompt

logger = logging.getLogger("lapwing.prompt_evolver")

_PROMPTS_DIR = ROOT_DIR / "prompts"
_LAPWING_PROMPT_PATH = _PROMPTS_DIR / "lapwing.md"
_BACKUP_DIR = DATA_DIR / "backups" / "prompts"
_LEARNINGS_DIR = DATA_DIR / "learnings"

# 核心身份标记，缺失任何一个都拒绝应用新 prompt
_REQUIRED_MARKERS = [
    "Lapwing",
    "白发蓝眸",
    "不要自称 AI",
]


class PromptEvolver:
    """基于学习日志自动优化 Lapwing 人格 prompt。"""

    def __init__(self, memory, router) -> None:
        self._memory = memory
        self._router = router
        _BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    async def evolve(self) -> dict:
        """执行一次 prompt 进化。

        Returns:
            {"success": bool, "changes_summary": str, "backup_path": str, "error": str}
        """
        learnings = await asyncio.to_thread(self._load_recent_learnings, 7)
        if not learnings.strip():
            return {
                "success": False,
                "error": "最近7天没有学习日志，无法进行优化。",
            }

        current_prompt = await asyncio.to_thread(_LAPWING_PROMPT_PATH.read_text, encoding="utf-8")

        evolver_prompt_template = load_prompt("prompt_evolver")
        evolver_prompt = (
            evolver_prompt_template
            .replace("{learnings_text}", learnings)
            .replace("{current_prompt}", current_prompt)
        )

        try:
            raw = await self._router.complete(
                [{"role": "user", "content": evolver_prompt}],
                purpose="chat",  # 使用高质量模型
                max_tokens=4096,
                session_key="system:prompt_evolver",
                origin="core.prompt_evolver",
            )
        except Exception as exc:
            logger.error(f"[prompt_evolver] LLM 调用失败: {exc}")
            return {"success": False, "error": f"LLM 调用失败: {exc}"}

        # 解析 JSON 响应
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            logger.warning(f"[prompt_evolver] LLM 未返回 JSON: {raw[:200]}")
            return {"success": False, "error": "LLM 未返回有效 JSON"}

        try:
            result = json.loads(match.group())
        except json.JSONDecodeError as exc:
            logger.warning(f"[prompt_evolver] JSON 解析失败: {exc}")
            return {"success": False, "error": f"JSON 解析失败: {exc}"}

        new_prompt = result.get("new_prompt", "").strip()
        changes_summary = result.get("changes_summary", "（无摘要）")

        if not new_prompt:
            return {"success": False, "error": "LLM 未返回新 prompt 内容"}

        # 安全检查
        ok, missing = self._safety_check(new_prompt)
        if not ok:
            logger.warning(f"[prompt_evolver] 安全检查未通过，缺少: {missing}")
            return {
                "success": False,
                "error": f"新 prompt 缺少必要的核心标记，进化已中止: {missing}",
            }

        # 备份当前版本
        backup_path = await asyncio.to_thread(self._backup_current)

        # 写入新版本
        await asyncio.to_thread(_LAPWING_PROMPT_PATH.write_text, new_prompt, encoding="utf-8")
        reload_prompt("lapwing")

        logger.info(f"[prompt_evolver] prompt 已更新，备份: {backup_path.name}")
        logger.info(f"[prompt_evolver] 变更摘要: {changes_summary}")

        return {
            "success": True,
            "changes_summary": changes_summary,
            "backup_path": str(backup_path),
        }

    async def revert(self) -> dict:
        """回滚到最近一次备份。

        Returns:
            {"success": bool, "reverted_to": str, "error": str}
        """
        backups = sorted(_BACKUP_DIR.glob("lapwing_*.md"), reverse=True)
        if not backups:
            return {"success": False, "error": "没有找到备份文件。"}

        latest = backups[0]
        content = await asyncio.to_thread(latest.read_text, encoding="utf-8")

        # 写入前先备份当前版本（以防反悔）
        await asyncio.to_thread(self._backup_current)
        await asyncio.to_thread(_LAPWING_PROMPT_PATH.write_text, content, encoding="utf-8")
        reload_prompt("lapwing")

        logger.info(f"[prompt_evolver] 已回滚到: {latest.name}")
        return {"success": True, "reverted_to": latest.name}

    def _safety_check(self, new_prompt: str) -> tuple[bool, list[str]]:
        """验证新 prompt 包含所有必要的核心身份标记。"""
        missing = [m for m in _REQUIRED_MARKERS if m not in new_prompt]
        return (len(missing) == 0), missing

    def _backup_current(self) -> Path:
        """备份当前 lapwing.md，返回备份路径。"""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_path = _BACKUP_DIR / f"lapwing_{ts}.md"
        shutil.copy2(_LAPWING_PROMPT_PATH, backup_path)
        return backup_path

    def _load_recent_learnings(self, days: int) -> str:
        """读取最近 N 天的学习日志，拼接成文本。"""
        today = datetime.now(timezone.utc)
        parts = []
        for i in range(days):
            date_str = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            path = _LEARNINGS_DIR / f"{date_str}.md"
            if path.exists():
                try:
                    parts.append(path.read_text(encoding="utf-8").strip())
                except Exception as exc:
                    logger.warning(f"[prompt_evolver] 读取学习日志失败 {date_str}: {exc}")
        return "\n\n---\n\n".join(parts)
