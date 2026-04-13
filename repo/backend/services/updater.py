"""Offline signed update package import + rollback.

Update package format (a single ZIP file):
    update.json        — {"version": "1.2.0", "files": [...], "notes": "..."}
    payload/...        — files copied into the install dir, mirroring layout
    update.json.sig    — RSA-PSS signature over update.json (DER bytes)

The installer ships the public key at ``update_pubkey.pem`` in the data
directory. If `cryptography` is unavailable, signature verification falls
back to a SHA-256 fingerprint check and the row is recorded with
``signature_ok=0`` so operators can see the difference.

Before applying, a snapshot of the current SQLite database is copied to
``snapshots/<applied_at>__crhgc.db``. ``rollback(package_id)`` restores
that snapshot and marks the row as rolled back. Rollback is reversible by
re-applying a newer package.
"""
from __future__ import annotations
import hashlib
import json
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .. import audit, config, db
from ..permissions import Session, requires
from .auth import BizError

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    HAVE_CRYPTO = True
except Exception:  # pragma: no cover
    HAVE_CRYPTO = False


def _default_install_dir() -> Path:
    """Best guess for the actual application install directory.

    - When packaged with PyInstaller (``sys.frozen``), the installed binary
      lives next to its loader, so we use the executable's parent directory.
    - When running from a checkout, fall back to the repo root that contains
      the running ``main.py`` / ``backend/`` package.
    """
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return config.REPO_ROOT


@dataclass
class UpdatePackage:
    id: int
    version: str
    sha256: str
    signed_by: str | None
    signature_ok: bool
    applied_at: str
    rolled_back_at: str | None
    snapshot_path: str | None
    notes: str | None


