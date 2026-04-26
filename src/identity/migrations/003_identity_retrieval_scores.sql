-- 003_identity_retrieval_scores.sql
-- Adds per-claim score breakdown to retrieval traces so embedding-based
-- scoring (Ticket A.6) can surface relevance / confidence / final values.

ALTER TABLE identity_retrieval_traces
    ADD COLUMN scores TEXT NOT NULL DEFAULT '{}';

INSERT OR REPLACE INTO identity_migration_version (version, applied_at)
    VALUES (3, datetime('now'));
