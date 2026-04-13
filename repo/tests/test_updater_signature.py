"""Update packages without a valid signature must be rejected at every layer.

Signed packages are produced with a freshly-generated RSA-3072 keypair; the
public key is written to the location the updater reads from.
"""
from __future__ import annotations
import json
import zipfile

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    class _Pytest:
        @staticmethod
        def raises(exc):
            class _Ctx:
                value = None
                def __enter__(self_inner):
                    return self_inner
                def __exit__(self_inner, et, ev, tb):
                    if et is None:
                        raise AssertionError(f"expected {exc.__name__}")
                    if issubclass(et, exc):
                        self_inner.value = ev
                        return True
                    return False
            return _Ctx()
    pytest = _Pytest()  # type: ignore

from backend import config
from backend.services.auth import BizError
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


def _provision_test_signing_key():
    """Generate an RSA-3072 keypair and write the public PEM to the location
    the updater expects. Returns the private key for signing test packages.
    """
    priv = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo)
    pk_path = config.update_signing_key_path()
    pk_path.parent.mkdir(parents=True, exist_ok=True)
    pk_path.write_bytes(pem)
    return priv


def _signed_pkg(path, priv, *, version="9.0.0",
                payload=(("payload/note.txt", b"hi"),)):
    manifest = json.dumps({"version": version}).encode("utf-8")
    sig = priv.sign(
        manifest,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256())
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("update.json", manifest)
        zf.writestr("update.json.sig", sig)
        for member, body in payload:
            zf.writestr(member, body)
    return path


def _unsigned_pkg(path, version="9.0.0"):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("update.json", json.dumps({"version": version}))
        zf.writestr("payload/note.txt", b"hi")
    return path


def test_unsigned_package_rejected(container, admin_session, tmp_path):
    pkg = _unsigned_pkg(tmp_path / "u.zip")
    with pytest.raises(BizError) as ei:
        container.updater.apply_package(admin_session, pkg)
    assert ei.value.code == "SIGNATURE_REQUIRED"


def test_signed_package_accepted(container, admin_session, tmp_path):
    priv = _provision_test_signing_key()
    pkg = _signed_pkg(tmp_path / "u.zip", priv, version="9.0.0")
    applied = container.updater.apply_package(
        admin_session, pkg, install_dir=str(tmp_path / "install"))
    assert applied.signature_ok is True
    assert applied.version == "9.0.0"


def test_path_traversal_rejected_even_for_signed_package(
        container, admin_session, tmp_path):
    priv = _provision_test_signing_key()
    pkg = _signed_pkg(
        tmp_path / "evil.zip", priv,
        version="x",
        payload=(("payload/../../escape.txt", b"x"),))
    with pytest.raises(BizError):
        container.updater.apply_package(
            admin_session, pkg, install_dir=str(tmp_path / "install"))
