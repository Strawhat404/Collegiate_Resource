"""Housing & bed-assignment service."""
from __future__ import annotations
from datetime import date, datetime

from .. import audit, db, events
from ..models import Bed, BedAssignment
from ..permissions import Session, requires
from .auth import BizError


class HousingService:

    _READ_PERMS = ("housing.write", "housing.read", "student.write",
                   "student.pii.read", "system.admin")

    def _require_read(self, session: Session) -> None:
        if not session.has_any(self._READ_PERMS):
            from ..permissions import PermissionDenied
            raise PermissionDenied("housing.read")

    def list_buildings(self, session: Session) -> list[dict]:
        self._require_read(session)
        conn = db.get_connection()
        return [dict(r) for r in conn.execute(
            "SELECT id, name, address FROM buildings ORDER BY name").fetchall()]

    def list_beds(self, session: Session, building_id: int | None = None,
                  vacant_only: bool = False) -> list[Bed]:
        self._require_read(session)
        conn = db.get_connection()
        sql = """
            SELECT b.id, bld.name AS building, r.code AS room, b.code AS bed_code,
                   EXISTS (SELECT 1 FROM bed_assignments a
                           WHERE a.bed_id = b.id AND a.end_date IS NULL) AS occupied
            FROM beds b
            JOIN rooms r ON r.id = b.room_id
            JOIN buildings bld ON bld.id = r.building_id
        """
        args: list = []
        where = []
        if building_id:
            where.append("bld.id = ?")
            args.append(building_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY bld.name, r.code, b.code"
        rows = conn.execute(sql, args).fetchall()
        beds = [Bed(id=r["id"], building=r["building"], room=r["room"],
                    code=r["bed_code"], occupied=bool(r["occupied"])) for r in rows]
        if vacant_only:
            beds = [b for b in beds if not b.occupied]
        return beds

    @requires("housing.write")
    def assign_bed(self, session: Session, student_id: int, bed_id: int,
                   effective_date: date, reason: str = "") -> BedAssignment:
        conn = db.get_connection()
        active = conn.execute(
            "SELECT 1 FROM bed_assignments WHERE bed_id=? AND end_date IS NULL",
            (bed_id,)).fetchone()
        if active:
            raise BizError("BED_OCCUPIED", "Bed is currently occupied.")
        with db.transaction() as conn:
            # Vacate any other active assignment for this student first.
            conn.execute(
                "UPDATE bed_assignments SET end_date=? WHERE student_id=? AND end_date IS NULL",
                (effective_date.isoformat(), student_id))
            cur = conn.execute(
                """INSERT INTO bed_assignments(student_id, bed_id, effective_date,
                       reason, operator_id) VALUES (?, ?, ?, ?, ?)""",
                (student_id, bed_id, effective_date.isoformat(), reason,
                 session.user_id))
            assignment_id = cur.lastrowid
            conn.execute("UPDATE students SET housing_status='on_campus' WHERE id=?",
                         (student_id,))
        audit.record(session.user_id, "bed_assignment", assignment_id, "assign",
                     {"student_id": student_id, "bed_id": bed_id,
                      "effective_date": effective_date.isoformat(),
                      "reason": reason})
        events.bus.publish(events.BED_ASSIGNED,
                           {"student_id": student_id, "bed_id": bed_id,
                            "effective_date": effective_date.isoformat()})
        return self._get_assignment(assignment_id)

    @requires("housing.write")
    def vacate_bed(self, session: Session, assignment_id: int,
                   effective_date: date, reason: str = "") -> BedAssignment:
        with db.transaction() as conn:
            row = conn.execute(
                "SELECT student_id, bed_id, end_date FROM bed_assignments WHERE id=?",
                (assignment_id,)).fetchone()
            if not row:
                raise BizError("ASSIGNMENT_NOT_FOUND", "Assignment not found.")
            if row["end_date"] is not None:
                raise BizError("ALREADY_VACATED", "Assignment already ended.")
            conn.execute(
                "UPDATE bed_assignments SET end_date=?, reason=COALESCE(?, reason) WHERE id=?",
                (effective_date.isoformat(), reason or None, assignment_id))
            conn.execute("UPDATE students SET housing_status='pending' WHERE id=?",
                         (row["student_id"],))
        audit.record(session.user_id, "bed_assignment", assignment_id, "vacate",
                     {"effective_date": effective_date.isoformat(), "reason": reason})
        events.bus.publish(events.BED_VACATED,
                           {"assignment_id": assignment_id,
                            "student_id": row["student_id"]})
        return self._get_assignment(assignment_id)

    @requires("housing.write")
    def transfer(self, session: Session, student_id: int, new_bed_id: int,
                 effective_date: date, reason: str = "") -> BedAssignment:
        # End current, then assign new.
        conn = db.get_connection()
        cur = conn.execute(
            "SELECT id FROM bed_assignments WHERE student_id=? AND end_date IS NULL",
            (student_id,)).fetchone()
        if cur:
            self.vacate_bed(session, cur["id"], effective_date,
                            reason=f"transfer: {reason}".strip(": "))
        a = self.assign_bed(session, student_id, new_bed_id, effective_date, reason)
        events.bus.publish(events.BED_TRANSFERRED,
                           {"student_id": student_id, "bed_id": new_bed_id})
        return a

    def assignment_history(self, session: Session, *,
                           student_id: int | None = None,
                           bed_id: int | None = None) -> list[BedAssignment]:
        self._require_read(session)
        conn = db.get_connection()
        sql = """
            SELECT a.id, a.student_id, s.full_name AS sname, a.bed_id,
                   bld.name||' '||r.code||'-'||b.code AS bed_label,
                   a.effective_date, a.end_date, a.reason,
                   a.created_at, a.operator_id
            FROM bed_assignments a
            JOIN students s ON s.id = a.student_id
            JOIN beds b ON b.id = a.bed_id
            JOIN rooms r ON r.id = b.room_id
            JOIN buildings bld ON bld.id = r.building_id
        """
        args = []
        where = []
        if student_id:
            where.append("a.student_id=?")
            args.append(student_id)
        if bed_id:
            where.append("a.bed_id=?")
            args.append(bed_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY a.effective_date DESC, a.id DESC"
        rows = conn.execute(sql, args).fetchall()
        return [BedAssignment(
            id=r["id"], student_id=r["student_id"], student_name=r["sname"],
            bed_id=r["bed_id"], bed_label=r["bed_label"],
            effective_date=r["effective_date"], end_date=r["end_date"],
            reason=r["reason"],
            created_at=r["created_at"], operator_id=r["operator_id"]) for r in rows]

    def _get_assignment(self, assignment_id: int) -> BedAssignment:
        conn = db.get_connection()
        r = conn.execute("""
            SELECT a.id, a.student_id, s.full_name AS sname, a.bed_id,
                   bld.name||' '||r.code||'-'||b.code AS bed_label,
                   a.effective_date, a.end_date, a.reason,
                   a.created_at, a.operator_id
            FROM bed_assignments a
            JOIN students s ON s.id=a.student_id
            JOIN beds b ON b.id=a.bed_id
            JOIN rooms r ON r.id=b.room_id
            JOIN buildings bld ON bld.id=r.building_id
            WHERE a.id=?""", (assignment_id,)).fetchone()
        return BedAssignment(
            id=r["id"], student_id=r["student_id"], student_name=r["sname"],
            bed_id=r["bed_id"], bed_label=r["bed_label"],
            effective_date=r["effective_date"], end_date=r["end_date"],
            reason=r["reason"],
            created_at=r["created_at"], operator_id=r["operator_id"])
