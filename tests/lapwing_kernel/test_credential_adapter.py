"""CredentialAdapter tests — LLM-visible surface + no-plaintext invariants.

Covers blueprint §7.3 / §7.6 / §15.2 I-2.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from src.lapwing_kernel.adapters.credential import CredentialAdapter
from src.lapwing_kernel.adapters.credential_lease_store import CredentialLeaseStore
from src.lapwing_kernel.adapters.credential_use_state import CredentialUseState
from src.lapwing_kernel.primitives.action import Action


# ── fakes ────────────────────────────────────────────────────────────────────


@dataclass
class FakeCredential:
    service: str
    username: str
    password: str
    login_url: str


class FakeVault:
    def __init__(self, services: dict[str, FakeCredential] | None = None):
        self._services = services or {}

    def get(self, service: str) -> FakeCredential | None:
        return self._services.get(service)

    def list_services(self) -> list[str]:
        return sorted(self._services.keys())


@pytest.fixture(autouse=True)
def fresh_lease_store():
    CredentialLeaseStore.reset_for_tests()
    yield
    CredentialLeaseStore.reset_for_tests()


@pytest.fixture
def use_state(tmp_path: Path) -> CredentialUseState:
    return CredentialUseState(tmp_path / "lapwing.db")


@pytest.fixture
def vault() -> FakeVault:
    return FakeVault(
        services={
            "github": FakeCredential(
                service="github",
                username="kevin@example.com",
                password="MySecretPassword123!",
                login_url="https://github.com/login",
            ),
            "gmail": FakeCredential(
                service="gmail",
                username="kevin@gmail.com",
                password="another_super_secret",
                login_url="https://accounts.google.com",
            ),
        }
    )


def make_adapter(vault: FakeVault, use_state: CredentialUseState) -> CredentialAdapter:
    return CredentialAdapter(vault=vault, use_state=use_state)


# ── Resource Protocol ────────────────────────────────────────────────────────


class TestProtocolConformance:
    def test_name(self, vault, use_state):
        a = make_adapter(vault, use_state)
        assert a.name == "credential"

    def test_supports(self, vault, use_state):
        a = make_adapter(vault, use_state)
        for verb in ("list_count", "exists", "use", "create"):
            assert a.supports(verb)
        assert not a.supports("delete")


# ── LLM-visible surface (no service names exposed by default) ────────────────


class TestListCount:
    async def test_returns_count_only(self, vault, use_state):
        a = make_adapter(vault, use_state)
        obs = await a.execute(Action.new("credential", "list_count"))
        assert obs.status == "ok"
        assert obs.content == "count=2"

    async def test_no_service_names_in_observation(self, vault, use_state):
        """Blueprint §7.6: list_count must NOT leak service names."""
        a = make_adapter(vault, use_state)
        obs = await a.execute(Action.new("credential", "list_count"))
        for field in (obs.content or "", obs.summary or ""):
            assert "github" not in field
            assert "gmail" not in field

    async def test_empty_vault(self, use_state):
        a = make_adapter(FakeVault(), use_state)
        obs = await a.execute(Action.new("credential", "list_count"))
        assert obs.content == "count=0"


class TestExists:
    async def test_exists_returns_exists(self, vault, use_state):
        a = make_adapter(vault, use_state)
        obs = await a.execute(
            Action.new("credential", "exists", args={"service": "github"})
        )
        assert obs.content == "exists"

    async def test_missing_returns_missing(self, vault, use_state):
        a = make_adapter(vault, use_state)
        obs = await a.execute(
            Action.new("credential", "exists", args={"service": "twitter"})
        )
        assert obs.content == "missing"


# ── use: lease artifact, NO plaintext anywhere LLM-facing ────────────────────


class TestUse:
    async def test_returns_lease_ref_artifact(self, vault, use_state):
        a = make_adapter(vault, use_state)
        obs = await a.execute(
            Action.new("credential", "use", args={"service": "github"})
        )
        assert obs.status == "ok"
        assert obs.content == "credential available"
        assert len(obs.artifacts) == 1
        art = obs.artifacts[0]
        assert art["type"] == "credential_lease_ref"
        assert art["service"] == "github"

    async def test_password_never_in_observation(self, vault, use_state):
        """Blueprint §7.6 / §15.2 I-2: no plaintext password / token in
        ANY field reachable by LLM."""
        a = make_adapter(vault, use_state)
        obs = await a.execute(
            Action.new("credential", "use", args={"service": "github"})
        )
        leaked_fields = [
            obs.content or "",
            obs.summary or "",
            str(obs.provenance),
            str(obs.artifacts),
        ]
        for field in leaked_fields:
            assert "MySecretPassword123!" not in field, (
                f"Plaintext password leaked into Observation field: {field[:200]}"
            )

    async def test_lease_consumable_via_lease_store(self, vault, use_state):
        """The consumer (BrowserAdapter._login_with_lease) retrieves the
        plaintext credential from CredentialLeaseStore using the lease id."""
        a = make_adapter(vault, use_state)
        obs = await a.execute(
            Action.new("credential", "use", args={"service": "github"})
        )
        lease_id = obs.artifacts[0]["ref"]
        secret = await CredentialLeaseStore.instance().consume(lease_id)
        assert secret is not None
        assert secret.username == "kevin@example.com"
        assert secret.password == "MySecretPassword123!"
        # And the lease is now exhausted
        assert await CredentialLeaseStore.instance().consume(lease_id) is None

    async def test_missing_service_returns_missing_status(self, vault, use_state):
        a = make_adapter(vault, use_state)
        obs = await a.execute(
            Action.new("credential", "use", args={"service": "no_such_thing"})
        )
        assert obs.status == "missing"

    async def test_missing_service_arg_returns_failed(self, vault, use_state):
        a = make_adapter(vault, use_state)
        obs = await a.execute(Action.new("credential", "use", args={}))
        assert obs.status == "failed"
        assert obs.error == "missing_service"

    async def test_use_marks_state_used(self, vault, use_state):
        """After successful use, CredentialUseState records the approval
        so subsequent PolicyDecider checks return ALLOW (not INTERRUPT)."""
        a = make_adapter(vault, use_state)
        assert not use_state.has_been_used("github")
        await a.execute(
            Action.new("credential", "use", args={"service": "github"})
        )
        assert use_state.has_been_used("github")


# ── create: blocked_by_policy (owner-only via CLI) ───────────────────────────


class TestCreate:
    async def test_create_always_blocked(self, vault, use_state):
        a = make_adapter(vault, use_state)
        obs = await a.execute(
            Action.new(
                "credential",
                "create",
                args={"service": "x", "username": "u", "password": "p"},
            )
        )
        assert obs.status == "blocked_by_policy"
        assert "owner-only" in obs.error


# ── Adapter-to-adapter import boundary (§7.1 / §15.3 #11) ────────────────────


class TestNoCrossAdapterImports:
    """No adapter directly imports another adapter."""

    def test_credential_does_not_import_browser(self):
        adapter_dir = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "lapwing_kernel"
            / "adapters"
        )
        cred_src = (adapter_dir / "credential.py").read_text()
        assert (
            "from .browser" not in cred_src
            and "from src.lapwing_kernel.adapters.browser" not in cred_src
            and "import browser" not in cred_src.replace("# import browser", "")
        ), "CredentialAdapter must not import BrowserAdapter (blueprint §7.1)"

    def test_credential_lease_store_does_not_import_browser(self):
        adapter_dir = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "lapwing_kernel"
            / "adapters"
        )
        src = (adapter_dir / "credential_lease_store.py").read_text()
        assert (
            "from .browser" not in src
            and "from src.lapwing_kernel.adapters.browser" not in src
        ), "CredentialLeaseStore must not import BrowserAdapter"


# ── CredentialUseState ledger sanity ─────────────────────────────────────────


class TestCredentialUseState:
    def test_initial_state_empty(self, tmp_path):
        s = CredentialUseState(tmp_path / "lapwing.db")
        assert s.has_been_used("anything") is False
        assert s.list_approved() == []

    def test_mark_used_records(self, tmp_path):
        s = CredentialUseState(tmp_path / "lapwing.db")
        s.mark_used("github")
        assert s.has_been_used("github")
        assert "github" in s.list_approved()

    def test_mark_used_idempotent(self, tmp_path):
        s = CredentialUseState(tmp_path / "lapwing.db")
        s.mark_used("github")
        s.mark_used("github")  # no error, no duplicate
        assert s.list_approved() == ["github"]

    def test_state_survives_reopen(self, tmp_path):
        path = tmp_path / "lapwing.db"
        s1 = CredentialUseState(path)
        s1.mark_used("github")
        s2 = CredentialUseState(path)
        assert s2.has_been_used("github")

    def test_creates_parent_dir(self, tmp_path):
        path = tmp_path / "nested" / "dir" / "lapwing.db"
        CredentialUseState(path)
        assert path.parent.is_dir()
