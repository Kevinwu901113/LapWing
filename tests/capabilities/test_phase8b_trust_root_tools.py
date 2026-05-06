"""Phase 8B-3 tests: trust root tool registration gating and behaviour.

Tests: registration when enabled, absent when disabled, tool behaviour,
deterministic ordering, clean errors, filtering, status transitions.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.capabilities.signature import CapabilityTrustRoot
from src.capabilities.trust_roots import TrustRootStore
from src.tools.capability_tools import register_capability_trust_root_tools
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


class _FakeRegistry:
    def __init__(self):
        self._t: dict[str, object] = {}

    def register(self, spec):
        self._t[spec.name] = spec

    def get(self, name: str):
        return self._t.get(name)

    @property
    def names(self) -> list[str]:
        return sorted(self._t.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._t


def _make_store(tmp_path) -> TrustRootStore:
    return TrustRootStore(data_dir=tmp_path)


def _add_root(store: TrustRootStore, root_id: str, **overrides) -> CapabilityTrustRoot:
    kwargs = {
        "trust_root_id": root_id,
        "name": f"Test Root {root_id}",
        "key_type": "ed25519",
        "public_key_fingerprint": f"sha256:fp_{root_id}",
        "status": "active",
        **overrides,
    }
    return store.create_trust_root(CapabilityTrustRoot(**kwargs))


@pytest.fixture
def registry():
    return _FakeRegistry()


@pytest.fixture
def store(tmp_path):
    return _make_store(tmp_path)


# ── Registration ──────────────────────────────────────────────────────


class TestRegistration:
    def test_five_tools_registered(self, registry, store):
        register_capability_trust_root_tools(registry, store)
        assert "list_capability_trust_roots" in registry
        assert "view_capability_trust_root" in registry
        assert "add_capability_trust_root" in registry
        assert "disable_capability_trust_root" in registry
        assert "revoke_capability_trust_root" in registry

    def test_exactly_five_tools(self, registry, store):
        register_capability_trust_root_tools(registry, store)
        assert len(registry.names) == 5

    def test_tools_have_correct_capability_tag(self, registry, store):
        register_capability_trust_root_tools(registry, store)
        for name in registry.names:
            assert registry.get(name).capability == "capability_trust_operator"

    def test_risk_levels(self, registry, store):
        register_capability_trust_root_tools(registry, store)
        assert registry.get("list_capability_trust_roots").risk_level == "low"
        assert registry.get("view_capability_trust_root").risk_level == "low"
        assert registry.get("add_capability_trust_root").risk_level == "medium"
        assert registry.get("disable_capability_trust_root").risk_level == "medium"
        assert registry.get("revoke_capability_trust_root").risk_level == "high"

    def test_none_store_skips_registration(self, registry):
        register_capability_trust_root_tools(registry, None)
        assert "list_capability_trust_roots" not in registry

    def test_forbidden_tools_absent(self, registry, store):
        register_capability_trust_root_tools(registry, store)
        forbidden = {
            "verify_capability_signature",
            "trust_capability_signature",
            "mark_capability_trusted_signed",
            "fetch_trust_root",
            "import_remote_trust_root",
            "run_capability",
        }
        for name in forbidden:
            assert name not in registry, f"{name} should not be registered"


# ── List tool ─────────────────────────────────────────────────────────


class TestListTrustRoots:
    async def test_list_empty(self, registry, store):
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("list_capability_trust_roots")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert result.success
        assert result.payload["trust_roots"] == []
        assert result.payload["count"] == 0

    async def test_list_returns_compact_summaries(self, registry, store):
        _add_root(store, "tr-1")
        _add_root(store, "tr-2")
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("list_capability_trust_roots")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert result.success
        assert result.payload["count"] == 2
        for r in result.payload["trust_roots"]:
            assert "trust_root_id" in r
            assert "name" in r
            assert "key_type" in r
            assert "public_key_fingerprint" in r
            assert "status" in r
            assert "is_active" in r
            # Never expose secrets
            assert "private_key" not in r
            assert "secret_key" not in r
            assert "signing_key" not in r
            assert "key_material" not in r
            assert "api_key" not in r

    async def test_list_deterministic_ordering(self, registry, store):
        # Create in non-sorted order
        for rid in ["tr-c", "tr-a", "tr-b"]:
            _add_root(store, rid)
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("list_capability_trust_roots")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        ids = [r["trust_root_id"] for r in result.payload["trust_roots"]]
        assert ids == sorted(ids)

    async def test_list_filters_by_status(self, registry, store):
        _add_root(store, "tr-1", status="active")
        _add_root(store, "tr-2", status="disabled")
        _add_root(store, "tr-3", status="revoked")
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("list_capability_trust_roots")

        r = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"status": "active"}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert r.payload["count"] == 1
        assert r.payload["trust_roots"][0]["trust_root_id"] == "tr-1"

        r = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"status": "disabled"}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert r.payload["count"] == 1
        assert r.payload["trust_roots"][0]["trust_root_id"] == "tr-2"

    async def test_list_filters_by_scope(self, registry, store):
        _add_root(store, "tr-1", scope="global")
        _add_root(store, "tr-2", scope="project")
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("list_capability_trust_roots")

        r = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"scope": "global"}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert r.payload["count"] == 1
        assert r.payload["trust_roots"][0]["trust_root_id"] == "tr-1"

    async def test_list_respects_limit(self, registry, store):
        for i in range(10):
            _add_root(store, f"tr-{i:02d}")
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("list_capability_trust_roots")

        r = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"limit": 3}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert r.payload["count"] == 3

    async def test_list_default_limit(self, registry, store):
        for i in range(60):
            _add_root(store, f"tr-{i:02d}")
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("list_capability_trust_roots")

        r = await spec.executor(
            ToolExecutionRequest(name="test", arguments={}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert r.payload["count"] == 50  # default limit

    async def test_list_include_expired_default_true(self, registry, store):
        past = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        _add_root(store, "tr-1", status="active", expires_at=past)
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("list_capability_trust_roots")

        r = await spec.executor(
            ToolExecutionRequest(name="test", arguments={}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        # expired included by default
        assert r.payload["count"] == 1

    async def test_list_exclude_expired(self, registry, store):
        past = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        _add_root(store, "tr-old", status="active", expires_at=past)
        _add_root(store, "tr-fresh", status="active", expires_at=future)
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("list_capability_trust_roots")

        r = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"include_expired": False}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert r.payload["count"] == 1
        assert r.payload["trust_roots"][0]["trust_root_id"] == "tr-fresh"

    async def test_list_is_active_reflects_status_and_expiry(self, registry, store):
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        _add_root(store, "active-valid", status="active", expires_at=future)
        _add_root(store, "active-expired", status="active", expires_at=past)
        _add_root(store, "disabled-valid", status="disabled", expires_at=future)
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("list_capability_trust_roots")

        r = await spec.executor(
            ToolExecutionRequest(name="test", arguments={}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        by_id = {item["trust_root_id"]: item for item in r.payload["trust_roots"]}
        # is_active uses store.is_trust_root_active which checks status + expiry
        assert by_id["active-valid"]["is_active"] is True
        assert by_id["active-expired"]["is_active"] is False  # expired
        assert by_id["disabled-valid"]["is_active"] is False  # disabled


# ── View tool ─────────────────────────────────────────────────────────


class TestViewTrustRoot:
    async def test_view_valid_root(self, registry, store):
        _add_root(store, "tr-1", scope="global")
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("view_capability_trust_root")

        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"trust_root_id": "tr-1"}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert result.success
        assert result.payload["trust_root_id"] == "tr-1"
        assert result.payload["name"] == "Test Root tr-1"
        assert result.payload["scope"] == "global"
        assert "private_key" not in result.payload
        assert "secret_key" not in result.payload
        assert "signing_key" not in result.payload
        assert "key_material" not in result.payload

    async def test_view_missing_returns_not_found(self, registry, store):
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("view_capability_trust_root")

        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"trust_root_id": "nonexistent"}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert not result.success
        assert result.payload["error"] == "not_found"

    async def test_view_empty_id(self, registry, store):
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("view_capability_trust_root")

        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"trust_root_id": ""}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert not result.success

    async def test_view_shows_is_active(self, registry, store):
        _add_root(store, "tr-active", status="active")
        _add_root(store, "tr-disabled", status="disabled")
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("view_capability_trust_root")

        r1 = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"trust_root_id": "tr-active"}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert r1.payload["is_active"] is True

        r2 = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"trust_root_id": "tr-disabled"}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert r2.payload["is_active"] is False

    async def test_view_includes_metadata(self, registry, store):
        _add_root(store, "tr-1", metadata={"contact": "admin@example.com"})
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("view_capability_trust_root")

        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"trust_root_id": "tr-1"}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert result.payload["metadata"] == {"contact": "admin@example.com"}


# ── Add tool ──────────────────────────────────────────────────────────


class TestAddTrustRoot:
    async def test_add_valid_root(self, registry, store):
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("add_capability_trust_root")

        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={
                "trust_root_id": "new-root",
                "name": "New Root",
                "key_type": "ed25519",
                "public_key_fingerprint": "sha256:abc123",
            }),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert result.success
        assert result.payload["trust_root_id"] == "new-root"
        assert result.payload["status"] == "active"

        # Verify persisted
        stored = store.get_trust_root("new-root")
        assert stored is not None
        assert stored.name == "New Root"

    async def test_add_with_optional_fields(self, registry, store):
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("add_capability_trust_root")

        future = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={
                "trust_root_id": "full-root",
                "name": "Full Root",
                "key_type": "rsa-2048",
                "public_key_fingerprint": "sha256:def456",
                "owner": "kevin",
                "scope": "global",
                "expires_at": future,
                "metadata": {"note": "test"},
            }),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert result.success
        assert result.payload["owner"] == "kevin"
        assert result.payload["scope"] == "global"

    async def test_duplicate_add_rejected(self, registry, store):
        _add_root(store, "tr-1")
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("add_capability_trust_root")

        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={
                "trust_root_id": "tr-1",
                "name": "Dup",
                "key_type": "ed25519",
                "public_key_fingerprint": "sha256:xxx",
            }),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert not result.success

    async def test_add_missing_required_fields(self, registry, store):
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("add_capability_trust_root")

        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"trust_root_id": "x"}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert not result.success

    async def test_add_path_traversal_rejected(self, registry, store):
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("add_capability_trust_root")

        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={
                "trust_root_id": "../escape",
                "name": "Escape",
                "key_type": "ed25519",
                "public_key_fingerprint": "sha256:xxx",
            }),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert not result.success


# ── Disable tool ──────────────────────────────────────────────────────


class TestDisableTrustRoot:
    async def test_disable_active_root(self, registry, store):
        _add_root(store, "tr-1", status="active")
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("disable_capability_trust_root")

        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"trust_root_id": "tr-1"}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert result.success
        assert result.payload["status"] == "disabled"

        stored = store.get_trust_root("tr-1")
        assert stored is not None  # file still exists
        assert stored.status == "disabled"

    async def test_disable_with_reason(self, registry, store):
        _add_root(store, "tr-1", status="active")
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("disable_capability_trust_root")

        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={
                "trust_root_id": "tr-1",
                "reason": "key compromised",
            }),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert result.success
        stored = store.get_trust_root("tr-1")
        assert stored.metadata.get("disabled_reason") == "key compromised"

    async def test_disable_revoked_root_rejected(self, registry, store):
        _add_root(store, "tr-1", status="revoked")
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("disable_capability_trust_root")

        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"trust_root_id": "tr-1"}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert not result.success
        assert result.payload["error"] == "already_revoked"

    async def test_disable_missing_root(self, registry, store):
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("disable_capability_trust_root")

        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"trust_root_id": "nope"}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert not result.success
        assert result.payload["error"] == "not_found"

    async def test_disable_does_not_delete_file(self, registry, store):
        _add_root(store, "tr-1", status="active")
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("disable_capability_trust_root")

        await spec.executor(
            ToolExecutionRequest(name="test", arguments={"trust_root_id": "tr-1"}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert store.get_trust_root("tr-1") is not None


# ── Revoke tool ───────────────────────────────────────────────────────


class TestRevokeTrustRoot:
    async def test_revoke_active_root(self, registry, store):
        _add_root(store, "tr-1", status="active")
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("revoke_capability_trust_root")

        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={
                "trust_root_id": "tr-1",
                "reason": "no longer trusted",
            }),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert result.success
        assert result.payload["status"] == "revoked"

        stored = store.get_trust_root("tr-1")
        assert stored is not None
        assert stored.status == "revoked"

    async def test_revoke_disabled_root(self, registry, store):
        _add_root(store, "tr-1", status="disabled")
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("revoke_capability_trust_root")

        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={
                "trust_root_id": "tr-1",
                "reason": "formal revocation",
            }),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert result.success
        assert result.payload["status"] == "revoked"

    async def test_revoke_missing_root(self, registry, store):
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("revoke_capability_trust_root")

        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={
                "trust_root_id": "nope",
                "reason": "not found",
            }),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert not result.success
        assert result.payload["error"] == "not_found"

    async def test_revoke_without_reason(self, registry, store):
        _add_root(store, "tr-1")
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("revoke_capability_trust_root")

        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"trust_root_id": "tr-1"}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert not result.success  # reason required

    async def test_revoke_does_not_delete_file(self, registry, store):
        _add_root(store, "tr-1", status="active")
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("revoke_capability_trust_root")

        await spec.executor(
            ToolExecutionRequest(name="test", arguments={
                "trust_root_id": "tr-1",
                "reason": "test",
            }),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert store.get_trust_root("tr-1") is not None

    async def test_revoke_stores_reason_in_metadata(self, registry, store):
        _add_root(store, "tr-1", status="active")
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("revoke_capability_trust_root")

        await spec.executor(
            ToolExecutionRequest(name="test", arguments={
                "trust_root_id": "tr-1",
                "reason": "security incident #42",
            }),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        stored = store.get_trust_root("tr-1")
        assert stored.metadata.get("revoked_reason") == "security incident #42"


# ── Corrupt / edge case handling ──────────────────────────────────────


class TestEdgeCases:
    async def test_corrupt_root_skipped_in_list(self, registry, store, tmp_path):
        _add_root(store, "good")
        # Write a corrupt JSON file directly
        corrupt_path = store.roots_dir / "bad.json"
        store.roots_dir.mkdir(parents=True, exist_ok=True)
        corrupt_path.write_text("{not valid json", encoding="utf-8")

        register_capability_trust_root_tools(registry, store)
        spec = registry.get("list_capability_trust_roots")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert result.success
        assert result.payload["count"] == 1

    async def test_view_corrupt_root_returns_not_found(self, registry, store, tmp_path):
        corrupt_path = store.roots_dir / "bad.json"
        store.roots_dir.mkdir(parents=True, exist_ok=True)
        corrupt_path.write_text("{not valid json", encoding="utf-8")

        register_capability_trust_root_tools(registry, store)
        spec = registry.get("view_capability_trust_root")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"trust_root_id": "bad"}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert not result.success

    async def test_add_empty_id_rejected(self, registry, store):
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("add_capability_trust_root")

        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={
                "trust_root_id": "",
                "name": "Empty",
                "key_type": "ed25519",
                "public_key_fingerprint": "sha256:xxx",
            }),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert not result.success
