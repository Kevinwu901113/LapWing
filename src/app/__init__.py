"""应用层导出（惰性加载，避免导入副作用）。"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["AppContainer", "TaskViewStore", "TelegramApp"]

_EXPORTS = {
    "AppContainer": ("src.app.container", "AppContainer"),
    "TaskViewStore": ("src.app.task_view", "TaskViewStore"),
    "TelegramApp": ("src.app.telegram_app", "TelegramApp"),
}


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(name)
    module_name, attr_name = _EXPORTS[name]
    module = import_module(module_name)
    return getattr(module, attr_name)


def __dir__() -> list[str]:
    return sorted(set(globals().keys()) | set(__all__))
