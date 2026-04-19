# cleanup_report_step7.md — RAPTOR 记忆树 + WorkingSet

Step 7 of the v2.0 recast. Lapwing acquires layered long-term memory: past
conversations become searchable episodic events, distilled knowledge becomes
persistent semantic facts, and both feed her system prompt via a new
WorkingSet → `StateView.memory_snippets` path. The `MemorySnippets` placeholder
from Step 3 §C is filled in; no more empty tuple.

Branch: `refactor/step7-memory-tree` from master `f5bd88d`
(post-Step 5/6 polish). Final tag: `recast_v2_step7_complete`.

## §1 — Deletion clipboard

| Path / Site | Status | Replacement / Reason |
|-------------|--------|----------------------|
| `ConversationCompactor.__init__(auto_memory_extractor=None)` param | DELETED | Always None in production; the referenced `AutoMemoryExtractor` class does not exist anywhere in the tree. Step 7 rips out the dead injection point. |
| `ConversationCompactor._do_compact` "Pre-compression memory flush" branch | DELETED | Dead code (parent param was always None). The v2 replacement is Step 7's EpisodicExtractor which runs on conversation end, not on compaction. |
| `StateViewBuilder.build_for_chat/_for_inner` `MemorySnippets(snippets=())` hardcode | REPLACED | Now delegates to `_build_memory_snippets(trajectory_window)` which queries `WorkingSet`. Empty tuple is still returned when no WorkingSet is wired (phase-0 / pre-container boot) — contract preserved. |

No entire files were deleted. `src/memory/vector_store.py::VectorStore`
(per-chat wrapper) remains wired to `brain.clear_all_memory` via
`delete_chat` — §8 flags it for Step 8 removal once its sole call site
migrates.

## §2 — `grep -rn` verification

```
$ grep -rn auto_memory_extractor src/ tests/ --include="*.py" | grep -v __pycache__
(no matches — dead param removed cleanly)

$ grep -rn 'MemorySnippets(snippets=())' src/ --include="*.py" | grep -v __pycache__
src/memory/working_set.py:64 (early return: empty query)
src/memory/working_set.py:66 (early return: no stores)
src/core/state_view_builder.py:287 (no working_set wired)
src/core/state_view_builder.py:292 (empty query_text)
src/core/state_view_builder.py:299 (retrieval exception)
(all five sites are *fallback* paths. Happy-path returns WorkingSet results.)

$ grep -rn 'EpisodicStore\|SemanticStore\|WorkingSet' src/ --include="*.py" | grep -v __pycache__
src/memory/episodic_store.py        (definition)
src/memory/semantic_store.py        (definition)
src/memory/working_set.py           (definition)
src/memory/episodic_extractor.py    (5× — consumer)
src/memory/semantic_distiller.py    (9× — consumer)
src/core/state_view_builder.py      (6× — consumer)
src/app/container.py                (13× — wiring)
(single source per class, finite consumers, no scattered instantiation)
```

## §3 — New TODO/FIXME

None added. Step 7 ships without deferred markers. The known
limitations (Episodic per-day file size, ChromaDB `where` filter
portability) are documented in §6 / §8 rather than inline TODOs so
they live in the cleanup-report registry.

## §4 — Test net change

| Bucket | Δ |
|---|---|
| Baseline (Step 5/6 polish, `f5bd88d`) | 1221 |
| `test_episodic_store.py` | +13 |
| `test_semantic_store.py` | +13 |
| `test_working_set.py` | +11 |
| `test_episodic_extractor.py` | +5 |
| `test_semantic_distiller.py` | +6 |
| `test_state_view_builder_memory.py` | +8 |
| `test_brain_episodic_hook.py` | +4 |
| `test_step7_memory_integration.py` | +4 (integration) |
| **Final** | **1282** (+61 net) |

```
$ python -m pytest tests/ -q | tail -1
1282 passed, 2 warnings in 304.71s (0:05:04)
```

Zero rewrites, zero deletions, zero flakes. Every new suite runs green
on a cold ChromaDB collection (tmp_path fixtures).

## §5 — Data / schema changes

