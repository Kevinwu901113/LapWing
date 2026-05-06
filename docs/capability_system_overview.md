# Capability Evolution System — Consolidated Architecture Overview

**Date:** 2026-05-06
**Scope:** Phase 0 through Phase 8C-1 + Post-Phase-7 E2E + Post-Phase-8 Trust/Promotion Audit + Maintenance A+B+C + Post-Maintenance Consolidation (complete)
**Status:** Audit snapshot — all phases hardened, 2,583 capability tests pass + 30 new E2E (3,353 total regression, 0 failures)

---

## 1. Phase-by-Phase Summary

| Phase | Name | What It Delivers |
|-------|------|-----------------|
| 0 | Foundation | `CapabilityDocument`, `CapabilityParser`, `CapabilityManifest` schema, `compute_content_hash`, `generate_capability_id` — non-runtime data model |
| 1 | Parsing & Validation | Front-matter parsing, enum validation, standard directory scaffolding, error types |
| 2A | Store & Index | `CapabilityStore` (filesystem CRUD), `CapabilityIndex` (SQLite FTS), version snapshots |
| 2B | Read-only Tools | `list_capabilities`, `search_capability`, `view_capability` — feature-gated behind `capabilities.enabled` |
| 3A | Policy & Evaluation | `CapabilityPolicy`, `CapabilityEvaluator`, `EvalRecord`, `PromotionPlanner` — deterministic safety/quality checks |
| 3B | Lifecycle Manager | `CapabilityLifecycleManager` — gated maturity/status transitions with policy/evaluator/planner pipeline, version snapshots before mutation |
| 3C | Lifecycle Tools | `evaluate_capability`, `plan_capability_transition`, `transition_capability` — feature-gated behind `capabilities.lifecycle_tools_enabled` |
| 4 | Retriever & StateView | `CapabilityRetriever` — progressive disclosure of compact capability summaries into `StateView`, deterministic filtering + ranking, no LLM/embeddings |
| 5A | Curator & Proposal | `ExperienceCurator` (deterministic heuristic), `TraceSummary` (secrets redaction), `CapabilityProposal` (filesystem persistence), tools: `reflect_experience`, `propose_capability` |
| 5B | Execution Summary | `TaskEndContext` → `TraceSummaryObserver` — capture-only, sanitized, best-effort, no persistence by default |
| 5C | Curator Dry-Run | `CuratorDryRunAdapter` — in-memory curator decision on sanitized summary, never persists |
| 5D | Auto-Proposal | `AutoProposalAdapter` — controlled proposal-only persistence with 9 gates, never creates drafts |
| 6A | Agent Metadata | AgentSpec capability-backed metadata fields, `AgentPolicy.validate_capability_metadata()` — read-only lint, no runtime behavior change |
| 6B | Agent Candidates | `AgentCandidate` + `AgentEvalEvidence` + `AgentCandidateFinding` models, `AgentCandidateStore` (filesystem, separate from AgentCatalog), `AgentPolicy.validate_agent_candidate()` — data model and storage only, no runtime change |
| 6C | Agent Save Gate | Feature-gated `validate_persistent_save_gate` in AgentPolicy, extended `AgentRegistry.save_agent` with candidate/candidate_store/flag params, `is_capability_backed_agent()` helper — optional approval gate for saving capability-backed persistent agents, default off, no runtime change |
| 6D | Agent Candidate Tools | 6 operator tools for managing AgentCandidate objects (`list_agent_candidates`, `view_agent_candidate`, `add_agent_candidate_evidence`, `approve_agent_candidate`, `reject_agent_candidate`, `archive_agent_candidate`) — feature-gated behind `agents.candidate_tools_enabled`, require `AGENT_CANDIDATE_OPERATOR_PROFILE`, no execution, no auto-promotion. Save gate hardened: archived candidates blocked, optional evidence freshness check |
| 7A | External Import & Quarantine | `inspect_capability_package`, `import_capability_package` tools — feature-gated behind `capabilities.external_import_enabled` (requires `capabilities.enabled=true`), require `CAPABILITY_IMPORT_OPERATOR_PROFILE`. Parses local filesystem packages, runs evaluator + policy, copies to `data/capabilities/quarantine/<id>/` with forced `status=quarantined, maturity=draft`. No execution, no promotion, no network, no URL import, no active indexing, excluded from default search/retrieval. Source path stored as SHA256 hash. `import_report.json` with origin metadata |
| 7B | Quarantine Review & Audit | `list_quarantined_capabilities`, `view_quarantine_report`, `audit_quarantined_capability`, `mark_quarantine_review` tools — same feature gate and operator profile as 7A. Deterministic local audit scanning for dangerous patterns, prompt injection, missing sections, tool/permission risk, status mismatches. Writes `quarantine_audit_reports/<audit_id>.json` and `quarantine_reviews/<review_id>.json`. Report-only: no activation, no promotion, no execution, no network, no LLM judge. `approved_for_testing` is a review label only — never changes status/maturity or enables retrieval/StateView. |
| 7C | Quarantine Transition Requests | `request_quarantine_testing_transition`, `list_quarantine_transition_requests`, `view_quarantine_transition_request`, `cancel_quarantine_transition_request` tools — narrower flag `capabilities.quarantine_transition_requests_enabled` (default `false`), same operator profile. Creates explicit request objects recording "a quarantined capability has passed review and may be considered for future transition." Pure record-keeping: writes only `quarantine_transition_requests/<request_id>.json`, never changes status/maturity, never moves/copies/activates, never executes scripts, never runs tests. Gates: status=quarantined, maturity=draft, review=approved_for_testing, audit exists, evaluator/policy re-check pass. Future Phase 7D will read these pending requests for controlled activation. |
| 7D-A | Quarantine Activation Planning | `plan_quarantine_activation` tool — narrower flag `capabilities.quarantine_activation_planning_enabled` (default `false`), same operator profile (`capability_import_operator`). Computes explicit activation plans (allowed or blocked) given a quarantined capability and pending transition request. Planner-only: performs NO activation, NO file copy/move, NO status/maturity mutation, NO index update, NO retrieval, NO script execution. Writes only `quarantine_activation_plans/<plan_id>.json` under quarantine directory. Re-runs evaluator and policy checks, verifies content hashes, checks target scope collisions. Allowed plans require all 12 gates to pass. |
| 7D-B | Quarantine Activation Apply | `apply_quarantine_activation` tool — narrower flag `capabilities.quarantine_activation_apply_enabled` (default `false`), same operator profile. First phase that copies a quarantined capability into an active target scope (maturity=testing, status=active). 18 gates: all must pass before any writes. Atomic copy with rollback on failure. Quarantine original never mutated. Never produces stable maturity, never executes scripts, never promotes. |
| 8A-1 | Provenance / Integrity Foundation | `CapabilityProvenance` (18-field dataclass), `compute_capability_tree_hash` (deterministic SHA256 over included files, excluding volatile/post-hoc artifacts), `CapabilityTrustPolicy` (4 analytical methods: evaluate, activate, retrieve, promote). `provenance.json` written at import (fail-closed cleanup) and activation apply (fail-closed rollback). Tree hash includes CAPABILITY.md, manifest.json (normalized), scripts/, tests/, examples/; excludes evals, traces, versions, 6 quarantine dirs, reports, .sqlite/.db/.pyc, hidden files, symlinks. TrustPolicy is purely analytical — never gates any path in 8A-1. No execution, no network, no signature verification. |
| 8B-1 | Signature Metadata / Verifier Stub | `CapabilitySignature` (metadata container), `CapabilityTrustRoot` (local trust root config), `verify_signature_stub` (deterministic stub that NEVER returns verified/trusted_signed). `signature.json` I/O. Secret detection rejects private key material, API key patterns, secret field names. No real crypto, no network, no verified signatures. |
| 8B-2 | Trust Root Store | `TrustRootStore` — filesystem persistence for trust root metadata. Trust roots are metadata-only (name, status, operator, key hash — no key material). Validates IDs against filesystem-safe constraints. |
| 8C-1 | Stable Promotion Trust Gate | `trust_gate_enabled` feature flag (default False) wired into `CapabilityLifecycleManager.apply_transition()` for `testing -> stable` transitions only. Risk-specific gating: low risk allows reviewed + verified, medium risk requires reviewed or trusted_local, high risk requires trusted_local or trusted_signed. Hard blocks: integrity mismatch, invalid signature, untrusted/unknown trust. Denial is atomic — no manifest/provenance/index/snapshot mutation. TrustPolicy evaluation appended to policy_decisions in TransitionResult. |
| M-A | Maintenance A: Health Report | `CapabilityHealthReport`, `CapabilityHealthFinding`, `generate_capability_health_report()` — read-only, deterministic health audit. 10 checks: inventory counts, missing provenance, integrity mismatch, stale eval, stale trust roots, quarantine backlog, proposal backlog, agent candidate backlog, index drift, orphaned artifacts. Text-only recommendations. No mutation, no execution, no network, no LLM. |
| M-B | Maintenance B: Repair Queue | `RepairQueueItem`, `RepairQueueStore`, `create_from_health_report()` — maps health findings to inert repair queue items. Filesystem-backed JSON store with CRUD, filtering, dedup, status lifecycle. Recommended actions are labels only. No repair execution, no capability mutation, no index/lifecycle/proposal/candidate/trust-root changes. No tools. |
| M-C | Maintenance C: Repair Queue Tools | 6 operator-only tools: `list_repair_queue_items`, `view_repair_queue_item`, `create_repair_queue_from_health`, `acknowledge_repair_queue_item`, `resolve_repair_queue_item`, `dismiss_repair_queue_item`. Feature-gated behind `capabilities.repair_queue_tools_enabled=false`. Require `CAPABILITY_REPAIR_OPERATOR_PROFILE` (`capability_repair_operator` tag). Manage queue items only — no repair execution, no capability mutation, no index rebuild, no lifecycle transition, no proposal/candidate/trust-root mutation, no artifact deletion, no script execution, no network, no LLM judge. action_payload inert metadata only. |
| Post-Maint | Post-Maintenance Consolidation | End-to-end consolidation audit: 30 E2E tests across 4 flows (health→queue, operator lifecycle, corruption tolerance, permissions/flags). Full maintenance lifecycle verification from health report → findings → repair queue items → operator list/view → acknowledge/resolve/dismiss. No new behavior, tools, or flags. 3,353 regression (0 failures). |

