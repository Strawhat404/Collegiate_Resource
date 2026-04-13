# Internal API Specification

The CRHGC application has no network surface; "API" here refers to the in-process service layer that the PyQt UI consumes. Each service is a Python class exposed via the `backend.services` package. All methods are synchronous unless noted; long-running operations dispatch to a `QThreadPool` worker and return a `Future`-like handle.

All methods accept and return plain dataclasses (`backend.models`) — never raw rows. All write operations require an authenticated `Session` object that carries the user, the active roles, and the masked-field unlock expiry.

## Conventions

- **Errors** are raised as `BizError(code, message)` where `code` is a stable string (e.g., `STUDENT_DUPLICATE_ID`) and `message` is human-readable. The exception object also exposes `.code` and `.message` attributes.
- **Pagination** uses `Page(limit:int=50, offset:int=0)`; results return `Paged[T](items, total)`.
- **Permissions** are checked via `@requires("permission.code")` decorators and surfaced as `PermissionDenied`.
- **Audit** is automatic for any decorated mutator; pass `reason=` for sensitive operations.

---

## 1. AuthService

| Method | Signature | Notes |
|---|---|---|
| `login` | `(username, password) -> Session` | Returns `Session` or raises `AUTH_INVALID`. |
| `logout` | `(session) -> None` | Invalidates session; clears mask unlock. |
| `change_password` | `(session, old, new) -> None` | Validates strength (≥10 chars, mixed). |
| `unlock_masked_fields` | `(session, password) -> datetime` | Returns expiry (now+5m). |
| `bootstrap_admin` | `(username, password, full_name) -> User` | Permitted only when no users exist. |

## 2. UserService

**Planned, not yet implemented.** The intended surface is `list_users(session) -> list[User]`, `create_user(session, dto)`, `update_user(session, id, dto)`, `assign_roles(session, user_id, roles)`, `disable_user(session, user_id)`. Until it lands, user provisioning is performed by `AuthService.bootstrap_admin` plus direct DB seeding (see `database/seed/seed_dev.sql`).

## 3. StudentService

| Method | Notes |
|---|---|
| `get(session, student_id) -> Student` | Masked PII unless unlocked. |
| `search(session, query: StudentQuery, page) -> Paged[StudentSummary]` | Supports `text`, `college`, `class_year`, `housing_status`, `fuzzy=True`. |
| `create(session, dto) -> Student` | Audited; emits `STUDENT_CREATED` event. |
| `update(session, student_id, dto) -> Student` | Audited; emits `STUDENT_UPDATED`. |
| `import_csv(session, path, options: ImportOptions) -> ImportPreview` | Dry-run by default. |
| `commit_import(session, preview_id) -> ImportResult` | Applies a previously-previewed import. |
| `export_csv(session, query, path) -> int` | Returns row count. |
| `history(session, student_id) -> list[ChangeLogEntry]` | Immutable. |

## 4. HousingService

`list_buildings(session)`, `list_beds(session, building_id=None, vacant_only=False)`, `assign_bed(session, student_id, bed_id, effective_date, reason)`, `vacate_bed(session, assignment_id, effective_date, reason)`, `transfer(session, student_id, new_bed_id, effective_date, reason)`, `assignment_history(session, *, student_id=None, bed_id=None) -> list[BedAssignment]`.

Events: `BED_ASSIGNED`, `BED_VACATED`, `BED_TRANSFERRED`.

## 5. ResourceService

`list_categories(session)`, `search(session, query, page)`, `create_resource(session, dto)`, `add_version(session, resource_id, dto, file_bytes=None)`, `publish_version(session, version_id, semver_level: 'major'|'minor'|'patch'='minor')`, `unpublish_version(session, version_id)`, `place_on_hold(session, resource_id, reason)`, `release_hold(session, resource_id)`, `version_diff(session, v1, v2)`.

For catalog-attached resources, `publish_version` atomically (in one transaction) marks the version published AND increments `resource_catalog.semver` by `semver_level`. `CatalogService.publish_with_semver` delegates to this method — there is no path that bumps semver without publishing or vice versa.

