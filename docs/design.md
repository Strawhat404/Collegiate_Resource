# CRHGC — Design Document

## 1. Goals & Non-Goals

**Goals**
- Offline, single-workstation governance console for student housing, academic resources, and employer compliance.
- Keyboard-first PyQt UI on Windows 11 with system tray presence.
- Local SQLite persistence with at-rest encryption and field-level masking.
- In-app notification & messaging center with templating and trigger/schedule rules.
- Auditable, immutable change history.
- Hierarchical resource catalog with custom types, metadata templates, reviewer approval, and semantic versioning.
- Employer compliance with locally stored evidence files (SHA-256 fingerprints, 7-year retention), offline sensitive-word checks, and violation actions (takedown / suspend 30/60/180 / throttle).
- Style master data with multi-version BOM and process routing, two-step approval, automatic USD cost recalculation, and full historical traceability.
- Sub-5 s startup, 8-hour session stability, automatic crash recovery, and offline signed update packages with rollback.
- Delivered as a Windows `.msi`, usable at 1920×1080 and above with High-DPI scaling.

**Non-Goals**
- Network synchronization, multi-user collaboration on the same dataset, mobile clients.
- Outbound email/SMS/IM. (OS toast popups *are* allowed — they remain on-device.)
- LMS integration.

## 2. Architecture Overview

```
┌────────────────────────────────────────────────────────────────────┐
│                         PyQt Desktop Shell                          │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ MainWindow (tabbed) + Detached Windows + Tray Icon + Hotkeys │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                │                                   │
│                                ▼                                   │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                    Service Layer (sync + threadpool)         │  │
│  │  Auth  Student  Housing  Resource  Compliance  Reporting     │  │
│  │  Notification  Search  Audit  Settings                       │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                │                                   │
│                                ▼                                   │
│  ┌────────────────┐   ┌─────────────────┐   ┌───────────────────┐  │
│  │ Repositories   │   │ Event Bus       │   │ Background Worker │  │
│  │ (SQL access)   │   │ (in-process)    │   │ (notif dispatch,  │  │
│  └────────┬───────┘   └────────┬────────┘   │  schedule, audit) │  │
│           │                    │            └───────────┬───────┘  │
│           ▼                    ▼                        ▼          │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │     SQLite (encrypted file: crhgc.db)  +  FTS5 indexes       │  │
│  └──────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────┘
```

### Process & Threading

- **Main GUI thread**: all `QWidget` work.
- **Worker pool** (planned `QThreadPool`): bulk import/export, report generation, and FTS rebuild are designed to run off the GUI thread. The shipped build runs them inline on the calling thread; the pool wiring is not yet implemented.
- **Notification dispatcher** (`QTimer`-driven, 30 s tick): drains queue, retries failed local writes, fires scheduled rules.
- **Trigger evaluator**: subscribes to the in-process event bus; matches events to active rules; enqueues messages.

### Single-Instance Enforcement

Designed to be enforced via a `QSharedMemory("crhgc.singleton")` segment with focus-handoff over `QLocalSocket`. **Not yet implemented** in the shipped build — multiple instances on the same data directory are currently possible and rely on SQLite's own write locking for consistency.

## 3. Module Layout

