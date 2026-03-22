"""Markdown Prompt 加载器。

从 prompts/ 目录读取 .md 文件作为 system prompt。
支持缓存和重载。
"""

from pathlib import Path
from config.settings import PROMPTS_DIR

_cache: dict[str, str] = {}


def load_prompt(name: str, use_cache: bool = True) -> str:
    """加载指定名称的 prompt 文件。

    Args:
        name: 文件名（不含扩展名），如 "lapwing"
        use_cache: 是否使用缓存，默认 True

    Returns:
        prompt 内容字符串

    Raises:
        FileNotFoundError: 找不到对应的 prompt 文件
    """
    if use_cache and name in _cache:
        return _cache[name]

    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt 文件不存在: {path}")

    content = path.read_text(encoding="utf-8")
    _cache[name] = content
    return content


def reload_prompt(name: str) -> str:
    """强制重新加载 prompt（跳过缓存）。"""
    return load_prompt(name, use_cache=False)


def clear_cache() -> None:
    """清除所有缓存。"""
    _cache.clear()
