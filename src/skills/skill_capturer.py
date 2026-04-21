"""自动技能捕获：从工具调用轨迹中提炼可复用 skill。

触发时机：3AM 维护窗口（MaintenanceTimer._run_daily）。
流程：
  1. 从 TrajectoryStore 读取过去 24h 的 tool call 链
  2. 筛选"成功且涉及 3+ 步工具调用"的任务
  3. 用 LLM 判断是否值得提炼为 skill
  4. 如果值得，用 LLM 生成 SKILL.md
  5. 调用 SkillStore.create() 存储，maturity 固定为 draft
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.core.llm_router import LLMRouter
    from src.core.trajectory_store import TrajectoryStore
    from src.skills.skill_store import SkillStore

logger = logging.getLogger("lapwing.skills.skill_capturer")

_MAX_CAPTURES_PER_RUN = 3
_MIN_TOOL_CALLS = 3
_WINDOW_SECONDS = 24 * 3600

_JUDGE_PROMPT = """\
你刚刚完成了一个任务。以下是你使用的工具调用链：
{tool_call_chain}

请判断这个任务是否值得总结为一个可复用的技能（skill）。

判断标准：
- 这个任务以后可能会再做吗？
- 步骤是否有固定的模式？
- 有没有容易犯的错误值得记住？

如果值得，用以下格式输出 skill 内容：
---
name: <技能名>
description: <一句话描述>
category: <分类>
---
## 适用场景
<什么时候该用这个技能>

## 步骤
<具体怎么做>

## 容易出错的地方
<踩过的坑>

## 验证方法
<怎么确认做对了>

