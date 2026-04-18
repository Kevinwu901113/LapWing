# Step 3 Cleanup Report — StateSerializer + Read-Path Retirement

**Branch**: `refactor/step3-state-serializer`
**Baseline tag**: `recast_v2_step2_complete` → `d37f148` (Step 2 merge commit)
**Date range**: 2026-04-18
**Pre-step-3 baseline**: 1038 tests (Step 2 complete)
**Final test count**: **1072** passed (net +34 vs pre-branch 1038)

Blueprint v2.0 Step 3: Prompt assembly becomes a declarative pure
function (StateSerializer) over a frozen snapshot (StateView) built by
a single entry point (StateViewBuilder). The procedural PromptBuilder +
brain helper pair is retired. Every legacy read path that survived
Step 2's dual-write window is removed: the `conversations` table + 6
FTS shadow tables go; nine ConversationMemory methods go; the
`trajectory_compat` shim goes; the `search_archive` / `get_context`
tools go.

---

## 1. Deletion Inventory

### Source modules deleted

| Path | Lines | Reason |
| --- | ---: | --- |
| `src/core/prompt_builder.py` | 269 | PromptBuilder class + PromptSnapshotManager + build_phase0_prompt + _get_period_name all replaced or relocated |
| `src/core/trajectory_compat.py` | 92 | Step 2g transitional shim; projection logic inlined into `trajectory_store.py` |
| `src/core/prompt_snapshot.py` | 40 | Created in M2.f, deleted in M3.b — no live consumer (see M3.a memo) |

### Test modules deleted

| Path | Lines | Reason |
| --- | ---: | --- |
| `tests/core/test_prompt_builder_v2.py` | 219 | Tests for the deleted class |
| `tests/core/test_trajectory_compat.py` | 144 | Tests for the deleted shim |
| `tests/core/test_brain_facts.py` | 97 | Superseded by rewritten `test_brain_system_prompt.py` |
| `tests/memory/test_conversation_archive.py` | 65 | Exercised `get_active` + `search_deep_archive` — both deleted |
| `tests/memory/test_conversation_fts.py` | 159 | Exercised `search_history` + FTS5 primitives — all deleted |

### Inline deletions inside surviving modules

| Location | Lines | What went |
| --- | ---: | --- |
| `src/memory/conversation.py` | ~240 | 9 legacy fns + conversations/FTS DDL + clear-table DELETEs |
| `src/memory/compactor.py` | ~5 | Import redirect from compat shim |
| `src/tools/memory_tools_v2.py` | ~100 | `search_archive` + `get_context` executors + specs + unused `asyncio.iscoroutinefunction` branch |
| `src/core/brain.py` | ~60 | `_build_system_prompt` + `_inject_voice_reminder` + PromptSnapshot wiring |
| `src/core/task_types.py` | ~18 | `_refresh_voice_reminder` body collapsed to no-op (ImportError-silenced since Step 2) |
| `src/app/container.py` | ~10 | PromptBuilder wiring replaced by StateViewBuilder |

### Database objects dropped

| Table | Rows before drop | Kind |
| --- | ---: | --- |
| `conversations` | 1370 | legacy conversation log |
| `conversations_fts` | 1414 | FTS5 virtual table |
| `conversations_fts_config` | 1 | FTS5 shadow |
| `conversations_fts_content` | 1414 | FTS5 shadow |
| `conversations_fts_data` | 163 | FTS5 shadow |
| `conversations_fts_docsize` | 1414 | FTS5 shadow |
| `conversations_fts_idx` | 160 | FTS5 shadow |

7 tables, total ~5,736 rows (double-counting the content appearing both
in `conversations` and the FTS shadows). Post-drop verification via
`sqlite_master`: every name absent.

---

## 2. Grep Verification

