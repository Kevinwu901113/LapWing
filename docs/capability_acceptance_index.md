# Capability Evolution System — Acceptance Documentation Index

**Date:** 2026-05-05
**Purpose:** Central index of all phase acceptance documents

---

## Acceptance Documents

| Phase | Document | Status | Capability Tests | Major Guarantees |
|-------|----------|--------|-----------------|-----------------|
| 0+1 | [Phase 0/1 Acceptance](capability_phase01_acceptance.md) | Accepted | Parsing, schema, hashing, IDs | Data model correct, no runtime wiring |
| 2A | [Phase 2A Acceptance](capability_phase2a_acceptance.md) | Accepted | Store, Index | Filesystem CRUD, SQLite FTS, version snapshots |
| 2B | [Phase 2B Acceptance](capability_phase2b_acceptance.md) | Accepted | Read-only tools | list/search/view tools, feature-gated, no mutation |
| 3A | [Phase 3A Acceptance](capability_phase3a_acceptance.md) | Accepted | Policy, Evaluator, Records, Promotion | Deterministic safety/quality checks, offline |
| 3B | [Phase 3B Acceptance](capability_phase3b_acceptance.md) | Accepted | Lifecycle Manager | Gated transitions, snapshots before mutation, fail-closed |
| 3C | [Phase 3C Acceptance](capability_phase3c_acceptance.md) | Accepted (Hardened) | Lifecycle Tools | evaluate/plan/transition tools, operator profile required |
| 4 | [Phase 4 Acceptance](capability_phase4_acceptance.md) | Accepted (Hardened) | 735 | CapabilityRetriever, progressive disclosure, compact summaries |
| 5A | [Phase 5A Acceptance](capability_phase5a_acceptance.md) | Accepted (Hardened) | 845 | TraceSummary, ExperienceCurator, CapabilityProposal, tools |
| 5B | [Phase 5B Acceptance](capability_phase5b_acceptance.md) | Accepted (Hardened) | 1,031 | Task-end execution summary capture, sanitized, best-effort |
| 5C | [Phase 5C Acceptance](capability_phase5c_acceptance.md) | Accepted | 1,080 | Curator dry-run at task end, in-memory only |
| 5D | [Phase 5D Acceptance](capability_phase5d_acceptance.md) | Accepted | 1,110 | Controlled auto-proposal persistence, 9 gates, proposal-only |
| 6A | [Phase 6A Acceptance](capability_phase6a_acceptance.md) | Accepted (Hardened) | 1,490 | AgentSpec metadata fields, policy lint, no runtime change |
| 6B | [Phase 6B Acceptance](capability_phase6b_acceptance.md) | Accepted (Hardened) | 1,597 | AgentCandidate, evidence model, candidate store, candidate policy |
| 6C | [Phase 6C Acceptance](capability_phase6c_acceptance.md) | Accepted (Hardened) | 1,510+ | Save gate for persistent capability-backed agents, feature-gated, default off |
| 6D | [Phase 6D Acceptance](capability_phase6d_acceptance.md) | Accepted (Hardened) | 323 | Candidate operator tools, save gate hardening, evidence freshness, operator profile, config semantics cleanup |
| 7A | [Phase 7A Acceptance](capability_phase7a_acceptance.md) | Accepted | 49 | External import into quarantine, inspect/import tools, safety validation, no execution, no network |
| 7B | [Phase 7B Acceptance](capability_phase7b_acceptance.md) | Accepted | 79 | Quarantine audit/review tools, deterministic local scan, report-only, no activation |
| 7C | [Phase 7C Acceptance](capability_phase7c_acceptance.md) | Accepted (Hardened) | 137 | Quarantine transition request bridge, request/list/view/cancel tools, operator-only, no activation, exhaustive gate + isolation + permission + safety hardening |
| 7D-A | [Phase 7D-A Acceptance](capability_phase7d_a_acceptance.md) | Accepted (Hardened) | 85 | Quarantine activation planner, plan_quarantine_activation tool, planner-only, no activation, no mutation, 12 audit categories verified |
| 7D-B | [Phase 7D-B Acceptance](capability_phase7d_b_acceptance.md) | Accepted (Hardened) | 65 | Quarantine activation apply, apply_quarantine_activation tool, operator-only, testing only, atomic copy with 18 gates, 14 audit categories verified |
| 8A-1 | [Phase 8A-1 Acceptance](capability_phase8a_acceptance.md) | Accepted (Hardened) | 228 | Provenance data model, tree hashing, trust policy, import/activation integration, fail-closed, exhaustive hardening |
| 8B-0 | [Phase 8B-0 Signature/Trust Model](capability_signature_trust_model.md) | Accepted (Design Lock) | 75 | Signature states, trust levels, trust root model, invariants, no implementation |
| 8B-1 | [Phase 8B-1 Acceptance](capability_phase8b_1_acceptance.md) | Accepted (Hardened) | 169 | Signature metadata parser, verifier stub, secret rejection, no crypto/network/verified |
| 8B-2 | [Phase 8B-2 Acceptance](capability_phase8b_2_acceptance.md) | Accepted (Hardened) | 114 | TrustRootStore, verifier stub expiry enforcement, duck-typing integration, safe fallback for non-dict stores |
| 8B-3 | [Phase 8B-3 Acceptance](capability_phase8b_3_acceptance.md) | Accepted (Hardened) | 94 | Trust root operator tools (list/view/add/disable/revoke), operator-only, metadata-only, no crypto/network/verified/trusted_signed, 10-section hardening audit |
| 8C-0 | [Phase 8C-0 Trust Gate Design Lock](capability_stable_promotion_trust_gate.md) | Accepted (Design Lock) | 50 | Stable promotion trust gate model, risk-specific rules, state distinctions, invariant tests, no wiring |
| 8C-1 | [Phase 8C-1 Acceptance](capability_phase8c_1_acceptance.md) | Accepted (Hardened) | 124 | Trust gate wiring (feature-gated), risk-specific gating, no mutation on denial, integration + regression + invariant tests |
| Post-8 | [Post-Phase-8 Trust/Promotion Security Audit](capability_post_phase8_trust_promotion_audit.md) | Accepted | 22 E2E | Full lifecycle diagram, risk/source/trust/integrity/signature matrices, feature flag matrix, mutation path matrix, no-execution/crypto/network proof, operational recommendations |
| Post-8 | [Post-Phase-8 Audit Acceptance](capability_post_phase8_audit_acceptance.md) | Accepted | 22 E2E + 3072 regression | 5 flows A-E, trust gate behavior, no-mutation-on-denial proof, provenance immutability proof, import/forbidden-tool/crypto-network audits, known issues, rollback notes |
| M-A | [Maintenance A: Health Report](capability_maintenance_health.md) | Accepted (Hardened) | 89 (66 report + 23 safety) | Read-only health audit, 10 checks, deterministic, no mutation/execution/network/LLM, corruption-tolerant, exhaustive hardening |
| M-B | [Maintenance B: Repair Queue](capability_maintenance_repair_queue_acceptance.md) | Accepted (Hardened) | 110 (88 functional + 22 safety) | Inert repair queue, health finding→item mapping, dedup, status lifecycle, no mutation/execution/network/LLM, no tools, exhaustive 10-section hardening |
| M-C | [Maintenance C: Repair Queue Tools](capability_maintenance_repair_queue_tools_acceptance.md) | Accepted (Re-hardened) | 81 (34 tools + 26 profile + 21 safety) | Operator-only tools, feature-gated, capability_repair_operator tag, 6 tools managing queue items only, no repair execution, exhaustive safety hardening |
| Post-Maint | [Post-Maintenance Consolidation Audit](capability_post_maintenance_audit_acceptance.md) | Accepted | 30 E2E + 3,353 regression | 4 E2E flows (health→queue, operator lifecycle, corruption tolerance, permissions/flags), full maintenance lifecycle consolidation, no new behavior/tools/flags, no repair execution |

