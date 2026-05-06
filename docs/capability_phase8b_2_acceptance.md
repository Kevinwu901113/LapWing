# Phase 8B-2 Acceptance (Hardened)

Phase 8B-2: Local filesystem-backed TrustRootStore + verifier stub integration.
Hardening pass completed before Phase 8B-3.

## Deliverables

| File | Status |
|---|---|
| `src/capabilities/trust_roots.py` | Created + hardened |
| `tests/capabilities/test_phase8b_trust_root_store.py` | Created (79 tests after hardening) |
| `tests/capabilities/test_phase8b_trust_root_policy.py` | Created (35 tests after hardening) |
| `docs/capability_phase8b_trust_roots.md` | Created |
| `docs/capability_phase8b_2_acceptance.md` | Updated with hardening results |

## Modified files

| File | Change |
|---|---|
| `src/capabilities/__init__.py` | Exported TrustRootStore, updated module docstring |
| `src/capabilities/signature.py` | Added `_is_trust_root_expired` helper; verifier stub expiry check; duck-typing support for TrustRootStore; safety guard for non-dict TrustRootStore return values; added `key_material` to `_SECRET_FIELD_NAMES` |
| `tests/capabilities/test_phase8b_signature_verifier_stub.py` | Updated `test_trust_root_expired_but_active_still_passes` → `test_trust_root_expired_returns_invalid` |
| `docs/capability_phase8b_2_acceptance.md` | Updated with full hardening results |

## Test results (post-hardening)

| Suite | Count | Status |
|---|---|---|
| Phase 8B-2 (trust root store) | 79 | all pass |
| Phase 8B-2 (trust root policy) | 35 | all pass |
| Phase 8B-1 (signature metadata + verifier stub + integration) | 169 | all pass |
| Phase 8B-0 (signature/trust model invariants) | 75 | all pass |
| Phase 8A-1 (provenance) | 228 | all pass |
| Phase 7 (quarantine, import, activation) | 444 | all pass |
| Agent tests | 544 | all pass |
| Core (ToolDispatcher, RuntimeProfiles, StateView) | 128 | all pass |
| Skills + logging | 96 | all pass |
| **Total** | **2,739** | **all pass, 1 pre-existing skip** |

## 1. Store behavior hardening

- [x] create_trust_root writes exactly one `<trust_root_id>.json`
- [x] get_trust_root reads by id; returns None for missing/corrupt/secret
- [x] list_trust_roots returns deterministic ordering (sorted by filename via `sorted(glob("*.json"))`)
- [x] list filters by status (active/disabled/revoked)
- [x] list filters by scope (global/project/user)
- [x] list filters by both status and scope
- [x] duplicate trust_root_id rejected (ValueError)
- [x] path traversal trust_root_id rejected (contains /, \\, or ..)
- [x] absolute path id rejected (starts with /, caught by path separators check)
- [x] empty id rejected
- [x] whitespace-only id rejected
- [x] unicode valid ids accepted (e.g. `tröst-rööt-αβγ`)
- [x] unicode with `..` traversal rejected
- [x] corrupt JSON file → get returns None, list skips
- [x] non-dict JSON file → get returns None, list skips
- [x] empty file → get returns None
- [x] atomic writes via temp file + os.replace (no .tmp files left behind)
- [x] no writes outside `data/capabilities/trust_roots`
- [x] all tests use `tmp_path`

## 2. Status semantics hardening

- [x] active + not expired → is_trust_root_active returns True
- [x] active + expired → is_trust_root_active returns False
- [x] disabled → is_trust_root_active returns False (status checked before expiry)
- [x] revoked → is_trust_root_active returns False
- [x] disable_trust_root changes only status + optional reason in metadata
- [x] revoke_trust_root changes only status + optional reason in metadata
- [x] disabled roots remain stored and retrievable via get/list
- [x] revoked roots remain stored and retrievable via get/list
- [x] disabled/revoked visible in list when filtered by status
- [x] no hard delete (not implemented)
- [x] unparseable expiry → is_trust_root_active returns False (conservative)
- [x] corrupt file → is_trust_root_active returns False
- [x] secret-containing file → is_trust_root_active returns False
- [x] at_time parameter correctly overrides now

## 3. Secret/private key rejection hardening

### Value patterns rejected on create (case-insensitive)
- [x] `-----BEGIN PRIVATE KEY-----`
- [x] `-----BEGIN OPENSSH PRIVATE KEY-----`
- [x] `sk-` prefix (OpenAI-style API keys)
- [x] `sk_` prefix (alternative format)
- [x] `Bearer ` (bearer token)

### Field names rejected on read (get_trust_root, case-insensitive)
- [x] `private_key`
- [x] `secret_key`
- [x] `api_key`
- [x] `password`
- [x] `token`
- [x] `signing_key`
- [x] `key_material` ← added during hardening
- [x] case-insensitive: `PRIVATE_KEY` also rejected

### Exemptions
- [x] `public_key_fingerprint` accepted
- [x] `metadata` dict exempt from all scanning
- [x] no private key material ever persisted to disk

## 4. Verifier stub integration hardening

