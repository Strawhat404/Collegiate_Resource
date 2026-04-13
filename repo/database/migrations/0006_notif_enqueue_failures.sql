-- Dead-letter for trigger-rule enqueue failures. Without this, a transient
-- DB-locked error during an event-bus callback would lose the notification
-- silently (events.py swallows handler exceptions to protect publishers).
CREATE TABLE IF NOT EXISTS notif_enqueue_failures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_name TEXT NOT NULL,
    template_id INTEGER NOT NULL,
    audience_user_id INTEGER NOT NULL,
    subject TEXT NOT NULL,
    body_rendered TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    replayed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_notif_enqueue_failures_pending
    ON notif_enqueue_failures(replayed_at) WHERE replayed_at IS NULL;
