"""How many of his messages sit on a measured task.

Until 2026-09-18 this carried a 95% target: the message-grouping rules placed
nearly every message somewhere, so a low number meant a routing break. Those
rules are gone (Decisions, 2026-09-18). A message is placed only when it is a
comment on a board task, so the share is a fact about how much of the week ran
through the board, not a health check, and there is no target. It prints every
run so the reader can see what the classifier (a later task) has left to place.
"""
from __future__ import annotations

from collections import Counter

from .events import Event
from .segmentation import Task


def coverage_report(tasks: list[Task], events: list[Event]) -> dict:
    user_indices = {i for i, ev in enumerate(events) if ev.kind == "user_message"}
    assigned = set()
    for t in tasks:
        for idx in t.event_ids:
            if idx in user_indices:
                assigned.add(idx)

    total = len(user_indices)
    assigned_count = len(assigned)
    unassigned = sorted(user_indices - assigned)
    percent = 100.0 * assigned_count / total if total else 100.0

    unassigned_by_project: Counter[str] = Counter()
    for idx in unassigned:
        unassigned_by_project[events[idx].project] += 1

    return {
        "user_message_events": total,
        "assigned": assigned_count,
        "unassigned": len(unassigned),
        "coverage_percent": round(percent, 2),
        "unassigned_by_project": dict(unassigned_by_project),
    }


def rule_counts(tasks: list[Task]) -> dict[str, int]:
    """How many event placements each segmentation rule made, over the whole run.

    Printed every run. The per-task counts live in the run db
    (`rule_counts_json`); this is the same trace summed, so a rule that is
    meant to be gone shows up here by name if it ever fires again.
    """
    counts: Counter[str] = Counter()
    for t in tasks:
        for step in t.segmentation_trace:
            counts[step.get("rule", "?")] += 1
    return dict(counts.most_common())
