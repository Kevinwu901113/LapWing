# Post-Phase-8 Trust / Promotion Security Audit

## Overview

This document audits the complete trusted capability lifecycle implemented across Phases 7A through 8C. It covers provenance, trust levels, integrity verification, signature metadata, trust roots, stable promotion trust gates, and the control surfaces exposed through lifecycle transitions.

**Audit date**: 2026-05-05
**Audit scope**: `src/capabilities/` — provenance, signature, trust roots, lifecycle trust gate
**Excluded**: runtime execution, agent binding, task dispatching, Brain internals

---

## Full Lifecycle Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     TRUSTED CAPABILITY LIFECYCLE                         │
│                                                                          │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐           │
│  │ EXTERNAL │───▶│QUARANTINE│───▶│ TESTING  │───▶│  STABLE  │           │
│  │ PACKAGE  │    │ (draft)  │    │ (active) │    │ (active) │           │
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘           │
│       │               │               │               │                  │
│       ▼               ▼               ▼               ▼                  │
│  inspect        import           activate        promote                  │
│  (dry run)   provenance.A     provenance.B    trust gate                 │
│              trust=untrusted  trust=reviewed  trust≥reviewed             │
│              integrity=       integrity=      integrity=verified         │
│                verified         verified       eval passed              │
│              source=          source=         policy gated               │
│                local_package    quarantine_    risk-specific             │
│                                 activation                                │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ QUARANTINE PIPELINE                                               │   │
│  │                                                                    │   │
│  │  audit ──▶ review ──▶ transition request ──▶ activation plan     │   │
│  │                                            ──▶ activation apply  │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ STABLE PROMOTION GATE                                             │   │
│  │                                                                    │   │
│  │  testing ──▶ [Policy] ──▶ [TrustPolicy] ──▶ [Snapshot] ──▶ stable│   │
│  │                  │              │                                  │   │
│  │                  ▼              ▼                                  │   │
│  │              deny if        deny if:                               │   │
│  │              policy fails   - integrity mismatch                  │   │
│  │                             - invalid signature                   │   │
│  │                             - trust insufficient for risk level   │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Phase 8 Components

### 8A-1: Provenance Foundation (`provenance.py`)
- **CapabilityProvenance**: Dataclass with provenance_id, capability_id, source_type, trust_level, integrity_status, signature_status, parent_provenance_id, lineage tracking
- **Tree hash**: Deterministic SHA256 over directory tree, excludes volatile artifacts (evals, traces, versions, quarantine artifacts), normalizes manifest.json computed fields
- **TrustDecision**: Analytical-only structured decision (allowed, severity, code, message, details) — never gates on its own
- **CapabilityTrustPolicy**: Pure analytical functions: `evaluate_provenance()`, `can_activate_from_quarantine()`, `can_retrieve()`, `can_promote_to_stable()`

### 8B-1: Signature Metadata (`signature.py`)
- **CapabilitySignature**: Metadata container (algorithm, key_id, signer, signature hash, trust_root_id) — no cryptographic verification
- **CapabilityTrustRoot**: Local trust root configuration (name, key material hash, status, operator)
- **verify_signature_stub()**: Deterministic stub that NEVER returns "verified" or "trusted_signed" — always returns "present_unverified" at best
- **Secret detection**: Rejects private key material, secret field names, API key patterns in signature data

### 8B-2: Trust Root Store (`trust_roots.py`)
- **TrustRootStore**: Local filesystem store for trust root metadata
- All trust roots are metadata-only — no key material stored
- Validates trust root IDs against filesystem-safe constraints

### 8C-1: Stable Promotion Trust Gate (`lifecycle.py`)
- Feature flag: `trust_gate_enabled` (default: False)
- Only applies to `testing -> stable` transitions
- Requires `trust_policy is not None` and `trust_gate_enabled is True`
- Trust decision is appended to `policy_decisions` in TransitionResult
- Denial is atomic — no manifest, provenance, index, or snapshot mutation occurs

---

## Stable Promotion Trust Gate Semantics

### Gating order
1. CapabilityPolicy.validate_promote() — existing policy gates (risk, approval, eval)
2. CapabilityTrustPolicy.can_promote_to_stable() — trust gate (only if enabled)
3. Version snapshot creation
4. Manifest mutation
5. Index refresh

### Risk-specific rules (from `can_promote_to_stable()`)

| Condition | Low Risk | Medium Risk | High Risk |
|-----------|----------|-------------|-----------|
| No provenance | Warn, allow | Deny | Deny |
| trust=untrusted | Deny | Deny | Deny |
| trust=unknown | Deny | Deny | Deny |
| trust=reviewed | Warn, allow | Warn, allow | **Deny** |
| trust=trusted_local | Allow | Allow | Allow |
| trust=trusted_signed | Allow | Allow | Allow |
| integrity=mismatch | Deny | Deny | Deny |
| signature=invalid | Deny | Deny | Deny |

