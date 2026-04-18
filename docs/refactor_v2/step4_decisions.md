# Step 4 — MainLoop Unification: Decision Memos

Living document. Each entry records a judgement call made while
executing the Step 4 plan that the spec didn't pin down. Format:

> **D-N**: One-line summary
> *Made at*: M-x.y
> *Choice*: …
> *Why*: …
> *Carryover*: open / closed-by-commit-X

---

## D-1: Branched from current master, not from `recast_v2_step3_complete`

*Made at*: pre-M1 (branch creation)
*Choice*: Branch `refactor/step4-main-loop` cut from `master` (HEAD
`c20d1ed`), which is **6 feature commits ahead** of the
`recast_v2_step3_complete` tag (`16f9814`). Those commits merged
`feat/life-v2-api` and the `IdentityFileManager` unification.
*Why*: The Step 4 spec said "current HEAD: master (`recast_v2_step3_complete`)",
which was true at spec-writing time but no longer is. Branching from
the tag would lose `life_v2.py` API routes (~432 LoC) and the
identity unification work. Branching from master keeps the codebase
linear and makes the eventual merge a fast-forward. Step 4's scope
(MainLoop + EventQueue + adapter migration) is largely orthogonal to
the life_v2 routes.
*Carryover*: `_refresh_voice_reminder` (Step 3 Debt A) may already be
resolved by `IdentityFileManager`'s `on_after_write=clear_prompt_cache`
hook — re-evaluate at M4.d before deleting the no-op.

---

## D-2: MessageEvent carries optional typing_fn / status_callback / done_future

*Made at*: M2 (event design)
*Choice*: The spec's MessageEvent shape (chat_id / user_id / text /
images / adapter / send_fn / auth_level) was extended with three
optional fields: `typing_fn`, `status_callback`, `done_future`.
*Why*: The desktop `/ws/chat` route currently wraps three callbacks
(send/typing/status) and **awaits the brain call** so it can emit a
final `{"type":"reply", "final": true}` after the turn completes. If
MessageEvent only had `send_fn`, we'd have to either (a) hide
typing/status inside a closure on send_fn (couples channels to brain
internals) or (b) drop typing indicators on desktop (regression). The
done_future lets producers that need synchronous semantics await the
handler's reply; QQ producers leave it `None` and fire-and-forget.
*Carryover*: closed.

---

## D-3: DurableScheduler `_fire_agent` keeps calling `brain.think_conversational` directly (Step 4 scope deferred)

*Made at*: M2 (producer audit)
*Choice*: `src/core/durable_scheduler.py:_fire_agent` still invokes
`self._brain.think_conversational(chat_id="__scheduler__", ...)`
directly instead of enqueuing a MessageEvent. Not migrated in Step 4.
*Why*: The Step 4 spec's M2 Exit criteria explicitly enumerate "QQ /
Desktop adapter 不再直接调 brain" — adapters only. The scheduler is
not an adapter; it's an internal scheduled-task driver. Migrating it
through MainLoop would force a fake "user message" abstraction (or a
new event subclass), and the design question of how scheduled
agent-mode tasks express completion semantics differently from user
messages is genuinely out of scope for Step 4. Leaving it alone keeps
the M2 surface tight.
*Carryover*: open. Step 5+ should add a `ScheduledTaskEvent` (or
similar) so this last brain call site goes through MainLoop too.
Tracked in cleanup_report_step4.md §8.

---

## D-4: MainLoop starts in `start()` (after prepare), stops in `shutdown()` after channels stop

*Made at*: M2 (container wiring)
*Choice*: `MainLoop.run()` is launched as an asyncio task in
`AppContainer.start()`, immediately before `channel_manager.start_all()`.
Stopped in `shutdown()` after channels stop, before brain teardown.
*Why*: The loop must exist before adapters fire so the very first
`MessageEvent.put` has a consumer waiting. Stopping after channels
stop ensures no in-flight adapter callbacks get orphaned mid-enqueue.
The order matters: API stop → channels stop → MainLoop stop → brain
close.
*Carryover*: closed.

---

## D-5: EventQueue is constructed in `AppContainer.__init__`, not `prepare()`

*Made at*: M2 (container wiring)
*Choice*: `self.event_queue = EventQueue()` lives in `__init__`, not
`prepare()`.
*Why*: `LocalApiServer` needs the queue reference at construction
time (passed to `chat_ws.init`), which happens in `__init__`. The
queue itself is a thin asyncio.PriorityQueue wrapper — no I/O, no
state — so building it in `__init__` is safe and avoids ordering
gymnastics.
*Carryover*: closed.
