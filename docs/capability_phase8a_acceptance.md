# Phase 8A-1 Acceptance Report — Hardened (Final)

**Date:** 2026-05-04
**Phase:** 8A-1 — Capability Provenance / Integrity Foundation
**Status:** Accepted (Hardened — second pass)

## Test Results

| Suite | Count | Status |
|---|---|---|
| `test_phase8a_provenance_model.py` | 17 | 17 passed |
| `test_phase8a_tree_hash.py` | 29 | 29 passed |
| `test_phase8a_trust_policy.py` | 27 | 27 passed |
| `test_phase8a_provenance_integration.py` | 29 | 29 passed |
| `test_phase8a_state_model_invariants.py` | 46 | 46 passed (Phase 8A-0) |
| `test_phase8a_hardening.py` | 80 | 80 passed |
| **Phase 8A subtotal** | **228** | **228 passed, 0 failed** |
| Full capability suite | 1,678 | 1,678 passed, 0 failed |
| Agent tests | 544 | 544 passed, 0 failed |
| Core tests (ToolDispatcher, RuntimeProfile, StateView) | 128 | 128 passed, 0 failed |
| Skills/logging tests | 96 | 96 passed, 0 failed |
| **Grand total** | **2,472** | **2,472 passed, 0 failed** |

---

## 1. Provenance Model Hardening

| Criterion | Status | Evidence |
|---|---|---|
| All 5 source_type values accepted | Pass | `test_all_valid_source_types_accepted_in_provenance` |
| All 5 trust_level values accepted | Pass | `test_all_valid_trust_levels_accepted_in_provenance` |
| All 3 integrity_status values accepted | Pass | `test_all_valid_integrity_statuses_accepted_in_provenance` |
| All 4 signature_status values accepted | Pass | `test_all_valid_signature_statuses_accepted_in_provenance` |
| Invalid enum values rejected cleanly (model) | Pass | `test_from_dict_with_invalid_*` — model stores as-is without crash |
| Invalid enum values normalized by policy | Pass | Invalid trust→untrusted, invalid integrity→unknown |
| Invalid enum values rejected at write points | Pass | `test_update_integrity_status_rejects_all_invalid` |
| Serialization round-trip preserves all fields | Pass | `test_round_trip_to_dict_from_dict`, `test_round_trip_with_all_fields_populated` |
| Metadata round-trip (nested, deep, special chars) | Pass | `test_metadata_round_trip`, `test_round_trip_with_special_characters_in_metadata` |
| Null values round-trip correctly | Pass | `test_round_trip_with_null_values` |
| Unknown fields behavior documented | Pass | `test_unknown_fields_in_provenance_preserved` |
| provenance_id path-safe (`prov_{hex12}`) | Pass | `test_provenance_id_is_path_safe` |
| Raw source path never stored | Pass | `test_import_provenance_raw_source_path_absent` (on-disk scan) |
| source_path_hash used instead (SHA256 hex) | Pass | `test_source_path_hash_is_sha256_hex` (64 char, hex) |
| Metadata caller-sanitized (stored as-is) | Documented | Metadata stored as provided; CoT/chain-of-thought fields are preserved. Caller is responsible for sanitization. |
| source_content_hash is SHA256 hex | Pass | `test_source_content_hash_is_sha256_hex` |
| imported_at/activated_at are ISO format | Pass | `test_imported_at_is_iso_format`, `test_activated_at_is_iso_format` |
| origin_scope/origin_capability_id preserved | Pass | `test_origin_scope_quarantine_preserved` |
| parent_provenance_id preserved | Pass | `test_parent_provenance_id_preserved` |
| provenance.json is always valid JSON | Pass | `test_provenance_json_is_valid_json_always` |

## 2. Tree Hash Determinism

