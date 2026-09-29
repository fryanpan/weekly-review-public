"""Refresh the productivity multiplier tables from one pipeline run.

The age-of-agents post quoted a multiplier that had gone stale by September.
This turns the Apr-Sep refresh into one command, so any month can be rerun and
compared with the last:

  headline      per-task median est_h / hands-on (3-minute floor), transcripts-only
                variant, wall-clock speedup, pooled sum(est) / sum(hands-on)
  monthly       the same per Pacific month, by the month the task started
  weekly        the same per Monday-start PT week
  by type, by size, and where pooled output concentrates
  validation    enrichment coverage, est vs its own rationale, artifact double
                credit, the run's non-PASS audits
  spend         enrichment calls recorded in the run DB, at list price

Hands-on is the persisted `task_metrics.active_hours`. Its transcript half is not
persisted, so it is recomputed with the shipped `hands_on_intervals_by_task`, and
the run is refused unless that union reproduces the persisted value per task.

Weeks and months carry markers where a measurement source arrived, so a step
there is not read as a change in how the work was done.

Usage:
  uv run python3 scripts/multiplier_refresh.py --run run-20260914T111428Z
  uv run python3 scripts/multiplier_refresh.py --run <new> --prior run-20260914T111428Z \\
      --since 2026-09-14 --until 2026-10-11 --out docs/research/<date>-multiplier.md
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
RUNS = Path.home() / ".local" / "share" / "weekly-review" / "runs"
PT = ZoneInfo("America/Los_Angeles")
FLOOR_H = 3 / 60
RECONSTRUCTION_TOLERANCE_H = 1 / 60

# When a new instrument started feeding hands-on time. A step in the series on
# or after one of these is a measurement change until shown otherwise.
SOURCE_ARRIVALS = {
    date(2026, 4, 17): "workspace review time",
    date(2026, 8, 13): "board actions tied to tasks",
    date(2026, 8, 18): "voice requests",
    date(2026, 8, 28): "recorded meetings",
}

SIZE_BUCKETS = [("trivial <30m", 0.0, 0.5), ("small 30m-2h", 0.5, 2.0),
                ("moderate 2-8h", 2.0, 8.0), ("day+ >=8h", 8.0, float("inf"))]

# List price per million tokens: input, output, cache read, cache write.
PRICES = {"claude-sonnet-4-6": (3.0, 15.0, 0.30, 3.75)}

_RATIONALE_HOURS = re.compile(r"(?:≈|~|about |roughly )\s*(\d+(?:\.\d+)?)\s*h")


# ----------------------------------------------------------------- statistics

def size_bucket(est_h: float) -> str:
    for name, lo, hi in SIZE_BUCKETS:
        if lo <= est_h < hi:
            return name
    return SIZE_BUCKETS[0][0]


def ratios(rows: list[dict], den: str) -> list[float]:
    """est / den for every task with an estimate and at least 3 minutes of den."""
    return [r["est"] / r[den] for r in rows if r.get("est") and (r.get(den) or 0) >= FLOOR_H]


def quartiles(values: list[float]):
    """(median, p25, p75, n), or None under four values."""
    if len(values) < 4:
        return None
    p25, med, p75 = statistics.quantiles(sorted(values), n=4, method="inclusive")
    return med, p25, p75, len(values)


def fmt(stat) -> str:
    return "n<4" if not stat else f"{stat[0]:.1f}× (IQR {stat[1]:.1f}–{stat[2]:.1f}, n={stat[3]})"


def pooled(rows: list[dict]) -> tuple[float, float, float | None]:
    e = sum(r.get("est") or 0 for r in rows)
    h = sum(r.get("hands_on") or 0 for r in rows)
    return e, h, (e / h if h else None)


def headline(rows: list[dict]) -> dict:
    e, h, p = pooled(rows)
    return {"median": quartiles(ratios(rows, "hands_on")),
            "transcripts": quartiles(ratios(rows, "transcript")),
            "wall_clock": quartiles(ratios(rows, "wall")),
            "est_h": e, "hands_on_h": h, "pooled": p, "tasks": len(rows)}


def week_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def markers_between(start: date, end: date) -> list[str]:
    """Source arrivals in [start, end), as '<Mon d> <source>'."""
    return [f"{d:%b} {d.day} {name}" for d, name in sorted(SOURCE_ARRIVALS.items()) if start <= d < end]


def rationale_mismatch(est: float | None, rationale: str | None) -> float | None:
    """The rationale's last stated hour figure when it is >=3x from the stored est."""
    nums = [float(x) for x in _RATIONALE_HOURS.findall(rationale or "")]
    if not nums or not est:
        return None
    lo, hi = sorted((nums[-1], est))
    return nums[-1] if hi / max(lo, 1e-6) >= 3 else None


