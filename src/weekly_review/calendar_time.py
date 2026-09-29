"""Calendar time and wall-clock speedup (method Step 7, 2026-09-18).

`wall_clock_hours` is calendar time: every hour between two moments counts,
nights and weekends included. For a week it is the window's days × 24 (168).
Per board task it runs from the first agent work on any unit linked to it to
the task's done transition. Speedup compares an expert's calendar time,
8 hours per working day, with the measured one: (est_h / 8) ÷ (wall / 24).
No weekend adjustment (the user, 2026-09-17).

Distinct from `labor_hours` (user ∪ agent, pauses removed), which is what the
column called wall_clock_hours held before 2026-09-18.
"""
from __future__ import annotations

from datetime import datetime, timezone
from statistics import median
from typing import Any

from .effort import first_agent_work_ts
from .events import Event
from .segmentation import Task

EXPERT_HOURS_PER_DAY = 8.0
HOURS_PER_DAY = 24.0

SPEEDUP_FORMULA = (
    "wall-clock speedup = (est_h / 8) ÷ (wall_clock_hours / 24): the calendar "
    "days an expert would take at 8h a day, over the calendar days it took, "
    "every calendar day counted. wall_clock_hours is calendar time — 168 for a "
    "week; per board task, first agent work on it to its done transition. "
    "labor_hours is agent time and hands-on time unioned with pauses removed "
    "(the figure called wall_clock_hours before 2026-09-18)."
)


def speedup(est_h: float, wall_clock_hours: float) -> float | None:
    """(est_h / 8) ÷ (wall / 24). None when wall is not positive or est is 0."""
    if wall_clock_hours <= 0 or est_h <= 0:
        return None
    return (est_h / EXPERT_HOURS_PER_DAY) / (wall_clock_hours / HOURS_PER_DAY)


def week_wall_clock_hours(period_start: datetime, period_end: datetime) -> float:
    """Calendar hours in the window; `period_end` is inclusive by date, so a
    Mon–Sun window is 7 days = 168h."""
    days = (period_end.date() - period_start.date()).days + 1
    return max(0, days) * HOURS_PER_DAY


def done_ts_by_board_task(board_tasks: list[Any], until_ms: int | None = None) -> dict[str, int]:
    """Board task id → epoch ms of its LAST `done` transition at or before
    `until_ms` (the window end). A task never done, or done only after the
    window, is absent: the window's units cannot be priced against calendar
    days outside it. The last transition wins so a reopened-and-redone task
    ends at its real finish."""
    out: dict[str, int] = {}
    for bt in board_tasks:
        for ts, status in bt.transitions:
            if status == "done" and (until_ms is None or int(ts) <= until_ms):
                out[bt.id] = int(ts)
    return out


def board_task_wall_clock(
    tasks: list[Task], events: list[Event], done_ts: dict[str, int],
) -> dict[str, dict[str, Any]]:
    """Per board task: first agent work across the units linked to it, the
    done transition, and the calendar hours between them.

    A board task with no agent work in the window, not done by the window's
    end, or done BEFORE its first agent work here (rework after close, or a
    unit linked late) gets `wall_clock_hours` None — the digest says which,
    rather than printing a zero that reads as instant. The start is the first
    agent work inside this run's window: a board task begun in an earlier
    week measures short here, and the digest prints how many rows that is
    (`started_before_window` is not knowable from one window; see the caller).
    """
    first: dict[str, datetime] = {}
    for task in tasks:
        bt = getattr(task, "board_task", "") or ""
        if not bt:
            continue
        ts = first_agent_work_ts(task, events)
        if ts is None:
            continue
        if bt not in first or ts < first[bt]:
            first[bt] = ts
    out: dict[str, dict[str, Any]] = {}
    ids = set(first) | {t.board_task for t in tasks if getattr(t, "board_task", "")}
    for bt in ids:
        start = first.get(bt)
        end_ms = done_ts.get(bt)
        end = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc) if end_ms else None
        hours = None
        if start is not None and end is not None and end >= start:
            hours = (end - start).total_seconds() / 3600
        out[bt] = {
            "first_agent_ts": start.isoformat() if start else None,
            "done_ts": end.isoformat() if end else None,
            "wall_clock_hours": hours,
        }
    return out


def board_task_speedups(
    tasks: list[Task], est_h: dict[str, float], calendar: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """One row per board task with a measured calendar span: Σest of its
    units, wall_clock_hours, speedup. Sorted by est descending."""
    est_by_bt: dict[str, float] = {}
    units_by_bt: dict[str, int] = {}
    for t in tasks:
        bt = getattr(t, "board_task", "") or ""
        if not bt:
            continue
        est_by_bt[bt] = est_by_bt.get(bt, 0.0) + float(est_h.get(t.task_id, 0.0) or 0.0)
        units_by_bt[bt] = units_by_bt.get(bt, 0) + 1
    rows = []
    for bt, cal in calendar.items():
        wall = cal.get("wall_clock_hours")
        est = est_by_bt.get(bt, 0.0)
        first, done = cal.get("first_agent_ts"), cal.get("done_ts")
        if wall is not None:
            why = ""
        elif not first:
            why = "no agent work"
        elif not done:
            why = "not done"
        else:
            why = "done before its agent work"
        rows.append({
            "why_no_span": why,
            "board_task": bt,
            "units": units_by_bt.get(bt, 0),
            "est_h": est,
            "wall_clock_hours": wall,
            "speedup": speedup(est, wall) if wall is not None else None,
            "first_agent_ts": cal.get("first_agent_ts"),
            "done_ts": cal.get("done_ts"),
        })
    rows.sort(key=lambda r: -r["est_h"])
    return rows


def median_speedup(rows: list[dict[str, Any]]) -> tuple[float | None, int]:
    vals = [r["speedup"] for r in rows if r.get("speedup") is not None]
    return (median(vals) if vals else None), len(vals)
