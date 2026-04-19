# Refactor v2.0 — Migration Script Archive

Migration scripts are one-shot tools. After execution they become
historical records: useful for auditing what ran, what it touched, and
why, but not part of the live tree. This file is the registry — the
scripts themselves have been deleted; use `git log --follow <path>` to
recover source.

## Executed migrations

### Step 2e — `conversations` → `trajectory` row migration

- **Script**: `scripts/migrate_to_trajectory.py`
- **Test**: `tests/scripts/test_migrate_to_trajectory.py`
- **Ran**: 2026-04-18 during Step 2e branch
- **Outcome**: 1351 rows moved; 3 discarded per audit rules
  (`docs/refactor_v2/step2_data_audit_notes.md`)
- **Deleted**: 2026-04-19 MVP cleanup
- **Recover**: `git show <pre-cleanup-commit>:scripts/migrate_to_trajectory.py`

### Step 2f — dual-write verification

- **Script**: `scripts/verify_dual_write.py`
- **Ran**: 2026-04-18; confirmed every conversations insert also landed in trajectory
- **Deleted**: 2026-04-19 MVP cleanup

### Step 2j — `sessions` table drop

- **Script**: `scripts/drop_sessions_table.py`
- **Ran**: 2026-04-18; sessions concept retired with TrajectoryStore + Attention
- **Deleted**: 2026-04-19 MVP cleanup

### Step 3 — `conversations` + FTS shadow tables drop

- **Scripts**:
  - `scripts/migrations/step3_verify_drop_safety.py` (pre-check)
  - `scripts/migrations/step3_drop_legacy_tables.py` (execution)
- **Ran**: 2026-04-18; dropped conversations + 6 FTS shadows (7 tables, ~5.7k rows)
- **Outcome**: See `cleanup_report_step3.md` §5
- **Deleted**: 2026-04-19 MVP cleanup

### Step 5 — manual smoke test framework

- **Script**: `scripts/smoke_test_step5.py`
- **Status**: Manual-only harness; never integrated. Step 5 behaviour is
  now covered by `tests/integration/test_step5_regression.py`.
- **Deleted**: 2026-04-19 MVP cleanup

### Codex OAuth — connectivity probe

- **Script**: `scripts/test_codex_oauth.py`
- **Status**: Early-phase debugging tool; Codex OAuth flow is covered by
  `src/auth/openai_codex.py` + `scripts/diagnose_schedule.py`.
- **Deleted**: 2026-04-19 MVP cleanup

### MVP cleanup — legacy SQLite table drop

- **Script**: `scripts/migrations/mvp_drop_legacy_tables.py`
- **Ran**: 2026-04-19 during MVP cleanup
- **Outcome**: Dropped 5 tables (`user_facts`, `interest_topics`,
  `discoveries`, `todos`, `reminders`) — 321 rows total, all backed up to
  `~/lapwing-backups/pre_mvp_cleanup_20260419_194749/`
- **Status**: Deleted 2026-04-19. Source recoverable via `git log --follow`.
