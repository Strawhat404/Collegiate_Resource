# Reinspection Report (2026-04-13)

Scope: Re-verify the five findings from the prior inspection against current code and tests.

## 1) Publication governance remains split; semver increment is not guaranteed on publish
- Status: **Fixed**
- Evidence:
  - `ResourceService.publish_version` now enforces catalog approval and performs version publish + `resource_catalog.semver` update in one transaction: `repo/backend/services/resource.py:77-115`.
  - `CatalogService.publish_with_semver` delegates to `ResourceService.publish_version` (no separate split path): `repo/backend/services/catalog.py:345-375`.
  - UI publish action still calls `publish_version`, which is now the governed/atomic path: `repo/frontend/main_window.py:282-292`.
  - Coverage exists for coupling and delegation: `repo/tests/test_governance_publish.py:63-105`.

## 2) Retry guarantee for local queue-write failures is incomplete at enqueue stage
- Status: **Fixed (with residual test gap)**
- Evidence:
  - Trigger handler path now routes writes through `_insert_with_retry(...)` with bounded retries and dead-letter persistence to `notif_enqueue_failures`: `repo/backend/services/notification.py:257-299`, `repo/backend/services/notification.py:324-326`.
  - Event bus still swallows subscriber exceptions, but handler now owns retries/persistence so failures are not silently dropped at enqueue stage: `repo/backend/events.py:28-35`.
  - Operator replay exists for enqueue failures via `retry_failed`: `repo/backend/services/notification.py:215-244`.
- Residual gap:
  - No explicit test found for enqueue-failure replay path (`notif_enqueue_failures` -> `retry_failed` replay). Current notification tests focus on cron firing/dedup: `repo/tests/test_scheduled_notifications.py:17-38`.

## 3) Read-level authorization boundaries are permissive for sensitive domains
- Status: **Fixed**
- Evidence:
  - Compliance read methods now enforce explicit read gate via `_require_compliance_read`: `repo/backend/services/compliance.py:10-25`, `repo/backend/services/compliance.py:56-59`.
  - Student search now checks `_SEARCH_PERMS` and denies otherwise: `repo/backend/services/student.py:46-55`.
  - Sensitive tabs are permission-gated before being added to the main UI: `repo/frontend/main_window.py:493-537`.

## 4) Documentation/spec drift persists against actual module layout and APIs
- Status: **Partial Fail (still present)**
- Evidence:
  - `docs/design.md` data model says `resource_versions.body_path`: `docs/design.md:121`, but schema/service use `body`: `repo/database/migrations/0001_initial.sql:117`, `repo/backend/services/resource.py:69`.
  - `docs/api-spec.md` defines pagination conventions (`Page`, `Paged[T]`) and query-style signatures (e.g., Student search): `docs/api-spec.md:10`, `docs/api-spec.md:35`; implementation uses `limit/offset` and returns lists: `repo/backend/services/student.py:49-52`, `repo/backend/services/student.py:73-76`.

## 5) Governance tests do not assert semver bump coupling on publish
- Status: **Fixed**
- Evidence:
  - Dedicated tests now assert semver changes on publish and delegated publish path correctness: `repo/tests/test_governance_publish.py:63-105`.

## Verification execution
- Ran built-in test runner (pytest unavailable in environment): `cd repo && python3 tests/run_all.py`
- Result: **19/19 tests passed**.
