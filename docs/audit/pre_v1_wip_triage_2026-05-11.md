# Pre-v1 WIP triage (2026-05-11)

> Required by Post-v1 B §0.2. Documents the working-tree state immediately
> before Post-v1 B work begins.

## Outcome

Pre-v1 uncommitted edits **committed** as `fe065ee` ("1") on master,
2026-05-11 20:42 +0800, after Post-v1 A acceptance. Working tree is now
clean. Post-v1 B begins on a clean base.

```
fe065ee 1                                       ← pre-v1 WIP (this triage)
0751ef0 PR-14: ResidentOperator runtime + V-A3 closed-loop  ← Post-v1 A
d5024ee PR-13: production kernel composition                ← Post-v1 A
55770bd test: assemble Slack-token fixture at runtime       ← v1 baseline
```

## Files in `fe065ee`

```
src/core/main_loop.py                      |  6 ++----
src/core/system_send.py                    |  9 +--------
src/tools/personal_tools.py                | 18 ------------------
tests/agents/test_agent_tool_dispatcher.py |  3 +--
tests/agents/test_agent_tools_v2.py        |  1 -
tests/agents/test_base_agent.py            |  3 +--
tests/agents/test_dynamic_agent.py         |  7 +++----
tests/agents/test_e2e_chain_trace.py       |  2 --
tests/agents/test_e2e_delegation.py        |  4 +---
tests/agents/test_e2e_dynamic.py           |  1 -
tests/agents/test_e2e_shim.py              |  1 -
tests/agents/test_registry.py              |  3 +--
tests/agents/test_registry_v2.py           |  2 --
tests/core/test_tool_dispatcher.py         | 16 ----------------
tests/tools/test_agent_tools.py            |  1 -
tests/tools/test_agent_tools_v2.py         |  1 -
16 files changed, 10 insertions(+), 68 deletions(-)
```

Pattern: pure **deletions** — 68 lines removed, 10 added. Looks like a
sweep that pruned dead branches in the legacy main-loop / tool-dispatcher
paths that Post-v1 A's STANDARD_PROFILE collapse already made
unreachable. No new functionality, no new tests, no new schemas. The
commit message ("1") is sparse but the diff signature is harmless
cleanup.

## Decision

**Accepted as committed.** The changes are all net-negative deletions
in legacy surfaces (system_send, main_loop, personal_tools) that
Post-v1 A's read_state/update_state migration left dormant. Post-v1 B
proceeds on top of `fe065ee` without further intervention.

Should regressions surface during Post-v1 B verification, `fe065ee` is
the first suspect alongside Post-v1 A's `0751ef0` / `d5024ee`. Revert
order from newest: `fe065ee` first (least invasive), then the Post-v1 A
pair.

## Out of scope for this triage

- No code review of `fe065ee` itself was performed beyond diff
  signature. If the deletions broke a less-trafficked surface, it will
  show up in PR-08 §15.1 e2e or full pytest. Both are green as of
  Post-v1 A acceptance.
- No retro-active commit-message rewrite. The repo's existing
  short-summary commit convention (cf. `92bbe22 2`, `b41ca59 1`) is
  preserved.
