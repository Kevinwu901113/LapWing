"""Phase 8B-3 tests: trust root tool safety constraints.

Verifies all hard constraints:
- Secret/private key rejection
- Path traversal rejection
- No writes outside trust_roots dir
- No crypto imports
- No network
- No signature_status=verified path
- No trusted_signed elevation
- No capability provenance modified
- No run_capability
"""

from __future__ import annotations

import json
from pathlib import Path
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


# ── Secret rejection ──────────────────────────────────────────────────


class TestSecretRejectionOnAdd:
    async def _try_add(self, registry, store, **kwargs):
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("add_capability_trust_root")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={
                "trust_root_id": "test-root",
                "name": "Test",
                "key_type": "ed25519",
                "public_key_fingerprint": "sha256:abc",
                **kwargs,
            }),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        return result

    async def test_add_rejects_private_key(self, registry, store):
        result = await self._try_add(registry, store,
                                     name="-----BEGIN PRIVATE KEY-----")
        assert not result.success

    async def test_add_rejects_secret_key(self, registry, store):
        result = await self._try_add(registry, store,
                                     name="sk-my-secret-key")
        assert not result.success

    async def test_add_rejects_pem_openssh_private_key(self, registry, store):
        result = await self._try_add(registry, store,
                                     name="-----BEGIN OPENSSH PRIVATE KEY-----")
        assert not result.success

    async def test_add_rejects_pem_rsa_private_key(self, registry, store):
        result = await self._try_add(registry, store,
                                     name="-----BEGIN RSA PRIVATE KEY-----")
        assert not result.success

    async def test_add_rejects_pem_ec_private_key(self, registry, store):
        result = await self._try_add(registry, store,
                                     name="-----BEGIN EC PRIVATE KEY-----")
        assert not result.success

    async def test_add_rejects_bearer_token(self, registry, store):
        result = await self._try_add(registry, store,
                                     name="Bearer abc123")
        assert not result.success

    async def test_add_rejects_sk_prefix_api_key(self, registry, store):
        result = await self._try_add(registry, store,
                                     name="sk-proj-abc123")
        assert not result.success

    async def test_add_rejects_sk_underscore_api_key(self, registry, store):
        result = await self._try_add(registry, store,
                                     name="sk_my_api_key_12345")
        assert not result.success

    async def test_add_rejects_secret_in_public_key_fingerprint(self, registry, store):
        result = await self._try_add(registry, store,
                                     public_key_fingerprint="-----BEGIN PRIVATE KEY-----")
        assert not result.success

    async def test_field_name_rejection_in_store_is_indirect(self, registry, store):
        """Field name rejection (e.g. signing_key, key_material) only triggers
        via get_trust_root reading raw JSON from disk. The add tool path goes
        through to_dict() which only outputs known CapabilityTrustRoot fields,
        so field-name-based rejection is not reachable through the tool.
        This is tested directly in test_phase8b_trust_root_store.py."""
        pass  # Documented constraint — field name rejection is a read-time guard

    async def test_add_allows_normal_name(self, registry, store):
        result = await self._try_add(registry, store, name="Normal Trust Root")
        assert result.success


# ── Path traversal rejection ──────────────────────────────────────────


class TestPathTraversalRejection:
    async def test_add_rejects_dot_dot(self, registry, store):
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("add_capability_trust_root")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={
                "trust_root_id": "foo/../bar",
                "name": "Traversal",
                "key_type": "ed25519",
                "public_key_fingerprint": "sha256:abc",
            }),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert not result.success

    async def test_add_rejects_slash(self, registry, store):
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("add_capability_trust_root")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={
                "trust_root_id": "foo/bar",
                "name": "Slash",
                "key_type": "ed25519",
                "public_key_fingerprint": "sha256:abc",
            }),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert not result.success

    async def test_add_rejects_backslash(self, registry, store):
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("add_capability_trust_root")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={
                "trust_root_id": "foo\\bar",
                "name": "Backslash",
                "key_type": "ed25519",
                "public_key_fingerprint": "sha256:abc",
            }),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert not result.success


# ── No writes outside trust_roots dir ─────────────────────────────────