| Criterion | Status | Evidence |
|---|---|---|
| Same directory tree → same hash (repeated calls) | Pass | `test_same_content_same_hash`, `test_string_passed_unchanged_through_content_unchanged` |
| Same content → same hash after file creation order change | Pass | `test_file_ordering_does_not_matter` |
| Path ordering deterministic (sorted rglob) | Pass | `test_file_ordering_does_not_matter` |
| Binary file contents hashed by bytes | Pass | `test_binary_file_hashed_by_bytes`, `test_binary_file_change_changes_hash` |
| Invalid UTF-8 file handled by bytes | Pass | `test_invalid_utf8_file_hashed_by_bytes` |
| Line ending changes affect hash (bytes differ) | Pass | `test_line_ending_changes_hash` |
| Symlinks skipped, never followed | Pass | `test_symlinks_skipped` |
| Changing CAPABILITY.md changes hash | Pass | `test_includes_capability_md`, `test_different_capability_md_changes_hash` |
| Changing manifest.json changes hash | Pass | `test_includes_manifest_json`, `test_different_manifest_changes_hash` |
| Changing scripts/ file changes hash | Pass | `test_includes_scripts` |
| Changing tests/ file changes hash | Pass | `test_includes_tests` |
| Changing examples/ file changes hash | Pass | `test_includes_examples` |
| Changing nested file changes hash | Pass | `test_nested_file_changes_hash` |
| Deleting included file changes hash | Pass | `test_deleting_included_file_changes_hash` |
| Adding included file changes hash | Pass | `test_adding_included_file_changes_hash` |
| Empty directory produces stable hash (SHA256 of "") | Pass | `test_empty_directory` |
| Identical empty dirs produce same hash | Pass | `test_identical_empty_dirs_same_hash` |
| Non-existent directory returns "" | Pass | `test_nonexistent_directory` |
| package_hash is compute_capability_tree_hash alias | Pass | `test_package_hash_is_alias` |

**Algorithm:** "sha256_path_sorted" — walk tree, filter to included files, hash each as `SHA256(relpath_bytes + ":" + content_bytes)`, sort by relpath, final `SHA256("||".join("relpath=hash"))`. manifest.json is normalized (content_hash, created_at, updated_at stripped before hashing).

## 3. Volatile Exclusion Hardening

| Excluded artifact | Status | Evidence |
|---|---|---|
| provenance.json | Pass | `test_excludes_provenance_json` |
| import_report.json | Pass | `test_excludes_import_report_json` |
| activation_report.json | Pass | `test_excludes_activation_report_json` |
| evals/ | Pass | `test_excludes_evals` |
| traces/ | Pass | `test_excludes_traces` |
| versions/ | Pass | `test_excludes_versions` |
| quarantine_audit_reports/ | Pass | `test_excludes_quarantine_audit_reports` |
| quarantine_reviews/ | Pass | `test_excludes_quarantine_reviews` |
| quarantine_transition_requests/ | Pass | `test_excludes_quarantine_transition_requests` |
| quarantine_activation_plans/ | Pass | `test_excludes_quarantine_activation_plans` |
| quarantine_activation_reports/ | Pass | `test_excludes_quarantine_activation_reports` |
| provenance_verification_logs/ | Pass | `test_excludes_provenance_verification_logs` |
| .sqlite / .db files | Pass | `test_excludes_sqlite` |
| .pyc / .pyo files | Pass | `test_excludes_hidden_directory` |
| __pycache__ / hidden directories | Pass | `test_excludes_hidden_directory` (dot-prefixed segments excluded) |
| Dot-files (.hidden/) | Pass | `test_excludes_dot_files` |
| .gitkeep | Pass | `test_excludes_gitkeep` |
| manifest.json content_hash field changes | Pass | `test_manifest_normalized_ignores_content_hash` |
| Dir name matching excluded dir (part-based) | Pass | `test_excluded_dir_detected_by_part_matching` |

All 19 volatile exclusion categories verified. No omissions.

## 4. Read/Write/Update Safety