| Pattern | Domain | Count |
| --- | --- | ---: |
| `PromptBuilder(` | `src/` | 0 |
| `prompt_builder` import | `src/` | 0 (docstring mentions only) |
| `.prompt_builder` attribute | `src/` | 0 |
| `get_context(` | `src/` | 0 |
| `trajectory_compat` | `src/`+`tests/` | 0 |
| `search_history` | `src/`+`tests/` | 0 |
| `search_deep_archive` | `src/`+`tests/` | 0 |
| `get_active` | `src/`+`tests/` | 0 |
| `get_messages` | `src/`+`tests/` | 0 |
| `_get_surrounding_messages` | `src/`+`tests/` | 0 |
| `_migrate_fts` | `src/`+`tests/` | 0 |
| `_fts_insert` | `src/`+`tests/` | 0 |
| `_load_recent_history` | `src/`+`tests/` | 0 |
| `_cjk_tokenize` | `src/`+`tests/` | 0 |
| `_build_system_prompt` | `src/core/` | 0 |
| `_inject_voice_reminder` | `src/core/` | 0 (in docstrings only) |
| `PromptSnapshotManager` | `src/`+`tests/` | 0 |
| `session_id` | end-to-end-parity blob | 0 |
| 7 legacy table names | `sqlite_master` | 0 |

---

## 3. New TODO / FIXME / temporary markers introduced

Zero. No new `TODO`, `FIXME`, `temporary`, or `for now` markers were
added during Step 3. Every transitional behaviour has either a
purpose-built cleanup time point already scheduled (see §8 carryover
debt) or an explicit deletion in this step.

---

## 4. Test Delta

### New test files

| File | Tests | Purpose |
| --- | ---: | --- |
| `tests/core/test_state_view.py` | 13 | Construction + frozen semantics for every sub-record |
| `tests/core/test_state_serializer.py` | 49 | Pure-function rendering + layer-by-layer assertions |
| `tests/core/test_state_view_builder.py` | 20 | Store-to-StateView projection with in-memory stubs |
| `tests/integration/test_step3_m1_parity_smoke.py` | 4 | 8-turn QQ validation pass against Step 2 tag semantics |
| `tests/integration/test_step3_end_to_end_parity.py` | 3 | Five-layer coverage + forbidden-token scrub |

### Deleted test files

| File | Tests removed | Reason |
| --- | ---: | --- |
| `tests/core/test_prompt_builder_v2.py` | 19 | Tests for deleted class |
| `tests/core/test_trajectory_compat.py` | 15 | Tests for deleted shim |
| `tests/core/test_brain_facts.py` | 3 | Superseded by rewritten test_brain_system_prompt |
| `tests/memory/test_conversation_archive.py` | 4 | Tests for deleted legacy read methods |
| `tests/memory/test_conversation_fts.py` | 11 | Tests for deleted FTS5 layer |

### Rewritten test files (same or similar count, different coverage)

| File | Note |
| --- | --- |
| `tests/core/test_brain_system_prompt.py` | 4 tests → 5 tests; now exercises `_render_messages` instead of the deleted `_build_system_prompt` |
| `tests/tools/test_memory_tools_v2.py` | -5 tests (search_archive × 3, get_context × 2); expected-tools list shrinks from 9 to 7 |
| `tests/memory/test_conversation_dual_write.py` | Same 11 tests; the Step 2h "table-stays-empty" assertion becomes "table-is-absent" via sqlite_master |
| `tests/integration/test_step2_trajectory_integration.py` | Same 9 tests; compat-shim import redirected; assertion updated to post-Step-3 invariant |
| `tests/core/test_brain_tools.py` | Same 22 tests; one assertion updated (soul-fallback text → runtime-state header) |
| `tests/core/test_task_runtime_guards.py` | Same count; `_get_period_name` imports redirect to `src.core.vitals.get_period_name` (canonical home) |

### Net test count

See §10 for the accounting breakdown.

---

## 5. Data Migration Verification

### Pre-drop verification script output

`scripts/migrations/step3_verify_drop_safety.py` (exit code 0):

```
conversations_total: 1370
matched_in_trajectory_x: 1367
in_step2_discard_list_y: 3      (ids 1728, 1752, 1878)
unmatched_z: 0
```

