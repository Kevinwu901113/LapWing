"""AuthorityGate — Lapwing 权限认证系统。

三级权限模型：
  OWNER  — Kevin，可以做一切
  TRUSTED — 信任的朋友，可用普通功能（搜索、聊天等）
  GUEST  — 群里的其他人，只能聊天

对应 CLAUDE.md §4 的设计。
"""

from __future__ import annotations

from enum import IntEnum

from config.settings import DESKTOP_DEFAULT_OWNER, OWNER_IDS, TRUSTED_IDS


class AuthLevel(IntEnum):
    GUEST = 0
    TRUSTED = 1
    OWNER = 2


def identify(adapter: str, user_id: str) -> AuthLevel:
    """
    识别用户权限级别。

    Args:
        adapter: 消息来源适配器，如 "telegram"、"qq"、"desktop"
        user_id: 用户 ID 字符串

    Returns:
        对应的权限级别
    """
    # 桌面应用本地连接 → 默认 OWNER
    if adapter == "desktop" and DESKTOP_DEFAULT_OWNER:
        return AuthLevel.OWNER

    uid = str(user_id).strip()

    if uid and uid in OWNER_IDS:
        return AuthLevel.OWNER

    if uid and uid in TRUSTED_IDS:
        return AuthLevel.TRUSTED

    return AuthLevel.GUEST


# 工具名 → 最低所需权限
# 基于 src/tools/registry.py 中注册的实际工具名
OPERATION_AUTH: dict[str, AuthLevel] = {
    # 纯聊天（无工具时）
    "chat": AuthLevel.GUEST,
    # 信息查询类
    "web_search": AuthLevel.TRUSTED,
    "web_fetch": AuthLevel.TRUSTED,
    "file_list_directory": AuthLevel.TRUSTED,
    "activate_skill": AuthLevel.TRUSTED,
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
}

# 未注册工具的默认权限（保守策略）
DEFAULT_AUTH: AuthLevel = AuthLevel.OWNER


def authorize(tool_name: str, auth_level: AuthLevel) -> tuple[bool, str]:
    """
    检查用户是否有权限使用某个工具。

    Returns:
        (是否允许, 拒绝理由)。允许时理由为空字符串。
    """
    required = OPERATION_AUTH.get(tool_name, DEFAULT_AUTH)

    if auth_level >= required:
        return True, ""

    if required == AuthLevel.OWNER:
        return False, "这个操作只有 Kevin 能让我做。"

    if required == AuthLevel.TRUSTED:
        return False, "我不太认识你，不能帮你做这个。不过你可以跟我聊天。"

    return False, "没有权限使用这个功能。"
