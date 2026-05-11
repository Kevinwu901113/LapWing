-- Blueprint §9.1: append-only operational history.
-- Lives in data/lapwing.db alongside mutations / trajectory / interrupts.
-- HARD CONSTRAINT: no UPDATE / DELETE paths anywhere in the code.

CREATE TABLE IF NOT EXISTS events (
    id                 TEXT PRIMARY KEY,
    time               TEXT NOT NULL,
    actor              TEXT NOT NULL,
    type               TEXT NOT NULL,
    resource           TEXT,
    summary            TEXT NOT NULL,
    outcome            TEXT,
    refs_json          TEXT NOT NULL DEFAULT '{}',
    data_redacted_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_events_time     ON events(time);
CREATE INDEX IF NOT EXISTS idx_events_type     ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_resource ON events(resource);
CREATE INDEX IF NOT EXISTS idx_events_actor    ON events(actor);