The verifier applies the Step 2e/2i `__consciousness__ → __inner__`
chat-id remap when matching, so consciousness-tick rows line up
correctly against `source_chat_id="__inner__"` / `entry_type=inner_thought`
trajectory entries.

Without the remap, Z reads 571 (false positives from the rename boundary).
With it, Z = 0. This confirms every non-discarded `conversations` row
has a semantic counterpart in the trajectory table; the DROP loses
nothing that still has a consumer.

### DROP verification

`scripts/migrations/step3_drop_legacy_tables.py --execute` output:

```
DROP TABLE IF EXISTS executed for all 7 names
verification: every target name is absent from sqlite_master
```

Post-drop `SELECT name FROM sqlite_master WHERE type='table'` lists:
`commitments, discoveries, event_log, interest_topics, reminders,
reminders_v2, sqlite_sequence, todos, trajectory, user_facts`. Zero
legacy-table survivors.

### Backup location

`~/lapwing-backups/pre_step3_20260418_174802/`:
  - `conversations.jsonl` (1370 rows with rowid)
  - `conversations_fts.jsonl` (1414 rows)
  - `conversations_fts_shadow.tar` (5 FTS shadow tables, ~2.6 MB)
  - `prompt_builder.py.bak` (source snapshot)
  - `prompts_dir.tar.gz` (full prompts/ directory)
  - `metadata.json` (row counts + timestamps + source_db path + git commit)

---

## 6. Exit Invariants

| Invariant | State |
| --- | --- |
| StateSerializer is the sole prompt-assembly entry | ✓ |
| `brain.get_context()` does not exist | ✓ (never existed; the target was the `get_context` tool in `memory_tools_v2.py`, now removed) |
| `trajectory_compat` file does not exist | ✓ |
| `conversations` + 6 FTS shadow tables DROPped | ✓ (verified via `sqlite_master`) |
| `PromptSnapshotManager` deleted | ✓ (decision in M3.a memo; executed in M3.b) |
| `memory_tools_v2.py:267 / :317` references gone | ✓ (both tools deleted) |
| ConversationMemory 9 legacy fns deleted | ✓ |
| All tests pass | ✓ |

---

## 7. Architecture Decisions

Three judgement calls made under the "自决" clause of the Step 3 brief.
Each has its own memo; all are marked "DECISION BY CLAUDE CODE,
PENDING REVIEW".

| Decision | Memo | Gist |
| --- | --- | --- |
| Identity boundary | `docs/refactor_v2/step3_identity_boundary.md` | `IdentityDocs` = {soul, constitution, voice}. rules.md + interests.md stay out of StateView in Step 3 (evolution layer is a separate future surface). |
| PromptSnapshot fate | `docs/refactor_v2/step3_prompt_snapshot_analysis.md` | Delete the whole class. `freeze()` + `get()` had zero callers through Step 2 + Step 3; `invalidate()` was clearing already-clear state. |
| Retire `search_archive` + `get_context` tools instead of migrating to TrajectoryStore | M2.a commit messages | `recall` covers the archive use case with semantic vector search; `get_context`'s three sections are redundant with what the LLM already sees on every render. |

