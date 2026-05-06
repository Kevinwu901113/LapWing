# Phase 7D-B: Quarantine Activation Apply

Explicit operator-only application of a previously approved quarantine
activation plan into testing. This is the first phase that copies a
quarantined capability into an active target scope.

## Motivation

Phase 7D-A created the activation planner — it computes plans with
`would_activate=False` but never activates. Phase 7D-B closes the loop
by adding the explicit `apply_quarantine_activation` operation.

## What it produces

- `status=active`
- `maturity=testing`

## What it does NOT produce

- `maturity=stable`
- `run_capability`
- Script execution
- Automatic retrieval beyond normal testing filters
- Automatic promotion
- Dynamic agent binding

## Feature flag

```toml
[capabilities]
quarantine_activation_apply_enabled = false  # default
```

Required flags (all must be true):
- `capabilities.enabled`
- `capabilities.external_import_enabled`
- `capabilities.quarantine_transition_requests_enabled`
- `capabilities.quarantine_activation_planning_enabled`
- `capabilities.quarantine_activation_apply_enabled`

## Permission

`capability_import_operator` — not granted to standard, default, chat,
or local_execution profiles.

## Tool

**`apply_quarantine_activation`**

Input:
- `capability_id` (required)
- `reason` (required)
- `plan_id` (optional; if omitted, uses latest plan)
- `request_id` (optional)
- `target_scope` (optional; must match plan/request if provided)
- `applied_by` (optional)
- `dry_run` (default false)

## Core function

`apply_quarantine_activation(...)` in `src/capabilities/quarantine_activation_apply.py`

Returns `ActivationResult` with:
- `applied: bool`
- `dry_run: bool`
- `target_scope`, `target_status` (=active), `target_maturity` (=testing)
- `blocking_findings`, `message`

## Gates (18 checks)

1. Capability exists in quarantine
2. manifest.status == quarantined
3. manifest.maturity == draft
4. Activation plan loaded (by id or latest)
5. plan.allowed == true
6. plan.target_maturity == testing
7. plan.target_status == active
8. plan.would_activate == false (apply is separate authority)
9. Pending transition request exists
10. request.status == pending
11. Review still approved_for_testing
12. Audit still passed
13. Content hash matches plan/request
14. Target scope collision — none
15. Evaluator re-run — passed
16. Policy install check — allowed
17. High risk — blocked (no human approval model yet)
18. Symlinks — rejected

## Copy behavior

On successful apply:
1. Copies quarantine directory to `data/capabilities/<scope>/<id>/`
2. Normalizes manifest: status=active, maturity=testing, scope=target_scope
3. Adds `extra.origin` metadata (quarantine_capability_id, activation_plan_id, etc.)
4. Writes `activation_report.json` in target copy and quarantine
5. Refreshes `CapabilityIndex` for target copy
6. Marks transition request as superseded
7. Original quarantine copy remains unchanged

## Atomicity guarantees

- All gates pass before any writes
- Any failure rolls back target directory
- Quarantine original never mutated
- Dry run writes nothing, copies nothing

## Files

| File | Purpose |
|------|---------|
| `src/capabilities/quarantine_activation_apply.py` | Core module |
| `src/tools/capability_tools.py` (Phase 7D-B section) | Tool schema, executor, registration |
| `src/config/settings.py` | Feature flag model |
| `config/settings.py` | Feature flag value |
| `src/app/container.py` | Tool registration wiring |
| `tests/capabilities/test_phase7d_activation_apply.py` | Gate + behavior tests |
| `tests/capabilities/test_phase7d_activation_apply_tools.py` | Tool tests |
| `tests/capabilities/test_phase7d_activation_apply_atomicity.py` | Atomicity tests |
| `tests/capabilities/test_phase7d_activation_apply_safety.py` | Safety tests |
