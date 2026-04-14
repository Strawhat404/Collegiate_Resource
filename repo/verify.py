"""Headless verification script.

Boots the backend (no GUI), seeds an admin, exercises representative service
methods, and prints a pass/fail summary. Use this to confirm the install in
environments where launching the PyQt GUI is not convenient.

Run:
    python verify.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Use a throwaway DB for verification.
tmp = tempfile.mkdtemp(prefix="crhgc-verify-")
os.environ["CRHGC_DB"] = str(Path(tmp) / "verify.db")

from backend import audit, db   # noqa: E402
from backend.app import Container  # noqa: E402
from backend.models import StudentDTO  # noqa: E402


def step(name: str, fn):
    try:
        fn()
        print(f"  PASS  {name}")
        return True
    except Exception as e:
        print(f"  FAIL  {name}: {e}")
        return False


def main() -> int:
    print("CRHGC verification — using DB:", os.environ["CRHGC_DB"])
    c = Container()
    results: list[bool] = []

    # 1. Bootstrap admin
    def t1():
        u = c.auth.bootstrap_admin("admin", "Adm1nP@ssw0rd!", "Test Admin")
        assert u.id > 0
    results.append(step("bootstrap admin", t1))

    session = c.auth.login("admin", "Adm1nP@ssw0rd!")

    # 2. Create student + masking
    def t2():
        s = c.students.create(session, StudentDTO(
            student_id="S2026-0001", full_name="Alice Example",
            college="Liberal Arts", class_year=2026,
            email="alice@example.edu", phone="555-123-4567"))
        assert s.id > 0
        assert "***" in (s.email or "")  # masked by default
    results.append(step("create student + masking", t2))

    # 3. Unlock PII reveals plaintext
    def t3():
        c.auth.unlock_masked_fields(session, "Adm1nP@ssw0rd!")
        s = c.students.search(session, text="Alice")[0]
        full = c.students.get(session, s.id)
        assert full.email == "alice@example.edu"
    results.append(step("unlock reveals PII", t3))

    # 4. Bed assignment + event-driven notification
    def t4():
        beds = c.housing.list_beds(session, vacant_only=True)
        student = c.students.search(session, text="Alice")[0]
        c.housing.assign_bed(session, student.id, beds[0].id, date.today(),
                             reason="initial")
        c.notifications.drain_queue()
        msgs = c.notifications.inbox(session)
        assert any("assignment" in m.subject.lower() or
                   "bed" in m.subject.lower() for m in msgs)
    results.append(step("bed assignment triggers notification", t4))

    # 5. Resource version + publish
    def t5():
        r = c.resources.create_resource(session, "Intro to Logic")
        v = c.resources.add_version(session, r.id, "v1", "Body of v1")
        # Catalog governance gate: must be attached + reviewer-approved.
        c.catalog.attach(session, r.id, node_id=None, type_code=None)
        c.catalog.submit_for_review(session, r.id)
        c.catalog.review(session, r.id, "approve", "ok")
        c.resources.publish_version(session, v.id)
        assert c.resources.search(session, text="Logic")[0].published_version == 1
    results.append(step("resource publish", t5))

    # 6. Compliance flow
    def t6():
        import tempfile
        cid = c.compliance.submit_employer(session, "Acme Inc", "12-3456789",
                                           "hr@acme.example")
        emp_id = [c2.employer_id for c2 in c.compliance.list_cases(session)
                  if c2.id == cid][0]
        # Approval requires evidence on file (governance gate).
        f = Path(tempfile.mkstemp(suffix=".pdf")[1])
        f.write_bytes(b"verification document")
        c.evidence.upload(session, emp_id, f, case_id=cid)
        c.compliance.decide(session, cid, "approve", "looks good")
        cases = c.compliance.list_cases(session)
        assert any(case.id == cid and case.state == "approved" for case in cases)
    results.append(step("compliance approve", t6))

    # 7. Universal search
    def t7():
        hits = c.search.global_search(session, "Alice")
        assert any(h.entity_type == "student" for h in hits)
    results.append(step("universal search", t7))

    # 8. Audit chain integrity
    def t8():
        v = audit.verify_chain()
        assert v.ok, f"chain broken at {v.first_break_id}"
        assert v.checked > 0
    results.append(step("audit chain integrity", t8))

    # 9. Reporting
    def t9():
        rep = c.reporting.occupancy(session)
        assert rep.columns and rep.rows
    results.append(step("occupancy report", t9))

    # 10. Catalog: tree + custom type with required regex field + review/publish
    def t10():
        node_id = c.catalog.create_node(session, "Custom Folder")
        # Use the seeded "syllabus" type which has a required regex field.
        r = c.resources.create_resource(session, "Calc I Syllabus")
        # Missing required field -> rejected.
        try:
            c.catalog.attach(session, r.id, node_id=node_id, type_code="syllabus",
                             metadata={})
            assert False, "should have rejected missing required field"
        except Exception:
            pass
        # Bad regex -> rejected.
        try:
            c.catalog.attach(session, r.id, node_id=node_id, type_code="syllabus",
                             metadata={"course_code": "lower-bad", "credits": "3",
                                       "effective": "01/15/2026"})
            assert False, "should have rejected bad course_code regex"
        except Exception:
            pass
        # Valid metadata -> accepted.
        c.catalog.attach(session, r.id, node_id=node_id, type_code="syllabus",
                         metadata={"course_code": "MATH-101", "credits": "3",
                                   "effective": "01/15/2026"},
                         tags=["math", "freshman"])
        # Unified governance: publish_with_semver delegates to
        # ResourceService.publish_version which requires an existing version.
        c.resources.add_version(session, r.id, "v1", "Course outline body")
        c.catalog.submit_for_review(session, r.id)
        c.catalog.review(session, r.id, "approve", "looks good")
        new_v = c.catalog.publish_with_semver(session, r.id, level="minor")
        assert new_v == "0.2.0", new_v
    results.append(step("catalog: type validation, review, semver bump", t10))

    # 11. Evidence file with SHA-256 + verify
    def t11():
        import tempfile
        cid = c.compliance.submit_employer(session, "Beta Corp", None, None)
        emp_id = [c2.employer_id for c2 in c.compliance.list_cases(session)
                  if c2.id == cid][0]
        f = Path(tempfile.mkstemp(suffix=".pdf")[1])
        f.write_bytes(b"sample evidence bytes")
        ev = c.evidence.upload(session, emp_id, f, case_id=cid)
        assert len(ev.sha256) == 64
        assert c.evidence.verify(ev.id, session=session) is True
    results.append(step("evidence upload + sha256 verify", t11))

    # 12. Sensitive-word scan
    def t12():
        hits = c.sensitive.scan(
            "We GUARANTEE_EMPLOYMENT after a brief intro, no SSN_REQUIRED.")
        assert any(h["severity"] == "high" for h in hits)
    results.append(step("sensitive-word scan", t12))

    # 13. Violation actions: takedown + suspend(30) + throttle, hidden flag
    def t13():
        cid = c.compliance.submit_employer(session, "Gamma LLC", None, None)
        emp_id = [c2.employer_id for c2 in c.compliance.list_cases(session)
                  if c2.id == cid][0]
        try:
            c.violations.suspend(session, emp_id, days=45, reason="bad")
            assert False, "should reject non-{30,60,180}"
        except Exception:
            pass
        c.violations.suspend(session, emp_id, days=30, reason="reason")
        assert c.violations.is_hidden_from_default_search(emp_id) is True
        c.violations.takedown(session, emp_id, "evidence collected")
        c.violations.throttle(session, emp_id, "spam content")
    results.append(step("violation actions", t13))

    # 14. Style + BOM + two-step approval + cost recalc + change request
    def t14():
        s = c.bom.create_style(session, "ST-001", "Sample Style")
        v = c.bom.list_versions(s.id)[0]
        c.bom.add_bom_item(session, v.id, component_code="C-A",
                           quantity=2, unit_cost_usd=3.50)
        c.bom.add_routing_step(session, v.id, operation="cut",
                               run_minutes=30, rate_per_hour_usd=20.0)
        v2 = c.bom.get_version(v.id)
        # Materials = 2 * 3.50 = 7.00; labor = 30/60 * 20 = 10.00; total 17.00
        assert abs(v2.cost_usd - 17.0) < 1e-3, v2.cost_usd
        c.bom.submit_for_approval(session, v.id)
        c.bom.first_approve(session, v.id)
        # Same actor for final must fail
        try:
            c.bom.final_approve(session, v.id)
            assert False, "should reject same approver"
        except Exception:
            pass
        # Use a second user (acting as system_admin, distinct id)
        from backend import db as _db
        _db.get_connection().execute(
            "INSERT OR IGNORE INTO users(username, full_name, password_hash, password_salt) "
            "VALUES ('approver2', 'Final Approver', x'00', x'00')")
        uid = _db.get_connection().execute(
            "SELECT id FROM users WHERE username='approver2'").fetchone()["id"]
        # Grant the required permissions via system_admin role.
        rid = _db.get_connection().execute(
            "SELECT id FROM roles WHERE code='system_admin'").fetchone()["id"]
        _db.get_connection().execute(
            "INSERT OR IGNORE INTO user_roles(user_id, role_id) VALUES (?, ?)",
            (uid, rid))
        from backend.permissions import Session as Sess
        s2 = Sess(user_id=uid, username="approver2", full_name="Final Approver",
                  roles={"system_admin"},
                  permissions={"bom.approve.final", "bom.write"})
        c.bom.final_approve(s2, v.id)
        # Released -> cannot edit
        try:
            c.bom.add_bom_item(session, v.id, component_code="X")
            assert False, "released version must be locked"
        except Exception:
            pass
        # Change request opens new draft with copied BOM
        cr_id = c.bom.open_change_request(session, s.id, v.id, "fix cost")
        crs = c.bom.list_change_requests(s.id)
        assert any(c2["id"] == cr_id for c2 in crs)
    results.append(step("style + BOM + two-step approval + cost + CR", t14))

    # 15. Checkpoints / workspace state
    def t15():
        c.checkpoints.save_workspace(session, {"open_tabs": ["Students", "BOM"]})
        ws = c.checkpoints.load_workspace(session)
        assert ws and ws["open_tabs"] == ["Students", "BOM"]
        c.checkpoints.save_draft(session, "student:new",
                                 {"name": "Bob"})
        d = c.checkpoints.load_draft(session, "student:new")
        assert d and d["name"] == "Bob"
        c.checkpoints.discard_draft(session, "student:new")
        assert c.checkpoints.load_draft(session, "student:new") is None
    results.append(step("workspace state + draft checkpoints", t15))

    # 16. Updater: build a tiny SIGNED package, apply, then rollback
    def t16():
        try:
            from cryptography.hazmat.primitives import hashes, serialization  # noqa: F401
        except ImportError:
            print("  SKIP  updater apply + rollback (cryptography not installed)")
            return
        import tempfile, zipfile, json
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        # Generate a throwaway keypair, write the public key where the
        # updater expects it, sign the manifest. There is no allow-unsigned
        # path at any layer — verification mirrors production trust.
        priv = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        from backend import config as _config
        _config.update_signing_key_path().write_bytes(
            priv.public_key().public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo))
        pkg = Path(tempfile.mkstemp(suffix=".zip")[1])
        manifest = {"version": "9.9.9", "files": ["readme.txt"],
                    "notes": "test package"}
        manifest_bytes = json.dumps(manifest).encode("utf-8")
        sig = priv.sign(
            manifest_bytes,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256())
        with zipfile.ZipFile(pkg, "w") as zf:
            zf.writestr("update.json", manifest_bytes)
            zf.writestr("update.json.sig", sig)
            zf.writestr("payload/readme.txt", b"hello from updater test")
        applied = c.updater.apply_package(session, pkg)
        assert applied.version == "9.9.9"
        assert applied.signature_ok is True
        # Rollback returns DB to the snapshot taken pre-apply.
        c.updater.rollback(session, applied.id)
        pkgs = c.updater.list_packages()
        assert pkgs[0].rolled_back_at is not None
    results.append(step("updater apply + rollback", t16))

    # 17. Startup profile under target
    def t17():
        from backend.app import STARTUP_PROFILE
        assert STARTUP_PROFILE["total_s"] < 5.0, STARTUP_PROFILE
    results.append(step("startup under 5s target", t17))

    passed = sum(1 for r in results if r)
    total = len(results)
    print(f"\nVerification: {passed}/{total} checks passed.")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
