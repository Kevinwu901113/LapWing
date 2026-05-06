# Phase 8A-1: Capability Provenance / Integrity Foundation

**Date:** 2026-05-04
**Scope:** Provenance data model, deterministic tree hashing, analytical trust policy
**Status:** Implemented — data + hashing + analytical policy only, no runtime gating

---

## 1. Design Summary

Phase 8A-1 adds three capabilities to the capability system:

1. **CapabilityProvenance** — a serializable provenance record stored alongside each capability as `provenance.json`, tracking origin, trust, integrity, and signature metadata.
2. **Deterministic tree hashing** — a content hash computed over the capability directory tree that excludes volatile/post-hoc artifacts, giving a stable integrity fingerprint.
3. **CapabilityTrustPolicy** — an analytical policy class that evaluates provenance data and returns TrustDecision objects. Non-gating in Phase 8A-1: callers decide whether to act on decisions.

All three are purely additive: no existing code paths are modified beyond the two integration points (import and activation apply), and no runtime behavior changes.

## 2. Data Model

### 2.1 CapabilityProvenance

Stored as `provenance.json` in the capability directory (quarantine or active scope).

| Field | Type | Description |
|---|---|---|
| `provenance_id` | `str` | Unique ID: `prov_{uuid12}` |
| `capability_id` | `str` | The capability this provenance belongs to |
| `source_type` | `str` | One of: `local_package`, `manual_draft`, `curator_proposal`, `quarantine_activation`, `unknown` |
| `source_path_hash` | `str\|None` | SHA256 of resolved source path (import only). Raw paths never stored. |
| `source_content_hash` | `str` | Tree hash computed at provenance creation time |
| `imported_at` | `str\|None` | ISO timestamp of import |
| `imported_by` | `str\|None` | Operator who performed the import |
| `activated_at` | `str\|None` | ISO timestamp of activation |
| `activated_by` | `str\|None` | Operator who performed the activation |
| `parent_provenance_id` | `str\|None` | Links to quarantine provenance (activation only) |
| `origin_capability_id` | `str\|None` | Original capability ID (activation only) |
| `origin_scope` | `str\|None` | Origin scope, e.g. `quarantine` |
| `trust_level` | `str` | One of: `unknown`, `untrusted`, `reviewed`, `trusted_local`, `trusted_signed` |
| `integrity_status` | `str` | One of: `unknown`, `verified`, `mismatch` |
| `signature_status` | `str` | One of: `not_present`, `present_unverified`, `verified`, `invalid` |
| `metadata` | `dict` | Arbitrary key-value metadata |

Why 5 trust levels instead of 3 in the Phase 8A-0 conceptual model:
- `unknown` — no provenance at all (distinct from untrusted-but-present)
- `trusted_local` — developer-authored in the local repo (distinct from reviewed import)
- Normalized to `verified`/`mismatch` for integrity (avoiding `intact`/`tampered` which imply malice)

### 2.2 TrustDecision

Analytical decision dataclass. Mirrors PolicyDecision:

| Field | Type | Description |
|---|---|---|
| `allowed` | `bool` | Whether the trust check passed |
| `severity` | `str` | `info`, `warning`, or `error` |
| `code` | `str` | Machine-readable decision code |
| `message` | `str` | Human-readable description |
| `details` | `dict` | Structured context |

Factory methods: `TrustDecision.allow()`, `.warn()`, `.deny()`.

### 2.3 CapabilityTrustPolicy

Four pure methods:

| Method | Returns | Purpose |
|---|---|---|
| `evaluate_provenance(provenance)` | `TrustDecision` | Trust analysis from provenance data alone |
| `can_activate_from_quarantine(provenance, audit_result, review)` | `TrustDecision` | Trust gates for quarantine activation |
| `can_retrieve(manifest, provenance)` | `TrustDecision` | Always allows in 8A-1; warns on missing/untrusted |
| `can_promote_to_stable(manifest, provenance, eval_record)` | `TrustDecision` | Requires reviewed/trusted_local minimum; denies on integrity/signature failure |

## 3. Tree Hash Algorithm

### 3.1 Included files
- `CAPABILITY.md` — raw bytes
- `manifest.json` — normalized (content_hash, created_at, updated_at stripped)
- `scripts/` — all regular files (recursive)
- `tests/` — all regular files (recursive)
- `examples/` — all regular files (recursive)

### 3.2 Excluded (volatile/post-hoc artifacts)
- `evals/`, `traces/`, `versions/`
- `quarantine_audit_reports/`, `quarantine_reviews/`
- `quarantine_transition_requests/`, `quarantine_activation_plans/`, `quarantine_activation_reports/`
- `provenance_verification_logs/`
- `provenance.json`, `import_report.json`, `activation_report.json`
- `.sqlite`, `.db`, `.pyc`, `.pyo` files
- Hidden files/directories (leading `.`), `.gitkeep`
- Symlinks (never followed)

### 3.3 Algorithm ("sha256_path_sorted")

