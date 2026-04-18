# cleanup_report_step5.md — tell_user + Commitment 工具 + 体验层落地

Step 5 of the v2.0 recast. Replaces the heuristic "filter monologue from
LLM bare text" path with a **structural contract**: `tell_user` is the
only tool that delivers user-visible text, and `commit_promise` /
`fulfill_promise` / `abandon_promise` track every actionable promise.
The model can no longer ghost a task or claim work it never did without
leaving a hole in the audit trail.

Branch: `refactor/step5-tell-user` from master `193f095` (post-Step-4).
Final tag: `recast_v2_step5_complete`.

## §1 — Deletion clipboard

| Path | Status | Replacement / Reason |
|------|--------|----------------------|
| `src/logging/hallucination_patch.py` (124 LoC) | DELETED | Step 5 is the structural fix the patch was waiting for. tell_user + commit_promise replace the observation-only phrase matcher. |
| `tests/logging/test_hallucination_patch.py` (107 LoC, 12 tests) | DELETED | Covered by the M4 regression suite — Case 4 + Case 5 verify the structural guarantees the patch was probing for. |
| `MutationType.LLM_HALLUCINATION_SUSPECTED` enum entry | DELETED | No emitter remains. Audit reads `TELL_USER` + `COMMITMENT_*` instead. |
| `_check_hallucination` import + call site in `src/core/task_runtime.py` | DELETED | Same. The post-iteration hallucination probe is gone from the hot path. |
| `_INTERNAL_MONOLOGUE_PATTERNS` (21-entry list) + `_is_internal_monologue` heuristic in `src/core/brain.py` | DELETED | Replaced by structural rule: bare text from LLM is *always* `INNER_THOUGHT` trajectory, never user-visible. |
| `_send_with_split` closure inside `brain.think_conversational` | DELETED | `tell_user` is the only path to `send_fn`. `[SPLIT]` and fallback-newline mechanics are obsolete; multi-message replies are multiple `tell_user` calls. |
| `parts_sent` / `originals_sent` accumulators + the post-loop "send full_reply if not already_sent" fallback in `brain.think_conversational` | DELETED | `tell_user_buffer` (populated by the tool) is the new source for `memory_text`. The "send if not sent" fallback was the last bypass for the structural rule. |
| `# Step 4 M5: emit removed` breadcrumb comments (×2 in `brain.py`) | DELETED | Step 4 is shipped; the breadcrumbs served their merge-window purpose. |
| Unused imports in `src/core/brain.py`: `sanitize_outgoing`, `split_on_markers`, `split_on_paragraphs`, `MESSAGE_SPLIT_DELAY_BASE`, `MESSAGE_SPLIT_DELAY_MAX`, `MESSAGE_SPLIT_DELAY_PER_CHAR`, `MESSAGE_SPLIT_ENABLED`, `MESSAGE_SPLIT_FALLBACK_NEWLINE`, `MESSAGE_SPLIT_SINGLE_NL_MIN_LEN` | DELETED | Cascade from removing `_send_with_split`. The `MESSAGE_SPLIT_*` settings still exist in `config/settings.py` because `output_sanitizer` may consult them; nothing in the runtime uses them now. |
| `tests/core/test_brain_split.py` (10 tests, ~243 LoC) | REWRITTEN | Same file, 7 Step-5 contract tests instead. Old [SPLIT] semantics tests are dead. |

## §2 — `grep -rn` verification

```
$ grep -rn "_INTERNAL_MONOLOGUE_PATTERNS\|_is_internal_monologue\|_send_with_split" \
    src/ tests/ --include="*.py"
(no matches)

$ grep -rn "LLM_HALLUCINATION_SUSPECTED\|hallucination_patch\|_check_hallucination" \
    src/ tests/ --include="*.py"
src/core/task_runtime.py:442:                # patch (src/logging/hallucination_patch.py). Replaced by
src/logging/state_mutation_log.py:121:    # Step 5 cleanup: LLM_HALLUCINATION_SUSPECTED + hallucination_patch.py
(both are removal-rationale comments, not active references)

$ grep -rn "Step 4 M5: emit removed\|Step 4 M5: message.sent" src/ --include="*.py"
src/core/brain.py:750:            # Step 4 M5: message.sent dispatcher emit removed — SSE now
(one breadcrumb retained — explains a structurally important absence in
 the non-streaming brain.think path; not Step-5-relevant clutter)
```

