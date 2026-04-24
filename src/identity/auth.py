from __future__ import annotations

# 身份系统鉴权上下文 — AuthContext、作用域定义、工厂函数、辅助检查
# Identity system auth context — AuthContext, scope definitions, factory functions, scope check helper

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.identity.models import ActorType


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------

class AuthorizationError(Exception):
    """当作用域检查失败时抛出"""
    pass


# ---------------------------------------------------------------------------
# 作用域定义
# ---------------------------------------------------------------------------

SCOPE_DEFINITIONS: dict[str, str] = {
    "identity.read": "读取身份主张 / Read identity claims",
    "identity.write": "写入或更新身份主张 / Write or update identity claims",
    "identity.deprecate": "软删除（弃用）身份主张 / Soft-delete (deprecate) identity claims",
    "identity.redact": "抹除主张文本（隐私保护）/ Redact claim text (privacy)",
    "identity.erase": "完全删除主张（tombstone，参见 Addendum P1.1）/ Full erase (tombstone, per Addendum P1.1)",
    "identity.rebuild": "从 Markdown 触发完整重建 / Trigger full rebuild from markdown",
    "identity.admin": "管理员操作 / Administrative operations",
    "sensitive.restricted.explicit": "访问受限敏感度主张（参见 Addendum P1.4）/ Access restricted-sensitivity claims (per Addendum P1.4)",
}

# ---------------------------------------------------------------------------
# 按角色划分的默认作用域
# ---------------------------------------------------------------------------

DEFAULT_SCOPES_BY_ACTOR: dict[ActorType, frozenset[str]] = {
    "kevin": frozenset(SCOPE_DEFINITIONS.keys()),   # 全部权限
    "lapwing": frozenset({                           # 可自我管理，但不可删除或访问受限主张
        "identity.read",
        "identity.write",
        "identity.deprecate",
    }),
    "system": frozenset({                            # 维护操作，但不可删除或使用管理员权限
        "identity.read",
        "identity.write",
        "identity.deprecate",
        "identity.rebuild",
    }),
}


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AuthContext:
    """
    鉴权上下文，携带操作者身份与其拥有的作用域集合。
    Auth context carrying actor identity and the set of scopes they hold.
    """
    actor: ActorType
    scopes: frozenset[str]
    session_id: str | None = None


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------

def create_kevin_auth(session_id: str | None = None) -> AuthContext:
    """创建 Kevin 的鉴权上下文（全部作用域）"""
    return AuthContext(
        actor="kevin",
        scopes=DEFAULT_SCOPES_BY_ACTOR["kevin"],
        session_id=session_id,
    )


def create_system_auth(session_id: str | None = None) -> AuthContext:
    """创建系统级鉴权上下文（维护操作，不含删除和管理员权限）"""
    return AuthContext(
        actor="system",
        scopes=DEFAULT_SCOPES_BY_ACTOR["system"],
        session_id=session_id,
    )


def create_lapwing_auth(session_id: str | None = None) -> AuthContext:
    """创建 Lapwing 自身的鉴权上下文（读/写/弃用，不含删除或受限主张访问）"""
    return AuthContext(
        actor="lapwing",
        scopes=DEFAULT_SCOPES_BY_ACTOR["lapwing"],
        session_id=session_id,
    )


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def check_scope(auth: AuthContext, required_scope: str) -> None:
    """
    检查 auth 是否持有 required_scope；不持有则抛出 AuthorizationError。
    Check that auth holds required_scope; raise AuthorizationError if not.
    """
    if required_scope not in auth.scopes:
        raise AuthorizationError(
            f"actor={auth.actor!r} lacks required scope {required_scope!r}"
        )
