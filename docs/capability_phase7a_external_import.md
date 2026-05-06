# Phase 7A: External Capability Import / Quarantine v0

## Overview

Adds a safe external import path for capabilities. External packages (local
filesystem directories) can be inspected and imported into **quarantined**
storage only. Quarantined capabilities are never active, executable,
promotable, or retrievable by default.

## Feature Flag

`capabilities.external_import_enabled` — default `false`.
Requires `capabilities.enabled = true`.

## Tools

Both tools use the `capability_import_operator` tag and require the
`CAPABILITY_IMPORT_OPERATOR_PROFILE` runtime profile.

### `inspect_capability_package`

- **Input**: `path` (required), `scope` (optional, default `user`), `include_files` (bool, default true)
- **Behavior**: Parses the external package, runs evaluator and policy checks, returns findings. No writes, no index updates, no script execution.
- **Returns**: Package summary, eval findings, policy findings, `would_import`, `quarantine_reason`

### `import_capability_package`

- **Input**: `path` (required), `target_scope`, `imported_by`, `reason`, `dry_run` (default false)
- **dry_run=true**: Same as inspect, no writes
- **dry_run=false**: Inspect → validate → copy to `data/capabilities/quarantine/<id>/` → write `import_report.json` → index with `status=quarantined`

## Storage Layout

```
data/capabilities/quarantine/<capability_id>/
  CAPABILITY.md
  manifest.json        # status=quarantined, maturity=draft (forced)
  import_report.json   # origin metadata, source_path_hash, eval/policy findings
  scripts/
  tests/
  examples/
```

## Quarantine Semantics

- `status=quarantined` is forced on import; package's declared status/maturity are ignored
- Quarantined capabilities are excluded from default `store.list()`, `index.search()`, and `CapabilityRetriever.retrieve()`
- `CapabilityPolicy.validate_run()` blocks quarantined
- `CapabilityPolicy.validate_promote()` blocks quarantined
- Explicit `filters={"status": "quarantined"}` or `include_quarantined=True` can surface them if needed

## Safety Guarantees

- No script execution — scripts are copied as data, never run
- No Python module imports from packages
- No subprocess calls
- No network access
- Path traversal rejected (`..` patterns, symlinks)
- Remote URLs rejected (`://` in path)
- Source path stored as SHA256 hash, not raw
- Duplicate IDs rejected (active or quarantine)
- Prompt injection text in packages treated as data

## Future (not in v0)

- Remote registry search
- URL/git-clone imports
- Archive format support
- Promotion out of quarantine
- run_capability for quarantined capabilities