```
repo/
├── backend/
│   ├── app.py              # Application bootstrap, container wiring
│   ├── config.py           # Paths, constants, default settings
│   ├── db.py               # Connection factory, encryption shim
│   ├── crypto.py           # Key management, AES-GCM helpers, PBKDF2
│   ├── events.py           # In-process pub/sub
│   ├── permissions.py      # Role/permission registry, decorators
│   ├── audit.py            # Hash-chained append-only audit log
│   ├── services/           # Service classes per api-spec.md
│   │   ├── auth.py             # AuthService + BizError
│   │   ├── student.py          # CSV + XLSX import/export with dry-run preview
│   │   ├── housing.py, resource.py, catalog.py
│   │   ├── compliance.py, compliance_ext.py  # Cases, evidence, violations
│   │   ├── bom.py              # Styles + BOM + routing + two-step approval
│   │   ├── notification.py     # Dispatcher + scheduled rules + retry/dead-letter
│   │   ├── reporting.py, search.py, settings.py, checkpoint.py, updater.py
│   └── models/__init__.py  # All dataclasses (DTOs + entities) live here
├── frontend/
│   ├── main_window.py      # Tabs + tray + global shortcuts (single module)
│   ├── tabs_extra.py       # Catalog, Evidence/Actions, Styles/BOM, Updates
│   ├── windows/            # Detachable windows (StudentProfile, etc.)
│   ├── widgets/            # Reusable widgets (SearchPalette, ResultsTable)
│   ├── dialogs.py          # Login, Bootstrap, Unlock
│   └── style.qss           # Stylesheet (high-contrast keyboard focus)
├── database/
│   ├── migrations/         # 0001_initial → 0006_notif_enqueue_failures
│   └── seed/               # seed_dev.sql + seed_extras.sql for demo data
└── README.md
```

## 4. Data Model (key tables)

```sql
-- Identity
users(id PK, username UNIQUE, full_name, password_hash, password_salt,
      disabled INT, created_at, last_login_at)
roles(id PK, code UNIQUE, name)
user_roles(user_id, role_id, PRIMARY KEY(user_id, role_id))
permissions(id PK, code UNIQUE)
role_permissions(role_id, permission_id, PRIMARY KEY(role_id, permission_id))

-- Student / Housing
students(id PK, student_id_ext UNIQUE, full_name, college, class_year,
         email_enc, phone_enc, ssn_last4_enc, housing_status,
         created_at, updated_at)
buildings(id, name, address)
rooms(id, building_id, floor, code)
beds(id, room_id, code, capacity, status)
bed_assignments(id, student_id, bed_id, effective_date, end_date,
                reason, operator_id, created_at)

-- Academic Resources
resource_categories(id, name, parent_id)
resources(id, category_id, title, owner_id, status,  -- 'active'|'on_hold'
          created_at)
resource_versions(id, resource_id, version_no, summary, body TEXT,
                  status, -- 'draft'|'published'|'unpublished'|'superseded'
                  published_at, published_by, created_at)

-- Employer Compliance
employers(id, name, ein, contact_email_enc, status, created_at)
employer_cases(id, employer_id, kind, -- 'onboarding'|'violation'
               state, reviewer_id, decision, decided_at, notes, created_at)

-- Notifications
notif_templates(id, name, subject, body, variables_json)
notif_rules(id, name, kind, -- 'trigger'|'schedule'
            event_name, cron_spec, template_id, audience_query, enabled)
notif_messages(id, template_id, audience_user_id, body_rendered,
               status, -- 'queued'|'delivered'|'failed'|'dead'
               attempts, last_attempt_at, scheduled_for, created_at,
               read_at)

-- Search & Settings
synonyms(id, term, alt_term)
saved_searches(id, owner_id, name, scope, query_json, pinned, created_at)
settings(key PRIMARY KEY, value)

-- Audit
audit_log(id PK, ts, actor_id, entity_type, entity_id, action,
          payload_json, prev_hash, this_hash)
```

FTS5 virtual tables (`students_fts`, `resources_fts`, `employers_fts`,
`cases_fts`) shadow the canonical tables via triggers.

## 5. Security

