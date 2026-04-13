"""Application settings & synonym sets."""
from __future__ import annotations

from .. import audit, db
from ..permissions import Session, requires


class SettingsService:

    def get(self, key: str) -> str | None:
        conn = db.get_connection()
        r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None

    @requires("system.admin")
    def set(self, session: Session, key: str, value: str) -> None:
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        audit.record(session.user_id, "settings", key, "set", {"value": value})

    def list_synonyms(self) -> list[dict]:
        conn = db.get_connection()
        return [dict(r) for r in conn.execute(
            "SELECT id, term, alt_term FROM synonyms ORDER BY term")]

    @requires("system.admin")
    def add_synonym(self, session: Session, term: str, alt: str) -> int:
        with db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO synonyms(term, alt_term) VALUES(?, ?) "
                "ON CONFLICT DO NOTHING", (term.lower(), alt.lower()))
        audit.record(session.user_id, "synonym", f"{term}->{alt}", "add", {})
        return cur.lastrowid

    @requires("system.admin")
    def remove_synonym(self, session: Session, syn_id: int) -> None:
        with db.transaction() as conn:
            conn.execute("DELETE FROM synonyms WHERE id=?", (syn_id,))
        audit.record(session.user_id, "synonym", syn_id, "remove", {})