---

## Risk Matrix

| Risk | Scenario | Impact | Likelihood | Mitigation |
|------|----------|--------|------------|------------|
| Unauthorized stable promotion | Malicious capability bypasses trust gate | High | Low | Trust gate + policy + approval required |
| Provenance forgery | Attacker writes fake provenance.json | Medium | Low | Integrity verified via tree hash; trust gate checks integrity_status |
| Trust level escalation | reviewed provenance manually changed to trusted_local | High | Low | No tool to change trust level; provenance write only at import/activation |
| Integrity bypass | Capability mutated after provenance write | Medium | Low | Tree hash comparison on trust gate check |
| Flag-off bypass | Trust gate disabled, untrusted capability reaches stable | Medium | Medium | Flag defaults to False; explicit operator action needed |
| Replay attack | Old provenance replayed to new capability | Low | Low | Tree hash tied to specific capability content |

---

## Source-Type Matrix

| Source Type | When Written | Trust Level | Notes |
|-------------|-------------|-------------|-------|
| `local_package` | Import to quarantine | untrusted | Written by `import_capability_package()` |
| `quarantine_activation` | Activation to testing | reviewed (if review+audit passed) or untrusted | Written by `apply_quarantine_activation()` |
| `manual_draft` | Manual creation | untrusted | Not yet implemented in creation path |
| `curator_proposal` | Curator proposes | untrusted | Placeholder |
| `unknown` | Fallback | untrusted | Default when source cannot be determined |

---

## Trust Level Matrix

| Trust Level | Meaning | How Achieved | Required For |
|-------------|---------|--------------|--------------|
| `untrusted` | No trust established | Default on import | Nothing — blocked from stable |
| `unknown` | Unknown provenance | Fallback | Nothing — blocked from stable |
| `reviewed` | Human review completed | Review + audit passed during activation | Low/medium risk stable |
| `trusted_local` | Local trust root verified | Operator explicitly sets | High risk stable |
| `trusted_signed` | Cryptographic signature verified | Not yet implemented (verifier stub) | High risk stable (future) |

---

## Integrity / Signature Decision Matrix

| Integrity Status | Signature Status | Trust Gate Result |
|-----------------|------------------|-------------------|
| verified | not_present | Check trust level |
| verified | present_unverified | Check trust level |
| verified | verified | Check trust level *(never occurs — stub never returns verified)* |
| mismatch | any | **Deny** (hard block) |
| unknown | any | Check trust level |
| any | invalid | **Deny** (hard block) |

---

## Feature Flag Matrix

| Flag | Default | Scope | Effect |
|------|---------|-------|--------|
| `trust_gate_enabled` | False | LifecycleManager constructor | Enables trust policy check on testing→stable |
| `trust_policy` | None | LifecycleManager constructor | Trust policy instance; None = gate skipped even if flag is True |

---

## Mutation Path Matrix

| Operation | Manifest | Provenance | Index | Version Snapshot | Activation Report |
|-----------|----------|------------|-------|-----------------|-------------------|
| Import | Write (quarantine) | Write | No | No | Write (import_report.json) |
| Audit | No | No | No | No | Write (audit report) |
| Review | No | No | No | No | Write (review) |
| Transition request | No | No | No | No | Write (request) |
| Activation plan | No | No | No | No | Write (plan) |
| Activation apply | Write (target) | Write (target) | Upsert (target) | No | Write (activation report) |
| Promote to stable (allowed) | Write | **No** | Upsert | Write | No |
| Promote to stable (denied) | **No** | **No** | **No** | **No** | **No** |

---

## No-Execution Proof

All code paths in the capability lifecycle operate on filesystem artifacts only:
- `import_capability_package()`: Copies files, parses YAML/JSON, computes hashes. No `exec()`, `subprocess`, `os.system()`, or `eval()`.
- `audit_quarantined_capability()`: Static analysis of CAPABILITY.md, manifest.json. No execution.
- `apply_quarantine_activation()`: `shutil.copytree()` with a whitelist. No script execution.
- `CapabilityLifecycleManager.apply_transition()`: Manifest field mutation, JSON write, index upsert. No execution.
- `CapabilityTrustPolicy.can_promote_to_stable()`: Pure function — string comparisons against constant sets.
- Trace summary, curator, proposal: All operate on trace/session data structures, never execute.

Confirmed by grep: no `exec(`, `eval(`, `subprocess`, `os.system` in `src/capabilities/`.

