# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What Lapwing Is

A long-running personal AI companion for Kevin — not a stateless chatbot. Python 3.12+ backend (`main.py`, `src/`) with a Tauri v2 + React 19 desktop client (`desktop-v2/`). The product goal is a persistent agent that converses through QQ and desktop channels, maintains identity / memory / commitments / reminders / inner thoughts, uses tools, and runs autonomous inner ticks while no user message is pending.

The quality bar is not "the LLM answers correctly" — it's whether the harness lets the same model behave naturally, quickly, and visibly. Treat harness behavior (prompt size, tool exposure, send path) as a first-class concern.

For a detailed architecture map see `README.md` (Chinese). For audit notes / known broken paths see `CODEX.md`. For the short repository guidelines (English) see `AGENTS.md`. This file is the canonical Claude Code briefing.

## Build, Test, Run

Backend (always run pytest with `PYTHONPATH=.` — `src` is not installed as a package):

```bash
pip install -r requirements.txt
PYTHONPATH=. python -m pytest tests/ -x -q
PYTHONPATH=. python -m pytest tests/core/test_task_runtime.py -q
PYTHONPATH=. python -m pytest tests/core/test_brain.py::test_name -x -q
python -m compileall -q src tests
python main.py auth list      # CLI: auth profiles, credentials, etc.
python main.py                # foreground run; production uses systemd
bash scripts/deploy.sh        # production deploy (systemctl restart lapwing)
```

Frontend (`desktop-v2/`):

```bash
cd desktop-v2 && npm run dev          # Vite dev server
cd desktop-v2 && npm run tauri dev    # full Tauri app
cd desktop-v2 && npm run build        # tsc + vite build (this IS the type-check)
```

There is no repo-wide linter, formatter, type checker, or CI gate. Tests are the only quality gate. `pytest.ini` sets `asyncio_mode = auto`. Some tests are gated behind `--run-evals` (see the `eval` marker).

## Core Runtime Flow (read this before changing behavior)

```
main.py
 └─ AppContainer.prepare() / start()  (src/app/container.py — DI root)
     ├─ LapwingBrain                   (src/core/brain.py — the only entry point for requests)
     │   ├─ LLMRouter                  (src/core/llm_router.py — chat / tool / heartbeat slots)
     │   ├─ TaskRuntime                (src/core/task_runtime.py — tool loop)
     │   ├─ StateViewBuilder/Serializer (prompt assembly: soul/voice/memory/rules/trajectory)
     │   ├─ TrajectoryStore            (src/core/trajectory_store.py — SQLite, single source of conversation truth)
     │   ├─ AgentRegistry              (src/agents/ — Researcher + Coder sub-agents, dispatched via agent tools)
     │   └─ optional deps              (skill_manager, browser_manager, vector_store, … — feature-flagged)
     ├─ MainLoop + EventQueue          (single-consumer, priority OWNER>TRUSTED>SYSTEM>INNER)
     ├─ InnerTickScheduler             (autonomous ticks while idle)
     ├─ DurableScheduler               (reminders_v2)
     ├─ ChannelManager                 (QQ + Desktop adapters)
     └─ LocalApiServer                 (FastAPI + SSE/WebSocket for desktop-v2)
```

A user turn:

1. Channel adapter (`src/adapters/qq_adapter.py`, `desktop_adapter.py`) drops an event into `EventQueue`.
2. `MainLoop` calls `LapwingBrain.think_conversational(chat_id, user_message, send_fn, typing_fn=None, status_callback=None, adapter="", user_id="", metadata=None, images=None)`.
3. Brain trust-tags the user, builds `StateView` from `TrajectoryStore`, calls `_complete_chat` → `TaskRuntime.complete_chat`.
4. `TaskRuntime` loops: `LLMRouter.complete_with_tools` → execute tool calls → append results → repeat until no tool calls. Sub-agent work (research, coding) is dispatched as a tool call into `AgentRegistry`, not inline in the main loop.
5. **Bare assistant text from the model is the user-visible reply** (split on blank lines and emitted via `send_fn`). Text accompanying a tool call is treated as internal scratch and is NOT sent.

`send_message` is a separate tool for **proactive** outbound messages (inner ticks, reminders, agent flows) — it is not the normal reply path.

## Non-Negotiable Invariants

- Bare model text → user; tool-call companion text → internal. Don't change this without updating `tests/core/test_brain_split.py` to assert exact send counts.
- Inner ticks are not user turns. They must not casually message Kevin without a real reason.
- `TrajectoryStore` (`data/lapwing.db`) is the single source of conversation truth — there is no longer a `ConversationMemory` cache layer in front of it. `StateMutationLog` (`data/mutation_log.db`) is the authoritative event log of LLM calls and tool executions. Trust both over comments and old docs.
- Old comments and telemetry names still mention `tell_user`; the current path is direct bare-text. Verify against current code before believing a doc.
- Brain dependencies are optional (default `None`) and gated by feature flags in `config/.env` (e.g. `BROWSER_ENABLED`, `SKILLS_ENABLED`, `LOOP_DETECTION_ENABLED`). Don't assume any subsystem exists; check via `getattr(brain, "...", None)`.
- More always-on tools = higher selection entropy and latency. Adding a tool to the chat profile is not free.
- Treat `data/`, `logs/`, browser profiles, screenshots, and SQLite DBs as volatile runtime state. Don't commit or modify them unless a task explicitly calls for fixture work.

