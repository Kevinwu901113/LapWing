# Phase 8B-3 Acceptance (Hardened)

**Date:** 2026-05-05
**Status:** Hardened — all 10 audit sections verified, 4,821 tests pass

Phase 8B-3: Trust Root Operator Tools for local metadata management.

## Deliverables

| File | Status |
|---|---|
| `src/tools/capability_tools.py` | Modified — added trust root tool schemas, executors, registration |
| `src/core/runtime_profiles.py` | Modified — added CAPABILITY_TRUST_OPERATOR_PROFILE |
| `src/config/settings.py` | Modified — added trust_root_tools_enabled to CapabilitiesConfig + _ENV_MAP |
| `config/settings.py` | Modified — exported CAPABILITIES_TRUST_ROOT_TOOLS_ENABLED |
| `config.toml` | Modified — added trust_root_tools_enabled = false |
| `src/app/container.py` | Modified — wired TrustRootStore + trust root tools |
| `tests/capabilities/test_phase8b_trust_root_tools.py` | Created (registration + behaviour tests) |
| `tests/capabilities/test_phase8b_trust_root_operator_profile.py` | Created (profile gating tests) |
| `tests/capabilities/test_phase8b_trust_root_tools_safety.py` | Created (safety constraint tests) |
| `tests/capabilities/test_phase0_regression.py` | Modified — added capability_trust_operator to expected set |
| `docs/capability_phase8b_trust_root_tools.md` | Created |
| `docs/capability_phase8b_3_acceptance.md` | Created (this file) |

## Test results

| Suite | Count | Status |
|---|---|---|
| Phase 8B-3 (trust root tools) | 40 | all pass |
| Phase 8B-3 (operator profile) | 23 | all pass |
| Phase 8B-3 (safety constraints) | 31 | all pass |
| Phase 8B-2 (trust root store + policy) | 114 | all pass |
| Phase 8B-1 (signature metadata + verifier stub) | 169 | all pass |
| Phase 8B-0 (signature/trust model invariants) | 75 | all pass |
| Phase 8A-1 (provenance) | 228 | all pass |
| Phase 7 (quarantine, import, activation) | 444 | all pass |
| Agent tests | 544 | all pass |
| Core (ToolDispatcher, RuntimeProfiles, StateView) | 128 | all pass |
| Skills + logging | 96 | all pass |
| **Total** | **4,821** | **all pass, 11 skipped (1 pre-existing)** |

## 1. Feature Flag Matrix

| Check | Result |
|---|---|
| `trust_root_tools_enabled` defaults to `false` in CapabilitiesConfig | [x] |
| `trust_root_tools_enabled = false` in config.toml | [x] |
| `CAPABILITIES_TRUST_ROOT_TOOLS_ENABLED` exported from `config/settings.py` | [x] |
| Tools absent when `capabilities.enabled = false` | [x] |
| Tools registered only when both `capabilities.enabled` and `trust_root_tools_enabled` are true | [x] |
| `trust_root_tools_enabled` grants no permissions by itself | [x] |
| Container wiring at correct indentation (sibling of EXTERNAL_IMPORT, not nested) | [x] |

## 2. Permission Matrix

| Profile | trust root tools granted? |
|---|---|
| capability_trust_operator | yes (only) |
| standard | no |
| chat_shell | no |
| inner_tick | no |
| local_execution | no |
| task_execution | no |
| identity_operator | no |
| browser_operator | no |
| capability_lifecycle_operator | no |
| capability_curator_operator | no |
| capability_import_operator | no |
| agent_candidate_operator | no |
| All other profiles (20 total verified programmatically) | no |

- [x] `CAPABILITY_TRUST_OPERATOR_PROFILE` in `_PROFILES` dict, retrievable via `get_runtime_profile`
- [x] All 5 tools tagged `capability="capability_trust_operator"`
- [x] All 5 tools checked: list_capability_trust_roots, view_capability_trust_root, add_capability_trust_root, disable_capability_trust_root, revoke_capability_trust_root

## 3. Tool Behavior

### Registration
- [x] Exactly 5 trust root tools registered
- [x] `list_capability_trust_roots` — low risk
- [x] `view_capability_trust_root` — low risk
- [x] `add_capability_trust_root` — medium risk
- [x] `disable_capability_trust_root` — medium risk
- [x] `revoke_capability_trust_root` — high risk
- [x] None store skips registration (no-op)
- [x] Forbidden tools absent: verify/trust/mark/fetch/import_remote/run

### list_capability_trust_roots
- [x] Returns compact summaries, secret-free
- [x] Empty store returns empty array
- [x] Deterministic ordering (sorted by filename)
- [x] Filters by status (active/disabled/revoked)
- [x] Filters by scope
- [x] Respects limit (default 50, max 200)
- [x] include_expired default true
- [x] exclude_expired works
- [x] is_active respects both status AND expiry

### view_capability_trust_root
- [x] Returns full metadata including is_active
- [x] Missing root returns not_found
- [x] Empty id returns error
- [x] Secret-containing files rejected (stripped by get_trust_root)

### add_capability_trust_root
- [x] Creates valid trust root (status=active)
- [x] Supports optional fields (owner, scope, expires_at, metadata)
- [x] Rejects duplicate trust_root_id
- [x] Rejects missing required fields
- [x] Rejects path traversal trust_root_id
- [x] Status defaults to active

### disable_capability_trust_root
- [x] Changes status to disabled
- [x] Reason stored in metadata
- [x] Revoked root returns already_revoked
- [x] Missing root returns not_found
- [x] Does not delete file

