"""Render one week as a single review page: hands-on by project at the top,
then per project at most 10 items on a timeline with output hours each.

Method Step 7, the page (a board task, 2026-09-17). An item is a workspace
goal; units linked to no goal are one "no goal" item per project. When a
project has more than nine goals, the smallest by output fold into one "other
goals" item so the project never shows more than ten items. An item opens to
its priced units only when clicked. The user's two questions are the page's two
comment targets: does the hands-on split by project feel right, and does the
output per item look okay.

The output carries goal and board-task titles and unit titles from the run db:
write it under a gitignored path and serve it on his private board only.

Usage:
  uv run python3 scripts/make_week_page.py --run-id <run> --out docs/data/<file>.html
"""
from __future__ import annotations

import argparse
import html
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone

LOCAL_TZ = datetime.now().astimezone().tzinfo   # the page is read on the machine that ran it
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA_DIR = Path.home() / ".local/share/weekly-review"
MAX_ITEMS = 10          # per project, counting the "no goal" item
MAX_GOALS = MAX_ITEMS - 1
EXPERT_HOURS_PER_WEEK = 56  # 7 days × 8 expert hours (Step 7)


def load_units(conn: sqlite3.Connection) -> list[dict]:
    from weekly_review.persist import metric_columns
    mc = metric_columns(conn)
    rows = conn.execute(f"""
        SELECT t.task_id, t.project, COALESCE(e.title, t.title) AS title,
               t.unit_kind, t.board_task, t.first_ts, t.last_ts, t.user_msg_count,
               COALESCE(e.est_human_hours, 0) AS est,
               COALESCE(m.active_hours, 0) AS hands_on,
               COALESCE({mc['labor']}, 0) AS labor
        FROM tasks t
        LEFT JOIN enriched e ON e.task_id = t.task_id
        LEFT JOIN task_metrics m ON m.task_id = t.task_id
        ORDER BY t.first_ts
    """).fetchall()
    units = [dict(r) for r in rows]
    comps = load_components(conn)
    for u in units:
        u["components"] = comps.get(u["task_id"], dict(COMPONENT_ZERO))
    return units


COMPONENT_ZERO = {"prs": 0, "loc": 0, "data_loc": 0, "commits": 0, "docs_read": 0, "comments": 0, "messages": 0}
COMPONENT_LABELS = [("prs", "PR", "PRs"), ("loc", "line", "lines"),
                    ("data_loc", "data line (not priced)", "data lines (not priced)"),
                    ("commits", "commit", "commits"),
                    ("docs_read", "doc read", "docs read"), ("comments", "comment", "comments"),
                    ("messages", "message", "messages")]


def load_components(conn: sqlite3.Connection) -> dict[str, dict]:
    """Per task: merged PRs, lines changed, commits, docs read, his comments and
    messages. Lines come from the task's merged PRs; a task with commits and no
    merged PR counts its commits' lines instead, so a squash-merged change is
    not counted twice."""
    out: dict[str, dict] = defaultdict(lambda: dict(COMPONENT_ZERO))
    prs: dict[str, dict[str, int]] = defaultdict(dict)       # task → pr identity → lines
    commit_lines: dict[str, int] = defaultdict(int)
    docs: dict[str, set] = defaultdict(set)
    rows = conn.execute("""
        SELECT te.task_id, e.kind, e.source_system, e.actor, e.metadata_json
        FROM task_events te JOIN events e ON e.event_id = te.event_id
        WHERE e.kind IN ('pr_merged', 'commit', 'review_session', 'user_message')
    """)
    for task_id, kind, source, actor, md_json in rows:
        md = json.loads(md_json or "{}")
        c = out[task_id]
        if kind == "pr_merged":
            key = f"{md.get('repo', '')}#{md.get('number', '')}"
            prs[task_id][key] = int(md.get("additions") or 0) + int(md.get("deletions") or 0)
        elif kind == "commit":
            c["commits"] += 1
            commit_lines[task_id] += int(md.get("insertions") or 0) + int(md.get("deletions") or 0)
            # checked-in data the project's diffstat_exclude took out of pricing;
            # shown apart so a data commit does not read as written code
            c["data_loc"] += int(md.get("excluded_insertions") or 0)
        elif kind == "review_session":
            if md.get("doc_id"):
                docs[task_id].add(md["doc_id"])
        elif kind == "user_message" and actor == "user":
            c["comments" if source == "live_feedback" else "messages"] += 1
    for task_id, c in out.items():
        c["prs"] = len(prs[task_id])
        c["loc"] = sum(prs[task_id].values()) if prs[task_id] else commit_lines[task_id]
        c["docs_read"] = len(docs[task_id])
    return dict(out)


