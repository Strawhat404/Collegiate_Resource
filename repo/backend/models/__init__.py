"""Data classes shared between the service layer and the UI."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass
class Page:
    limit: int = 50
    offset: int = 0


@dataclass
class Paged(Generic[T]):
    items: list[T]
    total: int

    def __iter__(self):
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, idx):
        return self.items[idx]

    def __bool__(self) -> bool:
        return bool(self.items)


@dataclass
class User:
    id: int
    username: str
    full_name: str
    disabled: bool = False
    roles: list[str] = field(default_factory=list)


@dataclass
class StudentDTO:
    student_id: str
    full_name: str
    college: str | None = None
    class_year: int | None = None
    email: str | None = None
    phone: str | None = None
    ssn_last4: str | None = None
    housing_status: str = "pending"


@dataclass
class Student:
    id: int
    student_id: str
    full_name: str
    college: str | None
    class_year: int | None
    email: str | None        # already masked or revealed by service
    phone: str | None
    ssn_last4: str | None
    housing_status: str
    created_at: str
    updated_at: str


@dataclass
class StudentSummary:
    id: int
    student_id: str
    full_name: str
    college: str | None
    class_year: int | None
    housing_status: str


@dataclass
class Bed:
    id: int
    building: str
    room: str
    code: str
    occupied: bool


@dataclass
class BedAssignment:
    id: int
    student_id: int
    student_name: str
    bed_id: int
    bed_label: str
    effective_date: str
    end_date: str | None
    reason: str | None
    created_at: str | None = None
    operator_id: int | None = None


@dataclass
class Resource:
    id: int
    title: str
    category: str | None
    status: str
    latest_version: int | None
    published_version: int | None


@dataclass
class ResourceVersion:
    id: int
    resource_id: int
    version_no: int
    summary: str | None
    body: str | None
    status: str
    published_at: str | None


@dataclass
class EmployerCase:
    id: int
    employer_id: int
    employer_name: str
    kind: str
    state: str
    reviewer_id: int | None
    decision: str | None
    decided_at: str | None
    notes: str | None


@dataclass
class NotificationMessage:
    id: int
    template_name: str
    subject: str
    body: str
    status: str
    attempts: int
    scheduled_for: str | None
    created_at: str
    read_at: str | None


@dataclass
class SearchHit:
    entity_type: str
    entity_id: int
    title: str
    subtitle: str
    score: float
    open_action: str


@dataclass
class ImportPreview:
    preview_id: str
    accepted: list[dict]
    rejected: list[dict]
    columns: list[str]
    duplicate_strategy: str


@dataclass
class ChangeLogEntry:
    id: int
    ts: str
    actor_id: int | None
    action: str
    payload: dict