---

## 2. Component Map

```
src/capabilities/                    # Core package — 26 modules
├── __init__.py                      # Public API exports
├── document.py                      # CapabilityDocument, CapabilityParser, parse_capability
├── schema.py                        # CapabilityManifest, enums, ALLOWED_* sets
├── errors.py                        # CapabilityError hierarchy
├── hashing.py                       # compute_content_hash
├── ids.py                           # generate_capability_id, is_valid_capability_id
├── store.py                         # CapabilityStore — filesystem CRUD
├── index.py                         # CapabilityIndex — SQLite FTS
├── search.py                        # search helpers, scope precedence, dedup
├── versioning.py                    # VersionSnapshot, snapshot_on_disable/archive
├── policy.py                        # CapabilityPolicy — deterministic allow/deny
├── evaluator.py                     # CapabilityEvaluator — safety/quality lint
├── eval_records.py                  # EvalRecord persistence
├── promotion.py                     # PromotionPlanner — transition planning
├── lifecycle.py                     # CapabilityLifecycleManager — gated mutation
├── ranking.py                       # Deterministic candidate scoring
├── retriever.py                     # CapabilityRetriever — progressive disclosure
├── trace_summary.py                 # TraceSummary — secrets redaction, CoT stripping
├── curator.py                       # ExperienceCurator — heuristic reflection
├── proposal.py                      # CapabilityProposal — model + filesystem persistence
├── trace_summary_adapter.py         # TraceSummaryObserver — TaskEndContext → TraceSummary
├── curator_dry_run_adapter.py       # CuratorDryRunAdapter — in-memory curator decision
├── auto_proposal_adapter.py         # AutoProposalAdapter — controlled proposal persistence
├── import_quarantine.py             # External import → quarantine, InspectResult, ImportResult
├── quarantine_review.py             # Quarantine audit/review — list, view, audit, mark (Phase 7B)
├── quarantine_transition.py         # Quarantine transition requests — request, list, view, cancel (Phase 7C)
├── quarantine_activation_planner.py # Quarantine activation planning — plan, list, view (Phase 7D-A)
├── quarantine_activation_apply.py   # Quarantine activation apply — atomic copy to active/testing (Phase 7D-B)
├── provenance.py                    # Provenance data model, tree hashing, trust policy, I/O (Phase 8A-1)
├── signature.py                     # Signature metadata, verifier stub, trust root model (Phase 8B-1)
├── trust_roots.py                   # TrustRootStore — persistent trust root storage (Phase 8B-2)
├── health.py                         # CapabilityHealthReport — read-only maintenance health (Maintenance A)
├── repair_queue.py                    # RepairQueueItem, RepairQueueStore — inert repair queue (Maintenance B)
│
tests/capabilities/
├── test_phase8_e2e_trusted_promotion.py  # Post-Phase-8: 22 E2E tests, 5 flows (A-E)
├── test_phase8c_stable_promotion_gate.py # Phase 8C-1: trust gate unit tests (38 tests)
├── test_phase8c_stable_promotion_gate_integration.py # Phase 8C-1: trust gate integration (13 tests)
├── test_phase8c_stable_promotion_gate_regression.py  # Phase 8C-1: trust gate regression (19 tests)
├── test_phase8c_stable_promotion_trust_gate_invariants.py # Phase 8C-1: invariants
├── test_phase8a_provenance_integration.py  # Phase 8A-1: provenance integration
├── test_phase8a_provenance_model.py        # Phase 8A-1: provenance model
├── test_phase8b_signature_integration.py   # Phase 8B-1: signature integration
├── test_phase8b_signature_verifier_stub.py # Phase 8B-1: verifier stub tests
├── test_maintenance_health_report.py       # Maintenance A: health report functional tests
├── test_maintenance_health_safety.py       # Maintenance A: health report safety tests
├── test_maintenance_repair_queue.py        # Maintenance B: repair queue functional tests
├── test_maintenance_repair_queue_safety.py # Maintenance B: repair queue safety tests
├── test_maintenance_repair_queue_tools.py  # Maintenance C: repair queue tools functional tests
├── test_maintenance_repair_queue_operator_profile.py # Maintenance C: repair queue operator profile tests
└── test_maintenance_repair_queue_tools_safety.py # Maintenance C: repair queue tools safety tests
    └── test_maintenance_e2e_health_to_queue.py      # Post-Maintenance: E2E flows A-D

src/core/execution_summary.py        # Protocols + data models (no capability deps)

src/agents/candidate.py              # AgentCandidate, AgentEvalEvidence, AgentCandidateFinding (Phase 6B)
src/agents/candidate_store.py        # AgentCandidateStore — filesystem candidate storage (Phase 6B)

src/tools/capability_tools.py        # Tool registration + executors (only tool entry point)
src/tools/repair_queue_tools.py      # Maintenance C: repair queue operator tools

src/app/container.py                 # Wiring — the only runtime import site besides tools
```