- **Authentication**: PBKDF2-HMAC-SHA256, 200,000 iterations, 16-byte salt; constant-time compare.
- **Session**: in-memory `Session(user_id, roles, permissions, mask_unlock_until)`. No persistence.
- **Encryption at rest**: SQLCipher when available; otherwise per-field AES-GCM with a 32-byte key wrapped by DPAPI on Windows. Key file lives in `%LOCALAPPDATA%\CRHGC\key.bin` with restricted ACLs.
- **Field masking**: `email`, `phone`, `ssn_last4` are stored encrypted and rendered as `***@***.com`, `(***) ***-1234`, `***-**-1234` unless `student.pii.read` is held *and* `mask_unlock_until > now()`.
- **Audit chain**: each row's `this_hash = SHA256(prev_hash || canonical_json(payload))`. `verify_chain()` walks the table; mismatch surfaces in the Operator Console.
- **Re-entry**: revealing masked fields requires re-entering the password (independent of session age).

## 6. UI / UX

### Tabbed Workspace
Default tabs: **Students**, **Housing**, **Resources**, **Compliance**, **Notifications**, **Reports**. Each tab is a `QWidget` with its own toolbar + table + filter panel + detail pane.

### Detachable Windows
Right-clicking a row offers "Open in new window" — the detail widget is reparented into a top-level `QMainWindow`. Closing the window returns it to the tabbed pane (state preserved).

### Global Shortcuts
| Shortcut | Action |
|---|---|
| `Ctrl+K` | Universal search palette (modal, fuzzy) |
| `Ctrl+Shift+N` | Create new record (context-sensitive: student/resource/case) |
| `Ctrl+E` | Export current result set |
| `Ctrl+,` | Settings |
| `Ctrl+L` | Lock session (re-login required) |
| `F1` | Context help |
| `Esc` | Close current dialog or detached window |

### Right-click Context Menus
- Students table: *Open profile*, *Assign bed*, *Vacate bed*, *Export selection*, *History*.
- Resources table: *Add version*, *Publish version*, *Unpublish*, *Place on hold*, *Release hold*, *Diff versions*.
- Compliance queue: *Assign reviewer*, *Approve*, *Reject*, *Open violation*, *Resolve violation*.

### Tray Presence
`QSystemTrayIcon` with menu: Open, Search, Notifications (badge counter), Lock, Quit. Closing the main window minimizes to tray; the worker keeps running for scheduled rules.

### Accessibility
- Tab order is explicit on every form.
- Visible focus outline (3 px accent) via `style.qss`.
- All actions also reachable via menubar (no mouse required).
- Hi-DPI aware (`AA_EnableHighDpiScaling`).

## 7. Notifications

### Template Rendering
`render(template, vars)` performs `{Var}` substitution from a fixed registry (see questions.md Q12). Unknown placeholders are replaced with empty strings and emit a warning into the audit log; this prevents a missing variable from blocking delivery.

### Trigger Rules
The event bus emits high-level events from services. Each `notif_rule` with matching `event_name` is evaluated; the `audience_query` (a saved structured query) resolves to a list of user IDs at evaluation time; one `notif_message` row per recipient is enqueued.

### Scheduled Rules
Cron-like spec (subset: `M H * * *` daily; weekdays via list). The dispatcher tick checks the next-fire timestamp.

### Delivery & Retry
1. Dispatcher selects `status='queued' AND (scheduled_for IS NULL OR scheduled_for <= now)`.
2. Renders body, writes to inbox, marks `delivered`, increments unread counter.
3. On local write failure (disk-full, locked DB), increments `attempts`, sets `status='queued'`, schedules next attempt at `now + 5 minutes`.
4. After 3 failed attempts the row is moved to `status='dead'` and surfaced in the Operations dashboard. Manual `retry_failed` re-queues.

### Enqueue-stage retry (event-bus triggers)
Trigger-rule callbacks must own their own retry: the in-process event bus
swallows handler exceptions to protect publishers, so a transient
`OperationalError` would otherwise lose a notification at the `INSERT INTO
notif_messages` step. Each insert is wrapped in a bounded retry loop
(`config.NOTIF_RETRY_LIMIT` attempts, exponential 50 ms back-off). On
exhaustion the would-be message is persisted to `notif_enqueue_failures`
(dead-letter for enqueue) and surfaced through `retry_failed`, which both
re-queues `notif_messages` dead-letters and replays unreplayed
`notif_enqueue_failures` rows.

