# Phase 8B-3: Trust Root Operator Tools Design

**Date:** 2026-05-04
**Status:** Implemented (pre-hardening)

## Scope

Operator-only tools for managing local trust root metadata. Read-only inspection plus explicit add/disable/revoke. No crypto verification. No network. No trust elevation.

## Feature Flag

```
capabilities.trust_root_tools_enabled = false  (default)
```

Requires `capabilities.enabled = true`.

## Permission Model

```
CAPABILITY_TRUST_OPERATOR_PROFILE
  name: "capability_trust_operator"
  capabilities: {"capability_trust_operator"}
```

Not granted to any other profile (standard, default, chat, local_execution, browser, identity, import, lifecycle, curator, candidate).

## Tools

### 1. list_capability_trust_roots

| Field | Value |
|-------|-------|
| Capability | `capability_trust_operator` |
| Risk | low |
| Mutates | no |

**Inputs:**
- `status` (optional): `"active"` | `"disabled"` | `"revoked"`
- `scope` (optional): arbitrary string
- `include_expired` (bool, default true)
- `limit` (int, default 50, max 200)

**Returns:** Compact summaries — never includes private key fields or secret material.

**Summary fields:** `trust_root_id`, `name`, `key_type`, `public_key_fingerprint`, `owner`, `scope`, `status`, `created_at`, `expires_at`, `is_active`

### 2. view_capability_trust_root

| Field | Value |
|-------|-------|
| Capability | `capability_trust_operator` |
| Risk | low |
| Mutates | no |

**Inputs:**
- `trust_root_id` (required)

**Returns:** Full metadata including `is_active` (respects status + expiry), `metadata` dict. Never includes private key fields.

### 3. add_capability_trust_root

| Field | Value |
|-------|-------|
| Capability | `capability_trust_operator` |
| Risk | medium |
| Mutates | yes (writes one JSON file) |

**Inputs:**
- `trust_root_id`, `name`, `key_type`, `public_key_fingerprint` (required)
- `owner`, `scope`, `expires_at`, `metadata` (optional)

**Behavior:**
- Creates metadata only — `status` defaults to `active`
- Rejects duplicate `trust_root_id`
- Rejects private key / secret material (PEM blocks, API key patterns)
- Rejects path traversal IDs (`/`, `\`, `..`)
- No crypto verification, no network, no provenance change

### 4. disable_capability_trust_root

| Field | Value |
|-------|-------|
| Capability | `capability_trust_operator` |
| Risk | medium |
| Mutates | yes (status metadata only) |

**Inputs:**
- `trust_root_id` (required)
- `reason` (optional)

**Behavior:**
- `status: active` or `status: disabled` → `disabled`
- `status: revoked` → rejected (`already_revoked`)
- Writes status metadata only, does not delete file
- Does not change any capability provenance

### 5. revoke_capability_trust_root

| Field | Value |
|-------|-------|
| Capability | `capability_trust_operator` |
| Risk | high |
| Mutates | yes (status metadata only) |

**Inputs:**
- `trust_root_id` (required)
- `reason` (required)

**Behavior:**
- `status` → `revoked`
- Writes status + reason metadata only, does not delete file
- Does not change any capability provenance

## Storage

Trust roots stored as JSON in `<CAPABILITIES_DATA_DIR>/trust_roots/<id>.json`. Managed by `TrustRootStore` (Phase 8B-2).

## Forbidden Tools

The following are explicitly NOT registered and must never be:
- `verify_capability_signature`
- `trust_capability_signature`
- `mark_capability_trusted_signed`
- `fetch_trust_root`
- `import_remote_trust_root`
- `run_capability`

## Hard Constraints

- No real cryptographic verification
- No crypto dependency
- No network / remote registry
- No `signature_status=verified`
- No `trusted_signed` elevation
- No capability provenance mutation
- No retrieval/lifecycle/runtime behavior change
- No `run_capability`

## Container Wiring

```python
if CAPABILITIES_ENABLED:
    # ... existing capability wiring ...
    if CAPABILITIES_TRUST_ROOT_TOOLS_ENABLED:
        from src.capabilities.trust_roots import TrustRootStore
        from src.tools.capability_tools import register_capability_trust_root_tools

        trust_root_store = TrustRootStore(data_dir=CAPABILITIES_DATA_DIR)
        self.brain._trust_root_store = trust_root_store
        register_capability_trust_root_tools(
            self.brain.tool_registry,
            trust_root_store,
        )
```

## Test Structure

| File | Purpose | Tests |
|------|---------|-------|
| `test_phase8b_trust_root_tools.py` | Registration, tool behaviour, edge cases | TBD |
| `test_phase8b_trust_root_operator_profile.py` | Profile gating, permission denial | TBD |
| `test_phase8b_trust_root_tools_safety.py` | Secret rejection, path safety, no-crypto/network, no-elevation | TBD |
