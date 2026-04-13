"""Employer onboarding & compliance reviews."""
from __future__ import annotations

from .. import audit, crypto, db, events
from ..models import EmployerCase
from ..permissions import Session, requires
from .auth import BizError


_COMPLIANCE_READ_PERMS = ("compliance.review", "compliance.violation",
                          "compliance.evidence", "compliance.action",
                          "system.admin")


def _require_compliance_read(session: Session) -> None:
    if not session.has_any(_COMPLIANCE_READ_PERMS):
        from ..permissions import PermissionDenied
        raise PermissionDenied("compliance.read")


class EmployerComplianceService:

    def list_employers(self, session: Session) -> list[dict]:
        _require_compliance_read(session)
        conn = db.get_connection()
        return [dict(r) for r in conn.execute(
            "SELECT id, name, ein, status FROM employers ORDER BY name")]

    @requires("compliance.review")
    def submit_employer(self, session: Session, name: str, ein: str | None,
                        contact_email: str | None) -> int:
        with db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO employers(name, ein, contact_email_enc, status) "
                "VALUES (?, ?, ?, 'pending')",
                (name, ein, crypto.encrypt_field(contact_email)))
            emp_id = cur.lastrowid
            conn.execute(
                "INSERT INTO employer_cases(employer_id, kind, state) "
                "VALUES (?, 'onboarding', 'submitted')", (emp_id,))
            cur2 = conn.execute("SELECT last_insert_rowid() AS id")
            case_id = cur2.fetchone()["id"]
            conn.execute(
                "INSERT INTO employers_fts(rowid, name, ein) VALUES (?, ?, ?)",
                (emp_id, name, ein or ""))
            conn.execute(
                "INSERT INTO cases_fts(rowid, employer_name, kind, state, notes) "
                "VALUES (?, ?, 'onboarding', 'submitted', '')",
                (case_id, name))
        audit.record(session.user_id, "employer", emp_id, "submit",
                     {"name": name, "ein": ein})
        events.bus.publish(events.CASE_SUBMITTED,
                           {"employer_id": emp_id, "case_id": case_id})
        return case_id

    def list_cases(self, session: Session, *, state: str | None = None,
                   kind: str | None = None) -> list[EmployerCase]:
        _require_compliance_read(session)
        conn = db.get_connection()
        sql = """SELECT c.*, e.name AS employer_name FROM employer_cases c
                 JOIN employers e ON e.id = c.employer_id"""
        where, args = [], []
        if state:
            where.append("c.state=?")
            args.append(state)
        if kind:
            where.append("c.kind=?")
            args.append(kind)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY c.created_at DESC"
        return [EmployerCase(
            id=r["id"], employer_id=r["employer_id"],
            employer_name=r["employer_name"], kind=r["kind"], state=r["state"],
            reviewer_id=r["reviewer_id"], decision=r["decision"],
            decided_at=r["decided_at"], notes=r["notes"]
        ) for r in conn.execute(sql, args).fetchall()]

    @requires("compliance.review")
    def assign_reviewer(self, session: Session, case_id: int, user_id: int) -> None:
        with db.transaction() as conn:
            conn.execute("UPDATE employer_cases SET reviewer_id=?, state='under_review' "
                         "WHERE id=?", (user_id, case_id))
        audit.record(session.user_id, "employer_case", case_id, "assign_reviewer",
                     {"reviewer_id": user_id})

    @requires("compliance.review")
    def decide(self, session: Session, case_id: int, decision: str,
               notes: str = "") -> EmployerCase:
        if decision not in ("approve", "reject"):
            raise BizError("BAD_DECISION", "decision must be approve|reject")
        new_state = "approved" if decision == "approve" else "rejected"
        conn0 = db.get_connection()
        r = conn0.execute(
            "SELECT employer_id, kind FROM employer_cases WHERE id=?",
            (case_id,)).fetchone()
        if not r:
            raise BizError("CASE_NOT_FOUND", "Case not found.")
        # Approval gate: onboarding/violation cases REQUIRE that
        #   (a) at least one piece of verification evidence has been uploaded
        #       for this employer, and
        #   (b) the supplied notes (or the employer's record) clear the
        #       sensitive-word scan.
        # Reject decisions skip the gate so reviewers can always close a bad
        # actor without first uploading evidence.
        if decision == "approve":
            ev_count = conn0.execute(
                "SELECT COUNT(*) AS n FROM employer_evidence WHERE employer_id=?",
                (r["employer_id"],)).fetchone()["n"]
            if not ev_count:
                raise BizError(
                    "EVIDENCE_REQUIRED",
                    "Approval requires at least one verification document on file. "
                    "Upload evidence first via EvidenceService.upload.")
            # Sensitive-word gate is fail-closed. If the scanner cannot run
            # (table missing / DB error / dictionary load failure), the
            # approval is REJECTED — fail-open here would let a reviewer
            # approve an employer the gate was designed to block whenever
            # the gate's own infrastructure is broken.
            try:
                from .compliance_ext import SensitiveWordService
                sw = SensitiveWordService()
                emp = conn0.execute(
                    "SELECT name FROM employers WHERE id=?",
                    (r["employer_id"],)).fetchone()
                hits = sw.scan(f"{emp['name'] if emp else ''} {notes or ''}")
            except BizError:
                raise
            except Exception as _scan_err:
                raise BizError(
                    "SENSITIVE_WORD_SCAN_UNAVAILABLE",
                    "Approval blocked: sensitive-word scanner is unavailable "
                    f"({type(_scan_err).__name__}). Restore the scanner "
                    "(seed `sensitive_words` and verify DB connectivity) "
                    "before retrying the approval.")
            if any(h["severity"] == "high" for h in hits):
                raise BizError(
                    "SENSITIVE_WORD_BLOCK",
                    f"Approval blocked: high-severity terms detected "
                    f"({', '.join(sorted({h['word'] for h in hits if h['severity'] == 'high'}))}). "
                    "Resolve compliance review before approving.")
        with db.transaction() as conn:
            conn.execute(
                "UPDATE employer_cases SET decision=?, state=?, decided_at=datetime('now'), "
                "notes=COALESCE(?, notes) WHERE id=?",
                (decision, new_state, notes or None, case_id))
            conn.execute("UPDATE employers SET status=? WHERE id=?",
                         (new_state, r["employer_id"]))
        audit.record(session.user_id, "employer_case", case_id, "decide",
                     {"decision": decision, "notes": notes})
        events.bus.publish(events.CASE_DECIDED,
                           {"case_id": case_id, "decision": decision,
                            "employer_id": r["employer_id"]})
        return [c for c in self.list_cases(session) if c.id == case_id][0]

    @requires("compliance.violation")
    def open_violation(self, session: Session, employer_id: int,
                       notes: str) -> int:
        with db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO employer_cases(employer_id, kind, state, notes) "
                "VALUES (?, 'violation', 'submitted', ?)",
                (employer_id, notes))
            case_id = cur.lastrowid
        audit.record(session.user_id, "employer_case", case_id, "open_violation",
                     {"employer_id": employer_id, "notes": notes})
        events.bus.publish(events.VIOLATION_OPENED,
                           {"case_id": case_id, "employer_id": employer_id})
        return case_id

    @requires("compliance.violation")
    def resolve_violation(self, session: Session, case_id: int, notes: str) -> None:
        with db.transaction() as conn:
            conn.execute(
                "UPDATE employer_cases SET state='resolved', "
                "notes=COALESCE(notes,'')||char(10)||? WHERE id=? AND kind='violation'",
                (notes, case_id))
        audit.record(session.user_id, "employer_case", case_id, "resolve_violation",
                     {"notes": notes})
        events.bus.publish(events.VIOLATION_RESOLVED, {"case_id": case_id})
