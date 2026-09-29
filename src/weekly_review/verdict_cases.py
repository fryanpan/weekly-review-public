"""The user's recorded verdicts, re-checked against every run.

The aggregate audits count shapes of error (zero hands-on, zero wall clock,
split tasks). They passed rounds of segmentation changes while the specific
tasks the user had flagged stayed wrong or got worse: an estimate he called low at
19h fell to 1.5h because the work spread over 11 tasks. Nothing noticed,
because nothing remembered what he had said.

A case pins one of his verdicts to the events of the task he reviewed. Event
ids are stable across runs; task ids are not. So each run finds the task that
now holds most of those events (the host) and checks his expectation against
it. The cases live in the user's data dir, not the repo: they are his verdicts
on his work.

Expectation keys:
  est_h      his number; the host's estimate must be within 2x of it
  est_ok     he accepted the estimate; the host's must stay within 2x of it
  hands_on_min / wall_min   host value must exceed this (0 = must be non-zero)
  wall_max   host wall clock must not exceed this
  project    host must sit in this project

**A verdict that cannot be checked is not a verdict that failed (2026-09-15).**
The check reported "0 of 3 hold" on two of the three era weeks, which reads as
his judgement being wrong, and not one of those was that. `uncheckable` now
separates the two, for the two ways a boundary can move under a case:

  - The reviewed work was re-cut across several tasks, so no task speaks for it.
    This was already detected and was reported as a failing estimate.
  - The host SWALLOWED much more work, which nothing looked for, because `share`
    only asks whether the reviewed events stayed together. On `2dfac481c8` his
    "Sounds fine" was recorded against 40 events of steering and a merge
    instruction, priced 0.25h with the build explicitly "priced in the sibling";
    the same id in the April run holds 323 events including the build and is
    priced 18h. Share was 0.97, so the check judged it and called his verdict
    broken over a 72x gap it had manufactured itself. `calibration` already
    refuses a review override on exactly this evidence, so reuse its ratio.

Uncheckable is neither a pass nor a fail, like out-of-window. A week where
nothing is checkable still reports REVIEW — the alarm is that the harness has
nothing to say about that week, and saying so is the point.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .calibration import carry_forward_ok

CASES_PATH = Path.home() / ".local/share/weekly-review/verdict_cases.json"
EST_TOLERANCE = 2.0
MIN_HOST_SHARE = 0.5


@dataclass
class Host:
    task_id: str
    share: float          # fraction of the anchor's events on the host
    spread: int           # distinct tasks now holding anchor events
    est: float | None
    hands_on: float
    wall: float
    project: str
    events: int = 0       # everything the host holds, reviewed or not


def _within(value: float | None, target: float, tol: float = EST_TOLERANCE) -> bool:
    return value is not None and target / tol <= value <= target * tol


def uncheckable(host: Host | None, anchor_events: int) -> str | None:
    """Why this run cannot judge the verdict at all, or None if it can.

    Both branches say the same thing in opposite directions: the task in front
    of us is not the task he looked at. Neither is a defect on its own — a
    re-segmentation that cuts a 22,880-event mega-task into fifty is the
    oversize rule working — so neither may be reported as a broken verdict.
    """
    if host is None or not anchor_events:
        return None                      # no host at all is a real failure
    if host.share < MIN_HOST_SHARE:
        return (f"the reviewed work is now split {host.spread} ways, "
                f"{host.share:.0%} of it on the largest piece")
    if not carry_forward_ok(anchor_events, host.events):
        return (f"the host has since absorbed other work: {anchor_events} events when "
                f"he reviewed it, {host.events} now ({host.events / anchor_events:.1f}x)")
    return None


def check_case(expect: dict, anchor_est: float | None, host: Host | None) -> list[str]:
    """Every way the host breaks the verdict, in words; empty means it holds.

    Call it only on a host `uncheckable` cleared; it assumes the host speaks for
    the reviewed work."""
    if host is None:
        return ["none of the reviewed events are on any task"]
    fails: list[str] = []
    if "est_h" in expect and not _within(host.est, expect["est_h"]):
        fails.append(f"est {host.est}h, he said ~{expect['est_h']}h")
    if expect.get("est_ok") and anchor_est is not None and not _within(host.est, anchor_est):
        fails.append(f"est {host.est}h, he accepted {anchor_est}h")
    if "hands_on_min" in expect and not host.hands_on > expect["hands_on_min"]:
        fails.append(f"hands-on {host.hands_on:.2f}h, must exceed {expect['hands_on_min']}h")
    if "wall_min" in expect and not host.wall > expect["wall_min"]:
        fails.append(f"wall {host.wall:.2f}h, must exceed {expect['wall_min']}h")
    if "wall_max" in expect and host.wall > expect["wall_max"]:
        fails.append(f"wall {host.wall:.2f}h, must be at most {expect['wall_max']}h")
    if "project" in expect and host.project != expect["project"]:
        fails.append(f"project {host.project}, he said {expect['project']}")
    return fails


def _metrics(c: sqlite3.Connection, tid: str) -> sqlite3.Row | None:
    # `wall` here is labor (user ∪ agent, pauses removed) — the figure his
    # wall_min/wall_max verdicts were written against before 2026-09-18.
    from .persist import metric_columns
    mc = metric_columns(c)
    return c.execute(
        f"""SELECT t.project, e.est_human_hours est, COALESCE(m.active_hours, 0) ho,
                  COALESCE({mc['labor']}, 0) wall
           FROM tasks t LEFT JOIN enriched e USING(task_id)
           LEFT JOIN task_metrics m USING(task_id) WHERE t.task_id = ?""", (tid,)).fetchone()


def find_host(c: sqlite3.Connection, anchor_events: list[str]) -> Host | None:
    owners: Counter[str] = Counter()
    for i in range(0, len(anchor_events), 900):
        chunk = anchor_events[i:i + 900]
        owners.update(r[0] for r in c.execute(
            f"SELECT task_id FROM task_events WHERE event_id IN ({','.join('?' * len(chunk))})", chunk))
    if not owners:
        return None
    tid, n = owners.most_common(1)[0]
    m = _metrics(c, tid)
    held = c.execute("SELECT COUNT(*) FROM task_events WHERE task_id = ?", (tid,)).fetchone()[0]
    return Host(tid, n / len(anchor_events), len(owners), m["est"], m["ho"], m["wall"],
                m["project"], held)


def run_cases(c: sqlite3.Connection, runs_dir: Path, cases: list[dict]) -> list[dict]:
    """One result per case: {case, host, fails}. A case whose anchor run is gone
    is reported as a failure, never skipped, so a deleted run can't turn red green."""
    c.row_factory = sqlite3.Row
    out = []
    anchors: dict[str, sqlite3.Connection] = {}
    for case in cases:
        run, tid = case["anchor_run"], case["anchor_task"]
        db = runs_dir / run / "run.sqlite"
        if not db.exists():
            out.append({"case": tid, "host": None, "fails": [f"anchor run {run} is missing"]})
            continue
        a = anchors.setdefault(run, sqlite3.connect(db))
        a.row_factory = sqlite3.Row
        events = [r[0] for r in a.execute("SELECT event_id FROM task_events WHERE task_id = ?", (tid,))]
        am = _metrics(a, tid)
        if not events or am is None:
            out.append({"case": tid, "host": None, "fails": [f"task not in anchor run {run}"]})
            continue
        present = sum(c.execute(
            f"SELECT COUNT(*) FROM events WHERE event_id IN ({','.join('?' * len(chunk))})", chunk).fetchone()[0]
            for chunk in (events[i:i + 900] for i in range(0, len(events), 900)))
        if present < MIN_HOST_SHARE * len(events):
            # A one-week run cannot judge a task from another month. Say so
            # rather than pass it: out-of-window is neither a pass nor a fail.
            out.append({"case": tid, "host": None, "fails": [],
                        "out_of_window": True, "blocked": "outside this window"})
            continue
        host = find_host(c, events)
        blocked = uncheckable(host, len(events))
        out.append({"case": tid, "host": host.task_id if host else None, "blocked": blocked,
                    "fails": [] if blocked else check_case(case.get("expect", {}), am["est"], host)})
    for a in anchors.values():
        a.close()
    return out


def load_cases(path: Path = CASES_PATH) -> list[dict] | None:
    return json.loads(path.read_text()) if path.exists() else None