**Hard boundary:** Only `container.py`, `capability_tools.py`, and `repair_queue_tools.py` import from `src.capabilities/`. No Brain, TaskRuntime, StateViewBuilder, SkillExecutor, ToolDispatcher, AgentRegistry, candidate modules, or agent execution paths import from capabilities directly.

---

## 3. Data Flow Diagrams

### 3a. Read-only Discovery Flow

```
User → list_capabilities/search_capability/view_capability
     → CapabilityStore.get/list/search
     → CapabilityIndex (SQLite FTS)
     → compact summary response
     → NO mutation, NO script execution
```

### 3b. Lifecycle Mutation Flow

```
User → evaluate_capability → CapabilityEvaluator → EvalRecord (optional persistence)
User → plan_capability_transition → PromotionPlanner → PromotionPlan (read-only preview)
User → transition_capability(dry_run=false)
     → PromotionPlanner.plan_transition → gate check
     → CapabilityPolicy.validate_promote → policy check
     → CapabilityEvaluator.evaluate → quality check (for draft→testing, testing→stable, repairing→testing)
     → create_version_snapshot → snapshot before mutation
     → manifest update → content_hash recalc
     → index refresh → mutation log record
```

### 3c. Progressive Disclosure Flow (Phase 4)

```
TaskRuntime → StateViewBuilder._build_capability_summaries()
           → CapabilityRetriever.retrieve(query, context)
             → _fetch_candidates → CapabilityIndex.search()
             → filter_candidates → maturity/risk/tool filters
             → rank_candidates → deterministic scoring
           → tuple[CapabilitySummary, ...] → StateView.capability_summaries
           → StateSerializer renders compact reference hints
```

