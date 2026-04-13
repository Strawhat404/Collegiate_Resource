"""Evidence files, sensitive-word checks, violation actions.

Extends the chunk-1 EmployerComplianceService without modifying it.
"""
from __future__ import annotations
import hashlib
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .. import audit, config, db, events
from ..permissions import Session, requires
from .auth import BizError


VIOLATION_TAKEDOWN = "VIOLATION_TAKEDOWN"
VIOLATION_SUSPEND = "VIOLATION_SUSPEND"
VIOLATION_THROTTLE = "VIOLATION_THROTTLE"


@dataclass
class EvidenceFile:
    id: int
    employer_id: int
    case_id: int | None
    file_name: str
    stored_path: str
    sha256: str
    size_bytes: int
    uploaded_at: str
    retain_until: str


@dataclass
class ViolationAction:
    id: int
    employer_id: int
    action: str
    duration_days: int | None
    starts_at: str
    ends_at: str | None
    reason: str | None
    revoked_at: str | None


class EvidenceService:

    @requires("compliance.evidence")
    def upload(self, session: Session, employer_id: int, source_path: str | Path,
               case_id: int | None = None) -> EvidenceFile:
        src = Path(source_path)
        if not src.is_file():
            raise BizError("FILE_MISSING", f"file not found: {src}")
        size = src.stat().st_size
        sha = self._sha256(src)
        # Layout: evidence/<employer_id>/<sha-prefix>/<uuid>__<filename>
        target_dir = config.evidence_dir() / str(employer_id) / sha[:2]
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{uuid.uuid4().hex}__{src.name}"
        shutil.copy2(src, target)
        retain_until = (datetime.utcnow()
                        + timedelta(days=365 * config.EVIDENCE_RETENTION_YEARS)
                        ).date().isoformat()
        rel = str(target.relative_to(config.evidence_dir()))
        with db.transaction() as conn:
            cur = conn.execute(
                """INSERT INTO employer_evidence
                       (employer_id, case_id, file_name, stored_path,
                        size_bytes, sha256, uploaded_by, retain_until)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (employer_id, case_id, src.name, rel, size, sha,
                 session.user_id, retain_until))
            eid = cur.lastrowid
        audit.record(session.user_id, "evidence", eid, "upload",
                     {"employer_id": employer_id, "sha256": sha,
                      "size": size, "name": src.name})
        # Notify (best-effort)
        try:
            from . import _notify  # type: ignore
        except Exception:
            pass
        return EvidenceFile(
            id=eid, employer_id=employer_id, case_id=case_id,
            file_name=src.name, stored_path=rel, sha256=sha,
            size_bytes=size, uploaded_at=datetime.utcnow().isoformat(),
            retain_until=retain_until)

    _READ_PERMS = ("compliance.review", "compliance.violation",
                   "compliance.evidence", "compliance.action", "system.admin")

    def list_for_employer(self, employer_id: int,
                          session: "Session | None" = None) -> list[EvidenceFile]:
        # Function-level authorization: callers must hold a compliance perm.
        # ``session`` is keyword-optional for back-compat; when omitted, we
        # refuse rather than silently allow unauthenticated reads.
        if session is None or not session.has_any(self._READ_PERMS):
            from ..permissions import PermissionDenied
            raise PermissionDenied("compliance.read")
        conn = db.get_connection()
        rows = conn.execute(
            "SELECT * FROM employer_evidence WHERE employer_id=? "
            "ORDER BY uploaded_at DESC", (employer_id,)).fetchall()
        return [EvidenceFile(
            id=r["id"], employer_id=r["employer_id"], case_id=r["case_id"],
            file_name=r["file_name"], stored_path=r["stored_path"],
            sha256=r["sha256"], size_bytes=r["size_bytes"],
            uploaded_at=r["uploaded_at"], retain_until=r["retain_until"]
        ) for r in rows]

    def verify(self, evidence_id: int,
               session: "Session | None" = None) -> bool:
        """Re-hash the stored file and compare against the recorded digest.

        Function-level authorization: callers must hold a compliance perm.
        ``session`` is keyword-optional for back-compat with internal call
        sites; when omitted, the call is refused rather than silently
        allowing an unauthenticated read of evidence presence + integrity.
        """
        if session is None or not session.has_any(self._READ_PERMS):
            from ..permissions import PermissionDenied
            raise PermissionDenied("compliance.read")
        conn = db.get_connection()
        r = conn.execute(
            "SELECT stored_path, sha256 FROM employer_evidence WHERE id=?",
            (evidence_id,)).fetchone()
        if not r:
            return False
        p = config.evidence_dir() / r["stored_path"]
        return p.is_file() and self._sha256(p) == r["sha256"]

    @requires("compliance.evidence")
    def purge_expired(self, session: Session) -> int:
        """Delete files whose retention window has passed (≥7 years).

        Returns number of files removed. Audit row records each deletion.
        """
        conn = db.get_connection()
        today = datetime.utcnow().date().isoformat()
        rows = conn.execute(
            "SELECT id, stored_path FROM employer_evidence WHERE retain_until <= ?",
            (today,)).fetchall()
        n = 0
        for r in rows:
            p = config.evidence_dir() / r["stored_path"]
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                continue
            with db.transaction() as conn:
                conn.execute("DELETE FROM employer_evidence WHERE id=?", (r["id"],))
            audit.record(session.user_id, "evidence", r["id"], "purge",
                         {"path": r["stored_path"]})
            n += 1
        return n

    @staticmethod
    def _sha256(p: Path) -> str:
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()


# ---------------------------------------------------------------------------

class SensitiveWordService:
    """Offline sensitive-word dictionary.

    Used to scan employer-supplied text for prohibited terms before approval.
    Words are stored in `sensitive_words`; matching is case-insensitive,
    word-boundary aware.
    """

    def list(self) -> list[dict]:
        return [dict(r) for r in db.get_connection().execute(
            "SELECT id, word, severity, category FROM sensitive_words "
            "ORDER BY severity DESC, word")]

    @requires("system.admin")
    def add(self, session: Session, word: str, severity: str = "medium",
            category: str | None = None) -> int:
        if severity not in ("low", "medium", "high"):
            raise BizError("BAD_SEVERITY", severity)
        with db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO sensitive_words(word, severity, category) "
                "VALUES (?, ?, ?) ON CONFLICT(word) DO UPDATE SET "
                "severity=excluded.severity, category=excluded.category",
                (word.lower(), severity, category))
        audit.record(session.user_id, "sensitive_word", word, "upsert",
                     {"severity": severity})
        return cur.lastrowid

    @requires("system.admin")
    def remove(self, session: Session, word_id: int) -> None:
        with db.transaction() as conn:
            conn.execute("DELETE FROM sensitive_words WHERE id=?", (word_id,))
        audit.record(session.user_id, "sensitive_word", word_id, "remove", {})

    def scan(self, text: str) -> list[dict]:
        """Return list of {word, severity, category, position} matches."""
        if not text:
            return []
        words = self.list()
        hits: list[dict] = []
        lower = text.lower()
        for w in words:
            for m in re.finditer(rf"\b{re.escape(w['word'].lower())}\b", lower):
                hits.append({"word": w["word"], "severity": w["severity"],
                             "category": w["category"], "position": m.start()})
        return hits


# ---------------------------------------------------------------------------

class ViolationActionService:

    @requires("compliance.action")
    def takedown(self, session: Session, employer_id: int, reason: str) -> int:
        return self._record(session, employer_id, "takedown", None, reason)

    @requires("compliance.action")
    def suspend(self, session: Session, employer_id: int, days: int,
                reason: str) -> int:
        if days not in (30, 60, 180):
            raise BizError("BAD_DURATION", "duration must be 30, 60, or 180 days")
        return self._record(session, employer_id, "suspend", days, reason)

    @requires("compliance.action")
    def throttle(self, session: Session, employer_id: int, reason: str) -> int:
        """Hide content from default searches (i.e., set employer.status='throttled')."""
        with db.transaction() as conn:
            conn.execute("UPDATE employers SET status='throttled' WHERE id=?",
                         (employer_id,))
        return self._record(session, employer_id, "throttle", None, reason)

    @requires("compliance.action")
    def revoke(self, session: Session, action_id: int, reason: str = "") -> None:
        conn = db.get_connection()
        a = conn.execute(
            "SELECT employer_id, action FROM violation_actions WHERE id=?",
            (action_id,)).fetchone()
        if not a:
            raise BizError("NOT_FOUND", "action not found")
        with db.transaction() as conn:
            conn.execute(
                "UPDATE violation_actions SET revoked_at=datetime('now') "
                "WHERE id=?", (action_id,))
            if a["action"] in ("takedown", "throttle"):
                conn.execute(
                    "UPDATE employers SET status='approved' WHERE id=?",
                    (a["employer_id"],))
        audit.record(session.user_id, "violation_action", action_id, "revoke",
                     {"reason": reason})

    _READ_PERMS = ("compliance.review", "compliance.violation",
                   "compliance.evidence", "compliance.action", "system.admin")

    def list_for_employer(self, employer_id: int,
                          active_only: bool = False,
                          session: "Session | None" = None) -> list[ViolationAction]:
        # Function-level authorization: callers must hold a compliance perm.
        # ``session`` is keyword-optional for back-compat with the internal
        # ``is_hidden_from_default_search`` call site (which is invoked by
        # already-authenticated search code paths).
        if session is None or not session.has_any(self._READ_PERMS):
            from ..permissions import PermissionDenied
            raise PermissionDenied("compliance.read")
        conn = db.get_connection()
        sql = "SELECT * FROM violation_actions WHERE employer_id=?"
        args: list = [employer_id]
        if active_only:
            sql += (" AND revoked_at IS NULL "
                    "AND (ends_at IS NULL OR ends_at > datetime('now'))")
        sql += " ORDER BY starts_at DESC"
        return [ViolationAction(
            id=r["id"], employer_id=r["employer_id"], action=r["action"],
            duration_days=r["duration_days"], starts_at=r["starts_at"],
            ends_at=r["ends_at"], reason=r["reason"], revoked_at=r["revoked_at"]
        ) for r in conn.execute(sql, args).fetchall()]

    def is_hidden_from_default_search(self, employer_id: int) -> bool:
        # Internal predicate used by search code to apply default hiding.
        # Bypasses the user-facing authz on ``list_for_employer`` because it
        # exposes only a boolean derived from system-owned moderation state,
        # not the underlying action records.
        conn = db.get_connection()
        row = conn.execute(
            "SELECT 1 FROM violation_actions WHERE employer_id=? "
            "AND action IN ('takedown','throttle','suspend') "
            "AND revoked_at IS NULL "
            "AND (ends_at IS NULL OR ends_at > datetime('now')) LIMIT 1",
            (employer_id,)).fetchone()
        return row is not None

    # -------------------------------------------------------------------

    def _record(self, session: Session, employer_id: int, action: str,
                duration_days: int | None, reason: str) -> int:
        ends_at = None
        if duration_days:
            ends_at = (datetime.utcnow()
                       + timedelta(days=duration_days)).isoformat()
        with db.transaction() as conn:
            cur = conn.execute(
                """INSERT INTO violation_actions
                       (employer_id, action, duration_days, ends_at,
                        reason, actor_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (employer_id, action, duration_days, ends_at,
                 reason, session.user_id))
            aid = cur.lastrowid
            if action == "takedown":
                conn.execute("UPDATE employers SET status='taken_down' WHERE id=?",
                             (employer_id,))
            elif action == "suspend":
                conn.execute("UPDATE employers SET status='suspended' WHERE id=?",
                             (employer_id,))
        audit.record(session.user_id, "violation_action", aid, action,
                     {"employer_id": employer_id, "days": duration_days,
                      "reason": reason})
        ev = {"takedown": VIOLATION_TAKEDOWN, "suspend": VIOLATION_SUSPEND,
              "throttle": VIOLATION_THROTTLE}[action]
        events.bus.publish(ev, {"employer_id": employer_id, "action_id": aid,
                                "days": duration_days})
        return aid
