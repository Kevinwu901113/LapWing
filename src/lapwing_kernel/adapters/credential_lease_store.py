"""CredentialLeaseStore — in-memory one-shot credential lease.

HARD CONSTRAINT (blueprint §7.2, GPT final-pass sign-off note):
  This store MUST remain in-memory only. Persisting leases ANYWHERE is
  forbidden:
    - sqlite / data/lapwing.db / any other DB
    - jsonl / log files / append-only stores
    - shelve / pickle / marshal
    - in-process disk caches
    - shared memory across processes

  Lease secrets are vault-decrypted plaintext; persisting them would
  defeat CredentialVault's encryption and violate I-2 (no LLM-visible
  plaintext). Process restart = all leases lost — this is acceptable
  because lease TTL is bounded to 30s; no scenario requires a sub-minute
  lease to survive a restart.

  Enforced by tests/lapwing_kernel/test_credential_lease_store.py static
  grep over this file's source (blueprint §15.2 I-2).
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


# Default TTL: 30 seconds — enough for a single login form fill, expires
# automatically if the consuming adapter crashes mid-flow.
DEFAULT_LEASE_TTL = timedelta(seconds=30)


@dataclass(frozen=True)
class CredentialLease:
    """Ephemeral credential lease metadata. NOT serializable to LLM /
    EventLog / Action.args / Observation.content.

    The actual secret lives inside CredentialLeaseStore (separate dict);
    accessing it requires LeaseStore.consume(id), which removes the entry.
    """

    id: str
    service: str
    purpose: str
    issued_at: datetime
    expires_at: datetime


class CredentialLeaseStore:
    """In-memory one-shot lease store. Singleton per process.

    NOT visible to LLM. NOT serialized. NOT logged.
    """

    _instance: "CredentialLeaseStore | None" = None

    @classmethod
    def instance(cls) -> "CredentialLeaseStore":
        if cls._instance is None:
            cls._instance = CredentialLeaseStore()
        return cls._instance

    @classmethod
    def reset_for_tests(cls) -> None:
        cls._instance = None

    def __init__(self) -> None:
        self._secrets: dict[str, Any] = {}
        self._leases: dict[str, CredentialLease] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        *,
        service: str,
        secret: Any,
        purpose: str = "browser_login",
        ttl: timedelta = DEFAULT_LEASE_TTL,
    ) -> CredentialLease:
        async with self._lock:
            now = datetime.utcnow()
            lease = CredentialLease(
                id=str(uuid.uuid4()),
                service=service,
                purpose=purpose,
                issued_at=now,
                expires_at=now + ttl,
            )
            self._secrets[lease.id] = secret
            self._leases[lease.id] = lease
            asyncio.create_task(self._auto_expire(lease.id, ttl))
        return lease

    async def consume(self, lease_id: str) -> Any | None:
        """One-shot retrieve. Returns the secret object and removes the lease.

        Subsequent calls with the same lease_id return None — the secret has
        already been handed off and must not be re-issued.
        """
        async with self._lock:
            lease = self._leases.get(lease_id)
            if lease is None:
                return None
            if datetime.utcnow() > lease.expires_at:
                self._purge_locked(lease_id)
                return None
            secret = self._secrets.pop(lease_id)
            self._leases.pop(lease_id)
            return secret

    def peek_meta(self, lease_id: str) -> CredentialLease | None:
        """Read metadata only (no secret). Used by consumers to verify
        lease exists / belongs to the right service before consuming."""
        return self._leases.get(lease_id)

    def active_count(self) -> int:
        """Test/diagnostic helper. NOT a security boundary."""
        return len(self._leases)

    async def _auto_expire(self, lease_id: str, ttl: timedelta) -> None:
        await asyncio.sleep(ttl.total_seconds() + 1)
        async with self._lock:
            self._purge_locked(lease_id)

    def _purge_locked(self, lease_id: str) -> None:
        """Caller must hold self._lock."""
        self._secrets.pop(lease_id, None)
        self._leases.pop(lease_id, None)
