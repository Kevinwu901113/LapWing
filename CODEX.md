# CODEX.md

This document is for future Codex sessions only. It is a fast project briefing and a current health note for `/home/kevin/lapwing`.

Last updated: 2026-04-24.

## What Lapwing Is

Lapwing is a long-running personal AI companion for Kevin, not a stateless chatbot. The backend is Python 3.12+ and the desktop client is Tauri v2 with React/TypeScript.

The product goal is a persistent agent that can:

- converse through QQ and desktop channels;
- maintain identity, memory, commitments, reminders, and inner thoughts;
- use tools for research, browser automation, shell, images, reminders, and delegation;
- run autonomous inner ticks while no user message is pending.

The important quality bar is not just "LLM answers correctly"; it is whether the harness lets the same model behave naturally, quickly, accurately, and visibly to Kevin.

## Repository Map

- `main.py`: process entrypoint. Creates `AppContainer`, starts channels, API server, and long-running loops.
- `src/app/container.py`: dependency wiring. Builds `LapwingBrain`, `LLMRouter`, channels, memory stores, tools, browser, reminders, inner tick scheduler, mutation log, and API routes.
- `src/core/brain.py`: conversation orchestration. Builds prompt state, calls `TaskRuntime`, sends direct bare-text replies through `send_fn`, and records spoken text to trajectory/memory.
- `src/core/task_runtime.py`: tool loop. Chooses chat tools, calls `LLMRouter.complete_with_tools`, executes tools, tracks loop/error guards, and finalizes output.
- `src/core/llm_router.py`: provider abstraction for Anthropic-compatible, OpenAI, Codex Responses API, retries, payload normalization, and model routing.
- `src/core/state_serializer.py`: converts `StateView` into model messages and injects identity/persona/voice/runtime state.
- `src/core/inner_tick_scheduler.py`: builds autonomous inner tick prompt and schedules future ticks.
- `src/tools/`: LLM-facing tools. Current normal conversation uses direct bare-text output; `send_message` is for proactive/no-conversation contexts.
- `src/research/`: search, fetch, and refine pipeline behind the `research` tool.
- `src/memory/`: compaction, vector/episodic/semantic memory, working set, and stores.
- `src/adapters/`: QQ and desktop channel adapters.
- `src/api/`: FastAPI server and desktop/life/debug routes.
- `desktop-v2/`: Tauri v2 desktop UI; `desktop-v2/src/` is React/TS, `desktop-v2/src-tauri/` is Rust.
- `prompts/`, `data/identity/`: live prompt and identity material.
- `data/`, `logs/`: runtime state, SQLite DBs, browser profile/screenshots, generated artifacts, and logs. Treat as volatile unless a task explicitly asks for fixture work.

## Core Runtime Flow

1. `main.py` starts `AppContainer`.
2. `AppContainer.start()` initializes stores, tools, channel adapters, `MainLoop`, `InnerTickScheduler`, `DurableScheduler`, and the API server.
3. A QQ/desktop user event goes into `EventQueue`.
4. `MainLoop` calls `LapwingBrain.think_conversational(...)`.
5. `Brain` builds recent trajectory/history, trust tags Kevin messages, renders `StateView`, and calls `_complete_chat`.
6. `_complete_chat` calls `TaskRuntime.complete_chat` with chat tools.
7. `TaskRuntime` repeatedly asks `LLMRouter.complete_with_tools`, executes tool calls, appends tool results, and stops on completion.
8. Current conversational mode sends bare assistant text through `send_fn`, split on blank lines. Text accompanying tool calls is treated as internal and not sent.

This design has changed over time. Some comments and telemetry names still mention `tell_user`, so verify the current code path before relying on old Step 5 docs.

## Non-Negotiable Invariants

- In current `think_conversational`, bare model text is user-visible output. Tool-call companion text is internal and is not sent.
- `send_message` is for proactive messages from inner ticks/reminders/agent flows, not the normal reply path.
- `StateMutationLog` and `TrajectoryStore` are the main evidence sources for what actually happened.
- Inner ticks are not user turns. They must not casually message Kevin unless there is a real reason.
- The model is allowed many tools, but too many always-on tools increase selection entropy and latency.
- Do not trust old docs blindly. Verify against current code, `config.toml`, SQLite data, and logs.

## Build And Test Commands

Backend:

```bash
pip install -r requirements.txt
PYTHONPATH=. python -m pytest tests/ -x -q
PYTHONPATH=. python -m pytest tests/core/test_task_runtime.py -q
python -m compileall -q src tests
python main.py auth list
python main.py
```

Frontend:

```bash
cd desktop-v2 && npm run dev
cd desktop-v2 && npm run tauri dev
cd desktop-v2 && npm run build
```

Useful diagnostics:

