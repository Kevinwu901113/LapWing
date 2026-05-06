# Capability Trust State Model

Phase 8A-0 reference. Documents all trust-related state domains, their
valid values, and the invariants that govern them. This is the conceptual
foundation for provenance and signature implementation (future phases).

No runtime behavior. No new tools. Documentation only.

## 1. QuarantineReviewStatus

Defined in `src/capabilities/quarantine_review.py`. Stored in
`quarantine_reviews/<review_id>.json` under each quarantined capability
directory.

| Value | Meaning |
|---|---|
| `needs_changes` | Audit found issues that must be addressed before testing |
| `approved_for_testing` | Review recommends this capability may be considered for testing |
| `rejected` | Capability is rejected; no further action expected |

**Critical invariant:** `approved_for_testing` does NOT imply
`maturity=testing`. It is a review recommendation stored in a separate
JSON file. The capability manifest remains `maturity=draft` and
`status=quarantined` regardless of review status.

`mark_quarantine_review()` is strictly report-only:
- Does NOT change capability status from quarantined
- Does NOT change maturity from draft
- Does NOT call CapabilityLifecycleManager
- Does NOT update the active index
- Does NOT make the capability retrievable by default
- Does NOT make the capability visible in StateView

## 2. QuarantineTransitionRequestStatus

Defined in `src/capabilities/quarantine_transition.py`. Stored in
`quarantine_transition_requests/<request_id>.json`.

| Value | Meaning |
|---|---|
| `pending` | Request is awaiting action |
| `cancelled` | Operator cancelled the request |
| `rejected` | Request was rejected (manual or policy-driven) |
| `superseded` | Request was superseded by activation apply |

**Critical invariant:** A transition request does NOT imply approval.
It is a pure data record that captures intent. It does not:
- Activate or promote quarantined capabilities
- Move files from quarantine to active scopes
- Set status=active or maturity=testing
- Execute scripts or run tests
- Make quarantined capabilities visible in default retrieval/StateView

The `request_quarantine_testing_transition()` function gates on:
- Capability exists in quarantine
- manifest.status == quarantined
- manifest.maturity == draft
- Latest review_status == approved_for_testing
- Latest audit report exists
- Evaluator/policy install checks pass
- No existing pending request for same capability + target scope
- High-risk sets required_approval=true

## 3. QuarantineActivationPlan (allowed/blocked)

Defined in `src/capabilities/quarantine_activation_planner.py`. Stored in
`quarantine_activation_plans/<plan_id>.json`.

The plan has an `allowed` boolean field:
- `allowed=true`: All gates passed. Plan is ready for a future activation
  apply phase.
- `allowed=false`: One or more gates failed. Plan records blocking findings.

**Critical invariants:**
- An activation plan does NOT imply authority to activate. It is a pure
  plan — `plan_quarantine_activation()` always returns `would_activate: false`.
- The plan does NOT change manifest.status, manifest.maturity, or copy files.
- The target is always `status=active`, `maturity=testing` (hardcoded as
  `TARGET_STATUS` and `TARGET_MATURITY`).
- The plan cannot target `maturity=stable`.

Plan gates (12+):
1. Capability exists in quarantine
2. manifest.status == quarantined
3. manifest.maturity == draft
4. Transition request exists (by ID or latest pending)
5. Request status == pending
6. Request target maturity == testing
7. Content hash match between request and current state
8. Review still approved_for_testing
9. Audit still passed / recommends approved_for_testing
10. Target collision check (read-only)
11. Re-run evaluator (must pass)
12. Re-run policy install check (must pass)
13. High risk requires required_approval flag set

## 4. Activation Apply Constraints

Defined in `src/capabilities/quarantine_activation_apply.py`.

**Hardcoded targets:**
```python
TARGET_STATUS = "active"
TARGET_MATURITY = "testing"
```

**Critical invariants:**
- Activation apply can ONLY create `status=active`, `maturity=testing`.
- Activation apply can NEVER create `maturity=stable`.
- Activation apply requires a separate call — a plan alone is insufficient.
  The `apply_quarantine_activation()` function checks that the persisted
  plan's `would_activate` field is NOT `True` (it must be the default
  `False` from the planner), enforcing that plan and apply are separate
  authorities.
- The original quarantine copy remains unchanged (quarantined/draft).
- Activation creates a **copy** in the target scope, normalizes the
  manifest, and writes `extra.origin` metadata.

### Origin metadata written by activation apply

```json
{
  "extra": {
    "origin": {
      "quarantine_capability_id": "<id>",
      "activation_plan_id": "<plan_id>",
      "transition_request_id": "<request_id>",
      "import_source_hash": "<hash>",
      "activated_at": "<ISO timestamp>",
      "activated_by": "<operator>"
    }
  }
}
```

This ensures the active/testing external copy retains traceable origin
metadata back to the quarantine source.

## 5. AgentCandidate Approval State

Defined in `src/agents/spec.py:VALID_APPROVAL_STATES`. Used by
`src/agents/candidate.py:AgentCandidate.approval_state`.

| Value | Meaning |
|---|---|
| `not_required` | Candidate does not need approval (low risk, internal) |
| `pending` | Candidate is awaiting review |
| `approved` | Candidate has been approved for promotion |
| `rejected` | Candidate has been rejected |

Agent candidates are a future-promotion staging area. They are NOT active
agents, do NOT run, and do NOT affect ToolDispatcher. The approval state
is metadata only — no automated promotion is triggered by state changes.

