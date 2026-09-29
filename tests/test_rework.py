"""Tests for rework detection."""
from datetime import datetime, timezone

from weekly_review.events import Event
from weekly_review.segmentation import Task
from weekly_review.rework import (
    score_task,
    score_all_tasks,
    cluster_rework_tasks,
    top_rework_clusters,
    ReworkSignals,
    MIN_SCORE,
    W_FRUSTRATION,
    W_REVERT,
)


def _ev(kind: str, actor: str, ts: str, seq: int, content: str = "", project: str = "p") -> Event:
    return Event(
        ts=ts,
        actor=actor,
        project=project,
        session_id="s",
        source_system="claude_transcript",
        source_machine="mm",
        kind=kind,
        seq=seq,
        content_snippet=content,
        metadata={"words": len(content.split())},
    )


def _task(task_id: str, project: str, event_ids: list[int], events: list[Event]) -> Task:
    t = Task(task_id=task_id, project=project, title="t")
    t.event_ids = event_ids
    if event_ids:
        ts0 = datetime.fromisoformat(events[event_ids[0]].ts.replace("Z", "+00:00"))
        ts1 = datetime.fromisoformat(events[event_ids[-1]].ts.replace("Z", "+00:00"))
        t.first_event_ts = ts0
        t.last_event_ts = ts1
    return t


def test_frustration_marker_detected():
    events = [
        _ev("user_message", "user", "2026-04-28T10:00:00Z", 0, "Bug is still there."),
    ]
    t = _task("a", "bike-map", [0], events)
    sig = score_task(t, events)
    assert sig.frustration_markers == 1
    assert sig.score == W_FRUSTRATION
    assert sig.is_rework  # 3 >= MIN_SCORE


def test_multiple_frustration_markers_in_same_message_count_once():
    events = [
        _ev("user_message", "user", "2026-04-28T10:00:00Z", 0,
            "still broken AND doesn't work"),
    ]
    t = _task("a", "bike-map", [0], events)
    sig = score_task(t, events)
    assert sig.frustration_markers == 1


def test_revert_commit_detected():
    events = [
        _ev("commit", "artifact", "2026-04-28T10:00:00Z", 0,
            "Revert: PR #168 caused exit code 1"),
    ]
    t = _task("a", "p", [0], events)
    sig = score_task(t, events)
    assert sig.revert_commits == 1
    assert sig.score == W_REVERT
    assert sig.is_rework


def test_revert_pr_detected():
    events = [
        _ev("pr_merged", "artifact", "2026-04-28T10:00:00Z", 0,
            "revert: PR #168 (bypassPermissions caused exit code 1)"),
    ]
    t = _task("a", "p", [0], events)
    sig = score_task(t, events)
    assert sig.revert_prs == 1


def test_no_signals_means_not_rework():
    events = [
        _ev("user_message", "user", "2026-04-28T10:00:00Z", 0, "Please make this nice"),
        _ev("commit", "artifact", "2026-04-28T11:00:00Z", 1, "feat: add nice thing"),
    ]
    t = _task("a", "p", [0, 1], events)
    sig = score_task(t, events)
    assert sig.score == 0
    assert not sig.is_rework


def test_routine_word_no_false_positive():
    # "still" in non-rework context shouldn't fire — pattern requires
    # follow-up like "still broken" / "still not working".
    events = [
        _ev("user_message", "user", "2026-04-28T10:00:00Z", 0, "Still need to add docs"),
        _ev("user_message", "user", "2026-04-28T11:00:00Z", 1, "Looks great"),
    ]
    t = _task("a", "p", [0, 1], events)
    sig = score_task(t, events)
    assert sig.frustration_markers == 0


def test_default_back_to_old_marker_detected():
    events = [
        _ev("user_message", "user", "2026-04-28T10:00:00Z", 0,
            "Default to the old render -- there's some issues"),
    ]
    t = _task("a", "p", [0], events)
    sig = score_task(t, events)
    assert sig.frustration_markers == 1


def test_cluster_groups_same_project_within_72h():
    # Two rework tasks in same project, 24h apart → one cluster
    events = [
        _ev("user_message", "user", "2026-04-28T10:00:00Z", 0, "still broken", project="bike-map"),
        _ev("user_message", "user", "2026-04-29T10:00:00Z", 1, "still broken", project="bike-map"),
    ]
    t1 = _task("a", "bike-map", [0], events)
    t2 = _task("b", "bike-map", [1], events)
    signals = score_all_tasks([t1, t2], events)
    clusters = cluster_rework_tasks(
        [t1, t2], signals,
        active_hours={"a": 1.0, "b": 2.0},
        agent_hours={"a": 0.5, "b": 1.0},
    )
    assert len(clusters) == 1
    c = clusters[0]
    assert c.project == "bike-map"
    assert set(c.task_ids) == {"a", "b"}
    assert c.bryan_active_hours == 3.0
    assert c.agent_active_hours == 1.5
    assert c.frustration_markers == 2


