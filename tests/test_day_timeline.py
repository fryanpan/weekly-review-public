"""Hands-on by time of day (the user's ask on the Sep 14 week page, 2026-09-24)."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from weekly_review.day_timeline import (
    blocks,
    flatten,
    hours_split,
    labeled_presence_intervals,
)
from weekly_review.effort import _union_seconds, presence_hours_by_task
from weekly_review.events import Event
from weekly_review.segmentation import Task

LA = ZoneInfo("America/Los_Angeles")


def _t(day, h, m=0):
    """2026-09-<day> h:m in LA (Sep 14 2026 is a Monday)."""
    return datetime(2026, 9, day, h, m, tzinfo=LA)


def _msg(ts, project, kind="user_message", actor="user", words=20, session="s"):
    return Event(ts=ts.astimezone(timezone.utc).isoformat(), actor=actor, project=project, session_id=session,
                 source_system="claude_code", source_machine="m", kind=kind, seq=0,
                 metadata={"words": words})


def test_labeled_union_equals_published_presence():
    evs = [
        _msg(_t(14, 10, 0), "a"), _msg(_t(14, 10, 4), "a"), _msg(_t(14, 10, 9), "b"),
        _msg(_t(14, 13, 0), "b"), _msg(_t(14, 13, 30), "a"),
        _msg(_t(14, 12, 59), "a", kind="agent_message", actor="agent", words=600),
    ]
    evs.sort(key=lambda e: e.ts)
    week = Task(task_id="w", project="all", title="w")
    week.event_ids.extend(range(len(evs)))
    want = presence_hours_by_task([week], evs)["w"]
    lab = labeled_presence_intervals(evs)
    got = _union_seconds([(s, e) for s, e, _ in lab]) / 3600
    assert abs(got - want) < 1e-9
    assert abs(sum((e - s).total_seconds() for s, e, _ in flatten(lab)) / 3600 - want) < 1e-9


def test_switching_inside_the_tolerance_reads_as_both_projects():
    evs = [_msg(_t(14, 10, 0), "a"), _msg(_t(14, 10, 8), "b")]
    lab = labeled_presence_intervals(evs)
    assert frozenset({"a", "b"}) in {p for *_, p in lab}


def test_flatten_splits_overlaps_and_merges_equal_neighbours():
    a, b = frozenset({"a"}), frozenset({"b"})
    pieces = flatten([(_t(14, 10), _t(14, 11), a), (_t(14, 10, 30), _t(14, 12), b),
                      (_t(14, 12), _t(14, 12, 30), b)])
    assert pieces == [
        (_t(14, 10), _t(14, 10, 30), a),
        (_t(14, 10, 30), _t(14, 11), a | b),
        (_t(14, 11), _t(14, 12, 30), b),
    ]


def test_blocks_join_gaps_up_to_five_minutes_only():
    p = frozenset({"a"})
    got = blocks([(_t(14, 10), _t(14, 10, 10), p), (_t(14, 10, 15), _t(14, 10, 20), p),
                  (_t(14, 10, 26), _t(14, 10, 30), p)])
    assert got == [(_t(14, 10), _t(14, 10, 20)), (_t(14, 10, 26), _t(14, 10, 30))]


def test_hours_split_cuts_at_six_pm_and_counts_weekends_apart():
    p = frozenset({"a"})
    pieces = [
        (_t(14, 17), _t(14, 19), p),     # Mon: 1h in hours, 1h evening
        (_t(19, 10), _t(19, 11), p),     # Sat: weekend, never in hours
    ]
    h = hours_split(pieces, LA)
    assert abs(h.total_h - 3) < 1e-9
    assert abs(h.in_hours_h - 1) < 1e-9
    assert abs(h.weekday_off_h - 1) < 1e-9
    assert abs(h.weekend_h - 1) < 1e-9
    # the Monday block is half in hours, so it counts as an in-hours block
    assert h.in_blocks_min == [120.0] and h.out_blocks_min == [60.0]


def test_hours_split_across_a_dst_change():
    # 2026-11-01 is the US fall-back Sunday; the Monday after must still split at 9am local
    p = frozenset({"a"})
    s = datetime(2026, 11, 2, 8, tzinfo=LA)
    h = hours_split([(s, s + timedelta(hours=2), p)], LA)
    assert abs(h.in_hours_h - 1) < 1e-9
