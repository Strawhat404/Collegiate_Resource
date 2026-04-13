# Recheck Rerun (2026-04-13)

Static-only verification of the previously reported findings. No runtime execution, Docker, or automated test runs were performed.

## Overall
- Fixed: 8
- Improved/Partial: 1
- Not fixed: 0

## Findings Status

1. High - At-rest encryption can degrade after crash/abrupt termination
Status: **Fixed**
Evidence: `repo/backend/db.py:59-63`, `repo/backend/db.py:251-255`, `repo/frontend/main_window.py:807-813`, `repo/frontend/main_window.py:842-847`, `repo/tests/test_security_controls.py:91-110`, `repo/tests/test_security_controls.py:137-175`

2. High - Signed update policy bypassable in UI and shipping key placeholder
Status: **Fixed**
Evidence: `repo/backend/services/updater.py:68-77`, `repo/backend/services/updater.py:102-110`, `repo/backend/services/updater.py:252-253`, `repo/backend/services/updater.py:255-259`, `repo/frontend/tabs_extra.py:502-513`, `repo/installer/CRHGC.wxs:58-64`, `repo/installer/build_msi.ps1:23-44`, `repo/installer/README.md:9-10`

3. High - Function-level authorization gaps on read methods
Status: **Fixed**
Evidence: `repo/backend/services/resource.py:174-176`, `repo/backend/services/compliance_ext.py:122-124`, `repo/backend/services/compliance_ext.py:271-273`

4. Medium - Trigger enqueue failure replay lacks explicit 3-attempt cap
Status: **Fixed**
Evidence: `repo/database/migrations/0007_notif_enqueue_attempts.sql:5-8`, `repo/backend/services/notification.py:241`, `repo/backend/services/notification.py:250-262`, `repo/backend/services/notification.py:284-288`

5. Medium - Compliance sensitive-word gate fail-open on scanner/infrastructure error
Status: **Fixed**
Evidence: `repo/backend/services/compliance.py:115-133`

6. Medium - Documentation materially overclaims implementation
Status: **Fixed (for previously cited claims)**
Evidence: `docs/design.md:54`, `docs/design.md:60`, `docs/design.md:238`, `docs/design.md:261`, `docs/api-spec.md:26-28`, `docs/api-spec.md:82`

7. Medium - Automated coverage missing high-risk controls
Status: **Fixed**
Evidence: `repo/tests/test_security_controls.py:55-86`, `repo/tests/test_security_controls.py:91-110`, `repo/tests/test_security_controls.py:137-175`, `repo/tests/test_security_controls.py:180-203`, `repo/tests/test_security_controls.py:244-275`

8. API / integration tests: previously Fail
Status: **Fixed (suite now present)**
Evidence: `repo/tests/test_integration_flow.py:1-16`, `repo/tests/test_integration_flow.py:48-181`, `repo/tests/test_ui_smoke.py:1-15`, `repo/tests/test_ui_smoke.py:92-115`

9. Unit tests: previously Partial Pass
Status: **Improved (still Partial Pass overall as a maturity judgment)**
Evidence: `repo/tests/test_permissions.py`, `repo/tests/test_governance_publish.py`, `repo/tests/test_updater_signature.py`, `repo/tests/test_scheduled_notifications.py`, `repo/tests/test_security_controls.py`, `repo/tests/test_integration_flow.py`, `repo/tests/test_ui_smoke.py`

## Static Boundary Notes
- This rerun confirms code-path presence and test artifacts statically.
- Runtime behavior, real crash semantics, and packaging pipeline integrity are still **Manual Verification Required** in an operational environment.
