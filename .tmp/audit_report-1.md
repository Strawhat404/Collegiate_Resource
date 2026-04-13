# Delivery Acceptance and Project Architecture Audit (Static-Only, Rerun)

## 1. Verdict
- Overall conclusion: **Partial Pass**
- Summary: the previous major blockers were largely remediated (scheduled rules, student write/import authorization, XLSX import/export, updater signature enforcement, audit redaction, lock behavior), but material gaps remain in publication-governance coupling, queue-write retry guarantees, and read-level authorization boundaries.

## 2. Scope and Static Verification Boundary
- What was reviewed:
  - Docs and setup/testing guidance: `repo/README.md:31`, `repo/README.md:128`, `docs/design.md:57`, `docs/api-spec.md:1`
  - Entry/wiring: `repo/main.py:13`, `repo/backend/app.py:21`
  - Migrations/seeds: `repo/database/migrations/0001_initial.sql:10`, `repo/database/migrations/0005_governance_fixes.sql:4`, `repo/database/seed/seed_extras.sql:43`
  - Security/business services: `repo/backend/services/*.py`
  - Frontend workflow wiring: `repo/frontend/main_window.py:59`, `repo/frontend/tabs_extra.py:15`
  - Test suite and config: `repo/pytest.ini:1`, `repo/tests/*.py`, `repo/tests/run_all.py:1`
- What was not reviewed:
  - Runtime behavior under real GUI interaction, real disk-lock/disk-full faults, real Windows packaging/install, performance under production hardware, 8-hour soak behavior.
- What was intentionally not executed:
  - No app startup, no tests, no Docker, no external services.
- Claims requiring manual verification:
  - startup < 5s (`repo/backend/app.py:61`, `repo/verify.py:267`)
  - 8-hour stability and resource cleanup effectiveness
  - High-DPI correctness on target displays
  - MSI compile/install success on Windows toolchain

## 3. Repository / Requirement Mapping Summary
- Prompt goal and major flows are mapped to:
  - Offline PyQt desktop shell with tray/shortcuts/tabs: `repo/frontend/main_window.py:449`
  - Local SQLite + services for housing/resources/compliance/notifications/reporting: `repo/backend/services/__init__.py:1`
  - Governance/security persistence via migrations and audit: `repo/database/migrations/0003_catalog_compliance_bom.sql:45`, `repo/backend/audit.py:15`
- Core remediations now present:
  - scheduled notification firing + dedup (`repo/backend/services/notification.py:170`)
  - student write/import permissions (`repo/backend/services/student.py:74`)
  - XLSX import/export in service + UI (`repo/backend/services/student.py:132`, `repo/frontend/main_window.py:103`)
  - updater signature required by default (`repo/backend/services/updater.py:87`)
  - update pubkey provisioning at startup (`repo/backend/app.py:67`)
  - PII redaction in student audit payloads (`repo/backend/services/student.py:19`)

## 4. Section-by-section Review

### 4.1 Hard Gates
#### 4.1.1 Documentation and static verifiability
- Conclusion: **Partial Pass**
- Rationale: run/start/test instructions are present and static verification is feasible, but docs still contain architecture/spec drift.
- Evidence:
  - setup/start/test docs: `repo/README.md:53`, `repo/README.md:128`
  - architecture doc references modules not present as separate files (`importer.py`, `exporter.py`, `tray.py`, `shortcuts.py`): `docs/design.md:69`, `docs/design.md:79`
- Manual verification note: build/install instructions still require real Windows verification.

#### 4.1.2 Material deviation from prompt
- Conclusion: **Partial Pass**
- Rationale: implementation is now strongly aligned, but publication semantics still deviate from “publish requires approval and semver increment” as a single enforced flow.
- Evidence:
  - resource publish enforces approval gate only: `repo/backend/services/resource.py:85`
  - semver bump remains separate method, not coupled to publish: `repo/backend/services/catalog.py:345`
  - standard resource tab publishes without semver bump call: `repo/frontend/main_window.py:291`