### 3d. Manual Curator Flow (Phase 5A)

```
User → reflect_experience(trace_summary)
     → TraceSummary.from_dict → validate + strip CoT
     → TraceSummary.sanitize → redact secrets
     → ExperienceCurator.should_reflect → CuratorDecision
     → ExperienceCurator.summarize → CuratedExperience
     → NO mutation, NO persistence

User → propose_capability(trace_summary, apply=false)
     → curator analysis → CapabilityProposal
     → persist_proposal → proposal.json + PROPOSAL.md + source_trace_summary.json
     → NO draft, NO index, NO lifecycle

User → propose_capability(trace_summary, apply=true)
     → persist_proposal (proposal files)
     → CapabilityStore.create_draft (draft capability only)
     → CapabilityEvaluator.evaluate + write_eval_record
     → Index refresh + mark_applied
     → NEVER promotes, NEVER transitions past draft
```

### 3e. Task-End Observer Chain (Phase 5B→5C→5D)

```
TaskRuntime._emit_task_end()
  ├─ [5B] if execution_summary_enabled:
  │       TraceSummaryObserver.capture(TaskEndContext)
  │       → TaskEndContext.to_dict()
  │       → TraceSummary.from_dict() → sanitize()
  │       → sanitized dict (in-memory only)
  │
  ├─ [5C] if curator_dry_run_enabled AND last_summary exists:
  │       CuratorDryRunAdapter.capture(summary_dict)
  │       → TraceSummary.from_dict() (idempotent)
  │       → ExperienceCurator.should_reflect()
  │       → CuratorDryRunResult (in-memory only)
  │
  └─ [5D] if auto_proposal_enabled AND last_summary exists AND last_decision exists:
          AutoProposalAdapter.capture(summary, decision)
          → 9 gates: should_create, action, confidence, risk, boundary, verification, secrets, rate-limit, dedup
          → persist_proposal() (proposal files ONLY)
          → NEVER create_draft, NEVER index, NEVER eval, NEVER lifecycle, NEVER promote
```

---

## 4. Feature Flag Matrix

| Flag | Default | What It Enables | What It Must NOT Enable |
|------|---------|-----------------|------------------------|
| `capabilities.enabled` | `false` | Master kill-switch for all capability features | — |
| `capabilities.retrieval_enabled` | `false` | CapabilityRetriever → StateView progressive disclosure | Tool registration, mutation |
| `capabilities.curator_enabled` | `false` | `reflect_experience`, `propose_capability` tools | Auto-curation, task-end behavior |
| `capabilities.external_import_enabled` | `false` | `inspect_capability_package`, `import_capability_package` tools | Script execution, network access, active indexing, promotion |
| `capabilities.lifecycle_tools_enabled` | `false` | `evaluate_capability`, `plan_capability_transition`, `transition_capability` tools | Auto-promotion |
| `capabilities.execution_summary_enabled` | `false` | TraceSummary capture at task end (in-memory) | Persistence, curator calls |
| `capabilities.curator_dry_run_enabled` | `false` | In-memory curator dry-run at task end | Proposal creation, persistence |
| `capabilities.auto_proposal_enabled` | `false` | Auto-proposal persistence at task end | Draft creation, index updates, promotion |
| `agents.require_candidate_approval_for_persistence` | `false` | Agent save gate: require approved candidate for capability-backed persistent saves | Agent runtime, agent execution, candidate execution |
| `capabilities.auto_draft_enabled` | `false` | (Legacy — superseded by curator/proposal flow) | — |
| `capabilities.auto_proposal_min_confidence` | `0.75` | Minimum confidence threshold for auto-proposal | — |
| `capabilities.auto_proposal_allow_high_risk` | `false` | Allow auto-proposal for high-risk proposals | — |
| `capabilities.auto_proposal_max_per_session` | `3` | Max auto-proposals per session | — |
| `capabilities.auto_proposal_dedupe_window_hours` | `24` | Dedup window for similar proposals | — |
| `capabilities.data_dir` | `data/capabilities` | Root data directory | — |
| `capabilities.quarantine_transition_requests_enabled` | `false` | Phase 7C: quarantine transition request bridge (operator-only) | Script execution, activation, promotion, file moves |
| `capabilities.index_db_path` | `data/capabilities/capability_index.sqlite` | SQLite FTS index path | — |
| `trust_gate_enabled` | `false` (LifecycleManager constructor) | Trust gate evaluation on testing→stable transitions | Non-testing→stable transitions, runtime behavior |