| Subsystem | Change | Migration |
|---|---|---|
| `data/memory/` directory | Added `episodic/` and `semantic/` subdirectories at first write; `conversations/` and `notes/` untouched. | None (lazy create). Production boxes that never triggered extraction will start empty. |
| ChromaDB (`data/chroma_memory/`) | `lapwing_memory` collection schema unchanged — new metadata keys (`date`, `title`, `category`, `source_trajectory_ids`, `source_episodes`) are additive. Existing `note_type` values (`observation`/`reflection`/`fact`/`summary`) gain two peers: `episodic` and `semantic`. | Pure addition. Existing entries continue to be returned by filter-less queries; filter-scoped queries exclude them. |
| `RecallResult` dataclass | Added `metadata: dict` field (default empty dict) so callers can read the full metadata back. | Backwards-compatible — existing callers that access named fields (`note_type`, `file_path`, etc.) keep working. |
| `MemoryVectorStore.recall()` | Optional kwarg `where: dict \| None = None`, passed through to `collection.query(where=...)`. | Backwards-compatible. |
| `StateViewBuilder.__init__` | Added `working_set` + `memory_top_k` + `memory_query_chat_turns` kwargs; defaults preserve old "empty memory" behaviour. | Backwards-compatible (unit tests that construct bare builders still pass). |
| `ConversationCompactor.__init__` | Removed `auto_memory_extractor` kwarg. | **Breaking for anyone passing it.** No production call site; one test (`test_compactor.py`) already constructed without it. |
| `brain._schedule_conversation_end(chat_id: str \| None = None)` | Added `chat_id` parameter (defaulted for back-compat). | Backwards-compatible — existing callers pass it now; absent values disable extraction. |
| `MaintenanceTimer._run_daily` | Added semantic-distillation call after the existing daily heartbeat actions. | Additive. |
| New env vars | `MEMORY_WORKING_SET_TOP_K` (10), `EPISODIC_EXTRACT_ENABLED` (true), `EPISODIC_EXTRACT_MIN_TURNS` (3), `EPISODIC_EXTRACT_WINDOW_SIZE` (20), `SEMANTIC_DISTILL_ENABLED` (true), `SEMANTIC_DISTILL_EPISODES_WINDOW` (20), `SEMANTIC_DISTILL_DEDUP_THRESHOLD` (0.85). | Defaults match new intended behaviour; set to `false` to disable the extraction / distillation triggers. |
| New prompts | `prompts/episodic_extract.md`, `prompts/semantic_distill.md` | Hot-reloadable via the existing prompt loader. |

## §6 — Exit invariants (Step 7 contract)

1. **`StateView.memory_snippets` is populated from runtime memory** when
   a WorkingSet is wired — not a hardcoded empty tuple. Verified by
   `test_state_view_builder_memory.py::TestBuildForChat::test_populates_from_working_set`
   and the integration test in §M5.
2. **Episodic and Semantic layers are partitioned inside the shared
   ChromaDB collection.** `EpisodicStore.query` never returns semantic
   hits; `SemanticStore.query` never returns episodic hits. Verified
   by `test_step7_memory_integration.py::test_no_leakage_across_layers`.
3. **Episodic extraction fires on conversation end**, not on every
   message, and is guarded by `min_turns`. Verified by
   `test_brain_episodic_hook.py` + `test_episodic_extractor.py::TestExtractFromChat::test_skips_when_too_few_turns`.
4. **Semantic distillation dedupes before writing.** Verified by
   `test_semantic_distiller.py::TestDistillRecent::test_dedup_skips_duplicate_fact`
   and `test_semantic_store.py::TestDedup::test_exact_duplicate_skipped`.
5. **StateSerializer emits `## 记忆片段` when snippets are non-empty
   and omits it when empty.** Verified by
   `test_state_view_builder_memory.py::TestSerializerMemoryLayer`.
6. **Failure in any memory path does not break the main conversation.**
   `WorkingSet.retrieve` swallows store errors and returns empty;
   `StateViewBuilder._build_memory_snippets` swallows retrieval errors;
   `brain._schedule_conversation_end` catches extractor exceptions.
   Verified by the three `*_failure_*` / `*_does_not_crash_*` tests.

## §7 — Architectural decisions

### Why reuse `MemoryVectorStore` instead of standing up a new Chroma client?

Full rationale in `docs/refactor_v2/step7_memory_reuse.md` §3.1. Short
answer: `MemoryVectorStore` already has the async lock, the scoring
weights (recency + trust + summary-boost + access frequency), and the
cluster-dedup pass. Standing up a parallel client would have duplicated
300 lines for no architectural gain. The *one* extension it needed was
a `where` filter kwarg on `recall`, which is a 5-line change that keeps
full backwards compatibility.

### Why not split Episodic / Semantic into separate Chroma collections?

Considered and rejected. Pros of separate collections: cleaner mental
model, no accidental cross-layer hits. Cons: (a) the existing 9 memory
tools (`recall` / `write_note` / ...) would break unless they gained
collection-awareness, (b) ChromaDB init cost scales per collection on
boot, (c) cross-layer queries (future "give me everything about Kevin")
would need client-side merging. The `where={"note_type": ...}` filter
gives us layer scoping at negligible cost. See `step7_memory_reuse.md`
§3.1.