## Full Suite Status (as of 2026-05-06 Post-Maintenance Consolidation)

- **All suites:** 0 failed
- **`tests/agents/`:** 545 passed
- **`tests/capabilities/`:** 2,583 passed
- **`tests/core/test_tool_dispatcher.py`:** 86 passed
- **`tests/core/test_state_view*`:** 42 passed
- **`tests/core/test_runtime_profiles_exclusion.py` / `tests/skills/` / `tests/logging/`:** 182 passed (combined)

| Post-7 Audit | [Post-Phase-7 Security Audit](capability_post_phase7_security_audit.md) | Accepted | 1,476 | Full lifecycle: import→audit→review→request→plan→apply, E2E, security audit |
| Post-7 E2E | [Post-Phase-7 Audit Acceptance](capability_post_phase7_audit_acceptance.md) | Accepted | 14 E2E | 4 flows, 0 failures, cross-phase consistency |
| 8A-1 Prov. | [Phase 8A-1 Provenance Design](capability_phase8a_provenance.md) | Implemented (Hardened) | 228 | Data model, tree hash algorithm, integration points, invariants, exhaustive hardening |
| 8B-1 Sig. | [Phase 8B-1 Signature Metadata Design](capability_phase8b_signature_metadata.md) | Implemented (Hardened) | 169 | CapabilitySignature, CapabilityTrustRoot, verifier stub, secret rejection |
| 8B-2 Store | [Phase 8B-2 Trust Root Store Design](capability_phase8b_trust_roots.md) | Implemented (Hardened) | 114 | TrustRootStore, verifier stub expiry enforcement, duck-typing with safe fallback |

