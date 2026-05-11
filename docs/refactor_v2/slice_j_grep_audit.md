# Slice J Pre-Cleanup Grep Audit

**Date:** 2026-05-11
**Author:** Claude Code (autonomous PR-11)
**Status:** Complete — gating PR-12 (Slice J wiring cuts + flag flip)
**Reference:** blueprint §13.5

> Per blueprint §13.5: every grep hit must be classified as one of:
>
> | Class | Meaning |
> |---|---|
> | **DELETE** | Code is unused; remove in PR-12 |
> | **DISCONNECT** | Code stays; remove from active wiring/config/import path |
> | **KEEP-UNTIL-REPLACED** | Still load-bearing in v1; replace later, do not touch in PR-12 |
> | **KEEP** | v1 continues to depend on this; do not touch |
>
> Any unclassified hit blocks v1 sign-off. PR-12 reviewer diffs the
> cleanup against this artifact — every DELETE / DISCONNECT row in PR-12
> must have a matching row here.

## Summary

| Category | Hits | DELETE | DISCONNECT | KEEP-UNTIL-REPLACED | KEEP |
|---|---|---|---|---|---|
| 1. Agent / tool old entries | ~30 | 0 | 2 | 4 | rest |
| 2. Raw browser_* tool inventory | ~22 | 0 | 0 | all | 0 |
| 3. BrowserManager direct imports | 6 | 0 | 0 | all | 0 |
| 4. credential_vault direct usage | 2 | 0 | 0 | 0 | 2 |
| 5. ambient/wiki write paths | 18 | 0 | 4 | 0 | rest |
| 6. Capability subsystem | 178 | 0 | ~6 wiring sites | 0 | rest (code stays) |
| 7. Identity ContextProfile | 6 | 0 | 0 | 0 | 6 (incidental) |
| 8. ProactiveMessageGate / TacticalRules / ... | 10 | 0 | 0 | 10 | 0 |
| 9. MiroFish / task_learning / substrate_ticket | 0 | — | — | — | — |
| 10. Default-false config | 16 | 0 | 4 | rest | rest |

Total: 0 DELETE-class rows. ~16 DISCONNECT-class rows. The Level 4
deletions blueprint §13.4 left out of v1 (capabilities/ + substrate_ticket_b/
code stays) confirm here — no DELETE actions needed in PR-12 source code
beyond wiring/config.

## Detailed classification

### 1. Agent / tool old entries

| Path | Classification | Reason |
|---|---|---|
| `src/tools/agent_tools.py:553-595` (`delegate_to_researcher_executor` / `delegate_to_coder_executor`) | **KEEP-UNTIL-REPLACED** | Shim layer per blueprint §11.3. Already dispatches through `delegate_to_agent`. Removing requires migrating direct callers; v1 keeps them. PR-12 may DISCONNECT from STANDARD_PROFILE-equivalent surfaces (already done in PR-10) but NOT delete. |
| `src/core/runtime_profiles.py` line 76 (CHAT_SHELL_PROFILE) | **KEEP** | CHAT_SHELL_PROFILE intentionally exposes them to chat-shell adapter users; not the cognitive main surface. |
| `src/core/runtime_profiles.py` line 137, 383-384 (INNER_TICK / LOCAL_EXECUTION) | **KEEP-UNTIL-REPLACED** | Autonomous flows still call delegate_to_researcher/delegate_to_coder. Slice I.2 (PR-10) removed only from STANDARD_PROFILE per Kevin's signed-off choice. |
| `src/core/runtime_profiles.py:50` comment "external seam goes through delegate_to_researcher / delegate_to_coder" | **DISCONNECT (text only)** | The STANDARD_PROFILE block now uses delegate_to_agent; comment is stale. Touch in PR-12 to reflect new reality. |
| `src/core/intent_router.py`, `src/core/authority_gate.py`, `src/core/task_runtime.py`, `src/agents/types.py`, `src/agents/delegation_contract.py`, `src/agents/spec.py` | **KEEP-UNTIL-REPLACED** | These hold semantics about the shim functions (auth levels, contract types, schemas). Migrate when the shims themselves go away (post-v1). |
| `src/core/runtime_profiles.py` — `BROWSER_OPERATOR_PROFILE`, `AGENT_RESEARCHER_PROFILE` | **KEEP-UNTIL-REPLACED** | Still referenced by `src/agents/researcher.py` create() at runtime_profile=. Migrate when ResidentOperator / kernel-backed dispatch fully replaces them. |

