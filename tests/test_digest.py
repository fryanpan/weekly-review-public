from weekly_review.events import Event
from weekly_review.segmentation import Task
from weekly_review.enrichment import EnrichedTask
from weekly_review.digest import render_digest


def _uev(ts, project, text, seq):
    return Event(
        ts=ts, actor="user", project=project, session_id="s",
        source_system="claude_transcript", source_machine="mm",
        kind="user_message", seq=seq,
        content_snippet=text, metadata={"words": len(text.split())},
    )


def test_digest_contains_all_required_sections():
    events = [_uev("2026-04-15T10:00:00Z", "weekly-review", "implement thing", 0)]
    task = Task(task_id="t1", project="weekly-review", title="Implement thing", event_ids=[0])
    enriched = EnrichedTask(
        task_id="t1", title="Implement thing",
        est_human_hours=4.0, uncertainty="medium",
        bryan_domain_expertise="expert",
        dimensions={"scope": "single-feature", "depth": "careful design", "type": "code"},
        rationale="moderate scope, careful design",
    )
    md = render_digest(
        period_start="2026-04-05",
        period_end="2026-04-19",
        tasks=[task],
        enriched={task.task_id: enriched},
        statuses={task.task_id: "shipped"},
        events=events,
        active_hours_by_task={task.task_id: 0.5},
        agent_hours_by_task={task.task_id: 0.2},
        resumptions_by_task={task.task_id: 0},
        coverage={
            "user_message_events": 1, "assigned": 1, "unassigned": 0,
            "coverage_percent": 100.0, "unassigned_by_project": {},
        },
        cognitive_wall_clock_hours=1.0,
        context_switches=0,
        last_calibrated_days=None,
    )
    for section in ["Leverage", "Coverage", "Top tasks", "Context switches"]:
        assert section in md
    assert "100.0%" in md
    assert "Implement thing" in md


def test_digest_flags_coverage_below_target():
    md = render_digest(
        period_start="2026-04-05",
        period_end="2026-04-19",
        tasks=[],
        enriched={},
        statuses={},
        events=[],
        active_hours_by_task={},
        agent_hours_by_task={},
        resumptions_by_task={},
        coverage={
            "user_message_events": 100, "assigned": 80, "unassigned": 20,
            "coverage_percent": 80.0,
            "unassigned_by_project": {"weekly-review": 20},
        },
        cognitive_wall_clock_hours=0.0,
        context_switches=0,
        last_calibrated_days=None,
    )
    assert "80/100" in md
    assert "below target" not in md
    assert "80.0%" in md