## 8. Search

- **Full-text**: SQLite FTS5 with `porter` tokenizer.
- **Fuzzy**: `rapidfuzz.fuzz.token_set_ratio` over the candidate set returned by FTS or by structured filters; threshold default 78, configurable.
- **Synonyms**: query terms expand to `term OR alt1 OR alt2` before being passed to FTS.
- **Universal palette** (`Ctrl+K`) ranks across student, resource, employer, case scopes; results carry an `open_action` so the UI knows which window to launch.
- **Saved searches** persist `query_json` (the structured form) and can be pinned to the sidebar; pinned queries also drive the right-click "Refresh from saved search" menu.

## 9. Bulk Import / Export

- **Importer** parses CSV/XLSX into row dicts, validates against a per-entity schema (required columns, MM/DD/YYYY date parsing, enumerations, length caps, duplicate-ID strategy), and produces an `ImportPreview` containing accepted/rejected rows and a unique `preview_id` cached in `import_previews` (TTL 30 minutes).
- **Commit** applies the preview within a single transaction with a savepoint per row; rejected rows are recorded in an `import_errors` table for download.
- **Exporter** mirrors the same column layout. CSV uses `utf-8-sig` BOM for Excel compatibility; XLSX uses `openpyxl` with frozen header row.

## 10. Reporting

- Reports are pure SQL views materialized on demand; the service layer wraps results in a `Report` dataclass and the UI renders via `QTableView`. Export currently supports CSV and XLSX (`openpyxl`); the planned ReportLab-based PDF export for the daily digest is not yet implemented and the `pdf` format is rejected with `ValueError`.

## 11. Failure Modes