| Criterion | Status | Evidence |
|---|---|---|
| write_provenance writes only provenance.json | Pass | `test_write_provenance_only_creates_provenance_json` |
| write_provenance does not touch manifest.json | Pass | `test_writing_provenance_does_not_affect_manifest` |
| read_provenance reads provenance.json | Pass | All model tests |
| Missing provenance returns None (no crash) | Pass | `test_missing_provenance_returns_none` |
| Corrupt JSON returns None (no crash) | Pass | `test_corrupt_json_returns_none` |
| Empty file returns None | Pass | `test_empty_provenance_json_returns_none` |
| Partial/incomplete JSON returns None | Pass | `test_partial_json_returns_none`, `test_corrupt_write_partial_file_returns_none_on_read` |
| read_provenance never raises (OSError, etc.) | Pass | `test_read_provenance_never_raises` |
| read_provenance: directory-not-file → None | Pass | `test_read_provenance_returns_none_for_directory_not_file` |
| Overwrite preserves structure cleanly | Pass | `test_overwrite_preserves_structure` |
| write creates file if missing | Pass | `test_write_provenance_creates_file_if_missing` |
| update_provenance_integrity_status sets verified | Pass | `test_sets_verified` |
| update_provenance_integrity_status sets mismatch | Pass | `test_sets_mismatch` |
| Update does not change unrelated fields | Pass | `test_does_not_change_other_fields` |
| Invalid integrity status rejected (all bogus values) | Pass | `test_invalid_status_rejected`, `test_update_integrity_status_rejects_all_invalid` |
| No provenance → update returns None | Pass | `test_update_integrity_status_no_provenance_returns_none` |
| No writes outside capability directory | Pass | `test_write_provenance_uses_directory_parameter`, `test_provenance_io_does_not_traverse_up` |
| verify_content_hash_against_provenance: match | Pass | `test_verified_when_hash_matches` |
| verify_content_hash_against_provenance: mismatch | Pass | `test_mismatch_when_hash_differs` |
| Empty hash on either side → False | Pass | `test_false_when_provenance_hash_empty`, `test_false_when_curren_hash_empty` |

**Write atomicity note:** `write_provenance` uses `Path.write_text()` which on Linux does write-to-temp + rename (atomic). No explicit fsync; crash during write may produce partial file. `read_provenance` recovers cleanly (returns None for corrupt/partial JSON). This matches the existing pattern used by `import_report.json`, `activation_report.json`, and other JSON artifacts in the capability system.

## 5. Import Integration Hardening

| Criterion | Status | Evidence |
|---|---|---|
| Writes provenance.json into quarantine dir | Pass | `test_import_writes_provenance_json` |
| source_type=local_package | Pass | `test_import_provenance_source_type_local_package` |
| trust_level=untrusted | Pass | `test_import_provenance_trust_level_untrusted` |
| integrity_status=verified | Pass | `test_import_provenance_integrity_status_verified` |
| signature_status=not_present | Pass | `test_import_provenance_signature_not_present` |
| source_path_hash present (SHA256 hex, 64 chars) | Pass | `test_import_provenance_source_path_hash_present` |
| source_content_hash present (tree hash of source) | Pass | `test_import_provenance_source_content_hash_present` |
| Raw source path absent (on-disk scan) | Pass | `test_import_provenance_raw_source_path_absent` |
| imported_at populated (ISO format) | Pass | `test_import_provenance_has_imported_at` |
| imported_by preserved/sanitized | Pass | `test_import_provenance_has_imported_by` |
| import_report still written | Pass | Verified by all Phase 7A tests |
| Provenance failure → import rollback + cleanup | Pass | `test_import_provenance_fail_closed_cleans_up` |
| Failed import leaves no partial quarantine dir | Pass | `test_import_provenance_fail_closed_cleans_up` |
| All Phase 7A hardening tests still pass | Pass | 1,678 capability tests pass |

**Fail-closed mechanism** (in `import_capability_package`): If the `write_provenance` call raises any exception, the quarantine directory is removed via `shutil.rmtree()` and an `ImportResult` with `applied=False` is returned. The outer `try/except` in the import function itself also catches any broader failure.

## 6. Activation Integration Hardening

