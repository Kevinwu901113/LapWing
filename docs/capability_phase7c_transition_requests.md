# Phase 7C: Quarantine Testing Transition Requests

## Overview

Phase 7C adds an operator-only request bridge between quarantine review (Phase 7B) and future activation (Phase 7D). It creates explicit request objects that record: "a quarantined capability has passed review and may be considered for a future transition into testing."

## Transition Request Semantics

A **QuarantineTransitionRequest** is a pure data record. It:
- Records intent to eventually move a quarantined capability into testing
- Stores which review and audit support the request
- Records the requested target scope and metadata
- Requires gates (review passed, audit clean, evaluator/policy checks)

It does **NOT**:
- Activate or promote the capability
- Move files from quarantine to any active scope
- Change capability status or maturity
- Execute scripts or run tests
- Make the capability visible in default retrieval/StateView

## Why Request Is Not Activation

This is deliberate separation of concerns:

1. **Phase 7C (this phase)**: Operator creates a request — "this quarantined capability is ready for consideration."
2. **Phase 7D (future)**: A separate activation workflow reads pending requests and applies controlled transitions.

The request is a decision record, not a mutation. This enables:
- Audit trail of who requested what and why
- Ability to cancel or supersede requests
- Review of pending requests before any activation
- Separation between "approved for testing" (review status) and "scheduled for transition" (request)

## Gates

Creating a transition request requires ALL of:
1. Capability exists in quarantine
2. `manifest.status == "quarantined"`
3. `manifest.maturity == "draft"`
4. Latest (or specified) review has `review_status == "approved_for_testing"`
5. Latest (or specified) audit report exists
6. Evaluator re-run passes
7. Policy install check passes
8. No existing pending request for same capability + target scope
9. High-risk capabilities get `required_approval: true` in request metadata

## Data Model

```
QuarantineTransitionRequest:
  request_id: string (qtr_<uuid>)
  capability_id: string
  created_at: ISO 8601
  created_by: string | null
  source_review_id: string | null
  source_audit_id: string | null
  requested_target_scope: "user" | "workspace" | "session" | "global"
  requested_target_maturity: "testing"
  status: "pending" | "cancelled" | "rejected" | "superseded"
  reason: string
  risk_level: "low" | "medium" | "high"
  required_approval: bool
  findings_summary: dict
  content_hash_at_request: string
  metadata: dict
```

## Storage

Requests are stored inside the quarantined capability directory:
```
data/capabilities/quarantine/<capability_id>/
  quarantine_transition_requests/
    <request_id>.json
```

Path safety:
- `capability_id` validated against `/`, `\\`, `..`
- `request_id` validated against `/`, `\\`, `..`
- All writes confined to `quarantine/<id>/quarantine_transition_requests/`

## Tools

| Tool | Description | Permission |
|------|-------------|------------|
| `request_quarantine_testing_transition` | Create a transition request (or dry-run gates) | capability_import_operator |
| `list_quarantine_transition_requests` | List requests with filters | capability_import_operator |
| `view_quarantine_transition_request` | View full request details | capability_import_operator |
| `cancel_quarantine_transition_request` | Cancel a pending request | capability_import_operator |

## Feature Flag

- `capabilities.quarantine_transition_requests_enabled` (default: `false`)
- Narrower than `capabilities.external_import_enabled`
- When disabled, transition request tools are absent from the registry

## Lifecycle Relationship

```
Phase 7A: Import external package → quarantine (status=quarantined, maturity=draft)
    ↓
Phase 7B: Audit + Review → approved_for_testing (report-only, no mutation)
    ↓
Phase 7C (THIS): Create transition request (pending record, no mutation)
    ↓
Phase 7D (FUTURE): Activation workflow reads pending requests → applies transition
```

## Future Phase 7D Activation Plan

Phase 7D will:
1. List pending transition requests
2. Verify gates still hold (content hash hasn't changed since request)
3. Require approval for high-risk capabilities
4. Apply controlled lifecycle transition: quarantine → draft/testing in target scope
5. Update manifest status from quarantined to active
6. Move/copy from quarantine to target scope directory
7. Update active index
8. Mark request as applied/completed

## Hard Constraints (verified by tests)

- [x] No activation
- [x] No promotion
- [x] No apply transition
- [x] No run_capability
- [x] No script execution
- [x] No Python import from package
- [x] No test execution from package
- [x] No network
- [x] No LLM judge
- [x] No default retrieval of quarantined capabilities
- [x] No StateView injection
- [x] No Brain/TaskRuntime behavior change
- [x] No dynamic agent changes