def spend_usd(model: str, inp: int, out: int, cache_read: int, cache_write: int) -> float | None:
    p = PRICES.get(model)
    if p is None:
        return None
    return (inp * p[0] + out * p[1] + cache_read * p[2] + cache_write * p[3]) / 1e6


# ----------------------------------------------------------------- loading

def resolve_run(run: str) -> Path:
    p = Path(run).expanduser()
    return p if p.is_dir() else RUNS / run


def load_rows(run_dir: Path) -> tuple[list[dict], float]:
    """Per-task rows and the worst |recomputed union - persisted hands-on| in hours."""
    sys.path.insert(0, str(REPO / "src"))
    from weekly_review.effort import _union_seconds, hands_on_intervals_by_task
    from weekly_review.persist import metric_columns
    from weekly_review.rundb import load_run

    evs, tasks = load_run(run_dir / "run.sqlite")
    c = sqlite3.connect(f"file:{run_dir / 'run.sqlite'}?mode=ro", uri=True)
    _mc = metric_columns(c)
    metrics = {r[0]: r[1:] for r in c.execute(f"select task_id, active_hours, {_mc['labor']} from task_metrics m")}
    est = {r[0]: r[1:] for r in c.execute("select task_id, est_human_hours, type from enriched")}

    rows, worst = [], 0.0
    for tid, (tr, ws) in hands_on_intervals_by_task(list(tasks.values()), evs).items():
        ho, wall = metrics.get(tid, (0.0, 0.0))
        worst = max(worst, abs(_union_seconds(tr + ws) / 3600 - ho))
        if tid not in est:
            continue
        first = tasks[tid].first_event_ts
        rows.append({"task_id": tid, "project": tasks[tid].project, "est": est[tid][0], "type": est[tid][1],
                     "hands_on": ho, "transcript": _union_seconds(tr) / 3600, "wall": wall,
                     "day": first.astimezone(PT).date() if first else None})
    return rows, worst


# ----------------------------------------------------------------- rendering

def _headline_table(cur: dict, prior: dict | None, label: str, prior_label: str) -> list[str]:
    cols = [label] + ([prior_label] if prior else [])
    out = ["| Measure | " + " | ".join(cols) + " |", "| --- |" + " --- |" * len(cols)]

    def row(name, key, f):
        vals = [f(cur)] + ([f(prior)] if prior else [])
        out.append(f"| {name} | " + " | ".join(vals) + " |")

    row("Per-task median, hands-on", "median", lambda h: fmt(h["median"]))
    row("Per-task median, transcripts only", "transcripts", lambda h: fmt(h["transcripts"]))
    row("Wall-clock speedup", "wall_clock", lambda h: fmt(h["wall_clock"]))
    row("Pooled Σest ÷ Σhands-on", "pooled",
        lambda h: f"{h['pooled']:.1f}× ({h['est_h']:,.0f}h ÷ {h['hands_on_h']:.1f}h)" if h["pooled"] else "—")
    row("Estimated tasks", "tasks", lambda h: f"{h['tasks']:,}")
    return out


def _period_table(groups: dict, title: str, span_days: int) -> list[str]:
    out = [f"| {title} | Tasks | Hands-on | Transcripts only | Wall-clock speedup | Pooled | Of hands-on, transcripts | Source arrivals |",
           "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for start in sorted(groups):
        rs = groups[start]
        e, h, p = pooled(rs)
        tr = sum(r["transcript"] for r in rs)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) if span_days == 31 else start + timedelta(days=7)
        label = f"{start:%Y-%m}" if span_days == 31 else start.isoformat()
        out.append(f"| {label} | {len(rs)} | {fmt(quartiles(ratios(rs, 'hands_on')))} | "
                   f"{fmt(quartiles(ratios(rs, 'transcript')))} | {fmt(quartiles(ratios(rs, 'wall')))} | "
                   f"{f'{p:.1f}×' if p else '—'} | {100 * tr / h if h else 0:.0f}% | "
                   f"{', '.join(markers_between(start, end))} |")
    return out


