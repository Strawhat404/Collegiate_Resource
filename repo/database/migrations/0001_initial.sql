-- CRHGC initial schema
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

-- Identity & access ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    password_hash BLOB NOT NULL,
    password_salt BLOB NOT NULL,
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_roles (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, role_id)
);

CREATE TABLE IF NOT EXISTS permissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS role_permissions (
    role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission_id INTEGER NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_id)
);

-- Students & housing --------------------------------------------------------
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id_ext TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    college TEXT,
    class_year INTEGER,
    email_enc TEXT,
    phone_enc TEXT,
    ssn_last4_enc TEXT,
    housing_status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS buildings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    address TEXT
);

CREATE TABLE IF NOT EXISTS rooms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    building_id INTEGER NOT NULL REFERENCES buildings(id),
    floor INTEGER NOT NULL,
    code TEXT NOT NULL,
    UNIQUE (building_id, code)
);

CREATE TABLE IF NOT EXISTS beds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id INTEGER NOT NULL REFERENCES rooms(id),
    code TEXT NOT NULL,
    capacity INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'available',
    UNIQUE (room_id, code)
);

CREATE TABLE IF NOT EXISTS bed_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id),
    bed_id INTEGER NOT NULL REFERENCES beds(id),
    effective_date TEXT NOT NULL,
    end_date TEXT,
    reason TEXT,
    operator_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_assign_student ON bed_assignments(student_id);
CREATE INDEX IF NOT EXISTS idx_assign_bed_active ON bed_assignments(bed_id) WHERE end_date IS NULL;

-- Academic resources --------------------------------------------------------
CREATE TABLE IF NOT EXISTS resource_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    parent_id INTEGER REFERENCES resource_categories(id)
);

CREATE TABLE IF NOT EXISTS resources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER REFERENCES resource_categories(id),
    title TEXT NOT NULL,
    owner_id INTEGER REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS resource_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_id INTEGER NOT NULL REFERENCES resources(id),
    version_no INTEGER NOT NULL,
    summary TEXT,
    body TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    published_at TEXT,
    published_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (resource_id, version_no)
);

-- Employer compliance -------------------------------------------------------
CREATE TABLE IF NOT EXISTS employers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    ein TEXT,
    contact_email_enc TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS employer_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employer_id INTEGER NOT NULL REFERENCES employers(id),
    kind TEXT NOT NULL,                -- 'onboarding' | 'violation'
    state TEXT NOT NULL DEFAULT 'submitted',
    reviewer_id INTEGER REFERENCES users(id),
    decision TEXT,
    decided_at TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Notifications -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notif_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    variables_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS notif_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,                -- 'trigger' | 'schedule'
    event_name TEXT,
    cron_spec TEXT,
    template_id INTEGER NOT NULL REFERENCES notif_templates(id),
    audience_query TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS notif_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER NOT NULL REFERENCES notif_templates(id),
    audience_user_id INTEGER NOT NULL REFERENCES users(id),
    subject TEXT NOT NULL,
    body_rendered TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    scheduled_for TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    read_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_msg_user_unread ON notif_messages(audience_user_id, read_at);
CREATE INDEX IF NOT EXISTS idx_msg_status ON notif_messages(status);

-- Search & settings ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS synonyms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    term TEXT NOT NULL,
    alt_term TEXT NOT NULL,
    UNIQUE (term, alt_term)
);

CREATE TABLE IF NOT EXISTS saved_searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    scope TEXT NOT NULL,
    query_json TEXT NOT NULL,
    pinned INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Audit ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL DEFAULT (datetime('now')),
    actor_id INTEGER,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    action TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    prev_hash TEXT,
    this_hash TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity_type, entity_id);
