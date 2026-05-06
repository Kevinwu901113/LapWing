# Phase 8B-2: Local Trust Root Store

Phase 8B-2 implements a local filesystem-backed TrustRootStore for managing trust root metadata. No real cryptographic verification. No network. No behavior changes to existing systems.

## TrustRootStore

Storage: `data/capabilities/trust_roots/<trust_root_id>.json`

Uses the existing `CapabilityTrustRoot` dataclass from Phase 8B-1 (`signature.py`). No new data model.

### API

| Method | Returns | Description |
|---|---|---|
| `create_trust_root(trust_root)` | `CapabilityTrustRoot` | Persist a new trust root; auto-populates created_at if empty |
| `get_trust_root(trust_root_id)` | `CapabilityTrustRoot \| None` | Read by id; returns None for missing/corrupt/secret |
| `list_trust_roots(status=None, scope=None)` | `list[CapabilityTrustRoot]` | List all, optionally filtered; skips corrupt files |
| `disable_trust_root(trust_root_id, reason=None)` | `CapabilityTrustRoot \| None` | Set status to disabled |
| `revoke_trust_root(trust_root_id, reason=None)` | `CapabilityTrustRoot \| None` | Set status to revoked |
| `is_trust_root_active(trust_root_id, at_time=None)` | `bool` | True if active and not expired |
| `as_verifier_dict()` | `dict[str, CapabilityTrustRoot]` | All roots as dict for verify_signature_stub |

### Status Semantics

| Method | Behavior |
|---|---|
| `create_trust_root` | Rejects duplicate ids; validates no secrets; atomic write |
| `disable_trust_root` | Changes status only; root remains stored and retrievable |
| `revoke_trust_root` | Changes status only; root remains stored and retrievable |
| `is_trust_root_active` | True only when status=active AND (no expiry OR expiry in future) |
| `get_trust_root` | Returns None for secret-containing files, corrupt JSON, non-dict |

### Safety

- **Path safety**: trust_root_id is validated — no path separators, no `..`, must be a valid filename
- **Atomic writes**: all writes use temp file + `os.replace`
- **Secret rejection**: reuses `_validate_no_secrets` from `signature.py` (field names, PEM markers, API key patterns, length limits)
- **Corrupt files**: return None / silently skipped in list
- **Metadata exempt**: `metadata` dict is exempt from secret scanning

### ID Validation

`_validate_trust_root_id(id)` rejects:
- Empty or whitespace-only
- Path separators (`/`, `\`)
- `..` (traversal)
- Non-filename-safe values (`Path(id).name != id`)

## Verifier Stub Integration

`verify_signature_stub` now accepts a `TrustRootStore` via duck-typing (`as_verifier_dict()` method). No import of `trust_roots.py` from `signature.py`.

### Updated Decision Tree (Phase 8B-2)

```
signature.json exists?
├── No  → not_present, allowed, code="no_signature"
└── Yes → parse
    ├── Unparseable → invalid, code="malformed_signature"
    └── Parseable
        ├── signed_tree_hash missing → invalid, code="missing_tree_hash"
        └── signed_tree_hash present
            ├── Hash mismatch → invalid, code="tree_hash_mismatch"
            └── Hash match
                ├── No trust_root_id → present_unverified, code="hash_consistent_unverified"
                └── trust_root_id present
                    ├── Not in trust_roots → present_unverified, code="unknown_trust_root"
                    ├── trust root disabled → invalid, code="trust_root_disabled"
                    ├── trust root revoked → invalid, code="trust_root_revoked"
                    ├── trust root expired → invalid, code="trust_root_expired"  ← NEW in 8B-2
                    └── trust root active → present_unverified, code="hash_consistent_unverified"
```

Key: expiry enforcement was added in Phase 8B-2. Active but expired trust roots now return `invalid` with code `trust_root_expired`.

## Out of Scope

- No real cryptographic verification
- No crypto dependencies
- No private key storage
- No network / remote registry / keyserver
- No `signature_status=verified`
- No `trusted_signed` recommendation
- No retrieval/lifecycle/runtime behavior changes
- No `run_capability`
- No trust root tools (deferred to later phase)
