CREATE TABLE IF NOT EXISTS agent_tasks (
    task_id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    parent_event_id TEXT NOT NULL,
    parent_turn_id TEXT,
    parent_task_id TEXT,
    root_task_id TEXT NOT NULL,
    spawned_by TEXT NOT NULL CHECK (spawned_by IN ('lapwing','agent','system')),
    replaces_task_id TEXT,
    spec_id TEXT NOT NULL,
    spec_version TEXT,
    instance_id TEXT NOT NULL,
    objective TEXT NOT NULL,
    user_visible_summary TEXT NOT NULL,
    semantic_tags TEXT NOT NULL,
    expected_output TEXT,
    status TEXT NOT NULL,
    status_reason TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    last_event_at TEXT,
    workspace_path TEXT NOT NULL,
    result_summary TEXT,
    error_summary TEXT,
    artifact_refs TEXT NOT NULL DEFAULT '[]',
    last_progress_summary TEXT,
    checkpoint_id TEXT,
    checkpoint_question TEXT,
    cancellation_requested INTEGER NOT NULL DEFAULT 0,
    cancellation_reason TEXT,
    notify_policy TEXT NOT NULL DEFAULT 'auto',
    salience TEXT NOT NULL DEFAULT 'normal',
    priority INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_tasks_idempotency
    ON agent_tasks(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_chat_status
    ON agent_tasks(chat_id, status);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_root
    ON agent_tasks(root_task_id);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_owner_status
    ON agent_tasks(owner_user_id, status);

CREATE TABLE IF NOT EXISTS agent_events (
    event_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    summary_for_lapwing TEXT NOT NULL,
    summary_for_owner TEXT,
    raw_payload_ref TEXT,
    salience TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    sequence_in_task INTEGER NOT NULL,
    FOREIGN KEY (task_id) REFERENCES agent_tasks(task_id)
);

CREATE INDEX IF NOT EXISTS idx_agent_events_task_seq
    ON agent_events(task_id, sequence_in_task);
CREATE INDEX IF NOT EXISTS idx_agent_events_chat_time
    ON agent_events(chat_id, occurred_at);

CREATE TABLE IF NOT EXISTS agent_runtime_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    conversation_state_json TEXT NOT NULL,
    scratchpad_summary TEXT NOT NULL,
    pending_question_json TEXT NOT NULL,
    tool_context_json TEXT NOT NULL,
    workspace_snapshot_ref TEXT,
    rounds_consumed INTEGER NOT NULL,
    FOREIGN KEY (task_id) REFERENCES agent_tasks(task_id)
);

CREATE TABLE IF NOT EXISTS system_lifecycle (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL CHECK (event_type IN ('startup','shutdown','recovery_marked','operator_emergency')),
    occurred_at TEXT NOT NULL,
    metadata TEXT
);
