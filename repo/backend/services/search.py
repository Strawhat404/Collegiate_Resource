"""Universal search across students, resources, employers, and cases."""
from __future__ import annotations
import json

from .. import config, db
from ..models import SearchHit
from ..permissions import Session

try:
    from rapidfuzz import fuzz
    HAVE_FUZZ = True
except Exception:  # pragma: no cover
    HAVE_FUZZ = False


class SearchService:

    _READ_PERMS = ("student.write", "student.import", "student.pii.read",
                   "housing.write", "housing.read",
                   "resource.write", "resource.publish", "resource.read",
                   "compliance.review", "compliance.violation",
                   "compliance.evidence", "compliance.action",
                   "catalog.write", "catalog.review", "catalog.publish",
                   "system.admin")

    def global_search(self, session: Session, query: str, *,
                      types: set[str] | None = None,
                      fuzzy: bool = True, limit: int = 20,
                      include_hidden: bool = False) -> list[SearchHit]:
        if not session.has_any(self._READ_PERMS):
            from ..permissions import PermissionDenied
            raise PermissionDenied("search.read")
        types = types or {"student", "resource", "employer", "case"}
        expanded = self._expand_synonyms(query)
        hits: list[SearchHit] = []
        if "student" in types:
            hits.extend(self._search_students(expanded))
        if "resource" in types:
            hits.extend(self._search_resources(expanded))
        if "employer" in types:
            hits.extend(self._search_employers(expanded, include_hidden=include_hidden))
        if "case" in types:
            hits.extend(self._search_cases(expanded))
        if fuzzy and HAVE_FUZZ:
            for h in hits:
                h.score = max(h.score,
                              fuzz.token_set_ratio(query.lower(), h.title.lower()))
            hits = [h for h in hits if h.score >= config.FUZZY_THRESHOLD] or hits
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]

    def _expand_synonyms(self, query: str) -> str:
        conn = db.get_connection()
        parts = []
        for tok in query.split():
            base = tok.lower()
            alts = [r["alt_term"] for r in conn.execute(
                "SELECT alt_term FROM synonyms WHERE term=?", (base,))]
            parts.append("(" + " OR ".join([base] + alts) + ")" if alts else tok)
        return " ".join(parts)

    def _search_students(self, q: str) -> list[SearchHit]:
        conn = db.get_connection()
        try:
            rows = conn.execute(
                "SELECT s.id, s.full_name, s.student_id_ext, s.college, "
                "       students_fts.rank "
                "FROM students_fts JOIN students s ON s.id = students_fts.rowid "
                "WHERE students_fts MATCH ? LIMIT 50", (q,)).fetchall()
        except Exception:
            rows = conn.execute(
                "SELECT id, full_name, student_id_ext, college, 0 AS rank "
                "FROM students WHERE full_name LIKE ? OR student_id_ext LIKE ? LIMIT 50",
                (f"%{q}%", f"%{q}%")).fetchall()
        return [SearchHit(
            entity_type="student", entity_id=r["id"],
            title=r["full_name"], subtitle=f"{r['student_id_ext']} · {r['college'] or ''}",
            score=80.0, open_action="open_student") for r in rows]

    def _search_resources(self, q: str) -> list[SearchHit]:
        conn = db.get_connection()
        try:
            rows = conn.execute(
                "SELECT r.id, r.title, r.status FROM resources_fts "
                "JOIN resources r ON r.id = resources_fts.rowid "
                "WHERE resources_fts MATCH ? LIMIT 50", (q,)).fetchall()
        except Exception:
            rows = conn.execute(
                "SELECT id, title, status FROM resources WHERE title LIKE ? LIMIT 50",
                (f"%{q}%",)).fetchall()
        return [SearchHit(entity_type="resource", entity_id=r["id"],
                          title=r["title"], subtitle=f"status: {r['status']}",
                          score=80.0, open_action="open_resource") for r in rows]

    def _search_employers(self, q: str, include_hidden: bool = False) -> list[SearchHit]:
        conn = db.get_connection()
        try:
            rows = conn.execute(
                "SELECT e.id, e.name, e.status FROM employers_fts "
                "JOIN employers e ON e.id = employers_fts.rowid "
                "WHERE employers_fts MATCH ? LIMIT 50", (q,)).fetchall()
        except Exception:
            rows = conn.execute(
                "SELECT id, name, status FROM employers WHERE name LIKE ? LIMIT 50",
                (f"%{q}%",)).fetchall()
        # Default search excludes throttled / taken-down / suspended employers
        # AND any employer carrying an active violation_action of those types.
        if not include_hidden:
            hidden_status = {"throttled", "taken_down", "suspended"}
            hidden_ids = {r["employer_id"] for r in conn.execute(
                "SELECT DISTINCT employer_id FROM violation_actions "
                "WHERE revoked_at IS NULL "
                "AND (ends_at IS NULL OR ends_at > datetime('now')) "
                "AND action IN ('takedown','suspend','throttle')")}
            rows = [r for r in rows
                    if r["status"] not in hidden_status
                    and r["id"] not in hidden_ids]
        return [SearchHit(entity_type="employer", entity_id=r["id"],
                          title=r["name"], subtitle=f"status: {r['status']}",
                          score=80.0, open_action="open_employer") for r in rows]

    def _search_cases(self, q: str) -> list[SearchHit]:
        conn = db.get_connection()
        rows = conn.execute(
            "SELECT c.id, e.name, c.kind, c.state FROM employer_cases c "
            "JOIN employers e ON e.id=c.employer_id "
            "WHERE e.name LIKE ? OR c.notes LIKE ? LIMIT 50",
            (f"%{q}%", f"%{q}%")).fetchall()
        return [SearchHit(entity_type="case", entity_id=r["id"],
                          title=f"{r['name']} — {r['kind']}",
                          subtitle=f"state: {r['state']}",
                          score=75.0, open_action="open_case") for r in rows]

    # ---- saved searches ---------------------------------------------------

    def save_search(self, session: Session, name: str, scope: str,
                    query: dict) -> int:
        with db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO saved_searches(owner_id, name, scope, query_json, pinned) "
                "VALUES (?, ?, ?, ?, 0)",
                (session.user_id, name, scope, json.dumps(query)))
        return cur.lastrowid

    def list_saved(self, session: Session) -> list[dict]:
        conn = db.get_connection()
        rows = conn.execute(
            "SELECT id, name, scope, query_json, pinned FROM saved_searches "
            "WHERE owner_id=? ORDER BY pinned DESC, name", (session.user_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["query"] = json.loads(d.pop("query_json"))
            out.append(d)
        return out

    def pin(self, session: Session, search_id: int, pinned: bool) -> None:
        with db.transaction() as conn:
            conn.execute("UPDATE saved_searches SET pinned=? WHERE id=? AND owner_id=?",
                         (1 if pinned else 0, search_id, session.user_id))

    def delete_saved(self, session: Session, search_id: int) -> None:
        with db.transaction() as conn:
            conn.execute("DELETE FROM saved_searches WHERE id=? AND owner_id=?",
                         (search_id, session.user_id))
