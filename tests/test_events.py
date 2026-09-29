import json
import pytest
from weekly_review.events import Event, dedupe_by_event_id, read_events, write_events


def test_event_roundtrip(tmp_path):
    events = [
        Event(
            ts="2026-04-15T10:22:33Z",
            actor="user",
            project="weekly-review",
            session_id="sess-1",
            source_system="claude_transcript",
            source_machine="mac_mini",
            kind="user_message",
            content_snippet="hello",
            metadata={"words": 1},
            seq=0,
        ),
    ]
    path = tmp_path / "events.jsonl"
    write_events(path, events)
    read_back = list(read_events(path))
    assert read_back == events


def test_event_rejects_missing_fields():
    with pytest.raises(TypeError):
        Event(ts="2026-04-15T10:22:33Z")  # missing required fields


def test_dedupe_by_event_id_keeps_first_occurrence():
    """Regression: the bug that produced 940h of phantom uncaptured bike-map
    work was the SAME logical Claude transcript being extracted twice
    (~/.claude/projects/ + a mirror under ~/dev/weekly-review-transcripts/),
    yielding identical event_ids at different list indices. Segmentation
    then attached the same event to two tasks, inflating PR counts."""
    from weekly_review.events import dedupe_by_event_id

    e1 = Event(
        ts="2026-04-15T10:00:00Z", actor="user", project="wr", session_id="s1",
        source_system="claude_transcript", source_machine="mac_mini",
        kind="user_message", seq=0, content_snippet="from primary",
        metadata={"words": 2},
    )
    e1_dup = Event(
        ts="2026-04-15T10:00:00Z", actor="user", project="wr", session_id="s1",
        source_system="claude_transcript", source_machine="laptop",
        kind="user_message", seq=0, content_snippet="from mirror",
        metadata={"words": 2},
    )
    e2 = Event(
        ts="2026-04-15T11:00:00Z", actor="user", project="wr", session_id="s1",
        source_system="claude_transcript", source_machine="mac_mini",
        kind="user_message", seq=1, content_snippet="next",
        metadata={"words": 1},
    )
    # e1 and e1_dup share event_id (same source_system:session:seq).
    assert e1.event_id == e1_dup.event_id
    assert e1.event_id != e2.event_id

    deduped = dedupe_by_event_id([e1, e1_dup, e2])
    assert len(deduped) == 2
    # First occurrence wins — e1's content_snippet, not e1_dup's.
    assert deduped[0].content_snippet == "from primary"
    assert deduped[1].content_snippet == "next"


def test_dedupe_preserves_order_for_distinct_ids():
    from weekly_review.events import dedupe_by_event_id
    evs = [
        Event(ts=f"2026-04-15T10:0{i}:00Z", actor="user", project="wr",
              session_id="s1", source_system="claude_transcript",
              source_machine="mm", kind="user_message", seq=i,
              content_snippet=f"msg {i}", metadata={"words": 1})
        for i in range(5)
    ]
    deduped = dedupe_by_event_id(evs)
    assert deduped == evs


# --- event_id must not collide across agent lanes ---------------------------
# Subagent transcripts carry the PARENT's sessionId and restart seq at 0, so a
# bare source:session:seq collides across every dispatched agent in a session and
# dedupe_by_event_id silently discards all but the first.

def _agent_ev(seq: int, lane: str | None, session: str = "s1") -> Event:
    md = {"words": 5}
    if lane is not None:
        md["agent_lane"] = lane
    return Event(
        ts="2026-07-10T10:00:00Z", actor="agent", project="wr", session_id=session,
        source_system="claude_transcript", source_machine="mm",
        kind="agent_message", seq=seq, content_snippet="x", metadata=md,
    )


def test_event_id_distinct_across_lanes_sharing_session_and_seq():
    main = _agent_ev(0, "main")
    a = _agent_ev(0, "agent-aaa")
    b = _agent_ev(0, "agent-bbb")
    assert len({main.event_id, a.event_id, b.event_id}) == 3


def test_event_id_unchanged_for_main_lane():
    # Previously-persisted ids must not shift.
    assert _agent_ev(7, "main").event_id == "claude_transcript:s1:7"
    assert _agent_ev(7, None).event_id == "claude_transcript:s1:7"


def test_dedupe_preserves_concurrent_lanes():
    events = [_agent_ev(0, lane) for lane in ("main", "agent-aaa", "agent-bbb")]
    assert len(dedupe_by_event_id(events)) == 3


def test_dedupe_still_collapses_the_same_event_from_two_mirrors():
    # The reason dedupe exists: one transcript ingested from both the live dir
    # and the archive mirror. Same file => same lane => same id => one event.
    ev = _agent_ev(0, "agent-aaa")
    assert len(dedupe_by_event_id([ev, ev])) == 1