def test_cluster_splits_when_gap_exceeds_72h():
    events = [
        _ev("user_message", "user", "2026-04-28T10:00:00Z", 0, "still broken", project="bike-map"),
        _ev("user_message", "user", "2026-05-05T10:00:00Z", 1, "still broken", project="bike-map"),
    ]
    t1 = _task("a", "bike-map", [0], events)
    t2 = _task("b", "bike-map", [1], events)
    signals = score_all_tasks([t1, t2], events)
    clusters = cluster_rework_tasks(
        [t1, t2], signals,
        active_hours={"a": 1.0, "b": 2.0},
        agent_hours={"a": 0.5, "b": 1.0},
    )
    assert len(clusters) == 2


def test_cluster_separates_by_project():
    events = [
        _ev("user_message", "user", "2026-04-28T10:00:00Z", 0, "still broken", project="bike-map"),
        _ev("user_message", "user", "2026-04-28T11:00:00Z", 1, "still broken", project="health-tool"),
    ]
    t1 = _task("a", "bike-map", [0], events)
    t2 = _task("b", "health-tool", [1], events)
    signals = score_all_tasks([t1, t2], events)
    clusters = cluster_rework_tasks(
        [t1, t2], signals,
        active_hours={"a": 1.0, "b": 2.0},
        agent_hours={"a": 0.5, "b": 1.0},
    )
    assert len(clusters) == 2
    assert {c.project for c in clusters} == {"bike-map", "health-tool"}


def test_top_rework_clusters_filters_low_signal_clusters():
    # Each task has only 1 frustration marker (score 3), and they're in
    # different projects so they form singleton clusters. Each cluster's
    # signal_score = 3 < MIN_CLUSTER_SCORE (5), so none should surface.
    events = [
        _ev("user_message", "user", "2026-04-28T10:00:00Z", 0, "still broken", project="p1"),
        _ev("user_message", "user", "2026-04-28T11:00:00Z", 1, "still broken", project="p2"),
    ]
    tasks = [_task("a", "p1", [0], events), _task("b", "p2", [1], events)]
    clusters, _ = top_rework_clusters(
        tasks, events,
        active_hours={"a": 5.0, "b": 5.0},
        agent_hours={"a": 5.0, "b": 5.0},
    )
    assert clusters == []


def test_top_rework_clusters_surfaces_cluster_with_revert():
    # A single task with a revert commit (score 5) clears MIN_CLUSTER_SCORE.
    events = [
        _ev("commit", "artifact", "2026-04-28T10:00:00Z", 0,
            "Revert: bad change", project="p1"),
    ]
    tasks = [_task("a", "p1", [0], events)]
    clusters, _ = top_rework_clusters(
        tasks, events,
        active_hours={"a": 1.0},
        agent_hours={"a": 0.5},
    )
    assert len(clusters) == 1
    assert clusters[0].project == "p1"


def test_top_rework_clusters_surfaces_multi_task_cluster():
    # Three tasks in same project within 72h, each with 1 frustration marker.
    # Signal_score = 3 * 3 = 9, clears the bar.
    events = [
        _ev("user_message", "user", "2026-04-28T10:00:00Z", 0, "still broken", project="bike-map"),
        _ev("user_message", "user", "2026-04-29T10:00:00Z", 1, "still broken", project="bike-map"),
        _ev("user_message", "user", "2026-04-30T10:00:00Z", 2, "still broken", project="bike-map"),
    ]
    tasks = [
        _task("a", "bike-map", [0], events),
        _task("b", "bike-map", [1], events),
        _task("c", "bike-map", [2], events),
    ]
    clusters, _ = top_rework_clusters(
        tasks, events,
        active_hours={"a": 1.0, "b": 2.0, "c": 0.5},
        agent_hours={"a": 0.5, "b": 1.0, "c": 0.25},
    )
    assert len(clusters) == 1
    assert clusters[0].project == "bike-map"
    assert clusters[0].frustration_markers == 3


def test_top_rework_clusters_ranks_by_total_hours():
    # Both pass cluster threshold (1 revert each = 5pts), rank by hours.
    events = [
        _ev("commit", "artifact", "2026-04-28T10:00:00Z", 0, "Revert bad", project="p1"),
        _ev("commit", "artifact", "2026-04-28T11:00:00Z", 1, "Revert bad", project="p2"),
    ]
    tasks = [_task("a", "p1", [0], events), _task("b", "p2", [1], events)]
    clusters, _ = top_rework_clusters(
        tasks, events,
        active_hours={"a": 1.0, "b": 5.0},
        agent_hours={"a": 1.0, "b": 0.0},
        n=2,
    )
    assert len(clusters) == 2
    assert clusters[0].project == "p2"
    assert clusters[1].project == "p1"


def test_low_score_task_excluded_from_clusters():
    events = [
        _ev("user_message", "user", "2026-04-28T10:00:00Z", 0, "Add more docs please"),
    ]
    t = _task("a", "p", [0], events)
    signals = score_all_tasks([t], events)
    assert signals["a"].score < MIN_SCORE
    clusters = cluster_rework_tasks(
        [t], signals,
        active_hours={"a": 5.0},
        agent_hours={"a": 5.0},
    )
    assert clusters == []
