-- Blueprint §7.4: persistent record of credentials Kevin has approved for
-- machine-driven use. PolicyDecider consults this on credential.use to
-- decide first-use INTERRUPT vs already-approved ALLOW.
-- Lives in data/lapwing.db alongside mutations / trajectory / interrupts /
-- events.

CREATE TABLE IF NOT EXISTS credential_use_approvals (
    service     TEXT PRIMARY KEY,
    approved_at TEXT NOT NULL,
    approved_by TEXT NOT NULL DEFAULT 'owner'
);