| Criterion | Status | Evidence |
|---|---|---|
| Writes provenance.json into target active/testing dir | Pass | `test_apply_writes_provenance_json` |
| source_type=quarantine_activation | Pass | `test_apply_provenance_source_type_quarantine_activation` |
| parent_provenance_id links quarantine provenance | Pass | `test_apply_provenance_parent_links_quarantine` |
| origin_capability_id set | Pass | `test_apply_provenance_origin_capability_id` |
| origin_scope=quarantine | Pass | `test_apply_provenance_origin_scope_quarantine` |
| trust_level=reviewed only when review+audit pass | Pass | `test_apply_provenance_trust_reviewed_when_review_and_audit_pass` |
| trust_level=untrusted when review not approved | Pass | `test_apply_provenance_trust_untrusted_when_review_not_approved` (gates block correctly) |
| trust_level=untrusted when audit not passed | Pass | `test_apply_provenance_trust_untrusted_when_audit_not_passed` (gates block correctly) |
| integrity_status=verified | Pass | `test_apply_provenance_integrity_verified` |
| signature_status inherited from quarantine | Pass | `test_apply_provenance_signature_inherited_from_quarantine` |
| activated_at populated (ISO format) | Pass | `test_apply_provenance_activated_at_set` |
| activated_by preserved/sanitized | Pass | `test_apply_provenance_activated_by_set` |
| activation_plan_id and transition_request_id in metadata | Pass | `test_apply_provenance_metadata_contains_plan_and_request` |
| Target provenance failure → rollback target dir | Pass | `test_provenance_write_failure_rolls_back_target_dir` |
| Quarantine provenance unchanged byte-for-byte (success) | Pass | `test_quarantine_provenance_unchanged_after_activation` |
| Quarantine provenance unchanged byte-for-byte (failure) | Pass | `test_quarantine_provenance_unchanged_even_when_activation_fails` |
| activation_report still written | Pass | Verified by all Phase 7D-B tests |
| Full round-trip provenance chain verified | Pass | `test_full_import_to_activation_provenance_chain` |
| All Phase 7D-B hardening tests still pass | Pass | 1,678 capability tests pass |

**Fail-closed mechanism** (in `apply_quarantine_activation`): The provenance write is wrapped in a try/except. On failure, the target directory is removed via `shutil.rmtree()` and an `ActivationResult` with `applied=False` is returned with a `provenance_write_failed` finding. The outer `except Exception` also cleans up the target directory if it was created.

## 7. Trust Policy Hardening

| Criterion | Status | Evidence |
|---|---|---|
| Deterministic: same input → same decision | Pass | 2 explicit determinism tests |
| Non-mutating (call twice, same result) | Pass | `test_non_mutating` (policy tests) |
| Trust policy state never mutates provenance | Pass | `test_trust_policy_state_never_mutates_provenance` |
| evaluate_provenance never raises (None, valid, invalid) | Pass | `test_evaluate_provenance_never_raises` |
| Missing provenance warns, does not deny legacy retrieval | Pass | `test_missing_provenance_warns` (retrieval) |
| Unknown trust evaluates (warns but allows) | Pass | `test_unknown_trust_evaluates` |
| Invalid trust normalized to untrusted in policy | Pass | `test_invalid_trust_defaults_to_untrusted_in_policy` |
| Invalid integrity normalized to unknown in policy | Pass | `test_invalid_integrity_defaults_to_unknown_in_policy` |
| untrusted cannot promote to stable | Pass | `test_untrusted_denies` |
| reviewed can be considered for testing | Pass | `test_reviewed_allows` (activate) |
| trusted_local can be considered for stable if eval passes | Pass | `test_trusted_local_allows` (promotion) |
| trusted_signed enum accepted (no verification yet) | Pass | `test_trusted_signed_allows` |
| trusted_signed + verified sig → allows promote | Pass | `test_trusted_signed_with_verified_sig_allows_promote` |
| Invalid integrity blocks activation | Pass | `test_mismatch_blocks_activate` |
| Invalid integrity blocks promotion | Pass | `test_mismatch_blocks_promote` |
| Invalid signature blocks activation | Pass | `test_invalid_signature_blocks_activate` |
| Invalid signature blocks promotion | Pass | `test_invalid_signature_blocks_promote` |
| present_unverified signature allows activation | Pass | `test_present_unverified_signature_allows_activation` |
| present_unverified signature allows promotion (if trusted) | Pass | `test_present_unverified_signature_warns_on_promotion` |
| can_retrieve returns decision only, does not mutate | Pass | `test_can_retrieve_does_not_change_retrieval` |
| can_retrieve never denies (even with mismatch) | Pass | `test_can_retrieve_never_denies` (exhaustive trust×integrity) |
| can_retrieve without provenance warns, allows | Pass | `test_can_retrieve_without_provenance_does_not_deny` |
| can_promote_to_stable not wired to LifecycleManager | Pass | `test_can_promote_to_stable_not_wired_to_lifecycle` (inspect.getsource check) |
| TrustPolicy never imports LifecycleManager | Pass | `test_trust_policy_never_imports_lifecycle` |
| No runtime behavior changes | Pass | Analytical only — no gate integration in any code path |

