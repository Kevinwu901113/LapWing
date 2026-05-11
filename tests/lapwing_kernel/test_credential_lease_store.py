"""CredentialLeaseStore tests — in-memory invariant + lifecycle.

Critical: covers blueprint §15.2 I-2 HARD CONSTRAINT static-grep test —
no persistence imports anywhere in the source file.
"""
from __future__ import annotations

import asyncio
import re
from datetime import timedelta
from pathlib import Path

import pytest

from src.lapwing_kernel.adapters.credential_lease_store import (
    DEFAULT_LEASE_TTL,
    CredentialLease,
    CredentialLeaseStore,
)


LEASE_STORE_SRC = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "lapwing_kernel"
    / "adapters"
    / "credential_lease_store.py"
)


@pytest.fixture(autouse=True)
def fresh_store():
    CredentialLeaseStore.reset_for_tests()
    yield
    CredentialLeaseStore.reset_for_tests()


# ── I-2 HARD CONSTRAINT: no persistence anywhere in this file ────────────────


class TestNoPersistenceInvariant:
    """Blueprint §7.2 / §15.2 I-2: this file must remain in-memory only.

    Static grep over the source rejects every persistence-shaped pattern.
    """

    def test_no_sqlite3_import(self):
        src = LEASE_STORE_SRC.read_text()
        assert (
            "import sqlite3" not in src and "from sqlite3" not in src
        ), "credential_lease_store.py must not import sqlite3 (§7.2 HARD CONSTRAINT)"

    def test_no_aiosqlite_import(self):
        src = LEASE_STORE_SRC.read_text()
        assert (
            "import aiosqlite" not in src and "from aiosqlite" not in src
        ), "credential_lease_store.py must not import aiosqlite"

    def test_no_file_writes(self):
        src = LEASE_STORE_SRC.read_text()
        # open(..., 'w'/'wb'/'a') or open(..., 'wt') or open(..., 'ab')
        pat = re.compile(r"open\s*\([^)]*['\"][wa]b?['\"]", re.IGNORECASE)
        assert (
            not pat.search(src)
        ), "credential_lease_store.py must not open files for writing"

    def test_no_pickle(self):
        src = LEASE_STORE_SRC.read_text()
        forbidden = ["import pickle", "from pickle", "pickle.dump", "pickle.dumps"]
        for f in forbidden:
            assert f not in src, (
                f"credential_lease_store.py must not contain {f!r} — "
                f"persisting plaintext defeats vault encryption"
            )

    def test_no_shelve(self):
        src = LEASE_STORE_SRC.read_text()
        assert (
            "import shelve" not in src and "from shelve" not in src
        ), "credential_lease_store.py must not use shelve"

    def test_no_marshal(self):
        src = LEASE_STORE_SRC.read_text()
        assert (
            "import marshal" not in src and "from marshal" not in src
        ), "credential_lease_store.py must not use marshal"

    def test_no_json_dump_to_file(self):
        """json.dumps to memory is fine; json.dump to a file handle is not."""
        src = LEASE_STORE_SRC.read_text()
        assert "json.dump(" not in src and "json.dump_to" not in src, (
            "credential_lease_store.py must not write json to file"
        )

    def test_no_shared_memory(self):
        src = LEASE_STORE_SRC.read_text()
        forbidden = ["multiprocessing.shared_memory", "mmap.mmap"]
        for f in forbidden:
            assert f not in src, (
                f"credential_lease_store.py must not use {f!r} (no cross-process state)"
            )


# ── lease lifecycle ──────────────────────────────────────────────────────────


class TestLeaseLifecycle:
    async def test_create_then_consume_returns_secret(self):
        store = CredentialLeaseStore.instance()
        secret = {"username": "kevin", "password": "hunter2"}
        lease = await store.create(service="github", secret=secret)
        assert isinstance(lease, CredentialLease)
        assert lease.service == "github"
        out = await store.consume(lease.id)
        assert out == secret

    async def test_consume_is_one_shot(self):
        store = CredentialLeaseStore.instance()
        lease = await store.create(service="github", secret={"u": "k"})
        first = await store.consume(lease.id)
        assert first is not None
        second = await store.consume(lease.id)
        assert second is None

    async def test_consume_unknown_returns_none(self):
        store = CredentialLeaseStore.instance()
        assert await store.consume("nonexistent-id") is None

    async def test_peek_meta_does_not_consume(self):
        store = CredentialLeaseStore.instance()
        lease = await store.create(service="github", secret="s")
        meta = store.peek_meta(lease.id)
        assert meta is not None
        assert meta.id == lease.id
        # Still consumable
        assert await store.consume(lease.id) == "s"

    async def test_active_count(self):
        store = CredentialLeaseStore.instance()
        assert store.active_count() == 0
        l1 = await store.create(service="a", secret="s1")
        l2 = await store.create(service="b", secret="s2")
        assert store.active_count() == 2
        await store.consume(l1.id)
        assert store.active_count() == 1

    async def test_lease_expires_after_ttl(self):
        store = CredentialLeaseStore.instance()
        lease = await store.create(
            service="x",
            secret="s",
            ttl=timedelta(milliseconds=10),
        )
        # Wait past expiry
        await asyncio.sleep(0.05)
        # Consume should now return None (expired)
        assert await store.consume(lease.id) == None


# ── §15.2 I-2: lease lost after process restart ──────────────────────────────


class TestProcessRestart:
    """Integration test simulating process restart: a lease created in one
    'process' must not survive a fresh `CredentialLeaseStore.instance()`
    call (since reset_for_tests simulates a new process)."""

    async def test_lease_lost_after_process_restart(self):
        store_before = CredentialLeaseStore.instance()
        lease = await store_before.create(service="x", secret="some_secret")
        # peek_meta is sync — not a coroutine
        assert store_before.peek_meta(lease.id) is not None

        # Simulate process restart
        CredentialLeaseStore.reset_for_tests()

        store_after = CredentialLeaseStore.instance()
        # Different instance, empty
        assert store_after is not store_before
        assert store_after.active_count() == 0
        # Old lease id is gone
        assert await store_after.consume(lease.id) is None
