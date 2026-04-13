"""Minimal test runner used when pytest is unavailable.

Discovers ``test_*.py`` modules under ``tests/``, builds the same
fixtures as ``conftest.py`` per test, and executes every ``test_*``
function. Reports pass/fail and exits non-zero on any failure.

When pytest IS installed, prefer running ``pytest -q`` from the repo
root — both runners exercise the same test modules.
"""
from __future__ import annotations
import importlib
import inspect
import os
import sys
import tempfile
import traceback
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import crypto, db as _db


# ---- Fixture factories (mirror conftest.py) -------------------------------

@contextmanager
def _container_fixture():
    tmp = Path(tempfile.mkdtemp(prefix="crhgc-tests-"))
    saved = {k: os.environ.get(k) for k in
             ("CRHGC_DB", "LOCALAPPDATA", "XDG_DATA_HOME")}
    os.environ["CRHGC_DB"] = str(tmp / "test.db")
    os.environ["LOCALAPPDATA"] = str(tmp)
    os.environ["XDG_DATA_HOME"] = str(tmp)
    _db.reset_connection()
    try:
        from backend.app import Container
        yield Container(), tmp
    finally:
        _db.reset_connection()
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _admin_session(container):
    container.auth.bootstrap_admin("admin", "TestPassw0rd!", "Admin")
    return container.auth.login("admin", "TestPassw0rd!")


def _coordinator_session(container):
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


# ---- Discovery & execution -----------------------------------------------

def _build_kwargs(fn, container, tmp_path, admin_sess, coord_sess):
    sig = inspect.signature(fn)
    kw = {}
    for name in sig.parameters:
        if name == "container":
            kw[name] = container
        elif name == "admin_session":
            kw[name] = admin_sess
        elif name == "coordinator_session":
            kw[name] = coord_sess
        elif name == "tmp_path":
            kw[name] = tmp_path
        else:
            raise RuntimeError(f"unknown fixture: {name}")
    return kw


def main() -> int:
    test_dir = Path(__file__).parent
    test_files = sorted(p for p in test_dir.glob("test_*.py"))
    passed = failed = skipped = 0
    failures: list[str] = []

    for f in test_files:
        mod_name = f"tests.{f.stem}"
        try:
            mod = importlib.import_module(mod_name)
        except Exception as e:
            failed += 1
            failures.append(f"{mod_name}: import error: {e}")
            continue
        # Skip module if it called pytest.importorskip and bailed.
        for fn_name, fn in inspect.getmembers(mod, inspect.isfunction):
            if not fn_name.startswith("test_"):
                continue
            with _container_fixture() as (container, tmp):
                try:
                    admin = _admin_session(container)
                    coord = None
                    if "coordinator_session" in inspect.signature(fn).parameters:
                        coord = _coordinator_session(container)
                    kw = _build_kwargs(fn, container, tmp, admin, coord)
                    fn(**kw)
                    passed += 1
                    print(f"  PASS  {mod_name}::{fn_name}")
                except Exception as e:
                    failed += 1
                    msg = f"{mod_name}::{fn_name}: {e.__class__.__name__}: {e}"
                    failures.append(msg)
                    print(f"  FAIL  {msg}")
                    traceback.print_exc()

    total = passed + failed + skipped
    print(f"\n{passed}/{total} tests passed", end="")
    if failed:
        print(f"  ({failed} failed)")
        return 1
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
