"""Phase 5A: TraceSummary model tests."""

from __future__ import annotations

import pytest
from src.capabilities.trace_summary import TraceSummary


# ── from_dict: valid input ──────────────────────────────────────────────


def test_from_dict_valid_all_fields():
    d = {
        "trace_id": "trace-001",
        "user_request": "Fix the login bug in auth.py",
        "final_result": "Login bug fixed",
        "task_type": "bug-fix",
        "context": "Python project, Flask auth module",
        "tools_used": ["execute_shell", "read_file", "write_file"],
        "files_touched": ["src/auth.py", "tests/test_auth.py"],
        "commands_run": ["pytest tests/", "git diff"],
        "errors_seen": ["ImportError: missing module"],
        "failed_attempts": ["Tried patching import directly"],
        "successful_steps": ["Located the bug", "Applied fix", "Ran tests"],
        "verification": ["All tests pass"],
        "user_feedback": "Great, thanks!",
        "existing_capability_id": None,
        "created_at": "2026-05-01T10:00:00Z",
        "metadata": {"repetition_count": 3},
    }
    ts = TraceSummary.from_dict(d)
    assert ts.trace_id == "trace-001"
    assert ts.user_request == "Fix the login bug in auth.py"
    assert ts.final_result == "Login bug fixed"
    assert ts.task_type == "bug-fix"
    assert ts.tools_used == ["execute_shell", "read_file", "write_file"]
    assert ts.files_touched == ["src/auth.py", "tests/test_auth.py"]
    assert ts.commands_run == ["pytest tests/", "git diff"]
    assert ts.errors_seen == ["ImportError: missing module"]
    assert ts.failed_attempts == ["Tried patching import directly"]
    assert ts.successful_steps == ["Located the bug", "Applied fix", "Ran tests"]
    assert ts.verification == ["All tests pass"]
    assert ts.user_feedback == "Great, thanks!"
    assert ts.existing_capability_id is None
    assert ts.created_at == "2026-05-01T10:00:00Z"
    assert ts.metadata == {"repetition_count": 3}


def test_from_dict_minimal():
    d = {"user_request": "Do a thing"}
    ts = TraceSummary.from_dict(d)
    assert ts.user_request == "Do a thing"
    assert ts.trace_id is None
    assert ts.tools_used == []
    assert ts.files_touched == []
    assert ts.metadata == {}
    assert ts.created_at  # auto-generated


def test_from_dict_missing_user_request_raises():
    with pytest.raises(ValueError, match="user_request"):
        TraceSummary.from_dict({"tools_used": ["a"]})


def test_from_dict_empty_user_request_raises():
    with pytest.raises(ValueError, match="user_request"):
        TraceSummary.from_dict({"user_request": "   "})


def test_from_dict_not_a_dict_raises():
    with pytest.raises(ValueError):
        TraceSummary.from_dict(None)


# ── from_dict: CoT stripping ────────────────────────────────────────────


def test_from_dict_drops_cot_key():
    d = {
        "user_request": "test",
        "_cot": "secret internal reasoning chain",
        "chain_of_thought": "more hidden stuff",
        "_internal": {"foo": "bar"},
    }
    ts = TraceSummary.from_dict(d)
    # CoT fields should not appear in any field.
    assert "_cot" not in ts.to_dict()
    d2 = ts.to_dict()
    for v in d2.values():
        if isinstance(v, str):
            assert "secret internal" not in v
            assert "hidden stuff" not in v


def test_from_dict_drops_reasoning_trace():
    d = {
        "user_request": "test",
        "_reasoning": "private",
        "_thinking": "private",
        "reasoning_trace": "private",
    }
    ts = TraceSummary.from_dict(d)
    assert ts.user_request == "test"


def test_from_dict_drops_prototype_pollution():
    d = {
        "user_request": "test",
        "__class__": "dangerous",
        "__init__": "dangerous",
        "__dict__": "dangerous",
    }
    ts = TraceSummary.from_dict(d)
    d2 = ts.to_dict()
    assert "__class__" not in d2
    assert "__init__" not in d2


# ── from_dict: list coercion ────────────────────────────────────────────


def test_from_dict_coerces_none_lists():
    ts = TraceSummary.from_dict({"user_request": "test"})
    assert ts.tools_used == []
    assert ts.files_touched == []
    assert ts.commands_run == []


def test_from_dict_coerces_non_list_values():
    d = {
        "user_request": "test",
        "tools_used": "just a string",
        "commands_run": None,
    }
    ts = TraceSummary.from_dict(d)
    assert isinstance(ts.tools_used, list)
    assert ts.commands_run == []


