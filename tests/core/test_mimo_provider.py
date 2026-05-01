import sys
from types import SimpleNamespace

import pytest

from src.core.llm_protocols import _anthropic_messages_endpoint
from src.core.model_config import ResolvedModelRoute


def test_anthropic_messages_endpoint_preserves_provider_path():
    assert (
        _anthropic_messages_endpoint("https://token-plan-cn.xiaomimimo.com/anthropic")
        == "https://token-plan-cn.xiaomimimo.com/anthropic/v1/messages"
    )
    assert (
        _anthropic_messages_endpoint("https://token-plan-cn.xiaomimimo.com/anthropic/v1")
        == "https://token-plan-cn.xiaomimimo.com/anthropic/v1/messages"
    )


def test_anthropic_messages_endpoint_avoids_path_duplication():
    assert (
        _anthropic_messages_endpoint("https://token-plan-cn.xiaomimimo.com/anthropic/")
        == "https://token-plan-cn.xiaomimimo.com/anthropic/v1/messages"
    )


def test_build_anthropic_client_bearer_style(monkeypatch):
    captured = {}

    class FakeAsyncAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(AsyncAnthropic=FakeAsyncAnthropic))
    from src.core.llm_router import LLMRouter

    router = LLMRouter()
    router._build_anthropic_client_for_route(
        api_key="test-token",
        base_url="https://token-plan-cn.xiaomimimo.com/anthropic",
        auth_style="bearer",
    )
    assert captured.get("auth_token") == "test-token"
    assert "api_key" not in captured
    assert captured.get("base_url") == "https://token-plan-cn.xiaomimimo.com/anthropic"


def test_build_anthropic_client_x_api_key_style(monkeypatch):
    captured = {}

    class FakeAsyncAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(AsyncAnthropic=FakeAsyncAnthropic))
    from src.core.llm_router import LLMRouter

    router = LLMRouter()
    router._build_anthropic_client_for_route(
        api_key="test-token",
        base_url="https://api.anthropic.com/v1",
        auth_style="x_api_key",
    )
    assert captured.get("api_key") == "test-token"
    assert "auth_token" not in captured
    assert captured.get("base_url") == "https://api.anthropic.com"


def test_model_ref_maps_to_raw_model_without_prefix_and_preserves_dot():
    from src.core.llm_router import LLMRouter

    router = LLMRouter()
    route = ResolvedModelRoute(
        provider_id="xiaomimimo",
        provider_name="Xiaomi MiMo",
        model_id="mimo-v2.5-pro",
        model_name="MiMo V2.5 Pro",
        model_ref="xiaomimimo/mimo-v2.5-pro",
        api_type="anthropic",
        base_url="https://token-plan-cn.xiaomimimo.com/anthropic",
        api_key="",
        auth_type="api_key",
        auth_style="bearer",
    )
    router._model_routes_by_ref[route.model_ref] = route
    router._model_routes_by_model_id.setdefault(route.model_id, []).append(route)

    looked_up = router._lookup_model_route("xiaomimimo/mimo-v2.5-pro")
    assert looked_up is not None
    assert looked_up.model_id == "mimo-v2.5-pro"
    assert "." in looked_up.model_id
    assert "/" not in looked_up.model_id
