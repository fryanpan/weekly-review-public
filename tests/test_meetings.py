"""Recorded meetings are measured presence, and nothing spoken leaves the store.

The board task these pin (2026-09-12) exists because off-keyboard time reads as
zero everywhere else in this pipeline. Two of its stated premises did not hold
when the store was censused on 2026-09-12 — `meeting.json` is not the ledger,
and no segment is missing an end — so several of these tests assert the guard
for a condition that does not occur today. That is deliberate: the guard's whole
job is to be right on the day it starts occurring, and an untested guard is a
comment.
"""
import json

import pytest

from weekly_review.config import ProjectSpec, ProjectsConfig
from weekly_review.effort import (
    WORKSPACE_SOURCES,
    _workspace_span,
    is_workspace_event,
    workspace_attribution_key,
    workspace_blocks,
)
from weekly_review.extractors.meetings import (
    extract_meetings,
    meeting_coverage,
)
from tests.fakeids import did, tid

PROJECTS = ProjectsConfig(projects=[
    ProjectSpec(slug="weekly-review", cwd_patterns=["/dev/weekly-review/**"]),
])

# 2026-09-10 17:00:00Z and onwards, in the epoch milliseconds the ledger uses.
T0 = 1789059600000


def _ms(minutes=0.0, seconds=0.0):
    return T0 + int(minutes * 60000 + seconds * 1000)


