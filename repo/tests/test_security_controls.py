"""Tests for high-risk controls added during the post-audit hardening pass.

Covers:
  * compliance approval evidence gate (EVIDENCE_REQUIRED)
  * compliance sensitive-word gate is fail-CLOSED on scanner failure
  * at-rest sealing leaves only the encrypted blob on disk after shutdown
  * the GUI-side ``allow_unsigned`` override is no longer reachable from the
    update tab (the only call sites pass no override flag)
  * function-level authz on previously-unprotected read methods
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

from backend.services.auth import BizError
from backend.permissions import PermissionDenied


# ---- Compliance: evidence + sensitive-word gates -------------------------

def _make_employer_case(container, session) -> tuple[int, int]:
    case_id = container.compliance.submit_employer(
        session, name="Acme Co", ein="11-1111111",
        contact_email="ops@acme.example")
    # The first row in employer_cases is the onboarding case for this emp.
    from backend import db as _db
    row = _db.get_connection().execute(
        "SELECT id, employer_id FROM employer_cases WHERE id=?",
        (case_id,)).fetchone()
    return row["id"], row["employer_id"]


def test_approve_blocked_without_evidence(container, admin_session, tmp_path):
    case_id, _emp_id = _make_employer_case(container, admin_session)
    with pytest.raises(BizError) as ei:
        container.compliance.decide(admin_session, case_id, "approve",
                                    notes="looks good")
    assert ei.value.code == "EVIDENCE_REQUIRED"


def test_sensitive_word_scanner_failure_is_fail_closed(
        container, admin_session, tmp_path):
    case_id, emp_id = _make_employer_case(container, admin_session)
    # Satisfy the evidence-required precondition.
    f = tmp_path / "verification.txt"
    f.write_bytes(b"verification doc")
    container.evidence.upload(admin_session, emp_id, f, case_id=case_id)

    # Force the scanner to fail; gate must REJECT the approval (fail-closed)
    # rather than silently allow it through (the prior fail-open behaviour).
    from backend.services import compliance_ext

    def _boom(self, text):  # noqa: ARG001
        raise RuntimeError("scanner offline")

    original = compliance_ext.SensitiveWordService.scan
    compliance_ext.SensitiveWordService.scan = _boom
    try:
        with pytest.raises(BizError) as ei:
            container.compliance.decide(admin_session, case_id, "approve",
                                        notes="approve please")
        assert ei.value.code == "SENSITIVE_WORD_SCAN_UNAVAILABLE"
    finally:
        compliance_ext.SensitiveWordService.scan = original


# ---- At-rest sealing -----------------------------------------------------

def test_no_plaintext_db_on_disk_while_running(container, admin_session,
                                                tmp_path):
    """In fallback mode the live DB is in memory; no plaintext file ever
    exists on disk, even mid-session before any explicit seal.
    """
    from backend import db as _db, config as _config
    if getattr(_db, "HAVE_SQLCIPHER", False):
        return  # SQLCipher mode encrypts page-by-page; check is N/A
    # Drive a real write through ``transaction()`` to ensure the at-rest
    # blob has been persisted at least once.
    container.compliance.submit_employer(
        admin_session, name="LiveCheck Co", ein="22-2222222",
        contact_email="x@y.example")

    db_path = _config.db_path()
    enc = db_path.with_suffix(db_path.suffix + ".enc")
    assert enc.is_file(), "encrypted at-rest blob must exist after a commit"
    assert not db_path.exists(), (
        "fallback mode must NEVER materialise a plaintext SQLite file")


def test_close_and_seal_round_trip(container, admin_session, tmp_path):
    """``close_and_seal`` leaves only the encrypted blob, and reopening
    the DB deserializes the same data.
    """
    from backend import db as _db, config as _config
    if getattr(_db, "HAVE_SQLCIPHER", False):
        return
    case_id = container.compliance.submit_employer(
        admin_session, name="RoundTrip Co", ein="33-3333333",
        contact_email="rt@example.com")

    _db.close_and_seal()

    db_path = _config.db_path()
    enc = db_path.with_suffix(db_path.suffix + ".enc")
    assert enc.is_file()
    assert not db_path.exists()
    # Reopen — deserializes from the encrypted blob — and the prior write
    # must still be visible.
    conn = _db.get_connection()
    row = conn.execute(
        "SELECT id FROM employer_cases WHERE id=?", (case_id,)).fetchone()
    assert row is not None


def test_abrupt_kill_leaves_only_encrypted_blob(container, admin_session,
                                                 tmp_path):
    """Simulate SIGKILL: drop the connection without calling
    ``close_and_seal`` and confirm that the on-disk state is still only
    the encrypted blob — no plaintext SQLite file, no WAL/SHM sidecars.
    """
    from backend import db as _db, config as _config
    if getattr(_db, "HAVE_SQLCIPHER", False):
        return
    # Two committed writes; per-commit reseal must keep the encrypted blob
    # current after each one.
    container.compliance.submit_employer(
        admin_session, name="Kill1 Co", ein="44-4444441",
        contact_email="k1@example.com")
    container.compliance.submit_employer(
        admin_session, name="Kill2 Co", ein="44-4444442",
        contact_email="k2@example.com")

    # Simulate abrupt termination: forget the connection without sealing.
    # We do NOT close it (close would flush). atexit handlers will run
    # on real interpreter exit, but inside a test we just clear the global.
    _db._CONN = None  # type: ignore[attr-defined]

    db_path = _config.db_path()
    enc = db_path.with_suffix(db_path.suffix + ".enc")
    wal = db_path.with_suffix(db_path.suffix + "-wal")
    shm = db_path.with_suffix(db_path.suffix + "-shm")
    assert enc.is_file(), "per-commit persist must keep blob current"
    assert not db_path.exists(), "no plaintext DB file must exist"
    assert not wal.exists(), "no WAL sidecar must exist (in-memory mode)"
    assert not shm.exists(), "no SHM sidecar must exist (in-memory mode)"

    # And the latest committed state must be recoverable from the blob
    # alone, proving per-commit persistence actually ran.
    conn = _db.get_connection()
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM employers WHERE name LIKE 'Kill%'"
    ).fetchone()["n"]
    assert n == 2


# ---- Updater: the UI bypass for unsigned packages is removed -------------

def test_update_tab_does_not_pass_allow_unsigned():
    """The hardened update tab must NEVER call ``apply_package`` with
    ``allow_unsigned=True``. Inspect the source so a future regression that
    re-introduces the bypass dialog is caught even without a Qt runtime.
    """
    src = (Path(__file__).resolve().parent.parent
           / "frontend" / "tabs_extra.py").read_text(encoding="utf-8")
    # Strip comments so the policy reminder in the source doesn't trip the
    # check; only an actual call-site argument should fail this assertion.
    code_only = "\n".join(
        line.split("#", 1)[0] for line in src.splitlines())
    assert "allow_unsigned=True" not in code_only, (
        "update tab re-introduced the unsigned-override bypass")


def test_unsigned_package_still_rejected_at_backend(
        container, admin_session, tmp_path):
    pkg = tmp_path / "u.zip"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("update.json", json.dumps({"version": "9.9.9"}))
        zf.writestr("payload/note.txt", b"hi")
    with pytest.raises(BizError) as ei:
        container.updater.apply_package(admin_session, pkg)
    assert ei.value.code == "SIGNATURE_REQUIRED"


# ---- Function-level authz on read methods --------------------------------

def test_evidence_verify_requires_session(container, admin_session, tmp_path):
    case_id, emp_id = _make_employer_case(container, admin_session)
    f = tmp_path / "doc.txt"
    f.write_bytes(b"data")
    ev = container.evidence.upload(admin_session, emp_id, f, case_id=case_id)
    # No session => denied.
    with pytest.raises(PermissionDenied):
        container.evidence.verify(ev.id)
    # With an authorised session => allowed.
    assert container.evidence.verify(ev.id, session=admin_session) is True


def test_violation_list_for_employer_requires_session(
        container, admin_session):
    _case_id, emp_id = _make_employer_case(container, admin_session)
    with pytest.raises(PermissionDenied):
        container.violations.list_for_employer(emp_id)
    # Authorised call is fine (returns empty list when no actions exist).
    out = container.violations.list_for_employer(
        emp_id, session=admin_session)
    assert out == []


def test_resource_list_versions_requires_read_permission(
        container, admin_session, coordinator_session):
    r = container.resources.create_resource(admin_session, "Trig Notes")
    container.resources.add_version(admin_session, r.id, "v1", "body")
    # Admin can read (has resource.write etc.).
    assert container.resources.list_versions(admin_session, r.id)
    # Housing coordinator does not hold any resource/* perm.
    with pytest.raises(PermissionDenied):
        container.resources.list_versions(coordinator_session, r.id)


# ---- Notification 3-attempt cap on enqueue replay -----------------------

def test_enqueue_failure_replay_caps_at_three_attempts(
        container, admin_session):
    """A persistently-broken enqueue-failure row must be marked dead after
    exactly 3 replay attempts, not retried forever.
    """
    from backend import db as _db
    svc = container.notifications

    # Seed a failure row pointing at a non-existent template so every replay
    # INSERT fails the foreign-key check in notif_messages.
    with _db.transaction() as conn:
        conn.execute(
            """INSERT INTO notif_enqueue_failures(event_name, template_id,
                  audience_user_id, subject, body_rendered, error,
                  created_at)
               VALUES ('test.event', 999999, 1, 'subj', 'body', 'seed',
                       datetime('now', '-1 hour'))""")
        fid = conn.execute(
            "SELECT last_insert_rowid() AS id").fetchone()["id"]

    # Drive the replay loop more than the cap; force=True bypasses the
    # 5-minute cutoff so each call is an attempt.
    for _ in range(6):
        svc.retry_failed(admin_session)

    row = _db.get_connection().execute(
        "SELECT attempts, dead_at FROM notif_enqueue_failures WHERE id=?",
        (fid,)).fetchone()
    assert row["attempts"] == 3, (
        f"replay must cap at 3 attempts, got {row['attempts']}")
    assert row["dead_at"] is not None, (
        "row must be marked dead once the cap is exhausted")


# ---- Updater placeholder/example pubkey rejection -----------------------

def test_updater_refuses_placeholder_public_key(
        container, admin_session, tmp_path):
    """A PEM containing the placeholder/example markers must be rejected
    by the verifier (signed_by reports ``placeholder-pubkey``).
    """
    import json as _json
    import zipfile as _zip
    from backend import config as _config
    from backend.services.auth import BizError

    pk_path = _config.update_signing_key_path()
    pk_path.parent.mkdir(parents=True, exist_ok=True)
    pk_path.write_bytes(
        b"-----BEGIN PUBLIC KEY-----\n"
        b"PLACEHOLDER - operator must replace before shipping\n"
        b"-----END PUBLIC KEY-----\n")

    pkg = tmp_path / "u.zip"
    with _zip.ZipFile(pkg, "w") as zf:
        zf.writestr("update.json", _json.dumps({"version": "1.0"}))
        zf.writestr("update.json.sig", b"\x00" * 16)
        zf.writestr("payload/x.txt", b"x")

    with pytest.raises(BizError) as ei:
        container.updater.apply_package(admin_session, pkg)
    assert ei.value.code == "SIGNATURE_REQUIRED"
    assert "placeholder" in str(ei.value).lower()


# ---- At-rest envelope decrypt error path --------------------------------

def test_corrupt_at_rest_blob_does_not_crash_startup(container, admin_session):
    """A corrupted on-disk encrypted blob must not crash the next startup;
    the app should fall back to an empty in-memory DB and re-run migrations.
    """
    from backend import db as _db, config as _config
    if getattr(_db, "HAVE_SQLCIPHER", False):
        return

    # Force a known-good blob to exist so we can corrupt it.
    container.compliance.submit_employer(
        admin_session, name="WillBeLost Co", ein="66-6666666",
        contact_email="x@y.example")
    _db.close_and_seal()
    enc = _config.db_path().with_suffix(_config.db_path().suffix + ".enc")
    assert enc.is_file()

    # Corrupt the blob: keep the AES-GCM magic prefix so the decrypt path is
    # exercised, but mangle the ciphertext so AESGCM raises InvalidTag.
    raw = bytearray(enc.read_bytes())
    for i in range(20, min(80, len(raw))):
        raw[i] ^= 0xFF
    enc.write_bytes(bytes(raw))

    # Reopen — must NOT raise. The new connection should have a fresh
    # empty schema (migrations re-applied), proving graceful degradation.
    conn = _db.get_connection()
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM employers").fetchone()["n"]
    assert n == 0  # the corrupted history was lost, but the app survived
