"""Permission helpers and decorators."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from functools import wraps
from typing import Iterable


@dataclass
class Session:
    user_id: int
    username: str
    full_name: str
    roles: set[str] = field(default_factory=set)
    permissions: set[str] = field(default_factory=set)
    mask_unlock_until: datetime | None = None

    def has(self, code: str) -> bool:
        return code in self.permissions

    def has_any(self, codes: Iterable[str]) -> bool:
        return any(c in self.permissions for c in codes)

    def mask_unlocked(self) -> bool:
        return (self.mask_unlock_until is not None
                and self.mask_unlock_until > datetime.utcnow())


class PermissionDenied(Exception):
    def __init__(self, code: str):
        super().__init__(f"Permission denied: {code}")
        self.code = code


def requires(*codes: str):
    """Decorator: enforce that the first arg (session) holds all listed perms."""
    def deco(fn):
        @wraps(fn)
        def wrapped(self, session: Session, *args, **kw):
            for c in codes:
                if not session.has(c):
                    raise PermissionDenied(c)
            return fn(self, session, *args, **kw)
        return wrapped
    return deco
