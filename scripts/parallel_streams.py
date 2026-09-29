"""How many projects and parallel agent streams does the fleet run, and how often overnight?

Reproduces the lead agent's 2026-09-14 fleet check for the age-of-agents post, which
reported 6-18 active projects a week (median 9), one stream per active project
bursting to 4-7, and overnight activity. Their method, kept here as the default:

  project  = transcript project dir, worktree suffix stripped
  turn     = one `"type":"assistant"` record
  active   = a project with >= 50 turns in a Monday-start PT week
  stream   = one transcript file (a session's main lane, or one subagent lane)
  window   = 30-minute PT bucket; night = 01:00-05:00 PT

`--team-lead-method` reproduces that check exactly. The default fixes two parse
defects in it (see `find_transcripts` and `scan_lines`).

Two things make the unfiltered count misleading, so both have switches:

  --drop-prompt-source system   drops turns answering a scheduled (cron) prompt.
      Cron prompts are injected into long-running sessions, so this is per turn:
      a turn inherits the source of the latest human-lane prompt before it.
  --min-lane-minutes N          drops streams whose first-to-last turn span is
      under N minutes, so a burst of tiny subagents does not read as N teams.

The live store (~/.claude/projects) keeps about 16 weeks, so a window reaching
further back must include the archive. Copies of one transcript found under
several roots or project dirs count once (the largest copy wins).

Output is counts only: no project names, prompts or paths.

Usage:
  python3 scripts/parallel_streams.py --root ~/.claude/projects
  python3 scripts/parallel_streams.py --root ~/dev/weekly-review-transcripts/mac_mini \\
      --drop-prompt-source system --min-lane-minutes 5
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PT = ZoneInfo("America/Los_Angeles")
BUCKET_SEC = 1800
ACTIVE_TURNS = 50
NIGHT_HOURS = range(1, 5)
NIGHT_TURN_LEVELS = (1, 50, 200)

# `<home>/dev/` as ~/.claude/projects encodes it (`/` becomes `-`), whether the
# home directory is on the boot disk or on a second volume.
_HOME_PREFIX = re.compile(r"^-(?:Volumes-[^-]+-)?Users-[^-]+-dev-")
_WORKTREE_SPLIT = re.compile(r"--claude-worktrees-|-\.claude-worktrees-|-worktrees-")
_TS = re.compile(rb'"timestamp":"([0-9T:.\-]+)Z"')
_PROMPT_SOURCE = re.compile(rb'"promptSource":"([a-zA-Z_]+)"')


def project_from_dir(name: str) -> str:
    """Encoded cwd dir name -> project, with the worktree suffix stripped."""
    name = _HOME_PREFIX.sub("", name, count=1)
    return _WORKTREE_SPLIT.split(name)[0]


@dataclass
class Stream:
    """One transcript file: its project, and its turns per 30-minute bucket."""
    project: str
    buckets: Counter = field(default_factory=Counter)  # bucket epoch -> turns
    first: float | None = None
    last: float | None = None

    def add(self, epoch: float) -> None:
        self.buckets[int(epoch // BUCKET_SEC * BUCKET_SEC)] += 1
        self.first = epoch if self.first is None else min(self.first, epoch)
        self.last = epoch if self.last is None else max(self.last, epoch)

    @property
    def minutes(self) -> float:
        return 0.0 if self.first is None else (self.last - self.first) / 60


def _epoch(raw: bytes) -> float:
    return datetime.fromisoformat(raw.decode() + "+00:00").timestamp()


def scan_lines(lines, project: str, start: float, end: float,
               drop_sources: frozenset[str] = frozenset(),
               team_lead_method: bool = False) -> Stream:
    """Collect one file's assistant turns inside [start, end).

    A user record carrying `promptSource` sets the source for the turns after it;
    a typed-looking user record without one (peer relay, channel message) resets
    it. Tool results do neither, because they continue the same prompt's work.

    `team_lead_method` reads the timestamp only from the line's first 3,000
    bytes, as the original check did. The top-level timestamp follows the message
    body, so every turn longer than that is dropped.
    """
    stream = Stream(project)
    source = None
    for line in lines:
        if b'"type":"user"' in line:
            m = _PROMPT_SOURCE.search(line)
            if m:
                source = m.group(1).decode()
            elif b'"tool_result"' not in line:
                source = None
            continue
        if b'"type":"assistant"' not in line:
            continue
        if source in drop_sources:
            continue
        if team_lead_method:
            m = _TS.search(line[:3000])
            raw = m.group(1) if m else None
        else:
            # Timestamps quoted inside the body are escaped and never match; a nested
            # object's would, and the top-level one is last, so take the last.
            matches = _TS.findall(line)
            raw = matches[-1] if matches else None
        if raw is None:
            continue
        t = _epoch(raw)
        if start <= t < end:
            stream.add(t)
    return stream


def find_transcripts(roots: list[Path], start: float,
                     dedup: bool = True) -> dict[str, tuple[Path, str]]:
    """Every transcript modified since `start`, one copy per (project dir-relative) path.

    Transcripts are append-only, so a file last written before the window has no
    turns in it. The same session can sit under several roots or project dirs;
    keying on the path inside the project dir and keeping the largest copy stops
    it counting as two streams. On the live store in Sep 2026 that one case was a
    300MB session copied into three scratchpad-cwd dirs, each of which read as an
    extra active project in every week the session ran.
    """
    best: dict[str, tuple[int, Path, str]] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for project_dir in root.iterdir():
            if not project_dir.is_dir():
                continue
            project = project_from_dir(project_dir.name)
            for dirpath, _dirs, files in os.walk(project_dir):
                for name in files:
                    if not name.endswith(".jsonl"):
                        continue
                    path = Path(dirpath) / name
                    try:
                        st = path.stat()
                    except OSError:
                        continue
                    if st.st_mtime < start:
                        continue
                    key = str(path.relative_to(project_dir)) if dedup else str(path)
                    if key not in best or st.st_size > best[key][0]:
                        best[key] = (st.st_size, path, project)
    return {k: (p, proj) for k, (_s, p, proj) in best.items()}


def _q(values: list, x: float):
    """The lead agent's quantile: the element at floor(n * x) of the sorted list."""
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * x))]


