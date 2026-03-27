"""src.app lazy export 测试。"""

from __future__ import annotations

import sys


def _clear_app_modules() -> None:
    for mod in list(sys.modules.keys()):
        if mod == "src.app" or mod.startswith("src.app."):
            del sys.modules[mod]


def test_import_src_app_does_not_eagerly_import_heavy_modules():
    _clear_app_modules()
    import src.app as app  # noqa: F401

    assert "src.app.container" not in sys.modules
    assert "src.app.telegram_app" not in sys.modules
    assert "src.app.task_view" not in sys.modules


def test_accessing_task_view_store_only_imports_task_view_module():
    _clear_app_modules()
    import src.app as app

    _ = app.TaskViewStore
    assert "src.app.task_view" in sys.modules
    assert "src.app.container" not in sys.modules
    assert "src.app.telegram_app" not in sys.modules
