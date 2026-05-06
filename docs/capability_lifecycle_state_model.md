# Capability Lifecycle State Model

Phase 8A-0 reference. Describes all state domains, legal transitions, and
invariants for the capability lifecycle. No runtime behavior — documentation
only.

## 1. CapabilityStatus

Defined in `src/capabilities/schema.py:CapabilityStatus` (str enum).

| Value | Meaning |
|---|---|
| `active` | Capability is live and discoverable via normal retrieval |
| `disabled` | Capability is present but blocked from execution/retrieval |
| `archived` | Terminal state; no transitions out (restore not implemented) |
| `quarantined` | Capability is isolated pending audit/review; excluded from default retrieval |

Invariants:
- `quarantined` does not imply `active`. A quarantined capability has
  status=quarantined, not status=active. No quarantine operation (review,
  transition request, activation plan) changes status from quarantined
  until an explicit activation apply copies it to a target scope.
- `disabled` and `archived` are terminal for promotion purposes. The
  lifecycle manager and policy layer both block promotion from these states.
- Only `active` capabilities are returned by `filter_active()` in search.

## 2. CapabilityMaturity

Defined in `src/capabilities/schema.py:CapabilityMaturity` (str enum).

| Value | Meaning |
|---|---|
| `draft` | Under development; may be incomplete |
| `testing` | Promoted from draft; observable but not guaranteed stable |
| `stable` | Promoted from testing; production-ready |
| `broken` | Degraded from stable with failure evidence |
| `repairing` | Under repair from broken |

Legal transitions (enforced by `PromotionPlanner._ALLOWED_TRANSITIONS`):

```
draft     → testing
testing   → stable, draft (downgrade)
stable    → broken
broken    → repairing
repairing → testing, draft (reset)
```

Additional status-only transitions (any maturity):
```
any → disabled  (CapabilityStore.disable)
any → archived  (CapabilityStore.archive)
```

Invariants:
- `testing` does not imply executable. Maturity=testing means the capability
  has passed basic evaluation gates, not that it can be run. No
  `run_capability` function exists anywhere in the codebase.
- `stable` promotion from `testing` is a **separate lifecycle gate** from
  `draft→testing`. It requires evaluator pass + risk-based approval.
  `testing→stable` is planned by `_plan_testing_to_stable()`, distinct from
  `_plan_draft_to_testing()`.
- `stable` promotion from `quarantined` is explicitly blocked:
  `PromotionPlanner` rejects `quarantined → stable` with code
  `quarantined_to_stable_blocked`.
- `approved_for_testing` (a quarantine review status, see trust model) does
  not imply maturity=testing. It is a review recommendation only.

## 3. CapabilityType

Defined in `src/capabilities/schema.py:CapabilityType` (str enum).

| Value |
|---|
| `skill` |
| `workflow` |
| `dynamic_agent` |
| `memory_pattern` |
| `tool_wrapper` |
| `project_playbook` |

## 4. CapabilityScope

Defined in `src/capabilities/schema.py:CapabilityScope` (str enum).

| Value | Precedence (lower = higher priority in dedup) |
|---|---|
| `session` | 0 |
| `workspace` | 1 |
| `user` | 2 |
| `global` | 3 |

## 5. CapabilityRiskLevel

Defined in `src/capabilities/schema.py:CapabilityRiskLevel` (str enum).

| Value | Promotion constraint |
|---|---|
| `low` | Requires passing eval for testing→stable |
| `medium` | Requires approval OR passing eval for testing→stable |
| `high` | Requires explicit owner approval; blocked in auto-activation (7D-B) |

## 6. Lifecycle Transition Graph

