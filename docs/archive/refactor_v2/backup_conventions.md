# Backup Conventions

Two backup classes exist. They live in different places on purpose.

## Step-level snapshots — `~/lapwing-backups/pre_<step>_<timestamp>/`

Created at the opening of each v2.0 Step (see `cleanup_report_step*.md` §1).
Contains a full copy of `data/` plus the git commit + baseline test count
taken at that moment. Immutable after creation — treat as a read-only
restore point if a Step goes sideways.

Location: **user home**, intentionally **outside the project tree**.
Rationale: prevents accidental inclusion in `git add`, and keeps the
snapshot alive even when `data/` is rebuilt from scratch or the project
dir is moved.

Examples:
- `~/lapwing-backups/pre_step1_20260417_234006/`
- `~/lapwing-backups/pre_step2_20260418_135452/`
   + `sessions_archive.json` (149 rows, captured by `scripts/drop_sessions_table.py`
     in 2j before DROP)

## In-project rolling state — `data/backups/`

Inside the repo, lives alongside the live data. Used by individual
subsystems (file_editor, soul, vital_guard) to keep short-window
recovery copies of files they mutate. Not a Step-boundary snapshot.
May be rotated or cleared by those subsystems at any time; do not
rely on it for cross-Step restore.

## Rule of thumb

| Purpose                                      | Location                     |
|----------------------------------------------|------------------------------|
| "Undo this Step"                             | `~/lapwing-backups/`         |
| "Recover this file from 5 minutes ago"       | `data/backups/`              |
| "Inspect data the subsystem just archived"   | `data/backups/<subsystem>/`  |
| "Forensic material for a past Step's audit"  | `~/lapwing-backups/pre_*/`   |

Step-level snapshots are never moved into `data/backups/`. Doing so
would couple them to the live data directory's lifecycle — exactly
the failure mode the separation prevents.
