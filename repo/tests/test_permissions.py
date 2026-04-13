"""Object-level authorization checks: student writes require permission."""
from __future__ import annotations
try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    class _Pytest:
        @staticmethod
        def raises(exc):
            class _Ctx:
                def __enter__(self_inner):
                    return self_inner
                def __exit__(self_inner, et, ev, tb):
                    if et is None:
                        raise AssertionError(f"expected {exc.__name__}")
                    return issubclass(et, exc)
            return _Ctx()
    pytest = _Pytest()  # type: ignore

from backend.models import StudentDTO
from backend.permissions import PermissionDenied, Session


def test_student_create_denied_without_permission(container, admin_session):
    # A bare session with no permissions should be rejected.
    bare = Session(user_id=admin_session.user_id, username="x", full_name="X")
    with pytest.raises(PermissionDenied):
        container.students.create(bare, StudentDTO(student_id="X1",
                                                   full_name="X"))


def test_student_update_denied_without_permission(container, admin_session):
    s = container.students.create(admin_session,
                                  StudentDTO(student_id="X2", full_name="Y"))
    bare = Session(user_id=admin_session.user_id, username="x", full_name="X")
    with pytest.raises(PermissionDenied):
        container.students.update(bare, s.id,
                                  StudentDTO(student_id="X2", full_name="Z"))


def test_student_import_denied_without_permission(container, admin_session,
                                                  tmp_path):
    csv_path = tmp_path / "in.csv"
    csv_path.write_text("student_id,full_name,college,class_year,email,phone,housing_status\n"
                        "S99,Foo,LA,2026,,,pending\n")
    bare = Session(user_id=admin_session.user_id, username="x", full_name="X")
    with pytest.raises(PermissionDenied):
        container.students.import_file(bare, csv_path)


def test_admin_can_create(container, admin_session):
    s = container.students.create(admin_session,
                                  StudentDTO(student_id="X3", full_name="OK"))
    assert s.id > 0


def test_coordinator_can_create(container, coordinator_session):
    s = container.students.create(coordinator_session,
                                  StudentDTO(student_id="X4", full_name="OK"))
    assert s.id > 0