## 8. Legacy Compatibility

| Criterion | Status | Evidence |
|---|---|---|
| Manifest creates fine without provenance field | Pass | `test_manifest_from_dict_without_provenance` |
| Store.get works for caps without provenance.json | Pass | `test_store_get_works_without_provenance` |
| Store.list includes caps without provenance | Pass | `test_store_list_includes_caps_without_provenance` |
| Index.search finds caps without provenance | Pass | `test_store_search_finds_caps_without_provenance` |
| LifecycleManager evaluate works without provenance | Pass | `test_lifecycle_manager_works_without_provenance` |
| CapabilityRetriever works without provenance | Pass | `test_retriever_works_without_provenance` |
| Retriever.summarize works on legacy caps | Pass | `test_retriever_summarize_includes_legacy_caps` |
| Caps with provenance retrieve and read correctly | Pass | `test_capability_with_provenance_retrieves_and_reads` |
| Manual draft/create behavior unchanged | Pass | No changes to draft/create paths |
| Curator proposal/apply behavior unchanged | Pass | No changes to curator paths |
| All 1,678 capability tests pass (no regressions) | Pass | Full regression suite |

## 9. Execution / Network / Signing Audit

| Check | Status |
|---|---|
| No subprocess | Pass (grep: 0 matches) |
| No os.system | Pass (grep: 0 matches) |
| No os.popen | Pass (grep: 0 matches) |
| No exec / eval | Pass (grep: 0 matches) |
| No importlib / runpy | Pass (grep: 0 matches) |
| No requests / httpx / urllib | Pass (grep: 0 matches) |
| No openai / anthropic | Pass (grep: 0 matches) |
| No cryptography / signing libraries | Pass (no such imports) |
| No script execution | Pass (no subprocess/exec) |
| No Python import from capability files | Pass (no importlib/runpy) |
| No network | Pass (no networking imports) |
| No remote registry | Pass |
| No signature verification | Pass (deferred to future phases) |
| No run_capability exists | Pass (grep: only docstring mentions in comments) |

**Only imports in provenance.py:** `hashlib`, `json`, `uuid`, `dataclasses`, `datetime`, `pathlib`, `typing`, `__future__`. All safe standard library.

## 10. Runtime Import Audit

```
grep -rn "from src.capabilities\|import src.capabilities" src/ | grep -v 'src/capabilities/'
```

Result: Only `src/tools/capability_tools.py` and `src/app/container.py` import from `src.capabilities/`. No imports from:
- Brain
- TaskRuntime
- StateView / StateViewBuilder
- SkillExecutor
- ToolDispatcher
- Agent modules (candidate, candidate_store, registry, spec, policy)
- Dynamic agent runtime paths

**Boundary held.**

## 11. Full Regression Summary

