"""Flat task_facts export — one denormalized row per task.

The substrate for local exploration (Tableau reads the CSV, DuckDB/sqlite3
read the sqlite twin). See scripts/export_task_facts.py for the CLI wrapper
and docs/product/specs/2026-06-13-review-data-model.md for the design.
Called automatically at the end of every pipeline run.
"""
from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from .calibration import carry_forward_ok, load_review_overrides_with_context
from .config import load_projects

DATA_DIR = Path.home() / ".local/share/weekly-review"
DEFAULT_OUT = Path.home() / "dev" / "weekly-review-data"
from .calendar_time import speedup

HANDS_ON_FLOOR_H = 5 / 3600  # 5s floor under which leverage is not computed

COLUMNS = [
    "task_id", "run_id", "project", "client", "billable", "excluded", "delegated",
    "week", "first_ts", "last_ts", "title", "type", "status",
    "uncertainty", "scope", "depth",
    "est_human_hours", "llm_est_hours", "review_status", "review_verdict",
    "hands_on_hours", "agent_active_hours", "agent_busy_hours", "agent_lane_hours",
    "labor_hours", "wall_clock_hours",
    "wall_clock_speedup", "hands_on_leverage", "est_size_bucket",
    "resumptions", "user_msg_count", "event_count",
    "hands_on_blocks", "human_input_chars", "dominant_model",
]


def week_of(ts: str) -> str:
    d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return (d - timedelta(days=d.weekday())).date().isoformat()


def size_bucket(est: float | None) -> str:
    if est is None:
        return ""
    if est < 0.5:
        return "trivial <0.5h"
    if est < 2:
        return "small 0.5-2h"
    if est < 8:
        return "moderate 2-8h"
    return "substantial >=8h"


def latest_run_id() -> str:
    # latest run that has actually persisted its DB (an in-flight run's dir
    # exists before its run.sqlite does)
    runs = sorted((DATA_DIR / "runs").glob("run-*"), reverse=True)
    for r in runs:
        if (r / "run.sqlite").exists():
            return r.name
    raise FileNotFoundError("no completed runs found")


def build_rows(run_id: str) -> list[dict]:
    db = DATA_DIR / "runs" / run_id / "run.sqlite"
    if not db.exists():
        raise FileNotFoundError(f"{db} missing")
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    mcols = {r[1] for r in conn.execute("PRAGMA table_info(task_metrics)")}
    # new metrics columns may be absent in runs older than 2026-07-08
    tcols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    deleg = "t.delegated" if "delegated" in tcols else "0"
    blocks = "m.hands_on_blocks" if "hands_on_blocks" in mcols else "NULL"
    chars = "m.human_input_chars" if "human_input_chars" in mcols else "NULL"
    model = "m.dominant_model" if "dominant_model" in mcols else "NULL"
    # labor (user ∪ agent, pauses removed), lane (per-lane agent time, since
    # 2026-07-12) and wall (calendar, since schema v2 2026-09-18) resolve per
    # schema version; NULL (not 0) where a run never measured one, so the BI
    # surface can tell "not measured" apart from a measured zero.
    from .persist import metric_columns
    mc = metric_columns(conn)

    projects = load_projects()
    all_dbs = sorted((DATA_DIR / "runs").glob("run-*/run.sqlite"))
    overrides = load_review_overrides_with_context(all_dbs)
    # verdict labels (latest per task) for review_status/review_verdict
    verdicts: dict[str, str] = {}
    for p in all_dbs:
        try:
            c = sqlite3.connect(str(p))
            for tid, ts, conf in c.execute(
                "SELECT task_id, reviewed_at, confidence FROM estimate_reviews "
                "WHERE bryan_est_hours IS NOT NULL"
            ):
                verdicts[tid] = conf or "OK"
            c.close()
        except sqlite3.DatabaseError:
            continue

    rows = []
    for r in conn.execute(f"""
        SELECT t.task_id, t.project, t.first_ts, t.last_ts, t.user_msg_count,
               t.event_count, {deleg} AS delegated,
               COALESCE(e.title, t.title) AS title, e.est_human_hours, e.uncertainty,
               e.scope, e.depth, e.type, e.status,
               m.active_hours, m.agent_active_hours, m.agent_busy_hours,
               {mc['wall']} AS wall_clock_hours, {mc['labor']} AS labor_hours, m.resumptions,
               {blocks} AS hands_on_blocks, {chars} AS human_input_chars,
               {model} AS dominant_model, {mc['lane']} AS agent_lane_hours
        FROM tasks t
        LEFT JOIN enriched e ON e.task_id = t.task_id
        LEFT JOIN task_metrics m ON m.task_id = t.task_id
        ORDER BY t.first_ts
    """):
        tid = r["task_id"]
        llm_est = r["est_human_hours"]
        est = llm_est
        review_status, verdict = "unreviewed", ""
        if tid in overrides:
            hours, base_ec = overrides[tid]
            if carry_forward_ok(base_ec, r["event_count"]):
                est = hours
                review_status, verdict = "spot-checked", verdicts.get(tid, "OK")
        wall = r["wall_clock_hours"]  # calendar; None on runs before schema v2
        ho = r["active_hours"] or 0.0
        rows.append({
            "task_id": tid, "run_id": run_id, "project": r["project"],
            "client": projects.client_for(r["project"]) or "",
            "billable": next((p.billable for p in projects.projects if p.slug == r["project"]), True),
            "excluded": projects.is_excluded(r["project"]),
            "delegated": r["delegated"],
            "week": week_of(r["first_ts"]) if r["first_ts"] else "",
            "first_ts": r["first_ts"], "last_ts": r["last_ts"],
            "title": r["title"], "type": r["type"] or "", "status": r["status"] or "",
            "uncertainty": r["uncertainty"] or "", "scope": r["scope"] or "",
            "depth": r["depth"] or "",
            "est_human_hours": est, "llm_est_hours": llm_est,
            "review_status": review_status, "review_verdict": verdict,
            "hands_on_hours": round(ho, 4),
            "agent_active_hours": round(r["agent_active_hours"] or 0.0, 4),
            "agent_busy_hours": round(r["agent_busy_hours"] or 0.0, 4),
            "agent_lane_hours": (
                None if r["agent_lane_hours"] is None
                else round(r["agent_lane_hours"], 4)
            ),
            "labor_hours": None if r["labor_hours"] is None else round(r["labor_hours"], 4),
            "wall_clock_hours": None if wall is None else round(wall, 4),
            # (est_h / 8) ÷ (wall / 24), method Step 7
            "wall_clock_speedup": (
                None if speedup(est or 0.0, wall or 0.0) is None
                else round(speedup(est, wall), 2)
            ),
            "hands_on_leverage": round(est / ho, 2) if est and ho >= HANDS_ON_FLOOR_H else None,
            "est_size_bucket": size_bucket(est),
            "resumptions": r["resumptions"] or 0,
            "user_msg_count": r["user_msg_count"] or 0,
            "event_count": r["event_count"] or 0,
            "hands_on_blocks": r["hands_on_blocks"],
            "human_input_chars": r["human_input_chars"],
            "dominant_model": r["dominant_model"] or "",
        })
    return rows



