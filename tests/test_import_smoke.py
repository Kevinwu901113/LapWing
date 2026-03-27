"""导入边界 smoke tests。"""

from __future__ import annotations

import importlib
import sys
from unittest.mock import patch


def _clear_modules(*keywords: str) -> None:
    for mod in list(sys.modules.keys()):
        if any(keyword in mod for keyword in keywords):
            del sys.modules[mod]


def test_settings_import_without_dotenv_dependency():
    _clear_modules("settings")
    with patch.dict(sys.modules, {"dotenv": None}):
        module = importlib.import_module("config.settings")
    assert str(module.ROOT_DIR).endswith("lapwing")


def test_llm_router_openai_path_does_not_require_anthropic():
    _clear_modules("settings", "llm_router")
    with patch.dict(
        "os.environ",
        {
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://generic.api.com/v1",
            "LLM_MODEL": "glm-4-flash",
            "LLM_CHAT_API_KEY": "",
            "LLM_CHAT_BASE_URL": "",
            "LLM_CHAT_MODEL": "",
            "LLM_TOOL_API_KEY": "",
            "LLM_TOOL_BASE_URL": "",
            "LLM_TOOL_MODEL": "",
            "NIM_API_KEY": "",
            "NIM_BASE_URL": "",
            "NIM_MODEL": "",
        },
        clear=True,
    ), patch.dict(sys.modules, {"anthropic": None}):
        module = importlib.import_module("src.core.llm_router")
        router = module.LLMRouter()
    assert router._api_types["chat"] == "openai"


def test_llm_router_anthropic_path_reports_missing_dependency():
    _clear_modules("settings", "llm_router")
    with patch.dict(
        "os.environ",
        {
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://api.minimaxi.com/anthropic/v1",
            "LLM_MODEL": "MiniMax-M2.7",
            "LLM_CHAT_API_KEY": "",
            "LLM_CHAT_BASE_URL": "",
            "LLM_CHAT_MODEL": "",
            "LLM_TOOL_API_KEY": "",
            "LLM_TOOL_BASE_URL": "",
            "LLM_TOOL_MODEL": "",
            "NIM_API_KEY": "",
            "NIM_BASE_URL": "",
            "NIM_MODEL": "",
        },
        clear=True,
    ), patch.dict(sys.modules, {"anthropic": None}):
        module = importlib.import_module("src.core.llm_router")
        try:
            module.LLMRouter()
            assert False, "expected ModuleNotFoundError"
        except ModuleNotFoundError as exc:
            assert "pip install anthropic" in str(exc)