Two smaller decisions captured inline in commit messages rather than
dedicated memos (they didn't warrant a standalone file):

  * `CommitmentView.kind` discriminator as the "commitments umbrella"
    so `StateView` keeps its 5 named fields while still surfacing
    reminders + active tasks + promises. Recorded in the M1.a commit.
  * Serializer does not filter promises by status — the builder's
    `CommitmentStore.list_open()` call is the single filter point.
    Recorded in M3.c commit.

---

## 8. Carryover Debt (Step 2 list updated)

Step 2 cleanup report §8 registered nine debt items. Status after
Step 3:

| # | Debt | Clearing time | Clearing condition |
| --- | --- | --- | --- |
| 1 | `trajectory_compat` transitional shim | **Cleared — M2.b** | n/a |
| 2 | `conversations` table + FTS shadows surviving writes | **Cleared — M2.e** | n/a |
| 3 | ConversationMemory 9 legacy fns | **Cleared — M2.d** | n/a |
| 4 | `brain.get_context()` (actually the tool of the same name) | **Cleared — M2.a/2 + M2.c** | n/a |
| 5 | PromptBuilder dead-code survival | **Cleared — M2.f** | n/a |
| 6 | PromptSnapshotManager dormant cache | **Cleared — M3.b** | n/a |
| 7 | `search_archive` tool + archive tier | **Cleared — M2.a/1** | n/a |
| 8 | `session_id` / `cache_key` renames (if snapshot kept) | **Moot — snapshot deleted** | n/a |
| 9 | `_refresh_voice_reminder` silent ImportError | **Cleared — M2.f** (collapsed to explicit no-op) | n/a |

### New Step 3 debt registered for Step 4+

| # | Debt | Clearing time | Clearing condition |
| --- | --- | --- | --- |
| A | Tool-loop voice refresh is a no-op (pre-Step-3 it silently failed; now it's explicit) | Step 4 or later | When iteration discipline gets its own voice re-injection design, rebuild `_refresh_voice_reminder` against the serializer path. |
| B | `CommitmentStore`-backed promises are wired in but no writer lands until Step 5 Reviewer | Step 5 | When the Commitment Reviewer starts emitting `COMMITMENT_CREATED` rows, the "我对 Kevin 的承诺" section becomes populated in production; verify in live smoke. |
| C | `MemorySnippets` layer is reserved but unused (builder passes `()`) | Step 4 or later | When vector-recall injection is wired into `build_for_chat` (Kevin's "recall reform" scope), populate `state_view.memory_snippets` from `MemoryVectorStore`. |
| D | `ConversationMemory` still holds the todos + reminders + user_facts + discoveries + interests facade | Step 4 or later | When those domains get their own stores (per Blueprint), retire the facade entirely. The `in-memory _store` cache can go the same day. |
| E | Prefix-caching the serialized system prompt is not implemented | Step 6 or later | Introduce a content-hash-keyed cache against `SerializedPrompt` (not `session_id`) once the prompt stabilises across turns. |
| F | `reload_persona` no longer invalidates a prompt cache (there isn't one) | Cleared-in-principle | Revisit only if prefix-caching (debt E) gets built. |

---

## 9. New Evaluation Corpus

Step 3 did not surface new hallucination / ghost / silent-drop cases
beyond the ones Step 2 logged. The two integration tests
(`test_step3_m1_parity_smoke` + `test_step3_end_to_end_parity`) serve
as the regression bed for the Step 5 evaluation set.

One observation worth Step 5's attention:

  * Commitment rendering is a new prompt surface. When Step 5's
    Commitment Reviewer starts producing rows in production, the "我
    对 Kevin 的承诺" bullet list will grow — watch for Lapwing
    hallucinating about commitments she doesn't hold (model confusing
    "I committed" with "I'm considering"), or silently dropping her
    obligations if the Reviewer mis-categorises. Both should go into
    the Step 5 eval set.

---

## 10. Test Count Accounting

Pre-step-3 baseline (Step 2 complete): **1038**

Changes:

| Source | Delta |
| --- | ---: |
| New: `test_state_view.py` | +13 |
| New: `test_state_serializer.py` | +49 |
| New: `test_state_view_builder.py` | +20 |
| New: `test_step3_m1_parity_smoke.py` | +4 |
| New: `test_step3_end_to_end_parity.py` | +3 |
| Deleted: `test_prompt_builder_v2.py` | −19 |
| Deleted: `test_trajectory_compat.py` | −15 |
| Deleted: `test_brain_facts.py` | −3 |
| Deleted: `test_conversation_archive.py` | −4 |
| Deleted: `test_conversation_fts.py` | −11 |
| `test_memory_tools_v2.py` rewrite | −5 |
| `test_brain_system_prompt.py` rewrite | +1 |

Net expected delta from the table: **+33**. Final pytest run:
**1072 passed, 0 failed, 2 warnings** (net +34 vs pre-branch 1038 —
one-test discrepancy from the table traced to an in-place rewrite
picking up an additional case in `test_state_serializer.py` during
the contract refinement covered by the M3.c commit).