class TestNoWritesOutside:
    async def test_add_only_writes_in_roots_dir(self, registry, store, tmp_path):
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("add_capability_trust_root")

        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={
                "trust_root_id": "safe-root",
                "name": "Safe Root",
                "key_type": "ed25519",
                "public_key_fingerprint": "sha256:abc",
            }),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert result.success

        # Check that the file was written only inside trust_roots dir
        root_file = store.roots_dir / "safe-root.json"
        assert root_file.is_file()

        # Verify the id resolves to a safe path
        assert root_file.parent == store.roots_dir
        assert ".." not in str(root_file.relative_to(store.roots_dir))

    async def test_disable_only_modifies_status(self, registry, store, tmp_path):
        _add_root(store, "tr-1")
        paths_before = set(store.roots_dir.glob("*.json"))

        register_capability_trust_root_tools(registry, store)
        spec = registry.get("disable_capability_trust_root")
        await spec.executor(
            ToolExecutionRequest(name="test", arguments={"trust_root_id": "tr-1"}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )

        paths_after = set(store.roots_dir.glob("*.json"))
        assert paths_before == paths_after  # no new files, no deleted files


# ── No crypto imports ─────────────────────────────────────────────────


class TestNoCryptoImports:
    def test_trust_roots_no_crypto_import(self):
        import inspect
        from src.capabilities import trust_roots as tr
        source = inspect.getsource(tr)
        assert "import cryptography" not in source
        assert "from cryptography" not in source
        assert "import nacl" not in source
        assert "from nacl" not in source
        assert "import PyNaCl" not in source
        assert "import rsa" not in source
        assert "from Crypto" not in source
        assert "import subprocess" not in source
        assert "importlib" not in source
        assert "import runpy" not in source

    def test_capability_tools_no_crypto_in_trust_root_section(self):
        """The capability_tools.py trust root executors have no crypto."""
        import inspect
        from src.tools import capability_tools as ct

        # Find the trust root executor functions
        source = inspect.getsource(ct._make_add_trust_root_executor)
        assert "cryptography" not in source
        assert "verify" not in source.lower() or "verify" not in source  # No actual verify

        source = inspect.getsource(ct._make_list_trust_roots_executor)
        assert "cryptography" not in source

        source = inspect.getsource(ct._make_view_trust_root_executor)
        assert "cryptography" not in source


# ── No network ────────────────────────────────────────────────────────


class TestNoNetwork:
    def test_trust_root_tools_no_network_imports(self):
        import inspect
        from src.tools import capability_tools as ct

        for func_name in [
            "_make_list_trust_roots_executor",
            "_make_view_trust_root_executor",
            "_make_add_trust_root_executor",
            "_make_disable_trust_root_executor",
            "_make_revoke_trust_root_executor",
        ]:
            source = inspect.getsource(getattr(ct, func_name))
            assert "requests" not in source
            assert "httpx" not in source
            assert "urllib" not in source
            assert "urlopen" not in source
            assert "socket" not in source

    def test_trust_root_store_no_network(self):
        import inspect
        from src.capabilities import trust_roots as tr
        source = inspect.getsource(tr)
        assert "requests" not in source
        assert "httpx" not in source
        assert "urllib" not in source
        assert "urlopen" not in source


# ── No signature_status=verified ──────────────────────────────────────


class TestNoVerifiedSignature:
    async def test_add_does_not_verify(self, registry, store):
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("add_capability_trust_root")

        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={
                "trust_root_id": "no-verify",
                "name": "No Verify",
                "key_type": "ed25519",
                "public_key_fingerprint": "sha256:abc",
            }),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert result.success
        assert "signature_status" not in result.payload
        assert result.payload.get("signature_status") != "verified"

    async def test_list_never_returns_verified(self, registry, store):
        _add_root(store, "tr-1")
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("list_capability_trust_roots")

        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        for r in result.payload["trust_roots"]:
            assert r.get("signature_status") != "verified"

    async def test_view_never_returns_verified(self, registry, store):
        _add_root(store, "tr-1")
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("view_capability_trust_root")

        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"trust_root_id": "tr-1"}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert result.payload.get("signature_status") != "verified"


# ── No trusted_signed elevation ───────────────────────────────────────


class TestNoTrustedSigned:
    def test_trust_roots_have_no_trusted_signed(self, store):
        root = _add_root(store, "tr-1")
        data = root.to_dict()
        assert "trusted_signed" not in data
        assert "trust_level" not in data

    async def test_add_never_sets_trusted_signed(self, registry, store):
        register_capability_trust_root_tools(registry, store)
        spec = registry.get("add_capability_trust_root")

        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={
                "trust_root_id": "no-elevate",
                "name": "No Elevate",
                "key_type": "ed25519",
                "public_key_fingerprint": "sha256:abc",
            }),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert result.success
        assert "trusted_signed" not in result.payload
        assert "trust_level" not in result.payload


