"""SecretRedactor tests — Layer 1 + Layer 2 secret defense.

Implements the test plan in blueprint §5.5.
"""
from __future__ import annotations

import pytest

from src.lapwing_kernel.primitives.observation import Observation
from src.lapwing_kernel.redactor import SecretRedactor


@pytest.fixture
def redactor() -> SecretRedactor:
    return SecretRedactor(config={})


# ── Layer 1: sensitive input detection ──────────────────────────────────────


class TestSensitiveInputDetection:
    def test_password_input_is_sensitive(self):
        assert SecretRedactor.is_sensitive_input("password", "pwd", None, None, None)

    def test_otp_autocomplete_is_sensitive(self):
        assert SecretRedactor.is_sensitive_input(
            "text", "code", "one-time-code", None, None
        )

    def test_current_password_autocomplete_is_sensitive(self):
        assert SecretRedactor.is_sensitive_input(
            "text", None, "current-password", None, None
        )

    def test_new_password_autocomplete_is_sensitive(self):
        assert SecretRedactor.is_sensitive_input(
            "text", None, "new-password", None, None
        )

    def test_name_pattern_api_key(self):
        assert SecretRedactor.is_sensitive_input("text", "api_key", None, None, None)

    def test_placeholder_pattern_otp(self):
        assert SecretRedactor.is_sensitive_input(
            "text", None, None, "Enter OTP code", None
        )

    def test_aria_label_recovery_code(self):
        assert SecretRedactor.is_sensitive_input(
            "text", None, None, None, "Recovery code"
        )

    def test_normal_email_input_not_sensitive(self):
        assert not SecretRedactor.is_sensitive_input(
            "text", "email", "email", None, None
        )

    def test_search_input_not_sensitive(self):
        assert not SecretRedactor.is_sensitive_input(
            "text", "search", None, "search", None
        )

    def test_case_insensitive(self):
        """Name 'PASSWORD' must be detected even uppercase."""
        assert SecretRedactor.is_sensitive_input("text", "PASSWORD", None, None, None)


# ── Layer 2: text scrub ─────────────────────────────────────────────────────