## 6. Provenance Trust Level (IMPLEMENTED — Phase 8A-1)

Defined in `src/capabilities/provenance.py`. Stored in `provenance.json`
alongside each capability's manifest.json.

| Value | Meaning |
|---|---|
| `unknown` | No provenance data exists |
| `untrusted` | Default for legacy and external import; provenance present but not reviewed |
| `reviewed` | Provenance data has been audited/reviewed but not cryptographically verified |
| `trusted_local` | Developer-authored in the local repository |
| `trusted_signed` | Provenance data is cryptographically signed and verified (future) |

Note: The conceptual model had 3 trust levels; implementation added `unknown`
(distinct from untrusted-but-present) and `trusted_local` (developer-authored).

**Critical invariants (enforced):**
- `reviewed` provenance does NOT imply `trusted_signed`. These are distinct
  trust levels with different evidentiary requirements.
- Missing provenance does NOT break legacy capabilities. Capabilities
  without provenance data have no `provenance.json` and default to
  `untrusted` without errors. `read_provenance()` returns None silently.
- Invalid provenance blocks promotion gates when checked (via
  `CapabilityTrustPolicy.can_promote_to_stable()`), but never causes crashes
  in unrelated code paths.
- `trusted_signed` requires both a valid signature AND a verified
  certificate chain. Neither exists yet (deferred to future phases).
- Activation from quarantine sets `trust_level="reviewed"` when both review
  (approved_for_testing) and audit (passed) are satisfied; otherwise
  `"untrusted"`.

## 7. Provenance Integrity Status (IMPLEMENTED — Phase 8A-1)

Defined in `src/capabilities/provenance.py`.

| Value | Meaning |
|---|---|
| `unknown` | No integrity check has been performed |
| `verified` | Content hash matches provenance record |
| `mismatch` | Content hash does not match provenance record |

Note: The conceptual model used `intact`/`tampered`/`unavailable` but
implementation uses `verified`/`mismatch` — neutral operational terms that
avoid implying malicious intent. `unavailable` is represented by
`read_provenance()` returning None rather than a status value.

Integrity is verified at import time (content just copied from source) and
can be re-verified via `verify_content_hash_against_provenance()`.

## 8. Signature Status (IMPLEMENTED — Phase 8A-1)

Defined in `src/capabilities/provenance.py`.

| Value | Meaning |
|---|---|
| `not_present` | No signature present (all capabilities in 8A-1) |
| `present_unverified` | Signature present but not yet verified |
| `verified` | Signature verified against a known public key |
| `invalid` | Signature verification failed |

No signing keys, certificate chains, or verification logic exist yet.
Signature status is recorded and carried through provenance chains
(inherited from quarantine during activation) but never verified.
Actual signature verification is deferred to future phases.

## 9. Trust State Flow (Conceptual)

```
External Import
      │
      ▼
┌──────────────────────────────────────────────┐
│ untrusted / quarantined                       │
│ provenance.json written (8A-1)                │
│ trust_level: untrusted                        │
│ integrity_status: verified                    │
│ signature_status: not_present                 │
└──────────────────┬───────────────────────────┘
                   │ audit + review
                   ▼
┌──────────────────────────────────────────────┐
│ reviewed / quarantined                        │
│ trust_level: untrusted (still)                │
│ integrity_status: verified                    │
│ signature_status: not_present                 │
│ (review/audit are separate JSON files)        │
└──────────────────┬───────────────────────────┘
                   │ transition request + activation plan + apply
                   ▼
┌──────────────────────────────────────────────┐
│ active / testing (with provenance chain)      │
│ provenance.json written (8A-1)                │
│ trust_level: reviewed (if review+audit pass)  │
│ source_type: quarantine_activation            │
│ parent_provenance_id → quarantine provenance  │
│ integrity_status: verified                    │
│ signature_status: inherited from quarantine   │
└──────────────────┬───────────────────────────┘
                   │ stable promotion gate (separate authority)
                   ▼
┌──────────────────────────────────────────────┐
│ active / stable                               │
│ trust_level: trusted_signed (future req)      │
│ integrity_status: verified                    │
│ signature_status: verified (future req)       │
└──────────────────────────────────────────────┘
```

Key: Each horizontal line is a separate authority. No step implicitly
grants the next. The `stable` gate is distinct from the `testing` gate.

## 10. Phase 8A-0 Trust Invariants Summary

1. **reviewed provenance ≠ trusted_signed.** Reviewed means a human or
   automated audit has inspected the provenance data. Trusted signed means
   cryptographic verification has passed. These are separate states.

2. **Missing provenance must not break legacy capabilities.** Capabilities
   without provenance data default to `untrusted` and operate normally.

3. **Invalid provenance blocks only when gates check it.** Tampered
   provenance should cause gate failures in activation/promotion paths
   that explicitly validate provenance, not crashes elsewhere.

4. **External import always starts untrusted/quarantined.** The import
   path forces `status=quarantined`, `maturity=draft`. Provenance defaults
   to `untrusted`.

5. **Active/testing external copy retains origin metadata.** The activation
   apply writes `extra.origin` with full traceability back to the quarantine
   source.

6. **Stable promotion is a separate lifecycle gate.** It is not reachable
   via activation apply. It requires `PromotionPlanner._plan_testing_to_stable()`
   which has its own evaluator + approval requirements.
