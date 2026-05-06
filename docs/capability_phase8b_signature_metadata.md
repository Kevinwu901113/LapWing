# Phase 8B-1: Signature Metadata Design

Phase 8B-1 implements local signature metadata parsing and a deterministic verifier stub. No real cryptographic verification. No network. No behavior changes to existing systems.

## CapabilitySignature

Stored in `signature.json` within the capability directory.

| Field | Type | Required | Description |
|---|---|---|---|
| `algorithm` | `str \| None` | no | Signature algorithm, e.g. "ed25519", "ecdsa-p256-sha256" |
| `key_id` | `str \| None` | no | Identifies which key was used |
| `signer` | `str \| None` | no | Identity of the signer |
| `signature` | `str \| None` | no | Signature bytes, hex-encoded |
| `signed_tree_hash` | `str \| None` | no | The tree hash that was signed |
| `signed_at` | `str \| None` | no | ISO 8601 timestamp of signing |
| `trust_root_id` | `str \| None` | no | Which trust root's key was used |
| `metadata` | `dict` | no | Extensible metadata |

All fields except `metadata` are optional. The minimum valid signature metadata is an empty dict (though `signed_tree_hash=None` will cause the verifier stub to return `invalid`).

## CapabilityTrustRoot

Local-only trust root configuration. No remote registry, no network.

| Field | Type | Required | Description |
|---|---|---|---|
| `trust_root_id` | `str` | **yes** | Unique identifier |
| `name` | `str` | **yes** | Human-readable label |
| `key_type` | `str` | **yes** | Key type, e.g. "ed25519", "ecdsa-p256" |
| `public_key_fingerprint` | `str` | **yes** | SHA256 hex fingerprint of the public key |
| `owner` | `str \| None` | no | Who owns/manages this trust root |
| `scope` | `str \| None` | no | e.g. "global", "project", "user" |
| `status` | `str` | **yes** | `active`, `disabled`, or `revoked` |
| `created_at` | `str` | no | ISO 8601 creation timestamp |
| `expires_at` | `str \| None` | no | ISO 8601 expiry, optional |
| `metadata` | `dict` | no | Extensible metadata |

### Status semantics

| Status | Semantics |
|---|---|
| `active` | Trust root is valid for signature verification |
| `disabled` | Temporarily inactive — verification must fail |
| `revoked` | Permanently revoked — verification must fail |

Invalid status values raise `ValueError` on construction.

## SignatureVerificationResult

Structured output from the verifier stub.

| Field | Type | Description |
|---|---|---|
| `capability_id` | `str` | Capability identifier from manifest.json or directory name |
| `signature_status` | `str` | One of `not_present`, `present_unverified`, `invalid` (never `verified`) |
| `trust_level_recommendation` | `str` | Always `untrusted` in Phase 8B-1 |
| `allowed` | `bool` | Whether the verification passed (does not imply execution) |
| `code` | `str` | Machine-readable result code |
| `message` | `str` | Human-readable explanation |
| `details` | `dict` | Additional context (hashes, trust root info) |

## Verifier Stub Decision Tree

```
signature.json exists?
├── No  → not_present, allowed, code="no_signature"
└── Yes → parse
    ├── Unparseable  → invalid, not allowed, code="malformed_signature"
    └── Parseable
        ├── signed_tree_hash missing  → invalid, code="missing_tree_hash"
        └── signed_tree_hash present
            ├── Hash mismatch  → invalid, code="tree_hash_mismatch"
            └── Hash match
                ├── No trust_root_id  → present_unverified, code="hash_consistent_unverified"
                └── trust_root_id present
                    ├── Not in trust_roots  → present_unverified, code="unknown_trust_root"
                    ├── trust root disabled  → invalid, code="trust_root_disabled"
                    ├── trust root revoked   → invalid, code="trust_root_revoked"
                    └── trust root active    → present_unverified, code="hash_consistent_unverified"
```

Key: even with active trust root and matching hash, the result is `present_unverified` — not `verified`. Real cryptographic verification is not implemented.

## Private Key / Secret Rejection (Hardened)

### Value patterns rejected (case-insensitive)

- `-----BEGIN PRIVATE KEY-----`
- `-----BEGIN RSA PRIVATE KEY-----`
- `-----BEGIN EC PRIVATE KEY-----`
- `-----BEGIN DSA PRIVATE KEY-----`
- `-----BEGIN OPENSSH PRIVATE KEY-----`
- `-----BEGIN ENCRYPTED PRIVATE KEY-----`

### API key / bearer token patterns rejected

- `sk-` prefix (OpenAI-style API keys)
- `sk_` prefix (alternative format)
- `Bearer ` (Authorization header tokens)
- `bearer ` (lowercase variant)

### Secret field names rejected (regardless of value, case-insensitive)

`private_key`, `secret_key`, `api_key`, `password`, `secret`, `passphrase`, `token`, `access_token`, `bearer_token`, `refresh_token`, `client_secret`, `signing_key`, `privatekey`, `secretkey`, `apikey`

### Field length limits

String fields are limited to 1 MiB (1,048,576 bytes). Larger values raise `ValueError`.

### Scope of scanning

- `parse_signature_dict`: scans all keys except `metadata`
- `parse_trust_root_dict`: scans all keys except `metadata`
- `write_signature`: scans all keys except `metadata` via `_validate_no_secrets`
- `read_signature`: returns `None` for any secret-containing or oversized data
- `metadata` dict values are **exempt** from all scanning

## Verifier Stub Decision Tree (Hardened)

```
signature.json exists?
├── No  → not_present, allowed, code="no_signature"
└── Yes → parse
    ├── Unparseable (bad JSON, non-dict, secrets, oversized)
    │   → invalid, not allowed, code="malformed_signature"
    └── Parseable
        ├── signed_tree_hash missing  → invalid, code="missing_tree_hash"
        └── signed_tree_hash present
            ├── Hash mismatch  → invalid, code="tree_hash_mismatch"
            └── Hash match
                ├── No trust_root_id  → present_unverified, code="hash_consistent_unverified"
                └── trust_root_id present
                    ├── Not in trust_roots  → present_unverified, code="unknown_trust_root"
                    ├── trust root disabled  → invalid, code="trust_root_disabled"
                    ├── trust root revoked   → invalid, code="trust_root_revoked"
                    └── trust root active    → present_unverified, code="hash_consistent_unverified"
```

## Tree Hash Exclusion

`signature.json` is excluded from the tree hash (`_TREE_HASH_EXCLUDED_FILES` in `provenance.py`). This prevents self-reference: writing a signature must not change the hash it signs.

## Out of Scope

- No real cryptographic verification
- No crypto dependencies (`cryptography`, `PyNaCl`, etc.)
- No private key storage
- No network / remote registry
- No `signature_status=verified` returned
- No `trusted_signed` recommendation
- No retrieval/lifecycle/runtime behavior changes
- No `run_capability`