def _existing_weeks(csv_path: Path) -> set[str]:
    """Weeks already represented in the BI table, if it exists."""
    if not csv_path.exists():
        return set()
    try:
        with open(csv_path, newline="") as f:
            return {r["week"] for r in csv.DictReader(f) if r.get("week")}
    except (OSError, csv.Error, KeyError):
        return set()


class ExportWouldTruncate(RuntimeError):
    """Raised when an export would drop weeks already present in the BI table."""


def export_task_facts(
    run_id: str | None = None,
    out_dir: Path | None = None,
    allow_shrink: bool = False,
) -> tuple[Path, Path, int]:
    """Build and write task_facts.csv + task_facts.sqlite. Returns (csv, sqlite, n).

    The BI table is rebuilt from a SINGLE run DB, so exporting a `--since`-limited
    run would replace full history with just that window. That is exactly how the
    pre-June-28 data was destroyed on 2026-07-12: a two-week verification run
    overwrote a 13-week export.

    So: refuse to drop weeks that are already in the table. A narrow run leaves
    the existing (wider) export untouched and says so, rather than silently
    truncating it. Pass allow_shrink=True to overwrite deliberately.
    """
    rid = run_id or latest_run_id()
    rows = build_rows(rid)
    out = Path(out_dir or DEFAULT_OUT).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    csv_path = out / "task_facts.csv"
    sq_path = out / "task_facts.sqlite"

    had = _existing_weeks(csv_path)
    now = {r["week"] for r in rows if r.get("week")}
    lost = had - now
    if lost and not allow_shrink:
        raise ExportWouldTruncate(
            f"run {rid} covers {len(now)} week(s) but the existing export has "
            f"{len(had)}; it would drop {len(lost)} week(s) "
            f"({min(lost)}..{max(lost)}). Existing export left untouched. "
            f"Re-export from a full-history run, or pass allow_shrink=True."
        )

    # Write via temp files + atomic rename: a crash mid-write must not leave a
    # half-written BI table behind.
    tmp_csv = csv_path.with_suffix(".csv.tmp")
    with open(tmp_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    tmp_csv.replace(csv_path)

    tmp_sq = sq_path.with_suffix(".sqlite.tmp")
    tmp_sq.unlink(missing_ok=True)
    sq = sqlite3.connect(str(tmp_sq))
    coldefs = ", ".join(f'"{c}"' for c in COLUMNS)
    sq.execute(f"CREATE TABLE task_facts ({coldefs})")
    sq.executemany(
        f"INSERT INTO task_facts VALUES ({','.join('?' * len(COLUMNS))})",
        [[r[c] for c in COLUMNS] for r in rows],
    )
    sq.commit()
    sq.close()
    tmp_sq.replace(sq_path)
    return csv_path, sq_path, len(rows)
