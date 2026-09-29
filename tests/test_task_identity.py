"""Task-id carry-forward across re-segmentation (event-set overlap matching)."""
import sqlite3

from weekly_review.events import Event
from weekly_review.segmentation import Task
from weekly_review.task_identity import carry_forward_task_ids


def _ev(seq, kind="user_message", actor="user"):
    return Event(ts=f"2026-06-10T{seq:02d}:00:00Z", actor=actor, project="p",
                 session_id="s", source_system="x", source_machine="mm",
                 kind=kind, seq=seq, content_snippet=f"msg {seq}",
                 metadata={"words": 2})


def _prior_db(tmp_path, name, task_events: dict[str, list[str]]):
    """Minimal prior-run DB: just the task_events table the matcher reads."""
    run_dir = tmp_path / name
    run_dir.mkdir()
    db = run_dir / "run.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE task_events (task_id TEXT, event_id TEXT, rule TEXT, detail TEXT)")
    for tid, eids in task_events.items():
        for eid in eids:
            conn.execute("INSERT INTO task_events VALUES (?,?,NULL,NULL)", (tid, eid))
    conn.commit()
    conn.close()
    return db


def test_identical_event_set_inherits_prior_id(tmp_path):
    events = [_ev(i) for i in range(4)]
    eids = [e.event_id for e in events]
    db = _prior_db(tmp_path, "run-A", {"old-id-123": eids})
    t = Task(task_id="fresh-id-999", project="p", title="t", event_ids=[0, 1, 2, 3])
    rewrites = carry_forward_task_ids([t], events, [db])
    assert t.task_id == "old-id-123"
    assert rewrites == [("fresh-id-999", "old-id-123", 1.0)]
    assert any(s.get("rule") == "id_carry_forward" for s in t.segmentation_trace)


def test_weak_overlap_keeps_fresh_id(tmp_path):
    events = [_ev(i) for i in range(10)]
    eids = [e.event_id for e in events]
    # Old task shares only 2 of 10 events with the new task: j = 0.2, cov = 2/8.
    db = _prior_db(tmp_path, "run-A", {"old-id": eids[:8]})
    t = Task(task_id="fresh", project="p", title="t", event_ids=[6, 7, 8, 9])
    # inter=2 (events 6,7), union=10, j=0.2; cov=2/8=0.25 -> s=0.25 < 0.5
    carry_forward_task_ids([t], events, [db])
    assert t.task_id == "fresh"


def test_stable_id_is_never_rewritten(tmp_path):
    events = [_ev(i) for i in range(4)]
    eids = [e.event_id for e in events]
    db = _prior_db(tmp_path, "run-A", {"stable-id": eids, "other-old": ["x:y:0"]})
    t = Task(task_id="stable-id", project="p", title="t", event_ids=[0, 1, 2, 3])
    rewrites = carry_forward_task_ids([t], events, [db])
    assert rewrites == []
    assert t.task_id == "stable-id"


def test_prior_id_still_live_in_current_run_is_not_stolen(tmp_path):
    events = [_ev(i) for i in range(6)]
    eids = [e.event_id for e in events]
    db = _prior_db(tmp_path, "run-A", {"live-id": eids[:4]})
    # live-id still exists in the current run (a different task kept it).
    keeper = Task(task_id="live-id", project="p", title="keeper", event_ids=[4, 5])
    fresh = Task(task_id="fresh", project="p", title="fresh", event_ids=[0, 1, 2, 3])
    carry_forward_task_ids([keeper, fresh], events, [db])
    assert fresh.task_id == "fresh"
    assert keeper.task_id == "live-id"


def test_greedy_assignment_one_old_id_one_winner(tmp_path):
    events = [_ev(i) for i in range(8)]
    eids = [e.event_id for e in events]
    db = _prior_db(tmp_path, "run-A", {"old-id": eids[:4]})
    # Both new tasks overlap the old task; the stronger match wins the id.
    strong = Task(task_id="strong", project="p", title="s", event_ids=[0, 1, 2, 3])
    weak = Task(task_id="weak", project="p", title="w", event_ids=[2, 3, 6, 7])
    rewrites = carry_forward_task_ids([strong, weak], events, [db])
    assert strong.task_id == "old-id"
    assert weak.task_id == "weak"
    assert len(rewrites) == 1


def test_coverage_rule_absorbed_small_task_carries(tmp_path):
    events = [_ev(i) for i in range(10)]
    eids = [e.event_id for e in events]
    # Old small task (3 events) fully absorbed into a bigger new task (10):
    # j = 3/10 = 0.3 (>= 0.2 gate), cov = 3/3 = 1.0 -> carries.
    db = _prior_db(tmp_path, "run-A", {"old-small": eids[:3]})
    t = Task(task_id="fresh-big", project="p", title="t", event_ids=list(range(10)))
    carry_forward_task_ids([t], events, [db])
    assert t.task_id == "old-small"


def test_newest_run_wins_across_multiple_priors(tmp_path):
    events = [_ev(i) for i in range(4)]
    eids = [e.event_id for e in events]
    older = _prior_db(tmp_path, "run-20260601", {"ancient-id": eids})
    newer = _prior_db(tmp_path, "run-20260701", {"recent-id": eids})
    t = Task(task_id="fresh", project="p", title="t", event_ids=[0, 1, 2, 3])
    # Paths are passed newest first, mirroring the CLI's descending sort.
    carry_forward_task_ids([t], events, [newer, older])
    assert t.task_id == "recent-id"


def test_missing_or_empty_dbs_are_ignored(tmp_path):
    events = [_ev(0)]
    t = Task(task_id="fresh", project="p", title="t", event_ids=[0])
    assert carry_forward_task_ids([t], events, [tmp_path / "nope" / "run.sqlite"]) == []
    assert t.task_id == "fresh"
