# Post-Phase-8 Trust / Promotion Audit Acceptance

**Date**: 2026-05-05
**Branch**: `fix/snooker-session-rootcause-20260504`
**Type**: Audit-only PR — no new features, no new tools, no runtime behavior changes

---

## Tests Run

### New E2E tests: `tests/capabilities/test_phase8_e2e_trusted_promotion.py`

**22 tests, 22 passed, 0 failed, 0 skipped**

#### Flow A: Low-risk reviewed provenance stable promotion (5 tests)
- `test_full_lifecycle_external_to_stable` — PASSED: External package → quarantine → testing → stable via trust gate. Verified source_type, trust_level, integrity at each stage.
- `test_provenance_immutable_through_promotion` — PASSED: Byte-for-byte provenance.json equality before and after promotion.
- `test_no_script_execution_during_promotion` — PASSED: Scripts copied but never executed.
- `test_index_reflects_promoted_capability` — PASSED: store.get() returns maturity=stable.
- `test_quarantine_unchanged_after_testing_to_stable` — PASSED: Quarantine copy stays quarantined/draft.

#### Flow B: Untrusted or mismatched integrity blocks stable (4 tests)
- `test_untrusted_provenance_blocks_stable` — PASSED: Denied, manifest+provenance unchanged byte-for-byte.
- `test_integrity_mismatch_blocks_stable` — PASSED: Denied, manifest+provenance unchanged byte-for-byte.
- `test_no_version_snapshot_on_denial` — PASSED: No new snapshot directory created.
- `test_index_unchanged_on_denial` — PASSED: Index maturity remains "testing".

#### Flow C: High-risk reviewed-only blocks stable (3 tests)
- `test_high_risk_reviewed_blocks_stable` — PASSED: Denied with reviewed trust for high risk.
- `test_high_risk_trusted_local_with_approval_allows` — PASSED: Allowed with trusted_local + approval.
- `test_high_risk_no_approval_blocks_before_trust_gate` — PASSED: Blocked by CapabilityPolicy before trust gate.

#### Flow D: Flag-off compatibility (3 tests)
- `test_flag_off_untrusted_provenance_promotes` — PASSED: Old behavior unchanged.
- `test_flag_off_missing_provenance_promotes` — PASSED: Old behavior unchanged.
- `test_flag_off_integrity_mismatch_promotes` — PASSED: Old behavior (no gating).

#### Flow E: Legacy/manual low-risk missing provenance (4 tests)
- `test_legacy_manual_low_risk_no_provenance_warns_allows` — PASSED: Warns + allows (legacy exception).
- `test_legacy_low_risk_no_provenance_stable_maturity_set` — PASSED: Actually reaches stable.
- `test_legacy_medium_risk_no_provenance_blocks` — PASSED: Denied for medium risk.
- `test_legacy_high_risk_no_provenance_blocks` — PASSED: Denied for high risk.

#### Cross-flow invariants (3 tests)
- `test_provenance_never_contains_raw_system_paths` — PASSED: No `/home/`, `/tmp/` or raw source path leaks.
- `test_no_runtime_behavior_changes` — PASSED: Trust gate not evaluated for non-stable transitions.
- `test_trust_policy_none_with_flag_true_no_effect` — PASSED: Gate skipped when trust_policy=None.

---

## Trust Gate Behavior Summary

| Provenance State | Low Risk | Medium Risk | High Risk |
|-----------------|----------|-------------|-----------|
| No provenance | Warn, allow | Deny | Deny |
| trust=untrusted, integrity=verified | Deny | Deny | Deny |
| trust=reviewed, integrity=verified | Warn, allow | Warn, allow | Deny |
| trust=trusted_local, integrity=verified | Allow | Allow | Allow (with approval) |
| trust=reviewed, integrity=mismatch | Deny | Deny | Deny |
| trust=reviewed, signature=invalid | Deny | Deny | Deny |

All findings match the documented semantics in `docs/capability_post_phase8_trust_promotion_audit.md`.

---

## No-Mutation-On-Denial Proof

All denied promotions were verified to leave state unchanged:

- **Manifest**: Byte-for-byte comparison before and after denied transition — identical in all Flow B and Flow C denial tests.
- **Provenance**: Byte-for-byte comparison before and after denied transition — identical in all denial tests.
- **Index**: Index search returns same maturity before and after denial.
- **Version snapshots**: `result.version_snapshot_id` is None on denial. No new directories created in `versions/`.
- **Policy decisions**: `result.policy_decisions` includes the trust gate decision with `allowed=False` for trust gate denials.

---

