"""SQLite connection management and migration runner.

At-rest encryption strategy:
  1. If the ``pysqlcipher3`` driver is importable, it is used in place of
     stdlib sqlite3 with PRAGMA key set from the app key file. The DB file
     is then encrypted page-by-page on disk (the strict spec interpretation).
  2. Otherwise, an in-memory SQLite connection is used and the encrypted
     blob at ``<db>.enc`` is the only on-disk representation. The blob is
     re-written after every committed transaction (and on the periodic
     reseal tick) so an abrupt termination — including SIGKILL or
     power-loss — never leaves a plaintext SQLite file on disk in fallback
     mode. There is, by construction, no plaintext file to expose.

Crash safety:
  ``close_and_seal`` is registered with ``atexit`` and SIGTERM/SIGINT
  handlers when ``get_connection`` is first called. Combined with the
  per-commit reseal in ``transaction()``, this means a crash between
  transactions loses no data and never exposes plaintext on disk in the
  fallback path. SQLCipher mode delegates the same guarantees to the
  driver.
"""
from __future__ import annotations
import atexit
import logging
import signal
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from . import config

try:  # pragma: no cover - depends on optional driver
    from pysqlcipher3 import dbapi2 as sqlcipher  # type: ignore
    HAVE_SQLCIPHER = True
except Exception:  # pragma: no cover
    sqlcipher = None  # type: ignore
    HAVE_SQLCIPHER = False


def _enc_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".enc")


