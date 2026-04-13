"""Notification & in-app messaging."""
from __future__ import annotations
import json
import re
from datetime import datetime, timedelta

from .. import audit, config, db, events
from ..models import NotificationMessage
from ..permissions import Session, requires
from .auth import BizError


_VAR_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class NotificationService:

    # ---- templates --------------------------------------------------------

    def list_templates(self, session: Session) -> list[dict]:
        conn = db.get_connection()
        return [dict(r) for r in conn.execute(
            "SELECT id, name, subject, body, variables_json FROM notif_templates "
            "ORDER BY name")]

    @requires("notification.admin")
    def upsert_template(self, session: Session, name: str, subject: str,
                        body: str) -> int:
        variables = sorted(set(_VAR_RE.findall(subject + " " + body)))
        unknown = [v for v in variables if v not in config.TEMPLATE_VARIABLES]
        if unknown:
            audit.record(session.user_id, "notif_template", name,
                         "warn_unknown_vars", {"unknown": unknown})
        with db.transaction() as conn:
            existing = conn.execute(
                "SELECT id FROM notif_templates WHERE name=?", (name,)).fetchone()
            vj = json.dumps(variables)
            if existing:
                conn.execute(
                    "UPDATE notif_templates SET subject=?, body=?, variables_json=? "
                    "WHERE id=?", (subject, body, vj, existing["id"]))
                tid = existing["id"]
            else:
                cur = conn.execute(
                    "INSERT INTO notif_templates(name, subject, body, variables_json) "
                    "VALUES (?, ?, ?, ?)", (name, subject, body, vj))
                tid = cur.lastrowid
        audit.record(session.user_id, "notif_template", tid, "upsert",
                     {"name": name})
        return tid

    # ---- rules ------------------------------------------------------------

    def list_rules(self, session: Session) -> list[dict]:
        conn = db.get_connection()
        return [dict(r) for r in conn.execute(
            "SELECT id, name, kind, event_name, cron_spec, template_id, "
            "audience_query, enabled FROM notif_rules ORDER BY name")]

    @requires("notification.admin")
    def set_rule_enabled(self, session: Session, rule_id: int, enabled: bool) -> None:
        with db.transaction() as conn:
            conn.execute("UPDATE notif_rules SET enabled=? WHERE id=?",
                         (1 if enabled else 0, rule_id))
        audit.record(session.user_id, "notif_rule", rule_id, "set_enabled",
                     {"enabled": enabled})

    # ---- direct enqueue --------------------------------------------------

    def enqueue(self, session: Session, *, template_name: str,
                audience_user_ids: list[int], variables: dict[str, str],
                scheduled_for: datetime | None = None) -> list[int]:
        conn = db.get_connection()
        tpl = conn.execute(
            "SELECT id, subject, body FROM notif_templates WHERE name=?",
            (template_name,)).fetchone()
        if not tpl:
            raise BizError("TEMPLATE_NOT_FOUND", "Unknown template.")
        subject = render(tpl["subject"], variables)
        body = render(tpl["body"], variables)
        msg_ids: list[int] = []
        with db.transaction() as conn:
            for uid in audience_user_ids:
                cur = conn.execute(
                    """INSERT INTO notif_messages(template_id, audience_user_id,
                          subject, body_rendered, status, scheduled_for)
                       VALUES (?, ?, ?, ?, 'queued', ?)""",
                    (tpl["id"], uid, subject, body,
                     scheduled_for.isoformat() if scheduled_for else None))
                msg_ids.append(cur.lastrowid)
        return msg_ids

    # ---- inbox ------------------------------------------------------------

    def inbox(self, session: Session, *, only_unread: bool = False,
              limit: int = 100) -> list[NotificationMessage]:
        conn = db.get_connection()
        sql = """SELECT m.id, t.name AS tname, m.subject, m.body_rendered,
                         m.status, m.attempts, m.scheduled_for, m.created_at, m.read_at
                  FROM notif_messages m JOIN notif_templates t ON t.id = m.template_id
                  WHERE m.audience_user_id = ? AND m.status='delivered'"""
        args = [session.user_id]
        if only_unread:
            sql += " AND m.read_at IS NULL"
        sql += " ORDER BY m.created_at DESC LIMIT ?"
        args.append(limit)
        rows = conn.execute(sql, args).fetchall()
        return [NotificationMessage(
            id=r["id"], template_name=r["tname"], subject=r["subject"],
            body=r["body_rendered"], status=r["status"], attempts=r["attempts"],
            scheduled_for=r["scheduled_for"], created_at=r["created_at"],
            read_at=r["read_at"]) for r in rows]

    def unread_count(self, session: Session) -> int:
        conn = db.get_connection()
        return conn.execute(
            "SELECT COUNT(*) AS n FROM notif_messages WHERE audience_user_id=? "
            "AND status='delivered' AND read_at IS NULL",
            (session.user_id,)).fetchone()["n"]

    def mark_read(self, session: Session, message_id: int) -> None:
        with db.transaction() as conn:
            conn.execute(
                "UPDATE notif_messages SET read_at=datetime('now') "
                "WHERE id=? AND audience_user_id=?",
                (message_id, session.user_id))

    # ---- dispatcher (called by worker) -----------------------------------

    def drain_queue(self) -> int:
        """Deliver due messages. Returns number delivered.

        Side-effect: any ``notif_enqueue_failures`` rows whose 5-minute retry
        window has elapsed are auto-replayed before draining, so the standard
        cadence applies even without an operator-driven retry.
        """
        self._auto_replay_failures()
        conn = db.get_connection()
        now_iso = datetime.utcnow().isoformat(timespec="seconds")
        rows = conn.execute(
            "SELECT id FROM notif_messages "
            "WHERE status='queued' AND (scheduled_for IS NULL OR scheduled_for <= ?)",
            (now_iso,)).fetchall()
        delivered = 0
        for r in rows:
            try:
                with db.transaction() as conn:
                    conn.execute(
                        "UPDATE notif_messages SET status='delivered', "
                        "attempts=attempts+1, last_attempt_at=datetime('now') "
                        "WHERE id=?", (r["id"],))
                delivered += 1
            except Exception:
                self._handle_failure(r["id"])
        return delivered

    def _handle_failure(self, msg_id: int) -> None:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT attempts FROM notif_messages WHERE id=?", (msg_id,)).fetchone()
        if not row:
            return
        attempts = row["attempts"] + 1
        next_status = "dead" if attempts >= config.NOTIF_RETRY_LIMIT else "queued"
        next_for = (datetime.utcnow()
                    + timedelta(seconds=config.NOTIF_RETRY_INTERVAL_SECONDS)).isoformat()
        try:
            with db.transaction() as conn:
                conn.execute(
                    "UPDATE notif_messages SET attempts=?, status=?, "
                    "scheduled_for=?, last_attempt_at=datetime('now') WHERE id=?",
                    (attempts, next_status, next_for, msg_id))
        except Exception:
            pass

    def fire_scheduled_rules(self, now: datetime | None = None) -> int:
        """Evaluate all `kind='schedule'` rules whose cron spec matches *now*.

        Subset cron grammar: ``M H * * *`` (minute, hour, *, *, *) — fires
        once per matching minute. Last-fire timestamp lives in
        `notif_rule_runs` so a tick that lands inside the same minute does
        not re-fire. Returns number of rules fired.
        """
        ts = (now or datetime.now()).replace(second=0, microsecond=0)
        conn = db.get_connection()
        rows = conn.execute(
            "SELECT id, name, cron_spec, template_id, audience_query "
            "FROM notif_rules WHERE enabled=1 AND kind='schedule'").fetchall()
        fired = 0
        for r in rows:
            if not _cron_matches(r["cron_spec"], ts):
                continue
            last = conn.execute(
                "SELECT last_fired_at FROM notif_rule_runs WHERE rule_id=?",
                (r["id"],)).fetchone()
            this_min = ts.isoformat(timespec="minutes")
            if last and last["last_fired_at"] == this_min:
                continue
            audience = _resolve_audience(json.loads(r["audience_query"] or "{}"))
            if not audience:
                continue
            tpl_subject = _template_subject(r["template_id"])
            tpl_body = _template_body(r["template_id"])
            vars_ = {"Today": ts.strftime("%m/%d/%Y")}
            with db.transaction() as conn:
                for uid in audience:
                    conn.execute(
                        """INSERT INTO notif_messages(template_id, audience_user_id,
                              subject, body_rendered, status)
                           VALUES (?, ?, ?, ?, 'queued')""",
                        (r["template_id"], uid,
                         render(tpl_subject, vars_), render(tpl_body, vars_)))
                conn.execute(
                    "INSERT INTO notif_rule_runs(rule_id, last_fired_at) "
                    "VALUES (?, ?) ON CONFLICT(rule_id) DO UPDATE SET "
                    "last_fired_at=excluded.last_fired_at",
                    (r["id"], this_min))
            fired += 1
        return fired

    def retry_failed(self, session: Session) -> int:
        """Re-queue dead-letter rows AND replay any enqueue-stage failures."""
        with db.transaction() as conn:
            cur = conn.execute(
                "UPDATE notif_messages SET status='queued', attempts=0, "
                "scheduled_for=NULL WHERE status='dead'")
            requeued = cur.rowcount
        # Operator-triggered retry replays ALL outstanding enqueue failures
        # immediately (regardless of the 5-minute window).
        replayed = self._replay_enqueue_failures(force=True)
        return requeued + replayed

    def _auto_replay_failures(self) -> int:
        """Auto-replay only enqueue failures whose 5-minute window elapsed."""
        return self._replay_enqueue_failures(force=False)

    # Hard cap on enqueue-failure replay attempts. Matches the
    # delivery-side 3-attempt budget documented in design.md §7 so a
    # permanently-broken row is moved to a dead state instead of being
    # retried indefinitely on every dispatcher tick.
    _ENQUEUE_REPLAY_MAX_ATTEMPTS = 3

    def _replay_enqueue_failures(self, *, force: bool) -> int:
        conn = db.get_connection()
        base_cols = ("id, template_id, audience_user_id, subject, "
                     "body_rendered, attempts")
        cap = self._ENQUEUE_REPLAY_MAX_ATTEMPTS
        if force:
            rows = conn.execute(
                f"SELECT {base_cols} FROM notif_enqueue_failures "
                "WHERE replayed_at IS NULL AND dead_at IS NULL "
                "AND attempts < ?",
                (cap,)).fetchall()
        else:
            cutoff = (datetime.utcnow()
                      - timedelta(seconds=config.NOTIF_RETRY_INTERVAL_SECONDS)
                      ).isoformat(timespec="seconds")
            rows = conn.execute(
                f"SELECT {base_cols} FROM notif_enqueue_failures "
                "WHERE replayed_at IS NULL AND dead_at IS NULL "
                "AND attempts < ? AND created_at <= ?",
                (cap, cutoff)).fetchall()
        replayed = 0
        for r in rows:
            attempt_no = (r["attempts"] or 0) + 1
            try:
                with db.transaction() as conn:
                    conn.execute(
                        """INSERT INTO notif_messages(template_id, audience_user_id,
                              subject, body_rendered, status)
                           VALUES (?, ?, ?, ?, 'queued')""",
                        (r["template_id"], r["audience_user_id"],
                         r["subject"], r["body_rendered"]))
                    conn.execute(
                        "UPDATE notif_enqueue_failures SET "
                        "replayed_at=datetime('now'), attempts=? "
                        "WHERE id=?", (attempt_no, r["id"]))
                replayed += 1
            except Exception:
                # Bump the attempt counter even on failure so the cap is
                # enforced; mark dead once we've exhausted the budget.
                try:
                    with db.transaction() as conn:
                        if attempt_no >= cap:
                            conn.execute(
                                "UPDATE notif_enqueue_failures SET "
                                "attempts=?, dead_at=datetime('now') "
                                "WHERE id=?", (attempt_no, r["id"]))
                        else:
                            conn.execute(
                                "UPDATE notif_enqueue_failures SET "
                                "attempts=? WHERE id=?",
                                (attempt_no, r["id"]))
                except Exception:
                    pass
                continue
        return replayed


