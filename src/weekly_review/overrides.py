"""Apply ~/.config/weekly-review/overrides.yaml to segmented tasks."""
from __future__ import annotations

from typing import Any

from .segmentation import Task


def apply_overrides(tasks: list[Task], overrides: dict[str, Any]) -> list[Task]:
    tasks_by_id = {t.task_id: t for t in tasks}
    removed: set[str] = set()

    # Merges
    for merge in overrides.get("merges", []) or []:
        ids = merge.get("tasks", [])
        if not ids or any(tid not in tasks_by_id for tid in ids):
            continue
        primary = tasks_by_id[ids[0]]
        if "into_title" in merge:
            primary.title = merge["into_title"]
        for tid in ids[1:]:
            other = tasks_by_id[tid]
            primary.event_ids.extend(other.event_ids)
            primary.issue_refs |= other.issue_refs
            if other.last_event_ts and (primary.last_event_ts is None or other.last_event_ts > primary.last_event_ts):
                primary.last_event_ts = other.last_event_ts
            primary.segmentation_trace.append({"rule": "override_merge", "detail": f"from {tid}"})
            removed.add(tid)

    # Title relabels
    for relabel in overrides.get("relabels", []) or []:
        tid = relabel.get("task_id")
        new_title = relabel.get("new_title")
        if tid in tasks_by_id and new_title:
            tasks_by_id[tid].title = new_title
            tasks_by_id[tid].segmentation_trace.append({"rule": "override_relabel"})

    # Ignores
    for tid in overrides.get("ignores", []) or []:
        if tid in tasks_by_id:
            removed.add(tid)

    # Splits: not implemented in v1; the user can merge-then-split via YAML later if needed.

    return [t for t in tasks if t.task_id not in removed]
