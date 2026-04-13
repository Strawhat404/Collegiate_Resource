"""PII must be redacted from audit payloads."""
from __future__ import annotations
import json

from backend import db
from backend.models import StudentDTO


def test_create_redacts_pii(container, admin_session):
    container.students.create(admin_session, StudentDTO(
        student_id="P1", full_name="P One",
        email="p1@example.edu", phone="555-867-5309", ssn_last4="1234"))
    row = db.get_connection().execute(
        "SELECT payload_json FROM audit_log WHERE entity_type='student' "
        "AND action='create' ORDER BY id DESC LIMIT 1").fetchone()
    payload = json.loads(row["payload_json"])
    assert "p1@example.edu" not in row["payload_json"]
    assert "5309" not in row["payload_json"]
    assert "1234" not in row["payload_json"]
    assert payload["email"].startswith("***")
    assert payload["phone"].startswith("***")
    assert payload["ssn_last4"].startswith("***")
