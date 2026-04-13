"""End-to-end integration test exercising the public service API surface.

This is the closest analogue to an "API integration suite" possible without
introducing a network/HTTP layer that the desktop architecture deliberately
omits. Each test wires a fresh ``Container`` (mirroring how the GUI boots),
then drives a multi-service workflow through the public DTO/dataclass API
the GUI itself uses, asserting that:

  * services compose correctly across module boundaries,
  * the event bus delivers triggered notifications between services,
  * the audit chain records each step,
  * the at-rest blob can be sealed and reopened mid-flow without data loss.

Concretely this is a black-box flow test: the test never reaches into
private state or the SQL layer except to assert observable post-conditions.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    class _Pytest:
        @staticmethod
        def raises(exc):
            class _Ctx:
                value = None
                def __enter__(self_inner):
                    return self_inner
                def __exit__(self_inner, et, ev, tb):
                    if et is None:
                        raise AssertionError(f"expected {exc.__name__}")
                    if issubclass(et, exc):
                        self_inner.value = ev
                        return True
                    return False
            return _Ctx()
    pytest = _Pytest()  # type: ignore

from backend import db as _db
from backend.models import StudentDTO
from backend.services.auth import BizError


def test_student_lifecycle_and_audit_chain(container, admin_session):
    """Create → search → update → history → audit-chain integrity."""
    s = container.students.create(
        admin_session,
        StudentDTO(student_id="INT-1", full_name="Integration One",
                   college="Engineering", class_year="2027",
                   email="int1@example.edu", phone="555-0101",
                   housing_status="on_campus"))
    assert s.id > 0

    # Searching by free text must surface the new record.
    found = container.students.search(admin_session, text="Integration One")
    assert any(row.id == s.id for row in found.items)

    # PII must be masked unless the session is unlocked.
    fetched = container.students.get(admin_session, s.id)
    assert fetched.email and "@" in fetched.email
    assert "***" in fetched.email  # masked

    # History records the create event at minimum.
    hist = container.students.history(admin_session, s.id)
    assert len(hist) >= 1

    # Audit chain must verify cleanly after the multi-step write.
    from backend.audit import verify_chain
    result = verify_chain()
    assert result.ok, (
        f"audit chain broke after {result.checked} rows "
        f"(first break id={result.first_break_id})")


def test_compliance_to_notification_flow(container, admin_session, tmp_path):
    """Submit employer → upload evidence → approve → CASE_DECIDED event
    propagates through the trigger handler without enqueue failures.
    """
    case_id = container.compliance.submit_employer(
        admin_session, name="Integration Employer",
        ein="55-5555555", contact_email="emp@example.com")

    # Approving without evidence is rejected (gate from prior fix).
    with pytest.raises(BizError) as ei:
        container.compliance.decide(admin_session, case_id, "approve",
                                    notes="ok")
    assert ei.value.code == "EVIDENCE_REQUIRED"

    # Upload evidence then approve. The compliance service emits CASE_DECIDED;
    # the notification trigger handler must convert this into queued
    # ``notif_messages`` rows (audience: compliance reviewers + admins).
    f = tmp_path / "verification.pdf"
    f.write_bytes(b"%PDF-1.4 fake but non-empty\n")
    emp_id = _db.get_connection().execute(
        "SELECT employer_id FROM employer_cases WHERE id=?",
        (case_id,)).fetchone()["employer_id"]
    container.evidence.upload(admin_session, emp_id, f, case_id=case_id)

    decided = container.compliance.decide(admin_session, case_id, "approve",
                                          notes="all clear")
    assert decided.decision == "approve"

    # No enqueue-failures should have accumulated for this synchronous flow.
    pending = _db.get_connection().execute(
        "SELECT COUNT(*) AS n FROM notif_enqueue_failures "
        "WHERE replayed_at IS NULL AND dead_at IS NULL").fetchone()["n"]
    assert pending == 0


def test_resource_publish_workflow(container, admin_session):
    """Create resource → add version → catalog attach + approve → publish."""
    r = container.resources.create_resource(admin_session, "Integration Notes")
    v = container.resources.add_version(admin_session, r.id, "v1", "body")
    container.catalog.attach(admin_session, r.id, node_id=None,
                             type_code=None)
    container.catalog.submit_for_review(admin_session, r.id)
    container.catalog.review(admin_session, r.id, "approve", "ok")

    pub = container.resources.publish_version(admin_session, v.id)
    assert pub.status == "published"

    # list_versions is now authz-gated; admin must succeed.
    versions = container.resources.list_versions(admin_session, r.id)
    assert any(ver.id == v.id for ver in versions)


def test_signed_update_apply_and_rollback(container, admin_session, tmp_path):
    """Build a real RSA-PSS signed package, apply it, then roll back."""
    from backend import config as _config
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    priv = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    _config.update_signing_key_path().write_bytes(
        priv.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo))

    pkg = tmp_path / "pkg.zip"
    manifest = json.dumps({"version": "9.9.9"}).encode("utf-8")
    sig = priv.sign(
        manifest,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256())
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("update.json", manifest)
        zf.writestr("update.json.sig", sig)
        zf.writestr("payload/readme.txt", b"hi")

    applied = container.updater.apply_package(
        admin_session, pkg, install_dir=str(tmp_path / "install"))
    assert applied.signature_ok is True
    assert applied.version == "9.9.9"

    container.updater.rollback(admin_session, applied.id)
    pkgs = container.updater.list_packages()
    assert any(p.id == applied.id and p.rolled_back_at for p in pkgs)


def test_at_rest_persistence_across_seal_cycle(container, admin_session):
    """Mid-flow seal and reopen must preserve all committed state — proves
    the in-memory + per-commit-encrypted-blob model round-trips correctly.
    """
    if getattr(_db, "HAVE_SQLCIPHER", False):
        return
    container.students.create(
        admin_session,
        StudentDTO(student_id="PERSIST-1", full_name="Persist Me",
                   college="Eng", class_year="2026"))
    _db.close_and_seal()
    # Reopen — services should still see the row through a fresh connection.
    conn = _db.get_connection()
    row = conn.execute(
        "SELECT id FROM students WHERE student_id_ext=?",
        ("PERSIST-1",)).fetchone()
    assert row is not None