## §3 — New TODO/FIXME

None added. Step 5 ships clean — the only deferred work is in §8 below
and each entry has explicit cleanup conditions.

## §4 — Test net change

| Bucket | Δ |
|---|---|
| Baseline (Step 4 final, master `193f095`) | 1182 |
| M1 — tell_user contract + brain rewrite | +5 (8 new tell_user, +7 new brain_split, −10 old brain_split) |
| M2 — commit/fulfill/abandon_promise tools + CommitmentStore overdue | +15 (15 new commitment_tool tests) |
| M3 — overdue surfacing in StateView | +7 (5 surfacing integration, +2 serializer overdue split) |
| M4 — voice.md + 8 regression cases | +8 |
| M5 — hallucination_patch removal | −12 (test_hallucination_patch.py deleted) |
| **Final** | **1205** (+23 net vs Step 4 baseline) |

```
$ python -m pytest tests/ -q | tail -1
1205 passed, 2 warnings in 180.89s
```

## §5 — Data / schema changes

| Subsystem | Change | Migration |
|---|---|---|
| `CommitmentStore.commitments` table | Two new nullable columns: `deadline REAL`, `closing_note TEXT` | `ALTER TABLE ADD COLUMN` runs every `init()`. Old rows transparently get `deadline=NULL` (= no expiry) and `closing_note=NULL`. |
| `CommitmentStore` indices | New `idx_commit_deadline` partial index on `WHERE deadline IS NOT NULL` | Created by `init()`; backs `list_overdue` queries efficiently without bloating the index for open-ended promises. |
| `CommitmentStore.create()` signature | Added `deadline: float \| None = None` kwarg | Backwards-compatible default. Existing call sites unaffected. |
| `CommitmentStore.set_status()` signature | Added `closing_note: str \| None = None` kwarg; SQL uses `COALESCE` so the new value only overwrites NULL | Backwards-compatible default. |
| `Commitment` dataclass | Two new fields with defaults: `deadline: float \| None = None`, `closing_note: str \| None = None` | Pure addition. |
| `MutationType` enum | Added `TELL_USER = "tell_user.invoked"`. Removed `LLM_HALLUCINATION_SUSPECTED`. | New event type ships in M1. Removed type was observation-only — no live consumer. |
| `CommitmentView` dataclass (state_view) | Added `is_overdue: bool = False` | Pure addition; default False keeps existing call sites compiling. |
| `ToolExecutionContext` dataclass | Added `send_fn: Callable[[str], Awaitable[Any]] \| None = None` | Optional kwarg, `None` for inner-tick / agent / heartbeat contexts. |
| `ToolLoopContext` dataclass | Added `send_fn` field with `None` default | Threaded through `complete_chat` → loop → `_execute_tool_call` → `execute_tool` → `ToolExecutionContext`. |
| `RuntimeProfile` constants | All four profiles add `communication` + `commitment` capabilities. The four `tool_names`-whitelist profiles add `tell_user` + the three promise tools. | Pure addition. `chat_shell` (capability-driven) gains them via the new capabilities; the three whitelist profiles list each name explicitly. |

## §6 — Exit invariants (Step 5 contract)

These are the structural guarantees Step 5 establishes. Each can be
falsified by `grep` or test failure — they are not aspirational.

1. **No code path delivers user-visible text except `tell_user_executor`.**
   Verified by `tests/core/test_brain_split.py::TestStep5InnerMonologueContract`
   and `tests/integration/test_step5_regression.py::TestCase2HallucinationFiltered`.
2. **Bare LLM text is written as `INNER_THOUGHT` trajectory, never to
   `send_fn`.** Same tests above.
