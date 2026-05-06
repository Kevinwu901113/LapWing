# Phase 8C-1 Acceptance

Stable Promotion Trust Gate Wiring — feature-gated testing → stable trust checks.

**Date:** 2026-05-05
**Status:** Accepted (Hardened)

## Acceptance Criteria

- [x] Feature flag `capabilities.stable_promotion_trust_gate_enabled` defaults to `false`
- [x] When flag is `false`, existing testing → stable lifecycle behavior unchanged
- [x] When flag is `true`, testing → stable checks provenance/integrity/trust
- [x] Non-stable transitions are never affected regardless of flag state
- [x] Denied trust gate leaves manifest/index/snapshot/mutation log untouched
- [x] Successful stable promotion follows existing lifecycle mutation path
- [x] No `run_capability` exists
- [x] No crypto/network behavior added
- [x] No retrieval/runtime behavior change
- [x] Import audit: only `capability_tools.py` and `container.py` import from capabilities

## Test Results

| Suite | Count | Status |
|---|---|---|
| Capability tests | 2,281 passed | PASS |
| Agent + core/tools/skills | 1,923 passed, 1 skipped | PASS |
| **Total** | **4,204 passed, 1 skipped** | **PASS** |

### Phase 8C Test Breakdown

| File | Count | Type |
|---|---|---|
| `test_phase8c_stable_promotion_gate.py` | 38 | Unit: flag, risk-specific gating, no-mutation, success |
| `test_phase8c_stable_promotion_gate_integration.py` | 13 | Integration: E2E flows, retry after fix, result shape |
| `test_phase8c_stable_promotion_gate_regression.py` | 19 | Regression: non-stable, legacy, evaluator, policy, store |
| `test_phase8c_stable_promotion_trust_gate_invariants.py` | 54 | Invariants: feature gate, state distinctions, constraints |
| **Phase 8C total** | **124** | **All passed** |

### Predecessor Phase Regression

| Phase | Tests | Status |
|---|---|---|
| Phase 8A (provenance) | 228 | PASS |
| Phase 8B-0 (signature trust model) | 75 | PASS |
| Phase 8B-1 (signature metadata) | 169 | PASS |
| Phase 8B-2 (trust root store) | 114 | PASS |
| Phase 8B-3 (trust root tools) | 94 | PASS |
| Phase 8C-0 (trust gate invariants) | 54 | PASS (updated for wiring) |
| Phase 7 (all sub-phases) | 444 | PASS |
| Pre-Phase-7 capability tests | ~1,100 | PASS |

## Files Changed

| File | Change |
|---|---|
| `config.toml` | Added `stable_promotion_trust_gate_enabled = false` |
| `src/config/settings.py` | Added `CapabilitiesConfig.stable_promotion_trust_gate_enabled` field + env map |
| `config/settings.py` | Added backward-compat constant |
| `src/capabilities/provenance.py` | Extended `can_promote_to_stable` with `risk_level`, `approval` params |
| `src/capabilities/lifecycle.py` | Added `trust_policy`/`trust_gate_enabled` params; wired trust gate |
| `src/app/container.py` | Passes `CapabilityTrustPolicy` to `LifecycleManager` when flag enabled |
| `docs/capability_stable_promotion_trust_gate.md` | Updated status to Phase 8C-1 implemented |
| `docs/capability_phase8c_stable_promotion_gate.md` | Phase 8C-1 design doc |
| `docs/capability_phase8c_1_acceptance.md` | This document |
| `docs/capability_acceptance_index.md` | Updated with Phase 8C entries |
| `tests/capabilities/test_phase8c_stable_promotion_gate.py` | 38 unit tests |
| `tests/capabilities/test_phase8c_stable_promotion_gate_integration.py` | 13 integration tests |
| `tests/capabilities/test_phase8c_stable_promotion_gate_regression.py` | 19 regression tests |
| `tests/capabilities/test_phase8c_stable_promotion_trust_gate_invariants.py` | 54 invariant tests |
| `tests/capabilities/test_phase8a_trust_policy.py` | Updated for risk-specific logic |
| `tests/capabilities/test_phase8a_hardening.py` | Updated for wired-behind-flag semantics |

## Feature Flag Matrix

| Flag State | Transition | Provenance | Trust Level | Result |
|---|---|---|---|---|
| `false` | testing → stable | None | N/A | Succeeds |
| `false` | testing → stable | Untrusted | N/A | Succeeds |
| `false` | testing → stable | Integrity mismatch | N/A | Succeeds |
| `false` | testing → stable | Invalid signature | N/A | Succeeds |
| `false` | draft → testing | Any | Any | Succeeds (not gated) |
| `false` | stable → broken | Any | Any | Succeeds (not gated) |
| `true` | testing → stable | None (low risk) | N/A | Warn, allows |
| `true` | testing → stable | None (medium risk) | N/A | Blocks |
| `true` | testing → stable | None (high risk) | N/A | Blocks |
| `true` | testing → stable | reviewed (low risk) | reviewed | Allows (warn) |
| `true` | testing → stable | reviewed (medium risk) | reviewed | Allows (warn) |
| `true` | testing → stable | reviewed (high risk) | reviewed | Blocks |
| `true` | testing → stable | trusted_local (any risk) | trusted_local | Allows |
| `true` | testing → stable | trusted_signed (any risk) | trusted_signed | Allows |
| `true` | testing → stable | Integrity mismatch | Any | Blocks |
| `true` | testing → stable | Invalid signature | Any | Blocks |
| `true` | draft → testing | Any | Any | Succeeds (not gated) |
| `true` | stable → broken | Any | Any | Succeeds (not gated) |