| Failure | Behavior |
|---|---|
| DB file missing / corrupt | Fail-fast with a "Restore from backup" dialog pointing at `%LOCALAPPDATA%\CRHGC\backups\`. |
| Encryption key missing | Block startup; offer "Recover from passphrase" or "Initialize new key (data will be unreadable)". |
| Worker thread crash | Logged to `crhgc.log`; UI shows a non-blocking toast; service is restarted. |
| Notification queue lock | Retry per §7. |
| Audit chain break | Status bar warning; report in Operator Console; no automatic repair. |

## 12. Build & Packaging

- Python 3.11+, PyQt6, dependencies in `requirements.txt`.
- PyInstaller spec produces a single-folder `CRHGC.exe` for distribution.
- First-run wizard creates the data directory, key file, runs migrations, and prompts for the bootstrap admin.
- **Windows installer**: WiX 3.x source at `repo/installer/CRHGC.wxs` and PowerShell build script at `repo/installer/build_msi.ps1` produce `CRHGC.msi`. Per-machine install into `C:\Program Files\CRHGC`; per-user data in `%LOCALAPPDATA%\CRHGC` is preserved across upgrades.

## 13. Testing Strategy

- **Unit**: services and repositories exercised against an in-memory SQLite (encryption disabled in tests).
- **Property**: importer date/format validators (Hypothesis).
- **UI smoke**: `repo/tests/test_ui_smoke.py` boots `MainWindow` under `QT_QPA_PLATFORM=offscreen`, walks every tab, fires the universal-search palette (Ctrl+K), and tears down cleanly. Uses `pytest-qt`'s `qtbot` when installed; falls back to a small `QTest`-based shim so the same test also runs under the `run_all.py` headless runner, and skips silently when PyQt6 itself is not present.
- **Audit**: regression test that 1,000 random mutations produce a chain that `verify_chain()` accepts.
- **Headless verification**: `repo/verify.py` exercises 17 service flows end-to-end against a throwaway SQLite database (bootstrap, masked PII reveal, bed assignment, resource publish, compliance approval, universal search, audit chain, occupancy reporting, catalog metadata validation + semver bump, evidence SHA-256 verify, sensitive-word scan, violation actions, BOM with two-step approval and cost recalc, workspace + draft checkpoints, updater apply + rollback, and startup-time budget).

## 14. Unified Resource Catalog

### Tree
A self-referential `catalog_nodes` table provides arbitrary-depth folders under the *Catalog* tab. Folders cannot be deleted while resources are attached.

### Custom resource types and metadata templates
`catalog_types` defines a type (e.g., *Syllabus*); `catalog_type_fields` holds an ordered list of fields with:

- `field_type` ∈ {`text`, `int`, `date`, `enum`, `url`, `file`, `markdown`}
- optional `regex` validation pattern
- `required` flag
- `enum_values` JSON list when `field_type='enum'`

A resource is attached to a node + type via `resource_catalog`; metadata values live in `resource_metadata` keyed by `(resource_id, field_code)`. `CatalogService.attach()` validates each value against the type definition (required / int / date in MM/DD/YYYY / enum membership / regex match) and raises `BizError(METADATA_BAD)` on the first failure.

### Tags and relationships
- `resource_tags(resource_id, tag)` — many-to-many; lower-cased on insert.
- `resource_relations(src_id, dst_id, relation)` — relation ∈ {`related`, `supersedes`, `requires`}.

### Subject / grade / course pointers
Stored directly on `resource_catalog` as plain strings — free-form to keep the schema simple while supporting the typical reporting lookups.

### Versioning + reviewer approval + semver
The chunk-1 `resource_versions` table continues to hold version bodies and draft/published state. Catalog adds:

- `resource_catalog.review_state` ∈ {`draft`, `in_review`, `approved`, `rejected`}
- `resource_catalog.semver` (e.g., `2.3.0`)

Workflow: `submit_for_review` → reviewer calls `review('approve'|'reject')` → on approval, the next call to `ResourceService.publish_version(version_id, semver_level=…)` (or its delegating wrapper `CatalogService.publish_with_semver`) atomically marks the version published AND increments `resource_catalog.semver` in a single transaction. There is no path that publishes without the semver bump or vice versa; the audit log records both the publish action and the resulting semver. The `semver_level` argument controls major/minor/patch.

## 15. Employer Compliance — Evidence, Words, Actions

### Evidence files
`employer_evidence` records every uploaded document. Files are copied into `%LOCALAPPDATA%\CRHGC\evidence\<employer_id>\<sha-prefix>\<uuid>__<name>`. Each row stores:

- `sha256` — full file digest
- `size_bytes`, `mime_type`, `uploaded_by`, `uploaded_at`
- `retain_until = uploaded_at + 7 years` (configurable in `config.EVIDENCE_RETENTION_YEARS`)

`EvidenceService.verify(id)` re-hashes the on-disk file and compares it to the recorded digest. `purge_expired()` deletes files past their retention window and writes per-file audit entries.

### Sensitive-word dictionary (offline)
`sensitive_words(word, severity, category)` is editable by System Administrators. `SensitiveWordService.scan(text)` performs case-insensitive word-boundary matching and returns `{word, severity, category, position}` hits — used by the *Sensitive-word scan* dialog and by validators before auto-approving any employer submission.

### Violation actions
`violation_actions(action, duration_days, starts_at, ends_at, reason)`:

| Action | `duration_days` | Effect |
| --- | --- | --- |
| `takedown` | NULL | `employers.status='taken_down'` |
| `suspend` | 30 / 60 / 180 | `employers.status='suspended'`; `ends_at = now + days` |
| `throttle` | NULL | `employers.status='throttled'` (hidden from default search) |

Any other duration is rejected. `is_hidden_from_default_search()` returns True when an active action exists; default search filters honor it. `revoke()` clears the action and (for takedown/throttle) restores `status='approved'`.

## 16. Style master + BOM + process routing

### Tables
- `styles(style_code, name, status)`
- `style_versions(style_id, version_no, state, cost_usd, …)` — state ∈ {`draft`, `submitted`, `first_approved`, `released`, `rejected`}
- `bom_items(style_version_id, component_code, quantity, unit_cost_usd, …)`
- `routing_steps(style_version_id, step_no, operation, machine, setup_minutes, run_minutes, rate_per_hour_usd)`
- `change_requests(style_id, base_version_id, proposed_version_id, requested_by, state, reason)`

### Cost recalculation
`compute_cost(version_id) = Σ qty·unit_cost  +  Σ ((setup+run)/60 · rate)` in USD. `add_bom_item` and `add_routing_step` both call `_recalc_cost`, which writes a `cost_recalc` audit row only when the value actually changes. Released versions are immutable: editing methods raise `BizError(LOCKED)`.

### Two-step approval
`submit_for_approval` → `first_approve` (perm `bom.approve.first`) → `final_approve` (perm `bom.approve.final`). The final approver MUST differ from the first approver — enforced by comparing user IDs. `released_at` is set on final approval; the version is then locked.

### Change requests against released versions
`open_change_request(style_id, base_version_id, reason)` clones the BOM and routing into a brand-new draft version (next `version_no`) and records a `change_requests` row. The new version follows the same two-step approval flow before becoming the new released revision; the prior released version remains permanently retrievable for historical traceability.

## 17. Non-functional requirements

### Startup < 5 s
`Container.__init__` instruments three phases in `STARTUP_PROFILE`: `db_open_s`, `seed_s`, `services_s` (and `total_s`). If `total_s` exceeds `config.TARGET_STARTUP_SECONDS` (5 s), a warning is written to `crhgc.log`. Verification step #17 asserts the headless boot stays under the limit.

Optimizations: WAL journal, no eager FTS rebuild, lazy service factories, deferred PyQt high-DPI policy set only when no `QApplication` exists yet.

### 8-hour stability + cleanup
- Every closed detached window is removed from `MainWindow.detached` and `deleteLater()`'d in `_drain_detached()`.
- Notification dispatcher runs on a single `QTimer`; the autosave checkpoint runs on another. Both are stopped on quit.
- The notification queue and audit chain are append-only; nothing grows in memory beyond the inbox view (capped at 200 rows).

### Crash recovery
- `workspace_state(user_id, payload_json)` — one row per user, written every 60 s with the active tab and the open tab list.
- `draft_checkpoints(user_id, draft_key, payload_json)` — keyed unsaved-form data, written by callers via `save_draft()`.
- On launch the main window calls `_restore_workspace()` and `_offer_draft_recovery()` (modal dialog: restore or discard).

### Offline signed update + rollback
- Format: ZIP containing `update.json`, `update.json.sig`, `payload/...`. Signature is RSA-PSS over `update.json` raw bytes using the public key at `update_pubkey.pem`.
- Apply: insert provenance row → snapshot DB → copy payload files → update row with snapshot path. Path traversal in member names is rejected up front.
- Rollback: closes the live SQLite connection, copies a *pre-rollback* snapshot for reversibility, then restores the saved snapshot.

### High-DPI / 1920×1080
- `Qt.HighDpiScaleFactorRoundingPolicy.PassThrough` is requested before the `QApplication` is constructed.
- `style.qss` uses logical px and 3 px focus outlines that remain visible at 150 % / 200 % scale.

## 18. Permissions added in chunk 2

| Code | Default holders |
| --- | --- |
| `catalog.write`, `catalog.review`, `catalog.publish` | Academic Admin, System Admin |
| `compliance.evidence`, `compliance.action` | Compliance Reviewer, System Admin |
| `bom.write`, `bom.approve.first` | Operations Analyst, System Admin |
| `bom.approve.final` | System Admin only |
| `update.apply` | System Admin only |

Migration `0004_permissions_extra.sql` is idempotent (`INSERT OR IGNORE`) so it lands on top of any existing database without disturbing earlier roles.
