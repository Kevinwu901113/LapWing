-- Migration 002: Identity Substrate — addendum tables + append-only triggers
-- Addendum P0.2, P0.3, P0.4, P1.4 from the Identity Substrate design.
-- Applied after 001; idempotent.

-- P0.3: Per-claim byte offsets, separate from canonical state
CREATE TABLE IF NOT EXISTS identity_claim_sources (
    claim_id            TEXT NOT NULL,
    source_file         TEXT NOT NULL,
    source_span_start   INTEGER NOT NULL,
    source_span_end     INTEGER NOT NULL,
    sha256_at_parse     TEXT NOT NULL,
    stable_block_key    TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (claim_id, source_file)
);

-- P0.2: Redaction tombstones — prevents rebuild from resurrecting erased claims
CREATE TABLE IF NOT EXISTS identity_redaction_tombstones (
    tombstone_id        TEXT PRIMARY KEY,
    claim_id            TEXT NOT NULL,
    source_file         TEXT NOT NULL,
    stable_block_key    TEXT NOT NULL,
    raw_block_id        TEXT NOT NULL,
    erased_at           TEXT NOT NULL DEFAULT (datetime('now')),
    reason              TEXT NOT NULL DEFAULT ''
);

-- P1.4: Explicit access requests for restricted claim access verification
CREATE TABLE IF NOT EXISTS identity_explicit_access_requests (
    request_id       TEXT PRIMARY KEY,
    actor_id         TEXT NOT NULL,
    scope            TEXT NOT NULL,
    target_claim_ids TEXT NOT NULL DEFAULT '[]',
    ttl_seconds      INTEGER NOT NULL DEFAULT 300,
    consumed         INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at       TEXT NOT NULL
);

-- Schema adjustment: extend identity_feature_flags_snapshots with hash + refcount
-- ALTER TABLE is guarded by adding the column only when absent (SQLite does not support
-- IF NOT EXISTS on ADD COLUMN, so we use a try/ignore pattern at the application level;
-- the statements are here for documentation and are safe to run once).
ALTER TABLE identity_feature_flags_snapshots ADD COLUMN snapshot_hash TEXT;
ALTER TABLE identity_feature_flags_snapshots ADD COLUMN reference_count INTEGER NOT NULL DEFAULT 1;
CREATE UNIQUE INDEX IF NOT EXISTS idx_flags_snapshot_hash
    ON identity_feature_flags_snapshots (snapshot_hash);

-- P0.4: Append-only triggers

-- identity_revisions: forbid UPDATE and DELETE
CREATE TRIGGER IF NOT EXISTS trg_revisions_no_update
BEFORE UPDATE ON identity_revisions
BEGIN
    SELECT RAISE(ABORT, 'append-only: identity_revisions does not allow UPDATE');
END;

CREATE TRIGGER IF NOT EXISTS trg_revisions_no_delete
BEFORE DELETE ON identity_revisions
BEGIN
    SELECT RAISE(ABORT, 'append-only: identity_revisions does not allow DELETE');
END;

-- identity_audit_log: forbid UPDATE and DELETE
CREATE TRIGGER IF NOT EXISTS trg_audit_log_no_update
BEFORE UPDATE ON identity_audit_log
BEGIN
    SELECT RAISE(ABORT, 'append-only: identity_audit_log does not allow UPDATE');
END;

CREATE TRIGGER IF NOT EXISTS trg_audit_log_no_delete
BEFORE DELETE ON identity_audit_log
BEGIN
    SELECT RAISE(ABORT, 'append-only: identity_audit_log does not allow DELETE');
END;

-- identity_redaction_tombstones: forbid UPDATE and DELETE
CREATE TRIGGER IF NOT EXISTS trg_tombstones_no_update
BEFORE UPDATE ON identity_redaction_tombstones
BEGIN
    SELECT RAISE(ABORT, 'append-only: identity_redaction_tombstones does not allow UPDATE');
END;

CREATE TRIGGER IF NOT EXISTS trg_tombstones_no_delete
BEFORE DELETE ON identity_redaction_tombstones
BEGIN
    SELECT RAISE(ABORT, 'append-only: identity_redaction_tombstones does not allow DELETE');
END;

-- identity_explicit_access_requests: UPDATE allowed (consumed marking), DELETE forbidden
CREATE TRIGGER IF NOT EXISTS trg_explicit_access_no_delete
BEFORE DELETE ON identity_explicit_access_requests
BEGIN
    SELECT RAISE(ABORT, 'delete-protected: identity_explicit_access_requests does not allow DELETE');
END;

-- Bump migration version to 2
INSERT OR REPLACE INTO identity_migration_version (version, applied_at)
    VALUES (2, datetime('now'));