class TestTextRedaction:
    def test_jwt_redacted(self, redactor):
        text = (
            "Header: eyJhbGciOiJIUzI1NiJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )
        out = redactor.redact_text(text)
        assert "REDACTED" in out
        # Original JWT pieces must not survive
        assert "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c" not in out

    def test_openai_key_redacted(self, redactor):
        text = "Set OPENAI_API_KEY to sk-abc1234567890abcdefghijklmnopqrstuvwxyz"
        out = redactor.redact_text(text)
        assert "REDACTED" in out
        assert "sk-abc1234567890abcdefghijklmnopqrstuvwxyz" not in out

    def test_github_pat_redacted(self, redactor):
        text = "Token is ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789"
        out = redactor.redact_text(text)
        assert "REDACTED" in out

    def test_slack_token_redacted(self, redactor):
        # Assemble the token-shaped fixture at runtime so this source file
        # doesn't contain the literal Slack-token pattern (GitHub's secret
        # scanner blocks pushes on the literal even though it's obviously
        # fake test data). The redactor regex still matches the assembled
        # string at runtime.
        fake_token = "x" + "oxb-" + "1234567890-" + "abcdefghijklmnop"
        text = f"Slack bot key {fake_token}"
        out = redactor.redact_text(text)
        assert "REDACTED" in out
        assert fake_token not in out

    def test_long_hex_redacted(self, redactor):
        text = "sha256=abc1234567890def1234567890abcdef1234567890"
        out = redactor.redact_text(text)
        assert "REDACTED" in out

    def test_long_base64ish_redacted(self, redactor):
        text = "Token: dGVzdHRva2VuZm9ydGVzdHRva2VudGVzdHRva2VudGVzdHRva2VudA=="
        out = redactor.redact_text(text)
        assert "REDACTED" in out

    def test_none_input_returns_none(self, redactor):
        assert redactor.redact_text(None) is None

    def test_empty_input_passes_through(self, redactor):
        assert redactor.redact_text("") == ""

    def test_plain_text_unchanged(self, redactor):
        text = "hello world this is plain text"
        assert redactor.redact_text(text) == text


# ── Dict redaction ──────────────────────────────────────────────────────────


class TestDictRedaction:
    def test_password_field_redacted_by_key(self, redactor):
        d = {"username": "kevin", "password": "hunter2", "service": "github"}
        out = redactor.redact_dict(d)
        assert out["password"] == "[REDACTED]"
        assert out["username"] == "kevin"

    def test_api_key_field_redacted(self, redactor):
        d = {"api_key": "anything", "user": "kevin"}
        out = redactor.redact_dict(d)
        assert out["api_key"] == "[REDACTED]"
        assert out["user"] == "kevin"

    def test_nested_dict_redacted(self, redactor):
        d = {"creds": {"api_key": "abc123def456", "url": "https://x.com"}}
        out = redactor.redact_dict(d)
        assert out["creds"]["api_key"] == "[REDACTED]"
        assert out["creds"]["url"] == "https://x.com"

    def test_list_string_values_redacted(self, redactor):
        # Use a properly-shaped JWT so the regex matches.
        jwt = (
            "eyJhbGciOiJIUzI1NiJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )
        d = {"logs": ["info: x", f"token: {jwt}"]}
        out = redactor.redact_dict(d)
        # JWT in list element should be redacted via redact_text
        assert all("SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c" not in entry for entry in out["logs"])

    def test_empty_dict_returns_empty(self, redactor):
        assert redactor.redact_dict({}) == {}

    def test_none_dict_returns_empty(self, redactor):
        assert redactor.redact_dict(None) == {}


# ── Observation-level redaction ─────────────────────────────────────────────


class TestObservationRedaction:
    def test_content_redacted(self, redactor):
        obs = Observation(
            id="o1",
            action_id="a1",
            resource="browser",
            status="ok",
            content=(
                "JWT in page: eyJhbGciOiJIUzI1NiJ9."
                "eyJzdWIiOiJrZXZpbiJ9."
                "signature_part_here_long_enough_to_match_pattern"
            ),
            artifacts=[],
        )
        out = redactor.redact_observation(obs)
        assert "REDACTED" in out.content
        assert "signature_part_here_long_enough" not in out.content

    def test_artifacts_password_key_redacted(self, redactor):
        obs = Observation(
            id="o1",
            action_id="a1",
            resource="browser",
            status="ok",
            artifacts=[{"page_state_ref": "ps1", "password": "leaked123"}],
        )
        out = redactor.redact_observation(obs)
        assert out.artifacts[0]["password"] == "[REDACTED]"
        assert out.artifacts[0]["page_state_ref"] == "ps1"

    def test_provenance_redacted(self, redactor):
        obs = Observation(
            id="o1",
            action_id="a1",
            resource="browser",
            status="ok",
            provenance={"url": "https://x.com", "api_key": "secret-value"},
        )
        out = redactor.redact_observation(obs)
        assert out.provenance["api_key"] == "[REDACTED]"
        assert out.provenance["url"] == "https://x.com"

    def test_summary_redacted(self, redactor):
        obs = Observation(
            id="o1",
            action_id="a1",
            resource="browser",
            status="ok",
            summary="Got token ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789 from page",
        )
        out = redactor.redact_observation(obs)
        assert "REDACTED" in out.summary

    def test_none_fields_remain_none(self, redactor):
        obs = Observation(
            id="o1",
            action_id="a1",
            resource="browser",
            status="ok",
        )
        out = redactor.redact_observation(obs)
        assert out.content is None
        assert out.summary is None


# ── Integration: full PageState leak path (blueprint §5.5) ──────────────────


class TestPageStateLeakPath:
    def test_password_input_value_not_in_to_label(self):
        """End-to-end: password value never appears in LLM-visible label,
        even when no redactor is injected (defense-in-depth)."""
        from src.core.browser_manager import InteractiveElement

        elem = InteractiveElement(
            index=1,
            tag="input",
            element_type="password",
            text="Password",
            name="login_password",
            aria_label=None,
            href=None,
            value="ShouldNeverAppear123",
            is_visible=True,
            selector="input",
        )
        label = elem.to_label()
        assert "ShouldNeverAppear123" not in label
        assert "REDACTED" in label

    def test_password_input_value_not_in_to_label_with_redactor(self):
        """Same but with redactor injected."""
        from src.core.browser_manager import InteractiveElement

        elem = InteractiveElement(
            index=1,
            tag="input",
            element_type="password",
            text="Password",
            name="login_password",
            aria_label=None,
            href=None,
            value="ShouldNeverAppear123",
            is_visible=True,
            selector="input",
        )
        label = elem.to_label(redactor=SecretRedactor())
        assert "ShouldNeverAppear123" not in label
        assert "REDACTED" in label

    def test_sensitive_name_pattern_redacts_value(self):
        """An input with name='api_key' (but type='text') still redacts value."""
        from src.core.browser_manager import InteractiveElement

        elem = InteractiveElement(
            index=2,
            tag="input",
            element_type="text",
            text="API Key",
            name="api_key",
            aria_label=None,
            href=None,
            value="sk-real-secret-value-here",
            is_visible=True,
            selector="input",
        )
        label = elem.to_label()
        assert "sk-real-secret-value-here" not in label
        assert "REDACTED" in label

    def test_normal_email_value_preserved(self):
        """A normal email input value passes through unchanged (no redactor)."""
        from src.core.browser_manager import InteractiveElement

        elem = InteractiveElement(
            index=3,
            tag="input",
            element_type="text",
            text="Email",
            name="email",
            aria_label=None,
            href=None,
            value="kevin@example.com",
            is_visible=True,
            selector="input",
        )
        label = elem.to_label()
        assert "kevin@example.com" in label

    def test_normal_value_with_secret_substring_scrubbed_with_redactor(self):
        """When redactor is injected, even non-sensitive fields lose secret-shaped
        substrings (defense-in-depth Layer 2)."""
        from src.core.browser_manager import InteractiveElement

        elem = InteractiveElement(
            index=4,
            tag="textarea",
            element_type=None,
            text="Note",
            name="note",
            aria_label=None,
            href=None,
            value="My token is ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789 do not share",
            is_visible=True,
            selector="textarea",
        )
        label = elem.to_label(redactor=SecretRedactor())
        assert "ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789" not in label
        assert "REDACTED" in label
