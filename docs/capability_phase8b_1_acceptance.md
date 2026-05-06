# Phase 8B-1 Acceptance (Hardened)

Phase 8B-1: Local signature metadata parser + deterministic verifier stub.
Hardening pass completed before Phase 8B-2.

## Deliverables

| File | Status |
|---|---|
| `src/capabilities/signature.py` | Created + hardened |
| `tests/capabilities/test_phase8b_signature_metadata.py` | Created (80 tests after hardening) |
| `tests/capabilities/test_phase8b_signature_verifier_stub.py` | Created (51 tests after hardening) |
| `tests/capabilities/test_phase8b_signature_integration.py` | Created (38 tests) |
| `docs/capability_phase8b_signature_metadata.md` | Created |
| `docs/capability_phase8b_1_acceptance.md` | Updated with hardening results |

## Modified files

| File | Change |
|---|---|
| `src/capabilities/provenance.py` | Added `"signature.json"` to `_TREE_HASH_EXCLUDED_FILES` |
| `src/capabilities/__init__.py` | Exported new types and functions |

## Test results (post-hardening)

| Suite | Count | Status |
|---|---|---|
| Phase 8B-1 (signature metadata + verifier stub + integration) | 169 | all pass |
| All capability tests | 1892 | all pass |
| Phase 8A state model invariants | 118 | all pass |
| Phase 8B-0 signature/trust model invariants | 75 | all pass |
| Phase 7 (quarantine, import, activation) | 444 | all pass |
| Agent tests | 544 | all pass |
| Core (ToolDispatcher, RuntimeProfiles, StateView) | 128 | all pass |
| Skills + logging | 96 | all pass |

## Import audit

Only `src/tools/capability_tools.py` and `src/app/container.py` import from `src.capabilities` outside the package. No new importers added. No unauthorized imports of `signature` module from Brain, TaskRuntime, StateViewBuilder, SkillExecutor, ToolDispatcher, or agent modules.

## Hardening: signature metadata safety

### Parsing validation
- [x] Valid signature parses with all optional fields
- [x] Missing optional fields allowed (all None)
- [x] Required field semantics documented (none required for signature; trust_root_id + fingerprint required for trust root)
- [x] Malformed JSON returns None from read_signature, invalid from verifier stub
- [x] Non-dict types rejected with TypeError
- [x] Field length limits enforced (1 MiB max per string field)
- [x] Metadata round-trip works
- [x] Unknown fields silently ignored (not round-tripped)
- [x] write_signature writes only signature.json
- [x] read_signature missing returns None
- [x] Corrupt/secret-containing signature.json returns None from read_signature
- [x] Path traversal rejected (both `..` in parts and resolved-path escape)
- [x] No files written outside capability directory

## Hardening: private key / secret rejection

### Value patterns rejected (case-insensitive)
- [x] `-----BEGIN PRIVATE KEY-----`
- [x] `-----BEGIN RSA PRIVATE KEY-----`
- [x] `-----BEGIN EC PRIVATE KEY-----`
- [x] `-----BEGIN DSA PRIVATE KEY-----`
- [x] `-----BEGIN OPENSSH PRIVATE KEY-----`
- [x] `-----BEGIN ENCRYPTED PRIVATE KEY-----`

### Field names rejected (regardless of value)
- [x] `private_key`, `secret_key`, `api_key`, `password`, `secret`, `passphrase`, `token`, `access_token`, `bearer_token`, `refresh_token`, `client_secret`, `signing_key`, `privatekey`, `secretkey`, `apikey` — 16 patterns total, case-insensitive

### API key / bearer token patterns rejected
- [x] `sk-` prefix (OpenAI-style API keys)
- [x] `sk_` prefix
- [x] `Bearer ` (Authorization header tokens)
- [x] `bearer ` (lowercase variant)

### Rejection behavior
- [x] `parse_signature_dict`: raises ValueError
- [x] `parse_trust_root_dict`: raises ValueError
- [x] `write_signature`: raises ValueError
- [x] `read_signature`: returns None (catches ValueError)
- [x] Verifier stub: returns invalid/malformed
- [x] `metadata` dict exempt from scanning (may contain arbitrary data)

### No private key fields in CapabilityTrustRoot
- [x] Field names scanned; values scanned; no private key storage