def _share_table(title: str, groups: list[tuple[str, list[dict]]], total: list[dict]) -> list[str]:
    e_all, h_all, _ = pooled(total)
    out = [f"| {title} | Tasks | Share of output | Share of hands-on | Pooled | Per-task median | Wall-clock speedup |",
           "| --- | --- | --- | --- | --- | --- | --- |"]
    for name, rs in groups:
        e, h, p = pooled(rs)
        out.append(f"| {name} | {len(rs):,} | {100 * e / e_all if e_all else 0:.0f}% | "
                   f"{100 * h / h_all if h_all else 0:.0f}% | {f'{p:.1f}×' if p else '—'} | "
                   f"{fmt(quartiles(ratios(rs, 'hands_on')))} | {fmt(quartiles(ratios(rs, 'wall')))} |")
    return out


def render_tables(rows: list[dict], prior_rows: list[dict] | None = None,
                  label: str = "This run", prior_label: str = "Prior") -> str:
    rows = [r for r in rows if r.get("est")]
    lines = ["## Headline", ""]
    lines += _headline_table(headline(rows), headline(prior_rows) if prior_rows else None, label, prior_label)

    months, weeks = defaultdict(list), defaultdict(list)
    for r in rows:
        if r["day"]:
            months[r["day"].replace(day=1)].append(r)
            weeks[week_of(r["day"])].append(r)
    lines += ["", "## By month the task started (PT)", ""] + _period_table(months, "Month", 31)
    lines += ["", "## By week the task started (Monday, PT)", ""] + _period_table(weeks, "Week", 7)

    types = defaultdict(list)
    for r in rows:
        types[r["type"] or "unknown"].append(r)
    lines += ["", "## By type", ""] + _share_table(
        "Type", sorted(types.items(), key=lambda kv: -len(kv[1])), rows)
    lines += ["", "## By size (estimated hours)", ""] + _share_table(
        "Size", [(name, [r for r in rows if size_bucket(r["est"]) == name]) for name, _, _ in SIZE_BUCKETS], rows)

    inc = [r for r in rows if r["hands_on"] >= FLOOR_H]
    own = lambda r: r["est"] / r["hands_on"]  # noqa: E731
    lines += ["", "## Where pooled output concentrates", "",
              "The median leaves out tasks under 3 minutes hands-on; pooled includes them.", ""]
    lines += _share_table("Own multiplier", [
        ("Under 3 min hands-on", [r for r in rows if r["hands_on"] < FLOOR_H]),
        ("Under 2×", [r for r in inc if own(r) < 2]),
        ("2–5×", [r for r in inc if 2 <= own(r) < 5]),
        ("5–20×", [r for r in inc if 5 <= own(r) < 20]),
        ("20× and up", [r for r in inc if own(r) >= 20])], rows)
    return "\n".join(lines)