**Defaults are conservative:** All capability flags default to `false`.

---

## 5. Tool Surface Matrix

### Read-only Tools

| Tool | Flags Required | Capability Tag | Risk | Mutates Store | Mutates Index | Writes Proposals | Creates Draft | Can Promote | Can Execute Scripts |
|------|---------------|----------------|------|---------------|---------------|------------------|---------------|-------------|---------------------|
| `list_capabilities` | `enabled` | `capability_read` | low | no | no | no | no | no | no |
| `search_capability` | `enabled` | `capability_read` | low | no | no | no | no | no | no |
| `view_capability` | `enabled` | `capability_read` | low | no | no | no | no | no | no |

### Lifecycle Tools

| Tool | Flags Required | Capability Tag | Risk | Mutates Store | Mutates Index | Writes Proposals | Creates Draft | Can Promote | Can Execute Scripts |
|------|---------------|----------------|------|---------------|---------------|------------------|---------------|-------------|---------------------|
| `evaluate_capability` | `enabled` + `lifecycle_tools_enabled` | `capability_lifecycle` | low | no (optional eval record) | no | no | no | no | no |
| `plan_capability_transition` | `enabled` + `lifecycle_tools_enabled` | `capability_lifecycle` | low | no | no | no | no | no | no |
| `transition_capability` | `enabled` + `lifecycle_tools_enabled` | `capability_lifecycle` | medium | yes (via lifecycle) | yes (refresh) | no | no | yes (controlled) | no |

### Curator Tools

| Tool | Flags Required | Capability Tag | Risk | Mutates Store | Mutates Index | Writes Proposals | Creates Draft | Can Promote | Can Execute Scripts |
|------|---------------|----------------|------|---------------|---------------|------------------|---------------|-------------|---------------------|
| `reflect_experience` | `enabled` + `curator_enabled` | `capability_curator` | low | no | no | no | no | no | no |
| `propose_capability` | `enabled` + `curator_enabled` | `capability_curator` | medium | only with apply=true | only with apply=true | yes | only with apply=true | no | no |

### Import Tools (Phase 7A)

| Tool | Flags Required | Capability Tag | Risk | Mutates Store | Mutates Index | Writes Proposals | Creates Draft | Can Promote | Can Execute Scripts |
|------|---------------|----------------|------|---------------|---------------|------------------|---------------|-------------|---------------------|
| `inspect_capability_package` | `enabled` + `external_import_enabled` | `capability_import_operator` | low | no | no | no | no | no | no |
| `import_capability_package` | `enabled` + `external_import_enabled` | `capability_import_operator` | medium | yes (quarantine dir only) | yes (quarantine status) | no (import report) | no | no | no |

### Quarantine Review Tools (Phase 7B)

| Tool | Flags Required | Capability Tag | Risk | Mutates Store | Mutates Index | Writes Proposals | Creates Draft | Can Promote | Can Execute Scripts |
|------|---------------|----------------|------|---------------|---------------|------------------|---------------|-------------|---------------------|
| `list_quarantined_capabilities` | `enabled` + `external_import_enabled` | `capability_import_operator` | low | no | no | no | no | no | no |
| `view_quarantine_report` | `enabled` + `external_import_enabled` | `capability_import_operator` | low | no | no | no | no | no | no |
| `audit_quarantined_capability` | `enabled` + `external_import_enabled` | `capability_import_operator` | low | no (audit report) | no | no | no | no | no |
| `mark_quarantine_review` | `enabled` + `external_import_enabled` | `capability_import_operator` | low | no (review decision) | no | no | no | no | no |

### Quarantine Transition Request Tools (Phase 7C)

| Tool | Flags Required | Capability Tag | Risk | Mutates Store | Mutates Index | Writes Proposals | Creates Draft | Can Promote | Can Execute Scripts |
|------|---------------|----------------|------|---------------|---------------|------------------|---------------|-------------|---------------------|
| `request_quarantine_testing_transition` | `enabled` + `quarantine_transition_requests_enabled` | `capability_import_operator` | low | no (request JSON) | no | no | no | no | no |
| `list_quarantine_transition_requests` | `enabled` + `quarantine_transition_requests_enabled` | `capability_import_operator` | low | no | no | no | no | no | no |
| `view_quarantine_transition_request` | `enabled` + `quarantine_transition_requests_enabled` | `capability_import_operator` | low | no | no | no | no | no | no |
| `cancel_quarantine_transition_request` | `enabled` + `quarantine_transition_requests_enabled` | `capability_import_operator` | low | no (status update) | no | no | no | no | no |

### Forbidden Tools (Verified Absent)

