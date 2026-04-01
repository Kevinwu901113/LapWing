"""Skills 子系统：发现、校验、筛选和按需激活。"""

from __future__ import annotations

from dataclasses import dataclass
import html
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any

logger = logging.getLogger("lapwing.core.skills")

_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MAX_NAME_LEN = 64
_MAX_DESCRIPTION_LEN = 1024
_MAX_RESOURCE_FILES = 200
_ALLOWED_FRONTMATTER_KEYS = {
    "name",
    "description",
    "user-invocable",
    "disable-model-invocation",
    "command-dispatch",
    "command-tool",
    "command-arg-mode",
    "metadata",
    # OpenClaw 扩展字段，允许存在但本期不使用。
    "homepage",
}


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    description: str
    directory: Path
    skill_file: Path
    body: str
    metadata: dict[str, Any]
    user_invocable: bool
    disable_model_invocation: bool
    command_dispatch: str | None
    command_tool: str | None
    command_arg_mode: str
    source: str


@dataclass(frozen=True)
class SkillParseResult:
    ok: bool
    skill: SkillDefinition | None = None
    reason: str = ""


class SkillManager:
    """管理 Skills 的加载、目录注入与按需激活。"""

    def __init__(
        self,
        *,
        enabled: bool,
        workspace_dir: Path,
        managed_dir: Path,
        bundled_dir: Path,
        extra_dirs: list[Path],
    ) -> None:
        self._enabled = enabled
        self._workspace_dir = workspace_dir
        self._managed_dir = managed_dir
        self._bundled_dir = bundled_dir
        self._extra_dirs = extra_dirs
        self._skills: dict[str, SkillDefinition] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    def reload(self) -> None:
        """扫描并重建 Skills 清单。"""
        if not self._enabled:
            self._skills = {}
            return

        merged: dict[str, SkillDefinition] = {}
        # 优先级：extra(最低) -> bundled -> managed -> workspace(最高)
        tiers: list[tuple[str, list[Path]]] = [
            ("extra", self._extra_dirs),
            ("bundled", [self._bundled_dir]),
            ("managed", [self._managed_dir]),
            ("workspace", [self._workspace_dir]),
        ]

        for source, roots in tiers:
            for root in roots:
                for skill in self._scan_root(root=root, source=source):
                    previous = merged.get(skill.name)
                    if previous is not None and previous.directory != skill.directory:
                        logger.info(
                            "技能 `%s` 被更高优先级目录覆盖: %s -> %s",
                            skill.name,
                            previous.directory,
                            skill.directory,
                        )
                    merged[skill.name] = skill

        self._skills = dict(sorted(merged.items(), key=lambda item: item[0]))
        logger.info("Skills 已加载: %s", ", ".join(self._skills.keys()) or "(none)")

    def all_skills(self) -> list[SkillDefinition]:
        return list(self._skills.values())

    def has_skills(self) -> bool:
        return bool(self._skills)

    def model_visible_skills(self) -> list[SkillDefinition]:
        return [skill for skill in self._skills.values() if not skill.disable_model_invocation]

    def has_model_visible_skills(self) -> bool:
        return bool(self.model_visible_skills())

    def get(self, name: str) -> SkillDefinition | None:
        normalized = name.strip().lower()
        if not normalized:
            return None
        return self._skills.get(normalized)

    def render_catalog_for_prompt(self) -> str:
        skills = self.model_visible_skills()
        if not skills:
            return ""

        lines = ["<available_skills>"]
        for skill in skills:
            lines.extend(
                [
                    "  <skill>",
                    f"    <name>{html.escape(skill.name)}</name>",
                    f"    <description>{html.escape(skill.description)}</description>",
                    f"    <location>{html.escape(str(skill.directory))}</location>",
                    "  </skill>",
                ]
            )
        lines.append("</available_skills>")

        return "\n".join(lines)

    def activate(self, name: str, user_input: str = "") -> dict[str, Any]:
        skill = self.get(name)
        if skill is None:
            raise KeyError(f"技能不存在: {name}")

        resources = self._list_resources(skill.directory)
        wrapped_content = self._build_wrapped_content(skill, resources=resources, user_input=user_input)
        return {
            "skill_name": skill.name,
            "skill_dir": str(skill.directory),
            "content": skill.body,
            "resources": resources,
            "metadata": skill.metadata,
            "wrapped_content": wrapped_content,
        }

    def _scan_root(self, *, root: Path, source: str) -> list[SkillDefinition]:
        resolved_root = root.expanduser().resolve()
        if not resolved_root.exists() or not resolved_root.is_dir():
            return []

        candidates: list[Path] = []
        direct_skill = resolved_root / "SKILL.md"
        if direct_skill.is_file():
            candidates.append(direct_skill)

        for child in sorted(resolved_root.iterdir(), key=lambda p: p.name):
            if not child.is_dir():
                continue
            skill_md = child / "SKILL.md"
            if skill_md.is_file():
                candidates.append(skill_md)

        loaded: list[SkillDefinition] = []
        for skill_md in candidates:
            try:
                resolved_skill = skill_md.resolve()
            except OSError as exc:
                logger.warning("跳过技能 `%s`：路径解析失败 (%s)", skill_md, exc)
                continue

            if not resolved_skill.is_relative_to(resolved_root):
                logger.warning("跳过技能 `%s`：SKILL.md 解析后越界", skill_md)
                continue

            parse_result = self._parse_skill(resolved_skill, source=source)
            if not parse_result.ok or parse_result.skill is None:
                logger.warning("跳过技能 `%s`：%s", skill_md, parse_result.reason)
                continue

            if not self._passes_load_gates(parse_result.skill):
                logger.info("技能 `%s` 未通过 requires gate，跳过", parse_result.skill.name)
                continue

            loaded.append(parse_result.skill)

        return loaded

    def _parse_skill(self, skill_file: Path, *, source: str) -> SkillParseResult:
        try:
            raw = skill_file.read_text(encoding="utf-8")
        except OSError as exc:
            return SkillParseResult(ok=False, reason=f"读取失败: {exc}")

        frontmatter, body, error = _extract_frontmatter_and_body(raw)
        if error:
            return SkillParseResult(ok=False, reason=error)

        unknown_keys = [key for key in frontmatter if key not in _ALLOWED_FRONTMATTER_KEYS]
        if unknown_keys:
            return SkillParseResult(ok=False, reason=f"包含不支持的 frontmatter 字段: {', '.join(unknown_keys)}")

        name = _strip_quotes(frontmatter.get("name", "")).strip().lower()
        if not name:
            return SkillParseResult(ok=False, reason="缺少必填字段 name")
        if len(name) > _MAX_NAME_LEN:
            return SkillParseResult(ok=False, reason="name 超过 64 字符")
        if not _NAME_RE.fullmatch(name):
            return SkillParseResult(ok=False, reason="name 不符合规范（仅允许 a-z/0-9/-）")

        parent_name = skill_file.parent.name.strip().lower()
        if parent_name != name:
            return SkillParseResult(ok=False, reason="name 必须与技能目录名一致")

        description = _strip_quotes(frontmatter.get("description", "")).strip()
        if not description:
            return SkillParseResult(ok=False, reason="缺少必填字段 description")
        if len(description) > _MAX_DESCRIPTION_LEN:
            return SkillParseResult(ok=False, reason="description 超过 1024 字符")

        user_invocable, error = _parse_bool(frontmatter.get("user-invocable"), default=True)
        if error:
            return SkillParseResult(ok=False, reason=error)

        disable_model_invocation, error = _parse_bool(
            frontmatter.get("disable-model-invocation"),
            default=False,
        )
        if error:
            return SkillParseResult(ok=False, reason=error)

        command_dispatch = _strip_quotes(frontmatter.get("command-dispatch", "")).strip()
        if command_dispatch == "":
            command_dispatch = None
        if command_dispatch is not None and command_dispatch != "tool":
            return SkillParseResult(ok=False, reason="command-dispatch 仅支持 tool")

        command_tool = _strip_quotes(frontmatter.get("command-tool", "")).strip() or None
        command_arg_mode = _strip_quotes(frontmatter.get("command-arg-mode", "")).strip() or "raw"
        if command_arg_mode != "raw":
            return SkillParseResult(ok=False, reason="command-arg-mode 仅支持 raw")

        if command_dispatch == "tool" and not command_tool:
            return SkillParseResult(ok=False, reason="command-dispatch=tool 时必须提供 command-tool")

        metadata_raw = frontmatter.get("metadata")
        metadata: dict[str, Any] = {}
        if metadata_raw is not None:
            metadata_text = str(metadata_raw).strip()
            if not metadata_text:
                metadata = {}
            else:
                try:
                    parsed = json.loads(metadata_text)
                except json.JSONDecodeError as exc:
                    return SkillParseResult(ok=False, reason=f"metadata 不是合法 JSON: {exc}")
                if not isinstance(parsed, dict):
                    return SkillParseResult(ok=False, reason="metadata 必须是 JSON object")
                metadata = parsed

        skill = SkillDefinition(
            name=name,
            description=description,
            directory=skill_file.parent.resolve(),
            skill_file=skill_file,
            body=body,
            metadata=metadata,
            user_invocable=user_invocable,
            disable_model_invocation=disable_model_invocation,
            command_dispatch=command_dispatch,
            command_tool=command_tool,
            command_arg_mode=command_arg_mode,
            source=source,
        )
        return SkillParseResult(ok=True, skill=skill)

    def _passes_load_gates(self, skill: SkillDefinition) -> bool:
        openclaw_meta = skill.metadata.get("openclaw") if isinstance(skill.metadata, dict) else None
        if not isinstance(openclaw_meta, dict):
            return True

        requires = openclaw_meta.get("requires")
        if not isinstance(requires, dict):
            requires = {}

        required_os = openclaw_meta.get("os")
        if required_os is None:
            required_os = requires.get("os")
        if required_os is not None:
            required_os_values = _normalize_string_list(required_os)
            if not required_os_values:
                return False
            current = _current_os_tag()
            if current not in required_os_values:
                return False

        env_keys = _normalize_string_list(requires.get("env"))
        for env_key in env_keys:
            if not env_key:
                return False
            if not str(os.environ.get(env_key, "")).strip():
                return False

        bins = _normalize_string_list(requires.get("bins"))
        for binary in bins:
            if not binary:
                return False
            if shutil.which(binary) is None:
                return False

        return True

    def _list_resources(self, skill_dir: Path) -> list[str]:
        resources: list[str] = []
        for folder_name in ("scripts", "references", "assets"):
            folder = skill_dir / folder_name
            if not folder.exists() or not folder.is_dir():
                continue
            for path in sorted(folder.rglob("*")):
                if not path.is_file():
                    continue
                try:
                    rel = path.resolve().relative_to(skill_dir.resolve())
                except ValueError:
                    continue
                resources.append(rel.as_posix())
                if len(resources) >= _MAX_RESOURCE_FILES:
                    return resources
        return resources

    def _build_wrapped_content(
        self,
        skill: SkillDefinition,
        *,
        resources: list[str],
        user_input: str,
    ) -> str:
        lines: list[str] = [
            f'<skill_content name="{skill.name}">',
            skill.body.strip(),
            "",
            f"Skill directory: {skill.directory}",
            "Relative paths in this skill are relative to the skill directory.",
            "<skill_resources>",
        ]

        for resource in resources:
            lines.append(f"  <file>{resource}</file>")

        lines.extend(["</skill_resources>"])

        cleaned_input = user_input.strip()
        if cleaned_input:
            lines.extend(["", f"User: {cleaned_input}"])

        lines.append("</skill_content>")
        return "\n".join(lines)