### 4.2 Delivery Completeness
#### 4.2.1 Core requirements coverage
- Conclusion: **Partial Pass**
- Rationale: most explicit requirements are statically covered; remaining critical gap is governance coupling and retry semantics for queue-write failures.
- Evidence:
  - scheduled rules + cron subset: `repo/backend/services/notification.py:170`, `repo/backend/services/notification.py:296`
  - CSV/XLSX dry-run + commit flow: `repo/backend/services/student.py:132`, `repo/backend/services/student.py:186`
  - XLSX export support: `repo/backend/services/student.py:230`
  - publish governance split (not atomic): `repo/backend/services/resource.py:78`, `repo/backend/services/catalog.py:346`

#### 4.2.2 End-to-end deliverable vs partial/demo
- Conclusion: **Pass**
- Rationale: complete multi-module desktop app structure plus smoke script plus unit tests are present.
- Evidence:
  - complete project layout: `repo/README.md:12`
  - verification script: `repo/verify.py:1`
  - test suite/config: `repo/pytest.ini:1`, `repo/tests/test_permissions.py:23`

### 4.3 Engineering and Architecture Quality
#### 4.3.1 Structure and decomposition
- Conclusion: **Pass**
- Rationale: clean service decomposition with coherent module boundaries.
- Evidence:
  - container wiring and service separation: `repo/backend/app.py:42`
  - frontend tab/window/widget decomposition: `repo/frontend/main_window.py:21`, `repo/frontend/tabs_extra.py:15`, `repo/frontend/widgets/results_table.py:10`

#### 4.3.2 Maintainability/extensibility
- Conclusion: **Partial Pass**
- Rationale: mostly maintainable, but critical governance logic remains split across services/UI paths.
- Evidence:
  - publish and semver remain decoupled: `repo/backend/services/resource.py:78`, `repo/backend/services/catalog.py:346`
  - tests guard this partially (approval gate) but not semver coupling: `repo/tests/test_governance_publish.py:26`

### 4.4 Engineering Details and Professionalism
#### 4.4.1 Error handling, logging, validation
- Conclusion: **Partial Pass**
- Rationale: validations are strong and security-sensitive redaction improved, but queue-write retry behavior is not guaranteed for enqueue-time failures and event failures are swallowed.
- Evidence:
  - redacted audit payload in student writes: `repo/backend/services/student.py:19`, `repo/backend/services/student.py:100`
  - trigger enqueue direct DB writes without retry wrapper: `repo/backend/services/notification.py:250`
  - event subscriber exceptions suppressed: `repo/backend/events.py:29`

#### 4.4.2 Product-like quality
- Conclusion: **Partial Pass**
- Rationale: product-like repository with installer, tests, and domain breadth; remaining issues are correctness/policy edges, not scaffolding.
- Evidence:
  - installer artifacts + build scripts: `repo/installer/CRHGC.wxs:57`, `repo/installer/build_msi.ps1:17`
  - test runner fallback: `repo/tests/run_all.py:1`

### 4.5 Prompt Understanding and Requirement Fit
#### 4.5.1 Business goal and constraints fit
- Conclusion: **Partial Pass**
- Rationale: strong fit overall; remaining deviations are publication-governance coupling and ambiguous read-authorization boundaries.
- Evidence:
  - role-based write/import enforcement for students: `repo/backend/services/student.py:74`, `repo/backend/services/student.py:131`
  - hidden employers excluded from default search: `repo/backend/services/search.py:95`
  - publication and semver still not atomically enforced: `repo/backend/services/resource.py:78`, `repo/backend/services/catalog.py:346`

### 4.6 Aesthetics (frontend-only)
#### 4.6.1 Visual/interaction quality
- Conclusion: **Partial Pass**
- Rationale: functional keyboard/tray/context interactions are implemented; detailed visual quality still requires manual runtime inspection.
- Evidence:
  - shortcuts and tray: `repo/frontend/main_window.py:523`, `repo/frontend/main_window.py:564`
  - context menus: `repo/frontend/widgets/results_table.py:41`
  - detachable student profile: `repo/frontend/windows/student_profile.py:9`
- Manual verification note: resolution/scaling and visual coherence need hands-on validation.