| Tool Name | Status |
|-----------|--------|
| `run_capability` | NOT PRESENT |
| `execute_capability` | NOT PRESENT |
| `auto_propose_capability` | NOT PRESENT |
| `task_end_curator` | NOT PRESENT |
| `create_capability` | NOT PRESENT |
| `install_capability` | NOT PRESENT |
| `patch_capability` | NOT PRESENT |
| `activate_quarantined_capability` | NOT PRESENT |
| `promote_quarantined_capability` | NOT PRESENT |
| `apply_quarantine_transition` | NOT PRESENT |
| `run_quarantined_capability` | NOT PRESENT |
| `apply_quarantine_activation` | Phase 7D-B (operator-only, testing only) |
| `auto_promote_capability` | NOT PRESENT |

---

## 6. RuntimeProfile / Permission Matrix

| Tool | Required Profile | Capability Tag |
|------|-----------------|----------------|
| `list_capabilities` | Standard (or any with `capability_read`) | `capability_read` |
| `search_capability` | Standard (or any with `capability_read`) | `capability_read` |
| `view_capability` | Standard (or any with `capability_read`) | `capability_read` |
| `evaluate_capability` | Operator (requires `capability_lifecycle`) | `capability_lifecycle` |
| `plan_capability_transition` | Operator (requires `capability_lifecycle`) | `capability_lifecycle` |
| `transition_capability` | Operator (requires `capability_lifecycle`) | `capability_lifecycle` |
| `reflect_experience` | Operator (requires `capability_curator`) | `capability_curator` |
| `propose_capability` | Operator (requires `capability_curator`) | `capability_curator` |
| `inspect_capability_package` | Operator (requires `capability_import_operator`) | `capability_import_operator` |
| `import_capability_package` | Operator (requires `capability_import_operator`) | `capability_import_operator` |

**Note:** `capability_lifecycle`, `capability_curator`, and `capability_import_operator` tags require explicit operator profiles — not granted to standard/default/chat/inner_tick.

---

## 7. Data Directory Layout

```
data/capabilities/
├── global/                          # Global scope capabilities
│   └── <capability_id>/
│       ├── CAPABILITY.md            # Front matter + body
│       ├── manifest.json            # Canonical manifest
│       ├── scripts/                 # (empty dir, no execution)
│       ├── tests/                   # (empty dir)
│       ├── examples/                # (empty dir)
│       ├── evals/                   # EvalRecord JSON files
│       ├── traces/                  # (empty dir)
│       └── versions/                # Version snapshots
├── user/                            # User scope capabilities
│   └── <capability_id>/ ...
├── workspace/                       # Workspace scope capabilities
│   └── <capability_id>/ ...
├── session/                         # Session scope capabilities
│   └── <capability_id>/ ...
├── archived/                        # Archived capabilities
│   └── <scope>/
│       └── <capability_id>/         # (or <capability_id>_<timestamp>/)
├── proposals/                       # Proposal directories
│   └── <proposal_id>/
│       ├── proposal.json
│       ├── PROPOSAL.md
│       └── source_trace_summary.json
├── quarantine/                       # Quarantined imports (Phase 7A)
│   └── <capability_id>/
│       ├── CAPABILITY.md
│       ├── manifest.json             # status=quarantined, maturity=draft (forced)
│       ├── import_report.json        # origin metadata, source_path_hash
│       ├── scripts/
│       ├── quarantine_audit_reports/   # Audit reports (Phase 7B)
│       ├── quarantine_reviews/         # Review decisions (Phase 7B)
│       └── quarantine_transition_requests/  # Transition requests (Phase 7C)
└── capability_index.sqlite          # SQLite FTS index
```

---

## 8. Mutation Paths

### Allowed Write Paths

1. **CapabilityStore.create_draft** — Creates new draft capability directory with CAPABILITY.md, manifest.json, standard dirs. Refreshes index. Records mutation log.

2. **CapabilityStore.disable** — Updates manifest status to `disabled`, syncs manifest.json, refreshes index, records mutation log. Does NOT move files.

3. **CapabilityStore.archive** — Updates manifest, moves directory to `archived/<scope>/`, handles timestamp collisions, refreshes index, records mutation log.

4. **CapabilityStore.rebuild_index** — Rebuilds entire SQLite index from store. Read-only from store's perspective.

5. **CapabilityStore.refresh_index_for** — Refreshes single capability in index.

6. **CapabilityLifecycleManager.apply_transition** (maturity) — Pipeline: planner → policy → evaluator → version snapshot → manifest update → re-parse → index refresh → mutation log. Blocked transitions produce zero file changes.

7. **CapabilityLifecycleManager.apply_transition** (status: disable/archive) — Pipeline: plan → policy → snapshot → delegate to store.disable/archive.

8. **propose_capability(apply=false)** — Writes proposal files only: `proposals/<id>/proposal.json` + `PROPOSAL.md` + `source_trace_summary.json`. Zero store/index changes.

9. **propose_capability(apply=true)** — Writes proposal files + calls `store.create_draft()` (draft maturity only) + evaluator + index refresh. NEVER promotes past draft.

10. **AutoProposalAdapter.capture** — Writes proposal files only. Never calls create_draft, never touches index, never runs evaluator, never promotes.