def _connect(path: Path) -> sqlite3.Connection:
    if HAVE_SQLCIPHER:
        conn = sqlcipher.connect(str(path), isolation_level=None,
                                 check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # Bind the page-encryption key from the app's keyfile.
        from . import crypto as _crypto
        key_hex = _crypto.load_or_create_key().hex()
        # Note: PRAGMA key must be the FIRST statement on the connection.
        conn.execute(f"PRAGMA key = \"x'{key_hex}'\"")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn
    # Fallback: in-memory SQLite + encrypted at-rest blob. Plaintext never
    # touches disk in this code path.
    conn = sqlite3.connect(":memory:", detect_types=sqlite3.PARSE_DECLTYPES,
                           isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    enc = _enc_path(path)
    if enc.is_file():
        from . import crypto as _crypto
        try:
            plain = _crypto.decrypt_bytes_at_rest(enc.read_bytes())
            # ``deserialize`` requires a writable, non-shared connection and
            # pre-3.11 pythons don't expose it; we depend on Python 3.11+ as
            # documented in design.md.
            conn.deserialize(plain)
        except Exception:
            logging.getLogger("crhgc.db").error(
                "could not decrypt/deserialize at-rest DB blob; starting "
                "with empty in-memory DB", exc_info=True)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


_CONN: sqlite3.Connection | None = None
_SEAL_LOCK = threading.RLock()
_SHUTDOWN_HOOKS_INSTALLED = False


def _install_shutdown_hooks() -> None:
    """Install atexit + signal handlers so the DB is always re-sealed.

    In fallback mode this is belt-and-braces — the per-commit reseal in
    ``transaction()`` already keeps the encrypted blob current — but it
    catches any uncommitted in-memory state on a clean exit. In SQLCipher
    mode it's the documented close path.
    """
    global _SHUTDOWN_HOOKS_INSTALLED
    if _SHUTDOWN_HOOKS_INSTALLED:
        return
    _SHUTDOWN_HOOKS_INSTALLED = True
    atexit.register(close_and_seal)
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            prev = signal.getsignal(sig)

            def _handler(signum, frame, _prev=prev):
                try:
                    close_and_seal()
                finally:
                    if callable(_prev) and _prev not in (
                            signal.SIG_DFL, signal.SIG_IGN):
                        try:
                            _prev(signum, frame)
                        except Exception:
                            pass
                    if signum == signal.SIGINT:
                        raise KeyboardInterrupt()

            signal.signal(sig, _handler)
        except (ValueError, OSError):
            pass


def get_connection() -> sqlite3.Connection:
    global _CONN
    if _CONN is None:
        _CONN = _connect(config.db_path())
        _ensure_migrations(_CONN)
        _install_shutdown_hooks()
    return _CONN


def _persist_in_memory_locked() -> None:
    """Serialize the in-memory DB and write the encrypted blob to disk.

    Caller MUST hold ``_SEAL_LOCK``. No-op when SQLCipher is in use or
    when no connection is open. Writes are atomic at the filesystem level
    (write to a temporary file, fsync, then rename) so a crash mid-write
    can't leave a half-encrypted blob.
    """
    if HAVE_SQLCIPHER or _CONN is None:
        return
    try:
        raw = bytes(_CONN.serialize())
    except Exception:
        logging.getLogger("crhgc.db").warning(
            "serialize failed; skipping at-rest persist", exc_info=True)
        return
    from . import crypto as _crypto
    blob = _crypto.encrypt_bytes_at_rest(raw)
    enc = _enc_path(config.db_path())
    enc.parent.mkdir(parents=True, exist_ok=True)
    tmp = enc.with_suffix(enc.suffix + ".tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(blob)
            f.flush()
            try:
                import os as _os
                _os.fsync(f.fileno())
            except OSError:
                pass
        tmp.replace(enc)
        try:
            import os as _os
            _os.chmod(enc, 0o600)
        except OSError:
            pass
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def periodic_reseal() -> None:
    """Refresh the encrypted at-rest blob from the current DB state.

    Safe to call from a background timer. No-op when SQLCipher is in use
    or when no connection is currently open.
    """
    if HAVE_SQLCIPHER or _CONN is None:
        return
    with _SEAL_LOCK:
        _persist_in_memory_locked()


def reset_connection() -> None:
    """Used by tests to discard the cached connection.

    Persists the in-memory DB before closing so the next ``get_connection``
    call can deserialize it back. Tests that intentionally simulate an
    abrupt kill should clear ``_CONN`` directly.
    """
    global _CONN
    if _CONN is not None:
        with _SEAL_LOCK:
            try:
                _persist_in_memory_locked()
            except Exception:
                pass
            try:
                _CONN.close()
            except Exception:
                pass
            _CONN = None


def close_and_seal() -> None:
    """Cleanly close the DB and re-seal it as an encrypted at-rest blob.

    No-op when SQLCipher is in use (the file is already encrypted on disk).
    In fallback mode this serializes the in-memory DB and writes the
    encrypted blob; per-commit persistence already keeps the blob current,
    so this is mostly a final flush + close.
    """
    global _CONN
    with _SEAL_LOCK:
        if _CONN is None:
            return
        if HAVE_SQLCIPHER:
            try:
                _CONN.execute("PRAGMA wal_checkpoint(FULL)")
            except Exception:
                pass
            try:
                _CONN.close()
            except Exception:
                pass
            _CONN = None
            return
        try:
            _persist_in_memory_locked()
        finally:
            try:
                _CONN.close()
            except Exception:
                pass
            _CONN = None


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    conn = get_connection()
    with _SEAL_LOCK:
        conn.execute("BEGIN")
        try:
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        # Persist the post-commit state synchronously so an abrupt
        # termination immediately after this point still leaves the on-disk
        # encrypted blob consistent with what the caller observed as
        # committed.
        _persist_in_memory_locked()


def _ensure_migrations(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
    """)
    applied = {row["version"] for row in conn.execute(
        "SELECT version FROM schema_version")}
    files = sorted(config.MIGRATIONS_DIR.glob("*.sql"))
    for f in files:
        try:
            version = int(f.name.split("_", 1)[0])
        except ValueError:
            continue
        if version in applied:
            continue
        sql = f.read_text(encoding="utf-8")
        conn.executescript("BEGIN;\n" + sql + "\nCOMMIT;")
        conn.execute(
            "INSERT INTO schema_version(version, applied_at) VALUES(?, datetime('now'))",
            (version,))


def seed_if_empty() -> bool:
    """Apply seed data on first run.

    `seed_dev.sql` runs only if no roles exist (initial bootstrap).
    `seed_extras.sql` is idempotent (INSERT OR IGNORE) and runs on every
    startup so newly-shipped catalog/sensitive-word entries land in older
    databases.
    """
    conn = get_connection()
    applied = False
    if conn.execute("SELECT COUNT(*) AS n FROM roles").fetchone()["n"] == 0:
        f = config.SEED_DIR / "seed_dev.sql"
        if f.exists():
            conn.executescript("BEGIN;\n" + f.read_text(encoding="utf-8") + "\nCOMMIT;")
            applied = True
    extras = config.SEED_DIR / "seed_extras.sql"
    if extras.exists():
        conn.executescript("BEGIN;\n" + extras.read_text(encoding="utf-8") + "\nCOMMIT;")
        applied = True
    if applied:
        # The seed scripts ran outside the ``transaction()`` context, so
        # persist explicitly to keep the at-rest blob current.
        with _SEAL_LOCK:
            _persist_in_memory_locked()
    return applied
