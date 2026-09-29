from weekly_review.events import Event
from weekly_review.segmentation import Task, segment
from weekly_review.coverage import coverage_report, rule_counts


def _uev(ts, project, seq):
    return Event(
        ts=ts, actor="user", project=project, session_id="s",
        source_system="claude_transcript", source_machine="mm",
        kind="user_message", seq=seq,
        content_snippet="x", metadata={"words": 1},
    )


def _card_msg(ts, project, card, seq):
    return Event(
        ts=ts, actor="user", project=project, session_id="s",
        source_system="live_feedback", source_machine="mm",
        kind="user_message", seq=seq,
        content_snippet="x", metadata={"words": 1, "task_id": card},
    )


def test_a_message_on_a_board_task_is_placed_and_a_plain_one_is_not():
    """Since 2026-09-18 the only rule placing his message is a board-task link."""
    events = [_card_msg(f"2026-04-15T10:0{i}:00Z", "weekly-review", "t-abc", i) for i in range(3)]
    events += [_uev(f"2026-04-15T11:0{i}:00Z", "weekly-review", 10 + i) for i in range(2)]
    tasks = segment(events)
    rep = coverage_report(tasks, events)
    assert rep["user_message_events"] == 5
    assert rep["assigned"] == 3
    assert rep["coverage_percent"] == 60.0
    assert rep["unassigned_by_project"] == {"weekly-review": 2}
    assert "meets_target" not in rep


def test_coverage_counts_unassigned_by_project():
    events = [_uev(f"2026-04-15T10:{i:02d}:00Z", "weekly-review", i) for i in range(20)]
    task = Task(task_id="t1", project="weekly-review", title="x", event_ids=list(range(18)))
    rep = coverage_report([task], events)
    assert rep["coverage_percent"] == 90.0
    assert rep["unassigned_by_project"] == {"weekly-review": 2}


def test_coverage_handles_empty_events():
    rep = coverage_report([], [])
    assert rep["user_message_events"] == 0
    assert rep["coverage_percent"] == 100.0


# The rule strings the dropped passes wrote into the trace (2026-09-18). The
# proximity placements all wrote `non_user_attach` with the rule in `detail`.
DROPPED_RULES = {"same_session", "cross_session_proximity", "new_topic_cue", "issue_ref_match",
                 "new_issue_ref", "new_task_default", "artifact_anchor", "non_user_attach",
                 "sealed_by_pr_opened", "r2_pr_join"}


def test_rule_counts_sums_the_trace_and_lists_no_dropped_rule():
    events = [_card_msg(f"2026-04-15T10:0{i}:00Z", "weekly-review", "t-abc", i) for i in range(3)]
    events += [_uev(f"2026-04-15T11:0{i}:00Z", "weekly-review", 10 + i) for i in range(2)]
    counts = rule_counts(segment(events))
    assert counts == {"board_card": 3}
    assert not DROPPED_RULES & set(counts)
