"""The workspace taskId join: what it attaches, and what it refuses to.

The pass exists because the board stamps a `taskId` on every action and a task
card IS a doc, so two events can name the same workspace task without the
segmenter ever putting them together. The tests that matter most here are the
negative ones -- a join that attaches everything it can reach would be a
proximity guess wearing an identity's clothes, and would move published hours
for work that happened in a different week.
"""
from __future__ import annotations

from weekly_review.events import Event
from weekly_review.segmentation import Task, attach_by_workspace_task


def _ev(ts: str, ws: str | None = None, kind: str = "board_action") -> Event:
    md: dict[str, object] = {}
    if ws:
        md["task_id"] = ws
    return Event(
        ts=ts,
        actor="agent",
        project="p",
        session_id="workspace-board:w-1",
        source_system="workspace_board",
        source_machine="mac_mini",
        kind=kind,
        seq=0,
        metadata=md,
    )


def _task(task_id: str, event_ids: list[int], first: str) -> Task:
    from weekly_review.segmentation import _parse_ts
    t = Task(task_id=task_id, project="p", title=task_id)
    for i in event_ids:
        t.touch(i, _parse_ts(first), rule="test")
    return t


def test_an_unattached_event_joins_the_task_holding_its_sibling():
    events = [
        _ev("2026-09-01T10:00:00Z", "t-aaa"),
        _ev("2026-09-01T11:00:00Z", "t-aaa"),
    ]
    tasks = [_task("h1", [0], "2026-09-01T10:00:00Z")]

    res = attach_by_workspace_task(tasks, events)

    assert res["attached"] == 1
    assert sorted(tasks[0].event_ids) == [0, 1]
    assert any(s["rule"] == "workspace_task_join" for s in tasks[0].segmentation_trace)


def test_an_event_with_no_sibling_anywhere_stays_orphaned():
    """The 1,779. A workspace task with no other evidence in the window is work
    that happened outside it; inventing a container reports a week that did not
    happen."""
    events = [
        _ev("2026-09-01T10:00:00Z", "t-aaa"),
        _ev("2026-09-01T11:00:00Z", "t-zzz"),
    ]
    tasks = [_task("h1", [0], "2026-09-01T10:00:00Z")]

    res = attach_by_workspace_task(tasks, events)

    assert res["attached"] == 0
    assert res["orphans_without_anchor"] == 1
    assert tasks[0].event_ids == [0]


def test_an_already_attached_event_is_never_moved():
    """Moving attached events would re-segment and shift published hours for a
    reason nobody measured. The split is reported instead."""
    events = [
        _ev("2026-09-01T10:00:00Z", "t-aaa"),
        _ev("2026-09-01T11:00:00Z", "t-aaa"),
        _ev("2026-09-01T12:00:00Z", "t-aaa"),
    ]
    tasks = [
        _task("h1", [0, 1], "2026-09-01T10:00:00Z"),
        _task("h2", [2], "2026-09-01T12:00:00Z"),
    ]

    res = attach_by_workspace_task(tasks, events)

    assert res["attached"] == 0
    assert res["split"] == 1
    assert tasks[0].event_ids == [0, 1]
    assert tasks[1].event_ids == [2]


def test_the_majority_holder_wins_the_orphan():
    events = [
        _ev("2026-09-01T10:00:00Z", "t-aaa"),
        _ev("2026-09-01T11:00:00Z", "t-aaa"),
        _ev("2026-09-01T12:00:00Z", "t-aaa"),
        _ev("2026-09-01T13:00:00Z", "t-aaa"),
    ]
    tasks = [
        _task("h1", [0], "2026-09-01T10:00:00Z"),
        _task("h2", [1, 2], "2026-09-01T11:00:00Z"),
    ]

    res = attach_by_workspace_task(tasks, events)

    assert res["attached"] == 1
    assert 3 in tasks[1].event_ids


def test_a_tie_breaks_on_the_earlier_task_and_is_stable():
    """Two hosts, one event each. An unstable winner would churn task
    boundaries run to run, and with them every id carry-forward tries to
    match."""
    events = [
        _ev("2026-09-01T10:00:00Z", "t-aaa"),
        _ev("2026-09-01T11:00:00Z", "t-aaa"),
        _ev("2026-09-01T12:00:00Z", "t-aaa"),
    ]
    first = [
        _task("zzz", [0], "2026-09-01T10:00:00Z"),
        _task("aaa", [1], "2026-09-01T11:00:00Z"),
    ]
    second = [
        _task("aaa", [1], "2026-09-01T11:00:00Z"),
        _task("zzz", [0], "2026-09-01T10:00:00Z"),
    ]

    attach_by_workspace_task(first, events)
    attach_by_workspace_task(second, events)

    won_first = [t.task_id for t in first if 2 in t.event_ids]
    won_second = [t.task_id for t in second if 2 in t.event_ids]
    assert won_first == won_second == ["zzz"]


def test_events_without_a_workspace_task_id_are_untouched():
    events = [
        _ev("2026-09-01T10:00:00Z", "t-aaa"),
        _ev("2026-09-01T11:00:00Z", None, kind="user_message"),
    ]
    tasks = [_task("h1", [0], "2026-09-01T10:00:00Z")]

    res = attach_by_workspace_task(tasks, events)

    assert res["attached"] == 0
    assert tasks[0].event_ids == [0]


def test_a_run_with_no_workspace_events_at_all_is_a_no_op():
    events = [_ev("2026-09-01T10:00:00Z", None, kind="user_message")]
    tasks = [_task("h1", [0], "2026-09-01T10:00:00Z")]

    res = attach_by_workspace_task(tasks, events)

    assert res == {"attached": 0, "orphans_without_anchor": 0, "split": 0}
    assert tasks[0].event_ids == [0]


def test_the_joined_event_extends_the_hosting_task_span():
    """A board action is an instant, not an interval -- but a stamped moment is
    still a true fact about when the task was alive."""
    events = [
        _ev("2026-09-01T10:00:00Z", "t-aaa"),
        _ev("2026-09-03T18:00:00Z", "t-aaa"),
    ]
    tasks = [_task("h1", [0], "2026-09-01T10:00:00Z")]

    attach_by_workspace_task(tasks, events)

    assert tasks[0].last_event_ts is not None
    assert tasks[0].last_event_ts.isoformat().startswith("2026-09-03T18:00")