---

## No-Crypto / No-Network Proof

### Crypto
- `provenance.py`: Uses `hashlib.sha256` for content hashing only — no signing, no verification
- `signature.py`: `verify_signature_stub()` explicitly returns `present_unverified` in all code paths. Docstring states: "No real cryptographic verification."
- `signature.py`: No imports of `cryptography`, `rsa`, `ecdsa`, `ed25519`, `OpenSSL`, `ssl`
- `hashing.py`: `compute_content_hash()` uses `hashlib.sha256` for manifest content integrity — not cryptographic signatures

### Network
- `import_quarantine.py`: Only accepts local filesystem paths — rejects URLs, network paths, path traversal
- Zero `requests`, `urllib`, `socket`, `http` imports across `src/capabilities/`
- Zero `git clone`, `git import`, URL-based import paths

### Verifier Stub Confirmation
`verify_signature_stub()` in `signature.py` line 427-610:
- Signature verification result status: always `"present_unverified"` (best case) or `"not_present"`/`"invalid"`
- `verified` is documented as "NOT verified — real crypto not implemented"
- No code path returns `signature_status="verified"` or `trust_level="trusted_signed"`

---

## Retrieval / StateView Non-Impact Proof

- `CapabilityTrustPolicy.can_retrieve()` at `provenance.py:526-559`: **Always returns** `TrustDecision.allow()` or `TrustDecision.warn()`. Never denies retrieval.
- `CapabilityRetriever` in `retriever.py`: Composes summaries from manifest + trace data. Does not gate on trust, provenance, or signature.
- `StateViewBuilder`: Receives precomputed summaries — does not directly access capability files.
- No capability module is imported by Brain, TaskRuntime, or ToolDispatcher internals.
- `src/tools/capability_tools.py`: Imports capability modules for tool implementations — these are operator-invoked, not automatic.

---

## Legacy Compatibility

| Scenario | Trust Gate Enabled | Result |
|----------|-------------------|--------|
| Low-risk testing → stable, no provenance | True | Warn + allow (legacy exception) |
| Low-risk testing → stable, no provenance | False | Allow (old behavior) |
| Medium-risk testing → stable, no provenance | True | Deny |
| High-risk testing → stable, no provenance | True | Deny |
| Any risk, untrusted provenance | True | Deny |
| Any risk, integrity mismatch | True | Deny |
| Any risk, invalid signature | True | Deny |

---

## Remaining Risks

1. **No cryptographic verification**: `trusted_signed` trust level is aspirational. The verifier stub never returns verified. No path to achieve `trusted_signed` exists in the system.

2. **Manual trust level mutation**: While no tool writes `trusted_local` automatically, an operator with filesystem access could manually edit provenance.json to set trust_level=trusted_local. There is no signature chaining to prevent this.

3. **Trust root store is metadata-only**: Trust roots are not used to verify anything. They store configuration (name, status, operator) but the verifier stub does not consume them.

4. **Flag defaults to off**: `trust_gate_enabled` defaults to `False`. Until explicitly enabled, the trust gate provides no protection.

5. **No integrity re-verification on promotion**: The trust gate checks integrity_status from provenance.json but does not re-compute the tree hash to verify it. If provenance.json is manually edited to set integrity=verified after content was tampered, the gate would not detect it.

6. **High-risk activation blocked**: Phase 7D-B blocks all high-risk quarantine activations. High-risk capabilities cannot reach testing through the quarantine pipeline, which limits test coverage of the full end-to-end flow.

---

## Operational Recommendations

1. **Enable trust gate in production**: Set `trust_gate_enabled=True` in the LifecycleManager constructor to activate stable promotion gating.

2. **Monitor trust gate denials**: Log and alert on `TrustDecision.deny()` calls in the stable promotion path. Unexpected denials may indicate tampering.

3. **Implement integrity re-verification**: Before stable promotion, re-compute the tree hash and compare against provenance.source_content_hash, updating integrity_status accordingly. This closes the risk of manual provenance tampering.

4. **Implement signature verification**: When ready, implement real cryptographic signature verification in `verify_signature_stub()` (or replace it). Connect trust roots to verification keys.

5. **Add trust level transition tool**: Create an explicit operator tool to elevate trust level from `reviewed` to `trusted_local`, with audit logging and approval requirements.

6. **Resolve high-risk activation**: Implement the human approval model referenced in Phase 7D-B to allow high-risk capabilities to proceed through quarantine activation.

7. **Automate tree hash re-computation**: On every lifecycle transition, re-compute the tree hash and compare against the stored content hash. Update provenance integrity_status accordingly.
