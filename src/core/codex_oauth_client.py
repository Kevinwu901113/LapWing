"""
Codex OAuth 客户端管理。

封装 oauth-codex 的 AsyncClient，处理：
- 单例初始化（整个应用共享一个 client 实例）
- 认证状态检查
- Auth 失败时的重置
"""

import logging

log = logging.getLogger(__name__)

_client = None
_initialized: bool = False


async def get_client():
    """
    获取已认证的 AsyncClient 单例。

    首次调用时初始化并认证。后续调用返回已有实例。
    oauth-codex 内部处理 token 自动刷新。

    抛出: ImportError 如果 oauth-codex 未安装
          oauth_codex.AuthenticationError 如果认证失败
    """
    global _client, _initialized

    if _initialized and _client is not None:
        return _client

    try:
        from oauth_codex import AsyncClient
    except ImportError as exc:
        raise ImportError(
            "oauth-codex 未安装。运行: pip install oauth-codex --break-system-packages"
        ) from exc

    client = AsyncClient()
    await client.authenticate()
    _client = client
    _initialized = True
    log.info("[codex_oauth] AsyncClient 已初始化并认证")
    return _client


async def reset_client() -> None:
    """重置客户端（用于 auth 失败后重新认证）。"""
    global _client, _initialized
    _client = None
    _initialized = False
    log.info("[codex_oauth] 客户端已重置，下次调用将重新认证")


def is_available() -> bool:
    """检查 oauth-codex 是否已安装。"""
    try:
        import oauth_codex  # noqa: F401
        return True
    except ImportError:
        return False