## Conventions

- **Imports**: absolute from repo root — `from src.core.brain import LapwingBrain`.
- **Types**: keep dataclasses and protocols in dedicated modules (`task_types.py`, `llm_types.py`, `shell_types.py`, `src/tools/types.py`).
- **Logging**: `logging.getLogger("lapwing.<module>")`. The `lapwing` logger does not propagate to root — third-party logs go to a separate handler in `main.setup_logging`.
- **Comments**: Chinese is fine in code. Commits, PRs, and maintainer-facing docs (this file, AGENTS.md, CODEX.md) are English. Recent commits use Conventional Commits (`feat(scope): …`, `fix(browser): …`).
- **Prompts**: live in `prompts/` and are hot-loaded by `prompt_loader.py`. Identity material lives in `data/identity/` (`soul.md`, `constitution.md`) and is protected by `VitalGuard` / `ConstitutionGuard`.
- **Config**: `config.toml` is the canonical non-secret config; `config/.env` holds secrets and per-environment overrides. Precedence (per `config/settings.py`): **env vars (.env) > config.toml > code defaults** — `.env` always wins. Per-runtime LLM routing persists to `data/config/model_routing.json`.

## Tools System (where most behavior lives)

- `src/tools/registry.py::build_default_tool_registry()` registers every tool. `chat_tools()` filters by `RuntimeProfile` (defined in `src/core/runtime_profiles.py` — 9 profiles: `chat_shell`, `chat_minimal`, `chat_extended`, `task_execution`, `coder_snippet`, `coder_workspace`, `file_ops`, `agent_researcher`, `agent_coder`).
- A tool is a `ToolSpec` (`src/tools/types.py`) with: `executor`, `capability` (e.g. `shell` / `web` / `file` / `memory` / `browser`), `risk_level`, and `visibility`. `internal` tools are not exposed to the LLM.
- `ToolExecutionContext` carries `auth_level` (`0=GUEST / 1=TRUSTED / 2=OWNER`), adapter, chat_id, services dict, and shell config. Authorization gate: `src/core/authority_gate.py`.
- Pre-execution guards run inside `TaskRuntime`: `VitalGuard` (core file protection), `AuthorityGate` (per-tool), and loop detection (`config.toml [loop_detection]` — by default `enabled = true, blocking = false`, i.e. observation mode: warnings emit but tool calls aren't blocked).

To add a tool: implement the executor, register the `ToolSpec` in `registry.py`, and (if non-trivially privileged) add an `AuthorityGate` rule. See `README.md` "扩展模式" for the recipe.

## Testing

- Tests mirror `src/` layout under `tests/`. File pattern `test_*.py`. Async tests work without decorators (`asyncio_mode = auto`).
- Standard pattern: mock `LLMRouter`, `TrajectoryStore`, adapters, and tool results — do not hit live APIs in tests.
- Run focused tests first (`PYTHONPATH=. python -m pytest tests/<area>/test_x.py::test_y -q`), then the full suite. The full suite has historically hung on browser/network paths; if `tests/ -x` stalls, suspect a test that opens Playwright or DNS-resolves before mocks are applied.
- For UI changes in `desktop-v2/`, `npm run build` is the type-check; there is no separate `tsc` script.

## Where To Look When Behavior Is Off

In rough order of usefulness:

1. `logs/lapwing.log` — search for `Refiner`, `think_inner timed out`, `send_fn`, `其他用户目录`, `401`, `tell_user`.
2. `logs/shell_execution.log` — was the model blocked by shell policy rather than reasoning poorly?
3. `logs/libraries.log` — Playwright / httpx unhandled futures.
4. `data/lapwing.db` — tables: `trajectory`, `reminders_v2`, `commitments`, `focuses`.
5. `data/mutation_log.db` — `llm.request`, `tool.called`, `tool.result`. Compare per-chat counts.
6. Current prompt stack: `src/core/state_serializer.py`, `prompts/lapwing_voice.md`, `data/identity/soul.md`, `data/identity/constitution.md`.

When the model "feels dumb," ask first: did it see a clean non-contradictory prompt? Were too many tools exposed? Did useful text ride alongside a tool call (and get hidden)? Did `research` fail before evidence reached the model? Did shell/browser/channel policy block the action? Did inner ticks consume the runtime budget?

## Safe-Editing Reminders

- Keep edits narrow; this repo carries a lot of runtime state and stale comments.
- Don't trust an old comment naming `tell_user`, `proxies=`, `BrowserGuard`, etc. — verify against current code.
- If you fix conversation quality, add a focused regression test around `TaskRuntime`, `Brain`, prompt rendering, or runtime logs — don't only tweak prompts.
- `CODEX.md` lists historical audit findings (last updated 2026-04-24). Treat them as leads to verify, not as current state.
