"""Operational reporting & exports."""
from __future__ import annotations
import csv
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .. import db
from ..permissions import Session, requires


@dataclass
class Report:
    title: str
    columns: list[str]
    rows: list[list]
    summary: dict


class ReportingService:

    @requires("report.read")
    def occupancy(self, session: Session, *, as_of: date | None = None) -> Report:
        conn = db.get_connection()
        rows = conn.execute("""
            SELECT bld.name AS building, COUNT(b.id) AS beds,
                   SUM(CASE WHEN EXISTS(
                        SELECT 1 FROM bed_assignments a
                         WHERE a.bed_id=b.id AND a.end_date IS NULL) THEN 1 ELSE 0 END) AS occupied
            FROM beds b JOIN rooms r ON r.id=b.room_id
                        JOIN buildings bld ON bld.id=r.building_id
            GROUP BY bld.id, bld.name ORDER BY bld.name
        """).fetchall()
        out_rows = []
        total_beds = total_occ = 0
        for r in rows:
            beds = r["beds"] or 0
            occ = r["occupied"] or 0
            pct = (occ / beds * 100) if beds else 0
            out_rows.append([r["building"], beds, occ, f"{pct:.1f}%"])
            total_beds += beds
            total_occ += occ
        return Report(
            title="Occupancy by Dorm",
            columns=["Building", "Beds", "Occupied", "Occupancy %"],
            rows=out_rows,
            summary={"total_beds": total_beds, "total_occupied": total_occ,
                     "as_of": (as_of or date.today()).isoformat()})

    @requires("report.read")
    def move_trends(self, session: Session, days: int = 30) -> Report:
        conn = db.get_connection()
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        rows = conn.execute("""
            SELECT substr(effective_date, 1, 10) AS d,
                   SUM(CASE WHEN end_date IS NULL THEN 1 ELSE 0 END) AS moves_in,
                   SUM(CASE WHEN end_date IS NOT NULL THEN 1 ELSE 0 END) AS moves_out
            FROM bed_assignments WHERE effective_date >= ?
            GROUP BY d ORDER BY d DESC
        """, (cutoff,)).fetchall()
        return Report(
            title=f"Move trends — last {days} days",
            columns=["Date", "Move-ins", "Move-outs"],
            rows=[[r["d"], r["moves_in"], r["moves_out"]] for r in rows],
            summary={"days": days})

    @requires("report.read")
    def resource_velocity(self, session: Session, days: int = 30) -> Report:
        conn = db.get_connection()
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        rows = conn.execute("""
            SELECT substr(published_at, 1, 10) AS d, COUNT(*) AS n
            FROM resource_versions
            WHERE status='published' AND published_at >= ?
            GROUP BY d ORDER BY d DESC
        """, (cutoff,)).fetchall()
        return Report(
            title=f"Resource publish velocity — last {days} days",
            columns=["Date", "Versions Published"],
            rows=[[r["d"], r["n"]] for r in rows],
            summary={"total": sum(r["n"] for r in rows)})

    @requires("report.read")
    def compliance_sla(self, session: Session, days: int = 30) -> Report:
        conn = db.get_connection()
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        rows = conn.execute("""
            SELECT kind,
                   AVG(julianday(decided_at) - julianday(created_at)) AS avg_days,
                   COUNT(*) AS decided
            FROM employer_cases WHERE decided_at IS NOT NULL AND created_at >= ?
            GROUP BY kind
        """, (cutoff,)).fetchall()
        return Report(
            title=f"Compliance SLA — last {days} days",
            columns=["Kind", "Avg days to decision", "Decisions"],
            rows=[[r["kind"], f"{(r['avg_days'] or 0):.2f}", r["decided"]] for r in rows],
            summary={"days": days})

    @requires("report.read")
    def notification_delivery(self, session: Session, days: int = 7) -> Report:
        conn = db.get_connection()
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        rows = conn.execute("""
            SELECT status, COUNT(*) AS n FROM notif_messages
            WHERE created_at >= ? GROUP BY status
        """, (cutoff,)).fetchall()
        return Report(
            title=f"Notification delivery — last {days} days",
            columns=["Status", "Count"],
            rows=[[r["status"], r["n"]] for r in rows],
            summary={"days": days})

    @requires("report.export")
    def export(self, session: Session, report: Report, fmt: str,
               path: str | Path) -> None:
        fmt = fmt.lower()
        if fmt == "csv":
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(report.columns)
                w.writerows(report.rows)
        elif fmt == "xlsx":
            try:
                from openpyxl import Workbook
            except ImportError as e:
                raise RuntimeError("openpyxl not installed") from e
            wb = Workbook()
            ws = wb.active
            ws.title = report.title[:30]
            ws.append(report.columns)
            for r in report.rows:
                ws.append(list(r))
            wb.save(str(path))
        else:
            raise ValueError(f"Unsupported export format: {fmt}")