```
1. Walk directory tree, sorted by path for determinism
2. Filter: only regular files passing inclusion rules
3. For each file: SHA256(relative_path_bytes + b":" + file_bytes)
   - manifest.json bytes are normalized (computed fields stripped)
4. Sort per-file hashes by relative path
5. Final: SHA256("||".join("relpath=hash" for each))
```

Returns empty string for non-existent directories, SHA256 of empty bytes for empty directories.

### 3.4 Functions

- `compute_capability_tree_hash(directory)` → `str` (64-char hex)
- `compute_package_hash(directory)` → `str` (alias, used at import time)
- `verify_content_hash_against_provenance(directory, provenance)` → `bool`

## 4. I/O Functions

| Function | Returns | Behavior |
|---|---|---|
| `write_provenance(directory, **kwargs)` | `CapabilityProvenance` | Builds record, writes `provenance.json`. Never raises on I/O. |
| `read_provenance(directory)` | `CapabilityProvenance \| None` | Reads `provenance.json`. Returns None if missing or unparseable. Never raises. |
| `update_provenance_integrity_status(directory, status)` | `CapabilityProvenance \| None` | Updates integrity_status in-place. Returns None if no provenance or invalid status. |

## 5. Integration Points

### 5.1 Phase 7A Import (import_quarantine.py)

After import_report.json write, before index upsert:

1. Compute `source_content_hash = compute_package_hash(source_path)` from the **original** source package (not the quarantined copy)
2. Write provenance.json with:
   - `source_type = "local_package"`
   - `source_path_hash = SHA256(resolved source path)`
   - `source_content_hash` from source package
   - `trust_level = "untrusted"`
   - `integrity_status = "verified"` (content just copied, verified by os.copy)
   - `signature_status = "not_present"`
   - `imported_at`, `imported_by`

**Fail-closed:** If provenance write fails, the quarantine directory is removed and import returns an error ImportResult. This prevents capabilities with missing provenance from entering quarantine.

### 5.2 Phase 7D-B Activation Apply (quarantine_activation_apply.py)

After activation_report.json writes, before index refresh:

1. Read quarantine provenance for `parent_provenance_id`
2. Determine trust_level:
   - `"reviewed"` if review status is `approved_for_testing` AND audit passed
   - `"untrusted"` otherwise
3. Inherit `signature_status` from quarantine provenance
4. Write provenance.json with:
   - `source_type = "quarantine_activation"`
   - `parent_provenance_id` from quarantine
   - `origin_capability_id`, `origin_scope = "quarantine"`
   - `source_content_hash` from content_hash_after
   - `activated_at`, `activated_by`
   - metadata includes `activation_plan_id` and `transition_request_id`

**Fail-closed:** If provenance write fails, the target directory is removed (activation rollback) and an error ActivationResult is returned.

## 6. Key Invariants

1. **provenance.json is excluded from tree hash.** Avoids self-referential hash churn.
2. **manifest.json is normalized before tree hashing.** `content_hash`, `created_at`, `updated_at` are stripped — matching the existing `compute_content_hash` behavior in `hashing.py`.
3. **Raw source paths are never stored in provenance.** Only `source_path_hash` (SHA256) is recorded.
4. **Quarantine provenance is never modified by activation.** The activation writes derived provenance to the target directory, leaving quarantine unchanged (byte-for-byte, verified by test).
5. **Missing provenance does not break legacy capabilities.** `read_provenance()` returns None silently. `evaluate_provenance(None)` warns but allows.
6. **TrustPolicy is purely analytical.** No method in CapabilityTrustPolicy gates any import, activation, retrieval, or execution path. Callers decide whether to act on decisions.
7. **No execution, no network, no signature verification.** This is data + hashing only.

## 7. Test Coverage

| Test File | Tests | Coverage |
|---|---|---|
| `test_phase8a_provenance_model.py` | 17 | CapabilityProvenance serialization, enums, TrustDecision factories |
| `test_phase8a_tree_hash.py` | 29 | Determinism, all include/exclude rules, binary files, symlinks |
| `test_phase8a_trust_policy.py` | 27 | All 4 policy methods, determinism, non-mutation |
| `test_phase8a_provenance_integration.py` | 29 | Import writes provenance, activation writes derived provenance, fail-closed, round-trip |

## 8. Files

| File | Status | Lines |
|---|---|---|
| `src/capabilities/provenance.py` | Created | ~620 |
| `src/capabilities/__init__.py` | Modified | +16 exports |
| `src/capabilities/import_quarantine.py` | Modified | +25 lines (provenance write after import) |
| `src/capabilities/quarantine_activation_apply.py` | Modified | +40 lines (provenance write after activation) |
| `tests/capabilities/test_phase8a_provenance_model.py` | Created | ~170 |
| `tests/capabilities/test_phase8a_tree_hash.py` | Created | ~250 |
| `tests/capabilities/test_phase8a_trust_policy.py` | Created | ~220 |
| `tests/capabilities/test_phase8a_provenance_integration.py` | Created | ~430 |