def _extract_frontmatter_and_body(raw: str) -> tuple[dict[str, str], str, str]:
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, "", "SKILL.md 缺少 frontmatter 开始分隔符 ---"

    end_index: int | None = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break

    if end_index is None:
        return {}, "", "SKILL.md 缺少 frontmatter 结束分隔符 ---"

    frontmatter_lines = lines[1:end_index]
    body = "\n".join(lines[end_index + 1:]).strip()

    parsed: dict[str, str] = {}
    for line in frontmatter_lines:
        stripped = line.strip()
        if not stripped:
            continue
        if ":" not in stripped:
            return {}, "", f"frontmatter 行格式错误: {line}"
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            return {}, "", f"frontmatter key 为空: {line}"
        parsed[key] = value

    return parsed, body, ""


def _parse_bool(raw: Any, *, default: bool) -> tuple[bool, str]:
    if raw is None:
        return default, ""
    value = _strip_quotes(str(raw)).strip().lower()
    if value == "true":
        return True, ""
    if value == "false":
        return False, ""
    return default, f"布尔字段值非法: {raw}"


def _strip_quotes(text: str) -> str:
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def _normalize_string_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        value = raw.strip()
        return [value] if value else []
    if isinstance(raw, list):
        normalized: list[str] = []
        for item in raw:
            if not isinstance(item, str):
                continue
            value = item.strip()
            if value:
                normalized.append(value)
        return normalized
    return []


def _current_os_tag() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("win"):
        return "win32"
    return sys.platform
