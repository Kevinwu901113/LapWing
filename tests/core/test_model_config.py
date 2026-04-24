from src.core.model_config import (
    ModelConfigManager,
    ModelInfo,
    ModelRoutingConfig,
    ProviderInfo,
    SlotAssignment,
    _serialize,
)


def _manager(config: ModelRoutingConfig) -> ModelConfigManager:
    manager = object.__new__(ModelConfigManager)
    manager._config = config
    return manager


def test_resolve_slot_route_returns_provider_qualified_ref():
    config = ModelRoutingConfig(
        providers=[
            ProviderInfo(
                id="volcengine",
                name="火山方舟",
                api_type="anthropic",
                base_url="https://ark.cn-beijing.volces.com/api/coding",
                api_key="sk-test",
                models=[ModelInfo(id="minimax-m2.7", name="MiniMax M2.7")],
            )
        ],
        slots={
            "main_conversation": SlotAssignment(
                provider_id="volcengine",
                model_id="minimax-m2.7",
            )
        },
    )

    route = _manager(config).resolve_slot_route("main_conversation")

    assert route is not None
    assert route.provider_id == "volcengine"
    assert route.model_id == "minimax-m2.7"
    assert route.model_ref == "volcengine/minimax-m2.7"
    assert route.api_type == "anthropic"


def test_duplicate_bare_model_requires_qualified_ref():
    config = ModelRoutingConfig(
        providers=[
            ProviderInfo(
                id="a",
                name="A",
                api_type="openai",
                base_url="https://a.example/v1",
                api_key="a-key",
                models=[ModelInfo(id="shared-model", name="Shared")],
            ),
            ProviderInfo(
                id="b",
                name="B",
                api_type="anthropic",
                base_url="https://b.example/anthropic",
                api_key="b-key",
                models=[ModelInfo(id="shared-model", name="Shared")],
            ),
        ],
    )
    manager = _manager(config)

    assert manager.resolve_model_ref("shared-model") is None
    route = manager.resolve_model_ref("b/shared-model")
    assert route is not None
    assert route.provider_id == "b"
    assert route.api_type == "anthropic"


def test_serialized_slots_include_model_refs_and_capability_metadata():
    config = ModelRoutingConfig(
        providers=[
            ProviderInfo(
                id="nvidia",
                name="NVIDIA NIM",
                api_type="openai",
                base_url="https://integrate.api.nvidia.com/v1",
                api_key="secret",
                api_key_env="NIM_API_KEY",
                models=[
                    ModelInfo(
                        id="moonshotai/kimi-k2-instruct",
                        name="Kimi K2",
                        capabilities={"tools": False},
                        limits={"runtime_context_tokens": 64000},
                    )
                ],
            )
        ],
        slots={
            "heartbeat_proactive": SlotAssignment(
                provider_id="nvidia",
                model_id="moonshotai/kimi-k2-instruct",
                fallback_model_ids=["moonshotai/kimi-k2-instruct"],
            )
        },
    )

    data = _serialize(config)

    assert data["providers"][0]["api_key"] == "FROM_ENV"
    assert data["providers"][0]["api_key_env"] == "NIM_API_KEY"
    assert data["providers"][0]["models"][0]["capabilities"] == {"tools": False}
    assert data["providers"][0]["models"][0]["limits"] == {"runtime_context_tokens": 64000}
    slot = data["slots"]["heartbeat_proactive"]
    assert slot["model_ref"] == "nvidia/moonshotai/kimi-k2-instruct"
    assert slot["fallback_model_refs"] == ["nvidia/moonshotai/kimi-k2-instruct"]