## Hardening: trust root metadata safety

- [x] Valid trust root parses
- [x] Status active/disabled/revoked accepted
- [x] Invalid status rejected (ValueError in __post_init__)
- [x] Missing fingerprint rejected
- [x] Missing trust_root_id rejected
- [x] Metadata round-trip works
- [x] Unknown fields silently ignored
- [x] No private key fields accepted (field name + value checks)
- [x] Expired trust root behavior documented (parses fine; active status still treated as active)
- [x] Disabled/revoked trust roots never verify (verifier stub returns invalid)
- [x] Trust roots are local metadata only, no remote fetch

## Hardening: verifier stub decision table

| Condition | signature_status | allowed | code |
|---|---|---|---|
| No signature.json | not_present | true | no_signature |
| Unparseable JSON | invalid | false | malformed_signature |
| Non-dict JSON | invalid | false | malformed_signature |
| Secret field / private key / API key | invalid | false | malformed_signature |
| signed_tree_hash missing | invalid | false | missing_tree_hash |
| signed_tree_hash != tree hash | invalid | false | tree_hash_mismatch |
| trust_root_id not in trust_roots | present_unverified | true | unknown_trust_root |
| trust root disabled | invalid | false | trust_root_disabled |
| trust root revoked | invalid | false | trust_root_revoked |
| active trust root + hash match | present_unverified | true | hash_consistent_unverified |
| No trust_root_id + hash match | present_unverified | true | hash_consistent_unverified |

## Hardening: never-verified / never-trusted_signed proof

- [x] `SIGNATURE_VERIFIED` not imported in signature.py (only INVALID, NOT_PRESENT, PRESENT_UNVERIFIED)
- [x] `TRUST_TRUSTED_SIGNED` not imported in signature.py (only TRUST_UNTRUSTED)
- [x] Programmatic source check: no `SIGNATURE_VERIFIED` or `TRUST_TRUSTED_SIGNED` in executable code of `verify_signature_stub`
- [x] 169 tests exercise every return path; none produce verified or trusted_signed

## Hardening: tree hash self-reference proof

- [x] `signature.json` in `_TREE_HASH_EXCLUDED_FILES`
- [x] Adding signature.json does not change tree hash
- [x] Modifying signature.json does not change tree hash
- [x] Deleting signature.json does not change tree hash
- [x] Changing CAPABILITY.md still changes tree hash
- [x] Changing manifest/scripts/tests/examples still changes tree hash
- [x] Rationale: avoid signature self-reference (signature signs the hash, hash must not include the signature)

## Hardening: no behavior integration proof

- [x] `CapabilityTrustPolicy` does not import or reference `signature` module
- [x] `CapabilityRetriever` unchanged
- [x] `StateView` unchanged
- [x] `CapabilityLifecycleManager` unchanged
- [x] Import/apply behavior unchanged
- [x] Stable promotion behavior unchanged
- [x] No tools registered from signature module
- [x] No permissions granted
- [x] Missing signature does not break legacy capabilities

## Hardening: no crypto/network audit

All checks zero results:
- [x] No `cryptography` import
- [x] No `nacl` / `PyNaCl` import
- [x] No `rsa` / `ecdsa` import
- [x] No `hmac` usage beyond standard hash (none used)
- [x] No `subprocess`
- [x] No `os.system` / `os.popen`
- [x] No `exec` / `eval`
- [x] No `importlib` / `runpy`
- [x] No `requests` / `httpx`
- [x] No `urllib` / `urlopen`
- [x] No `openai` / `anthropic`
- [x] No remote registry calls
- [x] No URL fetch
- [x] No keyserver
- [x] No network
- [x] No script execution
- [x] No Python import from capability files

## Known issues

- Expired active trust roots are treated as active (expiry is metadata for now; future phase may enforce)
- Field length limit is 1 MiB per string field — large but prevents memory exhaustion
- Unknown fields in JSON are silently dropped rather than round-tripped

## Rollback notes

To roll back Phase 8B-1:
1. Remove `"signature.json"` from `_TREE_HASH_EXCLUDED_FILES` in `provenance.py`
2. Remove signature exports from `__init__.py`
3. Delete `src/capabilities/signature.py`
4. Delete the three test files and two doc files

No other files were modified. No behavior changes to roll back.
