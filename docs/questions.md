# Open Questions & Clarifications

This document captures clarifying questions, assumptions, and decisions made during the design of the Collegiate Resource & Housing Governance Console (CRHGC).

## 1. Scope & Platform

- **Q1.** Is the application strictly Windows 11, or should it be portable to other desktops?
  - *Assumption:* Built for Windows 11 as primary target; PyQt provides cross-platform compatibility, so it will also run on Linux/macOS for development. System tray APIs are available on all three.
- **Q2.** Should the app support multi-instance launches on the same workstation?
  - *Assumption:* Single-instance per OS user. A second launch focuses the existing window. Enforced via a local lockfile + named QSharedMemory key.

## 2. Authentication & Roles

- **Q3.** Are user accounts local-only, or do we federate to AD/LDAP?
  - *Assumption:* Local-only (offline). Passwords stored as PBKDF2-HMAC-SHA256 hashes (200k iterations) with a per-user salt. Re-entry of the password unlocks masked fields for 5 minutes.
- **Q4.** Five roles defined: System Administrator, Housing Coordinator, Academic Admin, Compliance Reviewer, Operations Analyst. Can a single user hold multiple roles?
  - *Assumption:* Yes — roles are additive. Permissions are computed as the union of role permissions.
- **Q5.** What happens if no admin exists at first launch?
  - *Decision:* The first launch presents a one-time bootstrap dialog requiring creation of a System Administrator account.

## 3. Data Model

- **Q6.** Are SSN fragments actually stored?
  - *Decision:* By default, *no*. The schema reserves an `ssn_last4` column on `students` so it can be enabled by policy; it is masked unless the active session has the `student.pii.read` privilege and the user has unlocked masked fields.
- **Q7.** Bed assignment granularity: building → floor → room → bed?
  - *Decision:* Yes — four-level hierarchy. Each `bed` row uniquely identifies the position, and at most one active assignment per bed.
- **Q8.** Resource catalog item types?
  - *Decision:* `document`, `link`, `course_note`, `syllabus`, `practice_set`. Each item has a versioned `resource_version` row; only one version is "published" at a time.
- **Q9.** Employer compliance lifecycle states?
  - *Decision:* `submitted → under_review → approved | rejected → (optionally) violation_open → resolved`.

## 4. Notifications & Messaging

- **Q10.** "Local notification only" — does this preclude OS toast notifications?
  - *Decision:* OS toast (via `QSystemTrayIcon.showMessage`) is permitted because it is local. No outbound SMS, email, or push to third-party services.
- **Q11.** Retry semantics for failed queue writes?
  - *Decision:* Up to 3 attempts at 5-minute intervals. If all fail, the message is moved to the dead-letter table and surfaced in the Operator Console.
- **Q12.** Template variables — open list or fixed?
  - *Decision:* Fixed registry: `{StudentName}`, `{StudentID}`, `{Dorm}`, `{Room}`, `{Bed}`, `{EffectiveDate}`, `{EmployerName}`, `{ResourceTitle}`, `{Operator}`, `{Today}`. Unknown placeholders render as empty strings and emit a validator warning.

## 5. Search

- **Q13.** Fuzzy matching algorithm?
  - *Decision:* `rapidfuzz` token-set ratio with a configurable threshold (default 78). For full-text, SQLite FTS5 virtual tables back the `students_fts`, `resources_fts`, `employers_fts`, `cases_fts` indexes.
- **Q14.** Synonym sets — how managed?
  - *Decision:* `synonyms` table editable by System Administrators; expansion happens at query time before tokenization.

## 6. Encryption at Rest

- **Q15.** Which library is used for SQLite encryption?
  - *Assumption:* SQLCipher is preferred but may not be available in vanilla `sqlite3`. The application detects `pysqlcipher3`; if absent, it falls back to application-level field encryption (AES-GCM via `cryptography`) for sensitive columns and a key file stored under DPAPI on Windows (or a plain keyfile with a permission warning on other OSes). The schema and APIs are identical in either mode.

## 7. Bulk Import/Export

- **Q16.** Excel format support?
  - *Decision:* `.xlsx` only, via `openpyxl`. `.xls` is rejected with a friendly error.
- **Q17.** Date format strictness?
  - *Decision:* `MM/DD/YYYY` is enforced on input; export always emits `MM/DD/YYYY`. Validation rejects ambiguous values such as `02/30/2026`.
- **Q18.** Duplicate student ID handling on import?
  - *Decision:* User chooses one of: `skip`, `update`, `error` per import. Default is `error`.

## 8. Reporting & Exports

- **Q19.** Which reports are required for the Operations Analyst dashboard?
  - *Decision:* Occupancy by dorm, move-in/move-out trends (rolling 30 days), resource publish velocity, employer review SLA, and notification delivery rate.
- **Q20.** Export formats?
  - *Decision:* CSV everywhere, Excel for tabular reports, PDF (via ReportLab) for the daily digest.

## 9. Audit Logging

- **Q21.** What constitutes an immutable change?
  - *Decision:* Every `INSERT/UPDATE/DELETE` on `students`, `bed_assignments`, `resource_versions`, `employer_cases`, `users`, and `roles` writes a row to `audit_log` (append-only, hash-chained per row using SHA-256 of the prior row's hash + the canonical JSON of the new row).

## 10. Open Items Deferred

- Calendar integration (Outlook/iCal) — explicitly out of scope for v1.
- Mobile companion app — out of scope.
- Real-time multi-user collaboration — single-workstation only; multi-user is achieved through separate OS sessions.
