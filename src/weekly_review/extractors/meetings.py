"""Extract recorded meetings as measured intervals of the subject's presence.

Off-keyboard time reads as zero in every other source this pipeline has. A
transcript records what he typed; the workspace records what he clicked. An
hour spent talking — to a collaborator, to a doctor, or out loud to himself —
leaves no mark in either, so the hands-on figure has silently been a figure
about keyboards. The workspace's own recorder has been writing meetings down
since 2026-08-28 and nothing read the file.

**Two of the task's premises did not survive a census of the store (2026-09-12),
so this is built against the measured shape rather than the stated one.**

- `meeting.json` is the wrong coverage denominator. It is a rollup and 24 of 42
  directories have one; the file carrying the durations is `meetings.jsonl`, and
  **all 42 directories have one**. The caveat this module publishes is therefore
  42/42, not 24/42 — see `meeting_coverage`.
- No segment is missing an end. All 66 start records have a matching `endedAt`.
  The guard is still written and still tested, because the day it stops being
  true is the day it must not read as zero-duration work.

What a segment is worth is the LEDGER's span, not the turns. Over the 54 turn
files that carry timestamps the spoken span totals 4.22h against the ledger's
5.24h, so **0.98h (19%) is recorded and not spoken** — a pause, a document being
read, somebody else talking off-mic. The recorder was running; that is presence.
The spoken span rides along in metadata so the 19% stays inspectable.

A meeting counts as his whole-segment presence regardless of `mode`. It is his
recorder, running on his machine, and what is being measured is his presence
rather than who spoke — which also sidesteps a hazard the task names: the
auto-notes split one solo speaker into "Speaker A" and "Speaker B", so speaker
labels are not evidence of who was in the room. For the same reason `participant`
is recorded (it is his own handle on the 34 segments that carry one) but never
filtered on: it is absent on the other 32, and dropping those would delete real
meetings to enforce a field the store fills in half the time.

**Nothing spoken enters an event.** Turn text carries third parties' names and
their personal and medical business; the `speakers` ledger records carry real
names outright. This module reads turn files for TIMESTAMPS and WORD COUNTS and
reads nothing else from them, and it never opens the `speakers` records at all.
A word count cannot be un-said and a name can.

Where the time lands: `source_system` is in `effort.WORKSPACE_SOURCES`, so a
segment is blocked by the 60s contiguous-block rule alongside board actions and
read sessions, keyed on its doc (or its task, for the two segments recorded on a
task card). It is deliberately NOT added to the presence path — that instrument
is for transcripts, and billing one meeting through both would be the same
double-count that keeps peer relay out of the human lane.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from ..events import Event
from .live_feedback import (
    _route_doc,
    _task_stamp,
    load_board_projects,
    load_doc_boards,
    load_doc_roots,
    resolve_data_dir,
)

# The ledger. `meeting.json` beside it is a rollup that only some directories
# have; this one is written as the meeting runs and exists for every directory.
LEDGER_NAME = "meetings.jsonl"

# `m-<something>-<epoch ms>.jsonl` beside the ledger, named for the meetingId.
# `<meetingId>-timing.jsonl` is the note-taking agent's own telemetry and is NOT
# his time — it is excluded by name, and by never being looked for.
_TIMING_SUFFIX = "-timing.jsonl"


def meetings_dir(data_dir: Path) -> Path:
    return data_dir / "meetings"


def _ms_to_iso(ms: float) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _read_ledger(path: Path) -> list[dict]:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("meetingId"):
            out.append(rec)
    return out


@dataclass(frozen=True)
class _Segment:
    """One contiguous run of recording: opened once, closed once."""

    meeting_id: str
    doc_id: str
    start_ms: float
    end_ms: float
    turns: int
    index: int          # which run within the meeting; 0 unless it was resumed
    mode: str
    source: str
    participant: str


def _segments(records: list[dict]) -> tuple[list[_Segment], int]:
    """Closed runs, and how many opens never got a close.

    A meeting can be paused and resumed: `startedAt` and `resumedAt` both OPEN a
    run and `endedAt` closes it (2 of 66 meetings do this). Collapsing a resumed
    meeting to first-start..last-end would bill the pause as presence, so each
    run is its own interval. The two real pauses are 7s and 1.3s, so this changes
    the union by nothing today — it changes what happens the first time somebody
    pauses a recording to take a call.

    A run still open when the file ends contributes NO time and is returned as
    lost coverage. That is the whole point of the guard: a segment with one end
    missing is a segment we cannot measure, and calling it zero seconds would
    quietly report an unmeasured meeting as a meeting that did not happen.
    """
    by_meeting: dict[str, list[dict]] = {}
    for rec in records:
        by_meeting.setdefault(str(rec["meetingId"]), []).append(rec)

    out: list[_Segment] = []
    unclosed = 0
    for meeting_id, recs in by_meeting.items():
        doc_id = ""
        mode = source = participant = ""
        open_at: float | None = None
        index = 0
        for rec in recs:
            doc_id = str(rec.get("docId") or "") or doc_id
            mode = str(rec.get("mode") or "") or mode
            source = str(rec.get("source") or "") or source
            participant = str(rec.get("participant") or "") or participant
            opened = rec.get("startedAt") or rec.get("resumedAt")
            if opened:
                if open_at is not None:
                    unclosed += 1      # an open run replaced without a close
                open_at = float(opened)
                continue
            ended = rec.get("endedAt")
            if ended is None:
                continue               # `speakers` / `announced`: not an interval
            if open_at is None:
                unclosed += 1          # a close with nothing open: also unmeasurable
                continue
            end_ms = float(ended)
            if end_ms < open_at:
                unclosed += 1          # backwards: refuse rather than reorder
                open_at = None
                continue
            out.append(_Segment(
                meeting_id=meeting_id, doc_id=doc_id, start_ms=open_at, end_ms=end_ms,
                turns=int(rec.get("turns") or 0), index=index,
                mode=mode, source=source, participant=participant,
            ))
            index += 1
            open_at = None
        if open_at is not None:
            unclosed += 1
    out.sort(key=lambda s: (s.start_ms, s.meeting_id, s.index))
    return out, unclosed


def _turn_marks(path: Path) -> list[tuple[float, int]]:
    """(timestamp, word count) per turn. The text itself is never returned.

    Meeting turns carry third parties' names and their personal business. A word
    count is the most this pipeline can learn from a sentence without becoming a
    place that stores it, and it is enough: length is what makes a turn evidence
    that somebody was talking.
    """
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []
    marks: list[tuple[float, int]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict) or not rec.get("ts"):
            continue
        text = rec.get("text")
        marks.append((float(rec["ts"]), len(text.split()) if isinstance(text, str) else 0))
    marks.sort()
    return marks


@dataclass(frozen=True)
class MeetingCoverage:
    """What a published meeting total has to say about itself.

    A review doc asks for "how many meeting directories carried a
    meeting.json, out of how many exist". Measured, that is 24 of 42 — and it is
    the wrong question, because `meeting.json` is a rollup and no duration is
    read from it. The denominator that decides whether a meeting is measurable is
    the ledger, and it is 42 of 42. This carries both, so the substitution is
    visible in the output rather than buried here.
    """

    dirs: int = 0
    dirs_with_ledger: int = 0
    dirs_with_rollup: int = 0
    segments: int = 0
    unclosed: int = 0
    turn_files: int = 0
    turn_files_timestamped: int = 0

    @property
    def caveat(self) -> str:
        """One line, printed beside any meeting total."""
        lost = (f"; {self.unclosed} segment(s) missing an end, counted as coverage "
                f"lost, not as zero" if self.unclosed else "; every segment carries "
                f"both a start and an end")
        return (
            f"meetings: {self.dirs_with_ledger}/{self.dirs} directories carry a "
            f"{LEDGER_NAME} ledger (the rollup meeting.json, which measures nothing "
            f"here, is on {self.dirs_with_rollup}/{self.dirs}); "
            f"{self.segments} closed segment(s){lost}; "
            f"{self.turn_files_timestamped}/{self.turn_files} turn files carry "
            f"timestamps, so the spoken span below is short of the recorded one"
        )


def meeting_coverage(data_dir: Path | None = None) -> MeetingCoverage:
    """Count what exists before anything quotes what was measured."""
    md = meetings_dir(data_dir or resolve_data_dir())
    if not md.is_dir():
        return MeetingCoverage()
    dirs = sorted(p for p in md.iterdir() if p.is_dir())
    segments = unclosed = turn_files = timestamped = 0
    with_ledger = with_rollup = 0
    for d in dirs:
        if (d / "meeting.json").exists():
            with_rollup += 1
        ledger = d / LEDGER_NAME
        if not ledger.exists():
            continue
        with_ledger += 1
        segs, lost = _segments(_read_ledger(ledger))
        segments += len(segs)
        unclosed += lost
        for turn_path in sorted(d.glob("m-*.jsonl")):
            if turn_path.name.endswith(_TIMING_SUFFIX):
                continue
            turn_files += 1
            if _turn_marks(turn_path):
                timestamped += 1
    return MeetingCoverage(
        dirs=len(dirs), dirs_with_ledger=with_ledger, dirs_with_rollup=with_rollup,
        segments=segments, unclosed=unclosed,
        turn_files=turn_files, turn_files_timestamped=timestamped,
    )


def extract_meetings(
    projects,
    data_dir: Path | None = None,
    source_machine: str = "meetings",
) -> Iterator[Event]:
    """Yield one Event per closed recording run.

    Shaped like `review_session` on purpose: `ts` is the end and
    `metadata["start_ts"]` is the start, which is what `effort._workspace_span`
    reads to give an event a real interval instead of treating it as an instant.

    Routing reuses the doc machinery unchanged — a meeting is recorded against a
    docId, and 65 of 66 segments route to a real project through their board. The
    doc TITLE is deliberately not passed to the keyword fallback, exactly as the
    board extractor does not pass a task title: a meeting's name is often the
    name of a person.
    """
    dd = data_dir or resolve_data_dir()
    md = meetings_dir(dd)
    if not md.is_dir():
        return
    doc_boards = load_doc_boards(dd)
    doc_roots = load_doc_roots(dd)
    board_projects = load_board_projects(projects, doc_boards, doc_roots)

    for d in sorted(p for p in md.iterdir() if p.is_dir()):
        ledger = d / LEDGER_NAME
        if not ledger.exists():
            continue
        segments, _ = _segments(_read_ledger(ledger))
        marks_cache: dict[str, list[tuple[float, int]]] = {}
        for seg in segments:
            doc_id = seg.doc_id
            project = _route_doc(
                projects, "", doc_id, "", doc_boards.get(doc_id, ""),
                doc_roots.get(doc_id, ""), board_projects,
            )
            if seg.meeting_id not in marks_cache:
                marks_cache[seg.meeting_id] = _turn_marks(d / f"{seg.meeting_id}.jsonl")
            inside = [(ts, words) for ts, words in marks_cache[seg.meeting_id]
                      if seg.start_ms <= ts <= seg.end_ms]
            dur = (seg.end_ms - seg.start_ms) / 1000
            spoken = {}
            if inside:
                stamps = [ts for ts, _ in inside]
                # Every turn's own timestamp rides in the event, not just the
                # first and last. A summary says a segment was spoken over; the
                # list says WHERE the silence was, which is the only way a reader
                # can tell an hour of talking from an hour of a recorder someone
                # forgot to stop. `max_silence_sec` is the derivative that gets
                # used, and it is derived here so two readers cannot derive it
                # two ways.
                gaps = [(b - a) / 1000 for a, b in zip(stamps, stamps[1:])]
                spoken = {
                    "spoken_start_ts": _ms_to_iso(stamps[0]),
                    "spoken_end_ts": _ms_to_iso(stamps[-1]),
                    "spoken_sec": round((stamps[-1] - stamps[0]) / 1000, 1),
                    "turn_ts": [_ms_to_iso(ts) for ts in stamps],
                    "max_silence_sec": round(max(gaps), 1) if gaps else 0.0,
                }
            yield Event(
                ts=_ms_to_iso(seg.end_ms),
                actor="user",
                project=project,
                session_id=f"meeting:{doc_id or seg.meeting_id}",
                source_system="meetings",
                source_machine=source_machine,
                kind="meeting_segment",
                seq=seg.index,
                # No spoken text, ever. Length and count only.
                content_snippet=(f"meeting segment {dur:.0f}s, {len(inside)} turn(s), "
                                 f"{sum(w for _, w in inside)} word(s)"),
                metadata={
                    "fixed_active_sec": round(dur, 1),
                    "start_ts": _ms_to_iso(seg.start_ms),
                    "doc_id": doc_id,
                    "meeting_id": seg.meeting_id,
                    # `turns` as the ledger counted them; `turns_timestamped` as
                    # this run could place them. They differ when a turn file is
                    # empty (12 of 66 are), which is how the 19% stays visible.
                    "turns": seg.turns,
                    "turns_timestamped": len(inside),
                    "words": sum(w for _, w in inside),
                    "mode": seg.mode,
                    "capture_source": seg.source,
                    "participant": seg.participant,
                    **spoken,
                    "via": "meetings-ledger",
                    "source_event_id": f"meeting:{seg.meeting_id}:{seg.index}",
                    **_task_stamp(doc_id),
                },
                author_kind="human",
            )
