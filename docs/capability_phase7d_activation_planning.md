# Phase 7D-A: Quarantine Activation Planning

**Date:** 2026-05-03
**Scope:** Activation planner only — computes explicit activation plans for quarantined capabilities with pending transition requests.

---

## 1. Purpose

Phase 7D-A introduces a **planner-only** tool that computes activation plans for quarantined capabilities. It reads existing quarantine data (manifests, review reports, audit reports, transition requests) and produces a plan/report. It performs NO activation, file copy, mutation, or execution.

This phase is the first half of Phase 7D. The second half (Phase 7D-B) will implement the actual activation step using these plans.

---

## 2. Hard Constraints

Phase 7D-A does NOT:
- Activate quarantined capabilities
- Move/copy files into active scopes
- Change `manifest.status` or `manifest.maturity`
- Update the active index
- Make capabilities retrievable
- Execute scripts or import Python code
- Call `CapabilityLifecycleManager`
- Call `CapabilityStore.create_draft` or `refresh_index_for`
- Call `CapabilityIndex.upsert`
- Implement `run_capability`
- Expose capabilities via StateView

Phase 7D-A DOES:
- Read quarantine data (manifest, CAPABILITY.md, reviews, audits, requests)
- Re-run CapabilityEvaluator and CapabilityPolicy checks
- Check target scope collisions (read-only)
- Compute a copy plan summary
- Write activation plans as JSON under `quarantine/<id>/quarantine_activation_plans/`

---

## 3. Feature Flag

```toml
[capabilities]
quarantine_activation_planning_enabled = false  # new, default false
```

- Requires `capabilities.enabled = true`
- Requires `capabilities.external_import_enabled = true`
- Requires `capabilities.quarantine_transition_requests_enabled = true`
- Env var: `CAPABILITIES_QUARANTINE_ACTIVATION_PLANNING_ENABLED`

---

## 4. Tool

### `plan_quarantine_activation`

**Permission:** `capability_import_operator`

**Input:**
- `capability_id` (required) — quarantined capability ID
- `request_id` (optional) — specific transition request ID; defaults to latest pending
- `target_scope` (optional) — override; defaults from request
- `created_by` (optional) — operator identifier
- `persist_plan` (bool, default `true`) — write plan JSON to quarantine storage
- `dry_run` (bool, default `false`) — compute plan but write nothing

**Output:**
- `plan` — QuarantineActivationPlan (safe, no raw absolute paths)
- `would_activate` — always `false`
- `allowed` — boolean

---

## 5. Data Model

### QuarantineActivationPlan

| Field | Type | Description |
|-------|------|-------------|
| `plan_id` | str | Unique ID (`qap_` prefix + UUID hex) |
| `capability_id` | str | Quarantined capability ID |
| `request_id` | str | Transition request ID |
| `created_at` | str | ISO 8601 timestamp |
| `created_by` | str\|null | Operator identifier |
| `source_review_id` | str\|null | Review report ID |
| `source_audit_id` | str\|null | Audit report ID |
| `target_scope` | str | Target scope (user/workspace/session/global) |
| `target_status` | str | Always `"active"` |
| `target_maturity` | str | Always `"testing"` |
| `allowed` | bool | Whether all gates passed |
| `required_approval` | bool | True for high-risk capabilities |
| `blocking_findings` | list | Reasons plan is blocked (empty if allowed) |
| `policy_findings` | list | Policy check results |
| `evaluator_findings` | list | Evaluator check results |
| `copy_plan` | dict | Summary: target scope, file counts, categories, collision status |
| `content_hash` | str | Current capability content hash |
| `request_content_hash` | str | Hash from transition request |
| `risk_level` | str | From manifest (low/medium/high) |
| `explanation` | str | Human-readable explanation |
| `metadata` | dict | Extensible metadata |

---

## 6. Allowed Plan Gates

All must pass for `allowed=true`:

1. Capability exists in quarantine
2. `manifest.status == "quarantined"`
3. `manifest.maturity == "draft"`
4. Transition request exists and `status == "pending"`
5. Request `target_maturity == "testing"`
6. Content hash matches between request and current capability
7. Review with `review_status == "approved_for_testing"` exists
8. Audit report exists and passed/recommended `approved_for_testing`
9. CapabilityEvaluator passes (no error-level findings)
10. CapabilityPolicy install/transition checks allow
11. No target scope collision (capability ID doesn't already exist in target scope)
12. Target scope in allowed set (user/workspace/session/global)

For high-risk capabilities: `required_approval=true` set in plan, but plan is NOT authority.

---

## 7. Storage

Plans are stored under:
```
data/capabilities/quarantine/<capability_id>/quarantine_activation_plans/<plan_id>.json
```

Both allowed and blocked plans are persisted by default for auditability.

---

## 8. Safety Guarantees

- No script execution (verified via tests: no subprocess, no os.system, no imports)
- No network access
- No LLM judge
- No raw absolute paths in tool output or persisted plan
- Path traversal rejected on `capability_id` and `request_id`
- Prompt injection text treated as data
- Plan file writes are the only filesystem mutation

---

## 9. Tool Surface Audit

Only one tool registered: `plan_quarantine_activation`.

Absent tools:
- `apply_quarantine_activation`
- `activate_quarantined_capability`
- `promote_quarantined_capability`
- `run_quarantined_capability`
- `run_capability`

---

## 10. Integration Points

- `src/capabilities/quarantine_activation_planner.py` — core module
- `src/tools/capability_tools.py` — tool registration + executor
- `src/app/container.py` — wiring (behind feature flag)
- `config.toml` — feature flag
- `src/config/settings.py` — Pydantic model + exports
- `config/settings.py` — compat exports

---

## 11. Future Phase (7D-B)

Phase 7D-B will implement the actual activation step:
- Read an allowed plan from Phase 7D-A
- Verify plan is still valid (not stale)
- Move files from quarantine to target scope
- Update manifest (status → active, maturity → testing)
- Update active index
- Make capability retrievable

Phase 7D-A plans serve as explicit, auditable pre-approval for this future step.
