"""In-process event bus."""
from __future__ import annotations
from collections import defaultdict
from typing import Callable, Any

# Event names (constants used elsewhere)
STUDENT_CREATED = "STUDENT_CREATED"
STUDENT_UPDATED = "STUDENT_UPDATED"
BED_ASSIGNED = "BED_ASSIGNED"
BED_VACATED = "BED_VACATED"
BED_TRANSFERRED = "BED_TRANSFERRED"
RESOURCE_PUBLISHED = "RESOURCE_PUBLISHED"
RESOURCE_UNPUBLISHED = "RESOURCE_UNPUBLISHED"
RESOURCE_HELD = "RESOURCE_HELD"
CASE_SUBMITTED = "CASE_SUBMITTED"
CASE_DECIDED = "CASE_DECIDED"
VIOLATION_OPENED = "VIOLATION_OPENED"
VIOLATION_RESOLVED = "VIOLATION_RESOLVED"


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Callable[[dict[str, Any]], None]]] = defaultdict(list)

    def subscribe(self, event: str, fn: Callable[[dict[str, Any]], None]) -> None:
        self._subs[event].append(fn)

    def publish(self, event: str, payload: dict[str, Any]) -> None:
        for fn in list(self._subs.get(event, ())):
            try:
                fn(payload)
            except Exception:
                # Subscribers should not break publishers.
                import traceback, sys
                traceback.print_exc(file=sys.stderr)


bus = EventBus()
