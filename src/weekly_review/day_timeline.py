"""When in the day he was hands-on, and on which projects.

The user's ask on the Sep 14 week page (2026-09-24): a section showing hands-on
by day with intra-day detail, coloured by single or multiple projects, to answer
"to what extent am I working business hours, and to what extent am I fitting
things in". Plus the share of hands-on inside and outside work hours, and the
typical length of a work block in each.

The intervals are the whole-week presence model — the same ones behind the
week's published hands-on (46.2h on Sep 14) — with a project label on each piece,
so the chart adds up to the figure it sits under. A modelled reading and typing
window takes its event's project. A stitched stretch between two consecutive
stamps takes both endpoints' projects, so switching from one project to another
inside the tolerance reads as time on both.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from statistics import median

from .effort import (
    PRESENCE_STITCH_SEC,
    _bryan_intervals_for_task,
    _make_prior_agent,
    _parse_ts,
)
from .events import Event
from .segmentation import Task

BLOCK_GAP_SEC = 5 * 60   # the user's suggestion: hands-on joins into one block across gaps under 5 min
WORK_START_H = 9
WORK_END_H = 18          # Monday to Friday, 9am to 6pm local

Labeled = tuple[datetime, datetime, frozenset[str]]


def labeled_presence_intervals(
    events: list[Event], stitch_sec: float = PRESENCE_STITCH_SEC
) -> list[Labeled]:
    """The whole-week presence intervals, each with the projects it belongs to.

    Their union equals `presence_hours_by_task` over a task holding every event:
    the same windows and the same stitched runs, only labelled.
    """
    prior_agent = _make_prior_agent(events)
    out: list[Labeled] = []
    stamps: list[tuple[datetime, str]] = []
    for idx, ev in enumerate(events):
        if ev.kind not in ("user_message", "review_session"):
            continue
        one = Task(task_id="_", project=ev.project, title="_")
        one.event_ids.append(idx)
        label = frozenset({ev.project})
        out.extend((s, e, label) for s, e in _bryan_intervals_for_task(one, events, prior_agent))
        stamps.append((_parse_ts(ev.ts), ev.project))
    stamps.sort(key=lambda r: r[0])
    gap = timedelta(seconds=stitch_sec)
    for (t0, p0), (t1, p1) in zip(stamps, stamps[1:]):
        if t1 - t0 <= gap and t1 > t0:
            out.append((t0, t1, frozenset({p0, p1})))
    return out


def flatten(intervals: list[Labeled]) -> list[Labeled]:
    """Non-overlapping pieces covering the union, each carrying every project
    active in it. Adjacent pieces with the same project set are merged."""
    points = sorted({t for s, e, _ in intervals if e > s for t in (s, e)})
    if not points:
        return []
    # Sweep: +label at start, -label at end.
    starts: dict[datetime, list[frozenset[str]]] = {}
    ends: dict[datetime, list[frozenset[str]]] = {}
    for s, e, lab in intervals:
        if e > s:
            starts.setdefault(s, []).append(lab)
            ends.setdefault(e, []).append(lab)
    active: dict[str, int] = {}
    out: list[Labeled] = []
    for a, b in zip(points, points[1:]):
        for lab in ends.get(a, ()):
            for p in lab:
                active[p] -= 1
                if not active[p]:
                    del active[p]
        for lab in starts.get(a, ()):
            for p in lab:
                active[p] = active.get(p, 0) + 1
        if not active:
            continue
        cur = frozenset(active)
        if out and out[-1][1] == a and out[-1][2] == cur:
            out[-1] = (out[-1][0], b, cur)
        else:
            out.append((a, b, cur))
    return out


def blocks(pieces: list[Labeled], gap_sec: float = BLOCK_GAP_SEC) -> list[tuple[datetime, datetime]]:
    """Work blocks: the union joined across silences of at most `gap_sec`."""
    out: list[tuple[datetime, datetime]] = []
    for s, e, _ in sorted(pieces):
        if out and (s - out[-1][1]).total_seconds() <= gap_sec:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _in_hours_seconds(s: datetime, e: datetime, tz: tzinfo) -> float:
    """Seconds of [s, e) falling Monday-Friday between WORK_START_H and WORK_END_H local."""
    s, e = s.astimezone(tz), e.astimezone(tz)
    total = 0.0
    day = s.replace(hour=0, minute=0, second=0, microsecond=0)
    while day < e:
        if day.weekday() < 5:
            w0 = day.replace(hour=WORK_START_H)
            w1 = day.replace(hour=WORK_END_H)
            lo, hi = max(s, w0), min(e, w1)
            if hi > lo:
                total += (hi - lo).total_seconds()
        day = (day + timedelta(days=1, hours=2)).replace(hour=0)  # safe across a DST change
    return total


def _weekend_seconds(s: datetime, e: datetime, tz: tzinfo) -> float:
    s, e = s.astimezone(tz), e.astimezone(tz)
    total = 0.0
    day = s.replace(hour=0, minute=0, second=0, microsecond=0)
    while day < e:
        nxt = (day + timedelta(days=1, hours=2)).replace(hour=0)
        if day.weekday() >= 5:
            lo, hi = max(s, day), min(e, nxt)
            if hi > lo:
                total += (hi - lo).total_seconds()
        day = nxt
    return total


@dataclass
class HoursSplit:
    total_h: float
    in_hours_h: float
    weekday_off_h: float     # weekday, before 9am or after 6pm
    weekend_h: float
    in_blocks_min: list[float]
    out_blocks_min: list[float]

    @property
    def in_share(self) -> float:
        return self.in_hours_h / self.total_h if self.total_h else 0.0

    @staticmethod
    def med(xs: list[float]) -> float | None:
        return median(xs) if xs else None


def hours_split(pieces: list[Labeled], tz: tzinfo, gap_sec: float = BLOCK_GAP_SEC) -> HoursSplit:
    """Hands-on inside and outside work hours, and block lengths on each side.

    A block counts as in-hours when at least half of its hands-on time is; the
    time itself is always split exactly at the 9am / 6pm boundaries.
    """
    total = inh = wknd = 0.0
    for s, e, _ in pieces:
        total += (e - s).total_seconds()
        inh += _in_hours_seconds(s, e, tz)
        wknd += _weekend_seconds(s, e, tz)
    in_b: list[float] = []
    out_b: list[float] = []
    for bs, be in blocks(pieces, gap_sec):
        inside = sum(_in_hours_seconds(max(s, bs), min(e, be), tz) for s, e, _ in pieces if e > bs and s < be)
        present = sum((min(e, be) - max(s, bs)).total_seconds() for s, e, _ in pieces if e > bs and s < be)
        (in_b if present and inside / present >= 0.5 else out_b).append((be - bs).total_seconds() / 60)
    return HoursSplit(
        total_h=total / 3600, in_hours_h=inh / 3600, weekday_off_h=(total - inh - wknd) / 3600,
        weekend_h=wknd / 3600, in_blocks_min=in_b, out_blocks_min=out_b,
    )