# ---- template rendering --------------------------------------------------

def render(text: str, variables: dict[str, str]) -> str:
    def repl(m: re.Match) -> str:
        return str(variables.get(m.group(1), ""))
    return _VAR_RE.sub(repl, text)


# ---- trigger-rule wiring -------------------------------------------------

def install_trigger_handlers(svc: NotificationService) -> None:
    """Subscribe to events on the bus and enqueue messages per rule.

    Enqueue is single-shot: a single INSERT is attempted with a short
    SQLite-busy retry budget. Any persistent failure is recorded in
    ``notif_enqueue_failures`` and re-attempted by the dispatcher on the
    standard ``config.NOTIF_RETRY_INTERVAL_SECONDS`` (5 minute) cadence —
    matching the spec, instead of blocking the event-bus thread for minutes.
    """
    import sqlite3
    import time

    # Tight in-process retry budget for transient SQLite "database is locked"
    # contention only — measured in milliseconds, not minutes. Persistent
    # failures escalate to ``notif_enqueue_failures`` so the dispatcher can
    # replay on the 5-minute cadence.
    _LOCK_RETRY_MAX = 3
    _LOCK_RETRY_BACKOFF_SECS = 0.05

    def _insert_with_retry(template_id: int, uid: int, subject: str,
                            body: str, event_name: str) -> None:
        last_err: Exception | None = None
        # First attempt scheduled_for=NULL (deliver ASAP). Failed inserts go
        # to the failures table with a scheduled_for 5 minutes in the future
        # so retry_failed() / drain_queue() pick them up on the standard
        # cadence.
        for attempt in range(1, _LOCK_RETRY_MAX + 1):
            try:
                with db.transaction() as conn:
                    conn.execute(
                        """INSERT INTO notif_messages(template_id, audience_user_id,
                              subject, body_rendered, status, attempts,
                              last_attempt_at)
                           VALUES (?, ?, ?, ?, 'queued', 0, datetime('now'))""",
                        (template_id, uid, subject, body))
                return
            except sqlite3.OperationalError as e:
                last_err = e
                time.sleep(_LOCK_RETRY_BACKOFF_SECS * attempt)
            except Exception as e:
                last_err = e
                break
        # Persist failure so the operator sees + can replay.
        try:
            with db.transaction() as conn:
                conn.execute(
                    """INSERT INTO notif_enqueue_failures(event_name, template_id,
                          audience_user_id, subject, body_rendered, error,
                          created_at)
                       VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
                    (event_name, template_id, uid, subject, body,
                     repr(last_err)))
        except Exception:
            # Last-ditch: log to audit (audit writes are independent of the
            # notif tables, so a notif-table outage does not block this).
            try:
                audit.record(0, "notif_enqueue", uid, "fail_persist",
                             {"event": event_name, "error": repr(last_err)})
            except Exception:
                pass

    def make_handler(event_name: str):
        def handler(payload: dict) -> None:
            conn = db.get_connection()
            rules = conn.execute(
                "SELECT r.id, r.template_id, r.audience_query, t.name AS tname "
                "FROM notif_rules r JOIN notif_templates t ON t.id=r.template_id "
                "WHERE r.enabled=1 AND r.kind='trigger' AND r.event_name=?",
                (event_name,)).fetchall()
            for rule in rules:
                aq = json.loads(rule["audience_query"] or "{}")
                user_ids = _resolve_audience(aq)
                if not user_ids:
                    continue
                vars_ = _payload_to_vars(payload)
                subject = render(_template_subject(rule["template_id"]), vars_)
                body = render(_template_body(rule["template_id"]), vars_)
                for uid in user_ids:
                    _insert_with_retry(rule["template_id"], uid, subject,
                                       body, event_name)
        return handler

    for ev in (events.BED_ASSIGNED, events.BED_VACATED, events.BED_TRANSFERRED,
               events.RESOURCE_PUBLISHED, events.RESOURCE_UNPUBLISHED,
               events.RESOURCE_HELD, events.CASE_SUBMITTED, events.CASE_DECIDED,
               events.VIOLATION_OPENED, events.VIOLATION_RESOLVED):
        events.bus.subscribe(ev, make_handler(ev))


def _resolve_audience(aq: dict) -> list[int]:
    conn = db.get_connection()
    if not aq:
        return [r["id"] for r in conn.execute("SELECT id FROM users WHERE disabled=0")]
    role = aq.get("role")
    if role:
        # Members of the named role *plus* all system administrators (admins
        # see everything for operational visibility).
        rows = conn.execute(
            "SELECT DISTINCT u.id FROM users u "
            "JOIN user_roles ur ON ur.user_id=u.id "
            "JOIN roles r ON r.id=ur.role_id "
            "WHERE u.disabled=0 AND r.code IN (?, 'system_admin')",
            (role,)).fetchall()
        return [r["id"] for r in rows]
    if "user_id" in aq:
        return [int(aq["user_id"])]
    return []


def _template_subject(tid: int) -> str:
    return db.get_connection().execute(
        "SELECT subject FROM notif_templates WHERE id=?", (tid,)).fetchone()["subject"]


def _template_body(tid: int) -> str:
    return db.get_connection().execute(
        "SELECT body FROM notif_templates WHERE id=?", (tid,)).fetchone()["body"]


def _cron_matches(spec: str | None, when: datetime) -> bool:
    """Tiny cron-subset matcher: 'M H DOM MON DOW' with `*`, lists `1,5`,
    and ranges `1-5`. Sufficient for "daily at 7:00" and weekday digests.
    """
    if not spec:
        return False
    parts = spec.split()
    if len(parts) != 5:
        return False
    minute, hour, dom, mon, dow = parts
    fields = [
        (minute, when.minute, 0, 59),
        (hour,   when.hour,   0, 23),
        (dom,    when.day,    1, 31),
        (mon,    when.month,  1, 12),
        (dow,    when.isoweekday() % 7, 0, 6),  # cron Sun=0
    ]
    for raw, value, lo, hi in fields:
        if not _cron_field_matches(raw, value, lo, hi):
            return False
    return True


def _cron_field_matches(raw: str, value: int, lo: int, hi: int) -> bool:
    if raw == "*":
        return True
    for token in raw.split(","):
        token = token.strip()
        if "-" in token:
            try:
                a, b = (int(x) for x in token.split("-", 1))
            except ValueError:
                return False
            if a <= value <= b:
                return True
        else:
            try:
                if int(token) == value:
                    return True
            except ValueError:
                return False
    return False


def _payload_to_vars(payload: dict) -> dict[str, str]:
    vars_: dict[str, str] = {"Today": datetime.utcnow().strftime("%m/%d/%Y")}
    for k in ("StudentName", "Dorm", "Room", "Bed", "EffectiveDate",
              "ResourceTitle", "EmployerName", "Operator"):
        if k in payload:
            vars_[k] = str(payload[k])
    if "name" in payload and "StudentName" not in vars_:
        vars_["StudentName"] = str(payload["name"])
    if "operator" in payload:
        vars_["Operator"] = str(payload["operator"])
    if "effective_date" in payload:
        try:
            d = datetime.fromisoformat(payload["effective_date"])
            vars_["EffectiveDate"] = d.strftime("%m/%d/%Y")
        except Exception:
            vars_["EffectiveDate"] = str(payload["effective_date"])
    return vars_
