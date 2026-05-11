-- Blueprint §8.1: persistent state machine for owner-attention interrupts.
-- Resides in data/lapwing.db alongside mutations / trajectory / etc.

CREATE TABLE IF NOT EXISTS interrupts (
    id                      TEXT PRIMARY KEY,
    kind                    TEXT NOT NULL,
    status                  TEXT NOT NULL,           -- pending / resolved / denied / expired / cancelled
    actor_required          TEXT NOT NULL,
    resource                TEXT NOT NULL,
    resource_ref            TEXT,
    continuation_ref        TEXT,
    non_resumable           INTEGER NOT NULL DEFAULT 0,
    non_resumable_reason    TEXT,
    summary                 TEXT NOT NULL DEFAULT '',
    payload_redacted_json   TEXT NOT NULL DEFAULT '{}',
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    expires_at              TEXT,
    resolved_payload_json   TEXT
);

CREATE INDEX IF NOT EXISTS idx_interrupts_status   ON interrupts(status);
CREATE INDEX IF NOT EXISTS idx_interrupts_kind     ON interrupts(kind);
CREATE INDEX IF NOT EXISTS idx_interrupts_created  ON interrupts(created_at);
