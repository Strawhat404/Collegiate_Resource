-- Chunk-3 governance & compliance hardening.
-- Permission codes only; role bindings live in seed_extras.sql so they bind
-- AFTER seed_dev.sql has created the roles.
INSERT OR IGNORE INTO permissions (code) VALUES
    ('student.write'),
    ('student.import');

-- Track when scheduled notification rules last fired so we don't double-fire.
CREATE TABLE IF NOT EXISTS notif_rule_runs (
    rule_id INTEGER PRIMARY KEY REFERENCES notif_rules(id) ON DELETE CASCADE,
    last_fired_at TEXT
);