Events: `RESOURCE_PUBLISHED`, `RESOURCE_UNPUBLISHED`, `RESOURCE_HELD`.

## 6. EmployerComplianceService

`list_cases(session, filters, page)`, `get_case(session, case_id)`, `submit_case(session, dto)`, `assign_reviewer(session, case_id, user_id)`, `decide(session, case_id, decision: 'approve'|'reject', notes)`, `open_violation(session, employer_id, dto)`, `resolve_violation(session, violation_id, notes)`.

Events: `CASE_SUBMITTED`, `CASE_DECIDED`, `VIOLATION_OPENED`, `VIOLATION_RESOLVED`.

## 7. SearchService (universal search; powers Ctrl+K)

`global_search(session, query: str, types: set[str]|None=None, fuzzy: bool=True, limit:int=20) -> list[SearchHit]`. Hits include `entity_type`, `entity_id`, `title`, `subtitle`, `score`, and `open_action` (a stable command name).

`save_search(session, name, query)`, `list_saved(session)`, `delete_saved(session, id)`, `pin(session, id, pinned: bool)`.

## 8. NotificationService

`templates_list/create/update/delete` (admin only).
`rules_list/create/update/delete` — rule = `{trigger | schedule, template_id, audience_query}`.
`enqueue(session, message: NotificationDraft)` — direct send.
`inbox(session, user_id=None, page)` — delivery receipts.
`mark_read(session, message_id)`.
`retry_failed(session)` — manual replay of dead-letter items.

The dispatcher worker drains the queue, retrying up to 3 times at 5-minute intervals on local-write failure.

## 9. ReportingService

`occupancy(session, as_of: date)`, `move_trends(session, days:int=30)`, `resource_velocity(session, days:int=30)`, `compliance_sla(session, days:int=30)`, `notification_delivery(session, days:int=7)`. Each returns a `Report` with `columns`, `rows`, and `summary`. `export(session, report, fmt: 'csv'|'xlsx', path)` — `'pdf'` is reserved for a planned ReportLab-based export and currently raises `ValueError`.

## 10. AuditService

`tail(session, page)`, `for_entity(session, entity_type, entity_id)`, `verify_chain(session) -> ChainVerification` (re-walks the SHA-256 chain; reports any breaks).

## 11. SettingsService

`get(key) -> str|None`, `set(session, key, value)`, `list_synonyms(session)`, `add_synonym(session, term, alts:list[str])`, `remove_synonym(session, id)`.

---

## Event Bus

`backend.events.bus` is a small in-process pub/sub. Publishers call `bus.publish(EventName, payload)`; subscribers (notification rule matcher, audit shadow, UI status updates) attach via `bus.subscribe(EventName, callback)`. Subscriptions execute synchronously on the publisher's thread; UI subscribers must marshal back to the GUI thread via `QMetaObject.invokeMethod`.

## Permissions Reference

| Code | Roles |
|---|---|
| `system.admin` | System Administrator |
| `housing.write` | Housing Coordinator, System Administrator |
| `student.pii.read` | Housing Coordinator, System Administrator (after unlock) |
| `resource.write` | Academic Admin |
| `resource.publish` | Academic Admin |
| `compliance.review` | Compliance Reviewer |
| `compliance.violation` | Compliance Reviewer |
| `report.read` | Operations Analyst, System Administrator |
| `report.export` | Operations Analyst, System Administrator |
| `notification.admin` | System Administrator |

## Data Transfer Objects (excerpt)

```python
@dataclass
class StudentDTO:
    student_id: str        # external ID, e.g. "S2026-00421"
    full_name: str
    college: str
    class_year: int
    email: str | None
    phone: str | None
    ssn_last4: str | None  # optional, masked
    housing_status: str    # 'on_campus'|'off_campus'|'pending'
```

```python
@dataclass
class BedAssignmentDTO:
    student_id: str
    bed_id: int
    effective_date: date
    reason: str
```

```python
@dataclass
class NotificationDraft:
    template_id: int
    audience_user_ids: list[int]
    variables: dict[str, str]
    scheduled_for: datetime | None = None
```

## 12. CatalogService