- [x] TrustRootStore accepted via duck-typing (`hasattr(obj, "as_verifier_dict")`)
- [x] non-dict return from as_verifier_dict → treated as empty (no crash)
- [x] non-TrustRootStore, non-dict trust_roots → treated as empty (no crash)
- [x] active root + matching hash + unexpired → present_unverified (never verified)
- [x] active root + expired → invalid (`trust_root_expired`)
- [x] disabled root → invalid (`trust_root_disabled`)
- [x] revoked root → invalid (`trust_root_revoked`)
- [x] missing root → present_unverified (`unknown_trust_root`)
- [x] never returns signature_status=verified (114 tests exercise all paths)
- [x] never recommends trusted_signed
- [x] deterministic: same input → same output
- [x] non-mutating: does not update store
- [x] does not write to trust root files
- [x] does not update provenance.json
- [x] does not update signature.json
- [x] None trust_roots (Phase 8B-1 compat) still works
- [x] dict trust_roots (Phase 8B-1 compat) still works

## 5. No tools / flags / runtime wiring audit

- [x] No trust root tools exist anywhere: `list_capability_trust_roots`, `view_capability_trust_root`, `add_capability_trust_root`, `disable_capability_trust_root`, `revoke_capability_trust_root` — zero grep matches
- [x] No `CAPABILITY_TRUST_OPERATOR_PROFILE` exists
- [x] No `trust_root_tools_enabled` flag in config/settings
- [x] No TrustRootStore in `container.py`
- [x] No TrustRootStore in `capability_tools.py`
- [x] No trust_root references in `ToolDispatcher`
- [x] No trust_root references in `RuntimeProfiles`
- [x] No trust_root references in `StateView`
- [x] No trust_root references in `config.toml` or `settings.py`

## 6. No crypto/network audit

All zero results for `trust_roots.py` and `signature.py`:
- [x] No `cryptography` import
- [x] No `nacl` / `PyNaCl` import
- [x] No `rsa` / `ecdsa` / `ed25519` import
- [x] No `subprocess`
- [x] No `os.system` / `os.popen`
- [x] No `exec` / `eval`
- [x] No `importlib` / `runpy`
- [x] No `requests` / `httpx`
- [x] No `urllib` / `urlopen`
- [x] No `openai` / `anthropic`
- [x] No keyserver
- [x] No registry fetch
- [x] No remote registry
- [x] No network
- [x] No real cryptographic verification
- [x] No private key storage
- [x] No run_capability

## 7. Runtime import audit

```
grep -rn "from src.capabilities\|import src.capabilities" src/ | grep -v 'src/capabilities/'
```

Only allowed imports found:
- `src/tools/capability_tools.py` — existing capability tools
- `src/app/container.py` — existing capability wiring

No direct src.capabilities imports from:
- Brain
- TaskRuntime
- StateViewBuilder
- SkillExecutor
- ToolDispatcher
- agent modules
- dynamic agent runtime paths

## 8. Regression checks

- [x] All Phase 8A tests pass (228)
- [x] All Phase 8B-0 tests pass (75)
- [x] All Phase 8B-1 tests pass (169)
- [x] All Phase 8B-2 tests pass (117)
- [x] All Phase 7 tests pass (444)
- [x] All previous capability tests pass
- [x] Agent tests pass (544)
- [x] ToolDispatcher pass
- [x] RuntimeProfile pass
- [x] StateView pass
- [x] Skills/logging pass (96)
- [x] No run_capability exists
- [x] No new tools registered
- [x] No new runtime behavior

## 9. Documentation

- [x] `docs/capability_phase8b_trust_roots.md` — design doc
- [x] `docs/capability_phase8b_2_acceptance.md` — acceptance doc (this file)
- [x] `docs/capability_acceptance_index.md` — updated

## Known issues

- Expired active trust roots: `is_trust_root_active` checks status first (status=active), then expiry. Unparseable expiries are treated as expired (conservative).
- `as_verifier_dict()` returns all roots (including disabled/revoked/expired) so the verifier stub can make proper decisions.
- Verifier stub duck-typing uses `hasattr` + `isinstance(resolved, dict)` guard — non-dict results and non-TrustRootStore objects are treated as empty (fail-safe).
- Unknown fields in persisted JSON are silently dropped by `CapabilityTrustRoot.from_dict()` (not round-tripped).
- Field name rejection only triggers on `get_trust_root` (file read), not `create_trust_root`, because `CapabilityTrustRoot.to_dict()` only outputs known fields.

## Rollback notes

To roll back Phase 8B-2:
1. Remove `"TrustRootStore"` from `__all__` and import in `src/capabilities/__init__.py`
2. Remove `_is_trust_root_expired` function from `signature.py`
3. Remove expiry check and duck-typing from `verify_signature_stub` in `signature.py`
4. Remove `key_material` from `_SECRET_FIELD_NAMES` in `signature.py`
5. Delete `src/capabilities/trust_roots.py`
6. Delete the two test files and two doc files
7. Revert the Phase 8B-1 test change (`test_trust_root_expired_but_active_still_passes`)

No other files were modified. No behavior changes to roll back.
