"""Academic resource catalog with versioning and publishing."""
from __future__ import annotations

from .. import audit, db, events
from ..models import Resource, ResourceVersion
from ..permissions import Session, requires
from .auth import BizError


class ResourceService:

    _READ_PERMS = ("resource.write", "resource.publish", "resource.read",
                   "catalog.write", "catalog.review", "catalog.publish",
                   "system.admin")

    def _require_read(self, session: Session) -> None:
        if not session.has_any(self._READ_PERMS):
            from ..permissions import PermissionDenied
            raise PermissionDenied("resource.read")

    def list_categories(self, session: Session) -> list[dict]:
        self._require_read(session)
        conn = db.get_connection()
        return [dict(r) for r in conn.execute(
            "SELECT id, name, parent_id FROM resource_categories ORDER BY name")]

    def search(self, session: Session, *, text: str | None = None,
               status: str | None = None) -> list[Resource]:
        self._require_read(session)
        conn = db.get_connection()
        sql = """
            SELECT r.id, r.title, c.name AS category, r.status,
                   (SELECT MAX(version_no) FROM resource_versions v WHERE v.resource_id=r.id) AS latest,
                   (SELECT v.version_no FROM resource_versions v
                     WHERE v.resource_id=r.id AND v.status='published' LIMIT 1) AS published
            FROM resources r LEFT JOIN resource_categories c ON c.id=r.category_id
        """
        where, args = [], []
        if text:
            where.append("r.title LIKE ?")
            args.append(f"%{text}%")
        if status:
            where.append("r.status=?")
            args.append(status)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY r.title"
        rows = conn.execute(sql, args).fetchall()
        return [Resource(id=r["id"], title=r["title"], category=r["category"],
                         status=r["status"], latest_version=r["latest"],
                         published_version=r["published"]) for r in rows]

    @requires("resource.write")
    def create_resource(self, session: Session, title: str,
                        category_id: int | None = None) -> Resource:
        if not title.strip():
            raise BizError("BAD_TITLE", "Title required.")
        with db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO resources(title, category_id, owner_id) VALUES (?, ?, ?)",
                (title.strip(), category_id, session.user_id))
            new_id = cur.lastrowid
            conn.execute(
                "INSERT INTO resources_fts(rowid, title, summary) VALUES (?, ?, ?)",
                (new_id, title, ""))
        audit.record(session.user_id, "resource", new_id, "create",
                     {"title": title, "category_id": category_id})
        return self._get(new_id)

    @requires("resource.write")
    def add_version(self, session: Session, resource_id: int,
                    summary: str, body: str) -> ResourceVersion:
        conn = db.get_connection()
        existing_max = conn.execute(
            "SELECT COALESCE(MAX(version_no), 0) AS m FROM resource_versions WHERE resource_id=?",
            (resource_id,)).fetchone()["m"]
        next_no = existing_max + 1
        with db.transaction() as conn:
            cur = conn.execute(
                """INSERT INTO resource_versions(resource_id, version_no, summary, body, status)
                   VALUES (?, ?, ?, ?, 'draft')""",
                (resource_id, next_no, summary, body))
            vid = cur.lastrowid
        audit.record(session.user_id, "resource_version", vid, "add",
                     {"resource_id": resource_id, "version_no": next_no})
        return self._get_version(vid)

    @requires("resource.publish")
    def publish_version(self, session: Session, version_id: int,
                        semver_level: str = "minor") -> ResourceVersion:
        """Publish a version. For catalog-attached resources, the catalog
        review must be `approved` AND the catalog semver is bumped atomically
        in the same transaction so publication and version increment cannot
        diverge.
        """
        from .catalog import bump as _semver_bump
        conn = db.get_connection()
        v = conn.execute(
            "SELECT resource_id, status FROM resource_versions WHERE id=?",
            (version_id,)).fetchone()
        if not v:
            raise BizError("VERSION_NOT_FOUND", "Version not found.")
        # Catalog governance gate: every published resource MUST pass through
        # the unified catalog review workflow. If the resource has no catalog
        # row, refuse — the operator must explicitly attach + submit + review
        # so the action is captured in audit, not implicitly bypassed.
        cat = conn.execute(
            "SELECT review_state, semver FROM resource_catalog WHERE resource_id=?",
            (v["resource_id"],)).fetchone()
        if cat is None:
            raise BizError(
                "NOT_ATTACHED",
                "Resource is not attached to the unified catalog. "
                "Attach + submit_for_review + review-approve before publishing.")
        if cat["review_state"] != "approved":
            raise BizError(
                "NOT_APPROVED",
                f"Resource is in catalog review state '{cat['review_state']}'; "
                "reviewer approval is required before publishing.")
        new_semver: str | None = None
        with db.transaction() as conn:
            conn.execute(
                "UPDATE resource_versions SET status='superseded' "
                "WHERE resource_id=? AND status='published'", (v["resource_id"],))
            conn.execute(
                "UPDATE resource_versions SET status='published', "
                "published_at=datetime('now'), published_by=? WHERE id=?",
                (session.user_id, version_id))
            if cat is not None:
                new_semver = _semver_bump(cat["semver"], semver_level)
                conn.execute(
                    "UPDATE resource_catalog SET semver=? WHERE resource_id=?",
                    (new_semver, v["resource_id"]))
        audit.record(session.user_id, "resource_version", version_id, "publish",
                     {"semver": new_semver, "semver_level": semver_level}
                     if new_semver else {})
        if new_semver:
            audit.record(session.user_id, "resource", v["resource_id"],
                         "semver_bump",
                         {"semver": new_semver, "level": semver_level,
                          "via": "publish_version"})
        events.bus.publish(events.RESOURCE_PUBLISHED,
                           {"version_id": version_id,
                            "resource_id": v["resource_id"],
                            "operator": session.full_name,
                            "semver": new_semver})
        return self._get_version(version_id)

    @requires("resource.publish")
    def unpublish_version(self, session: Session, version_id: int) -> ResourceVersion:
        with db.transaction() as conn:
            conn.execute(
                "UPDATE resource_versions SET status='unpublished' WHERE id=?",
                (version_id,))
        audit.record(session.user_id, "resource_version", version_id, "unpublish", {})
        events.bus.publish(events.RESOURCE_UNPUBLISHED, {"version_id": version_id})
        return self._get_version(version_id)

    @requires("resource.write")
    def place_on_hold(self, session: Session, resource_id: int, reason: str) -> Resource:
        with db.transaction() as conn:
            conn.execute("UPDATE resources SET status='on_hold' WHERE id=?", (resource_id,))
        audit.record(session.user_id, "resource", resource_id, "hold", {"reason": reason})
        events.bus.publish(events.RESOURCE_HELD, {"resource_id": resource_id, "reason": reason})
        return self._get(resource_id)

    @requires("resource.write")
    def release_hold(self, session: Session, resource_id: int) -> Resource:
        with db.transaction() as conn:
            conn.execute("UPDATE resources SET status='active' WHERE id=?", (resource_id,))
        audit.record(session.user_id, "resource", resource_id, "release_hold", {})
        return self._get(resource_id)

    def list_versions(self, session: Session, resource_id: int) -> list[ResourceVersion]:
        self._require_read(session)
        conn = db.get_connection()
        rows = conn.execute(
            "SELECT * FROM resource_versions WHERE resource_id=? ORDER BY version_no DESC",
            (resource_id,)).fetchall()
        return [self._row_to_version(r) for r in rows]

    def _get(self, resource_id: int) -> Resource:
        conn = db.get_connection()
        r = conn.execute("""
            SELECT r.id, r.title, c.name AS category, r.status,
                   (SELECT MAX(version_no) FROM resource_versions v WHERE v.resource_id=r.id) AS latest,
                   (SELECT v.version_no FROM resource_versions v
                     WHERE v.resource_id=r.id AND v.status='published' LIMIT 1) AS published
            FROM resources r LEFT JOIN resource_categories c ON c.id=r.category_id
            WHERE r.id=?""", (resource_id,)).fetchone()
        return Resource(id=r["id"], title=r["title"], category=r["category"],
                        status=r["status"], latest_version=r["latest"],
                        published_version=r["published"])

    def _get_version(self, vid: int) -> ResourceVersion:
        conn = db.get_connection()
        r = conn.execute("SELECT * FROM resource_versions WHERE id=?", (vid,)).fetchone()
        return self._row_to_version(r)

    def _row_to_version(self, r) -> ResourceVersion:
        return ResourceVersion(
            id=r["id"], resource_id=r["resource_id"], version_no=r["version_no"],
            summary=r["summary"], body=r["body"], status=r["status"],
            published_at=r["published_at"])
