# Step 3 M3.a — PromptSnapshotManager analysis + decision

**DECISION BY CLAUDE CODE, PENDING REVIEW**

Date: 2026-04-18
Branch: `refactor/step3-state-serializer`
Scope: What to do with `PromptSnapshotManager` after M1 + M2 retire its
reason for existing.

## Call-site audit (post-M2)

`grep -rn "PromptSnapshotManager\|_prompt_snapshot\|prompt_snapshot" src/ tests/`:

| Location | What it does |
| --- | --- |
| `src/core/prompt_snapshot.py:12` | class definition (relocated from `prompt_builder.py` in M2.f) |
| `src/core/brain.py:127` | `from src.core.prompt_snapshot import PromptSnapshotManager` |
| `src/core/brain.py:128` | `self._prompt_snapshot = PromptSnapshotManager()` |
| `src/core/brain.py:216` | `self._prompt_snapshot.invalidate()` (inside `reload_persona()`) |
| `src/core/brain.py:234` | `self._prompt_snapshot.invalidate()` (inside `switch_model()`) |
| `src/core/brain.py:241` | `self._prompt_snapshot.invalidate()` (inside `reset_model()`) |

**No other references anywhere**: no `freeze()` call, no `get()` call,
no test file touches the class, no route or subsystem consults it.

## Behavioural implication

The manager's intent was to cache the rendered system prompt per
session to maximise Anthropic's prefix-cache hit rate. The design
required all three methods working together:

  1. `freeze(session_id, prompt)` — cache the first render.
  2. `get(session_id)` — reuse the cached render on subsequent turns.
  3. `invalidate()` — clear the cache on persona reload / model switch.

Steps 1 + 2 were never wired. The old `brain._build_system_prompt`
built the prompt from scratch on every turn, ignoring the cache. So
`get()` always returned `None`, `freeze()` was never called, and the
three `invalidate()` calls in reload_persona / switch_model /
reset_model clear already-clear state.

After Step 3's switch to StateSerializer, the situation is the same:
serialize() runs every call and the cache is never populated. The
class has been dead code since before it was moved to its own module.

## Decision

**Delete `PromptSnapshotManager` entirely, plus the three call sites.**

Rationale:

1. No live consumer — removing it cannot break any observable
   behaviour.
2. Keeping it sustains the illusion of a prefix-cache layer that
   doesn't exist. Future readers will assume brain caches prompts
   when it actually doesn't. That's confusing and wastes debug
   cycles.
3. Prefix-caching the system prompt has real value once the prompt
   stops changing every call (Step 3 made it more stable by moving
   to a pure-function serializer). When we want that optimisation,
   we'll want to design it against the `SerializedPrompt` shape, not
   retrofit an abandoned session-keyed cache built for a different
   era. A fresh design is simpler than unblocking the dormant one.
4. `session_id` as a cache key in particular is the wrong primitive:
   v2.0 Blueprint retires per-chat `sessions` (§ Attention). A new
   cache would key off the SerializedPrompt's own byte content hash,
   not a session ID — which is precisely why M3's spec asks "rename
   to cache_key" as an alternative. Neither form is worth preserving
   today.

## Rollback path

If `invalidate()` call sites turn out to have semantic meaning I
missed (they're wired to an IDE / CLI surface, a hook reads them for
observability, etc.):

  * restore `src/core/prompt_snapshot.py` from git history
    (commit where M2.f relocated it: `61aa08a`)
  * restore the three `self._prompt_snapshot.invalidate()` lines

Both are pure reverts. No data migration, no schema change.

## Why this memo exists

The Step 3 spec allowed me to make this call unilaterally, subject to
pending review. I'm recording the reasoning here so that (a) Kevin
can override if the decision is wrong, (b) a future Claude session
auditing Step 3 sees the intent behind the deletion, and (c) the
rollback path is explicit rather than buried in git blame.