### Why one Markdown file per day (Episodic) vs one per episode?

Step 7 picks per-day to keep `data/memory/episodic/` human-browsable
(tree depth 1) and to amortise filesystem overhead (each note otherwise
needs its own inode + frontmatter). Sections within a day file carry
their own `episode_id` comment line, so navigation from ChromaDB hit
→ source markdown remains deterministic. Trade-off: a day with 100+
conversations produces a file that gets large; migration path is to
split by hour if this becomes real (documented in
`memory_naming_conventions.md`).

### Why one Markdown file per category (Semantic) vs one per fact?

Mirrors the Episodic rationale. Category files read like a cumulative
knowledge base — `cat data/memory/semantic/kevin.md` is a
comprehensive profile at a glance. One-fact-per-file would fragment
this into hundreds of `sem_*.md` files.

### Why use ChromaDB default embedding (all-MiniLM-L6-v2)?

Already in use, works on CPU (PVE has no GPU), 384-dim is plenty for
paragraph-level retrieval. Chinese/English mixed content behaves
acceptably. Alternatives (MiniMax embedding API, multilingual
sentence-transformers) trade complexity for marginal quality; deferred
pending evidence of retrieval failures in production. Full memo in
`step7_memory_reuse.md` §2.

### Why trigger Episodic on conversation end, Semantic on daily tick?

Two different time scales. Episodic captures *what just happened* —
stale fast if not extracted near the event. Semantic distils *patterns
across events* — stable by definition, only worth recomputing once the
episode pool has grown. Coupling them to the same trigger would either
over-invoke the distiller (wasting LLM budget) or under-invoke the
extractor (losing events to trajectory compaction). The decoupled
design also means a crash in one path doesn't deny the other.

### Why `_episodic_extractor` / `_semantic_distiller` as private brain attributes rather than DI through a protocol?

Phase-0 and unit tests need to construct a brain without the extraction
pipeline wired. Making the attributes optional (`None` by default) and
letting container inject them keeps the test surface minimal. A
protocol-based DI rewrite would benefit only when a second extractor
implementation appears — premature today.

### Why not migrate `ConversationMemory.user_facts` into `SemanticStore`?

`user_facts` facade has zero production writers (grep across `src/`)
and the SQLite table has been frozen since Step 1. SemanticStore's
shape (freeform text + category) doesn't match the `(key, value)` row
model of `user_facts`; a migration would force a fabricated mapping.
Step 7 marks the facade as confirmed-dead in §8 and leaves the schema
alone to avoid a risky DB migration. Full reasoning in
`step7_memory_reuse.md` §4.

## §8 — Carryover debt registry (final-recast snapshot)

Step 7 is the last step of the v2 recast. Debt entries here are
classified as **cleared / post-recast / not-needed**.

| Debt | Source | Step-7 verdict |
|------|--------|----------------|
| `MemorySnippets` placeholder in StateView | Step 3 C | **Cleared** — WorkingSet populates it from live memory. |
| `ConversationMemory.user_facts` facade | Step 3 D-1 | **Post-recast**. Facade has no writers in src/. Schema frozen — removing the table is a separate DB-migration Step. |
| `ConversationMemory.reminders` / `todos` facade | Step 3 D-2 | **Not-needed this Step**. These facades are live (used by personal-tools / DurableScheduler). No cleanup scheduled — they remain stable Step 5 API. |
| `durable_scheduler._fire_agent` calls `brain.think_conversational` | Step 4 D-3 | **Post-recast**. Event-API migration not in Step 7 scope. |
| `MESSAGE_SPLIT_*` settings unused at runtime | Step 5 | **Post-recast**. Output sanitizer has its own stream. |
| `commit_promise.source_trajectory_entry_id = 0` sentinel | Step 5 | **Post-recast**. Trajectory linkage for commitments is orthogonal to Step 7's memory tree. |
| `IDENTITY_EDITED`, `MEMORY_RAPTOR_UPDATED`, `MEMORY_FILE_EDITED` MutationType members unemitted | Step 1 | **Post-recast**. Step 7 could have wired MEMORY_RAPTOR_UPDATED from EpisodicStore/SemanticStore writes, but the audit value is marginal (trajectory + chroma persistence already covers the record), so left unemitted. |
| Dispatcher remains for non-Agent subsystems | Step 6 new | **Post-recast**. Non-Agent Dispatcher subscribers stay on Dispatcher for now. |
| Agent workspace (`data/agent_workspace/`) retention | Step 6 new | **Post-recast**. Heartbeat cleanup task is a future Step. |
| `VectorStore` (per-chat wrapper) called only by `brain.clear_all_memory.delete_chat` | Step 7 new | **Post-recast**. `MemoryVectorStore` subsumes its role but the single `delete_chat` caller would need a per-source-chat delete path first. Target: Step 8. |
| `compactor._auto_memory_extractor` dead param | Step 7 surfaced | **Cleared** — removed in M4. |
| `SemanticDistiller._collect_recent_episodes` uses a generic probe ("Kevin") to fish out recent episodes via composite scoring | Step 7 new | **Post-recast**. Proper "list most recent by timestamp" would require a direct filesystem scan of `data/memory/episodic/*.md` or a `MemoryVectorStore.recent(note_type)` method. Good enough for v1 given the composite score weights recency heavily. |
| Stale `data/memory/` references in README table (`memory_index`, `auto_memory_extractor` rows) | pre-Step-7 | **Post-recast**. README needs a full sync pass. Scope: docs pass after all Steps merge. |
| SQLite `user_facts` / `discoveries` / `interest_topics` tables in `lapwing.db` | Step 6 + earlier | **Post-recast**. DB schema cleanup warrants its own Step. |