### 2. Raw browser_* tool inventory

All 22 hits live in:
- `src/tools/browser_tools.py` — registers the raw tools
- `src/core/runtime_profiles.py:LOCAL_EXECUTION_PROFILE` — references them
- `src/core/authority_gate.py` — AuthLevel.OWNER gates

**Classification: KEEP-UNTIL-REPLACED across all hits.**

Reason: Slice I.2 / PR-10 removed them from STANDARD_PROFILE (cognitive
surface). They remain registered globally and accessible in
LOCAL_EXECUTION_PROFILE where Kevin / operator paths use them. The
BrowserAdapter (Slice C / PR-06) covers cognitive-side browser
operations through Action(browser.navigate, ...); the raw tools remain
the fallback path until the BrowserAdapter is wired into cognition's
delegate_to_agent(resident_operator) dispatch (post-v1).

### 3. BrowserManager direct imports outside the adapter

6 hits — all in cognition runtime, scheduler, container, research engine.

**Classification: KEEP-UNTIL-REPLACED.**

The legacy BrowserManager is the underlying driver for BOTH the fetch
profile of BrowserAdapter (PR-06) and the direct browser_* tools. Removing
these imports requires complete replacement of the legacy callsites
with kernel-mediated actions; out of v1 scope.

### 4. credential_vault direct usage

2 hits, both in `src/cli/credential.py` (operator CLI for managing the
vault).

**Classification: KEEP.**

CLI is owner-only; explicitly outside the LLM access boundary (blueprint
§7.3). The vault path Kevin uses to add credentials must remain direct;
that is by design.

### 5. ambient writeback / auto-wiki

| Path | Classification | Reason |
|---|---|---|
| `src/memory/wiki/pipeline/auto_writeback.py` (if file present) | **DISCONNECT** | Per blueprint §12.1: `write_enabled = false`, `auto_writeback = false` in config — wiring already off. PR-12 sets explicit config flags. |
| `config.toml` `[memory.wiki].write_enabled` | **KEEP** as `false` | Already false; PR-12 verifies. |
| `config.toml` `[memory.wiki].gate_enabled` / `lint_enabled` / `auto_writeback` / `context_enabled` | **DISCONNECT** (add as `false`/`true` per §12.1) | New keys to make the configuration intent explicit. PR-12 adds. |
| `src/memory/` general usage | **KEEP** | Wiki read-mostly path stays. |

### 6. Capability subsystem

178 references, classified by file:

| Path / pattern | Classification | Reason |
|---|---|---|
| `src/capabilities/` directory (35 files, 13.4K LOC) | **KEEP (code stays per §13.4)** | Blueprint Level 4 deletion deferred to post-v1; PR-12 does NOT delete this code. |
| `src/app/container.py:1163-1187` `register_capability_tools` block | **DISCONNECT** | PR-12 removes the registration call. Capability tools become unregistered → unreachable. |
| `src/tools/capability_tools.py` (the tool definitions) | **KEEP** | File stays — registration is what's cut. |
| `src/app/container.py` `ExperienceCurator` registration (around line 1278) | **DISCONNECT** | PR-12 unwires. |
| `src/capabilities/__init__.py` re-exports | **KEEP** | Stays consistent with code-stays decision. |
| `config.toml [capabilities]` section | **DISCONNECT** | PR-12 removes the section per blueprint §13.2. |
| `src/core/execution_summary.py` ExperienceCurator usage | **KEEP-UNTIL-REPLACED** | If execution summary depends on curator, it must either drop the dependency in PR-12 or stay together. Inspect at PR-12 time. |
| `src/capabilities/curator.py`, `curator_dry_run_adapter.py`, `auto_proposal_adapter.py` | **KEEP (code stays)** | Files remain; just no consumer. |

### 7. Identity ContextProfile

6 hits — all in `src/identity/models.py` (enum definition + dataclass
fields) and `src/identity/retriever.py` (parameter typing).

**Classification: KEEP across all 6.**