def validation(run_dir: Path, rows: list[dict], worst_h: float) -> str:
    c = sqlite3.connect(f"file:{run_dir / 'run.sqlite'}?mode=ro", uri=True)
    n_tasks = c.execute("select count(*) from tasks").fetchone()[0]
    est_rows = [r for r in rows if r.get("est")]
    tot = sum(r["est"] for r in est_rows) or 1.0
    below = [r for r in est_rows if r["hands_on"] < FLOOR_H]
    mism = sum(1 for est, rat in c.execute("select est_human_hours, rationale from enriched")
               if rationale_mismatch(est, rat) is not None)
    owner_dup = c.execute("select count(*) from artifact_credit a, json_each(a.duplicate_task_ids_json) d "
                          "where d.value = a.owner_task_id").fetchone()[0]
    lines = ["## Validation", "",
             f"- Hands-on reconstruction: worst per-task difference {worst_h * 3600:.0f}s "
             f"(refused above {RECONSTRUCTION_TOLERANCE_H * 3600:.0f}s).",
             f"- Enriched: {len(est_rows):,} of {n_tasks:,} tasks.",
             f"- Under 3 min hands-on (outside the median, inside pooled): {len(below):,} tasks, "
             f"{100 * sum(r['est'] for r in below) / tot:.0f}% of estimated hours.",
             f"- Stored est ≥3× from the figure in its own rationale: {mism} tasks. "
             "Review verdicts carry forward with the LLM's new rationale, so some of these are expected.",
             f"- Artifacts credited to a task that also lists them as credited elsewhere (must be 0): {owner_dup}."]
    audits = run_dir / "audits.json"
    if audits.exists():
        data = json.loads(audits.read_text())
        checks = data["checks"] if isinstance(data, dict) else data
        bad = [a for a in checks if a.get("status") != "PASS"]
        lines.append(f"- Run audits not PASS: {len(bad)} of {len(checks)}.")
        lines += [f"  - {a['status']}: {a['name']}: {str(a.get('detail', ''))[:140]}" for a in bad]
    else:
        lines.append("- Run audits: no audits.json beside the run.")
    return "\n".join(lines)


def spend(run_dir: Path) -> str:
    c = sqlite3.connect(f"file:{run_dir / 'run.sqlite'}?mode=ro", uri=True)
    lines = ["## Enrichment spend", "",
             "Calls recorded in this run's DB at list price. Calls carried from earlier runs' "
             "cache are included, so this is an upper bound on what the run itself spent.", "",
             "| Day called (UTC) | Model | Calls | List price |", "| --- | --- | --- | --- |"]
    for day, model, n, i, o, cr, cw in c.execute(
            "select substr(called_at, 1, 10), model, count(*), sum(input_tokens), sum(output_tokens), "
            "sum(cache_read_tokens), sum(cache_creation_tokens) from enrichment_calls group by 1, 2 order by 1"):
        usd = spend_usd(model, i, o, cr, cw)
        lines.append(f"| {day} | {model} | {n:,} | {'unpriced' if usd is None else f'${usd:,.2f}'} |")
    return "\n".join(lines)


def in_window(rows: list[dict], since: date | None, until: date | None) -> list[dict]:
    return [r for r in rows if r["day"] and (since is None or r["day"] >= since)
            and (until is None or r["day"] <= until)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run", required=True, help="run id under ~/.local/share/weekly-review/runs, or a run dir")
    ap.add_argument("--prior", help="an earlier run whose headline goes beside this one")
    ap.add_argument("--since", type=date.fromisoformat, help="first PT day a task may start, inclusive")
    ap.add_argument("--until", type=date.fromisoformat, help="last PT day a task may start, inclusive")
    ap.add_argument("--out", type=Path, help="write the markdown here instead of stdout")
    args = ap.parse_args(argv)

    run_dir = resolve_run(args.run)
    rows, worst = load_rows(run_dir)
    if worst > RECONSTRUCTION_TOLERANCE_H:
        print(f"REFUSED: recomputed hands-on differs from the persisted value by {worst * 3600:.0f}s",
              file=sys.stderr)
        return 2
    window = in_window(rows, args.since, args.until)
    prior_rows = None
    if args.prior:
        prior_rows, prior_worst = load_rows(resolve_run(args.prior))
        if prior_worst > RECONSTRUCTION_TOLERANCE_H:
            print(f"REFUSED: prior run reconstruction differs by {prior_worst * 3600:.0f}s", file=sys.stderr)
            return 2

    span = (f"tasks started {args.since or 'from the first'} to {args.until or 'the last'}"
            if args.since or args.until else "all tasks")
    doc = "\n\n".join([
        f"# Multiplier refresh: {run_dir.name}, {span}",
        f"Generated by `scripts/multiplier_refresh.py`. Per-task figures use tasks with at least "
        f"3 minutes of the denominator. Pooled uses every estimated task.",
        render_tables(window, prior_rows, run_dir.name, args.prior or ""),
        validation(run_dir, window, worst),
        spend(run_dir),
    ]) + "\n"
    if args.out:
        args.out.write_text(doc)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(doc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
