"""Scheduled (cron) notification rules must fire when their minute matches."""
from __future__ import annotations
from datetime import datetime

from backend import db
from backend.services.notification import _cron_matches


def test_cron_matches_basics():
    when = datetime(2026, 4, 13, 7, 0)
    assert _cron_matches("0 7 * * *", when)
    assert not _cron_matches("0 8 * * *", when)
    assert _cron_matches("0 6-9 * * *", when)
    assert _cron_matches("0 7,9 * * *", when)


def test_scheduled_rule_fires_and_dedups(container, admin_session):
    # Insert a rule that should fire at exactly the test instant.
    when = datetime(2026, 4, 13, 7, 0)
    cron = f"{when.minute} {when.hour} * * *"
    conn = db.get_connection()
    tpl_id = conn.execute(
        "SELECT id FROM notif_templates WHERE name='daily_digest'"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO notif_rules(name, kind, cron_spec, template_id, "
        "audience_query, enabled) VALUES "
        "('test rule', 'schedule', ?, ?, '{}', 1)", (cron, tpl_id))

    fired1 = container.notifications.fire_scheduled_rules(now=when)
    fired2 = container.notifications.fire_scheduled_rules(now=when)
    assert fired1 >= 1
    assert fired2 == 0  # idempotent within the same minute

    # A drain delivers the queued message into the inbox.
    container.notifications.drain_queue()
    inbox = container.notifications.inbox(admin_session)
    assert any("digest" in m.subject.lower() for m in inbox)
