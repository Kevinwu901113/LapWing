"""ActionExecutor tests — ALLOW / BLOCK / INTERRUPT three-path coverage.

Uses in-memory mock InterruptStore + EventLog. Real implementations land in
Slice D / F.
"""
from __future__ import annotations

from typing import Any

import pytest

from src.lapwing_kernel.pipeline.continuation_registry import ContinuationRegistry
from src.lapwing_kernel.pipeline.executor import ActionExecutor
from src.lapwing_kernel.pipeline.registry import ResourceRegistry
from src.lapwing_kernel.policy import PolicyDecider
from src.lapwing_kernel.primitives.action import Action
from src.lapwing_kernel.primitives.event import Event
from src.lapwing_kernel.primitives.interrupt import Interrupt
from src.lapwing_kernel.primitives.observation import Observation


# ----- mocks -----


class MockInterruptStore:
    def __init__(self) -> None:
        self.persisted: list[Interrupt] = []
        self.cancellations: list[tuple[str, str]] = []
        self.resolutions: list[tuple[str, dict]] = []

    def persist(self, interrupt: Interrupt) -> None:
        self.persisted.append(interrupt)

    def get(self, interrupt_id: str) -> Interrupt | None:
        for i in self.persisted:
            if i.id == interrupt_id:
                return i
        return None

    def resolve(self, interrupt_id: str, owner_payload: dict[str, Any]) -> None:
        self.resolutions.append((interrupt_id, owner_payload))

    def cancel(self, interrupt_id: str, *, reason: str) -> None:
        self.cancellations.append((interrupt_id, reason))


class MockEventLog:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def append(self, event: Event) -> None:
        self.events.append(event)


class OkResource:
    name = "browser"

    def supports(self, verb: str) -> bool:
        return verb in {"navigate", "click"}

    async def execute(self, action: Action) -> Observation:
        return Observation.ok(action.id, "browser", summary="loaded")


class FailingResource:
    name = "browser"

    def supports(self, verb: str) -> bool:
        return True

    async def execute(self, action: Action) -> Observation:
        raise RuntimeError("boom")


class StubCredentialUseState:
    def __init__(self, approved: set[str] | None = None):
        self._approved = approved or set()

    def has_been_used(self, service: str) -> bool:
        return service in self._approved


# ----- fixtures -----


@pytest.fixture
def store() -> MockInterruptStore:
    return MockInterruptStore()


@pytest.fixture
def events() -> MockEventLog:
    return MockEventLog()


@pytest.fixture(autouse=True)
def fresh_continuation_registry():
    ContinuationRegistry.reset_for_tests()
    yield
    ContinuationRegistry.reset_for_tests()


@pytest.fixture
def executor_with_browser(store, events):
    reg = ResourceRegistry()
    reg.register(OkResource(), profile="fetch")
    policy = PolicyDecider(config={})
    return ActionExecutor(
        resource_registry=reg,
        interrupt_store=store,
        event_log=events,
        policy=policy,
    )


# ----- ALLOW path -----


async def test_allow_path_returns_ok_and_logs_outcome(executor_with_browser, events):
    action = Action.new(
        "browser", "navigate", resource_profile="fetch", args={"url": "https://x.com"}
    )
    obs = await executor_with_browser.execute(action)
    assert obs.status == "ok"
    types = [e.type for e in events.events]
    assert "browser.navigate" in types
    assert "browser.ok" in types


async def test_unsupported_verb_returns_failed(executor_with_browser):
    action = Action.new("browser", "fly", resource_profile="fetch")
    obs = await executor_with_browser.execute(action)
    assert obs.status == "failed"
    assert obs.error == "unsupported_verb:fly"


async def test_adapter_exception_returns_failed_and_logs(store, events):
    reg = ResourceRegistry()
    reg.register(FailingResource(), profile="fetch")
    policy = PolicyDecider(config={})
    exec_ = ActionExecutor(reg, store, events, policy)

    action = Action.new("browser", "navigate", resource_profile="fetch")
    obs = await exec_.execute(action)
    assert obs.status == "failed"
    assert obs.error == "RuntimeError"
    assert any(e.type == "browser.failed" for e in events.events)


# ----- BLOCK path -----