# ── sanitize: secrets redaction ─────────────────────────────────────────


def test_sanitize_redacts_sk_key():
    ts = TraceSummary.from_dict({
        "user_request": "Use API key sk-abcdefghijklmnopqrstuvwxyz123456 for auth",
    })
    sanitized = ts.sanitize()
    assert "sk-abcdefghij" not in sanitized.user_request
    assert "<REDACTED>" in sanitized.user_request


def test_sanitize_redacts_api_key_assignment():
    ts = TraceSummary.from_dict({
        "user_request": "Set API_KEY=my-secret-token and run",
    })
    sanitized = ts.sanitize()
    assert "my-secret-token" not in sanitized.user_request
    assert "<REDACTED>" in sanitized.user_request


def test_sanitize_redacts_authorization_header():
    ts = TraceSummary.from_dict({
        "user_request": "Call with Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc.def",
    })
    sanitized = ts.sanitize()
    assert "eyJhbGci" not in sanitized.user_request
    assert "<REDACTED>" in sanitized.user_request


def test_sanitize_redacts_password():
    ts = TraceSummary.from_dict({
        "user_request": "Login with password=superSecret123!",
    })
    sanitized = ts.sanitize()
    assert "superSecret123" not in sanitized.user_request
    assert "<REDACTED>" in sanitized.user_request


def test_sanitize_redacts_private_key_block():
    ts = TraceSummary.from_dict({
        "user_request": "My key:\n-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkq...\n-----END PRIVATE KEY-----",
    })
    sanitized = ts.sanitize()
    assert "MIIEvQIBADAN" not in sanitized.user_request
    assert "<REDACTED>" in sanitized.user_request


def test_sanitize_redacts_rsa_private_key():
    ts = TraceSummary.from_dict({
        "user_request": "-----BEGIN RSA PRIVATE KEY-----\nabc123\n-----END RSA PRIVATE KEY-----",
    })
    sanitized = ts.sanitize()
    assert "abc123" not in sanitized.user_request
    assert "<REDACTED>" in sanitized.user_request


def test_sanitize_preserves_non_sensitive_fields():
    ts = TraceSummary.from_dict({
        "user_request": "Run pytest on the test suite",
        "tools_used": ["execute_shell"],
    })
    sanitized = ts.sanitize()
    assert sanitized.user_request == "Run pytest on the test suite"
    assert sanitized.tools_used == ["execute_shell"]


def test_sanitize_handles_none_fields():
    ts = TraceSummary.from_dict({"user_request": "test"})
    sanitized = ts.sanitize()
    assert sanitized.final_result is None
    assert sanitized.user_feedback is None


def test_sanitize_handles_empty_lists():
    ts = TraceSummary.from_dict({"user_request": "test"})
    sanitized = ts.sanitize()
    assert sanitized.commands_run == []
    assert sanitized.errors_seen == []


def test_sanitize_returns_new_instance():
    ts = TraceSummary.from_dict({"user_request": "original"})
    sanitized = ts.sanitize()
    assert sanitized is not ts
    assert id(sanitized) != id(ts)


# ── to_dict round-trip ──────────────────────────────────────────────────


def test_to_dict_from_dict_round_trip():
    d = {
        "trace_id": "trace-1",
        "user_request": "Deploy the app",
        "final_result": "Deployed",
        "task_type": "deploy",
        "context": "K8s cluster",
        "tools_used": ["execute_shell", "read_file"],
        "files_touched": ["k8s/deploy.yaml"],
        "commands_run": ["kubectl apply -f k8s/"],
        "errors_seen": [],
        "failed_attempts": [],
        "successful_steps": ["Applied k8s manifest"],
        "verification": ["Pods running"],
        "user_feedback": None,
        "existing_capability_id": None,
        "created_at": "2026-05-01T12:00:00Z",
        "metadata": {"env": "production"},
    }
    ts = TraceSummary.from_dict(d)
    out = ts.to_dict()
    ts2 = TraceSummary.from_dict(out)
    assert ts2.user_request == ts.user_request
    assert ts2.trace_id == ts.trace_id
    assert ts2.tools_used == ts.tools_used
    assert ts2.created_at == ts.created_at
    assert ts2.metadata == ts.metadata


# ── Truncation ──────────────────────────────────────────────────────────


def test_from_dict_truncates_long_strings():
    long_text = "x" * 60000
    d = {"user_request": long_text}
    ts = TraceSummary.from_dict(d)
    assert len(ts.user_request) < 60000
    assert "[truncated]" in ts.user_request