def top_components(comp: dict, n: int = 3) -> str:
    """The n largest nonzero components as text, in the label order: '4 PRs · 1,210 lines · 3 docs read'."""
    parts = [(comp.get(k, 0), one, many) for k, one, many in COMPONENT_LABELS if comp.get(k, 0)
             and not (k == "commits" and comp.get("prs", 0))]   # squash commits duplicate the PRs
    parts = sorted(parts, key=lambda t: -t[0])[:n]
    return " · ".join(f"{v:,} {one if v == 1 else many}" for v, one, many in parts)


def load_board_goal_ids(conn: sqlite3.Connection) -> dict[str, str]:
    return {r[0]: (r[1] or "") for r in conn.execute("SELECT board_task, goal FROM board_tasks")}


def load_review_items(conn: sqlite3.Connection) -> dict[str, set[str]]:
    """Per board task: the review items he answered or asked a question on in
    the window. The board log carries no creation event for a review item, so
    the count is items he acted on, not items filed."""
    out: dict[str, set[str]] = defaultdict(set)
    rows = conn.execute("SELECT metadata_json FROM events WHERE kind = 'board_action'")
    for (md_json,) in rows:
        md = json.loads(md_json or "{}")
        if md.get("actor_kind") == "person" and md.get("review_item_id") and md.get("task_id"):
            out[md["task_id"]].add(md["review_item_id"])
    return dict(out)


def board_titles(board_tasks=None) -> dict[str, tuple[str, str, str]]:
    """board task id → (task title, goal title, board name) from the store."""
    if board_tasks is None:
        from weekly_review.message_classifier import load_board_tasks
        board_tasks = load_board_tasks()
    return {bt.id: (bt.title, bt.goal_title, bt.board) for bt in board_tasks}


def board_task_urls(board_tasks) -> dict[str, str]:
    """board task id → its page on the board, relative so it resolves on
    whichever host serves the page."""
    return {bt.id: f"/workspaces/{bt.workspace_id}?task={bt.id}" for bt in board_tasks if bt.workspace_id}


def _item(kind: str, key: str, title: str, board: str = "") -> dict:
    return {"kind": kind, "key": key, "title": title, "board": board, "est": 0.0,
            "hands_on": 0.0, "labor": 0.0, "units": [], "board_tasks": set(),
            "components": dict(COMPONENT_ZERO), "first_ts": None, "last_ts": None,
            "his_first_ts": None, "his_last_ts": None}


def _add(item: dict, u: dict) -> None:
    item["est"] += u["est"]
    item["hands_on"] += u["hands_on"]
    item["labor"] += u["labor"]
    item["units"].append(u)
    for k, v in u.get("components", {}).items():
        item["components"][k] = item["components"].get(k, 0) + v
    if u["board_task"]:
        item["board_tasks"].add(u["board_task"])
    for k in ("first_ts", "last_ts"):
        if u[k] and (item[k] is None or (u[k] < item[k] if k == "first_ts" else u[k] > item[k])):
            item[k] = u[k]
    if u["hands_on"] > 0:
        # the span of units he touched, drawn inside the item's span so agent-only
        # stretches read as agent-only
        for k in ("first_ts", "last_ts"):
            hk = "his_" + k
            if u[k] and (item[hk] is None or (u[k] < item[hk] if k == "first_ts" else u[k] > item[hk])):
                item[hk] = u[k]


def project_items(units: list[dict], goal_of: dict[str, str],
                  titles: dict[str, tuple[str, str, str]]) -> list[dict]:
    """At most MAX_ITEMS items: goals by output desc, the smallest folded into
    "other goals" past MAX_GOALS, then one "no goal" item."""
    by_goal: dict[str, dict] = {}
    nogoal = _item("nogoal", "nogoal", "no goal")
    for u in units:
        bt = u["board_task"] or ""
        gid = goal_of.get(bt, "") if bt else ""
        if not gid:
            _add(nogoal, u)
            continue
        if gid not in by_goal:
            gtitle = titles.get(bt, ("", "", ""))[1] or gid
            by_goal[gid] = _item("goal", gid, gtitle, titles.get(bt, ("", "", ""))[2])
        _add(by_goal[gid], u)
    goals = sorted(by_goal.values(), key=lambda i: -i["est"])
    if len(goals) > MAX_GOALS:
        keep, rest = goals[:MAX_GOALS - 1], goals[MAX_GOALS - 1:]
        other = _item("other", "other", f"other goals ({len(rest)})")
        for g in rest:
            for u in g["units"]:
                _add(other, u)
            other["board_tasks"] |= g["board_tasks"]
        other["folded"] = [{"title": g["title"], "est": g["est"]} for g in rest]
        goals = keep + [other]
    items = goals + ([nogoal] if nogoal["units"] else [])
    assert len(items) <= MAX_ITEMS
    return items