`list_tree()`, `create_node(session, name, parent_id=None)`, `rename_node(session, node_id, new_name)`, `delete_node(session, node_id)`, `list_types()`, `get_type(code)`, `upsert_type(session, code, name, description, fields=[{code,label,field_type,regex,required,enum_values}])`, `attach(session, resource_id, *, node_id, type_code, subject=None, grade=None, course=None, metadata={}, tags=[])`, `get_metadata(resource_id)`, `get_attachment(resource_id)`, `list_tags(resource_id)`, `relate(session, src_id, dst_id, relation)`, `submit_for_review(session, resource_id)`, `review(session, resource_id, decision: 'approve'|'reject', notes)`, `publish_with_semver(session, resource_id, level: 'major'|'minor'|'patch') -> str`.

Events: `CATALOG_SUBMITTED`, `CATALOG_REVIEWED`. Permissions: `catalog.write`, `catalog.review`, `catalog.publish`.

## 13. EvidenceService / SensitiveWordService / ViolationActionService

`EvidenceService.upload(session, employer_id, source_path, case_id=None) -> EvidenceFile`, `list_for_employer(employer_id)`, `verify(evidence_id) -> bool`, `purge_expired(session) -> int`. Files stored under `evidence/<employer_id>/<sha-prefix>/<uuid>__<name>` with `retain_until = uploaded_at + 7 years`.

`SensitiveWordService.list()`, `add(session, word, severity, category=None)`, `remove(session, id)`, `scan(text) -> list[{word, severity, category, position}]`.

`ViolationActionService.takedown(session, employer_id, reason)`, `suspend(session, employer_id, days∈{30,60,180}, reason)`, `throttle(session, employer_id, reason)`, `revoke(session, action_id, reason)`, `list_for_employer(employer_id, active_only=False)`, `is_hidden_from_default_search(employer_id) -> bool`.

Events: `VIOLATION_TAKEDOWN`, `VIOLATION_SUSPEND`, `VIOLATION_THROTTLE`. Permissions: `compliance.evidence`, `compliance.action`.

## 14. BomService

`create_style(session, style_code, name, description="")`, `get_style(id)`, `list_styles()`, `list_versions(style_id)`, `get_version(id)`, `add_bom_item(session, version_id, *, component_code, description, quantity, unit_cost_usd)`, `add_routing_step(session, version_id, *, operation, machine, setup_minutes, run_minutes, rate_per_hour_usd)`, `list_bom(version_id)`, `list_routing(version_id)`, `submit_for_approval(session, version_id)`, `first_approve(session, version_id)`, `final_approve(session, version_id)`, `reject(session, version_id, reason)`, `open_change_request(session, style_id, base_version_id, reason) -> int`, `list_change_requests(style_id=None)`, `compute_cost(version_id) -> float`.

Cost rule: `Σ qty·unit_cost + Σ ((setup+run)/60·rate)` USD; recomputed on every BOM/routing edit; logged to audit only on change. Final approver must differ from first; released versions are immutable. Permissions: `bom.write`, `bom.approve.first`, `bom.approve.final`.

## 15. CheckpointService

`save_workspace(session, payload)`, `load_workspace(session) -> dict|None`, `save_draft(session, draft_key, payload)`, `load_draft(session, draft_key) -> dict|None`, `list_drafts(session)`, `discard_draft(session, draft_key)`, `discard_all(session) -> int`. The UI calls `save_workspace` on a 60-second `QTimer` and offers draft recovery on next launch.

## 16. UpdaterService

`apply_package(session, zip_path, install_dir=None) -> UpdatePackage` — verifies the RSA-PSS signature in `update.json.sig` against `update_pubkey.pem`, snapshots the DB, then copies `payload/*` into the install directory. `rollback(session, package_id)` restores the pre-apply snapshot (after taking a *pre-rollback* snapshot for reversibility). `list_packages()`, `get(package_id)`. Permission: `update.apply`.

Update package layout:

```
update.zip
├── update.json        # {"version": "1.2.0", "files": [...], "notes": "..."}
├── update.json.sig    # RSA-PSS signature over update.json (raw bytes)
└── payload/...        # files copied into the install dir
```