## Provenance Immutability Proof

- `test_provenance_immutable_through_promotion`: Byte-for-byte comparison confirms provenance.json is never modified during testing → stable promotion.
- `test_provenance_never_contains_raw_system_paths`: Verified no `/home/`, `/tmp/`, or raw source path leaks in any provenance file across full lifecycle.
- The lifecycle manager does not read or write provenance — only reads it for trust gate evaluation. Provenance is only written by `import_capability_package()` and `apply_quarantine_activation()`.

---

## Runtime Import Audit

Non-capability files importing from `src.capabilities`:

| File | Status |
|------|--------|
| `src/app/container.py` | ALLOWED — dependency injection wiring |
| `src/tools/capability_tools.py` | ALLOWED — operator-invoked tool implementations |

No other files outside `src/capabilities/` import capability modules. Audit passes.

---

## Forbidden Tool Audit

| Pattern | Result |
|---------|--------|
| `run_capability` | NOT FOUND (only in docstrings stating "does NOT implement") |
| `execute_capability` | NOT FOUND |
| `run_quarantined_capability` | NOT FOUND |
| `remote_registry` | NOT FOUND |
| `auto_stable` / `auto_promote_stable` | NOT FOUND |
| Signature verification claiming `verified` | NOT FOUND (verifier stub explicitly never returns verified) |

Audit passes. No forbidden tools exist.

---

## No-Crypto / No-Network Audit

| Check | Result |
|-------|--------|
| Cryptographic imports (`cryptography`, `rsa`, `ecdsa`, `OpenSSL`, `ssl`) | NONE |
| Network imports (`requests`, `urllib`, `socket`, `http`) | NONE |
| URL-based import paths | NONE (only local filesystem paths accepted) |
| Git import | NONE |
| `verify_signature_stub()` returns `verified` | NEVER (best case: `present_unverified`) |
| Trust roots contain key material | NO (metadata-only, key hash stored not material) |
| API key patterns in code | NONE (only in rejection/detection logic) |

Audit passes.

---

## Files Changed

```
M  tests/capabilities/test_phase8_e2e_trusted_promotion.py  (new file, ~680 lines)
M  docs/capability_post_phase8_trust_promotion_audit.md      (new file)
M  docs/capability_post_phase8_audit_acceptance.md            (new file, this file)
```

No source files modified. No tools added. No flags added. No behavior changes.

---

## Known Issues

1. **High-risk quarantine activation blocked**: Phase 7D-B `apply_quarantine_activation()` blocks all high-risk capabilities regardless of provenance. Flow C tests work around this by creating testing capabilities directly. This is a pre-existing limitation, not introduced by this PR.

2. **No integrity re-verification on promotion**: The trust gate checks `integrity_status` from provenance.json but does not re-compute the tree hash to verify the stored value. This is a known design limitation documented in the security audit.

3. **`trusted_signed` never achievable**: The signature verifier stub never returns `verified`, so `trusted_signed` is aspirational. No capability can legitimately achieve this trust level.

4. **Flag defaults to off**: `trust_gate_enabled` defaults to `False` in `CapabilityLifecycleManager.__init__`. Until explicitly enabled, the trust gate provides no protection.

---

## Rollback Notes

To roll back this PR:
- Delete `tests/capabilities/test_phase8_e2e_trusted_promotion.py`
- Delete `docs/capability_post_phase8_trust_promotion_audit.md`
- Delete `docs/capability_post_phase8_audit_acceptance.md`

No source files were modified, so no code rollback is needed.

---

## Acceptance Criteria Verification

| Criterion | Status |
|-----------|--------|
| Full external-to-stable trusted lifecycle is E2E tested | PASSED — Flow A covers complete path |
| Stable promotion trust gate behavior is documented and verified | PASSED — Audit doc + 22 E2E tests |
| Denied promotion remains atomic | PASSED — Byte-for-byte verification in Flow B/C |
| Provenance remains immutable through promotion | PASSED — Byte-for-byte verification |
| Existing regression suite passes | PASSED — 3072 passed, 0 failed |

## Regression Test Results

Full regression suite: **3072 passed, 0 failed, 0 skipped** (407s runtime)

Test suites executed:
- `tests/capabilities/` — all capability lifecycle tests
- `tests/agents/` — all agent tests
- `tests/core/test_tool_dispatcher.py` — tool dispatching
- `tests/core/test_runtime_profiles_exclusion.py` — runtime profiles
- `tests/core/test_state_view*` — StateView tests
- `tests/skills/` — skill system tests
- `tests/logging/` — logging and mutation log tests

No regressions introduced.
