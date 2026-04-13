"""Test fixtures: isolated DB per test, container, and admin session."""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def container(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("CRHGC_DB", str(db_file))
    # Force backend.config.data_dir() to write into tmp_path so evidence and
    # snapshots stay isolated.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    # Reset cached connection from any prior test.
    from backend import db as _db
    _db.reset_connection()
    from backend.app import Container
    c = Container()
    yield c
    _db.reset_connection()


@pytest.fixture
def admin_session(container):
    user = container.auth.bootstrap_admin("admin", "TestPassw0rd!", "Admin")
    return container.auth.login("admin", "TestPassw0rd!")


@pytest.fixture
def coordinator_session(container, admin_session):
    """A second user holding only Housing Coordinator + student.write."""
    from backend import db as _db
    from backend import crypto
    h, salt = crypto.hash_password("CoordPassw0rd!")
    conn = _db.get_connection()
    conn.execute(
        "INSERT INTO users(username, full_name, password_hash, password_salt) "
        "VALUES ('coord', 'Coord', ?, ?)", (h, salt))
    uid = conn.execute(
        "SELECT id FROM users WHERE username='coord'").fetchone()["id"]
    rid = conn.execute(
        "SELECT id FROM roles WHERE code='housing_coordinator'").fetchone()["id"]
    conn.execute("INSERT INTO user_roles(user_id, role_id) VALUES (?, ?)",
                 (uid, rid))
    return container.auth.login("coord", "CoordPassw0rd!")