## 5. Issues / Suggestions (Severity-Rated)

### High
1. Severity: **High**
- Title: Publication governance remains split; semver increment is not guaranteed on publish
- Conclusion: **Fail**
- Evidence: `repo/backend/services/resource.py:78`, `repo/backend/services/catalog.py:346`, `repo/frontend/main_window.py:291`
- Impact: a resource can be published without semantic version increment in normal workflow, violating prompt publication rule.
- Minimum actionable fix: make a single publish operation that both enforces approval and increments semver atomically; use it in all UI paths.

2. Severity: **High**
- Title: Retry guarantee for local queue-write failures is incomplete at enqueue stage
- Conclusion: **Fail**
- Evidence: trigger handler inserts directly (`repo/backend/services/notification.py:250`) and relies on event bus callback execution where failures are swallowed (`repo/backend/events.py:29`); retry logic only exists for already-queued delivery updates (`repo/backend/services/notification.py:151`).
- Impact: failures while writing queue rows during trigger/scheduled enqueue may be lost without 3x/5-minute retry path.
- Minimum actionable fix: centralize queue writes behind durable enqueue API with the same retry/dead-letter policy and auditable failure state.

3. Severity: **High**
- Title: Read-level authorization boundaries are permissive for sensitive domains
- Conclusion: **Partial Fail (Suspected Risk)**
- Evidence: unguarded compliance reads (`repo/backend/services/compliance.py:12`, `repo/backend/services/compliance.py:44`), unguarded student list/search reads (`repo/backend/services/student.py:46`), tabs visible by default (`repo/frontend/main_window.py:463`, `repo/frontend/main_window.py:467`).
- Impact: non-owner roles may access domain data beyond intended least-privilege scope.
- Minimum actionable fix: define and enforce explicit read permissions per module (students/compliance/resources/reports), and conditionally expose tabs/actions by role.

### Medium
4. Severity: **Medium**
- Title: Documentation/spec drift persists against actual module layout and APIs
- Conclusion: **Partial Fail**
- Evidence: `docs/design.md:69`, `docs/design.md:79`, `docs/api-spec.md:9`.
- Impact: static verification confidence and maintenance handoff quality are reduced.
- Minimum actionable fix: align docs to implemented modules/API signatures and mark deferred items explicitly.

5. Severity: **Medium**
- Title: Governance tests do not assert semver bump coupling on publish
- Conclusion: **Partial Fail**
- Evidence: governance tests check approval gate only (`repo/tests/test_governance_publish.py:26`), no assertion against `resource_catalog.semver` update in publish flow.
- Impact: regressions can reintroduce policy violation while tests still pass.
- Minimum actionable fix: add tests asserting publish updates semver and cannot bypass the combined workflow.

### Low
6. Severity: **Low**
- Title: Compiled cache artifacts are committed
- Conclusion: **Low-quality hygiene issue**
- Evidence: `repo/tests/__pycache__/test_permissions.cpython-312.pyc`, `repo/backend/__pycache__/app.cpython-312.pyc`
- Impact: repository noise and stale-artifact risk.
- Minimum actionable fix: remove caches and enforce ignore rules.

## 6. Security Review Summary
- authentication entry points: **Pass**
  - Evidence: `repo/backend/services/auth.py:25`, `repo/backend/services/auth.py:47`, `repo/backend/services/auth.py:70`.
- route-level authorization: **Not Applicable**
  - Reason: no network routes; in-process desktop services.
- object-level authorization: **Partial Pass**
  - Evidence: student mutators/import now guarded (`repo/backend/services/student.py:74`, `repo/backend/services/student.py:131`); read-side and compliance list-side authorization remain broad (`repo/backend/services/compliance.py:12`, `repo/backend/services/student.py:46`).
- function-level authorization: **Partial Pass**
  - Evidence: broad decorator usage (`repo/backend/services/reporting.py:22`, `repo/backend/services/updater.py:54`, `repo/backend/services/bom.py:187`).
- tenant / user isolation: **Cannot Confirm Statistically**
  - Reason: single-workstation model; no tenant abstraction. Per-user saved searches/checkpoints are scoped (`repo/backend/services/search.py:137`, `repo/backend/services/checkpoint.py:25`).