async def test_block_path_blocked_by_policy(store, events):
    reg = ResourceRegistry()
    reg.register(OkResource(), profile="fetch")
    policy = PolicyDecider(config={"browser_fetch": {"url_blocklist": ["evil.com"]}})
    exec_ = ActionExecutor(reg, store, events, policy)

    action = Action.new(
        "browser", "navigate", resource_profile="fetch", args={"url": "https://evil.com/x"}
    )
    obs = await exec_.execute(action)
    assert obs.status == "blocked_by_policy"
    assert obs.error == "policy.block"
    assert any(e.type == "policy.blocked" for e in events.events)


# ----- INTERRUPT path -----


async def test_interrupt_path_credential_first_use(store, events):
    """Policy returns INTERRUPT for credential.use on never-seen service →
    executor persists Interrupt with continuation_ref + returns interrupted."""
    reg = ResourceRegistry()
    policy = PolicyDecider(config={}, use_state=StubCredentialUseState())
    exec_ = ActionExecutor(reg, store, events, policy)

    action = Action.new("credential", "use", args={"service": "github"})
    obs = await exec_.execute(action)

    assert obs.status == "interrupted"
    assert obs.interrupt_id is not None
    assert len(store.persisted) == 1
    persisted = store.persisted[0]
    assert persisted.kind == "policy.credential.use"
    assert persisted.continuation_ref is not None
    assert persisted.status == "pending"
    assert persisted.expires_at is not None
    assert any(e.type == "interrupt.created" for e in events.events)


async def test_interrupt_continuation_registered_for_await(store, events):
    """After INTERRUPT, the continuation must be live in the registry so a
    caller can wait_for_resume."""
    reg = ResourceRegistry()
    policy = PolicyDecider(config={}, use_state=StubCredentialUseState())
    exec_ = ActionExecutor(reg, store, events, policy)

    action = Action.new("credential", "use", args={"service": "github"})
    await exec_.execute(action)

    ref = store.persisted[0].continuation_ref
    assert ContinuationRegistry.instance().has(ref)


# ----- resume path -----


async def test_resume_marks_resolved_when_continuation_alive(store, events):
    reg = ResourceRegistry()
    policy = PolicyDecider(config={}, use_state=StubCredentialUseState())
    exec_ = ActionExecutor(reg, store, events, policy)

    action = Action.new("credential", "use", args={"service": "github"})
    obs = await exec_.execute(action)

    result = await exec_.resume(obs.interrupt_id, {"approved": True})
    assert result["status"] == "resumed"
    assert result["interrupt_id"] == obs.interrupt_id
    assert any(e.type == "interrupt.resolved" for e in events.events)
    assert store.resolutions == [(obs.interrupt_id, {"approved": True})]


async def test_resume_lost_continuation_cancels_not_resolves(store, events):
    """Critical edge: kernel restart between Interrupt creation and resume.
    Continuation no longer in registry → mark cancelled, NOT resolved."""
    reg = ResourceRegistry()
    policy = PolicyDecider(config={}, use_state=StubCredentialUseState())
    exec_ = ActionExecutor(reg, store, events, policy)

    action = Action.new("credential", "use", args={"service": "github"})
    obs = await exec_.execute(action)

    # Simulate kernel restart: registry forgets all continuations
    ContinuationRegistry.reset_for_tests()

    result = await exec_.resume(obs.interrupt_id, {"approved": True})
    assert result["status"] == "error"
    assert result["reason"] == "continuation_lost_after_restart"
    # InterruptStore.cancel was called, NOT resolve
    assert (obs.interrupt_id, "continuation_lost_after_restart") in store.cancellations
    assert store.resolutions == []
    # EventLog has the continuation_lost event
    assert any(e.type == "interrupt.continuation_lost" for e in events.events)


async def test_resume_unknown_interrupt_raises(store, events):
    reg = ResourceRegistry()
    policy = PolicyDecider(config={})
    exec_ = ActionExecutor(reg, store, events, policy)
    with pytest.raises(KeyError):
        await exec_.resume("nonexistent-id", {})


async def test_resume_non_pending_interrupt_raises(store, events):
    """Trying to resume an already-resolved interrupt errors out."""
    reg = ResourceRegistry()
    policy = PolicyDecider(config={}, use_state=StubCredentialUseState())
    exec_ = ActionExecutor(reg, store, events, policy)

    action = Action.new("credential", "use", args={"service": "github"})
    obs = await exec_.execute(action)
    # Mutate the stored interrupt status (mock doesn't enforce state machine)
    stored = store.persisted[0]
    from dataclasses import replace

    store.persisted[0] = replace(stored, status="resolved")

    with pytest.raises(ValueError, match="not pending"):
        await exec_.resume(obs.interrupt_id, {})