# ── No capability provenance modification ─────────────────────────────


class TestNoProvenanceModification:
    async def test_disable_does_not_touch_capability_files(self, registry, store, tmp_path):
        _add_root(store, "tr-1")
        # Create a mock capability directory to verify it's untouched
        cap_dir = tmp_path / "cap_dir"
        cap_dir.mkdir()
        prov_file = cap_dir / "provenance.json"
        prov_file.write_text(json.dumps({"provenance_id": "test"}))

        register_capability_trust_root_tools(registry, store)
        spec = registry.get("disable_capability_trust_root")

        prov_before = prov_file.read_text()
        await spec.executor(
            ToolExecutionRequest(name="test", arguments={"trust_root_id": "tr-1"}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert prov_file.read_text() == prov_before

    async def test_revoke_does_not_touch_capability_files(self, registry, store, tmp_path):
        _add_root(store, "tr-1")
        cap_dir = tmp_path / "cap_dir"
        cap_dir.mkdir()
        prov_file = cap_dir / "provenance.json"
        prov_file.write_text(json.dumps({"provenance_id": "test"}))

        register_capability_trust_root_tools(registry, store)
        spec = registry.get("revoke_capability_trust_root")

        prov_before = prov_file.read_text()
        await spec.executor(
            ToolExecutionRequest(name="test", arguments={
                "trust_root_id": "tr-1",
                "reason": "test",
            }),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert prov_file.read_text() == prov_before

    async def test_add_does_not_touch_capability_files(self, registry, store, tmp_path):
        cap_dir = tmp_path / "cap_dir"
        cap_dir.mkdir()
        prov_file = cap_dir / "provenance.json"
        prov_file.write_text(json.dumps({"provenance_id": "test"}))

        register_capability_trust_root_tools(registry, store)
        spec = registry.get("add_capability_trust_root")

        prov_before = prov_file.read_text()
        await spec.executor(
            ToolExecutionRequest(name="test", arguments={
                "trust_root_id": "no-touch",
                "name": "No Touch",
                "key_type": "ed25519",
                "public_key_fingerprint": "sha256:abc",
            }),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert prov_file.read_text() == prov_before


# ── No run_capability ─────────────────────────────────────────────────


class TestNoRunCapability:
    def test_no_run_capability_in_tool_names(self):
        from src.tools.capability_tools import register_capability_trust_root_tools

        import inspect
        source = inspect.getsource(register_capability_trust_root_tools)
        assert "run_capability" not in source

    def test_no_exec_in_executors(self):
        import inspect
        from src.tools import capability_tools as ct

        for func_name in [
            "_make_list_trust_roots_executor",
            "_make_view_trust_root_executor",
            "_make_add_trust_root_executor",
            "_make_disable_trust_root_executor",
            "_make_revoke_trust_root_executor",
        ]:
            source = inspect.getsource(getattr(ct, func_name))
            assert "exec(" not in source, f"{func_name} contains exec()"
            assert "eval(" not in source, f"{func_name} contains eval()"
            assert "subprocess" not in source, f"{func_name} contains subprocess"
            assert "os.system" not in source, f"{func_name} contains os.system"


# ── No view leaks secrets ─────────────────────────────────────────────


class TestNoSecretLeakage:
    async def test_view_never_leaks_secret_fields(self, registry, store, tmp_path):
        """Even if a trust root file contains secret field names, the view
        tool should not return them (get_trust_root strips them)."""
        # Write a file with a secret-looking field name directly
        store.roots_dir.mkdir(parents=True, exist_ok=True)
        bad_data = {
            "trust_root_id": "leaky",
            "name": "Leaky Root",
            "key_type": "ed25519",
            "public_key_fingerprint": "sha256:abc",
            "status": "active",
            "created_at": "",
            "metadata": {},
            "private_key": "should-not-appear",
        }
        (store.roots_dir / "leaky.json").write_text(
            json.dumps(bad_data), encoding="utf-8"
        )

        register_capability_trust_root_tools(registry, store)
        spec = registry.get("view_capability_trust_root")

        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"trust_root_id": "leaky"}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert not result.success  # secret-containing files rejected by get_trust_root
