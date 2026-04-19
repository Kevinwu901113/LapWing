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
OPERATION_AUTH: dict[str, AuthLevel] = {
    "chat": AuthLevel.GUEST,
    # Step 5：基础通讯能力——所有人都可以让 Lapwing 说话/承诺
    "tell_user": AuthLevel.GUEST,
    "commit_promise": AuthLevel.GUEST,
    "fulfill_promise": AuthLevel.GUEST,
    "abandon_promise": AuthLevel.GUEST,
    # 信息查询类
    "research": AuthLevel.TRUSTED,
    "file_list_directory": AuthLevel.TRUSTED,
    "send_image": AuthLevel.TRUSTED,
    "session_search": AuthLevel.TRUSTED,
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
    "memory_note": AuthLevel.OWNER,
    "memory_list": AuthLevel.OWNER,
    "memory_read": AuthLevel.OWNER,
    "memory_edit": AuthLevel.OWNER,
    "memory_delete": AuthLevel.OWNER,
    "memory_search": AuthLevel.OWNER,
    "schedule_task": AuthLevel.OWNER,
    "list_scheduled_tasks": AuthLevel.OWNER,
    "cancel_scheduled_task": AuthLevel.OWNER,
    "delegate_task": AuthLevel.OWNER,
    "report_incident": AuthLevel.OWNER,
    "self_status": AuthLevel.OWNER,
    "trace_mark": AuthLevel.OWNER,
    "send_proactive_message": AuthLevel.OWNER,
    # 后台进程
    "process_spawn": AuthLevel.OWNER,
    "process_status": AuthLevel.OWNER,
    "process_kill": AuthLevel.OWNER,
    "process_logs": AuthLevel.OWNER,
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