class UpdaterService:

    @requires("update.apply")
    def apply_package(self, session: Session,
                      zip_path: str | Path,
                      install_dir: str | Path | None = None) -> UpdatePackage:
        """Apply a signed update package.

        Packages with a missing or invalid RSA-PSS signature are REJECTED
        with ``BizError(SIGNATURE_REQUIRED)``. There is intentionally no
        ``allow_unsigned`` override at any layer (backend or UI): tests
        that need to exercise the apply path must produce a properly
        signed package using the documented signing key. This eliminates
        the previous "audited test hook" that could be invoked from any
        caller holding the ``update.apply`` permission.
        """
        zp = Path(zip_path)
        if not zp.is_file():
            raise BizError("PACKAGE_MISSING", str(zp))

        digest = self._sha256(zp)
        with zipfile.ZipFile(zp) as zf:
            try:
                manifest_bytes = zf.read("update.json")
            except KeyError:
                raise BizError("BAD_PACKAGE", "update.json missing from package")
            try:
                manifest = json.loads(manifest_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                raise BizError("BAD_PACKAGE", f"manifest parse error: {e}")
            sig = None
            try:
                sig = zf.read("update.json.sig")
            except KeyError:
                pass

            sig_ok, signer = self._verify_signature(manifest_bytes, sig)
            if not sig_ok:
                raise BizError(
                    "SIGNATURE_REQUIRED",
                    f"Package signature could not be verified "
                    f"({signer or 'no signature'}). Replace the public key "
                    f"at {config.update_signing_key_path()} with the "
                    "production key, or obtain a properly signed package "
                    "from the release pipeline.")

            # Insert the package row FIRST so the snapshot includes the
            # provenance record. Rollback then restores a DB that still
            # remembers this apply.
            with db.transaction() as conn:
                cur = conn.execute(
                    """INSERT INTO update_packages(version, sha256, signed_by,
                            signature_ok, snapshot_path, notes)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (manifest.get("version", "?"), digest, signer,
                     1 if sig_ok else 0, "", manifest.get("notes")))
                pid = cur.lastrowid

            snap = self._snapshot_db()
            with db.transaction() as conn:
                conn.execute(
                    "UPDATE update_packages SET snapshot_path=? WHERE id=?",
                    (str(snap), pid))

            # Copy payload/ files into the install dir. When no caller-
            # supplied path is given, target the actual application install
            # directory (frozen exe parent or repo root) — NOT the per-user
            # data folder. The per-user folder used to be the default which
            # silently let updates land somewhere they would never be loaded.
            target_root = (Path(install_dir) if install_dir
                           else _default_install_dir())
            target_root.mkdir(parents=True, exist_ok=True)
            for member in zf.namelist():
                if not member.startswith("payload/") or member.endswith("/"):
                    continue
                rel = member[len("payload/"):]
                # Reject path traversal.
                if rel.startswith("/") or ".." in Path(rel).parts:
                    raise BizError("BAD_PACKAGE",
                                   f"unsafe path in package: {member}")
                dest = target_root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, dest.open("wb") as out:
                    shutil.copyfileobj(src, out)
        audit.record(session.user_id, "update_package", pid, "apply",
                     {"version": manifest.get("version"),
                      "sha256": digest, "signature_ok": sig_ok,
                      "snapshot": str(snap)})
        return self.get(pid)

    @requires("update.apply")
    def rollback(self, session: Session, package_id: int) -> None:
        conn = db.get_connection()
        r = conn.execute(
            "SELECT snapshot_path, rolled_back_at FROM update_packages WHERE id=?",
            (package_id,)).fetchone()
        if not r:
            raise BizError("PACKAGE_NOT_FOUND", str(package_id))
        if r["rolled_back_at"]:
            raise BizError("ALREADY_ROLLED_BACK", "package already rolled back")
        snap = Path(r["snapshot_path"]) if r["snapshot_path"] else None
        if not snap or not snap.is_file():
            raise BizError("SNAPSHOT_MISSING", "snapshot not available")
        # Take a *current* snapshot before restoring so this rollback is also
        # reversible.
        current = self._snapshot_db(prefix="pre_rollback")
        # Restore: snapshots are encrypted blobs in the same envelope format
        # as the at-rest DB. Replace the at-rest blob and reopen so the next
        # ``get_connection`` call deserializes from the restored state.
        from .. import db as _db
        _db.reset_connection()
        enc_target = config.db_path().with_suffix(config.db_path().suffix + ".enc")
        shutil.copy2(snap, enc_target)
        _db.get_connection()  # reopen (deserializes from enc_target)
        with db.transaction() as conn:
            conn.execute(
                "UPDATE update_packages SET rolled_back_at=datetime('now'), "
                "notes=COALESCE(notes,'')||char(10)||? WHERE id=?",
                (f"rolled back; pre-rollback snapshot at {current}", package_id))
        audit.record(session.user_id, "update_package", package_id, "rollback",
                     {"restored_from": str(snap),
                      "pre_rollback_snapshot": str(current)})

    def list_packages(self) -> list[UpdatePackage]:
        rows = db.get_connection().execute(
            "SELECT * FROM update_packages ORDER BY applied_at DESC").fetchall()
        return [self._row(r) for r in rows]

    def get(self, package_id: int) -> UpdatePackage:
        r = db.get_connection().execute(
            "SELECT * FROM update_packages WHERE id=?", (package_id,)).fetchone()
        if not r:
            raise BizError("PACKAGE_NOT_FOUND", str(package_id))
        return self._row(r)

    # ---- Internals -----------------------------------------------------

    def _row(self, r) -> UpdatePackage:
        return UpdatePackage(
            id=r["id"], version=r["version"], sha256=r["sha256"],
            signed_by=r["signed_by"], signature_ok=bool(r["signature_ok"]),
            applied_at=r["applied_at"], rolled_back_at=r["rolled_back_at"],
            snapshot_path=r["snapshot_path"], notes=r["notes"])

    @staticmethod
    def _sha256(p: Path) -> str:
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    def _snapshot_db(self, prefix: str = "snapshot") -> Path:
        """Capture an encrypted snapshot of the live DB.

        In SQLCipher mode the on-disk file is already encrypted page-by-
        page, so a file copy preserves confidentiality. In fallback mode
        the live DB is in memory; we force the at-rest blob to be current
        and copy that, so snapshots — like the live DB — are never written
        to disk in plaintext.
        """
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        snap = config.snapshot_dir() / f"{prefix}_{ts}.db"
        from .. import db as _db
        if _db.HAVE_SQLCIPHER:
            try:
                db.get_connection().execute("PRAGMA wal_checkpoint(FULL)")
            except Exception:
                pass
            shutil.copy2(config.db_path(), snap)
        else:
            _db.periodic_reseal()
            enc = config.db_path().with_suffix(config.db_path().suffix + ".enc")
            if enc.is_file():
                shutil.copy2(enc, snap)
            else:
                snap.write_bytes(b"")
        return snap

    def _verify_signature(self, manifest_bytes: bytes,
                          sig: bytes | None) -> tuple[bool, str | None]:
        if sig is None:
            return False, None
        if not HAVE_CRYPTO:
            return False, "unverified-no-crypto-lib"
        pk_path = config.update_signing_key_path()
        if not pk_path.is_file():
            return False, "no-public-key"
        pk_bytes = pk_path.read_bytes()
        # Refuse the shipped placeholder PEM. The installer ships a stub
        # `update_pubkey.pem` so operators notice they need to drop in a real
        # production key — a placeholder must NEVER be treated as trusted.
        if b"PLACEHOLDER" in pk_bytes:
            return False, "placeholder-pubkey"
        try:
            pk = serialization.load_pem_public_key(pk_bytes)
        except Exception:
            return False, "pubkey-parse-failed"
        # Reject keys that aren't RSA — PSS verification below is RSA-only.
        try:
            from cryptography.hazmat.primitives.asymmetric import rsa
            if not isinstance(pk, rsa.RSAPublicKey):
                return False, "pubkey-not-rsa"
            if pk.key_size < 2048:
                return False, f"pubkey-too-small-{pk.key_size}"
        except Exception:
            return False, "pubkey-type-check-failed"
        try:
            pk.verify(sig, manifest_bytes,
                      padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                  salt_length=padding.PSS.MAX_LENGTH),
                      hashes.SHA256())
            return True, "rsa-pss"
        except Exception:
            return False, "verify-failed"
