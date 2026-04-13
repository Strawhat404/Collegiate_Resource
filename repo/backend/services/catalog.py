"""Unified resource catalog: tree, custom types, metadata, semver review."""
from __future__ import annotations
import json
import re
from dataclasses import dataclass
from datetime import datetime

from .. import audit, db, events
from ..permissions import Session, requires
from .auth import BizError


# ---- Events ---------------------------------------------------------------

CATALOG_SUBMITTED = "CATALOG_SUBMITTED"
CATALOG_REVIEWED = "CATALOG_REVIEWED"


@dataclass
class CatalogNode:
    id: int
    parent_id: int | None
    name: str
    children: list["CatalogNode"]


@dataclass
class TypeField:
    code: str
    label: str
    field_type: str
    regex: str | None
    required: bool
    enum_values: list[str] | None
    sort_order: int


@dataclass
class CatalogType:
    id: int
    code: str
    name: str
    description: str | None
    enabled: bool
    fields: list[TypeField]


# ---- Semver helpers -------------------------------------------------------

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _parse(v: str) -> tuple[int, int, int]:
    m = _SEMVER_RE.match(v or "")
    if not m:
        return (0, 1, 0)
    return tuple(int(x) for x in m.groups())  # type: ignore[return-value]


def bump(v: str, level: str = "minor") -> str:
    """Bump a semantic version. level in {major, minor, patch}."""
    M, m, p = _parse(v)
    if level == "major":
        return f"{M+1}.0.0"
    if level == "patch":
        return f"{M}.{m}.{p+1}"
    return f"{M}.{m+1}.0"


# ---------------------------------------------------------------------------