11. **import_capability_package(dry_run=false)** — Inspects external package, validates duplicates, copies to `quarantine/<id>/`, forces `status=quarantined, maturity=draft` in manifest.json, writes `import_report.json`, indexes with quarantined status (excluded from default search/retrieval). Never executes scripts, never imports Python modules, never calls subprocess, never promotes.

12. **request_quarantine_testing_transition(dry_run=false)** — Validates gates (exists in quarantine, status=quarantined, maturity=draft, review=approved_for_testing, audit exists, evaluator/policy re-check), writes `quarantine_transition_requests/<request_id>.json`. Never changes manifest, never moves/copies files, never activates, never executes scripts.

13. **cancel_quarantine_transition_request** — Updates request status from pending to cancelled in the request JSON file only. Never alters capability, never deletes request file, never affects active store/index.

### Read-only Paths (No Mutation)

- `CapabilityRetriever.retrieve` — Index search + filtering + ranking, returns CapabilitySummary list
- `CapabilityRetriever.summarize` — Creates compact summary from document (no body/procedure/scripts)
- `ExperienceCurator.should_reflect` — Deterministic heuristic on TraceSummary, no I/O
- `ExperienceCurator.summarize` — Extraction from TraceSummary, no I/O
- `CuratorDryRunAdapter.capture` — In-memory only, no persistence
- `TraceSummaryObserver.capture` — In-memory only, no persistence by default
- All read-only tools (list/search/view)
- `evaluate_capability` (optional eval record write is idempotent)
- `plan_capability_transition` (pure preview)
- `transition_capability(dry_run=true)` (pure preview)

### Confirmed: No Hidden Write Paths

- No writes from retriever/ranker
- No writes from curator dry-run
- No writes from execution summary observer
- No writes from StateView/StateViewBuilder
- No writes from Brain, TaskRuntime, SkillExecutor, ToolDispatcher paths

---

## 9. Automatic Behavior Paths

All automatic behavior is **task-end observer chain only**, feature-gated, and fail-closed:

1. **Execution Summary (5B):** `capabilities.execution_summary_enabled=true` → `TraceSummaryObserver.capture()` at task end → in-memory sanitized dict. If flag is off, nothing happens.

2. **Curator Dry-Run (5C):** `capabilities.curator_dry_run_enabled=true` AND execution summary succeeded → `CuratorDryRunAdapter.capture()` → in-memory decision. If flag is off or summary failed, nothing happens.

3. **Auto-Proposal (5D):** `capabilities.auto_proposal_enabled=true` AND both above succeeded → `AutoProposalAdapter.capture()` → 9 gates → persist proposal files only. If any gate fails or flag is off, nothing persists.

**No other automatic behavior exists.** No auto-promotion, no auto-indexing (except during explicit mutations), no auto-curation beyond the task-end chain, no timer-based or tick-based capability operations.

---

## 10. Explicit User/Operator Behavior Paths

| Action | How Invoked | Preconditions |
|--------|------------|---------------|
| List capabilities | `list_capabilities` tool | `capabilities.enabled=true` |
| Search capabilities | `search_capability` tool | `capabilities.enabled=true` |
| View capability | `view_capability` tool | `capabilities.enabled=true` |
| Evaluate capability | `evaluate_capability` tool | `capabilities.enabled=true` + `capabilities.lifecycle_tools_enabled=true` |
| Plan transition | `plan_capability_transition` tool | same as above |
| Apply transition | `transition_capability` tool | same as above |
| Reflect on experience | `reflect_experience` tool | `capabilities.enabled=true` + `capabilities.curator_enabled=true` |
| Propose capability | `propose_capability` tool | same as above |
| Inspect external package | `inspect_capability_package` tool | `capabilities.enabled=true` + `capabilities.external_import_enabled=true` |
| Import external package | `import_capability_package` tool | same as above |

---

## 11. Hard Safety Boundaries

