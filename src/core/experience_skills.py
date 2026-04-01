"""Lapwing 经验技能系统 — 管理 Lapwing 自身积累的工作经验。

与 src/core/skills.py 的插件式技能系统独立共存。
插件系统管理外部 SKILL.md 文件，本系统管理 Lapwing 自己写下的经验笔记。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from src.core.llm_router import LLMRouter

logger = logging.getLogger("lapwing.core.experience_skills")

_INITIAL_CATEGORIES = ["research", "coding", "daily", "content", "system"]
_MAX_INJECT_SKILLS = 3
_SUMMARY_MAX_CHARS = 150

_SKILL_MATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "selected": {
            "type": "array",
            "items": {"type": "string"},
            "description": "匹配的 skill ID 列表",
        },
    },
    "required": ["selected"],
}


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExperienceSkillMeta:
    id: str
    name: str
    category: str
    status: str  # "draft" | "active" | "deprecated"
    created: str
    updated: str
    source: str  # "trace" | "taught" | "preset" | "split" | "merged"
    parent_skills: list[str]
    version: int
    use_count: int
    last_used: str | None
    success_rate: float
    agents: list[str]
    tools: list[str]
    size_tokens: int


@dataclass(frozen=True)
class ExperienceSkill:
    meta: ExperienceSkillMeta
    body: str
    file_path: Path


@dataclass(frozen=True)
class MatchResult:
    skill_id: str
    match_level: str  # "quick" | "index"
    score: float


# ---------------------------------------------------------------------------
# 索引条目（_index.json 中的每一项）
# ---------------------------------------------------------------------------


@dataclass
class _IndexEntry:
    id: str
    name: str
    category: str
    status: str
    summary: str
    agents: list[str]
    use_count: int
    last_used: str | None
    success_rate: float
    size_tokens: int


# ---------------------------------------------------------------------------
# ExperienceSkillManager
# ---------------------------------------------------------------------------


class ExperienceSkillManager:
    """管理 Lapwing 自身积累的经验技能。"""

    def __init__(
        self,
        skills_dir: Path,
        traces_dir: Path,
        router: "LLMRouter",
    ) -> None:
        self._skills_dir = skills_dir
        self._traces_dir = traces_dir
        self._router = router
        self._index: list[_IndexEntry] = []
        self._index_loaded = False

        # 子系统：轨迹记录器和使用统计
        from src.core.trace_recorder import TraceRecorder
        from src.core.skill_registry import SkillRegistryManager
        self.trace_recorder = TraceRecorder(traces_dir)
        self.registry_manager = SkillRegistryManager(skills_dir / "_registry.json")

    # ------------------------------------------------------------------
    # 目录初始化
    # ------------------------------------------------------------------

    def ensure_directories(self) -> None:
        """创建 skills/ 目录结构和 skill_traces/ 目录，初始化 registry。"""
        self._skills_dir.mkdir(parents=True, exist_ok=True)
        for cat in _INITIAL_CATEGORIES:
            (self._skills_dir / cat).mkdir(exist_ok=True)
        self._traces_dir.mkdir(parents=True, exist_ok=True)
        self.trace_recorder.ensure_dir()
        # 初始化 registry（如果还未加载）
        if not self.registry_manager._loaded:
            self.registry_manager.load()
        logger.info("经验技能目录已就绪: %s", self._skills_dir)

    # ------------------------------------------------------------------
    # 索引加载与重建
    # ------------------------------------------------------------------

    def load_index(self) -> None:
        """加载 _index.json，不存在则重建。"""
        index_path = self._skills_dir / "_index.json"
        if index_path.exists():
            try:
                data = json.loads(index_path.read_text(encoding="utf-8"))
                self._index = [self._deserialize_entry(e) for e in data.get("skills", [])]
                self._index_loaded = True
                active_count = sum(1 for e in self._index if e.status in ("active", "draft"))
                logger.info("经验技能索引已加载：%d 个可用技能", active_count)
                return
            except Exception as exc:
                logger.warning("读取 _index.json 失败，重建中: %s", exc)

        self.rebuild_index()

    def rebuild_index(self) -> None:
        """扫描所有技能文件，重建并持久化 _index.json。"""
        if not self._skills_dir.exists():
            self._index = []
            self._index_loaded = True
            return

        entries: list[_IndexEntry] = []
        categories: set[str] = set()

        for category_dir in sorted(self._skills_dir.iterdir()):
            if not category_dir.is_dir() or category_dir.name.startswith("_"):
                continue
            categories.add(category_dir.name)
            for skill_file in sorted(category_dir.glob("*.md")):
                skill = self.parse_skill_file(skill_file)
                if skill is None:
                    continue
                summary = _extract_summary(skill.body)
                entries.append(
                    _IndexEntry(
                        id=skill.meta.id,
                        name=skill.meta.name,
                        category=skill.meta.category,
                        status=skill.meta.status,
                        summary=summary,
                        agents=skill.meta.agents,
                        use_count=skill.meta.use_count,
                        last_used=skill.meta.last_used,
                        success_rate=skill.meta.success_rate,
                        size_tokens=skill.meta.size_tokens,
                    )
                )

        # 按 use_count 降序排列（高频在前）
        entries.sort(key=lambda e: e.use_count, reverse=True)
        self._index = entries
        self._index_loaded = True

        active_count = sum(1 for e in entries if e.status != "deprecated")
        index_data = {
            "version": 1,
            "updated": datetime.now(timezone.utc).isoformat(),
            "skill_count": active_count,
            "categories": sorted(categories),
            "skills": [self._serialize_entry(e) for e in entries],
        }

        index_path = self._skills_dir / "_index.json"
        try:
            index_path.write_text(json.dumps(index_data, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("经验技能索引已重建：%d 个技能", len(entries))
        except Exception as exc:
            logger.warning("写入 _index.json 失败: %s", exc)

    # ------------------------------------------------------------------
    # 技能文件解析
    # ------------------------------------------------------------------

    def parse_skill_file(self, path: Path) -> ExperienceSkill | None:
        """解析单个经验技能 Markdown 文件。"""
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("读取技能文件失败 %s: %s", path, exc)
            return None

        frontmatter, body, error = _split_frontmatter(raw)
        if error:
            logger.warning("解析 %s 失败: %s", path, error)
            return None

        try:
            fm = yaml.safe_load(frontmatter)
        except yaml.YAMLError as exc:
            logger.warning("YAML 解析 %s 失败: %s", path, exc)
            return None

        if not isinstance(fm, dict):
            logger.warning("技能文件 %s frontmatter 不是字典", path)
            return None

        # 校验必填字段
        skill_id = str(fm.get("id", "")).strip()
        if not skill_id:
            logger.warning("技能文件 %s 缺少 id 字段", path)
            return None
        if skill_id != path.stem:
            logger.warning("技能 %s 的 id '%s' 与文件名不符（期望 '%s'）", path, skill_id, path.stem)
            return None

        category = str(fm.get("category", "")).strip()
        if category != path.parent.name:
            logger.warning(
                "技能 %s 的 category '%s' 与目录名不符（期望 '%s'）",
                path, category, path.parent.name,
            )
            return None

        name = str(fm.get("name", "")).strip()
        if not name:
            logger.warning("技能文件 %s 缺少 name 字段", path)
            return None

        status = str(fm.get("status", "draft")).strip()
        if status not in ("draft", "active", "deprecated"):
            logger.warning("技能 %s status 值非法: %s", path, status)
            return None

        last_used_raw = fm.get("last_used")
        last_used = str(last_used_raw) if last_used_raw is not None else None

        meta = ExperienceSkillMeta(
            id=skill_id,
            name=name,
            category=category,
            status=status,
            created=str(fm.get("created", "")).strip(),
            updated=str(fm.get("updated", "")).strip(),
            source=str(fm.get("source", "preset")).strip(),
            parent_skills=_normalize_list(fm.get("parent_skills")),
            version=int(fm.get("version", 1)),
            use_count=int(fm.get("use_count", 0)),
            last_used=last_used,
            success_rate=float(fm.get("success_rate", 0.0)),
            agents=_normalize_list(fm.get("agents")),
            tools=_normalize_list(fm.get("tools")),
            size_tokens=int(fm.get("size_tokens", 0)),
        )

        return ExperienceSkill(meta=meta, body=body, file_path=path)

    # ------------------------------------------------------------------
    # 索引匹配（LLM 选择）
    # ------------------------------------------------------------------

    async def index_match(
        self,
        user_request: str,
        candidates: list[_IndexEntry] | None = None,
    ) -> list[MatchResult]:
        """LLM 从技能摘要列表中选择最相关的 0-3 个技能。"""
        if not self._index_loaded:
            self.load_index()

        entries = candidates if candidates is not None else [
            e for e in self._index if e.status in ("active", "draft")
        ]
        if not entries:
            return []

        numbered_lines = [
            f"{i + 1}. {entry.name} — {entry.summary}"
            for i, entry in enumerate(entries)
        ]
        skill_list = "\n".join(numbered_lines)

        prompt = (
            f"以下是我积累的经验列表：\n\n{skill_list}\n\n"
            f"当前任务：{user_request}\n\n"
            "请从上面的经验列表中，选出对当前任务最有参考价值的 0-3 个（按相关度排序）。"
            "如果没有任何一条真正相关，直接返回空列表。"
            "\n\n请使用 skill_match 工具提交你的选择。"
        )

        try:
            parsed = await self._router.complete_structured(
                messages=[{"role": "user", "content": prompt}],
                result_schema=_SKILL_MATCH_SCHEMA,
                result_tool_name="skill_match",
                result_tool_description="提交匹配的技能 ID 列表",
                slot="lightweight_judgment",
            )
        except Exception as exc:
            logger.warning("Level 2 技能匹配 LLM 调用失败: %s", exc)
            return []

        selected_ids = parsed.get("selected", [])
        if not isinstance(selected_ids, list):
            return []

        # 按 entries 中的顺序构建 id->entry 映射
        entry_by_id = {e.id: e for e in entries}
        results: list[MatchResult] = []
        for sel_id in selected_ids[:_MAX_INJECT_SKILLS]:
            if isinstance(sel_id, str) and sel_id in entry_by_id:
                results.append(MatchResult(skill_id=sel_id, match_level="index", score=1.0))

        return results

    # ------------------------------------------------------------------
    # 检索入口
    # ------------------------------------------------------------------

    async def retrieve(self, user_request: str) -> list[ExperienceSkill]:
        """Retrieve 0-3 relevant experience skills via LLM index matching."""
        if not self._index_loaded:
            self.load_index()

        if not self._index:
            return []

        # Direct LLM index matching (no keyword/regex pre-filter)
        match_results = await self.index_match(user_request)

        # Load full skill content
        skills: list[ExperienceSkill] = []
        for result in match_results:
            skill = self._load_skill_by_id(result.skill_id)
            if skill is not None:
                skills.append(skill)

        return skills

    def _load_skill_by_id(self, skill_id: str) -> ExperienceSkill | None:
        """根据 id 定位并解析技能文件。"""
        # 从索引中找 category
        for entry in self._index:
            if entry.id == skill_id:
                skill_file = self._skills_dir / entry.category / f"{skill_id}.md"
                if skill_file.exists():
                    return self.parse_skill_file(skill_file)
                break
        logger.warning("技能文件未找到: %s", skill_id)
        return None

    # ------------------------------------------------------------------
    # 注入格式化
    # ------------------------------------------------------------------

    def format_injection(
        self,
        skills: list[ExperienceSkill],
        max_tokens: int = 4000,
    ) -> str:
        """将技能列表格式化为注入到 system prompt 的文本。

        若总 token 估算超出预算，丢弃排在后面的（低分）技能。
        """
        if not skills:
            return ""

        parts: list[str] = []
        token_estimate = 0

        for skill in skills:
            body = skill.body.strip()
            # 粗略 token 估算：中文约 1.5 char/token，英文约 4 char/token，取中间值 2
            estimated = len(body) // 2
            if parts and token_estimate + estimated > max_tokens:
                break
            parts.append(
                f"---参考经验开始---\n{body}\n---参考经验结束---"
            )
            token_estimate += estimated

        if not parts:
            return ""

        combined = "\n\n".join(parts)
        combined += (
            "\n\n以上是我处理类似任务时积累的经验。我会参考它来处理当前任务，"
            "但会根据具体情况灵活调整——它是指南，不是必须严格遵循的脚本。"
            "如果某个步骤在当前场景下不适用，我会跳过或替换。"
        )
        return combined

    # ------------------------------------------------------------------
    # 技能统计更新
    # ------------------------------------------------------------------

    def update_skill_stats(
        self,
        skill_id: str,
        *,
        used: bool = True,
        success: bool | None = None,
    ) -> None:
        """更新技能的 use_count / last_used / success_rate，回写文件。"""
        for entry in self._index:
            if entry.id == skill_id:
                skill_file = self._skills_dir / entry.category / f"{skill_id}.md"
                break
        else:
            return

        if not skill_file.exists():
            return

        skill = self.parse_skill_file(skill_file)
        if skill is None:
            return

        fm = _load_frontmatter_dict(skill_file)
        if fm is None:
            return

        if used:
            fm["use_count"] = skill.meta.use_count + 1
            fm["last_used"] = date.today().isoformat()

        if success is not None and used:
            old_count = skill.meta.use_count
            old_rate = skill.meta.success_rate
            new_count = old_count + 1
            # 加权平均
            fm["success_rate"] = round(
                (old_rate * old_count + (1.0 if success else 0.0)) / new_count, 2
            )

        fm["updated"] = date.today().isoformat()

        try:
            _write_frontmatter_back(skill_file, fm, skill.body)
        except Exception as exc:
            logger.warning("回写技能统计失败 %s: %s", skill_id, exc)
            return

        # 更新内存索引
        for entry in self._index:
            if entry.id == skill_id:
                entry.use_count = int(fm.get("use_count", 0))
                entry.last_used = fm.get("last_used")
                entry.success_rate = float(fm.get("success_rate", 0.0))
                break

    # ------------------------------------------------------------------
    # 辅助方法：索引序列化/反序列化
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize_entry(e: _IndexEntry) -> dict[str, Any]:
        return {
            "id": e.id,
            "name": e.name,
            "category": e.category,
            "status": e.status,
            "summary": e.summary,
            "agents": e.agents,
            "use_count": e.use_count,
            "last_used": str(e.last_used) if e.last_used is not None else None,
            "success_rate": e.success_rate,
            "size_tokens": e.size_tokens,
        }

    @staticmethod
    def _deserialize_entry(d: dict[str, Any]) -> _IndexEntry:
        return _IndexEntry(
            id=d.get("id", ""),
            name=d.get("name", ""),
            category=d.get("category", ""),
            status=d.get("status", "active"),
            summary=d.get("summary", ""),
            agents=_normalize_list(d.get("agents")),
            use_count=int(d.get("use_count", 0)),
            last_used=d.get("last_used"),
            success_rate=float(d.get("success_rate", 0.0)),
            size_tokens=int(d.get("size_tokens", 0)),
        )


# ---------------------------------------------------------------------------
# 模块级辅助函数
# ---------------------------------------------------------------------------


def _split_frontmatter(raw: str) -> tuple[str, str, str]:
    """分离 YAML frontmatter 和 Markdown body。

    返回 (frontmatter_text, body_text, error_message)。
    """
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return "", "", "缺少 frontmatter 起始 ---"

    end_index: int | None = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_index = i
            break

    if end_index is None:
        return "", "", "缺少 frontmatter 结束 ---"

    frontmatter = "\n".join(lines[1:end_index])
    body = "\n".join(lines[end_index + 1:]).strip()
    return frontmatter, body, ""


def _normalize_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        v = raw.strip()
        return [v] if v else []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    return []


def _extract_summary(body: str) -> str:
    """从技能正文提取摘要（优先取"什么时候用"段落）。"""
    # 尝试找 ## 什么时候用 段落
    match = re.search(r"##\s*什么时候用\s*\n+(.*?)(?=\n##|\Z)", body, re.DOTALL)
    if match:
        text = match.group(1).strip()
    else:
        # 取第一个非标题非空段落
        for line in body.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                text = stripped
                break
        else:
            text = body.strip()

    # 截断到 _SUMMARY_MAX_CHARS
    if len(text) > _SUMMARY_MAX_CHARS:
        text = text[:_SUMMARY_MAX_CHARS - 3].rstrip() + "..."
    return text


def _load_frontmatter_dict(skill_file: Path) -> dict | None:
    """读取并解析技能文件的 YAML frontmatter 为 dict。"""
    try:
        raw = skill_file.read_text(encoding="utf-8")
    except OSError:
        return None
    frontmatter, _, error = _split_frontmatter(raw)
    if error:
        return None
    try:
        fm = yaml.safe_load(frontmatter)
        return fm if isinstance(fm, dict) else None
    except yaml.YAMLError:
        return None


def _write_frontmatter_back(skill_file: Path, fm: dict, body: str) -> None:
    """将 frontmatter dict + body 写回技能文件。"""
    frontmatter_text = yaml.dump(
        fm,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    ).rstrip()
    new_content = f"---\n{frontmatter_text}\n---\n\n{body}\n"
    skill_file.write_text(new_content, encoding="utf-8")