## Architecture & Audit Reference

- [Phase 6C Save Gate Architecture](capability_phase6c_save_gate.md) — feature flag, gate application rules, candidate matching, evidence sufficiency, atomicity, legacy guarantees
- [Phase 6D Agent Candidate Tools](capability_phase6d_agent_candidate_tools.md) — tools, flags, permission model, save gate gap fixes
- [Phase 7A External Import Design](capability_phase7a_external_import.md) — tools, quarantine semantics, storage layout, safety guarantees
- [Phase 7B Quarantine Review](capability_phase7b_quarantine_review.md) — review semantics, audit report schema, tools, no-activation guarantee
- [Phase 7C Transition Requests](capability_phase7c_transition_requests.md) — transition request semantics, gates, storage, lifecycle relationship, future Phase 7D plan
- [Phase 7D-A Activation Planning](capability_phase7d_activation_planning.md) — activation planner module, data model, gates, storage, safety guarantees, tool surface audit
- [Phase 8B-3 Trust Root Tools](capability_phase8b_trust_root_tools.md) — trust root operator tools, feature flag, permission model, hard constraints, 10-section hardening audit
- [Phase 8C-0 Trust Gate Design](capability_stable_promotion_trust_gate.md) — stable promotion trust gate model, risk-specific rules, state distinctions
- [Phase 8C-1 Trust Gate Wiring](capability_phase8c_stable_promotion_gate.md) — feature-gated wiring, integration point, risk-specific behavior, hard constraints
- [Consolidated Architecture Overview](capability_system_overview.md) — phase summaries, component map, data flows, feature flags, tool matrix, data layout, mutation paths, safety boundaries
- [Consolidation Acceptance](capability_consolidation_acceptance.md) — audit results, test runs, all matrixes, verification findings
- [Post-Phase-7 Security Audit](capability_post_phase7_security_audit.md) — lifecycle diagram, all 5 Phase 7 modules, flag matrix, permission matrix, data layout, write paths, no-execution proof, quarantine isolation, E2E flows
- [Post-Phase-7 Audit Acceptance](capability_post_phase7_audit_acceptance.md) — E2E test results, security audit summary, full suite regression, hard constraints verification
- [Post-Phase-8 Trust/Promotion Security Audit](capability_post_phase8_trust_promotion_audit.md) — full lifecycle diagram, provenance/trust/signature/trust root components, stable promotion trust gate semantics, risk matrix, source-type matrix, trust level matrix, integrity/signature decision matrix, feature flag matrix, mutation path matrix, no-execution proof, no-crypto/network proof, remaining risks, operational recommendations
- [Post-Phase-8 Audit Acceptance](capability_post_phase8_audit_acceptance.md) — 22 E2E tests (5 flows A-E), trust gate behavior, no-mutation-on-denial proof, provenance immutability, import/forbidden-tool/crypto-network audits, 3072 regression (0 failures), known issues, rollback notes
- [Maintenance C: Repair Queue Tools](capability_maintenance_repair_queue_tools.md) — operator tools, feature flag, permission model, 6 tools, hard constraints
- [Post-Maintenance Consolidation Audit](capability_post_maintenance_audit.md) — lifecycle diagram, report/queue item schemas, tool surface matrix, permission matrix, mutation path matrix, no-repair proof, no-execution proof, runtime import boundary, remaining risks, Maintenance D guidance
- [Post-Maintenance Audit Acceptance](capability_post_maintenance_audit_acceptance.md) — 30 E2E tests (4 flows A-D), no-mutation proof, no-execution proof, full regression (3,353 tests, 0 failures), known issues, rollback notes
- [Known Failures](capability_known_failures.md) — detailed breakdown of all 13 pre-existing failures (all resolved)