1. **No script execution.** No tool, adapter, or internal path executes capability scripts.
2. **No `run_capability`.** The tool does not exist. Verified absent from entire codebase.
3. **No auto-promotion.** Maturity transitions require explicit `transition_capability` tool invocation with operator profile.
4. **No dynamic agent binding.** Capabilities are not bound to agents at runtime. Phase 6A adds metadata-only fields (bound_capabilities, risk_level, etc.) to AgentSpec — these are inert data, not runtime bindings. Phase 6B adds AgentCandidate storage and policy lint — read-only, non-mutating, no runtime effect. Phase 6C adds an optional save gate (`agents.require_candidate_approval_for_persistence`, default `false`) that requires an approved candidate with evidence before persisting capability-backed agents — purely gating, no runtime capability loading. Phase 6D adds operator-only candidate management tools behind `agents.candidate_tools_enabled` (default `false`) — tools manage candidates/evidence only, no execution, no auto-promotion. Agent/candidate modules do not import from `src.capabilities`.
5. **No LLM/network in critical paths.** Retriever, curator, policy, evaluator, planner are all deterministic and offline.
6. **Secrets redaction.** TraceSummary applies regex-based redaction (sk-*, API_KEY, Bearer tokens, passwords, PEM keys). Auto-proposal has defense-in-depth re-check.
7. **CoT/internal field stripping.** TraceSummary.from_dict drops `_cot`, `chain_of_thought`, `_reasoning`, `scratchpad`, `hidden_thoughts`, `internal_notes`, `reasoning_trace`, and any key starting with `__`.
8. **String truncation.** All string fields capped at 50,000 chars; commands at 2,000 chars; file paths at 1,000 chars.
9. **No raw transcript persistence.** Execution summaries are sanitized before in-memory storage. Proposals persist only curated, sanitized data.
10. **Path traversal blocked.** `proposed_id` validated in `ExperienceCurator.propose_capability` (rejects `..`, `/`, `\`). Store paths are always constructed from `data_dir / scope / cap_id` with enum-validated scope.
11. **Prompt injection mitigated.** Capability summaries in StateView are compact (no body, no procedure, no scripts). Malicious CAPABILITY.md text is data, not instruction. Summaries framed as references.
12. **Fail-closed.** All task-end observers return `None` on exception. All flags default to `false`.
13. **No permission broadening.** Lifecycle and curator tools use `capability_lifecycle` / `capability_curator` tags — not granted to standard profiles.
14. **No writes outside data root.** All store paths derived from configured `data_dir`. No symlink following. No absolute path injection.
15. **No execution during import.** Scripts are copied as data, never executed. Python files are never imported. No subprocess calls during import. No network access during import.
16. **Source path anonymized.** Source path stored as SHA256 hash in `import_report.json`, not raw.
17. **Quarantine isolation.** Imported capabilities have `status=quarantined, maturity=draft` forced. Excluded from default `store.list()`, `index.search()`, and `CapabilityRetriever.retrieve()`. Policy blocks `run` and `promote` for quarantined.
18. **No remote import.** URLs (`://`), symlinks, and path traversal (`..`) rejected at the validation layer.
19. **No overwrite via import.** Duplicate active or quarantine IDs rejected during import.
20. **No automatic activation.** Activation requires explicit `apply_quarantine_activation` tool invocation. Plans (`would_activate=False`), requests, reviews, and audits alone never activate.
21. **Atomic apply.** All 18 gates must pass before any writes. Any failure triggers rollback (`shutil.rmtree` on target). Quarantine original never mutated.
22. **Only testing maturity.** Apply produces `maturity=testing` only — never `stable`. Stable must go through normal lifecycle transitions.
23. **No symlink follow.** Gate 18 in apply explicitly scans for symlinks before copy. `shutil.copytree(symlinks=False)` used.
24. **Provenance immutability.** `provenance.json` is written only at import and activation apply. No lifecycle transition (including testing → stable) modifies provenance. Byte-for-byte equality verified before and after promotion.
25. **Stable promotion trust gate atomic.** Denied stable promotions touch no files: manifest unchanged, provenance unchanged, no version snapshot created, no index update.
26. **No crypto verification.** `verify_signature_stub()` always returns `present_unverified` at best. `trusted_signed` trust level is not achievable through any code path.
27. **Trust roots are metadata-only.** `TrustRootStore` persists configuration (name, status, operator, key hash) — never key material, never used for verification.
28. **Feature-gated trust enforcement.** `trust_gate_enabled` defaults to `False` in `CapabilityLifecycleManager.__init__`. No trust gating occurs until explicitly enabled.
29. **No provenance raw path leak.** Source paths stored as SHA256 hashes. Provenance JSON never contains absolute filesystem paths.
30. **Tree hash excludes volatile artifacts.** Evals, traces, versions, quarantine audit/review/reports/plans directories excluded. Manifest computed fields (content_hash, created_at, updated_at) stripped before hashing. `.sqlite`, `.db`, `.pyc` files excluded.

## 12. Post-Phase-7 and Post-Phase-8 Audit References

### Post-Phase-7 Audit
- [Post-Phase-7 Security Audit](capability_post_phase7_security_audit.md) — lifecycle diagram, module-by-module audit, feature flag matrix, permission matrix, data layout, write paths, no-execution proof, quarantine isolation
- [Post-Phase-7 Audit Acceptance](capability_post_phase7_audit_acceptance.md) — E2E test results (4 flows, 14 tests), security audit summary, full suite regression, hard constraints verification
- [E2E Test File](../tests/capabilities/test_phase7_e2e_quarantine_to_testing.py) — 14 tests, 4 flows: happy path, malicious package, failure/rollback, dry run

### Post-Phase-8 Trust/Promotion Audit (2026-05-05)
- [Post-Phase-8 Trust / Promotion Security Audit](capability_post_phase8_trust_promotion_audit.md) — full lifecycle diagram, provenance/trust/signature/trust root components, stable promotion trust gate semantics, risk matrix, source-type matrix, trust level matrix, integrity/signature decision matrix, feature flag matrix, mutation path matrix, no-execution proof, no-crypto/network proof, retrieval/StateView non-impact proof, legacy compatibility, remaining risks, operational recommendations
- [Post-Phase-8 Audit Acceptance](capability_post_phase8_audit_acceptance.md) — 22 E2E tests (5 flows), trust gate behavior, no-mutation-on-denial proof, provenance immutability proof, runtime import audit, forbidden tool audit, crypto/network audit, 3072 regression tests (0 failures), known issues, rollback notes
- [E2E Test File](../tests/capabilities/test_phase8_e2e_trusted_promotion.py) — 22 tests, 5 flows: full happy path (external→stable), blocked promotion (untrusted/mismatch), high-risk reviewed denied, flag-off compatibility, legacy missing provenance
