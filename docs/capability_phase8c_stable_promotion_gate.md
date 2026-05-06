# Phase 8C-1: Stable Promotion Trust Gate Wiring

Feature-gated provenance/trust/integrity checks for testing → stable promotion.

## Status

Implemented. Off by default. No runtime behavior change.

## Feature Flag

```toml
[capabilities]
stable_promotion_trust_gate_enabled = false  # default
```

When `false` (default): all lifecycle behavior unchanged.
When `true`: testing → stable transitions are gated by `CapabilityTrustPolicy.can_promote_to_stable`.

## Integration Point

`CapabilityLifecycleManager.apply_transition()`, between policy check (step 3) and snapshot write (step 4).

Only triggers when:
- `trust_gate_enabled = True`
- `trust_policy` is not None
- `from_maturity == "testing"` and `to_maturity == "stable"`

## Trust Gate Rules

### Hard blocks (any risk)

| Condition | Code |
|---|---|
| Integrity mismatch | `stable_integrity_mismatch` |
| Invalid signature | `stable_signature_invalid` |
| Untrusted/unknown trust level | `stable_trust_insufficient` |

### Risk-specific rules

| Risk | No provenance | reviewed | trusted_local | trusted_signed |
|---|---|---|---|---|
| Low | Warn, allow | Allow (warn) | Allow | Allow |
| Medium | Deny | Allow (warn) | Allow | Allow |
| High | Deny | Deny | Allow* | Allow* |

*High risk also requires explicit owner approval (checked by `CapabilityPolicy.validate_promote`).

## No Mutation on Denial

When the trust gate blocks promotion:
- Manifest is not modified
- No version snapshot is written
- No index update occurs
- `TransitionResult.applied = False`
- Trust gate decision included in `policy_decisions` with `source: "CapabilityTrustPolicy"`

## Files Changed

| File | Change |
|---|---|
| `config.toml` | Added `stable_promotion_trust_gate_enabled = false` |
| `src/config/settings.py` | Added setting field and env map entry |
| `config/settings.py` | Added backward-compat constant |
| `src/capabilities/provenance.py` | Extended `can_promote_to_stable` with `risk_level` parameter and risk-specific logic |
| `src/capabilities/lifecycle.py` | Added `trust_policy`/`trust_gate_enabled` params; wired trust gate in `apply_transition` |
| `src/app/container.py` | Passes `CapabilityTrustPolicy` to `LifecycleManager` when flag enabled |

## Hard Constraints Maintained

- No `run_capability`
- No script execution during promotion
- No crypto verification
- No network
- No remote registry
- No retrieval behavior change
- No Brain/TaskRuntime/StateView change
- No provenance/signature mutation during promotion
- Denied promotion is atomic (no partial mutation)
