"""An overwritten session transcript is recovered from the archive history.

On 2026-08-17 a worktree transcript was renamed onto the claude-workspaces
lead's session path, replacing four months of its main loop. The archiver kept
the old file in mac_mini_history, which discovery skips."""
import json
import os
from pathlib import Path

from weekly_review.config import ProjectSpec, ProjectsConfig
from weekly_review.events import dedupe_by_event_id
from weekly_review.extractors.claude_transcript import (
    GENERATION_TURN_OFFSET, extract_from_jsonl, recovered_generations)

PROJECTS = ProjectsConfig(projects=[
    ProjectSpec(slug="weekly-review", cwd_patterns=["/Users/*/dev/weekly-review", "/Users/*/dev/weekly-review/**"]),
])


def _rec(uid, ts, kind="assistant", text="working"):
    base = {"uuid": uid, "sessionId": "lead", "cwd": "/Users/me/dev/weekly-review", "timestamp": ts}
    if kind == "typed":
        return {**base, "type": "user", "promptSource": "typed", "message": {"role": "user", "content": text}}
    return {**base, "type": "assistant", "message": {"role": "assistant", "model": "m",
                                                     "content": [{"type": "text", "text": text}]}}


def _write(path: Path, records, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


OLD = [_rec("o1", "2026-08-17T19:00:00Z", "typed", "fix the export"), _rec("o2", "2026-08-17T19:01:00Z"),
       _rec("o3", "2026-08-17T19:28:00Z")]
NEW = [_rec("n1", "2026-08-17T19:44:00Z", "typed", "look at the board"), _rec("n2", "2026-08-17T19:45:00Z")]


def test_a_snapshot_of_a_different_generation_is_recovered_and_the_largest_copy_wins(tmp_path):
    cur = _write(tmp_path / "mirror" / "-proj" / "lead.jsonl", NEW)
    hist = tmp_path / "history"
    small = _write(hist / "20260817T194555Z" / "-proj--wt" / "lead.jsonl", OLD[:2])
    big = _write(hist / "20260824T174629Z" / "-proj" / "lead.jsonl", OLD)
    _write(hist / "20260801T000000Z" / "-proj" / "lead.jsonl", NEW[:1])  # older state of the current file
    _write(hist / "20260801T000000Z" / "-proj" / "gone.jsonl", OLD)  # no current copy of this session
    _write(hist / "20260801T000000Z" / "-proj" / "lead" / "subagents" / "agent-a.jsonl", OLD)
    got = recovered_generations(hist, [cur])
    assert got == [(big, "gen-o1")]
    assert small != big


def test_a_generation_matching_any_current_copy_is_not_recovered(tmp_path):
    cur_a = _write(tmp_path / "mirror" / "-proj" / "lead.jsonl", NEW)
    cur_b = _write(tmp_path / "mirror" / "-proj--wt" / "lead.jsonl", OLD)
    _write(tmp_path / "history" / "20260824T174629Z" / "-proj" / "lead.jsonl", OLD)
    assert recovered_generations(tmp_path / "history", [cur_a, cur_b]) == []
    assert recovered_generations(tmp_path / "nowhere", [cur_a]) == []


def test_a_snapshot_last_written_before_the_window_is_not_read(tmp_path):
    cur = _write(tmp_path / "mirror" / "-proj" / "lead.jsonl", NEW)
    snap = _write(tmp_path / "history" / "s" / "-proj" / "lead.jsonl", OLD, mtime=1_700_000_000)  # 2023
    assert recovered_generations(tmp_path / "history", [cur], since_iso="2026-08-17T00:00:00Z") == []
    assert recovered_generations(tmp_path / "history", [cur], since_iso="2023-01-01T00:00:00Z") == [(snap, "gen-o1")]


def test_recovered_events_skip_records_the_current_copy_holds_and_never_collide_with_it(tmp_path):
    # The current file resumed the old loop, so it repeats o3 before its own records.
    cur = _write(tmp_path / "mirror" / "lead.jsonl", [OLD[2]] + NEW)
    snap = _write(tmp_path / "history" / "s" / "p" / "lead.jsonl", OLD)
    held: set = set()
    current = list(extract_from_jsonl(cur, projects=PROJECTS, source_machine="mm", seen_uuids=held))
    plain = list(extract_from_jsonl(cur, projects=PROJECTS, source_machine="mm"))
    assert [e.event_id for e in current] == [e.event_id for e in plain]  # persisted ids never shift
    assert held == {"o3", "n1", "n2"}

    old = list(extract_from_jsonl(snap, projects=PROJECTS, source_machine="mm", seen_uuids=held, generation="gen-o1"))
    assert [e.ts for e in old] == ["2026-08-17T19:00:00Z", "2026-08-17T19:01:00Z"]
    assert all(":gen-o1:" in e.event_id for e in old)
    assert {e.metadata["transcript_generation"] for e in old} == {"gen-o1"}
    assert len(dedupe_by_event_id(current + old)) == len(current) + len(old)

    turns_new = {e.metadata["turn"] for e in current if e.metadata.get("turn") is not None}
    turns_old = {e.metadata["turn"] for e in old if e.metadata.get("turn") is not None}
    assert turns_old and not turns_new & turns_old
    assert min(turns_old) >= GENERATION_TURN_OFFSET
    asks = [e.metadata["ask_turn"] for e in old if e.metadata.get("ask_turn") is not None]
    assert asks and all(a >= GENERATION_TURN_OFFSET for a in asks)


def test_the_audit_finds_an_overwritten_session_by_timestamps_inside_the_window(tmp_path):
    from weekly_review.validate import overwritten_sessions
    mirror, hist = tmp_path / "mirror", tmp_path / "history"
    _write(mirror / "-proj" / "lead.jsonl", NEW)
    _write(mirror / "-proj" / "fine.jsonl", [_rec("f1", "2026-08-01T00:00:00Z")])
    _write(hist / "20260824T174629Z" / "-proj" / "lead.jsonl", [_rec("a", "2026-08-10T00:00:00Z")] + OLD)
    _write(hist / "20260817T000000Z" / "-proj" / "lead.jsonl", [_rec("a", "2026-08-10T00:00:00Z")])
    _write(hist / "20260801T000000Z" / "-proj" / "fine.jsonl", [_rec("f1", "2026-08-01T00:10:00Z")])  # same start hour
    got = overwritten_sessions(mirror, hist, "2026-08-17", "2026-08-23")
    assert got == {"lead": ("20260824T174629Z", "2026-08-17", "2026-08-17T19:44:00Z", 3)}
    assert overwritten_sessions(mirror, hist, "2026-08-18", "2026-08-23") == {}  # lost span ends before the window
    assert overwritten_sessions(mirror, hist, "2026-08-01", "2026-08-09") == {}  # and begins after this one
    assert overwritten_sessions(mirror, hist, "2026-08-01", "2026-08-10")["lead"][3] == 1  # end date inclusive


def test_the_pipeline_reads_recovered_generations():
    """Call-site check: a recovery function nothing calls recovers nothing."""
    src = (Path(__file__).parent.parent / "src" / "weekly_review" / "cli.py").read_text()
    assert "recovered_generations(troot / \"mac_mini_history\"" in src
    assert "generation=generation" in src
    assert "seen_uuids=seen)" in src
