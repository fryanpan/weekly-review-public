"""The fleet-shape counts quoted in the age-of-agents post must survive a re-check.

the lead agent's original scan counted copies of one session as extra projects and
misdated long turns. These tests pin the fixes and the two filters (scheduled
prompts, short-lived lanes) that the post's figures depend on.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "parallel_streams.py"
spec = importlib.util.spec_from_file_location("parallel_streams", SCRIPT)
ps = importlib.util.module_from_spec(spec)
sys.modules["parallel_streams"] = ps  # dataclasses resolve annotations through sys.modules
spec.loader.exec_module(ps)

START = datetime(2026, 5, 4, tzinfo=ps.PT).timestamp()
END = datetime(2026, 5, 11, tzinfo=ps.PT).timestamp()


def _iso(day: int, hour: int, minute: int = 0) -> str:
    """A PT wall time in May 2026 as the UTC `Z` string transcripts carry."""
    t = datetime(2026, 5, day, hour, minute, tzinfo=ps.PT)
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _assistant(ts: str, body: str = "ok") -> bytes:
    return json.dumps({"message": {"role": "assistant", "content": body},
                       "type": "assistant", "timestamp": ts}, separators=(",", ":")).encode()


def _user(ts: str, source: str | None = None, tool_result: bool = False) -> bytes:
    rec = {"message": {"role": "user",
                       "content": [{"type": "tool_result"}] if tool_result else "hi"},
           "type": "user", "timestamp": ts}
    if source:
        rec["promptSource"] = source
    return json.dumps(rec, separators=(",", ":")).encode()


def test_project_strips_both_worktree_forms():
    assert ps.project_from_dir("-Volumes-Data-Users-me-dev-ai-team-lead--claude-worktrees-fleet") == "ai-team-lead"
    assert ps.project_from_dir("-Users-me-dev-wrapper-project-worktrees-fast-build") == "wrapper-project"
    assert ps.project_from_dir("-Volumes-Data-Users-me-dev-weekly-review") == "weekly-review"


def test_long_turn_is_counted():
    # The top-level timestamp follows the body; the original check read only the
    # first 3,000 bytes and so dropped every long turn.
    lines = [_assistant(_iso(5, 10), "x" * 4000)]
    assert sum(ps.scan_lines(lines, "p", START, END).buckets.values()) == 1
    assert sum(ps.scan_lines(lines, "p", START, END, team_lead_method=True).buckets.values()) == 0


def test_scheduled_prompt_turns_drop_until_a_human_prompt():
    lines = [
        _user(_iso(5, 2), source="system"),
        _assistant(_iso(5, 2, 1)),
        _user(_iso(5, 2, 2), tool_result=True),   # same scheduled job keeps going
        _assistant(_iso(5, 2, 3)),
        _user(_iso(5, 9), source="typed"),
        _assistant(_iso(5, 9, 1)),
    ]
    kept = ps.scan_lines(lines, "p", START, END, drop_sources=frozenset({"system"}))
    assert sum(kept.buckets.values()) == 1
    assert sum(ps.scan_lines(lines, "p", START, END).buckets.values()) == 3


def test_relayed_message_without_a_source_ends_the_scheduled_job():
    # Peer relay and channel messages carry no promptSource; the work they start
    # is not the cron job's.
    lines = [_user(_iso(5, 2), source="system"), _assistant(_iso(5, 2, 1)),
             _user(_iso(5, 2, 5)), _assistant(_iso(5, 2, 6))]
    kept = ps.scan_lines(lines, "p", START, END, drop_sources=frozenset({"system"}))
    assert sum(kept.buckets.values()) == 1


def test_copies_of_one_session_count_once(tmp_path):
    for d in ("-Volumes-Data-Users-me-dev-alpha", "-private-tmp-scratch-copy"):
        (tmp_path / d).mkdir()
    small = tmp_path / "-private-tmp-scratch-copy" / "s1.jsonl"
    big = tmp_path / "-Volumes-Data-Users-me-dev-alpha" / "s1.jsonl"
    small.write_bytes(_assistant(_iso(5, 10)) + b"\n")
    big.write_bytes((_assistant(_iso(5, 10)) + b"\n") * 3)
    found = ps.find_transcripts([tmp_path], START)
    assert list(found.values()) == [(big, "alpha")]
    assert len(ps.find_transcripts([tmp_path], START, dedup=False)) == 2


def _stream(project: str, day: int, turns: int, span_min: int = 60) -> "ps.Stream":
    s = ps.Stream(project)
    base = datetime(2026, 5, day, 10, tzinfo=ps.PT).timestamp()
    for i in range(turns):
        s.add(base + span_min * 60 * i / max(1, turns - 1))
    return s


def test_active_project_needs_fifty_turns_in_the_week():
    streams = [_stream("a", 5, 50), _stream("b", 5, 49), _stream("c", 6, 30), _stream("c", 7, 30)]
    out = ps.summarize(streams, date(2026, 5, 4), date(2026, 5, 11))
    assert out["active_projects_per_week"]["by_week"] == [2]  # a, and c across two days


def test_short_lanes_are_dropped_from_streams_and_turns():
    streams = [_stream("a", 5, 60, span_min=60)] + [_stream("a", 5, 2, span_min=1) for _ in range(6)]
    loose = ps.summarize(streams, date(2026, 5, 4), date(2026, 5, 11))
    strict = ps.summarize(streams, date(2026, 5, 4), date(2026, 5, 11), min_lane_minutes=5)
    assert loose["peak_streams_per_project_day"]["max"] == 7
    assert strict["peak_streams_per_project_day"]["max"] == 1
    assert strict["streams"] == 1


def test_night_counts_turns_between_one_and_five_pt():
    s = ps.Stream("a")
    for minute in range(50):
        s.add(datetime(2026, 5, 6, 3, minute, tzinfo=ps.PT).timestamp())
    s.add(datetime(2026, 5, 7, 5, 30, tzinfo=ps.PT).timestamp())  # 05:30 is morning
    out = ps.summarize([s], date(2026, 5, 4), date(2026, 5, 11))
    assert out["nights"][">=1_turns"] == 1
    assert out["nights"][">=50_turns"] == 1