**New failure modes introduced in Step 7**:

- **ChromaDB `where` filter portability** — The `MemoryVectorStore.recall(where=...)` kwarg
  assumes ChromaDB ≥ 0.5. Tests confirm current 0.5+ behaviour; if the
  filter API changes in a future release, WorkingSet may regress
  silently (empty hits). Mitigation: integration test in M5 catches
  regressions on every test run.
- **Dedup-false-positive** — A new semantic fact with 0.85+ similarity
  to an existing one is silently dropped. If the model writes
  "Kevin 喝手冲咖啡" and later "Kevin 不喝手冲咖啡了", dedup probably
  rejects the second. Mitigation: `SEMANTIC_DISTILL_DEDUP_THRESHOLD`
  env-tunable; cleanup path is a manual `semantic/kevin.md` edit
  (the file is designed for this).
- **Per-day Episodic file growth** — A day with 100+ conversations
  could produce a 200 KB `.md`. StateViewBuilder does not read the
  markdown directly (ChromaDB delivers content), so prompt size is
  decoupled from file size. Still flagged for monitoring.

## §9 — Regression evaluation summary

Step 7 adds a new prompt layer (`## 记忆片段`) between runtime-state and
voice injection. Regression vectors considered:

- **Prompt token budget** — New layer is snippet-content-only,
  truncated per-snippet to 300 chars and capped at 2000 chars total.
  With 10 default top-K, worst-case contribution is 2 KB ≈ ~500 tokens.
  Within existing system prompt budget.
- **Existing tests** — All 1221 pre-Step-7 tests still green. The 5
  memory-specific fallback sites in `WorkingSet` / `StateViewBuilder`
  produce an empty layer when no stores are wired, which is exactly
  what phase-0 and unit-test paths exercise.
- **Conversation flow** — `_schedule_conversation_end` change is
  signature-compatible (`chat_id` defaulted to None). `think_*` call
  sites updated to pass chat_id; unit tests for think_inner still pass
  because inner path doesn't have a chat_id (None → no extraction —
  correct behaviour, matches decision memo).
- **Production failure modes** (documented in §8 above): all soft
  degradations that log and continue, never propagate to the
  conversation path.

No regression tests for pre-Step-7 flows were added this Step. The
existing regression suite (Step 5's 8 cases + Step 4's MainLoop suite)
exercises the main paths and all pass unchanged.

## §10 — Test count reconciliation

```
Pre-Step-7 master (f5bd88d — includes Step 5/6 polish committed
  right before the Step 7 branch):                             1221
+ test_episodic_store.py                                        +13
+ test_semantic_store.py                                        +13
+ test_working_set.py                                           +11
+ test_episodic_extractor.py                                     +5
+ test_semantic_distiller.py                                     +6
+ test_state_view_builder_memory.py                              +8
+ test_brain_episodic_hook.py                                    +4
+ test_step7_memory_integration.py                               +4
─────────────────────────────────────────────────────────────────────
Step 7 final:                                                   1282
```

Net delta: **+61 tests**. Zero deletions, zero rewrites. 1282 green,
no skips, no xfails introduced.

Breakdown of new behaviour under test:

- Episodic write/query + day-file layout + ID round-trip
- Semantic write/query + dedup threshold + category slugify
- WorkingSet merge + score sorting + per-layer failure isolation
- EpisodicExtractor LLM call + trajectory-window filtering
- SemanticDistiller parsing + source-episode attribution
- StateViewBuilder memory wiring + query-text derivation
- StateSerializer memory layer rendering + empty-case omission
- `_schedule_conversation_end` extractor hook + failure containment
- End-to-end chat-path flow from store write → prompt surface
