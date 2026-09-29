from datetime import datetime, timezone
from weekly_review.events import Event
from weekly_review.segmentation import segment
from weekly_review.overrides import apply_overrides
from weekly_review.lifecycle import compute_statuses


def _uev(ts, project, text, seq, card="t-AAA"):
    """His comment on a board task: the one message shape that still starts a
    task (a plain transcript message is placed by nothing)."""
    return Event(
        ts=ts, actor="user", project=project, session_id="s",
        source_system="live_feedback", source_machine="mm",
        kind="user_message", seq=seq,
        content_snippet=text, metadata={"words": len(text.split()), "task_id": card},
    )


def test_apply_overrides_merges_two_tasks():
    events = [
        _uev("2026-04-15T10:00:00Z", "weekly-review", "new topic: digest renderer work", 0, card="t-AAA"),
        _uev("2026-04-16T10:00:00Z", "weekly-review", "new topic: calibration anchor flow", 1, card="t-BBB"),
    ]
    tasks = segment(events)
    assert len(tasks) == 2
    t0, t1 = tasks
    overrides = {"merges": [{"tasks": [t0.task_id, t1.task_id], "into_title": "Renderer + calibration"}]}
    merged = apply_overrides(tasks, overrides)
    assert len(merged) == 1
    assert merged[0].title == "Renderer + calibration"
    assert set(merged[0].event_ids) == {0, 1}


def test_apply_overrides_ignore_removes_task():
    events = [_uev("2026-04-15T10:00:00Z", "weekly-review", "spike test", 0)]
    tasks = segment(events)
    overrides = {"ignores": [tasks[0].task_id]}
    kept = apply_overrides(tasks, overrides)
    assert kept == []


def test_apply_overrides_relabels_title():
    events = [_uev("2026-04-15T10:00:00Z", "weekly-review", "initial", 0)]
    tasks = segment(events)
    tid = tasks[0].task_id
    overrides = {"relabels": [{"task_id": tid, "new_title": "New Title"}]}
    out = apply_overrides(tasks, overrides)
    assert out[0].title == "New Title"


def test_apply_overrides_empty_dict_is_noop():
    events = [_uev("2026-04-15T10:00:00Z", "weekly-review", "x", 0)]
    tasks = segment(events)
    out = apply_overrides(tasks, {})
    assert len(out) == 1


def test_lifecycle_shipped_when_pr_merged_event_attached():
    # Nothing places his message now, so the merged PR is the one task here
    # and carries the status on its own.
    pr = Event(
        ts="2026-04-17T12:00:00Z", actor="artifact", project="weekly-review",
        session_id="github:fryanpan/weekly-review",
        source_system="github", source_machine="mm",
        kind="pr_merged", seq=99,
        content_snippet="feat(impl): add x parsing for digest renderer",
        metadata={"number": 7, "url": "https://github.com/x/y/pull/7"},
    )
    typed = Event(
        ts="2026-04-15T10:00:00Z", actor="user", project="weekly-review", session_id="s",
        source_system="claude_transcript", source_machine="mm", kind="user_message", seq=0,
        content_snippet="impl x parsing for digest renderer", metadata={"words": 6},
    )
    events = [typed, pr]
    tasks = segment(events)
    assert len(tasks) == 1 and tasks[0].event_ids == [1]
    statuses = compute_statuses(tasks, events, now=datetime(2026, 4, 19, tzinfo=timezone.utc))
    assert statuses[tasks[0].task_id] == "shipped"


def test_lifecycle_abandoned_after_30d_with_no_artifact():
    events = [_uev("2026-02-01T10:00:00Z", "weekly-review", "old idea", 0)]
    tasks = segment(events)
    statuses = compute_statuses(tasks, events, now=datetime(2026, 4, 19, tzinfo=timezone.utc))
    assert statuses[tasks[0].task_id] == "abandoned"


def test_lifecycle_open_for_recent_task_without_ship():
    events = [_uev("2026-04-18T10:00:00Z", "weekly-review", "in progress", 0)]
    tasks = segment(events)
    statuses = compute_statuses(tasks, events, now=datetime(2026, 4, 19, tzinfo=timezone.utc))
    assert statuses[tasks[0].task_id] == "open"