如果不值得，只输出：SKIP"""


def _fingerprint(tool_names: list[str]) -> str:
    raw = "|".join(tool_names)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _parse_skill_response(text: str) -> dict | None:
    """Parse LLM response into skill fields. Returns None if SKIP."""
    stripped = text.strip()
    if stripped.upper().startswith("SKIP"):
        return None

    match = re.search(
        r"---\s*\n(.*?)\n---",
        stripped,
        re.DOTALL,
    )
    if not match:
        return None

    try:
        import yaml
        front = yaml.safe_load(match.group(1))
    except Exception:
        return None

    if not isinstance(front, dict) or "name" not in front:
        return None

    body_start = match.end()
    body = stripped[body_start:].strip()

    return {
        "name": front.get("name", ""),
        "description": front.get("description", ""),
        "category": front.get("category", "general"),
        "body": body,
    }


class SkillCapturer:
    """Scan recent trajectory for multi-step tool chains worth capturing."""

    async def maybe_capture_skills(
        self,
        trajectory_store: "TrajectoryStore",
        skill_store: "SkillStore",
        llm_router: "LLMRouter",
    ) -> list[str]:
        """Return list of newly created skill_ids (may be empty)."""
        from src.core.trajectory_store import TrajectoryEntryType

        now = time.time()
        entries = await trajectory_store.in_window(
            now - _WINDOW_SECONDS, now, limit=2000,
        )

        candidates = self._extract_candidates(entries, TrajectoryEntryType)
        if not candidates:
            return []

        existing_fps = self._existing_fingerprints(skill_store)
        candidates = [c for c in candidates if c["fingerprint"] not in existing_fps]
        candidates = candidates[:_MAX_CAPTURES_PER_RUN]

        created: list[str] = []
        for candidate in candidates:
            try:
                skill_id = await self._try_capture_one(
                    candidate, skill_store, llm_router,
                )
                if skill_id:
                    created.append(skill_id)
            except Exception:
                logger.warning(
                    "skill capture failed for candidate %s",
                    candidate["fingerprint"],
                    exc_info=True,
                )
        return created

    def _extract_candidates(
        self,
        entries: list[Any],
        entry_types: Any,
    ) -> list[dict]:
        """Group entries by iteration and filter for 3+ tool calls."""
        iterations: dict[str | None, list] = {}
        for e in entries:
            it_id = e.related_iteration_id
            if it_id is None:
                continue
            iterations.setdefault(it_id, []).append(e)

        candidates = []
        for it_id, group in iterations.items():
            tool_calls = [
                e for e in group
                if e.entry_type == entry_types.TOOL_CALL.value
            ]
            if len(tool_calls) < _MIN_TOOL_CALLS:
                continue

            tool_names = [
                e.content.get("tool_name", e.content.get("name", "unknown"))
                for e in tool_calls
            ]
            fp = _fingerprint(tool_names)

            chain_desc = self._format_chain(group, entry_types)
            candidates.append({
                "iteration_id": it_id,
                "tool_names": tool_names,
                "fingerprint": fp,
                "chain_description": chain_desc,
                "tool_count": len(tool_calls),
            })

        candidates.sort(key=lambda c: c["tool_count"], reverse=True)
        return candidates

    def _format_chain(self, entries: list[Any], entry_types: Any) -> str:
        lines = []
        for e in entries:
            if e.entry_type == entry_types.TOOL_CALL.value:
                name = e.content.get("tool_name", e.content.get("name", "?"))
                args_str = str(e.content.get("arguments", ""))[:200]
                lines.append(f"→ 调用工具: {name}({args_str})")
            elif e.entry_type == entry_types.TOOL_RESULT.value:
                success = e.content.get("success", True)
                snippet = str(e.content.get("output", ""))[:100]
                status = "✓" if success else "✗"
                lines.append(f"  {status} 结果: {snippet}")
            elif e.entry_type == entry_types.USER_MESSAGE.value:
                text = str(e.content.get("text", ""))[:200]
                lines.append(f"用户: {text}")
        return "\n".join(lines)

    def _existing_fingerprints(self, skill_store: "SkillStore") -> set[str]:
        fps: set[str] = set()
        for meta in skill_store.list_skills():
            if meta.get("origin") == "captured":
                fp = meta.get("capture_fingerprint")
                if fp:
                    fps.add(fp)
        return fps

    async def _try_capture_one(
        self,
        candidate: dict,
        skill_store: "SkillStore",
        llm_router: "LLMRouter",
    ) -> str | None:
        prompt = _JUDGE_PROMPT.replace(
            "{tool_call_chain}", candidate["chain_description"],
        )
        response = await llm_router.complete(
            [{"role": "user", "content": prompt}],
            slot="tool",
            max_tokens=1500,
            origin="skills.capturer",
        )

        parsed = _parse_skill_response(response)
        if parsed is None:
            logger.debug(
                "LLM judged candidate %s as not worth capturing",
                candidate["fingerprint"],
            )
            return None

        name = parsed["name"]
        skill_id = re.sub(r"[^a-z0-9_-]", "_", name.lower().strip())[:40]
        if not skill_id:
            skill_id = f"captured_{candidate['fingerprint'][:8]}"

        body = parsed["body"]
        code = f"# 自动捕获的技能: {name}\n\n{body}"

        try:
            result = skill_store.create(
                skill_id=skill_id,
                name=name,
                description=parsed["description"],
                code=code,
                category=parsed["category"],
                origin="captured",
            )
            self._patch_fingerprint(skill_store, skill_id, candidate["fingerprint"])

            logger.info(
                "captured skill %s from iteration %s (%d tool calls)",
                skill_id, candidate["iteration_id"], candidate["tool_count"],
            )
            return skill_id
        except FileExistsError:
            logger.debug("skill %s already exists, skipping", skill_id)
            return None

    @staticmethod
    def _patch_fingerprint(
        skill_store: "SkillStore", skill_id: str, fingerprint: str,
    ) -> None:
        """Write capture_fingerprint into the SKILL.md YAML frontmatter."""
        import yaml as _yaml

        skill = skill_store.read(skill_id)
        if skill is None:
            return
        meta = skill["meta"]
        meta["capture_fingerprint"] = fingerprint
        code = skill.get("code", "")
        skill_dir = skill_store.skills_dir / skill_id
        frontmatter = _yaml.dump(meta, allow_unicode=True, sort_keys=False)
        body = f"## 代码\n\n```python\n{code}\n```"
        (skill_dir / "SKILL.md").write_text(
            f"---\n{frontmatter}---\n{body}", encoding="utf-8",
        )