def _store(tmp_path, doc_id, ledger, turns=None, rollup=False, board="live-feedback"):
    """One meeting directory, laid out the way the plugin lays it out."""
    (tmp_path / "workspaces").mkdir(parents=True, exist_ok=True)
    (tmp_path / "workspaces" / "w-a.tasks.json").write_text(json.dumps({
        "workspace": {"id": "w-a", "name": board, "docIds": [doc_id], "goals": []},
        "tasks": [],
    }))
    d = tmp_path / "meetings" / doc_id.replace(":", "_")
    d.mkdir(parents=True, exist_ok=True)
    (d / "meetings.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in ledger))
    if rollup:
        (d / "meeting.json").write_text(json.dumps({"docId": doc_id}))
    for meeting_id, records in (turns or {}).items():
        (d / f"{meeting_id}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in records))
    return tmp_path


def _simple(tmp_path, **kw):
    """A five-minute solo recording with four turns in the middle of it."""
    mid = "m-" + did("aaa-1789059600000")
    return _store(
        tmp_path, "d-aaa",
        ledger=[
            {"meetingId": mid, "docId": "d-aaa", "startedAt": _ms(0),
             "engine": "soniox", "sampleRate": 16000, "mode": "solo",
             "segment": 1, "source": "mic", "participant": "Arivera"},
            {"meetingId": mid, "endedAt": _ms(5), "turns": 4},
        ],
        turns={mid: [
            {"turn": 0, "text": "one two three", "ts": _ms(1)},
            {"turn": 1, "text": "four five", "ts": _ms(2)},
            {"turn": 2, "text": "six", "ts": _ms(4)},
            {"turn": 3, "text": "seven eight nine ten", "ts": _ms(4, 30)},
        ]},
        **kw,
    )


# --- review doc, 2026-09-12: turns reach the stream carrying their own timestamps ----

def test_a_meeting_reaches_the_stream_with_every_turn_timestamp(tmp_path):
    """Not a first-and-last summary: the whole list.

    A summary says the segment was spoken over. The list says WHERE the silence
    was, which is the only way a reader distinguishes an hour of talking from a
    recorder somebody forgot to stop — and that distinction is the one thing a
    meeting total has to survive.
    """
    ev, = list(extract_meetings(PROJECTS, data_dir=_simple(tmp_path)))

    assert ev.kind == "meeting_segment"
    assert ev.metadata["turn_ts"] == [
        "2026-09-10T17:01:00+00:00",
        "2026-09-10T17:02:00+00:00",
        "2026-09-10T17:04:00+00:00",
        "2026-09-10T17:04:30+00:00",
    ]
    assert ev.metadata["turns_timestamped"] == 4
    assert ev.metadata["spoken_start_ts"] == "2026-09-10T17:01:00+00:00"
    assert ev.metadata["spoken_end_ts"] == "2026-09-10T17:04:30+00:00"
    # The longest gap between consecutive turns, derived once here so two
    # readers cannot derive it two ways.
    assert ev.metadata["max_silence_sec"] == 120.0


def test_the_recorded_span_is_longer_than_the_spoken_one_and_both_are_kept(tmp_path):
    """19% of the real store is recorded and not spoken. Keep it visible.

    The segment is worth its LEDGER span — the recorder was running and he was
    there — but a reader who cannot see the spoken span cannot check that claim,
    so both are carried rather than one being re-derivable.
    """
    ev, = list(extract_meetings(PROJECTS, data_dir=_simple(tmp_path)))

    assert ev.metadata["fixed_active_sec"] == 300.0     # ledger: 5 minutes
    assert ev.metadata["spoken_sec"] == 210.0           # turns: 3.5 minutes
    assert ev.metadata["start_ts"] == "2026-09-10T17:00:00+00:00"
    assert ev.ts == "2026-09-10T17:05:00+00:00"


def test_no_spoken_text_ever_leaves_the_store(tmp_path):
    """Turn text carries third parties' names and their personal business.

    The store holds real ones — a doctor's name among them. This pipeline reads
    turn files for timestamps and word counts and reads nothing else from them.
    A word count cannot be un-said.
    """
    tmp = _store(
        tmp_path, "d-aaa",
        ledger=[
            {"meetingId": "m-x", "docId": "d-aaa", "startedAt": _ms(0), "mode": "conversation"},
            {"meetingId": "m-x", "endedAt": _ms(5), "turns": 1},
            # The `speakers` records are real names outright. They are not an
            # interval, and nothing here opens them.
            {"meetingId": "m-x", "speakers": {"room:A": "Nonexistent Testperson"}},
        ],
        turns={"m-x": [{"turn": 0, "text": "Nonexistent Testperson said the thing", "ts": _ms(1)}]},
    )

    ev, = list(extract_meetings(PROJECTS, data_dir=tmp))

    blob = json.dumps([ev.content_snippet, ev.metadata])
    assert "Nonexistent" not in blob and "Testperson" not in blob
    assert "said the thing" not in blob
    assert ev.metadata["words"] == 5          # the length survives, the words don't


# --- review doc, 2026-09-12: only a segment with both ends contributes time ----------

def test_a_segment_with_no_end_contributes_no_time_and_is_counted_as_lost(tmp_path):
    """Coverage lost, never zero duration.

    Every segment in the store today is closed, so this guard fires on nothing.
    It is written for the day a recorder is killed mid-meeting: a zero-length
    event would report an unmeasured meeting as a meeting that did not happen,
    which is the quietest way this tool can lie.
    """
    tmp = _store(
        tmp_path, "d-aaa",
        ledger=[
            {"meetingId": "m-open", "docId": "d-aaa", "startedAt": _ms(0)},
            {"meetingId": "m-shut", "docId": "d-aaa", "startedAt": _ms(10)},
            {"meetingId": "m-shut", "endedAt": _ms(15), "turns": 2},
        ],
    )

    evs = list(extract_meetings(PROJECTS, data_dir=tmp))

    assert [e.metadata["meeting_id"] for e in evs] == ["m-shut"]
    assert meeting_coverage(tmp).unclosed == 1
    assert meeting_coverage(tmp).segments == 1


def test_an_end_that_precedes_its_start_is_refused_rather_than_reordered(tmp_path):
    """A backwards pair is corrupt input, not a negative interval to flip.

    Swapping the two would manufacture a plausible-looking meeting out of a
    record nobody can vouch for.
    """
    tmp = _store(
        tmp_path, "d-aaa",
        ledger=[
            {"meetingId": "m-bad", "docId": "d-aaa", "startedAt": _ms(10)},
            {"meetingId": "m-bad", "endedAt": _ms(2), "turns": 1},
        ],
    )

    assert list(extract_meetings(PROJECTS, data_dir=tmp)) == []
    assert meeting_coverage(tmp).unclosed == 1


def test_a_paused_meeting_becomes_two_runs_so_the_pause_is_not_billed(tmp_path):
    """`resumedAt` opens a second run; it does not extend the first.

    Collapsing a resumed meeting to first-start..last-end bills the pause as
    presence. The two real pauses in the store are 7s and 1.3s, so this changes
    today's union by nothing — it changes what happens the first time somebody
    pauses a recording to take a call.
    """
    tmp = _store(
        tmp_path, "d-aaa",
        ledger=[
            {"meetingId": "m-r", "docId": "d-aaa", "startedAt": _ms(0)},
            {"meetingId": "m-r", "endedAt": _ms(5), "turns": 3},
            {"meetingId": "m-r", "resumedAt": _ms(25)},
            {"meetingId": "m-r", "endedAt": _ms(30), "turns": 9},
        ],
    )

    evs = list(extract_meetings(PROJECTS, data_dir=tmp))

    assert [e.seq for e in evs] == [0, 1]
    assert [e.metadata["fixed_active_sec"] for e in evs] == [300.0, 300.0]
    # 10 minutes of presence out of a 30-minute wall clock, not 30.
    assert sum(e.metadata["fixed_active_sec"] for e in evs) == 600.0
    # Two runs of one meeting are two events, so they need two ids.
    assert len({e.event_id for e in evs}) == 2


# --- review doc, 2026-09-12: a task, or an explicit marker that there is none --------

def test_every_segment_carries_a_task_or_an_explicit_no_task_marker(tmp_path):
    """An absent field cannot be told apart from an extractor that forgot to look.

    Two of the 66 real segments were recorded against a task card, whose docId
    is literally `task:<taskId>`; the other 64 were recorded against an ordinary
    doc and say so.
    """
    on_card = _store(
        tmp_path / "card", "task:" + tid("Ef5GhijK6lMn"),
        ledger=[
            {"meetingId": "m-c", "docId": "task:" + tid("Ef5GhijK6lMn"), "startedAt": _ms(0)},
            {"meetingId": "m-c", "endedAt": _ms(3), "turns": 1},
        ],
    )
    carded, = list(extract_meetings(PROJECTS, data_dir=on_card))
    assert carded.metadata["task_resolution"] == "task-card"
    assert carded.metadata["task_id"] == tid("Ef5GhijK6lMn")

    plain, = list(extract_meetings(PROJECTS, data_dir=_simple(tmp_path / "plain")))
    assert plain.metadata["task_resolution"] == "no-task"
    assert "task_id" not in plain.metadata
    assert plain.metadata["doc_id"] == "d-aaa"


def test_a_meeting_is_his_presence_whatever_the_mode_or_participant(tmp_path):
    """Mode is not an identity signal and neither is a speaker label.

    `participant` is on 34 of 66 segments and is always his own handle; `mode`
    is solo on 40 and conversation on 23. Filtering on either would delete real
    meetings to enforce a field the store fills in half the time — and the
    auto-notes split one solo speaker into "Speaker A" and "Speaker B", so a
    label is not evidence of who was in the room. It is his recorder on his
    machine; the measurement is his presence, not who spoke.
    """
    tmp = _store(
        tmp_path, "d-aaa",
        ledger=[
            {"meetingId": "m-1", "docId": "d-aaa", "startedAt": _ms(0),
             "mode": "conversation", "source": "mic+system"},
            {"meetingId": "m-1", "endedAt": _ms(4), "turns": 7},
            {"meetingId": "m-2", "docId": "d-aaa", "startedAt": _ms(10)},
            {"meetingId": "m-2", "endedAt": _ms(14), "turns": 2},
        ],
    )

    evs = list(extract_meetings(PROJECTS, data_dir=tmp))

    assert len(evs) == 2
    assert all(e.actor == "user" and e.author_kind == "human" for e in evs)
    assert [e.metadata["participant"] for e in evs] == ["", ""]
    assert [e.metadata["mode"] for e in evs] == ["conversation", ""]


# --- review doc, 2026-09-12: the coverage caveat -------------------------------------

def test_the_caveat_counts_the_ledger_and_names_the_rollup_it_replaces(tmp_path):
    """The task asks for meeting.json out of all dirs. That is the wrong denominator.

    `meeting.json` is a rollup and no duration is read from it (24 of 42 dirs in
    the real store); `meetings.jsonl` is the ledger and every directory has one
    (42 of 42). Publishing 24/42 beside a total derived from the ledger would
    understate coverage by a factor the reader has no way to spot, so the caveat
    carries BOTH and says which one decides.
    """
    tmp = _simple(tmp_path, rollup=True)
    _store(
        tmp, "d-bbb",
        ledger=[
            {"meetingId": "m-b", "docId": "d-bbb", "startedAt": _ms(60)},
            {"meetingId": "m-b", "endedAt": _ms(70), "turns": 5},
        ],
    )

    cov = meeting_coverage(tmp)

    assert (cov.dirs, cov.dirs_with_ledger, cov.dirs_with_rollup) == (2, 2, 1)
    assert cov.segments == 2 and cov.unclosed == 0
    assert cov.turn_files == 1 and cov.turn_files_timestamped == 1
    assert "2/2 directories carry a meetings.jsonl" in cov.caveat
    assert "rollup meeting.json, which measures nothing here, is on 1/2" in cov.caveat
    assert "every segment carries both a start and an end" in cov.caveat


def test_the_caveat_says_so_when_a_segment_is_lost(tmp_path):
    tmp = _store(
        tmp_path, "d-aaa",
        ledger=[{"meetingId": "m-open", "docId": "d-aaa", "startedAt": _ms(0)}],
    )

    assert "1 segment(s) missing an end, counted as coverage lost, not as zero" \
        in meeting_coverage(tmp).caveat


def test_a_directory_with_no_ledger_is_counted_not_skipped(tmp_path):
    """A directory the extractor cannot read is exactly what the denominator is for."""
    tmp = _simple(tmp_path)
    (tmp / "meetings" / "d-empty").mkdir()

    cov = meeting_coverage(tmp)

    assert cov.dirs == 2 and cov.dirs_with_ledger == 1
    assert "1/2 directories carry a meetings.jsonl" in cov.caveat


# --- where the time lands ----------------------------------------------------

def test_a_meeting_spans_its_measured_interval_under_the_block_rule(tmp_path):
    """The one thing that makes this source worth anything.

    A board action is an instant; a meeting is not. If `_workspace_span` treated
    it like every other workspace record, a five-minute recording would enter the
    union as a zero-length moment and off-keyboard time would still be zero.
    """
    ev, = list(extract_meetings(PROJECTS, data_dir=_simple(tmp_path)))

    assert is_workspace_event(ev)
    assert "meetings" in WORKSPACE_SOURCES
    start, end = _workspace_span(ev)
    assert (end - start).total_seconds() == 300.0
    assert workspace_attribution_key(ev) == "doc:d-aaa"

    (block_start, block_end, members), = workspace_blocks([ev])
    assert (block_end - block_start).total_seconds() == 300.0
    assert members == [0]


def test_a_long_meeting_is_not_clamped_the_way_a_read_session_is(tmp_path):
    """`REVIEW_SESSION_CAP_SEC` exists because a tab can be left open.

    A recording cannot: the ledger says when it stopped, and one that never
    stopped is refused outright rather than clamped. So a three-hour meeting is
    three hours.
    """
    tmp = _store(
        tmp_path, "d-aaa",
        ledger=[
            {"meetingId": "m-long", "docId": "d-aaa", "startedAt": _ms(0)},
            {"meetingId": "m-long", "endedAt": _ms(180), "turns": 400},
        ],
    )

    ev, = list(extract_meetings(PROJECTS, data_dir=tmp))
    start, end = _workspace_span(ev)

    assert (end - start).total_seconds() == 3 * 3600


def test_a_meeting_routes_through_its_board_like_any_other_doc(tmp_path):
    """65 of 66 real segments route to a project through the board that owns the doc."""
    tmp = _simple(tmp_path)
    projects = ProjectsConfig(projects=[
        ProjectSpec(slug="claude-live-feedback-plugin",
                    cwd_patterns=["/dev/claude-live-feedback-plugin/**"],
                    doc_keywords=["live-feedback"]),
    ])
    (tmp / "d-aaa.index.json").write_text(json.dumps({
        "meta": {"docId": "d-aaa", "owner": "/dev/claude-live-feedback-plugin"},
    }))

    ev, = list(extract_meetings(projects, data_dir=tmp))

    assert ev.project == "claude-live-feedback-plugin"


def test_the_notetakers_own_timing_file_is_never_read_as_his_time(tmp_path):
    """`<meetingId>-timing.jsonl` is the note-taking agent's telemetry."""
    tmp = _simple(tmp_path)
    mid = "m-" + did("aaa-1789059600000")
    (tmp / "meetings" / "d-aaa" / f"{mid}-timing.jsonl").write_text(
        json.dumps({"turn": 0, "text": "agent latency", "ts": _ms(9)}) + "\n")

    ev, = list(extract_meetings(PROJECTS, data_dir=tmp))

    assert ev.metadata["turns_timestamped"] == 4       # not 5
    assert meeting_coverage(tmp).turn_files == 1


def test_no_meetings_directory_is_an_empty_source_not_a_crash(tmp_path):
    assert list(extract_meetings(PROJECTS, data_dir=tmp_path)) == []
    assert meeting_coverage(tmp_path).dirs == 0


# --- the call site, not the behaviour ----------------------------------------

@pytest.mark.parametrize("needle", [
    "from .extractors.meetings import extract_meetings, meeting_coverage",
    "for ev in extract_meetings(projects):",
    "meeting_coverage().caveat",
])
def test_the_cli_actually_reaches_the_meetings_extractor(needle):
    """A function can be correct, tested, and never called.

    `presence_hours_by_task` shipped with four unit tests and a spec naming it
    as the metric while `cli.py` went on calling something else, for two days.
    Assert the call site.
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1]
           / "src" / "weekly_review" / "cli.py").read_text()
    assert needle in src