| Suite | Tests | Result |
|---|---|---|
| All capability tests | 1,678 | 0 failed |
| Agent tests | 544 | 0 failed |
| Core tests (ToolDispatcher, RuntimeProfile, StateView) | 128 | 0 failed |
| Skills/logging tests | 96 | 0 failed |
| **Grand total** | **2,472** | **0 failures, 0 regressions** |

## 12. Known Issues

1. **Metadata CoT/chain-of-thought fields:** `CapabilityProvenance.metadata` stores caller-provided values as-is. If a caller includes sensitive fields (chain_of_thought, reasoning, etc.), they will be serialized to `provenance.json` on disk. This is documented as caller-sanitized — the module does not strip any metadata keys. Future phases may add sanitization if needed.

2. **Write atomicity:** `write_provenance` uses `Path.write_text()`, which on Linux does write-to-temp + rename (atomic). No explicit fsync; a crash during the write may produce a partial `provenance.json`. `read_provenance` recovers cleanly (returns None for corrupt/partial JSON). This matches the existing pattern used by `import_report.json`, `activation_report.json`, and other JSON artifacts in the capability system.

3. **Invalid enum values in from_dict:** `CapabilityProvenance.from_dict()` passes through any string values without validation — it's a dataclass, not Pydantic. Invalid values are only checked at specific validation points (`update_provenance_integrity_status` validates against `PROVENANCE_INTEGRITY_STATUSES`; `CapabilityTrustPolicy.evaluate_provenance` normalizes invalid trust/integrity/signature to safe defaults). This is consistent with how `CapabilityManifest` handles its enum fields.

4. **No signature verification yet:** `signature_status` is recorded and carried through provenance chains but never verified. Actual cryptographic signature verification is deferred to future phases (8B+). The `trusted_signed` trust level is accepted and evaluated by the policy, but the policy warns when `trusted_signed` is combined with a non-verified signature status.

## 13. Files Changed (this hardening pass)

| File | Action | Lines (net change) |
|---|---|---|
| `tests/capabilities/test_phase8a_hardening.py` | Extended | +130 lines (54→80 tests) |

### Original Phase 8A-1 files (unchanged this pass)

| File | Action | Lines |
|---|---|---|
| `src/capabilities/provenance.py` | Created | ~380 |
| `src/capabilities/import_quarantine.py` | Modified | +25 (provenance integration) |
| `src/capabilities/quarantine_activation_apply.py` | Modified | +40 (provenance integration) |
| `tests/capabilities/test_phase8a_provenance_model.py` | Created | ~170 |
| `tests/capabilities/test_phase8a_tree_hash.py` | Created | ~250 |
| `tests/capabilities/test_phase8a_trust_policy.py` | Created | ~220 |
| `tests/capabilities/test_phase8a_provenance_integration.py` | Created | ~430 |
| `tests/capabilities/test_phase8a_state_model_invariants.py` | Created | ~370 |

## 14. New Test Categories Added (this hardening pass)

| Category | Tests | Purpose |
|---|---|---|
| `TestInvalidEnumHandling` | 7 | Invalid enum values at model and policy layer |
| `TestSerializationEdgeCases` | 5 | Special characters, nulls, full round-trip, JSON validity |
| `TestWriteCorruptionResilience` | 5 | Partial/corrupt file recovery, overwrite, directory-not-file |
| `TestTrustPolicyHardening` | 9 | Exhaustive can_retrieve, non-mutation, exhaustive edge cases |

## 15. Hard Constraints Verified (Final)

- [x] No `run_capability` anywhere
- [x] No script execution
- [x] No Python import from capability/package files
- [x] No subprocess
- [x] No network
- [x] No remote registry
- [x] No signature verification yet
- [x] No stable promotion change
- [x] No required provenance for legacy capabilities
- [x] No retrieval behavior change
- [x] No Brain / TaskRuntime / StateView behavior change
- [x] No dynamic agent behavior change
- [x] CapabilityTrustPolicy is purely analytical — never gates any path
- [x] Fail-closed at both integration points (import, activation)
- [x] Phase 8B not implemented
- [x] All 2,472 tests pass with 0 failures