class CatalogService:

    # ---- Tree ----------------------------------------------------------

    def list_tree(self) -> list[CatalogNode]:
        conn = db.get_connection()
        rows = conn.execute(
            "SELECT id, parent_id, name FROM catalog_nodes "
            "ORDER BY COALESCE(parent_id, 0), sort_order, name").fetchall()
        nodes = {r["id"]: CatalogNode(r["id"], r["parent_id"], r["name"], [])
                 for r in rows}
        roots: list[CatalogNode] = []
        for n in nodes.values():
            if n.parent_id and n.parent_id in nodes:
                nodes[n.parent_id].children.append(n)
            else:
                roots.append(n)
        return roots

    @requires("catalog.write")
    def create_node(self, session: Session, name: str,
                    parent_id: int | None = None) -> int:
        if not name.strip():
            raise BizError("BAD_NAME", "Node name required.")
        with db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO catalog_nodes(parent_id, name) VALUES (?, ?)",
                (parent_id, name.strip()))
            new_id = cur.lastrowid
        audit.record(session.user_id, "catalog_node", new_id, "create",
                     {"name": name, "parent_id": parent_id})
        return new_id

    @requires("catalog.write")
    def rename_node(self, session: Session, node_id: int, new_name: str) -> None:
        with db.transaction() as conn:
            conn.execute("UPDATE catalog_nodes SET name=? WHERE id=?",
                         (new_name.strip(), node_id))
        audit.record(session.user_id, "catalog_node", node_id, "rename",
                     {"name": new_name})

    @requires("catalog.write")
    def delete_node(self, session: Session, node_id: int) -> None:
        conn = db.get_connection()
        # Refuse if any resource is attached.
        in_use = conn.execute(
            "SELECT 1 FROM resource_catalog WHERE node_id=? LIMIT 1",
            (node_id,)).fetchone()
        if in_use:
            raise BizError("NODE_IN_USE", "Node holds resources; reassign first.")
        with db.transaction() as conn:
            conn.execute("DELETE FROM catalog_nodes WHERE id=?", (node_id,))
        audit.record(session.user_id, "catalog_node", node_id, "delete", {})

    # ---- Types ---------------------------------------------------------

    def list_types(self) -> list[CatalogType]:
        conn = db.get_connection()
        types = conn.execute(
            "SELECT id, code, name, description, enabled FROM catalog_types "
            "ORDER BY name").fetchall()
        out: list[CatalogType] = []
        for t in types:
            fields = [
                TypeField(
                    code=r["code"], label=r["label"],
                    field_type=r["field_type"], regex=r["regex"],
                    required=bool(r["required"]),
                    enum_values=json.loads(r["enum_values"]) if r["enum_values"] else None,
                    sort_order=r["sort_order"],
                )
                for r in conn.execute(
                    "SELECT * FROM catalog_type_fields WHERE type_id=? "
                    "ORDER BY sort_order, code", (t["id"],))
            ]
            out.append(CatalogType(
                id=t["id"], code=t["code"], name=t["name"],
                description=t["description"], enabled=bool(t["enabled"]),
                fields=fields))
        return out

    def get_type(self, code: str) -> CatalogType | None:
        for t in self.list_types():
            if t.code == code:
                return t
        return None

    @requires("catalog.write")
    def upsert_type(self, session: Session, code: str, name: str,
                    description: str = "", fields: list[dict] | None = None) -> int:
        with db.transaction() as conn:
            existing = conn.execute(
                "SELECT id FROM catalog_types WHERE code=?", (code,)).fetchone()
            if existing:
                tid = existing["id"]
                conn.execute(
                    "UPDATE catalog_types SET name=?, description=? WHERE id=?",
                    (name, description, tid))
                conn.execute("DELETE FROM catalog_type_fields WHERE type_id=?", (tid,))
            else:
                cur = conn.execute(
                    "INSERT INTO catalog_types(code, name, description) VALUES (?, ?, ?)",
                    (code, name, description))
                tid = cur.lastrowid
            for i, f in enumerate(fields or []):
                self._validate_field_def(f)
                conn.execute(
                    """INSERT INTO catalog_type_fields(type_id, code, label,
                            field_type, regex, required, enum_values, sort_order)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (tid, f["code"], f["label"], f["field_type"],
                     f.get("regex"), 1 if f.get("required") else 0,
                     json.dumps(f["enum_values"]) if f.get("enum_values") else None,
                     i))
        audit.record(session.user_id, "catalog_type", tid, "upsert",
                     {"code": code, "fields": len(fields or [])})
        return tid

    def _validate_field_def(self, f: dict) -> None:
        for k in ("code", "label", "field_type"):
            if not f.get(k):
                raise BizError("BAD_FIELD", f"field.{k} required")
        if f["field_type"] not in ("text", "int", "date", "enum", "url",
                                   "file", "markdown"):
            raise BizError("BAD_FIELD_TYPE", f"unknown field type: {f['field_type']}")
        if f["field_type"] == "enum" and not f.get("enum_values"):
            raise BizError("BAD_FIELD", "enum field requires enum_values")
        if f.get("regex"):
            try:
                re.compile(f["regex"])
            except re.error as e:
                raise BizError("BAD_REGEX", f"invalid regex: {e}")

    # ---- Resource attach + metadata ------------------------------------

    @requires("catalog.write")
    def attach(self, session: Session, resource_id: int, *, node_id: int | None,
               type_code: str | None, subject: str | None = None,
               grade: str | None = None, course: str | None = None,
               metadata: dict[str, str] | None = None,
               tags: list[str] | None = None) -> None:
        type_id = None
        type_def: CatalogType | None = None
        if type_code:
            type_def = self.get_type(type_code)
            if not type_def:
                raise BizError("TYPE_NOT_FOUND", type_code)
            type_id = type_def.id
        if type_def and metadata is not None:
            self._validate_metadata(type_def, metadata)
        with db.transaction() as conn:
            conn.execute(
                """INSERT INTO resource_catalog(resource_id, node_id, type_id,
                        subject, grade, course)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(resource_id) DO UPDATE SET
                        node_id=excluded.node_id, type_id=excluded.type_id,
                        subject=excluded.subject, grade=excluded.grade,
                        course=excluded.course""",
                (resource_id, node_id, type_id, subject, grade, course))
            if metadata is not None:
                conn.execute("DELETE FROM resource_metadata WHERE resource_id=?",
                             (resource_id,))
                for k, v in metadata.items():
                    conn.execute(
                        "INSERT INTO resource_metadata(resource_id, field_code, value) "
                        "VALUES (?, ?, ?)", (resource_id, k, v))
            if tags is not None:
                conn.execute("DELETE FROM resource_tags WHERE resource_id=?",
                             (resource_id,))
                for t in tags:
                    conn.execute(
                        "INSERT INTO resource_tags(resource_id, tag) VALUES (?, ?) "
                        "ON CONFLICT DO NOTHING", (resource_id, t.lower()))
        audit.record(session.user_id, "resource", resource_id, "catalog_attach",
                     {"node_id": node_id, "type": type_code,
                      "metadata_keys": sorted((metadata or {}).keys()),
                      "tags": tags or []})

    def _validate_metadata(self, type_def: CatalogType, md: dict[str, str]) -> None:
        present = set(md.keys())
        for f in type_def.fields:
            v = md.get(f.code, "")
            if f.required and not v:
                raise BizError("METADATA_MISSING",
                               f"required field '{f.code}' missing")
            if not v:
                continue
            if f.field_type == "int":
                try:
                    int(v)
                except ValueError:
                    raise BizError("METADATA_BAD",
                                   f"'{f.code}' must be an integer")
            elif f.field_type == "date":
                try:
                    datetime.strptime(v, "%m/%d/%Y")
                except ValueError:
                    raise BizError("METADATA_BAD",
                                   f"'{f.code}' must be MM/DD/YYYY")
            elif f.field_type == "enum":
                if v not in (f.enum_values or []):
                    raise BizError("METADATA_BAD",
                                   f"'{f.code}' must be one of {f.enum_values}")
            elif f.regex:
                if not re.match(f.regex, v):
                    raise BizError("METADATA_BAD",
                                   f"'{f.code}' fails pattern {f.regex}")

    def get_metadata(self, resource_id: int) -> dict[str, str]:
        conn = db.get_connection()
        rows = conn.execute(
            "SELECT field_code, value FROM resource_metadata WHERE resource_id=?",
            (resource_id,)).fetchall()
        return {r["field_code"]: r["value"] for r in rows}

    def get_attachment(self, resource_id: int) -> dict | None:
        conn = db.get_connection()
        r = conn.execute(
            "SELECT * FROM resource_catalog WHERE resource_id=?",
            (resource_id,)).fetchone()
        return dict(r) if r else None

    def list_tags(self, resource_id: int) -> list[str]:
        conn = db.get_connection()
        return [r["tag"] for r in conn.execute(
            "SELECT tag FROM resource_tags WHERE resource_id=? ORDER BY tag",
            (resource_id,))]

    # ---- Relationships -------------------------------------------------

    @requires("catalog.write")
    def relate(self, session: Session, src_id: int, dst_id: int,
               relation: str = "related") -> None:
        if src_id == dst_id:
            raise BizError("BAD_RELATION", "Cannot relate a resource to itself.")
        if relation not in ("related", "supersedes", "requires"):
            raise BizError("BAD_RELATION", relation)
        with db.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO resource_relations(src_id, dst_id, relation) "
                "VALUES (?, ?, ?)", (src_id, dst_id, relation))
        audit.record(session.user_id, "resource", src_id, "relate",
                     {"dst": dst_id, "relation": relation})

    # ---- Review workflow ----------------------------------------------

    @requires("catalog.write")
    def submit_for_review(self, session: Session, resource_id: int) -> None:
        with db.transaction() as conn:
            conn.execute(
                "UPDATE resource_catalog SET review_state='in_review', "
                "submitted_at=datetime('now') WHERE resource_id=?",
                (resource_id,))
        audit.record(session.user_id, "resource", resource_id, "submit_review", {})
        events.bus.publish(CATALOG_SUBMITTED, {"resource_id": resource_id})

    @requires("catalog.review")
    def review(self, session: Session, resource_id: int, decision: str,
               notes: str = "") -> None:
        if decision not in ("approve", "reject"):
            raise BizError("BAD_DECISION", "decision must be approve|reject")
        new_state = "approved" if decision == "approve" else "rejected"
        with db.transaction() as conn:
            conn.execute(
                "UPDATE resource_catalog SET review_state=?, reviewer_id=?, "
                "decided_at=datetime('now') WHERE resource_id=?",
                (new_state, session.user_id, resource_id))
        audit.record(session.user_id, "resource", resource_id, "review",
                     {"decision": decision, "notes": notes})
        events.bus.publish(CATALOG_REVIEWED,
                           {"resource_id": resource_id, "decision": decision})

    @requires("catalog.publish")
    def publish_with_semver(self, session: Session, resource_id: int,
                            level: str = "minor") -> str:
        """Publish a reviewer-approved resource and bump its semantic version.

        Unified governance: this delegates to `ResourceService.publish_version`
        which atomically (in one transaction) marks the latest version
        published AND bumps `resource_catalog.semver`. There is no longer any
        path that publishes without bumping semver, or vice versa.
        """
        conn = db.get_connection()
        rec = conn.execute(
            "SELECT review_state FROM resource_catalog WHERE resource_id=?",
            (resource_id,)).fetchone()
        if not rec:
            raise BizError("CATALOG_MISSING", "resource not attached to catalog")
        if rec["review_state"] != "approved":
            raise BizError("NOT_APPROVED",
                           "Reviewer approval required before publishing.")
        latest = conn.execute(
            "SELECT id FROM resource_versions WHERE resource_id=? "
            "ORDER BY version_no DESC LIMIT 1", (resource_id,)).fetchone()
        if not latest:
            raise BizError("NO_VERSION", "Resource has no versions to publish.")
        # Delayed import avoids circular module load at top of file.
        from .resource import ResourceService
        ResourceService().publish_version(session, latest["id"],
                                          semver_level=level)
        new_v = conn.execute(
            "SELECT semver FROM resource_catalog WHERE resource_id=?",
            (resource_id,)).fetchone()["semver"]
        return new_v
