"""Key management, password hashing, and field-level encryption.

We prefer SQLCipher for at-rest encryption when available; otherwise we fall
back to per-field AES-GCM (via `cryptography`) and an unencrypted SQLite file.
A friendly placeholder XOR cipher is used only when neither library is present
so the application remains runnable in restricted environments — this is
clearly NOT secure and emits a startup warning.
"""
from __future__ import annotations
import hashlib
import hmac
import os
import secrets
from base64 import b64encode, b64decode
from pathlib import Path

from . import config

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    HAVE_AESGCM = True
except Exception:  # pragma: no cover - cryptography not installed
    HAVE_AESGCM = False

# Emit a one-shot runtime warning when AES-GCM is unavailable. Production
# deployments MUST install `cryptography`; the XOR fallback exists only so
# the app can still boot for documentation/demo purposes.
if not HAVE_AESGCM:
    import logging as _logging
    import warnings as _warnings
    _msg = ("CRHGC: 'cryptography' library is missing — falling back to an "
            "INSECURE XOR cipher for sensitive fields. Install the "
            "`cryptography` package before storing real data.")
    _warnings.warn(_msg, RuntimeWarning, stacklevel=2)
    try:
        _logging.getLogger("crhgc.crypto").warning(_msg)
    except Exception:
        pass


# ---- Key file ------------------------------------------------------------

def load_or_create_key() -> bytes:
    p: Path = config.key_path()
    if p.exists():
        data = p.read_bytes()
        if len(data) >= 32:
            return data[:32]
    key = secrets.token_bytes(32)
    p.write_bytes(key)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return key


# ---- Password hashing ----------------------------------------------------

def hash_password(password: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    if salt is None:
        salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                            config.PBKDF2_ITERATIONS)
    return h, salt


def verify_password(password: str, expected_hash: bytes, salt: bytes) -> bool:
    h, _ = hash_password(password, salt)
    return hmac.compare_digest(h, expected_hash)


# ---- Field encryption ----------------------------------------------------

_KEY: bytes | None = None


def _key() -> bytes:
    global _KEY
    if _KEY is None:
        _KEY = load_or_create_key()
    return _KEY


def encrypt_field(plain: str | None) -> str | None:
    """Encrypt a field value. Returns base64 string, or None for None input."""
    if plain is None or plain == "":
        return plain
    raw = plain.encode("utf-8")
    if HAVE_AESGCM:
        nonce = secrets.token_bytes(12)
        ct = AESGCM(_key()).encrypt(nonce, raw, None)
        return "v1:" + b64encode(nonce + ct).decode("ascii")
    # Insecure fallback (XOR with key stream) — flagged as v0.
    k = _key()
    out = bytes(b ^ k[i % len(k)] for i, b in enumerate(raw))
    return "v0:" + b64encode(out).decode("ascii")


def decrypt_field(token: str | None) -> str | None:
    if token is None or token == "":
        return token
    try:
        if token.startswith("v1:") and HAVE_AESGCM:
            data = b64decode(token[3:])
            nonce, ct = data[:12], data[12:]
            return AESGCM(_key()).decrypt(nonce, ct, None).decode("utf-8")
        if token.startswith("v0:"):
            data = b64decode(token[3:])
            k = _key()
            return bytes(b ^ k[i % len(k)] for i, b in enumerate(data)).decode("utf-8")
        # No prefix — assume it's stored cleartext (legacy/test fixture).
        return token
    except Exception:
        return None


# ---- File-level envelope (database at-rest) ------------------------------

_ENC_MAGIC = b"CRHGC1\x00"  # 7-byte magic + version sentinel


def encrypt_bytes_at_rest(raw: bytes) -> bytes:
    """Envelope-encrypt a byte buffer for at-rest storage.

    Layout (AES-GCM): ``CRHGC1\\x00 || nonce(12) || aesgcm(plain)``.
    Layout (XOR fallback): ``CRHGC0\\x00 || xor(plain)``.

    Pure in-memory transform — used by the in-memory SQLite path so the
    plaintext database never has to touch disk.
    """
    if HAVE_AESGCM:
        nonce = secrets.token_bytes(12)
        ct = AESGCM(_key()).encrypt(nonce, raw, None)
        return _ENC_MAGIC + nonce + ct
    k = _key()
    return b"CRHGC0\x00" + bytes(b ^ k[i % len(k)] for i, b in enumerate(raw))


def decrypt_bytes_at_rest(blob: bytes) -> bytes:
    """Reverse of ``encrypt_bytes_at_rest``. Returns plaintext bytes."""
    if blob.startswith(_ENC_MAGIC) and HAVE_AESGCM:
        nonce, ct = blob[7:19], blob[19:]
        return AESGCM(_key()).decrypt(nonce, ct, None)
    if blob.startswith(b"CRHGC0\x00"):
        k = _key()
        body = blob[7:]
        return bytes(b ^ k[i % len(k)] for i, b in enumerate(body))
    # Unknown / legacy plaintext — return unchanged so upgrades from
    # earlier versions don't lose data.
    return blob


def encrypt_file_at_rest(plain_path: Path, encrypted_path: Path) -> None:
    """Envelope-encrypt a file (e.g., the SQLite DB) for at-rest storage.

    Plaintext input is *not* removed by this function — callers decide
    whether to ``unlink()`` it after the encrypted blob is durably written.
    """
    encrypted_path.write_bytes(encrypt_bytes_at_rest(plain_path.read_bytes()))
    try:
        os.chmod(encrypted_path, 0o600)
    except OSError:
        pass


def decrypt_file_at_rest(encrypted_path: Path, plain_path: Path) -> None:
    """Reverse of ``encrypt_file_at_rest``. Writes plaintext to disk."""
    plain_path.write_bytes(decrypt_bytes_at_rest(encrypted_path.read_bytes()))
    try:
        os.chmod(plain_path, 0o600)
    except OSError:
        pass


# ---- Field masking -------------------------------------------------------

def mask_email(value: str | None) -> str:
    if not value:
        return ""
    if "@" not in value:
        return "***"
    local, domain = value.split("@", 1)
    return (local[:1] + "***@" + domain) if local else "***@" + domain


def mask_phone(value: str | None) -> str:
    if not value:
        return ""
    digits = [c for c in value if c.isdigit()]
    if len(digits) < 4:
        return "***"
    return "(***) ***-" + "".join(digits[-4:])


def mask_ssn_last4(value: str | None) -> str:
    if not value:
        return ""
    digits = [c for c in value if c.isdigit()]
    return "***-**-" + "".join(digits[-4:]) if digits else "***"
