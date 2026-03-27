"""Auth store 测试。"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from src.auth.storage import AuthStore


def test_auth_store_round_trips_profiles_bindings_and_status(tmp_path):
    store = AuthStore(tmp_path / "auth-profiles.json")

    store.ensure_exists()
    store.upsert_profile(
        "openai:oauth",
        {
            "provider": "openai",
            "type": "oauth",
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "expiresAt": "2099-01-01T00:00:00Z",
        },
    )
    store.upsert_profile(
        "openai:key",
        {
            "provider": "openai",
            "type": "api_key",
            "secretRef": {"kind": "literal", "value": "sk-test"},
        },
    )
    store.set_binding("chat", "openai:oauth")

    data = store.read()
    profiles = store.list_profiles("openai")

    assert data["bindings"]["chat"] == "openai:oauth"
    assert {item["profileId"] for item in profiles} == {"openai:key", "openai:oauth"}
    assert all(item["status"] == "active" for item in profiles)


def test_auth_store_clear_binding_returns_whether_binding_existed(tmp_path):
    store = AuthStore(tmp_path / "auth-profiles.json")
    store.upsert_profile(
        "openai:oauth",
        {
            "provider": "openai",
            "type": "oauth",
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "expiresAt": "2099-01-01T00:00:00Z",
        },
    )
    store.set_binding("chat", "openai:oauth")

    assert store.clear_binding("chat") is True
    assert store.get_binding("chat") is None
    assert store.clear_binding("chat") is False


def test_auth_store_prefers_oauth_and_skips_cooldown_when_requested(tmp_path):
    store = AuthStore(tmp_path / "auth-profiles.json")

    store.upsert_profile(
        "openai:oauth",
        {
            "provider": "openai",
            "type": "oauth",
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "expiresAt": "2099-01-01T00:00:00Z",
        },
    )
    store.upsert_profile(
        "openai:key",
        {
            "provider": "openai",
            "type": "api_key",
            "secretRef": {"kind": "literal", "value": "sk-test"},
        },
    )

    assert store.ordered_profiles("openai", include_unavailable=False) == [
        "openai:oauth",
        "openai:key",
    ]

    store.mark_failure("openai:oauth", "auth")
    assert store.ordered_profiles("openai", include_unavailable=False) == ["openai:key"]

    store.mark_success("openai:oauth")
    assert store.ordered_profiles("openai", include_unavailable=False) == [
        "openai:oauth",
        "openai:key",
    ]


def test_auth_store_locking_keeps_json_valid_under_concurrent_writes(tmp_path):
    store = AuthStore(tmp_path / "auth-profiles.json")

    def write_profile(index: int) -> None:
        store.upsert_profile(
            f"openai:{index}",
            {
                "provider": "openai",
                "type": "api_key",
                "secretRef": {"kind": "literal", "value": f"sk-{index}"},
            },
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(write_profile, range(12)))

    raw = store.path.read_text(encoding="utf-8")
    payload = json.loads(raw)

    assert len(payload["profiles"]) == 12
    assert set(payload["profiles"].keys()) == {f"openai:{index}" for index in range(12)}
