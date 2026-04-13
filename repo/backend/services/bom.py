"""Style master data + multi-version BOM and process routing.

Released versions are immutable: cost, BOM lines, and routing steps are
locked. Changes happen through ``ChangeRequest`` -> new draft version ->
two-step approval (first_approved -> released).
"""
from __future__ import annotations
from dataclasses import dataclass

from .. import audit, db
from ..permissions import Session, requires
from .auth import BizError


@dataclass
class Style:
    id: int
    style_code: str
    name: str
    description: str | None
    status: str


@dataclass
class StyleVersion:
    id: int
    style_id: int
    version_no: int
    state: str
    cost_usd: float
    notes: str | None
    first_approver_id: int | None
    final_approver_id: int | None
    released_at: str | None


@dataclass
class BomItem:
    id: int
    component_code: str
    description: str | None
    quantity: float
    unit_cost_usd: float
    sort_order: int


@dataclass
class RoutingStep:
    id: int
    step_no: int
    operation: str
    machine: str | None
    setup_minutes: float
    run_minutes: float
    rate_per_hour_usd: float


class BomService:

    # ---- Styles --------------------------------------------------------

    @requires("bom.write")
    def create_style(self, session: Session, style_code: str, name: str,
                     description: str = "") -> Style:
        with db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO styles(style_code, name, description) VALUES (?, ?, ?)",
                (style_code, name, description))
            sid = cur.lastrowid
            # Seed first draft version 1.
            conn.execute(
                "INSERT INTO style_versions(style_id, version_no, state, created_by) "
                "VALUES (?, 1, 'draft', ?)", (sid, session.user_id))
        audit.record(session.user_id, "style", sid, "create",
                     {"code": style_code, "name": name})
        return self.get_style(sid)

    def get_style(self, style_id: int) -> Style:
        r = db.get_connection().execute(
            "SELECT * FROM styles WHERE id=?", (style_id,)).fetchone()
        if not r:
            raise BizError("STYLE_NOT_FOUND", str(style_id))
        return Style(id=r["id"], style_code=r["style_code"], name=r["name"],
                     description=r["description"], status=r["status"])

    def list_styles(self) -> list[Style]:
        rows = db.get_connection().execute(
            "SELECT * FROM styles ORDER BY style_code").fetchall()
        return [Style(id=r["id"], style_code=r["style_code"], name=r["name"],
                      description=r["description"], status=r["status"]) for r in rows]

    # ---- Versions ------------------------------------------------------

    def list_versions(self, style_id: int) -> list[StyleVersion]:
        rows = db.get_connection().execute(
            "SELECT * FROM style_versions WHERE style_id=? "
            "ORDER BY version_no DESC", (style_id,)).fetchall()
        return [self._row_to_version(r) for r in rows]

    def get_version(self, version_id: int) -> StyleVersion:
        r = db.get_connection().execute(
            "SELECT * FROM style_versions WHERE id=?", (version_id,)).fetchone()
        if not r:
            raise BizError("VERSION_NOT_FOUND", str(version_id))
        return self._row_to_version(r)

    @requires("bom.write")
    def add_bom_item(self, session: Session, version_id: int, *,
                     component_code: str, description: str = "",
                     quantity: float = 1, unit_cost_usd: float = 0) -> int:
        self._assert_editable(version_id)
        with db.transaction() as conn:
            n = conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) AS m FROM bom_items "
                "WHERE style_version_id=?", (version_id,)).fetchone()["m"] + 1
            cur = conn.execute(
                """INSERT INTO bom_items(style_version_id, component_code,
                       description, quantity, unit_cost_usd, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (version_id, component_code, description, quantity,
                 unit_cost_usd, n))
            bid = cur.lastrowid
        self._recalc_cost(version_id, session.user_id)
        audit.record(session.user_id, "bom_item", bid, "add",
                     {"version_id": version_id, "component_code": component_code,
                      "qty": quantity, "unit_cost": unit_cost_usd})
        return bid

    @requires("bom.write")
    def add_routing_step(self, session: Session, version_id: int, *,
                         operation: str, machine: str = "",
                         setup_minutes: float = 0, run_minutes: float = 0,
                         rate_per_hour_usd: float = 0) -> int:
        self._assert_editable(version_id)
        with db.transaction() as conn:
            step_no = conn.execute(
                "SELECT COALESCE(MAX(step_no), 0) AS m FROM routing_steps "
                "WHERE style_version_id=?", (version_id,)).fetchone()["m"] + 1
            cur = conn.execute(
                """INSERT INTO routing_steps(style_version_id, step_no, operation,
                       machine, setup_minutes, run_minutes, rate_per_hour_usd)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (version_id, step_no, operation, machine, setup_minutes,
                 run_minutes, rate_per_hour_usd))
            rid = cur.lastrowid
        self._recalc_cost(version_id, session.user_id)
        audit.record(session.user_id, "routing_step", rid, "add",
                     {"version_id": version_id, "operation": operation})
        return rid

    def list_bom(self, version_id: int) -> list[BomItem]:
        rows = db.get_connection().execute(
            "SELECT * FROM bom_items WHERE style_version_id=? ORDER BY sort_order",
            (version_id,)).fetchall()
        return [BomItem(id=r["id"], component_code=r["component_code"],
                        description=r["description"], quantity=r["quantity"],
                        unit_cost_usd=r["unit_cost_usd"],
                        sort_order=r["sort_order"]) for r in rows]

    def list_routing(self, version_id: int) -> list[RoutingStep]:
        rows = db.get_connection().execute(
            "SELECT * FROM routing_steps WHERE style_version_id=? "
            "ORDER BY step_no", (version_id,)).fetchall()
        return [RoutingStep(id=r["id"], step_no=r["step_no"],
                            operation=r["operation"], machine=r["machine"],
                            setup_minutes=r["setup_minutes"],
                            run_minutes=r["run_minutes"],
                            rate_per_hour_usd=r["rate_per_hour_usd"])
                for r in rows]

    # ---- Two-step approval --------------------------------------------

    @requires("bom.write")
    def submit_for_approval(self, session: Session, version_id: int) -> None:
        v = self.get_version(version_id)
        if v.state != "draft":
            raise BizError("BAD_STATE", f"cannot submit from state '{v.state}'")
        if not self.list_bom(version_id):
            raise BizError("EMPTY_BOM", "BOM must contain at least one item.")
        with db.transaction() as conn:
            conn.execute(
                "UPDATE style_versions SET state='submitted' WHERE id=?",
                (version_id,))
        audit.record(session.user_id, "style_version", version_id,
                     "submit_for_approval", {})

    @requires("bom.approve.first")
    def first_approve(self, session: Session, version_id: int) -> None:
        v = self.get_version(version_id)
        if v.state != "submitted":
            raise BizError("BAD_STATE", "version must be submitted first")
        with db.transaction() as conn:
            conn.execute(
                "UPDATE style_versions SET state='first_approved', "
                "first_approver_id=?, first_approved_at=datetime('now') "
                "WHERE id=?", (session.user_id, version_id))
        audit.record(session.user_id, "style_version", version_id,
                     "first_approve", {})

    @requires("bom.approve.final")
    def final_approve(self, session: Session, version_id: int) -> None:
        v = self.get_version(version_id)
        if v.state != "first_approved":
            raise BizError("BAD_STATE", "version must be first_approved")
        if v.first_approver_id == session.user_id:
            raise BizError("SAME_APPROVER",
                           "Final approver must differ from first approver.")
        with db.transaction() as conn:
            conn.execute(
                "UPDATE style_versions SET state='released', "
                "final_approver_id=?, released_at=datetime('now') WHERE id=?",
                (session.user_id, version_id))
        audit.record(session.user_id, "style_version", version_id,
                     "final_approve", {})

    @requires("bom.approve.first")
    def reject(self, session: Session, version_id: int, reason: str = "") -> None:
        with db.transaction() as conn:
            conn.execute(
                "UPDATE style_versions SET state='rejected', notes=COALESCE(notes,'')||char(10)||? "
                "WHERE id=?", (reason, version_id))
        audit.record(session.user_id, "style_version", version_id, "reject",
                     {"reason": reason})

    # ---- Change requests ----------------------------------------------

    @requires("bom.write")
    def open_change_request(self, session: Session, style_id: int,
                            base_version_id: int, reason: str) -> int:
        base = self.get_version(base_version_id)
        if base.state != "released":
            raise BizError("BAD_BASE",
                           "change requests must be raised against a released version")
        with db.transaction() as conn:
            # Create a new draft version that copies the released BOM/routing.
            new_no = conn.execute(
                "SELECT COALESCE(MAX(version_no), 0) + 1 AS n "
                "FROM style_versions WHERE style_id=?", (style_id,)
            ).fetchone()["n"]
            cur = conn.execute(
                "INSERT INTO style_versions(style_id, version_no, state, created_by, notes) "
                "VALUES (?, ?, 'draft', ?, ?)",
                (style_id, new_no, session.user_id, f"From CR: {reason}"))
            new_vid = cur.lastrowid
            for it in self.list_bom(base_version_id):
                conn.execute(
                    "INSERT INTO bom_items(style_version_id, component_code, description, "
                    "quantity, unit_cost_usd, sort_order) VALUES (?, ?, ?, ?, ?, ?)",
                    (new_vid, it.component_code, it.description, it.quantity,
                     it.unit_cost_usd, it.sort_order))
            for st in self.list_routing(base_version_id):
                conn.execute(
                    "INSERT INTO routing_steps(style_version_id, step_no, operation, "
                    "machine, setup_minutes, run_minutes, rate_per_hour_usd) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (new_vid, st.step_no, st.operation, st.machine,
                     st.setup_minutes, st.run_minutes, st.rate_per_hour_usd))
            cur2 = conn.execute(
                """INSERT INTO change_requests(style_id, base_version_id,
                        proposed_version_id, requested_by, reason)
                   VALUES (?, ?, ?, ?, ?)""",
                (style_id, base_version_id, new_vid, session.user_id, reason))
            cr_id = cur2.lastrowid
        self._recalc_cost(new_vid, session.user_id)
        audit.record(session.user_id, "change_request", cr_id, "open",
                     {"style_id": style_id, "base": base_version_id,
                      "new_version": new_vid, "reason": reason})
        return cr_id

    def list_change_requests(self, style_id: int | None = None) -> list[dict]:
        sql = "SELECT * FROM change_requests"
        args: list = []
        if style_id:
            sql += " WHERE style_id=?"
            args.append(style_id)
        sql += " ORDER BY created_at DESC"
        return [dict(r) for r in db.get_connection().execute(sql, args).fetchall()]

    # ---- Cost recalc --------------------------------------------------

    def compute_cost(self, version_id: int) -> float:
        bom = self.list_bom(version_id)
        routing = self.list_routing(version_id)
        materials = sum(round(b.quantity * b.unit_cost_usd, 4) for b in bom)
        labor = sum(round(((s.setup_minutes + s.run_minutes) / 60.0)
                          * s.rate_per_hour_usd, 4) for s in routing)
        return round(materials + labor, 2)

    def _recalc_cost(self, version_id: int, actor_id: int) -> None:
        v = self.get_version(version_id)
        if v.state == "released":
            return
        new_cost = self.compute_cost(version_id)
        if abs(new_cost - v.cost_usd) < 1e-6:
            return
        with db.transaction() as conn:
            conn.execute(
                "UPDATE style_versions SET cost_usd=? WHERE id=?",
                (new_cost, version_id))
        audit.record(actor_id, "style_version", version_id, "cost_recalc",
                     {"old": v.cost_usd, "new": new_cost})

    # ---- Helpers -------------------------------------------------------

    def _assert_editable(self, version_id: int) -> None:
        v = self.get_version(version_id)
        if v.state in ("released", "first_approved"):
            raise BizError("LOCKED",
                           f"version is {v.state}; edits require a change request")

    def _row_to_version(self, r) -> StyleVersion:
        return StyleVersion(
            id=r["id"], style_id=r["style_id"], version_no=r["version_no"],
            state=r["state"], cost_usd=r["cost_usd"], notes=r["notes"],
            first_approver_id=r["first_approver_id"],
            final_approver_id=r["final_approver_id"],
            released_at=r["released_at"])
