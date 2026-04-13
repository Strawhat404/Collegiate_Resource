"""Catalog-attached resources cannot be published without reviewer approval."""
from __future__ import annotations
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

from backend.services.auth import BizError


def test_publish_blocked_without_catalog_approval(container, admin_session):
    r = container.resources.create_resource(admin_session, "Algebra Notes")
    v = container.resources.add_version(admin_session, r.id, "v1", "body")
    container.catalog.attach(admin_session, r.id, node_id=None,
                             type_code=None)
    with pytest.raises(BizError) as ei:
        container.resources.publish_version(admin_session, v.id)
    assert ei.value.code == "NOT_APPROVED"


def test_publish_allowed_after_approval(container, admin_session):
    r = container.resources.create_resource(admin_session, "Geometry Notes")
    v = container.resources.add_version(admin_session, r.id, "v1", "body")
    container.catalog.attach(admin_session, r.id, node_id=None,
                             type_code=None)
    container.catalog.submit_for_review(admin_session, r.id)
    container.catalog.review(admin_session, r.id, "approve", "ok")
    pub = container.resources.publish_version(admin_session, v.id)
    assert pub.status == "published"


def test_publish_blocked_when_not_attached_to_catalog(container, admin_session):
    """Standalone resources MUST be attached + approved before publishing.

    Closes the prior bypass that let unattached resources skip the catalog
    governance gate.
    """
    r = container.resources.create_resource(admin_session, "Loose Page")
    v = container.resources.add_version(admin_session, r.id, "v1", "body")
    with pytest.raises(BizError) as ei:
        container.resources.publish_version(admin_session, v.id)
    assert ei.value.code == "NOT_ATTACHED"


def _semver(container, resource_id):
    from backend import db
    row = db.get_connection().execute(
        "SELECT semver FROM resource_catalog WHERE resource_id=?",
        (resource_id,)).fetchone()
    return row["semver"] if row else None


def test_publish_bumps_semver_atomically(container, admin_session):
    """Publishing a catalog-attached resource MUST also bump semver in the
    same transaction. Coupling is enforced by ResourceService.publish_version
    (no separate caller step required)."""
    r = container.resources.create_resource(admin_session, "Calc Notes")
    v = container.resources.add_version(admin_session, r.id, "v1", "body")
    container.catalog.attach(admin_session, r.id, node_id=None, type_code=None)
    container.catalog.submit_for_review(admin_session, r.id)
    container.catalog.review(admin_session, r.id, "approve", "ok")
    before = _semver(container, r.id)
    pub = container.resources.publish_version(admin_session, v.id)
    after = _semver(container, r.id)
    assert pub.status == "published"
    assert before is not None and after is not None
    assert before != after, "semver must change on publish"
    # default level is 'minor': 0.1.0 -> 0.2.0
    assert after == "0.2.0", f"expected 0.2.0 after minor bump, got {after}"


def test_publish_with_semver_level_major(container, admin_session):
    r = container.resources.create_resource(admin_session, "Trig Notes")
    v = container.resources.add_version(admin_session, r.id, "v1", "body")
    container.catalog.attach(admin_session, r.id, node_id=None, type_code=None)
    container.catalog.submit_for_review(admin_session, r.id)
    container.catalog.review(admin_session, r.id, "approve", "ok")
    container.resources.publish_version(admin_session, v.id,
                                        semver_level="major")
    assert _semver(container, r.id) == "1.0.0"


def test_catalog_publish_with_semver_delegates_atomically(container, admin_session):
    """publish_with_semver must produce BOTH a published version AND a bumped
    semver — no orphan path."""
    r = container.resources.create_resource(admin_session, "Stats Notes")
    container.resources.add_version(admin_session, r.id, "v1", "body")
    container.catalog.attach(admin_session, r.id, node_id=None, type_code=None)
    container.catalog.submit_for_review(admin_session, r.id)
    container.catalog.review(admin_session, r.id, "approve", "ok")
    new_v = container.catalog.publish_with_semver(admin_session, r.id,
                                                  level="patch")
    assert new_v == "0.1.1"
    versions = container.resources.list_versions(admin_session, r.id)
    assert any(v.status == "published" for v in versions)
