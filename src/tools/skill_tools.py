"""Skill 三级渐进加载工具 — skill_list / skill_view。

Level 1: skill_list  — 返回所有技能的完整名称 + 完整描述
Level 1: skill_view  — 按名称加载完整 SKILL.md body 与资源清单
Level 2: skill_view (with path) — 加载技能目录下指定资源文件内容
"""

from __future__ import annotations

from pathlib import Path

from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolExecutionResult


async def skill_list_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    """返回所有可用技能的完整名称和描述（Level 1 详情列表）。"""
    skill_manager = context.services.get("skill_manager")
    if skill_manager is None:
        payload = {"success": False, "reason": "skill_manager 不可用", "skills": []}
        return ToolExecutionResult(success=False, payload=payload, reason="skill_manager 不可用")

    skills = skill_manager.model_visible_skills()
    skill_list = [
        {
            "name": skill.name,
            "description": skill.description,
            "user_invocable": skill.user_invocable,
        }
        for skill in skills
    ]

    if not skill_list:
        payload = {"success": True, "reason": "", "skills": [], "message": "当前没有可用的技能。"}
        return ToolExecutionResult(success=True, payload=payload)

    lines = [f"共 {len(skill_list)} 个可用技能：\n"]
    for s in skill_list:
        invocable_tag = "（用户可调用）" if s["user_invocable"] else ""
        lines.append(f"• **{s['name']}**{invocable_tag}\n  {s['description']}")

    message = "\n".join(lines)
    payload = {"success": True, "reason": "", "skills": skill_list, "message": message}
    return ToolExecutionResult(success=True, payload=payload)


async def skill_view_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    """加载技能完整内容或指定资源文件。

    参数:
        name (str, 必填): 技能名称
        path (str, 可选): 相对于技能目录的资源文件路径（如 references/guide.md）
            不传则返回完整 SKILL.md body 和资源清单（Level 1）
            传入则返回指定文件内容（Level 2）
    """
    skill_manager = context.services.get("skill_manager")
    if skill_manager is None:
        payload = {"success": False, "reason": "skill_manager 不可用", "name": "", "content": ""}
        return ToolExecutionResult(success=False, payload=payload, reason="skill_manager 不可用")

    name = str(request.arguments.get("name", "")).strip().lower()
    if not name:
        payload = {"success": False, "reason": "缺少 name 参数", "name": "", "content": ""}
        return ToolExecutionResult(success=False, payload=payload, reason="缺少 name 参数")

    skill = skill_manager.get(name)
    if skill is None:
        payload = {"success": False, "reason": f"技能不存在: {name}", "name": name, "content": ""}
        return ToolExecutionResult(success=False, payload=payload, reason=f"技能不存在: {name}")

    resource_path = str(request.arguments.get("path", "")).strip()

    if not resource_path:
        # Level 1: 返回完整 SKILL.md body + 资源清单
        resources = skill_manager._list_resources(skill.directory)
        resources_text = "\n".join(f"  • {r}" for r in resources) if resources else "  （无资源文件）"
        content = (
            f"# 技能：{skill.name}\n\n"
            f"{skill.body}\n\n"
            f"## 资源文件清单\n{resources_text}\n\n"
            "（使用 skill_view 并传入 path 参数可加载具体资源文件）"
        )
        payload = {
            "success": True,
            "reason": "",
            "name": skill.name,
            "content": content,
            "resources": resources,
        }
        return ToolExecutionResult(success=True, payload=payload)

    # Level 2: 加载指定资源文件
    # 安全检查：path 不能越界出技能目录
    try:
        skill_dir = skill.directory.resolve()
        target = (skill_dir / resource_path).resolve()
        if not target.is_relative_to(skill_dir):
            payload = {
                "success": False,
                "reason": f"path 越界了技能目录: {resource_path}",
                "name": name,
                "content": "",
            }
            return ToolExecutionResult(success=False, payload=payload, reason="path 越界")
    except (ValueError, OSError) as exc:
        payload = {"success": False, "reason": f"path 无效: {exc}", "name": name, "content": ""}
        return ToolExecutionResult(success=False, payload=payload, reason=f"path 无效: {exc}")

    if not target.exists():
        payload = {
            "success": False,
            "reason": f"资源文件不存在: {resource_path}",
            "name": name,
            "content": "",
        }
        return ToolExecutionResult(success=False, payload=payload, reason=f"资源文件不存在: {resource_path}")

    try:
        file_content = target.read_text(encoding="utf-8")
    except Exception as exc:
        payload = {"success": False, "reason": f"读取失败: {exc}", "name": name, "content": ""}
        return ToolExecutionResult(success=False, payload=payload, reason=f"读取失败: {exc}")

    # 限制大小，防止单文件撑爆 context
    _MAX_CHARS = 8000
    truncated = False
    if len(file_content) > _MAX_CHARS:
        file_content = file_content[:_MAX_CHARS]
        truncated = True

    content = f"# {name} / {resource_path}\n\n{file_content}"
    if truncated:
        content += f"\n\n（已截断，仅显示前 {_MAX_CHARS} 字符）"

    payload = {
        "success": True,
        "reason": "",
        "name": name,
        "path": resource_path,
        "content": content,
        "truncated": truncated,
    }
    return ToolExecutionResult(success=True, payload=payload)