3. **Every promise the model makes that requires tool execution has a
   matching `commit_promise` row in `CommitmentStore`.** Voice.md
   teaches this; the M4 regression Case 1 verifies the happy path.
4. **`commit/fulfill/abandon_promise` reject invented IDs.** Verified
   by `TestCase4CommitmentTrustWorthy`.
5. **Open promises past their deadline surface as `⚠️ 已超时的承诺` in
   the inner-tick prompt.** Verified by
   `tests/integration/test_step5_m3_overdue_surfacing.py`.
6. **`fulfill/abandon_promise` writes `closing_note` to both the row
   and the `COMMITMENT_STATUS_CHANGED` mutation payload.** Verified
   by `tests/tools/test_commitment_tools.py::TestFulfillPromise` and
   `TestAbandonPromise`.
7. **`tell_user` in a context with `send_fn=None` (inner tick / agent
   / heartbeat) returns `success=False` and does NOT raise.** Verified
   by `TestCase3SilentTick`.

## §7 — Architectural decisions

### Why kill the `_INTERNAL_MONOLOGUE_PATTERNS` heuristic?

The Step 1–4 hallucination patch was a string-matching probe that
recorded suspect phrases ("我刚才走神了", "在看了") to mutation_log when
no tool calls accompanied them. It was always intended as a Step-5
removal target — it could not actually prevent the bad output, only
flag it after the fact.

Step 5 fixes the underlying mechanism: the LLM cannot deliver text to
the user except through `tell_user`. If the model emits "我在看了" as
bare text (no tell_user wrapper), the user simply never sees it. If the
model wraps "我在看了" in `tell_user` while having made no commitment
and run no search, the audit trail (TELL_USER mutation + zero
COMMITMENT_CREATED in the iteration + zero TOOL_CALLED for search) is
the structural evidence — far more reliable than substring matching.

### Why `closing_note` instead of separate `result_summary` / `abandon_reason` columns?

One column, two writers. fulfill stores its result summary, abandon
stores its reason. The status field tells you which interpretation
applies. Adding two columns would make NULL semantics ambiguous (which
is "not set" vs "wrong status") and bloat the schema for no information
gain.

### Why ALTER TABLE ADD COLUMN at init() instead of a migration script?

SQLite ALTER TABLE ADD COLUMN is O(metadata) — it does not rewrite the
table. Idempotent via the PRAGMA-checked `_add_column_if_missing`
helper. For a column-addition migration this is strictly simpler than
threading a real migration runner into AppContainer for the only
schema change Step 5 makes.

### Why `tell_user` returns `success=False` (instead of silently no-op-ing) when `send_fn` is None?

The model needs to learn it doesn't have a user channel — silent
success teaches the wrong lesson. By returning `success=False` with
reason "用户通道", the next-round prompt shows the failure, the model
adjusts, and inner-tick chatter naturally redirects to actions that
make sense in that context (`send_proactive_message`, etc.).

### Why heading rename "我对 Kevin 的承诺" → "我对用户的承诺"?

Step 5 makes commitment tools available at GUEST level — anyone the
model talks to (QQ group members, friends, future channels) can
receive a promise. The original PromptBuilder heading hardcoded
"Kevin"; the channel-neutral phrasing fits the actual capability
boundary.

## §8 — Carryover debt registry

