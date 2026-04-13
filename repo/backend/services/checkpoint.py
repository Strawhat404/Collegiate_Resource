"""Workspace state and unsaved-draft checkpoints (every 60 s).

`workspace_state` holds one row per user describing the open tabs and
detached windows so the next session can restore them.

`draft_checkpoints` holds in-progress form data keyed by `(user_id, draft_key)`.
The UI calls `save_draft()` on a 60-second QTimer; on launch it calls
`load_draft()` and offers to restore.
"""
from __future__ import annotations
import json
from typing import Any

from .. import db
from ..permissions import Session


class CheckpointService:

    # ---- Workspace state ----------------------------------------------

    def save_workspace(self, session: Session, payload: dict[str, Any]) -> None:
        with db.transaction() as conn:
            conn.execute(
                """INSERT INTO workspace_state(user_id, payload_json, saved_at)
                   VALUES (?, ?, datetime('now'))
                   ON CONFLICT(user_id) DO UPDATE SET
                        payload_json=excluded.payload_json,
                        saved_at=excluded.saved_at""",
                (session.user_id, json.dumps(payload, default=str)))

    def load_workspace(self, session: Session) -> dict[str, Any] | None:
        r = db.get_connection().execute(
            "SELECT payload_json FROM workspace_state WHERE user_id=?",
            (session.user_id,)).fetchone()
        if not r:
            return None
        try:
            return json.loads(r["payload_json"])
        except (TypeError, ValueError):
            return None

    # ---- Drafts -------------------------------------------------------

    def save_draft(self, session: Session, draft_key: str,
                   payload: dict[str, Any]) -> None:
        with db.transaction() as conn:
            conn.execute(
                """INSERT INTO draft_checkpoints(user_id, draft_key, payload_json, saved_at)
                   VALUES (?, ?, ?, datetime('now'))
                   ON CONFLICT(user_id, draft_key) DO UPDATE SET
                        payload_json=excluded.payload_json,
                        saved_at=excluded.saved_at""",
                (session.user_id, draft_key, json.dumps(payload, default=str)))

    def load_draft(self, session: Session,
                   draft_key: str) -> dict[str, Any] | None:
        r = db.get_connection().execute(
            "SELECT payload_json FROM draft_checkpoints "
            "WHERE user_id=? AND draft_key=?",
            (session.user_id, draft_key)).fetchone()
        if not r:
            return None
        try:
            return json.loads(r["payload_json"])
        except (TypeError, ValueError):
            return None

    def list_drafts(self, session: Session) -> list[dict]:
        rows = db.get_connection().execute(
            "SELECT draft_key, saved_at FROM draft_checkpoints "
            "WHERE user_id=? ORDER BY saved_at DESC",
            (session.user_id,)).fetchall()
        return [dict(r) for r in rows]

    def discard_draft(self, session: Session, draft_key: str) -> None:
        with db.transaction() as conn:
            conn.execute(
                "DELETE FROM draft_checkpoints WHERE user_id=? AND draft_key=?",
                (session.user_id, draft_key))

    def discard_all(self, session: Session) -> int:
        with db.transaction() as conn:
            cur = conn.execute(
                "DELETE FROM draft_checkpoints WHERE user_id=?",
                (session.user_id,))
        return cur.rowcount