def build_model(run_dir: Path) -> dict:
    conn = sqlite3.connect(str(run_dir / "run.sqlite"))
    conn.row_factory = sqlite3.Row
    start, end = conn.execute("SELECT period_start, period_end FROM run_meta").fetchone()
    units = load_units(conn)
    goal_of = load_board_goal_ids(conn)
    reviews = load_review_items(conn)
    conn.close()
    from weekly_review.message_classifier import load_board_tasks
    board_tasks = load_board_tasks()
    titles = board_titles(board_tasks)
    urls = board_task_urls(board_tasks)

    by_project: dict[str, list[dict]] = defaultdict(list)
    for u in units:
        by_project[u["project"]].append(u)

    projects = []
    for name, us in by_project.items():
        est = sum(u["est"] for u in us)
        ho = sum(u["hands_on"] for u in us)
        coord = sum(u["est"] for u in us if u["unit_kind"] == "coordination")
        items = project_items(us, goal_of, titles)
        projects.append({
            "name": name, "est": est, "hands_on": ho, "labor": sum(u["labor"] for u in us),
            "units": len(us), "coord_est": coord,
            "items": [{**i, "board_tasks": len(i["board_tasks"]),
                       "reviews": len(set().union(*(reviews.get(bt, set()) for bt in i["board_tasks"]))),
                       "units": sorted(({**u, "board_title": titles.get(u["board_task"] or "", ("", "", ""))[0],
                                         "board_url": urls.get(u["board_task"] or "", "")}
                                        for u in i["units"]), key=lambda u: -u["est"])}
                      for i in items],
        })
    projects.sort(key=lambda p: -p["hands_on"])
    timeline = build_timeline(run_dir, [p["name"] for p in projects])
    total_est = sum(p["est"] for p in projects)
    total_ho = sum(p["hands_on"] for p in projects)
    days = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days + 1
    return {
        "run_id": run_dir.name, "start": start, "end": end, "days": days,
        "total_est": total_est, "total_hands_on": total_ho,
        "total_coord": sum(p["coord_est"] for p in projects),
        "multiplier": (total_est / total_ho) if total_ho else None,
        "week_speedup": total_est / (8 * days) if days else None,
        "units": len(units), "projects": projects, "timeline": timeline,
    }


# Categorical colours for the day chart, one per project in order of hands-on.
# Projects past the palette share one grey. There is no legend: the project
# table carries each colour as a dot beside the name, and every block names its
# projects on hover.
PALETTE = ["#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#b07aa1", "#76b7b2", "#edc948", "#ff9da7", "#9c755f"]
OTHER_COLOR = "#bab0ac"


def build_timeline(run_dir: Path, table_order: list[str]) -> dict:
    """Hands-on by time of day over the whole week (the user's ask, 2026-09-24):
    the whole-week presence intervals, labelled by project, split at work hours."""
    from weekly_review.day_timeline import BLOCK_GAP_SEC, flatten, hours_split, labeled_presence_intervals
    from weekly_review.rundb import load_run
    events, _tasks = load_run(run_dir / "run.sqlite")
    pieces = flatten(labeled_presence_intervals(events))
    per_project: dict[str, float] = defaultdict(float)
    for s, e, ps in pieces:
        for p in ps:
            per_project[p] += (e - s).total_seconds() / 3600 / len(ps)
    # colours follow the project table's order, so its dots run down the palette
    ranked = [p for p in table_order if per_project.get(p, 0) > 0]
    ranked += [p for p, _ in sorted(per_project.items(), key=lambda kv: -kv[1]) if p not in ranked]
    colors = {p: PALETTE[i] for i, p in enumerate(ranked[:len(PALETTE)])}
    return {"pieces": pieces, "split": hours_split(pieces, LOCAL_TZ), "colors": colors, "present": set(per_project),
            "multi_h": sum((e - s).total_seconds() for s, e, ps in pieces if len(ps) > 1) / 3600,
            "gap_min": BLOCK_GAP_SEC / 60}


DEFINITIONS = {
    "hands_on": "Time you were at the keyboard or reading, per project. Transcript events within 10 minutes of each other "
                "form one block; workspace events within 60 seconds. Blocks are unioned, never added, so time on two "
                "things at once counts once.",
    "output": "Hours a median expert would need to produce what each unit shipped, estimated per unit from its work log "
              "and artifacts, then summed. Rework is excluded. Your review verdicts override the estimate. "
              "Shown in hours, 8-hour days or 5-day weeks, whichever fits the size.",
    "multiplier": "Output ÷ hands-on. Output over the time you spent steering, pooled across the week. It is not a "
                  "per-task speed; most output sits on delegated units you barely touched.",
    "speedup": "Output ÷ 56h, where 56h is one expert working 7 days × 8 hours. How many expert-weeks of output the "
               "calendar week produced.",
}

TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Week __START__</title>
<style>
:root{--bg:#fafaf8;--fg:#1c1c1c;--muted:#6b6b6b;--line:#e2e0da;--bar:#3b6cff;--bar2:#b9c7f5;--card:#fff;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;font-size:14px;color:var(--fg);background:var(--bg)}
*{box-sizing:border-box}body{margin:0;padding:0 16px 120px}
h1{font-size:18px;margin:12px 0 2px}h2{font-size:15px;margin:22px 0 6px}
.muted{color:var(--muted)}.num{font-variant-numeric:tabular-nums}
.top{display:flex;gap:28px;flex-wrap:wrap;margin:8px 0 14px}
.top .k{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
.top .v{font-size:20px}
.info{position:relative;display:inline-block;width:14px;height:14px;line-height:14px;border-radius:50%;border:1px solid var(--muted);color:var(--muted);font-size:10px;text-align:center;text-transform:none;letter-spacing:0;cursor:help;vertical-align:1px;font-style:italic;font-family:Georgia,serif}
.info .pop{display:none;position:absolute;left:0;top:18px;z-index:5;width:280px;padding:8px 10px;background:#fff;border:1px solid var(--line);border-radius:6px;box-shadow:0 2px 8px rgba(0,0,0,.08);color:var(--fg);font:13px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;text-transform:none;letter-spacing:0;text-align:left;white-space:normal}
.info:hover .pop,.info:focus .pop{display:block}
table{border-collapse:collapse;font-size:13px}td,th{padding:3px 12px 3px 0;text-align:left;vertical-align:middle}th{color:var(--muted);font-weight:normal;font-size:12px}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.hbar{display:inline-block;height:10px;background:var(--bar);border-radius:2px;vertical-align:middle;margin-right:6px}
.xb{position:relative;display:inline-block;width:120px;height:12px;vertical-align:middle;margin-right:6px}
.xb .fill{position:absolute;left:0;top:1px;height:10px;background:var(--bar);border-radius:2px}
.xb .ref{position:absolute;top:-2px;width:1px;height:16px;background:#c0392b}
.proj{position:relative;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 14px;margin:10px 0}
.proj .hd{display:flex;gap:18px;flex-wrap:wrap;align-items:baseline;margin-bottom:6px}
.proj .hd b{font-size:15px}
.days{display:grid;grid-template-columns:minmax(260px,50%) 1fr}
.days .cols{display:grid;grid-template-columns:repeat(__DAYS__,1fr);font-size:11px;color:var(--muted);border-bottom:1px solid var(--line)}
.days .cols span{padding:2px 4px;border-left:1px solid var(--line)}
details.item summary{display:grid;grid-template-columns:minmax(260px,50%) 1fr;cursor:pointer;list-style:none}
details.item summary::-webkit-details-marker{display:none}
.item .lab{padding:4px 8px 4px 0;border-bottom:1px solid #f0eee8;font-size:13px;line-height:1.3;overflow:hidden}
.item .lab .t{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.item .lab small{display:block;color:var(--muted);font-size:11.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.item .row{position:relative;border-bottom:1px solid #f0eee8;height:38px;background:repeating-linear-gradient(to right,var(--line) 0 1px,transparent 1px calc(100%/__DAYS__))}
.item .bar{position:absolute;top:11px;height:16px;background:var(--bar2);border:1px solid var(--bar);border-radius:3px;font-size:11px;line-height:14px;padding:0 4px;white-space:nowrap;overflow:visible;font-variant-numeric:tabular-nums}
.item.nogoal .bar{background:#eee;border-color:#999}
.item .his{position:absolute;top:30px;height:4px;background:var(--bar);border-radius:2px}
.item summary:hover .lab,.item summary:hover .row{background-color:#f4f2ec}
.list,.units{display:flex;flex-direction:column}
.units{padding:0 0 8px 0;border-bottom:1px solid var(--line);background:#fcfbf8}
.urow{display:grid;grid-template-columns:minmax(260px,50%) 1fr}
.urow .lab{padding-left:18px;font-size:12.5px;border-bottom:1px solid #f4f2ec}
.urow .lab a{color:inherit;text-decoration:none;border-bottom:1px dotted var(--muted)}
.urow .row{height:34px;border-bottom:1px solid #f4f2ec}
.urow .bar{top:9px;height:15px;font-size:10.5px;line-height:13px;background:#e9edfb}
.urow .his{top:27px}
.units .k{color:var(--muted);font-size:11px;padding:4px 0 2px 18px}
.proj input.sort{position:absolute;opacity:0;width:0;height:0;pointer-events:none}
.sw{margin-left:auto;font-size:11.5px;color:var(--muted)}
.sw label{cursor:pointer;padding:1px 8px;border:1px solid var(--line);border-radius:10px;margin-left:2px}
.proj input.so:checked ~ .hd label.lo,.proj input.sh:checked ~ .hd label.lh{background:var(--bar);border-color:var(--bar);color:#fff}
.proj input.so:checked ~ .list .item,.proj input.so:checked ~ .list .urow{order:var(--o)}
.proj input.sh:checked ~ .list .item,.proj input.sh:checked ~ .list .urow{order:var(--h)}
.proj input.so:checked ~ .list .vh,.proj input.sh:checked ~ .list .vo{display:none}
tr.total td{border-top:1px solid var(--line);font-weight:600}
.dc{max-height:400px;overflow:hidden;margin:4px 0 8px}
.dc p{margin:0 0 6px;font-size:12.5px;line-height:1.45;max-width:900px}
.dc svg{display:block;width:100%;max-width:1100px;height:auto;max-height:300px}
.dot{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px;vertical-align:0}
code{background:#f1efe9;padding:1px 4px;border-radius:3px;font-size:12px}
.foot{margin-top:24px;font-size:12px;color:var(--muted);line-height:1.5}
</style></head><body>
<h1>Week of __START__ to __END__</h1>
<div class="muted">Run <code>__RUN__</code> · click an item to open it</div>
<div class="muted">__WINDOW__</div>
<div class="top">__TOP__</div>
<h2>Hands-on by project</h2>
<table id="ptable">__PTABLE__</table>
<h2>Hands-on by time of day</h2>
<div class="dc" id="daychart">__DAYCHART__</div>
<h2>Output by goal, per project</h2>
<div id="projects">__PROJECTS__</div>
<div class="foot">
<b>How to read this.</b> Hands-on is the time you were at the keyboard or reading, per project (transcripts at 10-minute presence, workspace at 60-second blocks). Output is the estimator's hours an expert would need to produce what each unit shipped, summed per goal. Productivity multiplier is output ÷ hands-on. Coordination (a lead's day of dispatching, reviewing and merging, priced as that lead's time) is inside output. Wall clock speedup is output ÷ 56h (7 days × 8 expert hours). A bar runs from an item's first event to its last inside the week, agent or yours; the thin line under it is the span of the units you touched. Review items are the ones you answered or asked a question on during the week. Each project sorts by output or by hands-on; the bars show the chosen figure. Every time figure uses one unit, picked by size: minutes under an hour, hours under 7 days, 8-hour days under 4 weeks, 5-day weeks from there.<br>
<b>Two questions.</b> Does the hands-on split by project feel about right? Does the output per item look okay? Comment on the project row or the item.
</div>
</body></html>
"""


def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


EXPERT_HOURS_PER_DAY = 8      # one FTE day; weeks are 5 of them (the reviewer, 2026-09-18)
EXPERT_DAYS_PER_WEEK = 5


def _dur(x) -> str:
    """One unit per figure, picked by size: minutes under an hour, hours under
    7 days (56h), 8-hour days under 4 weeks (160h), 5-day weeks from there
    (the reviewer, 2026-09-18: hours and days read clearer for longer).
    '45m', '6.0h', '24.3d', '30.1w'."""
    if x is None:
        return "n/a"
    if 0 < x < 1:
        return f"{round(x * 60)}m"
    days = x / EXPERT_HOURS_PER_DAY
    if days < 7:
        return f"{x:.1f}h"
    if days < 4 * EXPERT_DAYS_PER_WEEK:
        return f"{days:.1f}d"
    return f"{days / EXPERT_DAYS_PER_WEEK:.1f}w"


_h = _H = _Hd = _dur


def _X(x) -> str:
    return "n/a" if x is None else f"{x:.1f}x"


def _pct(ts: str | None, t0: datetime, span_s: float) -> float:
    if not ts:
        return 0.0
    t = datetime.fromisoformat(ts)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return max(0.0, min(100.0, (t - t0).total_seconds() / span_s * 100))


RATIO_BAR_PX = 120


def _ratio_cell(x: float, max_x: float) -> str:
    """A bar for a ratio with a tick at 1x, so a project under 1x reads as under."""
    scale = RATIO_BAR_PX / max(max_x, 1.0)
    return (f'<span class="xb"><span class="fill" style="width:{round(x * scale)}px"></span>'
            f'<span class="ref" style="left:{round(scale)}px"></span></span>{_X(x)}')


def render_html(model: dict) -> str:
    """Static HTML: the viewer that serves mockups does not run scripts, so every
    table and bar is in the markup. Items open with <details>, which needs no JS."""
    m = model
    t0 = datetime.fromisoformat(m["start"]).replace(tzinfo=timezone.utc)
    span_s = m["days"] * 86400
    t0_local = t0.astimezone(LOCAL_TZ)
    t1_local = (t0 + timedelta(days=m["days"])).astimezone(LOCAL_TZ)
    tzname = t0_local.tzname() or "local"
    window_note = (f"Timeline in {tzname}. The run's week is {m['days']} UTC days, which here runs from "
                   f"{t0_local.strftime('%a %-d %b %-I:%M %p').lower()} to {t1_local.strftime('%a %-d %b %-I:%M %p').lower()}; "
                   f"each column is one 24h day from that start.")
    top = [("Hands-on", _H(m["total_hands_on"]), DEFINITIONS["hands_on"]),
           ("Estimated Output", _Hd(m["total_est"]), DEFINITIONS["output"]),
           ("Productivity Multiplier", _X(m["multiplier"]), DEFINITIONS["multiplier"]),
           ("Wall Clock Speedup", _X(m["week_speedup"]), DEFINITIONS["speedup"])]
    top_html = "".join(
        f'<div><div class="k">{k} <span class="info" tabindex="0">i<span class="pop">{_esc(d)}</span></span></div>'
        f'<div class="v num">{v}</div></div>' for k, v, d in top)

    max_ho = max([p["hands_on"] for p in m["projects"]] + [0.01])
    max_x = max([p["est"] / p["hands_on"] for p in m["projects"] if p["hands_on"] >= 0.05] + [1.0])
    max_sp = max([p["est"] / EXPERT_HOURS_PER_WEEK for p in m["projects"]] + [1.0])
    rows = ['<tr><th>project</th><th></th><th class="n">hands-on</th><th class="n">share</th>'
            '<th class="n">estimated output</th><th>productivity multiplier</th><th>wall clock speedup</th></tr>']
    for p in m["projects"]:
        share = f"{round(p['hands_on'] / m['total_hands_on'] * 100)}%" if m["total_hands_on"] else "—"
        mult = _ratio_cell(p["est"] / p["hands_on"], max_x) if p["hands_on"] >= 0.05 else "—"
        speed = _ratio_cell(p["est"] / EXPERT_HOURS_PER_WEEK, max_sp)
        name = _esc(p["name"])
        tl = m.get("timeline") or {}
        col = tl.get("colors", {}).get(p["name"]) or (OTHER_COLOR if p["name"] in tl.get("present", ()) else None)
        dot = f'<span class="dot" style="background:{col}"></span>' if col else '<span class="dot"></span>'
        rows.append(f'<tr data-project="{name}"><td>{dot}<a href="#p-{name}">{name}</a></td>'
                    f'<td><span class="hbar" style="width:{round(p["hands_on"] / max_ho * 180)}px"></span></td>'
                    f'<td class="n">{_h(p["hands_on"])}</td><td class="n">{share}</td><td class="n">{_Hd(p["est"])}</td>'
                    f'<td class="num">{mult}</td><td class="num">{speed}</td></tr>')
    rows.append(f'<tr class="total"><td>total</td><td></td><td class="n">{_h(m["total_hands_on"])}</td>'
                f'<td class="n">{"100%" if m["total_hands_on"] else "—"}</td><td class="n">{_Hd(m["total_est"])}</td>'
                f'<td class="num">{_X(m["multiplier"])}</td><td class="num">{_X(m["week_speedup"])}</td></tr>')

    days = "".join(f"<span>{(t0_local + timedelta(days=i)).strftime('%a %-d %-I%p').lower()}</span>"
                   for i in range(m["days"]))
    def span(first_ts, last_ts, his_first_ts=None, his_last_ts=None, min_w=1.5) -> tuple[float, float, str]:
        left = _pct(first_ts, t0, span_s)
        right = max(_pct(last_ts, t0, span_s), left + min_w)
        his = ""
        if his_first_ts:
            hl = _pct(his_first_ts, t0, span_s)
            hr = max(_pct(his_last_ts, t0, span_s), hl + 0.6)
            his = f'<div class="his" style="left:{hl:.1f}%;width:{hr - hl:.1f}%" title="span with your time"></div>'
        return left, right, his

    def bar(left, right, est, hands_on) -> str:
        # both figures in the markup; the project's sort toggle shows one
        return (f'<div class="bar" style="left:{left:.1f}%;width:{right - left:.1f}%">'
                f'<span class="vo">{_Hd(est)}</span><span class="vh">{_h(hands_on)}</span></div>')

    secs = []
    for p in m["projects"]:
        n = len(p["items"])
        name = _esc(p["name"])
        mult = f'{_X(p["est"] / p["hands_on"])} multiplier' if p["hands_on"] >= 0.05 else ""
        sid = f"sort-{name}"
        out = [f'<section class="proj" id="p-{name}" data-project="{name}">'
               f'<input class="sort so" type="radio" name="{sid}" id="{sid}-o" checked>'
               f'<input class="sort sh" type="radio" name="{sid}" id="{sid}-h">'
               f'<div class="hd"><b>{name}</b><span class="num">hands-on {_h(p["hands_on"])}</span>'
               f'<span class="num">output {_Hd(p["est"])}</span><span class="num">{mult}</span>'
               f'<span class="muted">{n} item{"" if n == 1 else "s"}</span>'
               f'<span class="sw">sort by <label class="lo" for="{sid}-o">output</label>'
               f'<label class="lh" for="{sid}-h">hands-on</label></span></div>',
               f'<div class="days"><div></div><div class="cols">{days}</div></div><div class="list">']
        by_ho = {id(it): r for r, it in enumerate(sorted(p["items"], key=lambda i: (-i["hands_on"], -i["est"])))}
        for o_rank, it in enumerate(p["items"]):
            nu = len(it["units"])
            units_s = f'{nu} unit{"" if nu == 1 else "s"}'
            comps = top_components(it.get("components", {}))
            sub = (f'{it["board_tasks"]} board task{"" if it["board_tasks"] == 1 else "s"}'
                   if it["kind"] == "goal" else units_s)
            if it.get("reviews"):
                sub += f' · {it["reviews"]} review item{"" if it["reviews"] == 1 else "s"}'
            if comps:
                sub += " · " + comps
            left, right, his = span(it["first_ts"], it["last_ts"], it.get("his_first_ts"), it.get("his_last_ts"))
            folded = ""
            if it.get("folded"):
                folded = ('<div class="k">folded goals: '
                          + " · ".join(f'{_esc(f["title"])} {_H(f["est"])}' for f in it["folded"]) + "</div>")
            urows = []
            u_by_ho = {id(u): r for r, u in enumerate(sorted(it["units"], key=lambda u: (-u["hands_on"], -u["est"])))}
            for uo, u in enumerate(it["units"]):
                # the unit row reads like its parent: board task (linked) on the
                # left, what the work was under it, its span and figure on the right
                if u["board_task"]:
                    head = _esc(u["board_title"] or u["title"])
                    if u.get("board_url"):
                        # _top: the page is shown inside a board frame, and a board
                        # page loaded into that frame renders as an error page
                        head = f'<a href="{_esc(u["board_url"])}" target="_top">{head}</a>'
                    detail = [_esc(u["title"])] if u["board_title"] and u["title"] != u["board_title"] else []
                else:
                    head, detail = _esc(u["title"]), []
                if u["unit_kind"] and u["unit_kind"] != "conversation":
                    detail.append(_esc(u["unit_kind"]))
                uc = top_components(u.get("components", {}))
                if uc:
                    detail.append(uc)
                ul, ur, uhis = span(u["first_ts"], u["last_ts"],
                                    u["first_ts"] if u["hands_on"] > 0 else None, u["last_ts"], min_w=1.0)
                urows.append(
                    f'<div class="urow" style="--o:{uo};--h:{u_by_ho[id(u)]}" data-task-id="{_esc(u["task_id"])}">'
                    f'<div class="lab" title="{_esc(u["title"])}"><span class="t">{head}</span><small>{" · ".join(detail)}</small></div>'
                    f'<div class="row">{bar(ul, ur, u["est"], u["hands_on"])}{uhis}</div></div>')
            title = _esc(it["title"])
            out.append(
                f'<details class="item {it["kind"]}" style="--o:{o_rank};--h:{by_ho[id(it)]}" data-item="{name}-{_esc(it["key"])}">'
                f'<summary><div class="lab" title="{title}"><span class="t">{title}</span><small>{sub}</small></div>'
                f'<div class="row">{bar(left, right, it["est"], it["hands_on"])}{his}</div></summary>'
                f'<div class="units">{folded}{"".join(urows)}</div></details>')
        out.append("</div></section>")
        secs.append("".join(out))

    return (TEMPLATE
            .replace("__TOP__", top_html)
            .replace("__PTABLE__", "".join(rows))
            .replace("__PROJECTS__", "".join(secs))
            .replace("__DAYCHART__", render_timeline(m["timeline"], t0, t0 + timedelta(days=m["days"]))
                     if m.get("timeline") else "")
            .replace("__WINDOW__", _esc(window_note))
            .replace("__RUN__", _esc(m["run_id"]))
            .replace("__START__", t0_local.strftime("%a %-d %b").lower())
            .replace("__END__", t1_local.strftime("%a %-d %b").lower())
            .replace("__DAYS__", str(m["days"]))
            .replace("__UNITS__", str(m["units"])))


def _clock(t: datetime) -> str:
    return t.strftime("%-I:%M%p").lower().replace(":00", "")


def render_timeline(tl: dict, w0: datetime, w1: datetime) -> str:
    """One row per local day, 24 hours wide, weekday 9am-6pm shaded. A block on
    one project is that project's colour; a block on several is striped, one
    band per project. Inline SVG, because the viewer runs no scripts."""
    from weekly_review.day_timeline import WORK_END_H, WORK_START_H
    pieces, split, colors = tl["pieces"], tl["split"], tl["colors"]
    if not pieces:
        return '<p class="muted">No hands-on time in this run.</p>'
    L, C, R, ROW, GAP, TOP = 70, 760, 170, 26, 6, 16
    W = L + C + R
    first = min(s for s, _, _ in pieces).astimezone(LOCAL_TZ).date()
    last = max(e for _, e, _ in pieces).astimezone(LOCAL_TZ).date()
    ndays = (last - first).days + 1
    H = TOP + ndays * (ROW + GAP)
    order = {p: i for i, p in enumerate(colors)}
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="hands-on by time of day">']
    for hr, lab in ((0, "12am"), (6, "6am"), (9, "9am"), (12, "noon"), (18, "6pm"), (24, "12am")):
        x = L + C * hr / 24
        out.append(f'<text x="{x:.1f}" y="11" font-size="10" fill="#6b6b6b" text-anchor="middle">{lab}</text>')
    for d in range(ndays):
        date = first + timedelta(days=d)
        day0 = datetime(date.year, date.month, date.day, tzinfo=LOCAL_TZ)
        day1 = datetime(date.year, date.month, date.day, tzinfo=LOCAL_TZ) + timedelta(days=1)
        y = TOP + d * (ROW + GAP)

        def x_of(t: datetime) -> float:
            t = t.astimezone(LOCAL_TZ)
            return L + C * ((t.hour * 3600 + t.minute * 60 + t.second) / 86400 if t < day1 else 1.0)

        out.append(f'<rect x="{L}" y="{y}" width="{C}" height="{ROW}" fill="#f4f2ec"/>')
        if date.weekday() < 5:
            out.append(f'<rect x="{L + C * WORK_START_H / 24:.1f}" y="{y}" width="{C * (WORK_END_H - WORK_START_H) / 24:.1f}" '
                       f'height="{ROW}" fill="#e6ecf7"/>')
        # outside the run's window: nothing was measured there
        for a, b in ((day0, min(day1, w0)), (max(day0, w1), day1)):
            if b > a:
                out.append(f'<rect x="{x_of(a):.1f}" y="{y}" width="{(L + C if b >= day1 else x_of(b)) - x_of(a):.1f}" '
                           f'height="{ROW}" fill="#fff" opacity=".75"><title>outside the measured week</title></rect>')
        day_s = in_s = 0.0
        for s, e, ps in pieces:
            a, b = max(s, day0), min(e, day1)
            if b <= a:
                continue
            day_s += (b - a).total_seconds()
            x0 = x_of(a)
            w = max((L + C if b >= day1 else x_of(b)) - x0, 0.8)
            names = sorted(ps, key=lambda p: order.get(p, 99))
            tip = _esc(f"{date.strftime('%a %-d')} {_clock(a.astimezone(LOCAL_TZ))}–{_clock(b.astimezone(LOCAL_TZ))} · "
                       + " + ".join(names))
            band = ROW / len(names)
            out.append("<g>" + "".join(
                f'<rect x="{x0:.1f}" y="{y + i * band:.1f}" width="{w:.1f}" height="{band:.1f}" '
                f'fill="{colors.get(p, OTHER_COLOR)}"/>' for i, p in enumerate(names))
                + f"<title>{tip}</title></g>")
            if date.weekday() < 5:
                lo = max(a, day0.replace(hour=WORK_START_H))
                hi = min(b, day0.replace(hour=WORK_END_H))
                in_s += max((hi - lo).total_seconds(), 0)
        out.append(f'<text x="{L - 8}" y="{y + ROW / 2 + 4}" font-size="11" text-anchor="end">'
                   f'{date.strftime("%a %-d").lower()}</text>')
        if day_s:
            note = (f"{round(in_s / day_s * 100)}% in work hours" if date.weekday() < 5 else "weekend")
            out.append(f'<text x="{L + C + 8}" y="{y + ROW / 2 + 4}" font-size="11" class="num">'
                       f'{_h(day_s / 3600)} · {note}</text>')
    out.append("</svg>")
    t = split.total_h
    med_in, med_out = split.med(split.in_blocks_min), split.med(split.out_blocks_min)
    pct = (lambda x: f"{round(x / t * 100)}%") if t else (lambda x: "—")
    fmt = lambda m_: "n/a" if m_ is None else f"{round(m_)} min"
    lede = (f"{_h(t)} hands-on over the week. {pct(split.in_hours_h)} fell in work hours (Mon–Fri 9am–6pm), "
            f"{pct(split.weekday_off_h)} on weekday evenings and early mornings, and {pct(split.weekend_h)} "
            f"on the weekend. A block is hands-on time joined across gaps under {tl['gap_min']:g} minutes. "
            f"In work hours the median block ran {fmt(med_in)} (n={len(split.in_blocks_min)}); outside them, "
            f"{fmt(med_out)} (n={len(split.out_blocks_min)}). {_h(tl['multi_h'])} ({pct(tl['multi_h'])}) touched "
            f"two or more projects at once, drawn as stripes. This is the whole-week figure, so it is larger "
            f"than the hands-on at the top, which counts only time that landed on a piece of work. Hover a block for its projects.")
    return f"<p>{_esc(lede)}</p>" + "".join(out)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--run-id", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()
    model = build_model(DATA_DIR / "runs" / args.run_id)
    Path(args.out).write_text(render_html(model))
    most = max((len(pr["items"]) for pr in model["projects"]), default=0)
    print(f"wrote week page: {args.out} ({model['units']} units, {len(model['projects'])} projects, "
          f"most items in one project: {most})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
