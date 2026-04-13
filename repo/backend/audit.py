"""Append-only, hash-chained audit log."""
from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from . import db


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def record(actor_id: int | None, entity_type: str, entity_id: str | int,
           action: str, payload: dict[str, Any]) -> str:
    conn = db.get_connection()
    row = conn.execute(
        "SELECT this_hash FROM audit_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    prev_hash = row["this_hash"] if row else ""
    canonical = _canonical(payload)
    h = hashlib.sha256((prev_hash + canonical).encode("utf-8")).hexdigest()
    conn.execute(
        """INSERT INTO audit_log
           (actor_id, entity_type, entity_id, action, payload_json, prev_hash, this_hash)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (actor_id, entity_type, str(entity_id), action, canonical, prev_hash, h),
    )
    return h


@dataclass
class ChainVerification:
    ok: bool
    checked: int
    first_break_id: int | None = None


def verify_chain() -> ChainVerification:
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT id, payload_json, prev_hash, this_hash FROM audit_log ORDER BY id"
    ).fetchall()
    expected_prev = ""
    for r in rows:
        if r["prev_hash"] != expected_prev:
            return ChainVerification(False, len(rows), r["id"])
        recomputed = hashlib.sha256(
            (expected_prev + r["payload_json"]).encode("utf-8")
        ).hexdigest()
        if recomputed != r["this_hash"]:
            return ChainVerification(False, len(rows), r["id"])
        expected_prev = r["this_hash"]
    return ChainVerification(True, len(rows))


def tail(limit: int = 100) -> list[dict[str, Any]]:
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT id, ts, actor_id, entity_type, entity_id, action FROM audit_log "
        "ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]