def summarize(streams: list[Stream], start_day: date, end_day: date,
              min_lane_minutes: float = 0.0) -> dict:
    """Weekly active projects, same-window concurrency, streams, and nights."""
    kept = [s for s in streams if s.buckets and s.minutes >= min_lane_minutes]
    week_turns: dict[date, Counter] = defaultdict(Counter)
    window: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    night: dict[date, Counter] = defaultdict(Counter)
    for s in kept:
        for b, n in s.buckets.items():
            t = datetime.fromtimestamp(b, PT)
            week_turns[t.date() - timedelta(days=t.weekday())][s.project] += n
            window[b][s.project] += 1
            if t.hour in NIGHT_HOURS:
                night[t.date()][s.project] += n

    weeks = []
    monday = start_day - timedelta(days=start_day.weekday())
    while monday < end_day:
        weeks.append(sum(1 for c in week_turns[monday].values() if c >= ACTIVE_TURNS))
        monday += timedelta(days=7)

    concurrent = [len(v) for v in window.values()]
    per_project = [n for v in window.values() for n in v.values()]
    peak: dict[tuple, int] = defaultdict(int)
    for b, v in window.items():
        d = datetime.fromtimestamp(b, PT).date()
        for p, n in v.items():
            peak[(p, d)] = max(peak[(p, d)], n)
    peaks = list(peak.values())

    def dist(values):
        if not values:
            return {"median": 0, "p90": 0, "max": 0}
        return {"median": statistics.median(values), "p90": _q(values, 0.9), "max": max(values)}

    return {
        "streams": len(kept),
        "weeks": len(weeks),
        "active_projects_per_week": {
            "median": statistics.median(weeks) if weeks else 0,
            "min": min(weeks, default=0), "max": max(weeks, default=0), "by_week": weeks,
        },
        "projects_in_same_window": dist(concurrent),
        "streams_per_active_project_window": dist(per_project),
        "peak_streams_per_project_day": dist(peaks),
        "nights": {
            "days": (end_day - start_day).days,
            **{f">={lvl}_turns": sum(1 for c in night.values() if sum(c.values()) >= lvl)
               for lvl in NIGHT_TURN_LEVELS},
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", action="append", type=Path,
                    help="transcript projects dir (repeatable); default ~/.claude/projects")
    ap.add_argument("--since", default="2026-04-13", help="first PT day, inclusive")
    ap.add_argument("--until", default="2026-09-13", help="last PT day, inclusive")
    ap.add_argument("--drop-prompt-source", action="append", default=[],
                    help="drop turns answering prompts with this promptSource (repeatable)")
    ap.add_argument("--min-lane-minutes", type=float, default=0.0,
                    help="drop streams whose first-to-last turn span is shorter")
    ap.add_argument("--team-lead-method", action="store_true",
                    help="reproduce the original check: no copy dedup, first timestamp in 3,000 bytes")
    ap.add_argument("--json", action="store_true", help="print the summary as JSON")
    args = ap.parse_args(argv)

    roots = [r.expanduser() for r in (args.root or [Path.home() / ".claude" / "projects"])]
    start_day = date.fromisoformat(args.since)
    end_day = date.fromisoformat(args.until) + timedelta(days=1)
    start = datetime.combine(start_day, datetime.min.time(), PT).timestamp()
    end = datetime.combine(end_day, datetime.min.time(), PT).timestamp()

    files = find_transcripts(roots, start, dedup=not args.team_lead_method)
    drop = frozenset(args.drop_prompt_source)
    streams = []
    for path, project in files.values():
        try:
            with open(path, "rb") as fh:
                streams.append(scan_lines(fh, project, start, end, drop, args.team_lead_method))
        except OSError as exc:
            print(f"skip unreadable transcript: {exc}", file=sys.stderr)
    out = summarize(streams, start_day, end_day, args.min_lane_minutes)
    out["window"] = f"{start_day} .. {end_day - timedelta(days=1)} ({(end_day - start_day).days} days)"
    out["files_scanned"] = len(files)

    if args.json:
        print(json.dumps(out, indent=2))
        return 0
    a = out["active_projects_per_week"]
    print(f"window {out['window']}, {out['files_scanned']} files, {out['streams']} streams kept")
    print(f"active projects/week (>={ACTIVE_TURNS} turns): median {a['median']} "
          f"min {a['min']} max {a['max']}  {a['by_week']}")
    for key in ("projects_in_same_window", "streams_per_active_project_window",
                "peak_streams_per_project_day"):
        d = out[key]
        print(f"{key}: median {d['median']} p90 {d['p90']} max {d['max']}")
    n = out["nights"]
    print("nights with turns 01-05 PT: " + ", ".join(
        f"{k} {v} of {n['days']}" for k, v in n.items() if k != "days"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
