# Repository Guidelines

## Project Structure & Module Organization

Lapwing is a Python 3.12+ backend with a Tauri v2 desktop client. The main entrypoint is `main.py`; backend code lives in `src/`, including `src/core/`, `src/tools/`, `src/memory/`, `src/api/`, `src/adapters/`, `src/agents/`, and `src/auth/`. Tests live in `tests/` and generally mirror the source layout. Prompt Markdown lives in `prompts/`. Runtime state, databases, browser profiles, and generated artifacts live under `data/` and `logs/`. The desktop app is in `desktop-v2/`, with React/TypeScript in `desktop-v2/src/` and Rust/Tauri code in `desktop-v2/src-tauri/`.

## Build, Test, and Development Commands

- `pip install -r requirements.txt`: install backend dependencies.
- `cp config/.env.example config/.env`: create local environment config, then fill secrets.
- `python -m pytest tests/ -x -q`: run the full backend test suite.
- `python -m pytest tests/core/test_brain.py::test_name -x -q`: run one focused test.
- `python main.py auth list`: inspect configured auth profiles.
- `bash scripts/deploy.sh`: deploy/restart the service.
- `cd desktop-v2 && npm run dev`: start the Vite desktop frontend.
- `cd desktop-v2 && npm run tauri dev`: run the full Tauri app.
- `cd desktop-v2 && npm run build`: type-check and build the desktop frontend.

## Coding Style & Naming Conventions

Use absolute Python imports from the repository root, for example `from src.core.brain import LapwingBrain`. Keep core dataclasses and protocol types in dedicated modules such as `task_types.py`, `llm_types.py`, `shell_types.py`, or `src/tools/types.py`. Python comments may be Chinese; commits and maintainer docs are English. Use `logging.getLogger("lapwing.module_name")` for project logging. No repo-wide linter, formatter, type checker, or CI gate is configured, so match nearby style.

## Testing Guidelines

Tests use `pytest` with `pytest-asyncio` and `asyncio_mode = auto` in `pytest.ini`. Name test files `test_*.py`, colocated by subsystem under `tests/core/`, `tests/memory/`, `tests/api/`, and similar directories. Prefer mocked `LLMRouter`, memory stores, adapters, and tool results over live API calls. Run focused tests first, then the full suite for shared behavior.

## Commit & Pull Request Guidelines

Recent commits mostly use concise English Conventional Commit style, for example `feat(proxy): ...` and `fix(browser): ...`. Keep subjects imperative and scoped. Pull requests should describe behavior changes, list tests run, mention config or migration impact, and include screenshots for visible `desktop-v2` UI changes.

## Security & Configuration Tips

Keep secrets in `config/.env` or local runtime config, not in source. Avoid committing generated state from `data/`, `logs/`, browser profiles, screenshots, or local databases unless a task requires a fixture.
