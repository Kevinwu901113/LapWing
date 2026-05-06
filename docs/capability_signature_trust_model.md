# Capability Signature / Trust Root Model

Phase 8B-0: Design lock. Documentation and invariant tests only.
No implementation. No crypto. No network. No behavior changes.

## 1. Signature States

Defined in `src/capabilities/provenance.py` as `PROVENANCE_SIGNATURE_STATUSES`:

| State | Semantics |
|---|---|
| `not_present` | No signature metadata exists for this capability. |
| `present_unverified` | Signature metadata exists but has not been verified locally against a configured trust root. |
| `verified` | Signature metadata exists and was successfully verified against a configured trust root. |
| `invalid` | Signature metadata exists but verification failed (hash mismatch, key mismatch, or malformed signature). |

These are carried on `CapabilityProvenance.signature_status` and stored in `provenance.json`.

Legacy capabilities without `provenance.json` treat `signature_status` as `not_present` on first provenance creation. This is explicitly compatible — nothing breaks.

## 2. Trust Levels

Defined in `src/capabilities/provenance.py` as `PROVENANCE_TRUST_LEVELS`:

| Level | Semantics |
|---|---|
| `unknown` | No trust assessment has been performed. |
| `untrusted` | Explicitly untrusted — default for newly imported capabilities. |
| `reviewed` | A human curator has reviewed the capability. Does NOT imply signature verification. |
| `trusted_local` | Trusted based on local review/audit. Does NOT imply signature verification. |
| `trusted_signed` | Trusted based on verified cryptographic signature from a configured trust root. |

### Key relationships between trust_level and signature_status

- **`reviewed != trusted_signed`**: A human review is not a cryptographic signature. These are distinct trust dimensions.
- **`trusted_local != trusted_signed`**: Local trust (e.g., operator-approved, internally audited) is not the same as signed trust from a trust root.
- **`trusted_signed` requires `signature_status == verified`**: You cannot reach `trusted_signed` without a verified signature.
- **`signature_status == verified` may allow `trusted_signed` only if the signer identity is trusted**: Verification alone is not sufficient; the signer must match a configured, active trust root.
- **`signature_status == invalid` must block promotion to `trusted_signed`**: An invalid signature is a hard block on signed trust.

### Trust level promotion rules (future)

```
not_present       → trusted_signed: BLOCKED (nothing to verify)
present_unverified → trusted_signed: BLOCKED (must verify first)
invalid           → trusted_signed: BLOCKED (verification failed)
verified          → trusted_signed: ALLOWED only if signer identity is trusted
```

### Trust level does NOT imply execution

`trusted_signed` is a trust assessment, not an execution contract. Specifically:

- `trusted_signed` does not imply executable.
- `trusted_signed` does not imply stable maturity.
- `trusted_signed` does not bypass policy/evaluator/lifecycle gates.
- `trusted_signed` does not grant permissions.
- `trusted_signed` does not bypass owner/operator approval.
- `trusted_signed` is necessary but not sufficient for future high-trust flows.

## 3. Future Signature Metadata Shape

Documented for design reference only. No implementation yet.

These fields will live on `CapabilityProvenance` (or a nested `signature` object) when signature support is implemented:

```
signature:
  algorithm: str | None          # e.g. "ed25519", "ecdsa-p256-sha256"
  key_id: str | None             # Identifies which key was used
  signer: str | None             # Identity of the signer (e.g. "lapwing-core")
  signature: str | None          # The signature bytes, hex-encoded
  signed_tree_hash: str | None   # The tree hash that was signed
  signed_at: str | None          # ISO 8601 timestamp of signing
  trust_root_id: str | None      # Which trust root's key was used
```

None of these fields exist yet. The shape is documented to constrain future implementation.

## 4. Trust Root Model

Future local-only trust roots. No remote fetching. No network. No registry lookup.

```
CapabilityTrustRoot:
  trust_root_id: str             # Unique identifier
  name: str                      # Human-readable label
  key_type: str                  # e.g. "ed25519", "ecdsa-p256"
  public_key_fingerprint: str    # SHA256 hex fingerprint of the public key
  owner: str                     # Who owns/manages this trust root
  scope: str                     # e.g. "global", "project", "user"
  status: str                    # "active" | "disabled" | "revoked"
  created_at: str                # ISO 8601
  expires_at: str | None         # ISO 8601, optional expiry
  metadata: dict[str, Any]       # Extensible metadata
```

