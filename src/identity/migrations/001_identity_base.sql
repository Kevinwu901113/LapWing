-- Migration 001: Identity Substrate — core tables
-- Applied by IdentityStore on first open; idempotent (CREATE TABLE IF NOT EXISTS).

-- 1. Canonical claim state
CREATE TABLE IF NOT EXISTS identity_claims (
    claim_id            TEXT PRIMARY KEY,
    raw_block_id        TEXT NOT NULL,
    claim_local_key     TEXT NOT NULL DEFAULT 'claim_0',
    source_file         TEXT NOT NULL,
    stable_block_key    TEXT NOT NULL,
    claim_type          TEXT NOT NULL,
    owner               TEXT NOT NULL,
    predicate           TEXT NOT NULL DEFAULT '',
    object_val          TEXT NOT NULL DEFAULT '',
    confidence          REAL NOT NULL DEFAULT 0.5,
    sensitivity         TEXT NOT NULL DEFAULT 'public',
    status              TEXT NOT NULL DEFAULT 'active',
    tags                TEXT NOT NULL DEFAULT '[]',
    evidence_ids        TEXT NOT NULL DEFAULT '[]',
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
    -- NOTE: source_span_start / source_span_end / source_sha live in
    --       identity_claim_sources (migration 002, Addendum P0.3)
);

-- 2. Append-only revision event log
CREATE TABLE IF NOT EXISTS identity_revisions (
    revision_id                 TEXT PRIMARY KEY,
    claim_id                    TEXT NOT NULL,
    action                      TEXT NOT NULL,
    old_snapshot                TEXT,
    new_snapshot                TEXT NOT NULL,
    actor                       TEXT NOT NULL,
    reason                      TEXT NOT NULL DEFAULT '',
    auth_context_id             TEXT,
    feature_flags_snapshot_id   TEXT,
    created_at                  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 3. Gate decision log
CREATE TABLE IF NOT EXISTS identity_gate_events (
    event_id                    TEXT PRIMARY KEY,
    claim_id                    TEXT NOT NULL,
    outcome                     TEXT NOT NULL,
    pass_reason                 TEXT,
    gate_level                  TEXT NOT NULL DEFAULT 'none',
    context_profile             TEXT,
    signals                     TEXT NOT NULL DEFAULT '{}',
    auth_context_id             TEXT,
    feature_flags_snapshot_id   TEXT,
    created_at                  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 4. Gate decision cache
CREATE TABLE IF NOT EXISTS identity_gate_cache (
    cache_key   TEXT PRIMARY KEY,
    outcome     TEXT NOT NULL,
    computed_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at  TEXT
);

-- 5. Conflict events
CREATE TABLE IF NOT EXISTS identity_conflict_events (
    event_id        TEXT PRIMARY KEY,
    claim_id_a      TEXT NOT NULL,
    claim_id_b      TEXT NOT NULL,
    conflict_type   TEXT NOT NULL,
    resolution      TEXT,
    resolved        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 6. Retrieval traces
CREATE TABLE IF NOT EXISTS identity_retrieval_traces (
    trace_id                    TEXT PRIMARY KEY,
    query                       TEXT NOT NULL,
    context_profile             TEXT,
    candidate_ids               TEXT NOT NULL DEFAULT '[]',
    selected_ids                TEXT NOT NULL DEFAULT '[]',
    redacted_ids                TEXT NOT NULL DEFAULT '[]',
    latency_ms                  REAL NOT NULL DEFAULT 0,
    auth_context_id             TEXT,
    feature_flags_snapshot_id   TEXT,
    created_at                  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 7. Injection traces
CREATE TABLE IF NOT EXISTS identity_injection_traces (
    trace_id                    TEXT PRIMARY KEY,
    retrieval_trace_id          TEXT NOT NULL,
    claim_ids                   TEXT NOT NULL DEFAULT '[]',
    token_count                 INTEGER NOT NULL DEFAULT 0,
    budget_total                INTEGER NOT NULL DEFAULT 0,
    auth_context_id             TEXT,
    feature_flags_snapshot_id   TEXT,
    created_at                  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 8. Append-only audit trail
CREATE TABLE IF NOT EXISTS identity_audit_log (
    entry_id                    TEXT PRIMARY KEY,
    action                      TEXT NOT NULL,
    claim_id                    TEXT,
    actor                       TEXT NOT NULL,
    details                     TEXT NOT NULL DEFAULT '{}',
    justification               TEXT NOT NULL DEFAULT '',
    auth_context_id             TEXT,
    feature_flags_snapshot_id   TEXT,
    created_at                  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 9. Auth context registry
CREATE TABLE IF NOT EXISTS identity_auth_contexts (
    context_id  TEXT PRIMARY KEY,
    actor       TEXT NOT NULL,
    scopes      TEXT NOT NULL DEFAULT '[]',
    session_id  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 10. Override tokens
CREATE TABLE IF NOT EXISTS identity_override_tokens (
    token_id            TEXT PRIMARY KEY,
    claim_id            TEXT NOT NULL,
    issuer              TEXT NOT NULL,
    reason              TEXT NOT NULL DEFAULT '',
    action_payload_hash TEXT,
    consumed            INTEGER NOT NULL DEFAULT 0,
    expires_at          TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 11. Approval requests
CREATE TABLE IF NOT EXISTS identity_approval_requests (
    request_id       TEXT PRIMARY KEY,
    claim_id         TEXT NOT NULL,
    requested_action TEXT NOT NULL,
    requester        TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending',
    resolved_at      TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 12. Evidence records
CREATE TABLE IF NOT EXISTS identity_evidence (
    evidence_id TEXT PRIMARY KEY,
    claim_id    TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    source_ref  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 13. Claim-to-claim relationships
CREATE TABLE IF NOT EXISTS identity_relations (
    source_claim_id TEXT NOT NULL,
    target_claim_id TEXT NOT NULL,
    relation_type   TEXT NOT NULL,
    weight          REAL NOT NULL DEFAULT 1.0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (source_claim_id, target_claim_id, relation_type)
);

-- 14. LLM classification cache (Module 2)
CREATE TABLE IF NOT EXISTS identity_extraction_cache (
    cache_key  TEXT PRIMARY KEY,
    result     TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 15. File-level SHA tracking for parser change detection
CREATE TABLE IF NOT EXISTS identity_source_files (
    file_path       TEXT PRIMARY KEY,
    sha256          TEXT NOT NULL,
    last_parsed_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 16. Chroma sync outbox
CREATE TABLE IF NOT EXISTS identity_index_outbox (
    outbox_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id     TEXT NOT NULL,
    action       TEXT NOT NULL,
    payload      TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    processed_at TEXT
);

-- 17. Immutable feature-flags snapshots
CREATE TABLE IF NOT EXISTS identity_feature_flags_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    flags       TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 18. Migration version tracking
CREATE TABLE IF NOT EXISTS identity_migration_version (
    version    INTEGER NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_claims_source_file  ON identity_claims (source_file);
CREATE INDEX IF NOT EXISTS idx_claims_status        ON identity_claims (status);
CREATE INDEX IF NOT EXISTS idx_claims_claim_type    ON identity_claims (claim_type);

CREATE INDEX IF NOT EXISTS idx_revisions_claim_created ON identity_revisions (claim_id, created_at);

CREATE INDEX IF NOT EXISTS idx_outbox_unprocessed
    ON identity_index_outbox (processed_at)
    WHERE processed_at IS NULL;

INSERT OR IGNORE INTO identity_migration_version (version, applied_at)
    VALUES (1, datetime('now'));
