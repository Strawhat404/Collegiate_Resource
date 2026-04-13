-- Chunk 2: hierarchical catalog, employer evidence, BOM/routing, checkpoints,
-- update package history.

-- =========================================================================
-- Unified resource catalog: hierarchical tree, custom types, metadata
-- =========================================================================

-- Tree nodes (folders) — distinct from individual resources so we can browse.
CREATE TABLE IF NOT EXISTS catalog_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER REFERENCES catalog_nodes(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (parent_id, name)
);

-- Custom resource types (e.g. "Syllabus", "Practice Set") owned by Academic
-- Admin. Each type has a metadata template: an ordered list of fields with
-- a type, optional regex validation, and required flag.
CREATE TABLE IF NOT EXISTS catalog_types (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS catalog_type_fields (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type_id INTEGER NOT NULL REFERENCES catalog_types(id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    label TEXT NOT NULL,
    field_type TEXT NOT NULL,        -- text|int|date|enum|url|file|markdown
    regex TEXT,                      -- optional validation pattern
    required INTEGER NOT NULL DEFAULT 0,
    enum_values TEXT,                -- JSON array, when field_type='enum'
    sort_order INTEGER NOT NULL DEFAULT 0,
    UNIQUE (type_id, code)
);

-- Augment the existing `resources` table by attaching catalog metadata.
-- We use a side table so chunk-1 columns remain unchanged.
CREATE TABLE IF NOT EXISTS resource_catalog (
    resource_id INTEGER PRIMARY KEY REFERENCES resources(id) ON DELETE CASCADE,
    node_id INTEGER REFERENCES catalog_nodes(id),
    type_id INTEGER REFERENCES catalog_types(id),
    subject TEXT,
    grade TEXT,
    course TEXT,
    semver TEXT NOT NULL DEFAULT '0.1.0',
    review_state TEXT NOT NULL DEFAULT 'draft',  -- draft|in_review|approved|rejected
    reviewer_id INTEGER REFERENCES users(id),
    submitted_at TEXT,
    decided_at TEXT
);

CREATE TABLE IF NOT EXISTS resource_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_id INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    field_code TEXT NOT NULL,
    value TEXT,
    UNIQUE (resource_id, field_code)
);

CREATE TABLE IF NOT EXISTS resource_tags (
    resource_id INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY (resource_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_restags_tag ON resource_tags(tag);

CREATE TABLE IF NOT EXISTS resource_relations (
    src_id INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    dst_id INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    relation TEXT NOT NULL,           -- 'related'|'supersedes'|'requires'
    PRIMARY KEY (src_id, dst_id, relation)
);

-- =========================================================================
-- Employer compliance: evidence files, sensitive words, violation actions
-- =========================================================================

CREATE TABLE IF NOT EXISTS employer_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employer_id INTEGER NOT NULL REFERENCES employers(id) ON DELETE CASCADE,
    case_id INTEGER REFERENCES employer_cases(id) ON DELETE SET NULL,
    file_name TEXT NOT NULL,
    stored_path TEXT NOT NULL,        -- relative path under data_dir/evidence/
    mime_type TEXT,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    uploaded_by INTEGER REFERENCES users(id),
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now')),
    retain_until TEXT NOT NULL        -- now + 7 years
);
CREATE INDEX IF NOT EXISTS idx_evidence_employer ON employer_evidence(employer_id);
CREATE INDEX IF NOT EXISTS idx_evidence_retain ON employer_evidence(retain_until);

CREATE TABLE IF NOT EXISTS sensitive_words (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    word TEXT NOT NULL UNIQUE,
    severity TEXT NOT NULL DEFAULT 'medium',  -- low|medium|high
    category TEXT
);

CREATE TABLE IF NOT EXISTS violation_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employer_id INTEGER NOT NULL REFERENCES employers(id) ON DELETE CASCADE,
    case_id INTEGER REFERENCES employer_cases(id) ON DELETE SET NULL,
    action TEXT NOT NULL,             -- takedown|suspend|throttle
    duration_days INTEGER,            -- 30|60|180 for suspend
    starts_at TEXT NOT NULL DEFAULT (datetime('now')),
    ends_at TEXT,
    reason TEXT,
    actor_id INTEGER REFERENCES users(id),
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_violation_emp ON violation_actions(employer_id, ends_at);

-- =========================================================================
-- Style master + BOM + process routing (multi-version, two-step approval)
-- =========================================================================

CREATE TABLE IF NOT EXISTS styles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    style_code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS style_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    style_id INTEGER NOT NULL REFERENCES styles(id) ON DELETE CASCADE,
    version_no INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'draft',  -- draft|submitted|first_approved|released|rejected
    cost_usd REAL NOT NULL DEFAULT 0,
    notes TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    first_approver_id INTEGER REFERENCES users(id),
    first_approved_at TEXT,
    final_approver_id INTEGER REFERENCES users(id),
    released_at TEXT,
    UNIQUE (style_id, version_no)
);

CREATE TABLE IF NOT EXISTS bom_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    style_version_id INTEGER NOT NULL REFERENCES style_versions(id) ON DELETE CASCADE,
    component_code TEXT NOT NULL,
    description TEXT,
    quantity REAL NOT NULL DEFAULT 1,
    unit_cost_usd REAL NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_bom_version ON bom_items(style_version_id);

CREATE TABLE IF NOT EXISTS routing_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    style_version_id INTEGER NOT NULL REFERENCES style_versions(id) ON DELETE CASCADE,
    step_no INTEGER NOT NULL,
    operation TEXT NOT NULL,
    machine TEXT,
    setup_minutes REAL NOT NULL DEFAULT 0,
    run_minutes REAL NOT NULL DEFAULT 0,
    rate_per_hour_usd REAL NOT NULL DEFAULT 0,
    UNIQUE (style_version_id, step_no)
);

-- Change requests (proposals against an existing released version).
CREATE TABLE IF NOT EXISTS change_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    style_id INTEGER NOT NULL REFERENCES styles(id) ON DELETE CASCADE,
    base_version_id INTEGER NOT NULL REFERENCES style_versions(id),
    proposed_version_id INTEGER REFERENCES style_versions(id),
    requested_by INTEGER REFERENCES users(id),
    state TEXT NOT NULL DEFAULT 'open',  -- open|first_approved|approved|rejected
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- =========================================================================
-- Crash recovery: workspace state + draft checkpoints (every 60s)
-- =========================================================================

CREATE TABLE IF NOT EXISTS workspace_state (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    payload_json TEXT NOT NULL,
    saved_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS draft_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    draft_key TEXT NOT NULL,         -- caller-defined, e.g. 'student:new'
    payload_json TEXT NOT NULL,
    saved_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, draft_key)
);

-- =========================================================================
-- Update packages: signed offline import with rollback history
-- =========================================================================

CREATE TABLE IF NOT EXISTS update_packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    signed_by TEXT,
    signature_ok INTEGER NOT NULL DEFAULT 0,
    applied_at TEXT NOT NULL DEFAULT (datetime('now')),
    rolled_back_at TEXT,
    snapshot_path TEXT,              -- where pre-apply DB snapshot is stored
    notes TEXT
);
