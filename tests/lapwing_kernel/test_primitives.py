"""Primitive dataclass tests — Action / Observation / Interrupt / Event / Resource."""
from __future__ import annotations

import pytest

from src.lapwing_kernel.primitives.action import Action
from src.lapwing_kernel.primitives.event import Event
from src.lapwing_kernel.primitives.interrupt import (
    DEFAULT_INTERRUPT_EXPIRY,
    Interrupt,
)
from src.lapwing_kernel.primitives.observation import (
    BROWSER_EXTRA_STATUS,
    COMMON_STATUS,
    CREDENTIAL_EXTRA_STATUS,
    Observation,
    validate_status,
)


class TestAction:
    def test_new_assigns_uuid(self):
        a = Action.new("browser", "navigate", args={"url": "https://x.com"})
        assert a.id
        assert a.resource == "browser"
        assert a.verb == "navigate"
        assert a.args == {"url": "https://x.com"}
        assert a.actor == "lapwing"

    def test_resource_profile_is_top_level(self):
        a = Action.new("browser", "navigate", resource_profile="personal")
        assert a.resource_profile == "personal"
        # profile is NOT in args
        assert "profile" not in a.args
        assert "resource_profile" not in a.args

    def test_parent_action_id_tracks_chain(self):
        parent = Action.new("browser", "login")
        child = Action.new("credential", "use", parent_action_id=parent.id)
        assert child.parent_action_id == parent.id

    def test_frozen(self):
        a = Action.new("browser", "navigate")
        with pytest.raises((AttributeError, TypeError)):
            a.verb = "click"  # type: ignore[misc]


class TestObservation:
    def test_ok_factory(self):
        o = Observation.ok("a1", "browser", summary="loaded", content="text")
        assert o.status == "ok"
        assert o.action_id == "a1"
        assert o.resource == "browser"
        assert o.content == "text"

    def test_failure_factory(self):
        o = Observation.failure("a1", "browser", status="timeout", error="TimeoutError")
        assert o.status == "timeout"
        assert o.error == "TimeoutError"

    def test_interrupted_factory(self):
        o = Observation.interrupted(
            "a1", "browser", interrupt_id="i1", summary="captcha"
        )
        assert o.status == "interrupted"
        assert o.interrupt_id == "i1"

    def test_status_validation_common(self):
        for s in COMMON_STATUS:
            assert validate_status("anything", s)

    def test_status_validation_browser_extra(self):
        for s in BROWSER_EXTRA_STATUS:
            assert validate_status("browser", s)
            # browser-extras must NOT validate on other resources
            assert not validate_status("credential", s)

    def test_status_validation_credential_extra(self):
        for s in CREDENTIAL_EXTRA_STATUS:
            assert validate_status("credential", s)
            assert not validate_status("browser", s)

    def test_status_validation_rejects_unknown(self):
        assert not validate_status("browser", "made_up_status")
        assert not validate_status("anything", "made_up_status")


class TestInterrupt:
    def test_continuation_first_enforced(self):
        """Hard rule: continuation_ref OR non_resumable=True required."""
        with pytest.raises(ValueError, match="continuation_ref OR non_resumable"):
            Interrupt.new(
                kind="browser.captcha",
                actor_required="owner",
                resource="browser",
            )

    def test_non_resumable_allowed_without_continuation(self):
        i = Interrupt.new(
            kind="browser.waf",
            actor_required="owner",
            resource="browser",
            non_resumable=True,
            non_resumable_reason="fetch profile cannot takeover",
        )
        assert i.continuation_ref is None
        assert i.non_resumable is True
        assert i.status == "pending"

    def test_continuation_ref_satisfies(self):
        i = Interrupt.new(
            kind="browser.captcha",
            actor_required="owner",
            resource="browser",
            continuation_ref="cont-123",
        )
        assert i.continuation_ref == "cont-123"
        assert i.non_resumable is False

    def test_expires_in_computes_expires_at(self):
        i = Interrupt.new(
            kind="browser.captcha",
            actor_required="owner",
            resource="browser",
            continuation_ref="cont-1",
            expires_in=DEFAULT_INTERRUPT_EXPIRY["browser.captcha"],
        )
        assert i.expires_at is not None
        assert i.expires_at > i.created_at

    def test_default_expiry_24h_for_browser_kinds(self):
        from datetime import timedelta

        for kind in (
            "browser.captcha",
            "browser.login_required",
            "browser.auth_2fa",
            "browser.waf",
        ):
            assert DEFAULT_INTERRUPT_EXPIRY[kind] == timedelta(hours=24)


class TestEvent:
    def test_new_captures_actor_type_summary(self):
        e = Event.new(
            actor="lapwing",
            type="browser.navigate",
            summary="loaded x.com",
            resource="browser",
            refs={"action_id": "a1"},
        )
        assert e.actor == "lapwing"
        assert e.type == "browser.navigate"
        assert e.resource == "browser"
        assert e.refs == {"action_id": "a1"}

    def test_data_redacted_default_empty(self):
        e = Event.new(actor="lapwing", type="test", summary="x")
        assert e.data_redacted == {}
        assert e.refs == {}