```bash
git status --short
tail -120 logs/lapwing.log
tail -80 logs/libraries.log
python - <<'PY'
import sqlite3
conn = sqlite3.connect("data/lapwing.db")
print(list(conn.execute("select entry_type, count(*) from trajectory group by entry_type order by count(*) desc")))
conn.close()
PY
```

## Current Health Snapshot

The strongest current hypothesis for "Lapwing feels worse than the same model in OpenClaw/Hermes/Claude Code/Codex" is harness degradation, not raw model capability.

Observed runtime data from local DBs on 2026-04-24:

- `trajectory`: `inner_thought` 977, `assistant_text` 442, `user_message` 418, `tell_user` 98.
- `mutation_log`: `llm.request` 1995, `tool.called` 1136, `tool.result` 1128, `tell_user.invoked` 98.
- LLM requests by chat: `_inner_tick` 1459, Kevin chat `919231551` 499, null 37.
- Tool failures are concentrated in `execute_shell`, `read_file`, `send_message`, `browser_open`, `write_file`, and `browse`.
- Commitments are barely used: only 2 rows total, despite prompts requiring promises.

This means most model spend is inner activity, not user-visible interaction. The current direct-output path removes one old `tell_user` bottleneck, but stale comments/telemetry still make this area easy to misread.

## High-Priority Findings From 2026-04-24 Audit

### 1. Research fetch proxy path is currently broken

`src/research/fetcher.py` passes `proxies=` to `httpx.AsyncClient`. Installed `httpx` is 0.28.1, whose `AsyncClient` accepts `proxy=`, not `proxies=`.

Effect: every proxied fetch fails before making a request, then falls back to browser fetch, which is slower and often times out.

Look for:

- `AsyncClient.__init__() got an unexpected keyword argument 'proxies'`
- `src/research/fetcher.py` around `_httpx_fetch_with_decision`
- `tests/research/test_fetcher.py`, which currently expects the old `proxies` kwarg and should be updated with the fix

### 2. Research refinement is unreliable on Anthropic-compatible MiniMax/Volcengine

Logs show `complete_structured` often fails with "Anthropic did not return tool call", then text fallback expects JSON but gets normal refusal/apology text.

Effect: `research` may have search/fetch evidence but return low-quality or empty answers.

Look at:

- `src/research/refiner.py`
- `src/core/llm_router.py::complete_structured`
- `logs/lapwing.log` entries containing `Refiner JSON 解析失败`

### 3. Direct-output mode can double-send final bare text

`TaskRuntime._run_step` calls `ctx.on_interim_text(final_text)` before returning a final reply when there are no tool calls. `Brain.think_conversational` then sends the returned `full_reply` again as the "tail".

Effect: plain text responses can be sent twice. Existing `tests/core/test_brain_split.py` checks containment, not exact send count, so this can slip through.

Relevant files:

- `src/core/task_runtime.py`
- `src/core/brain.py::think_conversational`
- `tests/core/test_brain_split.py`

### 4. Stale `tell_user` references remain after direct-output migration

Current code and `tests/core/test_brain_split.py` say bare model text is user-visible. However comments in `TaskRuntime`, `system_send`, `StateMutationLog`, `TrajectoryEntryType`, and `CommitmentStore` still refer to `tell_user` as the user-facing path.

Effect: maintainers and future agents can debug the wrong architecture, and commitment source IDs still depend on `last_tell_user_trajectory_id`.

Relevant files:

- `src/core/task_runtime.py`
- `src/core/brain.py::think_conversational`
- `src/core/system_send.py`
- `src/tools/commitments.py`

### 5. Inner tick dominates model spend and creates noisy behavior

`_inner_tick` has far more LLM requests than the Kevin chat. The inner prompt still frames the tick as "free time" and offers many possible actions. Logs show repeated 120s inner tick timeouts and proactive reminders that cannot be sent.

Effect: latency/cost/noise increases while user-visible quality does not.

Relevant files:

- `src/core/inner_tick_scheduler.py`
- `src/core/main_loop.py`
- `src/core/durable_scheduler.py`

### 6. Shell safety blocks legitimate project-root paths when running as root

Runtime logs show commands under `/home/kevin/lapwing` rejected as "other user directory `/home/kevin`". In `src/tools/shell_executor.py`, `_CURRENT_USER = getpass.getuser()`. If the service runs as root, Kevin's home is treated as another user's directory.

Effect: Lapwing fails its own diagnostics and scratch-pad writes, then burns retries.

Relevant files:

- `src/tools/shell_executor.py`
- `logs/shell_execution.log`
- `config.toml [shell] default_cwd`

### 7. `send_message` desktop path imports a non-existent module

`src/tools/personal_tools.py` imports `from src.adapters.desktop import DesktopAdapter`, but the actual adapter is `src.adapters.desktop_adapter.DesktopChannelAdapter`.

Effect: `send_message(target="kevin_desktop")` can report `desktop_not_connected` even when a desktop adapter exists.

