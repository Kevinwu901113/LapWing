import pytest

from src.config import reload_settings


_CONCURRENT_BG_WORK_FLAGS = (
    "CONCURRENT_BG_WORK_ENABLED",
    "CONCURRENT_BG_WORK_P2B_TASK_SUPERVISOR_READONLY",
    "CONCURRENT_BG_WORK_P2C_AGENT_RUNTIME_ASYNC",
    "CONCURRENT_BG_WORK_P2D_CANCEL_AND_NEEDS_INPUT",
)


@pytest.fixture(autouse=True)
def _disable_concurrent_bg_work_for_tool_tests(monkeypatch):
    for name in _CONCURRENT_BG_WORK_FLAGS:
        monkeypatch.setenv(name, "false")
    reload_settings()
    yield
    reload_settings()
