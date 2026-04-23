"""AuthorityGate — 四级权限控制。

Phase 1 重铸版：
  IGNORE  (0) — 完全忽略（未知来源）
  GUEST   (1) — 群里的其他人，只能聊天
  TRUSTED (2) — 信任的朋友，可用普通功能
  OWNER   (3) — Kevin，可以做一切
"""

from __future__ import annotations

from enum import IntEnum

from config.settings import DESKTOP_DEFAULT_OWNER, OWNER_IDS, TRUSTED_IDS


class AuthLevel(IntEnum):
    IGNORE = 0
    GUEST = 1
    TRUSTED = 2
    OWNER = 3


def identify(adapter: str, user_id: str) -> AuthLevel:
    """识别用户权限级别。"""
    # 桌面应用本地连接 → 默认 OWNER
    if adapter == "desktop" and DESKTOP_DEFAULT_OWNER:
        return AuthLevel.OWNER

    uid = str(user_id).strip()

    if uid and uid in OWNER_IDS:
        return AuthLevel.OWNER

    if uid and uid in TRUSTED_IDS:
        return AuthLevel.TRUSTED

    # QQ 群/私聊中的未知用户
    if adapter in ("qq", "qq_group"):
        return AuthLevel.GUEST

    return AuthLevel.IGNORE


# 工具名 → 最低所需权限
# TODO(recast-v1.1): 重构为工具自声明权限模式。
# 工具注册时携带 required_auth，OPERATION_AUTH 从注册表自动生成而非手动维护。
# 参考 Claude Code 的 per-tool permissionMode 模式（每个 tool 在 buildTool() 中
# 声明 checkPermissions/isReadOnly/isDestructive）。
OPERATION_AUTH: dict[str, AuthLevel] = {
    "chat": AuthLevel.GUEST,
    # 基础通讯能力——所有人都可以让 Lapwing 说话/承诺
    "tell_user": AuthLevel.GUEST,
    "commit_promise": AuthLevel.GUEST,
    "fulfill_promise": AuthLevel.GUEST,
    "abandon_promise": AuthLevel.GUEST,
    # 信息查询类
    "research": AuthLevel.TRUSTED,
    "file_list_directory": AuthLevel.TRUSTED,
    "send_image": AuthLevel.TRUSTED,
    "get_time": AuthLevel.GUEST,
    # 文件和系统操作类 → OWNER
    "execute_shell": AuthLevel.OWNER,
    "read_file": AuthLevel.OWNER,
    "write_file": AuthLevel.OWNER,
    "file_read_segment": AuthLevel.OWNER,
    "file_write": AuthLevel.OWNER,
    "file_append": AuthLevel.OWNER,
    "apply_workspace_patch": AuthLevel.OWNER,
    "run_python_code": AuthLevel.OWNER,
    "verify_code_result": AuthLevel.OWNER,
    "verify_workspace": AuthLevel.OWNER,
    # memory_tools_v2 实际注册名
    "recall": AuthLevel.OWNER,
    "write_note": AuthLevel.OWNER,
    "edit_note": AuthLevel.OWNER,
    "search_notes": AuthLevel.OWNER,
    # 调度（durable_scheduler 实际注册名）
    "set_reminder": AuthLevel.OWNER,
    "view_reminders": AuthLevel.OWNER,
    "cancel_reminder": AuthLevel.OWNER,
    # 委派（agent_tools 实际注册名）
    "delegate": AuthLevel.OWNER,
    "delegate_to_agent": AuthLevel.OWNER,
    # 个人工具
    "send_message": AuthLevel.OWNER,
    "view_image": AuthLevel.OWNER,
    "browse": AuthLevel.OWNER,
    # 灵魂编辑
    "read_soul": AuthLevel.OWNER,
    "edit_soul": AuthLevel.OWNER,
    # 浏览器操作
    "browser_open": AuthLevel.OWNER,
    "browser_click": AuthLevel.OWNER,
    "browser_type": AuthLevel.OWNER,
    "browser_select": AuthLevel.OWNER,
    "browser_scroll": AuthLevel.OWNER,
    "browser_screenshot": AuthLevel.OWNER,
    "browser_get_text": AuthLevel.OWNER,
    "browser_back": AuthLevel.OWNER,
    "browser_tabs": AuthLevel.OWNER,
    "browser_switch_tab": AuthLevel.OWNER,
    "browser_close_tab": AuthLevel.OWNER,
    "browser_wait": AuthLevel.OWNER,
    "browser_login": AuthLevel.OWNER,
    # 技能系统
    "create_skill": AuthLevel.OWNER,
    "run_skill": AuthLevel.OWNER,
    "edit_skill": AuthLevel.OWNER,
    "list_skills": AuthLevel.OWNER,
    "promote_skill": AuthLevel.OWNER,
    "delete_skill": AuthLevel.OWNER,
    "search_skill": AuthLevel.GUEST,
    "install_skill": AuthLevel.OWNER,
    # 纠正记录
    "add_correction": AuthLevel.OWNER,
}

# 未注册工具的默认权限（保守策略）
DEFAULT_AUTH: AuthLevel = AuthLevel.OWNER


def authorize(tool_name: str, auth_level: AuthLevel) -> tuple[bool, str]:
    """检查用户是否有权限使用某个工具。"""
    if auth_level == AuthLevel.IGNORE:
        return False, "无法识别你的身份。"

    required = OPERATION_AUTH.get(tool_name, DEFAULT_AUTH)

    if auth_level >= required:
        return True, ""

    if required == AuthLevel.OWNER:
        return False, "这个操作只有 Kevin 能让我做。"

    if required == AuthLevel.TRUSTED:
        return False, "我不太认识你，不能帮你做这个。不过你可以跟我聊天。"

    return False, "没有权限使用这个功能。"