### 8. Corrections are not durable learning

`CorrectionManager` stores violations in in-memory dictionaries only. `add_correction` depends on the model choosing to call it and does not persist rules into durable memory or a prompt-injected playbook.

Effect: Kevin's corrections do not reliably survive restart or influence future turns.

Relevant files:

- `src/core/correction_manager.py`
- `src/tools/correction_tools.py`
- `src/memory/working_set.py`

### 9. Loop detection exists but is disabled in live config

`config.toml [loop_detection] enabled = false`, while logs show repeated failures and retries. `TaskRuntime` has loop detection code, but it exits early when disabled.

Effect: repeated tool failures are handled mostly by local error burst guards, not a durable cross-turn circuit breaker.

### 10. Browser guard was removed, but browser tools remain high-impact

`AppContainer._init_browser()` sets `_browser_guard = None`. `browser_open` still has some URL safety in lower layers, but the old BrowserGuard operation/JS guard is absent.

Effect: when browser tools are enabled, safety depends on the remaining URL checks and tool implementation discipline.

### 11. `browse` URL safety makes tests and some real flows brittle

`browse` performs DNS fail-closed before checking whether `browser_manager` is available. In sandboxed/no-DNS test contexts, even mocked browser tests fail before browser code runs.

Observed test result on 2026-04-24:

- `PYTHONPATH=. python -m pytest tests/research/test_fetcher.py tests/core/test_task_runtime.py tests/core/test_correction_manager.py tests/tools/test_personal_tools.py -q`
- 90 passed, 4 failed, all in `tests/tools/test_personal_tools.py::TestBrowse`

### 12. Codex runtime config is partially ignored

`config.toml` has `runtime_base_url`, `runtime_client_version`, and `runtime_timeout_seconds`, but `src/config/settings.py::CodexConfig` does not define them and `src/core/codex_oauth_client.py` hardcodes the Responses API URL.

Effect: changing these config values does not change runtime behavior.

## Verification From This Audit

Commands run on 2026-04-24:

- `python -m compileall -q src tests`: passed.
- `cd desktop-v2 && npm run build`: passed; Vite warned that the main JS chunk is larger than 500 kB.
- Targeted backend tests: 90 passed, 4 failed in `TestBrowse` as noted above.
- Full `PYTHONPATH=. python -m pytest tests/ -x -q`: started, printed progress, then hung for over two minutes and was stopped.

## How To Debug "Lapwing Answer Quality Is Bad"

Start with evidence, in this order:

1. `logs/lapwing.log`: search for `research`, `Refiner`, `think_inner timed out`, `tell_user`, `send_fn`, `其他用户目录`, `browser`, `401`.
2. `logs/shell_execution.log`: check whether the model was blocked by shell policy rather than making a reasoning mistake.
3. `logs/libraries.log`: check unhandled Playwright futures/timeouts.
4. `data/lapwing.db`: compare `inner_thought`, `user_message`, `tell_user`, `commitments`, `reminders_v2`.
5. `data/mutation_log.db`: compare `llm.request`, `tool.called`, `tool.result`, and failed tool names.
6. Current prompt stack: `src/core/state_serializer.py`, `prompts/lapwing_voice.md`, `data/identity/soul.md`, `data/identity/constitution.md`.

When the model seems dumb, first ask:

- Did it actually see a clean, non-contradictory prompt?
- Did it have too many tools exposed?
- Did it return useful text alongside tool calls that was treated as internal and not sent?
- Did `research` fail before the model got evidence?
- Did shell/browser/channel policy block the action?
- Did inner ticks consume most of the runtime budget?

## Safe Editing Guidance

- Keep edits narrow. This repo has a lot of runtime state and old docs.
- Do not modify `data/`, `logs/`, screenshots, DBs, browser profiles, or generated artifacts unless explicitly asked.
- Prefer root imports like `from src.core.brain import LapwingBrain`.
- Python comments may be Chinese; commits/docs for maintainers are usually English.
- Use `PYTHONPATH=.` when pytest cannot import `src`.
- If fixing conversation quality, write focused regression tests around `TaskRuntime`, `Brain`, prompt rendering, and runtime logs rather than only changing prompts.

## Likely Repair Order

1. Fix `httpx` proxy kwarg and update research fetcher tests.
2. Fix `Refiner` structured output fallback for Anthropic-compatible providers.
3. Fix direct-output duplicate-send risk and add exact send-count tests.
4. Fix `send_message` desktop import.
5. Decide how tool-call followup text should be surfaced without duplicate sends or hidden useful output.
6. Reduce and specialize tool exposure by task type.
7. Make correction learning durable and prompt-injected.
8. Re-enable or replace loop detection with a cross-turn failure cache.
9. Fix shell policy so the configured project root is not treated as another user's home.
10. Rework inner tick prompt/schedule so autonomous work is cheap, sparse, and evidence-driven.
