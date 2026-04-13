# CRHGC — Collegiate Resource & Housing Governance Console

Offline desktop application for US-based education providers to administer
student housing records, an academic resource catalog, and employer
onboarding/compliance — with a notification center, universal search,
encrypted local SQLite storage, and an immutable, hash-chained audit log.

The UI is built with **PyQt6** (keyboard-first, multi-window, system-tray
present) targeting **Windows 11**. The application also runs on Linux/macOS for
development.

## Repository Layout

```
repo/
├── backend/        # Python service layer, persistence, crypto, events
├── frontend/       # PyQt6 windows, widgets, dialogs, stylesheet
├── database/
│   ├── migrations/   # 0001_initial.sql, 0002_fts.sql
│   └── seed/         # seed_dev.sql (roles, permissions, demo housing,
│                     #               notification templates and rules)
├── main.py         # GUI entry point
├── verify.py       # Headless verification script
├── requirements.txt
└── README.md
```

The companion design documents live at `../docs/` (`questions.md`,
`api-spec.md`, `design.md`).

## Installation

Requires **Python 3.11+**.

```bash
# 1. Clone or unpack the project, then change into repo/
cd repo

# 2. Create a virtual environment (recommended)
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt
```

If `PyQt6` is unavailable in your environment (e.g., a headless server),
the backend and `verify.py` still run because they do not import PyQt.

## Startup

### Launch the GUI

```bash
python main.py
```

On the very first run:
1. The data directory is created (Windows: `%LOCALAPPDATA%\CRHGC`, Linux:
   `~/.local/share/CRHGC`).
2. SQLite migrations are applied; demo data is seeded if the database is empty.
3. A **bootstrap dialog** appears asking you to create the first
   System Administrator account (password ≥ 10 characters).
4. Sign in. The main window opens with tabs for *Students, Housing, Resources,
   Compliance, Notifications, Reports*.

### Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| `Ctrl+K` | Universal search palette |
| `Ctrl+Shift+N` | Create new record (context-sensitive) |
| `Ctrl+E` | Export current result set |
| `Ctrl+L` | Lock session |
| `Ctrl+,` | Settings |
| `Esc` | Close active dialog / detached window |
| `F1` | About dialog |

### System tray

When you close the main window the app minimizes to the system tray; the
notification dispatcher continues to run. Right-click the tray icon for *Open*,
*Search…*, *Lock*, and *Quit*.

## Modules added in chunk 2

- **Catalog** — hierarchical folder tree, custom resource types with
  metadata templates (text/int/date/enum/url/file/markdown, regex
  validation, required flags), tags, relationships, reviewer approval,
  and semantic-version bumps on publish.
- **Evidence & violations** — locally stored evidence files with SHA-256
  fingerprints, 7-year retention, an offline sensitive-word scanner,
  and violation actions (`takedown`, `suspend 30/60/180`, `throttle`).
- **Styles / BOM / Routing** — multi-version BOM and process routing
  with two-step approval (first + final must be different users),
  automatic USD cost recalculation with audit trail, change requests
  against released versions, and full historical traceability.
- **Crash recovery** — workspace state persisted every 60 s, unsaved
  form drafts checkpointed and offered for restoration on next launch.
- **Updater** — import a signed offline update package (ZIP +
  RSA-PSS signature). A DB snapshot is taken before each apply; any
  package can be rolled back from the *Updates* tab.
- **Windows MSI installer** — see `installer/README.md` for build steps.

## Verification

A scripted, headless smoke test exercises the major service flows
(bootstrap, login, masked PII reveal, bed assignment with triggered
notification, resource publishing, compliance approval, universal search,
audit chain verification, and occupancy reporting). It uses a temporary,
throwaway SQLite file and prints a pass/fail summary:

```bash
python verify.py
```

Expected output ends with:

```
Verification: 17/17 checks passed.
```

Exit code is `0` on full success, `1` otherwise — suitable for CI.

### Unit tests

The `tests/` directory holds pytest-style unit tests covering object-level
authorization, PII redaction in the audit log, scheduled-notification
firing, catalog publish governance, hidden-employer search filtering,
update-package signature enforcement, and Excel import/export round-trips.

```bash
# Preferred (when pytest is installed):
pytest -q

# Fallback runner (no third-party deps required):
python tests/run_all.py
```

Both runners exercise the same test modules. Expected output ends with
`16/16 tests passed`.

## Notes

- **At-rest encryption.** Sensitive fields (email, phone, SSN-last4) are
  encrypted with AES-GCM via the `cryptography` library, using a 32-byte key
  stored in the per-user data directory. If `cryptography` is missing, the
  application falls back to an obfuscation cipher and emits a warning — do
  not rely on this for production data.
- **Masked fields** are revealed only after a fresh password re-entry; the
  reveal expires after 5 minutes of activity (configurable in `backend/config.py`).
- **Notification retries.** Failed local-queue writes are retried up to 3
  times at 5-minute intervals; persistently failing messages move to a
  dead-letter state surfaced in the *Notifications* tab.
- **Audit log.** Every mutating operation appends a row whose `this_hash` is
  `SHA-256(prev_hash || canonical_json(payload))`. Run
  `audit.verify_chain()` (or the `verify.py` script) to confirm integrity.

## Uninstall / reset

Delete the data directory printed at the top of `verify.py` output (or the
default `%LOCALAPPDATA%\CRHGC` / `~/.local/share/CRHGC`) to wipe all local
state, including the encryption key.
