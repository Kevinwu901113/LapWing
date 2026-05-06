"""Phase 8B-2: TrustRootStore tests — CRUD, status management, active checks,
edge cases, secret rejection."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.capabilities.signature import CapabilityTrustRoot
from src.capabilities.trust_roots import TrustRootStore, _validate_trust_root_id


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_trust_root(
    trust_root_id: str = "test-root-1",
    name: str = "Test Root",
    key_type: str = "ed25519",
    fingerprint: str = "abc123def456",
    status: str = "active",
    scope: str | None = None,
    expires_at: str | None = None,
    owner: str | None = None,
    created_at: str = "",
    metadata: dict | None = None,
) -> CapabilityTrustRoot:
    return CapabilityTrustRoot(
        trust_root_id=trust_root_id,
        name=name,
        key_type=key_type,
        public_key_fingerprint=fingerprint,
        status=status,
        scope=scope,
        expires_at=expires_at,
        owner=owner,
        created_at=created_at,
        metadata=metadata or {},
    )


# ── ID Validation ────────────────────────────────────────────────────────

class TestTrustRootIdValidation:
    def test_valid_id(self):
        _validate_trust_root_id("my-trust-root_1.v2")

    def test_empty_string_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            _validate_trust_root_id("")

    def test_whitespace_only_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            _validate_trust_root_id("   ")

    def test_slash_rejected(self):
        with pytest.raises(ValueError, match="path separators"):
            _validate_trust_root_id("foo/bar")

    def test_backslash_rejected(self):
        with pytest.raises(ValueError, match="path separators"):
            _validate_trust_root_id("foo\\bar")

    def test_double_dot_rejected(self):
        with pytest.raises(ValueError, match="must not contain '\\.\\.'"):
            _validate_trust_root_id("..")

    def test_dot_dot_in_path_rejected(self):
        # / is checked before .., so the error will mention path separators
        with pytest.raises(ValueError, match="path separators"):
            _validate_trust_root_id("foo/../bar")

    def test_dot_dot_alone_rejected(self):
        with pytest.raises(ValueError, match="must not contain '\\.\\.'"):
            _validate_trust_root_id("..")

    def test_absolute_path_rejected(self):
        with pytest.raises(ValueError, match="path separators"):
            _validate_trust_root_id("/etc/passwd")

    def test_unicode_valid_id_accepted(self):
        _validate_trust_root_id("tröst-rööt-αβγ")

    def test_unicode_traversal_dots_rejected(self):
        # Unicode with .. is caught by the dots check
        with pytest.raises(ValueError, match="must not contain '\\.\\.'"):
            _validate_trust_root_id("tröst..étc")


# ── Store CRUD ───────────────────────────────────────────────────────────

class TestCreateTrustRoot:
    def test_create_and_retrieve(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        root = _make_trust_root("root-1", name="Root One")
        created = store.create_trust_root(root)

        assert created.trust_root_id == "root-1"
        assert created.name == "Root One"
        assert created.created_at != ""
        assert (store._roots_dir / "root-1.json").is_file()

    def test_created_at_auto_populated(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        root = _make_trust_root("root-1", created_at="")
        created = store.create_trust_root(root)
        assert created.created_at != ""
        # Verify it's a valid ISO timestamp
        datetime.fromisoformat(created.created_at)

    def test_existing_created_at_preserved(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        ts = "2025-01-15T10:00:00+00:00"
        root = _make_trust_root("root-1", created_at=ts)
        created = store.create_trust_root(root)
        assert created.created_at == ts

    def test_file_persisted_correctly(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        root = _make_trust_root("root-1", name="Persist Test", key_type="ecdsa-p256")
        store.create_trust_root(root)

        path = store._roots_dir / "root-1.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["trust_root_id"] == "root-1"
        assert data["name"] == "Persist Test"
        assert data["key_type"] == "ecdsa-p256"
        assert "created_at" in data

    def test_duplicate_rejected(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(_make_trust_root("root-1"))
        with pytest.raises(ValueError, match="already exists"):
            store.create_trust_root(_make_trust_root("root-1"))

    def test_creates_trust_roots_dir(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        assert not store._roots_dir.exists()
        store.create_trust_root(_make_trust_root("root-1"))
        assert store._roots_dir.is_dir()


class TestGetTrustRoot:
    def test_get_existing(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(_make_trust_root("root-1", name="Found"))
        retrieved = store.get_trust_root("root-1")
        assert retrieved is not None
        assert retrieved.name == "Found"

    def test_get_missing(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        assert store.get_trust_root("nonexistent") is None

    def test_get_corrupt_json(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store._roots_dir.mkdir(parents=True)
        (store._roots_dir / "corrupt.json").write_text("not json", encoding="utf-8")
        assert store.get_trust_root("corrupt") is None

    def test_get_non_dict_json(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store._roots_dir.mkdir(parents=True)
        (store._roots_dir / "arr.json").write_text("[1, 2, 3]", encoding="utf-8")
        assert store.get_trust_root("arr") is None

    def test_get_empty_file(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store._roots_dir.mkdir(parents=True)
        (store._roots_dir / "empty.json").write_text("", encoding="utf-8")
        assert store.get_trust_root("empty") is None

    def test_get_invalid_id_returns_none(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        assert store.get_trust_root("../../etc/passwd") is None


class TestListTrustRoots:
    def test_list_empty(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        assert store.list_trust_roots() == []

    def test_list_all(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(_make_trust_root("a"))
        store.create_trust_root(_make_trust_root("b"))
        store.create_trust_root(_make_trust_root("c", status="disabled"))
        results = store.list_trust_roots()
        assert len(results) == 3
        ids = [r.trust_root_id for r in results]
        assert ids == ["a", "b", "c"]  # sorted

    def test_filter_by_status(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(_make_trust_root("a", status="active"))
        store.create_trust_root(_make_trust_root("b", status="active"))
        store.create_trust_root(_make_trust_root("c", status="disabled"))
        store.create_trust_root(_make_trust_root("d", status="revoked"))

        active = store.list_trust_roots(status="active")
        assert len(active) == 2
        assert all(r.status == "active" for r in active)

        disabled = store.list_trust_roots(status="disabled")
        assert len(disabled) == 1
        assert disabled[0].trust_root_id == "c"

        revoked = store.list_trust_roots(status="revoked")
        assert len(revoked) == 1
        assert revoked[0].trust_root_id == "d"

    def test_filter_by_scope(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(_make_trust_root("a", scope="global"))
        store.create_trust_root(_make_trust_root("b", scope="project"))
        store.create_trust_root(_make_trust_root("c", scope="global"))

        global_roots = store.list_trust_roots(scope="global")
        assert len(global_roots) == 2
        assert all(r.scope == "global" for r in global_roots)

        project_roots = store.list_trust_roots(scope="project")
        assert len(project_roots) == 1
        assert project_roots[0].trust_root_id == "b"

    def test_filter_by_status_and_scope(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(_make_trust_root("a", status="active", scope="global"))
        store.create_trust_root(_make_trust_root("b", status="disabled", scope="global"))
        store.create_trust_root(_make_trust_root("c", status="active", scope="project"))

        results = store.list_trust_roots(status="active", scope="global")
        assert len(results) == 1
        assert results[0].trust_root_id == "a"

    def test_skips_corrupt_files(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(_make_trust_root("good"))
        store._roots_dir.mkdir(parents=True, exist_ok=True)
        (store._roots_dir / "bad.json").write_text("garbage", encoding="utf-8")
        (store._roots_dir / "not_json.txt").write_text("{}", encoding="utf-8")

        results = store.list_trust_roots()
        assert len(results) == 1
        assert results[0].trust_root_id == "good"


# ── Status Management ────────────────────────────────────────────────────

class TestDisableTrustRoot:
    def test_disable_changes_status(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(_make_trust_root("root-1", status="active"))
        result = store.disable_trust_root("root-1")
        assert result is not None
        assert result.status == "disabled"

    def test_disable_persists(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(_make_trust_root("root-1", status="active"))
        store.disable_trust_root("root-1")
        retrieved = store.get_trust_root("root-1")
        assert retrieved.status == "disabled"

    def test_disable_stores_reason(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(_make_trust_root("root-1"))
        result = store.disable_trust_root("root-1", reason="key compromised")
        assert result.metadata.get("disabled_reason") == "key compromised"

    def test_disable_nonexistent(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        assert store.disable_trust_root("nope") is None

    def test_disable_idempotent(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(_make_trust_root("root-1", status="disabled"))
        result = store.disable_trust_root("root-1")
        assert result.status == "disabled"


class TestRevokeTrustRoot:
    def test_revoke_changes_status(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(_make_trust_root("root-1", status="active"))
        result = store.revoke_trust_root("root-1")
        assert result is not None
        assert result.status == "revoked"

    def test_revoke_persists(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(_make_trust_root("root-1", status="active"))
        store.revoke_trust_root("root-1")
        retrieved = store.get_trust_root("root-1")
        assert retrieved.status == "revoked"

    def test_revoke_stores_reason(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(_make_trust_root("root-1"))
        result = store.revoke_trust_root("root-1", reason="permanently compromised")
        assert result.metadata.get("revoked_reason") == "permanently compromised"

    def test_revoke_nonexistent(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        assert store.revoke_trust_root("nope") is None

    def test_disabled_still_retrievable(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(_make_trust_root("root-1"))
        store.disable_trust_root("root-1")
        assert store.get_trust_root("root-1") is not None
        assert store.list_trust_roots(status="disabled")[0].trust_root_id == "root-1"

    def test_revoked_still_retrievable(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(_make_trust_root("root-1"))
        store.revoke_trust_root("root-1")
        assert store.get_trust_root("root-1") is not None
        assert store.list_trust_roots(status="revoked")[0].trust_root_id == "root-1"


# ── Active Checks ────────────────────────────────────────────────────────

class TestIsTrustRootActive:
    def test_active_root_is_active(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(_make_trust_root("root-1", status="active"))
        assert store.is_trust_root_active("root-1") is True

    def test_disabled_not_active(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(_make_trust_root("root-1", status="disabled"))
        assert store.is_trust_root_active("root-1") is False

    def test_revoked_not_active(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(_make_trust_root("root-1", status="revoked"))
        assert store.is_trust_root_active("root-1") is False

    def test_missing_not_active(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        assert store.is_trust_root_active("nonexistent") is False

    def test_expired_not_active(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        past = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        store.create_trust_root(_make_trust_root("root-1", expires_at=past))
        assert store.is_trust_root_active("root-1") is False

    def test_not_expired_is_active(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        store.create_trust_root(_make_trust_root("root-1", expires_at=future))
        assert store.is_trust_root_active("root-1") is True

    def test_no_expiry_is_active(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(_make_trust_root("root-1", expires_at=None))
        assert store.is_trust_root_active("root-1") is True

    def test_at_time_overrides_now(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        store.create_trust_root(_make_trust_root("root-1", expires_at=future))
        # Fast-forward past expiry
        far_future = datetime.now(timezone.utc) + timedelta(days=60)
        assert store.is_trust_root_active("root-1", at_time=far_future) is False

    def test_unparseable_expiry_not_active(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(_make_trust_root("root-1"))
        # Manually write a bad expiry
        path = store._root_path("root-1")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["expires_at"] = "not-a-timestamp"
        path.write_text(json.dumps(data), encoding="utf-8")
        assert store.is_trust_root_active("root-1") is False

    def test_disabled_and_expired(self, tmp_path):
        """Disabled takes priority — not active even if not expired."""
        store = TrustRootStore(data_dir=tmp_path)
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        store.create_trust_root(_make_trust_root("root-1", status="disabled", expires_at=future))
        assert store.is_trust_root_active("root-1") is False

    def test_corrupt_file_not_active(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store._roots_dir.mkdir(parents=True)
        (store._roots_dir / "corrupt.json").write_text("not json", encoding="utf-8")
        assert store.is_trust_root_active("corrupt") is False

    def test_secret_containing_file_not_active(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store._roots_dir.mkdir(parents=True)
        (store._roots_dir / "secret.json").write_text(json.dumps({
            "trust_root_id": "secret",
            "name": "Test",
            "key_type": "ed25519",
            "public_key_fingerprint": "abc",
            "status": "active",
            "created_at": "",
            "metadata": {},
            "private_key": "-----BEGIN RSA PRIVATE KEY-----",
        }))
        assert store.is_trust_root_active("secret") is False


# ── as_verifier_dict ─────────────────────────────────────────────────────

class TestAsVerifierDict:
    def test_returns_all_roots_including_disabled_revoked_expired(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(_make_trust_root("a", status="active"))
        store.create_trust_root(_make_trust_root("b", status="disabled"))
        store.create_trust_root(_make_trust_root("c", status="revoked"))
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        store.create_trust_root(_make_trust_root("d", status="active", expires_at=past))
        d = store.as_verifier_dict()
        # All roots returned so verifier can check status/expiry
        assert len(d) == 4
        assert "a" in d
        assert "b" in d
        assert "c" in d
        assert "d" in d

    def test_empty_when_no_roots(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        assert store.as_verifier_dict() == {}

    def test_returns_capability_trust_root_objects(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(_make_trust_root("a"))
        d = store.as_verifier_dict()
        assert isinstance(d["a"], CapabilityTrustRoot)
        assert d["a"].trust_root_id == "a"


# ── Atomic Write ─────────────────────────────────────────────────────────

class TestAtomicWrite:
    def test_no_tmp_file_left_behind(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(_make_trust_root("root-1"))
        tmps = list(store._roots_dir.glob("*.tmp"))
        assert len(tmps) == 0

    def test_complete_file_on_disk(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(_make_trust_root("root-1"))
        path = store._roots_dir / "root-1.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["trust_root_id"] == "root-1"

    def test_disable_writes_atomically(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(_make_trust_root("root-1"))
        store.disable_trust_root("root-1")
        tmps = list(store._roots_dir.glob("*.tmp"))
        assert len(tmps) == 0

    def test_revoke_writes_atomically(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(_make_trust_root("root-1"))
        store.revoke_trust_root("root-1")
        tmps = list(store._roots_dir.glob("*.tmp"))
        assert len(tmps) == 0


# ── Secret / Private Key Rejection ───────────────────────────────────────

class TestSecretRejection:
    # Value-based rejection on create (top-level fields like name, owner)

    def test_pem_private_key_in_value_rejected(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        root = _make_trust_root("bad", name="-----BEGIN PRIVATE KEY-----")
        with pytest.raises(ValueError, match="private key material"):
            store.create_trust_root(root)

    def test_openssh_private_key_rejected(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        root = _make_trust_root("bad", name="-----BEGIN OPENSSH PRIVATE KEY-----")
        with pytest.raises(ValueError, match="private key material"):
            store.create_trust_root(root)

    def test_api_key_sk_prefix_rejected(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        root = _make_trust_root("bad", name="sk-proj-abc123")
        with pytest.raises(ValueError, match="API key or bearer token"):
            store.create_trust_root(root)

    def test_api_key_sk_underscore_rejected(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        root = _make_trust_root("bad", name="sk_abc123")
        with pytest.raises(ValueError, match="API key or bearer token"):
            store.create_trust_root(root)

    def test_bearer_token_rejected(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        root = _make_trust_root("bad", name="Bearer eyJhbGciOiJIUzI1NiJ9.xxx")
        with pytest.raises(ValueError, match="API key or bearer token"):
            store.create_trust_root(root)

    # Field name rejection tested via raw file read (get_trust_root)

    def test_get_rejects_private_key_field(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store._roots_dir.mkdir(parents=True)
        (store._roots_dir / "secret.json").write_text(json.dumps({
            "trust_root_id": "secret",
            "name": "Test",
            "key_type": "ed25519",
            "public_key_fingerprint": "abc",
            "status": "active",
            "created_at": "",
            "metadata": {},
            "private_key": "-----BEGIN RSA PRIVATE KEY-----",
        }))
        assert store.get_trust_root("secret") is None

    def test_get_rejects_secret_key_field(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store._roots_dir.mkdir(parents=True)
        (store._roots_dir / "s.json").write_text(json.dumps({
            "trust_root_id": "s",
            "name": "Test",
            "key_type": "ed25519",
            "public_key_fingerprint": "abc",
            "status": "active",
            "created_at": "",
            "metadata": {},
            "secret_key": "xyz",
        }))
        assert store.get_trust_root("s") is None

    def test_get_rejects_api_key_field(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store._roots_dir.mkdir(parents=True)
        (store._roots_dir / "ak.json").write_text(json.dumps({
            "trust_root_id": "ak",
            "name": "Test",
            "key_type": "ed25519",
            "public_key_fingerprint": "abc",
            "status": "active",
            "created_at": "",
            "metadata": {},
            "api_key": "sk-abc",
        }))
        assert store.get_trust_root("ak") is None

    def test_get_rejects_password_field(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store._roots_dir.mkdir(parents=True)
        (store._roots_dir / "pw.json").write_text(json.dumps({
            "trust_root_id": "pw",
            "name": "Test",
            "key_type": "ed25519",
            "public_key_fingerprint": "abc",
            "status": "active",
            "created_at": "",
            "metadata": {},
            "password": "hunter2",
        }))
        assert store.get_trust_root("pw") is None

    def test_get_rejects_token_field(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store._roots_dir.mkdir(parents=True)
        (store._roots_dir / "tok.json").write_text(json.dumps({
            "trust_root_id": "tok",
            "name": "Test",
            "key_type": "ed25519",
            "public_key_fingerprint": "abc",
            "status": "active",
            "created_at": "",
            "metadata": {},
            "token": "ghp_xxx",
        }))
        assert store.get_trust_root("tok") is None

    def test_get_rejects_signing_key_field(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store._roots_dir.mkdir(parents=True)
        (store._roots_dir / "sk.json").write_text(json.dumps({
            "trust_root_id": "sk",
            "name": "Test",
            "key_type": "ed25519",
            "public_key_fingerprint": "abc",
            "status": "active",
            "created_at": "",
            "metadata": {},
            "signing_key": "secret-value",
        }))
        assert store.get_trust_root("sk") is None

    def test_get_rejects_key_material_field(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store._roots_dir.mkdir(parents=True)
        (store._roots_dir / "km.json").write_text(json.dumps({
            "trust_root_id": "km",
            "name": "Test",
            "key_type": "ed25519",
            "public_key_fingerprint": "abc",
            "status": "active",
            "created_at": "",
            "metadata": {},
            "key_material": "secret-material",
        }))
        assert store.get_trust_root("km") is None

    def test_get_rejects_case_insensitive_field(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store._roots_dir.mkdir(parents=True)
        (store._roots_dir / "ci.json").write_text(json.dumps({
            "trust_root_id": "ci",
            "name": "Test",
            "key_type": "ed25519",
            "public_key_fingerprint": "abc",
            "status": "active",
            "created_at": "",
            "metadata": {},
            "PRIVATE_KEY": "value",
        }))
        assert store.get_trust_root("ci") is None

    # Metadata is exempt from scanning

    def test_metadata_exempt_from_secret_scanning(self, tmp_path):
        """metadata dict values are exempt from secret field name/value scanning."""
        store = TrustRootStore(data_dir=tmp_path)
        root = CapabilityTrustRoot(
            trust_root_id="ok",
            name="OK",
            key_type="ed25519",
            public_key_fingerprint="abc",
            metadata={"notes": "stored safely", "token": "this-is-fine-in-metadata"},
        )
        created = store.create_trust_root(root)
        assert created.trust_root_id == "ok"

    # Public key fingerprint is allowed

    def test_public_key_fingerprint_allowed(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        root = _make_trust_root("ok", fingerprint="sha256:abcdef1234567890")
        created = store.create_trust_root(root)
        assert created.public_key_fingerprint == "sha256:abcdef1234567890"

    def test_no_private_key_ever_stored(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(_make_trust_root("ok"))
        path = store._roots_dir / "ok.json"
        content = path.read_text(encoding="utf-8")
        assert "PRIVATE KEY" not in content.upper()
        assert "sk-" not in content


# ── Path Safety ──────────────────────────────────────────────────────────

class TestPathSafety:
    def test_create_rejects_traversal(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        root = _make_trust_root("../../etc/malicious")
        with pytest.raises(ValueError, match="path separators"):
            store.create_trust_root(root)

    def test_create_rejects_dot_dot(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        root = _make_trust_root("..")
        with pytest.raises(ValueError, match="must not contain '\\.\\.'"):
            store.create_trust_root(root)

    def test_no_write_outside_trust_roots_dir(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        root = _make_trust_root("ok")
        store.create_trust_root(root)
        path = store._root_path("ok")
        assert path.parent == store._roots_dir

    def test_get_rejects_traversal(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        # get_trust_root catches ValueError from _validate_trust_root_id
        assert store.get_trust_root("../../etc/passwd") is None

    def test_disable_rejects_traversal(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        assert store.disable_trust_root("../../etc/passwd") is None

    def test_revoke_rejects_traversal(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        assert store.revoke_trust_root("../../etc/passwd") is None


# ── Edge Cases ───────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_fields_round_trip(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        root = CapabilityTrustRoot(
            trust_root_id="full",
            name="Full Root",
            key_type="ecdsa-p256",
            public_key_fingerprint="sha256:abc123",
            owner="org-team",
            scope="project",
            status="active",
            created_at="2025-06-01T00:00:00+00:00",
            expires_at="2027-06-01T00:00:00+00:00",
            metadata={"env": "prod", "region": "us-east-1"},
        )
        created = store.create_trust_root(root)
        assert created.trust_root_id == "full"
        assert created.name == "Full Root"
        assert created.key_type == "ecdsa-p256"
        assert created.public_key_fingerprint == "sha256:abc123"
        assert created.owner == "org-team"
        assert created.scope == "project"
        assert created.status == "active"
        assert created.expires_at == "2027-06-01T00:00:00+00:00"
        assert created.metadata == {"env": "prod", "region": "us-east-1"}

    def test_empty_metadata_round_trip(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(_make_trust_root("minimal"))
        retrieved = store.get_trust_root("minimal")
        assert retrieved.metadata == {}

    def test_recreated_store_reads_same_data(self, tmp_path):
        store1 = TrustRootStore(data_dir=tmp_path)
        store1.create_trust_root(_make_trust_root("persist", name="Persistent"))
        store2 = TrustRootStore(data_dir=tmp_path)
        retrieved = store2.get_trust_root("persist")
        assert retrieved is not None
        assert retrieved.name == "Persistent"

    def test_nonexistent_dir_list_returns_empty(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path / "nonexistent")
        assert store.list_trust_roots() == []

    def test_filter_no_matches(self, tmp_path):
        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(_make_trust_root("a", status="active"))
        assert store.list_trust_roots(status="revoked") == []
