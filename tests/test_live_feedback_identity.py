"""The document was the identity, so every revisit collided with the first.

Every live-feedback event was keyed `live_feedback:live-feedback:<docId>:0` —
the doc id, and a `seq` that was always zero. `dedupe_by_event_id` then kept one
event per document and silently discarded the rest. Measured on the real store
before the fix: 5,699 person read sessions collapsed to 1,180 documents, and
2,560 of the user's comments to 475. One doc he visited 58 times became one event.

This is the same defect that destroyed 80% of a fan-out session's transcript
events, and the same fix: the id has to carry what actually distinguishes two
records. Here the store already assigns one, so the extractor stops guessing.
"""
import json

from weekly_review.identity import SubjectIdentity

from weekly_review.config import ProjectSpec, ProjectsConfig
from weekly_review.events import Event, dedupe_by_event_id
from weekly_review.extractors.live_feedback import (
    _comment_event,
    _iter_review_sessions,
)

PROJECTS = ProjectsConfig(projects=[
    ProjectSpec(slug="weekly-review", cwd_patterns=["/dev/weekly-review/**"]),
])

BRYAN = {"kind": "known", "name": "Alex", "id": "known-alex"}

# Passed explicitly so these tests measure the EXTRACTOR, not whichever
# identity.yaml happens to be on the machine running them.
IDENTITY = SubjectIdentity(name_fragments=("alex",))


def _read_session(event_id, doc_id="d1", end="2026-06-20T10:05:00.000Z", dur=300000):
    return {
        "type": "read_session", "actor": "person", "actorId": "known-alex",
        "actorName": "Alex", "isOwner": True,
        "eventId": event_id, "ts": end,
        "doc": {"docId": doc_id, "sourceUrl": "/dev/weekly-review/doc.md",
                "title": "t", "producedBy": {"cwd": "/dev/weekly-review"}},
        "payload": {"endTs": end, "durationMs": dur, "sessionId": "s-" + event_id,
                    "interactionBounded": True, "maxScrollDepthPct": 80},
    }


def _sessions(tmp_path, records):
    path = tmp_path / "activity.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return list(_iter_review_sessions(PROJECTS, path, "mac_mini", identity=IDENTITY))


def test_a_doc_visited_n_times_yields_n_events_after_dedup(tmp_path):
    """The headline criterion, checked for several N rather than one."""
    for n in (1, 2, 7, 58):
        records = [
            _read_session(f"e{i:04x}", end=f"2026-06-20T10:{i % 60:02d}:00.000Z")
            for i in range(n)
        ]
        evs = _sessions(tmp_path, records)
        assert len(evs) == n, f"extractor dropped one at N={n}"
        assert len(dedupe_by_event_id(evs)) == n, f"dedup collapsed N={n}"


def test_no_two_live_feedback_events_in_a_run_share_an_event_id(tmp_path):
    """Read sessions and comments on the same doc must not collide either."""
    reads = _sessions(tmp_path, [_read_session(f"e{i}") for i in range(5)])
    comments = [
        _comment_event(PROJECTS, "/dev/weekly-review/doc.md", "d1", tid,
                       {"id": cid, "ts": 1_787_354_995_744 + i, "text": "x",
                        "author": BRYAN}, "mac_mini", identity=IDENTITY)
        for i, (tid, cid) in enumerate(
            [("t1", "c1"), ("t1", "c2"), ("t2", "c1"), ("t2", "c3")])
    ]
    ids = [e.event_id for e in reads + comments]
    assert len(set(ids)) == len(ids), f"collision among {ids}"


def test_two_comments_in_one_thread_survive_as_two_events():
    a, b = (
        _comment_event(PROJECTS, "", "d1", "t1",
                       {"id": cid, "ts": ts, "text": t, "author": BRYAN}, "mac_mini", identity=IDENTITY)
        for cid, ts, t in [("c1", 1_787_354_995_744, "first"),
                           ("c2", 1_787_354_999_000, "second")]
    )
    assert a.event_id != b.event_id
    assert len(dedupe_by_event_id([a, b])) == 2


def test_a_comment_id_reused_across_threads_does_not_collide():
    """25 of 2,535 distinct comment ids in the real store repeat, so a bare
    comment id is not a key. The doc and thread are part of the identity."""
    same_id = "16rq23mtdikw"
    a = _comment_event(PROJECTS, "", "d1", "t1",
                       {"id": same_id, "ts": 1, "text": "x", "author": BRYAN}, "m", identity=IDENTITY)
    b = _comment_event(PROJECTS, "", "d1", "t2",
                       {"id": same_id, "ts": 2, "text": "y", "author": BRYAN}, "m", identity=IDENTITY)
    c = _comment_event(PROJECTS, "", "d2", "t1",
                       {"id": same_id, "ts": 3, "text": "z", "author": BRYAN}, "m", identity=IDENTITY)
    assert len({a.event_id, b.event_id, c.event_id}) == 3
    assert len(dedupe_by_event_id([a, b, c])) == 3


def test_a_genuine_duplicate_still_dedupes(tmp_path):
    """The same record ingested twice — two archive mirrors, a re-read — is
    still one event. Recovering revisits must not disable dedup."""
    rec = _read_session("e-same")
    evs = _sessions(tmp_path, [rec, rec])
    assert len(evs) == 2
    assert len(dedupe_by_event_id(evs)) == 1


def test_transcript_event_ids_are_unchanged():
    """Main-loop transcript ids stay byte-for-byte identical, so nothing already
    persisted shifts — review verdicts and carry-forward are keyed on them."""
    ev = Event(ts="2026-05-01T00:00:00+00:00", actor="user", project="p",
               session_id="abc-123", source_system="claude_transcript",
               source_machine="mac_mini", kind="user_message", seq=17)
    assert ev.event_id == "claude_transcript:abc-123:17"

    lane = Event(ts="2026-05-01T00:00:00+00:00", actor="agent", project="p",
                 session_id="abc-123", source_system="claude_transcript",
                 source_machine="mac_mini", kind="agent_message", seq=17,
                 metadata={"agent_lane": "agent-deadbeef"})
    assert lane.event_id == "claude_transcript:abc-123:agent-deadbeef:17"


def test_a_record_with_no_id_of_its_own_still_gets_a_distinct_one(tmp_path):
    """Records predating `eventId` fall back to payload.sessionId, and a comment
    with neither falls back to its timestamp. Degrade, never collapse."""
    rec = _read_session("e1")
    del rec["eventId"]
    other = _read_session("e2")
    del other["eventId"]
    evs = _sessions(tmp_path, [rec, other])
    assert len(dedupe_by_event_id(evs)) == 2

    a = _comment_event(PROJECTS, "", "d1", "t1",
                       {"ts": 111, "text": "x", "author": BRYAN}, "m", identity=IDENTITY)
    b = _comment_event(PROJECTS, "", "d1", "t1",
                       {"ts": 222, "text": "y", "author": BRYAN}, "m", identity=IDENTITY)
    assert a.event_id != b.event_id