| Debt | Source | Cleanup time | Cleanup condition |
|------|--------|--------------|-------------------|
| `MESSAGE_SPLIT_*` settings unused at runtime | This Step | Step 6 (or whenever output_sanitizer is touched) | Confirm `output_sanitizer` doesn't consult them; then delete from `config/settings.py`. |
| `ConversationMemory.user_facts` facade still in DB | Step 3 D-1 | Step 6 memory layer compaction | Memory v2 (RAPTOR + vector) is the new write target; once `fact_extractor` and `auto_memory` either get rewired or get retired, drop the table + facade methods. |
| `ConversationMemory.reminders` / `todos` facade | Step 3 D-2 | Step 6+ | Evaluated this step: reminders ≠ commitments (reminders are time-triggered notifications, commitments are model-self-tracked promises). Different lifecycle. Keep both. Cleanup happens only if reminder semantics get folded into CommitmentStore later. |
| `durable_scheduler._fire_agent` calls `brain.think_conversational` directly | Step 4 D-3 | Step 6 (when MainLoop gets a richer event API) | Currently works correctly post-Step-5 (the result-relay through `_silent_send` + `self._send_fn` is awkward but correct). Deferring because EventQueue doesn't yet have a "fire-and-collect-result" event variant. |
| `MemorySnippets` field in StateView is always empty tuple | Step 3 C | Step 6/7 (memory layer build-out) | Builder placeholder; depends on Memory v2 retrieval pipeline landing. |
| `commit_promise.source_trajectory_entry_id = 0` sentinel | This Step | Step 6 (when trajectory→tool linkage gets a service slot) | Could chain to `services["last_tell_user_trajectory_id"]` updated by `tell_user_executor` so promises link to the message that triggered them. Skipping for now to keep M2 footprint small. |
| `IDENTITY_EDITED`, `MEMORY_RAPTOR_UPDATED`, `MEMORY_FILE_EDITED` MutationType members defined but unemitted | Step 1 (carried) | Step 6/7 (when identity/memory editor tools land) | Same status as Step 4. |

## §9 — Regression evaluation summary

The 5 failure cases identified in the Step 2 audit:

| Case | What it caught | Step 5 result |
|---|---|---|
| 1. Ghost task ("等我查一下" → no follow-through) | Model said it would do something, then didn't | **Structurally prevented**: tell_user + commit_promise pairs are voice.md-required; commitment without follow-up is now visible (overdue alert). Test: `TestCase1GhostTask`. |
| 2. Hallucination ("走神了" mid-conversation) | Model emitted recovery meta-text in user-facing reply | **Structurally prevented**: bare text is never user-visible; "走神了" written as text without tell_user goes to INNER_THOUGHT trajectory only. Test: `TestCase2HallucinationFiltered`. |
| 3. Silent tick (inner returns "") | Empty reply silently consumed downstream pipeline | **Behaviour preserved + made explicit**: tell_user with `send_fn=None` returns success=False; tests/scheduler logic remains correct. Test: `TestCase3SilentTick`. |
| 4. Commitment hallucination (claims promise that wasn't logged) | Predicted by Step 3 §9; LLM might fabricate IDs | **Structurally prevented**: fulfill/abandon with invented ID returns success=False. CommitmentStore is single source of truth. Test: `TestCase4CommitmentTrustWorthy`. |
| 5. False status report ("在看了" while overdue) | Model claims work-in-progress when actually idle | **Made visible, not blocked**: closing_note records exactly what the model said in mutation log. Combined with overdue surfacing in next inner tick, the contradiction becomes self-correcting (model sees ⚠️ 已超时 next time). Test: `TestCase5OverdueExposesFalseStatus`. |

New failure modes to watch for in production:
- **Tell_user-wrapped hallucination**: model wraps an unsupported claim
  in `tell_user(...)` to bypass the structural shield. Detection idea:
  cross-check tell_user content against the iteration's tool result
  payloads. Future Step item.
- **Promise without follow-up tool**: model calls commit_promise but
  never invokes the matching action tool. Detection idea: post-iteration
  audit "did this iteration have ≥1 non-tell_user, non-promise tool
  call?" Future Step item.

## §10 — Test count reconciliation

```
Step 4 final (193f095):          1182
+  M1 (tell_user contract):      +5    1187
+  M2 (commitment tools):        +15   1202
+  M3 (overdue surfacing):       +7    1209
+  M4 (regression cases):        +8    1217
+  M5 (hallucination_patch rm):  -12   1205
─────────────────────────────────────────
Step 5 final:                          1205
```

Net delta vs Step 4: **+23 tests** (added 35 / deleted 12). All 1205
green; no skips, no xfails introduced.
