"""Authentication and session management."""
from __future__ import annotations
from datetime import datetime, timedelta

from .. import audit, config, crypto, db
from ..models import User
from ..permissions import Session


class BizError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class AuthService:

    # ---- bootstrap & user management --------------------------------------

    def has_any_users(self) -> bool:
        conn = db.get_connection()
        return conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] > 0

    def bootstrap_admin(self, username: str, password: str, full_name: str) -> User:
        if self.has_any_users():
            raise BizError("BOOTSTRAP_NOT_ALLOWED", "An administrator already exists.")
        if len(password) < 10:
            raise BizError("WEAK_PASSWORD", "Password must be at least 10 characters.")
        h, salt = crypto.hash_password(password)
        with db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO users(username, full_name, password_hash, password_salt) "
                "VALUES (?, ?, ?, ?)", (username, full_name, h, salt))
            user_id = cur.lastrowid
            role_id = conn.execute(
                "SELECT id FROM roles WHERE code='system_admin'").fetchone()["id"]
            conn.execute("INSERT INTO user_roles(user_id, role_id) VALUES (?, ?)",
                         (user_id, role_id))
        audit.record(user_id, "user", user_id, "bootstrap_admin",
                     {"username": username})
        return User(id=user_id, username=username, full_name=full_name,
                    roles=["system_admin"])

    # ---- login ------------------------------------------------------------

    def login(self, username: str, password: str) -> Session:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT id, username, full_name, password_hash, password_salt, disabled "
            "FROM users WHERE username = ?", (username,)).fetchone()
        if not row or row["disabled"]:
            raise BizError("AUTH_INVALID", "Invalid username or password.")
        if not crypto.verify_password(password, row["password_hash"], row["password_salt"]):
            raise BizError("AUTH_INVALID", "Invalid username or password.")
        conn.execute("UPDATE users SET last_login_at = datetime('now') WHERE id = ?",
                     (row["id"],))
        roles = self._user_roles(row["id"])
        perms = self._user_permissions(row["id"])
        audit.record(row["id"], "user", row["id"], "login", {"username": username})
        return Session(user_id=row["id"], username=row["username"],
                       full_name=row["full_name"], roles=roles, permissions=perms)

    def logout(self, session: Session) -> None:
        audit.record(session.user_id, "user", session.user_id, "logout", {})
        session.mask_unlock_until = None

    # ---- masked-field unlock ---------------------------------------------

    def unlock_masked_fields(self, session: Session, password: str) -> datetime:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT password_hash, password_salt FROM users WHERE id = ?",
            (session.user_id,)).fetchone()
        if not row or not crypto.verify_password(password, row["password_hash"], row["password_salt"]):
            raise BizError("AUTH_INVALID", "Re-entry failed.")
        session.mask_unlock_until = datetime.utcnow() + timedelta(
            seconds=config.MASK_UNLOCK_SECONDS)
        audit.record(session.user_id, "user", session.user_id, "mask_unlock", {})
        return session.mask_unlock_until

    def change_password(self, session: Session, old: str, new: str) -> None:
        if len(new) < 10:
            raise BizError("WEAK_PASSWORD", "Password must be at least 10 characters.")
        conn = db.get_connection()
        row = conn.execute(
            "SELECT password_hash, password_salt FROM users WHERE id=?",
            (session.user_id,)).fetchone()
        if not crypto.verify_password(old, row["password_hash"], row["password_salt"]):
            raise BizError("AUTH_INVALID", "Old password does not match.")
        h, salt = crypto.hash_password(new)
        with db.transaction() as conn:
            conn.execute(
                "UPDATE users SET password_hash=?, password_salt=? WHERE id=?",
                (h, salt, session.user_id))
        audit.record(session.user_id, "user", session.user_id, "change_password", {})

    # ---- helpers ----------------------------------------------------------

    def _user_roles(self, user_id: int) -> set[str]:
        conn = db.get_connection()
        rows = conn.execute(
            "SELECT r.code FROM user_roles ur JOIN roles r ON r.id=ur.role_id "
            "WHERE ur.user_id = ?", (user_id,)).fetchall()
        return {r["code"] for r in rows}

    def _user_permissions(self, user_id: int) -> set[str]:
        conn = db.get_connection()
        rows = conn.execute(
            "SELECT DISTINCT p.code FROM user_roles ur "
            "JOIN role_permissions rp ON rp.role_id = ur.role_id "
            "JOIN permissions p ON p.id = rp.permission_id "
            "WHERE ur.user_id = ?", (user_id,)).fetchall()
        return {r["code"] for r in rows}