### revoke_capability_trust_root
- [x] Changes status to revoked
- [x] Disabled root also works
- [x] Missing root returns not_found
- [x] Reason required (validated)
- [x] Reason stored in metadata under `revoked_reason` key
- [x] Does not delete file

### Edge cases
- [x] Corrupt root skipped in list
- [x] Corrupt root returns not_found for view

## 4. Secret Rejection

### Value-based patterns (enforced at add time)
- [x] PEM private key blocks: `BEGIN PRIVATE KEY`, `BEGIN RSA PRIVATE KEY`, `BEGIN EC PRIVATE KEY`, `BEGIN OPENSSH PRIVATE KEY`
- [x] API key patterns: `sk-proj-...`, `sk_...`, `Bearer ...`
- [x] Secret in public_key_fingerprint field rejected

### Field-name patterns (enforced at read time)
- [x] `_validate_no_secrets` checks top-level dict keys against 17 secret field name patterns
- [x] `get_trust_root` strips secret-containing files on read
- [x] Field-name rejection is documented as read-time only — not reachable via add tool since `CapabilityTrustRoot.to_dict()` only outputs known fields
- [x] Metadata dict is exempt from field-name scanning (Phase 8B-2 design)

## 5. Trust Non-Elevation

- [x] No `signature_status=verified` in any tool output
- [x] No `trusted_signed` in any tool output
- [x] Tools do not modify capability provenance files (provenance.json unchanged by disable/revoke/add)
- [x] No `run_capability` in tool names or executors
- [x] No trust elevation path through any tool

## 6. Disabled / Revoked / Expired Semantics

- [x] `is_trust_root_active()` returns True only for status="active" AND not expired AND parseable expiry
- [x] `is_trust_root_active()` returns False for disabled roots
- [x] `is_trust_root_active()` returns False for revoked roots
- [x] `is_trust_root_active()` returns False for expired active roots
- [x] `_trust_root_compact_summary` uses `store.is_trust_root_active()` (not raw status check)
- [x] `as_verifier_dict()` returns ALL roots (active/disabled/revoked/expired) for verifier stub decision-making
- [x] disable: active→disabled (OK), revoked→already_revoked (rejected)
- [x] revoke: active→revoked (OK), disabled→revoked (OK)

## 7. No Crypto / Network Audit

### Crypto
- [x] No `cryptography` import in trust_roots.py
- [x] No `cryptography` import in trust root executors
- [x] No `import rsa` in trust root files
- [x] No `from Crypto` in trust root files
- [x] No real cryptographic verification
- [x] No private key storage

### Network
- [x] No `requests` / `httpx` / `urllib` in trust root tools
- [x] No remote registry
- [x] No `fetch_trust_root` tool
- [x] No `import_remote_trust_root` tool

## 8. Runtime Import Audit

Only allowed imports:
- [x] `src/tools/capability_tools.py` — trust root tools
- [x] `src/app/container.py` — wiring

No direct imports from:
- [x] Brain
- [x] TaskRuntime
- [x] StateViewBuilder
- [x] SkillExecutor
- [x] ToolDispatcher
- [x] agent modules

## 9. Regression

- [x] All Phase 8B-2 tests pass (114)
- [x] All Phase 8B-1 tests pass (169)
- [x] All Phase 8B-0 tests pass (75)
- [x] All Phase 8A tests pass (228)
- [x] All Phase 7 tests pass (444)
- [x] All agent tests pass (544)
- [x] All core tests pass (128)
- [x] All skills/logging pass (96)
- [x] Phase 0 regression: capability_trust_operator in expected profiles set
- [x] Full suite: 4,821 passed, 0 failed, 11 skipped (1 pre-existing)

## 10. Documentation

- [x] `docs/capability_phase8b_trust_root_tools.md` — design doc with tool specs, feature flag, permission model, container wiring, test structure
- [x] `docs/capability_phase8b_3_acceptance.md` — this hardened acceptance doc
- [x] `docs/capability_acceptance_index.md` — updated with Phase 8B-3 entry and test counts

## Known Issues

- **Field-name-based secret rejection not reachable via add tool**: `_validate_no_secrets` checks top-level dict keys for secret field names (e.g., `signing_key`, `key_material`), but `CapabilityTrustRoot.to_dict()` only outputs known fields. Field-name rejection is exercised at read time (`get_trust_root`), not write time. This is by design — `_validate_no_secrets` on create catches value-based patterns (PEM, API keys) which are the primary injection vector through tools.
- **Metadata dict is exempt from secret scanning**: This is the Phase 8B-2 design. Metadata can contain arbitrary keys without triggering field-name rejection.

## Rollback Notes

To roll back Phase 8B-3:
1. Remove trust root tools block from `src/tools/capability_tools.py`
2. Remove `CAPABILITY_TRUST_OPERATOR_PROFILE` from `src/core/runtime_profiles.py`
3. Remove `trust_root_tools_enabled` from `src/config/settings.py` CapabilitiesConfig + _ENV_MAP
4. Remove `CAPABILITIES_TRUST_ROOT_TOOLS_ENABLED` from `config/settings.py`
5. Remove `trust_root_tools_enabled` from `config.toml`
6. Remove trust root wiring block from `src/app/container.py`
7. Remove `CAPABILITIES_TRUST_ROOT_TOOLS_ENABLED` from container.py import
8. Remove `capability_trust_operator` from `test_phase0_regression.py` expected set
9. Delete the three test files and two doc files