- admin / internal / debug protection: **Partial Pass**
  - Evidence: admin-only settings/sensitive-word edits (`repo/backend/services/settings.py:15`, `repo/backend/services/compliance_ext.py:164`); no HTTP debug surfaces found.

## 7. Tests and Logging Review
- Unit tests: **Pass**
  - Evidence: `repo/pytest.ini:1`, test modules in `repo/tests/`.
- API/integration tests: **Partial Pass**
  - Evidence: smoke verification script exists (`repo/verify.py:1`) and broad service tests exist; runtime integration still unexecuted.
- Logging categories / observability: **Partial Pass**
  - Evidence: file logging configured (`repo/backend/app.py:24`), hash-chained audit log (`repo/backend/audit.py:15`).
- Sensitive-data leakage risk in logs/responses: **Partial Pass**
  - Evidence: student audit payload redaction in place (`repo/backend/services/student.py:19`); broader module redaction policy is not comprehensively tested.

## 8. Test Coverage Assessment (Static Audit)

### 8.1 Test Overview
- Unit tests and integration-like service tests exist under `repo/tests/`.
- Framework: `pytest` configured (`repo/pytest.ini:1`), plus fallback runner (`repo/tests/run_all.py:1`).
- Documented test commands: `repo/README.md:136`, `repo/README.md:140`.
- Execution was not performed in this audit.

### 8.2 Coverage Mapping Table
| Requirement / Risk Point | Mapped Test Case(s) | Key Assertion / Fixture / Mock | Coverage Assessment | Gap | Minimum Test Addition |
|---|---|---|---|---|---|
| Student write/import authorization | `repo/tests/test_permissions.py:23` | `PermissionDenied` on bare session | sufficient | No explicit export-permission negative test | Add export denial test without `student.import` |
| PII redaction in audit trail | `repo/tests/test_audit_redaction.py:9` | raw payload excludes real email/phone/ssn | sufficient | Only student create path covered | Add update-path and non-student entities |
| Scheduled rules firing + dedup | `repo/tests/test_scheduled_notifications.py:17` | second fire in same minute returns 0 | basically covered | No fault-path retry assertions | Add simulated DB-write failure tests |
| Hidden employer filtering | `repo/tests/test_search_hidden_employers.py:5` | default search excludes throttled | sufficient | No suspend/takedown variant coverage | Add matrix for all action types |
| Updater signature enforcement | `repo/tests/test_updater_signature.py:36` | unsigned rejected by default | sufficient | No positive signed package test fixture | Add known-good signed package acceptance test |
| XLSX import/export | `repo/tests/test_xlsx_io.py:18` | .xlsx round-trip assertions | basically covered | UI path not covered | Add GUI-level file-dialog flow test |
| Publish governance gate | `repo/tests/test_governance_publish.py:26` | `NOT_APPROVED` before review | basically covered | No semver-coupling assertion | Add publish+semver atomicity test |

### 8.3 Security Coverage Audit
- authentication: **Insufficiently covered** (mostly happy path in smoke script; few negative auth tests).
- route authorization: **Not Applicable**.
- object-level authorization: **Basically covered for student writes**; **insufficient for read-level boundaries and compliance listing**.
- tenant/data isolation: **Cannot Confirm** (no tenant model tests).
- admin/internal protection: **Partial** (permissioned methods exist; no exhaustive role-matrix tests).

### 8.4 Final Coverage Judgment
- **Partial Pass**
- Covered well: student write permissions, updater signature default rejection, scheduled-rule basic firing, search hidden-employer behavior, audit redaction, XLSX round-trip.
- Uncovered high-risk area: publish-semver coupling and enqueue-write failure retry guarantees could still regress while tests pass.

## 9. Final Notes
- Rerun outcome improved materially from the previous audit due clear governance/security fixes now in code and tests.
- Remaining priority fixes are concentrated and actionable: unify publish+semver policy as one enforced operation, harden enqueue failure retry semantics, and tighten read-scope authorization by role.
