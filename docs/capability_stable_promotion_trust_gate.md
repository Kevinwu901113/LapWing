# Capability Stable Promotion Trust Gate

Phase 8C-0: Design lock. Phase 8C-1: Wired behind feature flag.

**Status: Implemented (Phase 8C-1).** Feature flag `capabilities.stable_promotion_trust_gate_enabled`
defaults to `false`. When `true`, `CapabilityTrustPolicy.can_promote_to_stable` is called
from `LifecycleManager.apply_transition` for `testing → stable` transitions only.
No crypto. No network. No behavior changes when flag is `false`.

## 1. Purpose

Gate `testing` → `stable` capability promotion behind provenance, integrity, and
trust checks. The gate is wired into `CapabilityLifecycleManager.apply_transition()`
and runs after the policy check and before the snapshot write.

## 2. Prerequisites for testing → stable promotion

The future gate requires ALL of the following:

| #  | Requirement                                    | Category        |
|----|------------------------------------------------|-----------------|
| 1  | Capability maturity is `testing`.             | Lifecycle       |
| 2  | Capability status is `active`.                | Lifecycle       |
| 3  | `CapabilityEvaluator` passes.                 | Evaluation      |
| 4  | Latest `EvalRecord` passes.                   | Evaluation      |
| 5  | `CapabilityPolicy` allows promotion.          | Policy          |
| 6  | Provenance exists OR legacy/manual exception. | Provenance      |
| 7  | Integrity status is `verified`.               | Integrity       |
| 8  | Trust level meets risk-specific minimum.      | Trust           |
| 9  | High risk requires explicit owner approval.   | Approval        |
| 10 | Invalid integrity blocks promotion.           | Integrity       |
| 11 | Invalid signature blocks promotion.           | Signature       |
| 12 | `trusted_signed` is necessary, not sufficient.| Trust           |
| 13 | Promotion remains operator/lifecycle-gated.   | Operational     |
| 14 | Promotion must not execute scripts.           | Safety          |
| 15 | Promotion must not grant permissions.         | Safety          |
| 16 | Promotion must not bypass RuntimeProfile.     | Safety          |
| 17 | Promotion must not change retrieval filtering.| Retrieval       |
| 18 | Missing provenance warns for legacy, blocks   | Provenance      |
|    | external imports from stable unless overridden.|                 |

## 3. Risk-specific rules

### Low risk

| Rule                        | Requirement                                              |
|-----------------------------|----------------------------------------------------------|
| Minimum trust level         | `reviewed`                                               |
| Integrity                   | `verified` required                                      |
| Eval                        | Passing eval required                                    |
| Legacy/manual no provenance | Warn, do not block                                       |
| Owner approval              | Not required by default                                  |

### Medium risk

| Rule                        | Requirement                                              |
|-----------------------------|----------------------------------------------------------|
| Minimum trust level         | `reviewed` or `trusted_local`                            |
| Integrity                   | `verified` required                                      |
| Eval                        | Passing eval required                                    |
| Owner approval              | May be required depending on policy configuration        |
| Signature invalid           | Deny                                                     |
| Integrity mismatch          | Deny                                                     |

### High risk

| Rule                        | Requirement                                              |
|-----------------------------|----------------------------------------------------------|
| Minimum trust level         | `trusted_local` or future `trusted_signed`               |
| Integrity                   | `verified` required                                      |
| Eval                        | Passing eval required                                    |
| Owner approval              | Explicitly required                                      |
| Signature invalid           | Deny                                                     |
| Integrity mismatch          | Deny                                                     |
| Auto-stable                 | Never; must be operator-gated                            |

## 4. State distinctions

These distinctions MUST hold in all implementations:

| Distinction                              | Meaning                                                                 |
|------------------------------------------|-------------------------------------------------------------------------|
| `active/testing != stable`               | Being in testing with active status does not confer stable privileges. |
| `reviewed != trusted_signed`             | Human review is not cryptographic trust.                               |
| `trusted_local != trusted_signed`        | Local operator trust is not remote signer trust.                       |
| `signature_status=present_unverified != verified` | Having a signature is not the same as verifying it.           |
| `trust root active != signature verified`| An active trust root does not mean any capability was signed by it.    |
| `approval != trust`                      | Owner approval is a policy decision, not a trust assessment.           |
| `eval pass != trust`                     | A passing evaluator run does not establish provenance trust.           |
| `trust != permission`                    | Trust level does not grant tool permissions or runtime capabilities.   |
| `stable != executable`                   | Stable maturity is a lifecycle label, not an execution contract.       |
| `stable != run_capability`               | There is no `run_capability` function. Stable does not mean runnable.  |

## 5. Provenance requirements by source

| Source type              | Provenance required for stable? | Behavior                                   |
|--------------------------|--------------------------------|---------------------------------------------|
| `local_package`          | Yes                            | Block if missing unless legacy exception.   |
| `manual_draft`           | No (legacy exception)          | Warn but allow with operator acknowledgment.|
| `curator_proposal`       | Yes                            | Block if missing.                           |
| `quarantine_activation`  | Yes                            | Block if missing.                           |
| `unknown`                | Yes                            | Block if missing unless legacy exception.   |
| Legacy (no provenance.json) | No (legacy exception)       | Warn; allows stable for manual_draft.       |
| External import           | Yes                           | Block if missing unless explicit override.  |

## 6. What the gate does NOT do

- It does not execute scripts or `run_capability`.
- It does not grant permissions or register tools.
- It does not bypass `RuntimeProfile` or `ToolDispatcher`.
- It does not change retrieval rules except through normal maturity/status filtering.
- It does not automatically promote anything.
- It does not replace operator judgment.

## 7. Current state (Phase 8C-1)

- `CapabilityTrustPolicy.can_promote_to_stable` accepts `risk_level` and `approval` parameters.
- `LifecycleManager.apply_transition` calls `can_promote_to_stable` when `trust_gate_enabled=True`
  and the transition is `testing → stable`.
- `CapabilityPolicy.validate_promote` does NOT consult provenance or trust level.
- The gate is wired but defaults to `false` (off).
- When the flag is `false`, existing behavior is completely unchanged.

## 8. Wiring path (implemented)

```
LifecycleManager.apply_transition(testing -> stable)
  → CapabilityPolicy.validate_promote(manifest, eval, approval)
  → [trust_gate_enabled?] CapabilityTrustPolicy.can_promote_to_stable(manifest, provenance, eval, risk_level, approval)
  → All pass → transition applied
```
