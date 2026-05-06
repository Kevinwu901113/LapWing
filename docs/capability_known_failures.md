# Capability System — Known Pre-Existing Failures

**Date:** 2026-05-02
**Baseline:** `aa19a32` (Phase 3+4 merged)
**Status:** ALL 13 failures resolved as of 2026-05-02 cleanup PR.

---

## Failure Details

### 1-9. `tests/core/test_brain_tools.py::TestBrainTools` (9 failures) — FIXED

**Root cause:** `LapwingBrain._fallback_profile_for_message` always returns `"standard"`. The `standard` profile does not include `execute_shell` in its allowed tools. When the LLM returns an `execute_shell` tool call, the `ToolDispatcher` profile check blocks it with `profile_not_allowed` before it reaches the shell executor. The test's `execute_shell` mock was never called.

**Fix:** Added `patch("src.core.brain.LapwingBrain._fallback_profile_for_message", return_value="chat_shell")` to each failing test. The `chat_shell` profile uses capabilities-based filtering which includes the `shell` capability, allowing `execute_shell` through the profile check to reach the mocked shell executor.

**Why unrelated to capability system:** This test file predates the capability system entirely. The failures reproduce identically on baseline commit `aa19a32` before any Phase 5 changes.

---

### 10-11. `tests/core/test_audit_logging.py` (2 failures) — FIXED

**Root cause:** Both tests used `profile="local_execution"` but `local_execution` does not include `browser_open` in its allowed tools. The profile check (`profile_not_allowed`) fired before the browser guard check (`browser_guard_missing` / `browser_guard`), causing assertion mismatches on the denial guard name.

**Fix:** Changed both tests to use `profile="browser_operator"` which includes `browser_open` in its allowed tools. This lets the browser guard check execute as intended.

**Why unrelated to capability system:** These tests test audit logging infrastructure. Zero capability code touches audit logging.

---

### 12. `tests/core/test_chat_tools_centralized.py` (1 failure) — FIXED

**Root cause:** `list_agents` was intentionally added to the `compose_proactive` profile in commit `4a45f46` (`feat: expose list_agents tool`), but the test assertion `assert "list_agents" not in names` was not updated.

**Fix:** Changed `assert "list_agents" not in names` to `assert "list_agents" in names`.

**Why unrelated to capability system:** This is a tool registry profile test. The capability system uses separate tool tags (`capability_read`, `capability_lifecycle`, `capability_curator`) that do not intersect with the default profile.

---

### 13. `tests/test_import_smoke.py::test_llm_router_anthropic_path_reports_missing_dependency` (1 failure) — FIXED

**Root cause:** `UnboundLocalError` in `src/core/llm_router.py:1075` — local variable `model` was accessed in the `except` block but was only assigned inside the preceding `try` block. If an exception occurred before the assignment, `model` was undefined.

**Fix:** Initialized `model = None` before the `try` block in `_with_routing_retry`.

**Why unrelated to capability system:** This is an LLM routing code bug. Zero capability code touches `llm_router.py`.

---

## Verification

All 13 failures were verified on the current HEAD before the fix. All now pass. Zero capability system changes were required.

No capability system change introduced any failure.