### Trust root status semantics

| Status | Semantics |
|---|---|
| `active` | Trust root is valid for signature verification. |
| `disabled` | Trust root is temporarily inactive. Signatures from this root must not verify. |
| `revoked` | Trust root is permanently revoked. Signatures from this root must not verify. |

### Hard constraints

- Trust roots are stored **locally only** (no remote registry, no network fetch).
- Trust roots are **configuration**, not capability data.
- A signature is verified when: (a) the trust root is `active`, (b) the key matches, (c) the signed tree hash matches the computed tree hash, and (d) the signature is cryptographically valid.
- `disabled` and `revoked` trust roots must NOT verify signatures, even if the key and signature are otherwise valid.

## 5. Future Intended Flow

```
capability directory
  → compute_capability_tree_hash()        (deterministic, local, content-only)
  → sign tree hash with private key       (offline / external; not in Lapwing)
  → store signature metadata in provenance.json

On verification (future):
  capability directory
    → compute_capability_tree_hash()      (same deterministic algorithm)
    → load signature metadata from provenance.json
    → load configured CapabilityTrustRoot (local only)
    → verify:
        - trust root status == active
        - signed_tree_hash == computed tree hash
        - cryptographic signature is valid for public key
    → set signature_status = verified | invalid
    → if verified AND signer identity is trusted:
        trust_level may become trusted_signed
    → still requires policy/evaluator/lifecycle approval
    → still no execution unless a separate future runtime contract exists
```

### What signature verification operates on

- **Content hash**, not runtime behavior. Verification hashes files, never executes them.
- The **deterministic tree hash** (`compute_capability_tree_hash`), not `manifest.content_hash` alone. The tree hash covers the full directory content (CAPABILITY.md, manifest.json, scripts/, tests/, examples/), making it a stronger binding.
- **Volatile reports are excluded** from the tree hash (evals/, traces/, versions/, quarantine artifacts). Changing these does NOT affect the signed hash.
- **Included content changes DO affect the signed hash**. Modifying CAPABILITY.md, manifest.json fields (except computed fields), scripts/, tests/, or examples/ changes the tree hash and invalidates any prior signature.

## 6. Out of Scope (explicitly)

- **No crypto dependency.** No `cryptography`, `PyNaCl`, or similar packages.
- **No signature verification implementation.** The `signature_status` field exists but no code verifies signatures.
- **No network.** Trust roots are local configuration only.
- **No remote registry.** No trust root fetching from external sources.
- **No `run_capability`.** No execution of capability code.
- **No retrieval behavior change.**
- **No lifecycle behavior change.**
- **No stable promotion behavior change.**
- **No Brain/TaskRuntime/StateView behavior change.**
- **No trust policy behavior change.** `CapabilityTrustPolicy` remains analytical.

## 7. Invariant Summary

These invariants are tested in `tests/capabilities/test_phase8b_signature_trust_model_invariants.py`:

### Trust level vs signature status
- `reviewed` does not imply `trusted_signed`.
- `trusted_local` does not imply `trusted_signed`.
- `trusted_signed` requires `signature_status == verified`.
- `signature_status == present_unverified` does not imply `trusted_signed`.
- `signature_status == invalid` blocks signed trust.
- `signature_status == not_present` cannot produce `trusted_signed`.

### Legacy compatibility
- Missing signature metadata must not break legacy capabilities (no provenance.json → not_present).

### Trust root model
- Local trust root model must be local-only (no network imports).
- `disabled`/`revoked` trust roots must not verify signatures.
- Remote registry trust is explicitly out of scope.
- Network verification is out of scope.

### Verification properties
- Signature verification must be deterministic and local when implemented.
- Signature verification must not execute capability code.
- Signature verification must hash content, not run content.

### Tree hash binding
- Changing volatile reports must not affect signed tree hash.
- Changing included content must affect signed tree hash.
- Signed hash must bind to deterministic tree hash, not `manifest.content_hash` alone.

### Trust level does not imply authority
- `trusted_signed` does not imply executable.
- `trusted_signed` does not imply stable.
- `trusted_signed` does not bypass policy/evaluator/lifecycle gates.
- `trusted_signed` does not grant permissions.
- `trusted_signed` does not bypass owner/operator approval.

### Future gates
- `invalid` signature should block future activation/promotion gates when wired.
- `trusted_signed` should be necessary but not sufficient for future high-trust flows.