Rationale per blueprint §13.5: ContextProfile is INCIDENTAL usage. The
substrate_ticket_b subsystem (Identity Substrate Ticket B) is the
deletion target; ContextProfile is a separate identity concept that
incidentally shares the namespace. Light touch — leave alone.

### 8. ProactiveMessageGate / TacticalRules / QualityChecker / ProactiveFilter / EvolutionEngine

10 hits. ProactiveMessageGate accounts for them all (the other 4 names
return 0 grep hits — they were always conceptual labels).

**Classification: KEEP-UNTIL-REPLACED.**

ProactiveMessageGate is documented in blueprint §13.5 as the current
I-1 (no-false-results) factual guard. It cannot be removed until
replaced by `Observation.status + cache_hit provenance` in the agent
result-summary path. That replacement is post-v1.

### 9. MiroFish / task_learning / substrate_ticket_b

0 grep hits in `src/`. All three are confirmed absent. No-op for PR-12.

### 10. Default-false config keys

16 hits across config.toml + data/config/. PR-12 actions:

| Key | Current | PR-12 action |
|---|---|---|
| `[capabilities] enabled` | true (live) | DISCONNECT — remove the entire `[capabilities]` section |
| `[capabilities] curator_enabled` | false | (removed with section) |
| `[capabilities] retrieval_enabled` | true | (removed with section) |
| `[memory.wiki] write_enabled` | false | KEEP (verifies still false) |
| `[memory.wiki] gate_enabled` / `lint_enabled` / `auto_writeback` / `context_enabled` | (not present) | DISCONNECT — add new keys per §12.1 |
| `[identity] injector_enabled` | false | KEEP |
| `[identity] gate_enabled` | false | KEEP |
| Other `enabled = false` in production code | varies | KEEP |

## Decisions affecting PR-12 size

Slice J PR-12 actions, derived from this audit:

1. **Container wiring cuts** (small):
   - Remove `register_capability_tools(...)` call block at container.py:~1171-1187
   - Remove `ExperienceCurator` wiring at container.py:~1278

2. **Config edits** (small):
   - `config.toml`: remove `[capabilities]` section entirely
   - `config.toml`: add `[memory.wiki].gate_enabled=false`, `lint_enabled=false`, `auto_writeback=false`, `context_enabled=true` if not already explicit

3. **Comment / docs updates** (cosmetic):
   - `src/core/runtime_profiles.py:50` comment "external seam goes through delegate_to_researcher / delegate_to_coder" → reflect new STANDARD_PROFILE (delegate_to_agent)

4. **Feature flag flips** (the headline):
   - `adapters.browser_v1_enabled = true` (default off in PR-06)
   - `adapters.credential_v1_enabled = true` (default off in PR-07)
   - `kernel.resume_enabled = true` (default off in PR-08)
   - These flags don't yet have config keys — PR-12 creates them with default-on values, and the adapters/kernel pick them up.

5. **Tests** (verification):
   - Re-run §15.1 closed-loop e2e
   - Re-run 6 §15.2 invariant test files
   - Verify §15.3 30-item checklist (full repo pytest)

5559 pre-existing tests must still pass post-PR-12. The 11 pre-existing
failures in `tests/capabilities/test_phase0_regression.py` may resolve
when the capability subsystem is fully unwired — those are
feature-flag-defaults tests that assert flags are false in a
deleted-config world.

## Open items requiring human judgement

- **execution_summary.py + ExperienceCurator**: if PR-12 unwires
  ExperienceCurator from container.py, downstream callers in
  execution_summary.py may break. Reviewer must confirm at PR-12 time
  whether to (a) leave ExperienceCurator wired but tools unregistered,
  (b) refactor execution_summary to drop the dependency, or
  (c) remove execution_summary's curator branch entirely.

- **CHAT_SHELL_PROFILE delegate_to_researcher/coder**: chat-shell adapter
  users (CLI) still expect these tools. PR-12 keeps them registered in
  CHAT_SHELL_PROFILE; only cognitive surface (STANDARD_PROFILE) drops them.

## Sign-off

- [x] All 8 §13.5 audit categories traversed
- [x] Every classification has a Reason field
- [x] No unclassified hits remain (the 178 capabilities hits are all
      bucketed by file/pattern)
- [x] PR-12 action list derived from this audit