## Trust Policy Decision Matrix (flag=true)

### Low Risk

| Provenance | Integrity | Trust Level | Decision |
|---|---|---|---|
| None (legacy) | N/A | N/A | Warn, allow |
| Present | verified | reviewed | Warn, allow |
| Present | verified | trusted_local | Allow |
| Present | verified | trusted_signed | Allow |
| Present | mismatch | Any | Deny |
| Present | verified | untrusted | Deny |

### Medium Risk

| Provenance | Integrity | Trust Level | Decision |
|---|---|---|---|
| None | N/A | N/A | Deny |
| Present | verified | reviewed | Warn, allow |
| Present | verified | trusted_local | Allow |
| Present | verified | trusted_signed | Allow |
| Present | mismatch | Any | Deny |
| Present | verified | untrusted | Deny |

### High Risk

| Provenance | Integrity | Trust Level | Approval | Decision |
|---|---|---|---|---|
| None | N/A | N/A | Any | Deny |
| Present | verified | reviewed | Any | Deny |
| Present | verified | trusted_local | No | Blocked by policy (pre-gate) |
| Present | verified | trusted_local | Yes | Allow |
| Present | verified | trusted_signed | Yes | Allow |
| Present | mismatch | Any | Any | Deny |
| Present | verified | untrusted | Any | Deny |

Note: High risk without owner approval is blocked by `CapabilityPolicy.validate_promote` (step 3) before the trust gate runs. This is correct layering — policy check precedes trust check.

## No-Mutation-on-Denial Proof

Verified byte-for-byte with file hashes before and after denied transition:

- **manifest.json** — unchanged (maturity stays `testing`)
- **CAPABILITY.md** — unchanged
- **provenance.json** — unchanged (never mutated, even on success)
- **No version snapshot** written (snapshot occurs after trust gate)
- **No index update** (index refresh occurs after snapshot)
- **`TransitionResult.applied`** = `False`
- **`policy_decisions`** includes trust gate decision with `source: "CapabilityTrustPolicy"`

Mutation order in `apply_transition`:
1. Policy check (`validate_promote`) — step 3
2. **Trust gate** — inserted between steps 3 and 4
3. Snapshot write — step 4
4. Manifest update — step 5
5. Index refresh — step 6

Denial at trust gate returns before steps 3-5, ensuring atomicity.

## Successful Promotion Behavior

When trust gate passes (flag=true, all checks satisfied):
- Manifest maturity: `testing` → `stable`
- Status: stays `active`
- Version snapshot: written with new maturity
- Provenance: **never mutated** (read-only during promotion)
- `TransitionResult.applied` = `True`
- `policy_decisions` includes both policy decision and trust gate decision

## Hard Constraints Verified

| Constraint | Verification |
|---|---|
| No `run_capability` | Grep: no match in codebase |
| No crypto operations | Source scan: no `hashlib`, `hmac`, `sign`, `verify` in Phase 8C paths |
| No network calls | Source scan: no `http`, `url`, `fetch`, `socket` in Phase 8C paths |
| No subprocess/exec | Source scan: no `subprocess`, `exec`, `eval` in Phase 8C paths |
| No Brain import | Grep: Brain does not import from capabilities |
| No TaskRuntime import | Grep: TaskRuntime does not import from capabilities |
| No StateView import | Grep: StateView does not import from capabilities |
| No ToolDispatcher import | Grep: ToolDispatcher does not import from capabilities |
| No SkillExecutor import | Grep: SkillExecutor does not import from capabilities |
| No agent module import | Agent modules do not import from capabilities |
| No retrieval behavior change | CapabilityRetriever unchanged by Phase 8C |
| No RuntimeProfile bypass | Promotion does not touch profiles |
| No permission grant | Promotion does not register tools or grant perms |

## Import Audit

```
src/capabilities/  importers:
  src/tools/capability_tools.py   ← allowed (capability tools surface)
  src/app/container.py             ← allowed (DI wiring)

NOT imported by:
  src/core/brain.py
  src/core/task_runtime.py
  src/core/state_view.py
  src/core/tool_dispatcher.py
  src/core/skill_executor.py
  src/agents/*
```

## State Distinctions Maintained (Phase 8C-0)

All 10 distinctions verified by invariant tests:

- [x] `active/testing != stable` — testing maturity does not grant stable privileges
- [x] `reviewed != trusted_signed` — human review is not cryptographic trust
- [x] `trusted_local != trusted_signed` — local operator trust is not remote signer trust
- [x] `present_unverified != verified` — having a signature is not verification
- [x] Trust root active != signature verified — active root doesn't mean capability was signed
- [x] Approval != trust — owner approval is policy, not trust assessment
- [x] Eval pass != trust — passing evaluator does not establish provenance trust
- [x] Trust != permission — trust level does not grant tool permissions
- [x] Stable != executable — stable maturity is a lifecycle label
- [x] Stable != run_capability — no run_capability function exists

## Known Issues

None.

## Rollback Notes

- Set `capabilities.stable_promotion_trust_gate_enabled = false` in `config.toml` to disable.
- When disabled, all lifecycle behavior is identical to pre-Phase 8C-1.
- The `trust_policy` parameter on `LifecycleManager` is optional — passes `None` by default.
- No migration required; no database schema changes.

## Conclusion

Phase 8C-1 complete and hardened. Trust gate wired behind feature flag (default off), risk-specific gating implemented, all 124 Phase 8C tests pass, full suite 4,204 passed with 0 failures, no regressions, all hard constraints maintained.
