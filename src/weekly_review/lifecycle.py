"""Task status: open / shipped / abandoned."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .events import Event
from .segmentation import Task


ABANDON_DAYS = 30
SHIPPED_KINDS = {"pr_merged", "notion_published", "doc_committed"}


def compute_statuses(tasks: list[Task], events: list[Event], now: datetime | None = None) -> dict[str, str]:
    now = now or datetime.now(timezone.utc)
    statuses: dict[str, str] = {}
    for task in tasks:
        attached_events = [events[i] for i in task.event_ids if i < len(events)]
        has_ship = any(ev.kind in SHIPPED_KINDS for ev in attached_events)
        if has_ship:
            statuses[task.task_id] = "shipped"
            continue
        if task.last_event_ts is None:
            statuses[task.task_id] = "abandoned"
            continue
        gap = now - task.last_event_ts
        if gap > timedelta(days=ABANDON_DAYS):
            statuses[task.task_id] = "abandoned"
        else:
            statuses[task.task_id] = "open"
    return statuses