```
 ┌──────────┐    evaluate+plan    ┌──────────┐   approve+evaluate   ┌──────────┐
 │  draft   │ ─────────────────→ │ testing  │ ───────────────────→ │  stable  │
 └──────────┘                    └──────────┘                      └──────────┘
      ↑                              │    │                              │
      │                              │    │ downgrade                    │ failure_evidence
      │                              │    └──────────────────────┐       │
      │                              │                           ↓       ↓
      │         reset         ┌──────────┐    repair     ┌──────────┐
      └────────────────────── │repairing │ ←─────────── │  broken  │
                              └──────────┘              └──────────┘
```

Status-only transitions (available from any maturity):
```
any ──→ disabled   (blocks execution, stays on disk)
any ──→ archived   (terminal, blocks execution and promotion)
```

## 7. External Import Path (Quarantine → Active)

```
external package
      │
      ▼
┌──────────────┐
│  quarantined  │  status=quarantined, maturity=draft
│  (import)     │  import_report.json written
└──────┬───────┘
       │ audit_quarantined_capability()
       ▼
┌──────────────┐
│ audit report  │  quarantine_audit_reports/<id>.json
└──────┬───────┘
       │ mark_quarantine_review()
       ▼
┌──────────────┐
│   review      │  quarantine_reviews/<id>.json
│   decision    │  status ∈ {needs_changes, approved_for_testing, rejected}
└──────┬───────┘
       │ request_quarantine_testing_transition()
       ▼
┌──────────────┐
│  transition   │  quarantine_transition_requests/<id>.json
│   request     │  status=pending; requires approved_for_testing review
└──────┬───────┘
       │ plan_quarantine_activation()
       ▼
┌──────────────┐
│  activation   │  quarantine_activation_plans/<id>.json
│   plan        │  allowed=true/false; target: active/testing ONLY
└──────┬───────┘
       │ apply_quarantine_activation()
       ▼
┌──────────────┐
│  active /     │  Copied to target scope. Manifest normalized:
│  testing      │  status=active, maturity=testing, extra.origin set
└──────────────┘
```

Each step is a separate authority. No step implicitly performs the next.

## 8. Key Phase 8A-0 Invariants

1. **quarantined ≠ active.** No quarantine operation changes capability
   status from quarantined except `apply_quarantine_activation`, which
   creates a *copy* in a target scope (the original stays quarantined).

2. **approved_for_testing ≠ maturity=testing.** `approved_for_testing` is
   a review decision stored in `quarantine_reviews/`. It does not mutate
   `manifest.maturity`.

3. **Transition request ≠ approval.** A `QuarantineTransitionRequest` is a
   pure data record. It does not perform lifecycle mutation.

4. **Activation plan ≠ authority.** A `QuarantineActivationPlan` is a pure
   plan. It returns `would_activate: false` always. Activation requires a
   separate `apply_quarantine_activation` call.

5. **Activation apply target is always active/testing.** Hardcoded:
   `TARGET_STATUS = "active"`, `TARGET_MATURITY = "testing"`.

6. **Activation apply can never create stable.** The target maturity is
   hardcoded to `"testing"`. Stable promotion requires a separate
   `PromotionPlanner._plan_testing_to_stable()` gate.

7. **testing ≠ executable.** No `run_capability` function exists. Maturity
   is metadata, not an execution guarantee.

8. **No run_capability exists.** Confirmed: zero runtime implementations
   of `run_capability` in the codebase.

9. **Stable promotion is a separate lifecycle gate.** `testing→stable`
   transit is planned by `_plan_testing_to_stable()`, distinct from
   `draft→testing` and from quarantine activation apply.

10. **External import always starts untrusted/quarantined.**
    `import_capability_package()` forces `status=quarantined`,
    `maturity=draft` regardless of what the source manifest declares.

11. **Active/testing external copy retains origin metadata.**
    `apply_quarantine_activation()` writes `extra.origin` with quarantine
    capability ID, activation plan ID, transition request ID, import source
    hash, activation timestamp, and activator identity.

12. **Missing provenance must not break legacy capabilities.** Provenance
    is a future concern. Existing capabilities without provenance data must
    continue to function normally.
