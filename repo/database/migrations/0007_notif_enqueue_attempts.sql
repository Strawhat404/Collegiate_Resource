-- Track replay attempts on enqueue-stage failures so the dead-letter
-- replay loop can enforce the same 3-attempt cap that delivery uses
-- (docs/design.md §7). Without an explicit counter the replay loop
-- silently retries forever on a permanently-broken row.
ALTER TABLE notif_enqueue_failures
    ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE notif_enqueue_failures
    ADD COLUMN dead_at TEXT;
