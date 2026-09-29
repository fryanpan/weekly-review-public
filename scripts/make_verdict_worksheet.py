"""Generate the estimate-verdict triage worksheet for a run.

One section per enriched task, optimized for a ~10-second-per-task review pass
in live-feedback. The user comments a verdict on any task whose estimate looks
wrong (OK / HIGH / LOW, optional corrected hours + note). Verdicts are
ingested into `estimate_reviews` by scripts/ingest_verdicts.py (companion).

Usage:
  uv run python3 scripts/make_verdict_worksheet.py --run-id <run> --out <path>
"""
from __future__ import annotations

import argparse
import random
import sqlite3
import sys
from pathlib import Path

DATA_DIR = Path.home() / ".local/share/weekly-review"
REPO = Path(__file__).resolve().parents[1]


def load_prior_verdicts(exclude_run: Path) -> dict[str, dict]:
    """Map task_id -> recorded verdict across ALL runs (latest per task wins).

    Lets the worksheet pre-fill tasks the user has already reviewed so he skips
    them and only spends time on the unreviewed window.
    """
    out: dict[str, dict] = {}
    runs_dir = DATA_DIR / "runs"
    for rd in sorted(runs_dir.glob("run-*")):
        db = rd / "run.sqlite"
        if not db.exists():
            continue
        try:
            conn = sqlite3.connect(str(db))
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT task_id, reviewed_at, llm_est_hours, bryan_est_hours, "
                "confidence, note FROM estimate_reviews WHERE bryan_est_hours IS NOT NULL"
            )
            for r in cur:
                tid = r["task_id"]
                if not tid:
                    continue
                ts = r["reviewed_at"] or ""
                prior = out.get(tid)
                if prior is None or ts >= prior["reviewed_at"]:
                    out[tid] = {
                        "reviewed_at": ts,
                        "llm_h": r["llm_est_hours"],
                        "bryan_h": r["bryan_est_hours"],
                        "confidence": r["confidence"],
                        "note": r["note"],
                    }
            conn.close()
        except sqlite3.DatabaseError:
            continue
    return out


SIZE_BUCKETS = [("trivial", 0.0, 0.5), ("small", 0.5, 2.0), ("moderate", 2.0, 8.0),
                ("day+", 8.0, float("inf"))]
BATCH_HANDS_ON_H = 1.0


def size_bucket(est_h: float) -> str:
    for name, lo, hi in SIZE_BUCKETS:
        if lo <= est_h < hi:
            return name
    return SIZE_BUCKETS[-1][0]


def _round_robin_by_type(pool: list, k: int, rng: random.Random) -> list:
    """Draw k rows cycling through task types, so no type crowds out the rest."""
    by_type: dict[str, list] = {}
    for r in pool:
        by_type.setdefault(r["type"] or "?", []).append(r)
    for rows in by_type.values():
        rng.shuffle(rows)
    order = sorted(by_type)
    picked: list = []
    while len(picked) < k and any(by_type.values()):
        for t in order:
            if by_type[t] and len(picked) < k:
                picked.append(by_type[t].pop())
    return picked


def stratified_sample(rows: list, n: int, seed: int, skip_ids: set[str] = frozenset(),
                      min_batch: int = 0) -> list:
    """A seeded sample spread over size buckets and, inside each, over types.

    The day+ bucket gets a third of the sample (at least `min_batch`), because
    those few tasks carry most of the pooled output. The first `min_batch` of
    them come from tasks under BATCH_HANDS_ON_H hands-on: commit and PR batches
    whose estimate rests on a diffstat alone. Tasks in `skip_ids` (already
    reviewed) are never drawn, so every pick asks for a fresh verdict.
    """
    rng = random.Random(seed)
    pools: dict[str, list] = {name: [] for name, _, _ in SIZE_BUCKETS}
    for r in rows:
        if r["task_id"] not in skip_ids:
            pools[size_bucket(r["est_human_hours"] or 0)].append(r)
    day_k = min(len(pools["day+"]), max(min_batch, round(n / 3)))
    rest = [name for name, _, _ in SIZE_BUCKETS if name != "day+"]
    quotas = {name: (n - day_k) // len(rest) for name in rest}
    for name in rest[: (n - day_k) % len(rest)]:
        quotas[name] += 1

    picked: list = []
    for name in rest:
        picked += _round_robin_by_type(pools[name], quotas[name], rng)
    batches = [r for r in pools["day+"] if (r["hands_on"] or 0) < BATCH_HANDS_ON_H]
    first = _round_robin_by_type(batches, min(min_batch, day_k), rng)
    chosen = {r["task_id"] for r in first}
    others = [r for r in pools["day+"] if r["task_id"] not in chosen]
    picked += first + _round_robin_by_type(others, day_k - len(first), rng)
    return sorted(picked, key=lambda r: r["first_ts"] or "")


def explain_rows(run_dir: Path, rows: list) -> dict[str, tuple[str, str]]:
    """Per task: how its hands-on and wall-clock figures arose, under 100 words each."""
    sys.path.insert(0, str(REPO / "src"))
    from weekly_review.effort import _make_prior_agent, hands_on_intervals_by_task
    from weekly_review.explain import RunIndex, explain_hands_on, explain_wall_clock
    from weekly_review.rundb import load_run

    events, tasks = load_run(run_dir / "run.sqlite")
    idx = RunIndex(events, tasks)
    wanted = [tasks[r["task_id"]] for r in rows if r["task_id"] in tasks]
    intervals = hands_on_intervals_by_task(wanted, events)
    prior_agent = _make_prior_agent(events)
    out = {}
    for r in rows:
        t = tasks.get(r["task_id"])
        if t is None:
            continue
        tr, ws = intervals[t.task_id]
        out[t.task_id] = (explain_hands_on(t, idx, r["hands_on"], tr, ws),
                          explain_wall_clock(t, idx, r["wall_clock"], prior_agent))
    return out


def board_titles() -> dict[str, tuple[str, str, str]]:
    """Board task id -> (board name, title, workspace id) from the workspace
    store. Empty on a machine with no store, so the line renders from ids."""
    import json
    sys.path.insert(0, str(REPO / "src"))
    titles: dict[str, tuple[str, str, str]] = {}
    try:
        from weekly_review.extractors.live_feedback import board_files, load_workspace_names, resolve_data_dir
        dd = resolve_data_dir()
        names = load_workspace_names(dd)
        for path in board_files(dd, ".tasks.json"):
            wid = path.name.split(".")[0]
            try:
                blob = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            for t in (blob.get("tasks") if isinstance(blob, dict) else None) or []:
                if isinstance(t, dict) and str(t.get("id", "")).startswith("t-"):
                    titles[t["id"]] = (names.get(wid, ""), (t.get("title") or "").strip(), wid)
    except Exception:  # no store on this machine: ids only
        return {}
    return titles


def board_task_refs(run_dir: Path, rows: list) -> dict[str, dict]:
    """Per task: the board task the segmenter put it on (`own`) and the other
    board tasks its events name (`others`, most-named first), each resolved
    against the workspace store as {id, board, title, wid, n}. Structured so
    the markdown worksheet and the HTML mockup render the same facts."""
    import collections
    import json
    titles = board_titles()
    conn = sqlite3.connect(str(run_dir / "run.sqlite"))
    tcols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    own = {}
    if "board_task" in tcols:
        own = {r[0]: r[1] for r in conn.execute("SELECT task_id, board_task FROM tasks") if r[1]}
    named: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    q = ("SELECT te.task_id, e.metadata_json FROM task_events te JOIN events e ON e.event_id = te.event_id "
         "WHERE e.metadata_json LIKE '%t-%'")
    for tid, mj in conn.execute(q):
        try:
            md = json.loads(mj or "{}")
        except ValueError:
            continue
        ctx = md.get("context") if isinstance(md.get("context"), dict) else {}
        for v in (md.get("card"), md.get("task_id"), ctx.get("taskId"), md.get("wake_task")):
            if isinstance(v, str) and v.startswith("t-"):
                named[tid][v] += 1
                break
    conn.close()

    def ref(bt: str, n: int | None = None) -> dict:
        board, title, wid = titles.get(bt, ("", "", ""))
        return {"id": bt, "board": board, "title": title, "wid": wid, "n": n}

    out = {}
    for r in rows:
        tid = r["task_id"]
        bt = own.get(tid)
        out[tid] = {
            "own": ref(bt) if bt else None,
            "others": [ref(b, n) for b, n in named[tid].most_common() if b != bt],
        }
    return out


def board_task_rows(run_dir: Path, rows: list, max_also: int = 3) -> dict[str, str]:
    """Per task: one line naming the board task the segmenter put it on, and the
    other board tasks its events name. The user reviews a week against the board,
    so a measured task that lines up with no board task, or with several, is
    the segmentation fault he is looking for. Titles come from the workspace
    store, and an id the store can place is a link into its workspace; an id
    no store holds renders as the id alone."""
    refs = board_task_refs(run_dir, rows)

    def label(x: dict) -> str:
        head = f"[{x['board']}] " if x["board"] else ""
        body = f"{head}{x['title']}" if x["title"] else f"{head}(not in any board store)"
        cnt = f", {x['n']} events" if x["n"] is not None else ""
        # The id is the link: the user opens the board task straight from the worksheet.
        bt, wid = x["id"], x["wid"]
        ref = f"[`{bt}`](/workspaces/{wid}?task={bt})" if wid else f"`{bt}`"
        return f"{body} ({ref}{cnt})"

    out = {}
    for r in rows:
        x = refs[r["task_id"]]
        if x["own"]:
            line = f"**Board task:** {label(x['own'])}"
        else:
            line = "**Board task:** _(none — the segmenter put this on no board task)_"
        others = x["others"]
        if others:
            shown = ", ".join(label(o) for o in others[:max_also])
            more = f", +{len(others) - max_also} more" if len(others) > max_also else ""
            line += f" **Also names:** {shown}{more}"
        out[r["task_id"]] = line
    return out


def render(run_dir: Path, since: str | None, until: str | None,
           delegated_only: bool = False, top: int = 0,
           sample: int = 0, seed: int = 0, stratified: int = 0,
           min_batch: int = 0, exclude_projects: tuple[str, ...] = (),
           skip_tasks: tuple[str, ...] = (), explain: bool = True,
           chronological: bool = False, board_tasks: bool = False) -> str:
    db = run_dir / "run.sqlite"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    ecols = {r[1] for r in conn.execute("PRAGMA table_info(enriched)").fetchall()}
    sys.path.insert(0, str(REPO / "src"))
    from weekly_review.persist import metric_columns
    wc = f"COALESCE({metric_columns(conn)['labor']}, 0)"  # labor: user ∪ agent, pauses removed
    goal = "e.goal" if "goal" in ecols else "NULL as goal"
    rows = conn.execute(f"""
        SELECT t.task_id, t.project, COALESCE(e.title, t.title) as title,
               {goal}, e.summary, e.est_human_hours, e.uncertainty, e.type,
               e.effort_breakdown, e.rationale, e.bryan_role_summary,
               COALESCE(m.active_hours, 0) as hands_on, {wc} as wall_clock,
               t.user_msg_count, t.first_ts, t.last_ts
        FROM tasks t
        LEFT JOIN enriched e ON e.task_id = t.task_id
        LEFT JOIN task_metrics m ON m.task_id = t.task_id
        WHERE e.est_human_hours IS NOT NULL
        ORDER BY t.first_ts
    """).fetchall()
    tcols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    if delegated_only and "delegated" in tcols:
        deleg_ids = {r[0] for r in conn.execute("SELECT task_id FROM tasks WHERE delegated=1")}
        rows = [r for r in rows if r["task_id"] in deleg_ids]
    if top:
        rows = sorted(rows, key=lambda r: -(r["est_human_hours"] or 0))[:top]
        rows = sorted(rows, key=lambda r: r["first_ts"] or "")
    if sample and len(rows) > sample:
        # Seeded so the same (run, seed) reproduces the same worksheet. Unlike
        # --top this samples the whole est distribution — small tasks included —
        # which is what an unbiased calibration audit needs.
        rows = random.Random(seed).sample(list(rows), sample)
        rows = sorted(rows, key=lambda r: r["first_ts"] or "")
    if exclude_projects:
        rows = [r for r in rows if r["project"] not in exclude_projects]
    if stratified:
        rows = stratified_sample(list(rows), stratified, seed,
                                 skip_ids=set(load_prior_verdicts(run_dir)) | set(skip_tasks),
                                 min_batch=min_batch)

    # Group by project; order projects chronologically by their earliest task,
    # tasks within a project chronologically by start time. The user reviews the
    # doc top-to-bottom "in order", so the whole thing reads as a timeline.
    # --chronological drops the project grouping: one timeline across every
    # project, so a task cut in two or counted twice sits next to its twin.
    by_project: dict[str, list] = {}
    if chronological:
        by_project["(all projects, in start-time order)"] = sorted(
            rows, key=lambda r: r["first_ts"] or "")
    else:
        for r in rows:
            by_project.setdefault(r["project"] or "unknown", []).append(r)
        for tasks in by_project.values():
            tasks.sort(key=lambda r: r["first_ts"] or "")
    projects = sorted(by_project, key=lambda p: by_project[p][0]["first_ts"] or "")
    n_projects = len({r["project"] or "unknown" for r in rows})

    explanations = explain_rows(run_dir, rows) if explain else {}
    board_lines = board_task_rows(run_dir, rows) if board_tasks else {}

    prior_verdicts = load_prior_verdicts(run_dir)
    reviewed_here = sum(1 for r in rows if r["task_id"] in prior_verdicts)
    to_review = len(rows) - reviewed_here

    win = f" ({since} → {until})" if since else ""
    out = [
        f"# Estimate verdicts — {run_dir.name}{win}",
        "",
        f"**{len(rows)} enriched tasks** across {n_projects} projects. "
        + ("Every task in start-time order regardless of project, so the doc reads "
           "top-to-bottom as one timeline; a task split in two or counted twice sits "
           "beside its twin. ● marks a task with under 3 minutes of your hands-on time."
           if chronological else
           "Projects ordered chronologically by first task; tasks within each project "
           "in start-time order — so the doc reads top-to-bottom as a timeline."),
        "",
        f"**{reviewed_here} already reviewed** (pre-filled ✅ — skip them) · "
        f"**{to_review} need a verdict.**",
        "",
        "**How to review:** for each un-filled task, comment a verdict — `OK` / `HIGH — more like Nh` / "
        "`LOW — was a full day` / any note. Tell me 'pass complete' when done.",
        "",
        "---",
        "",
    ]

    def fmt_ts(ts: str | None) -> str:
        return (ts or "")[:16].replace("T", " ")

    for proj in projects:
        tasks = by_project[proj]
        tot = sum(t["est_human_hours"] for t in tasks)
        out.append(f"# {proj}  ·  {len(tasks)} tasks below · {tot:g}h estimated between them")
        out.append("")
        for r in tasks:
            keys = r.keys()
            title = (r["title"] or "").replace("\n", " ")
            goal = (r["goal"] if "goal" in keys else None) or ""
            summary = (r["summary"] or "").strip()
            rationale = (r["rationale"] or "").strip()
            mark = "● " if r["hands_on"] < 3 / 60 else ""
            proj = f"[{r['project'] or 'unknown'}] · " if chronological else ""
            out.append(f"## {mark}{fmt_ts(r['first_ts'])} · {proj}{title}")
            out.append("")
            if goal.strip():
                out.append(f"**Why:** {goal.strip()}")
                out.append("")
            if summary:
                out.append(f"**What got done:** {summary}")
                out.append("")
            est_line = f"**Estimate — {r['est_human_hours']:g}h ({r['uncertainty'] or '?'} uncertainty):**"
            out.append(f"{est_line} {rationale}" if rationale else est_line)
            out.append("")
            # The component-level countables the estimate was derived FROM.
            # Without these a reviewer can only agree or disagree with a number;
            # with them they can check the number against its parts. Never
            # truncate it here — this is the audit trail.
            breakdown = (
                (r["effort_breakdown"] if "effort_breakdown" in keys else "") or ""
            ).strip()
            if breakdown:
                out.append(f"**Breakdown:** {breakdown}")
            else:
                out.append("**Breakdown:** _(none recorded — estimate is unaudited)_")
            out.append("")
            out.append(
                f"`est {r['est_human_hours']:g}h · hands-on {r['hands_on']:.2f}h · "
                f"wall {r['wall_clock']:.2f}h · {r['type'] or '?'} · {r['user_msg_count'] or 0} msgs`"
            )
            out.append("")
            if r["task_id"] in board_lines:
                out.append(board_lines[r["task_id"]])
                out.append("")
            for para in explanations.get(r["task_id"], ()):
                out.append(para)
                out.append("")
            pv = prior_verdicts.get(r["task_id"])
            if pv:
                verdict = pv["confidence"] or "OK"
                bh = pv["bryan_h"]
                lh = pv["llm_h"]
                changed = (bh is not None and lh is not None and abs(bh - lh) > 1e-6)
                corr = f" → {bh:g}h" if changed else ""
                note = f" — {pv['note']}" if pv["note"] else ""
                # The displayed estimate is the CURRENT run's. If the recorded
                # verdict no longer matches it, the verdict didn't carry (stale
                # re-segmentation, or pre-fix quarantine) — say so, loudly.
                # Printing "✅ reviewed: OK" beside a number the reviewer never
                # confirmed is worse than printing nothing.
                cur = r["est_human_hours"]
                superseded = (bh is not None and cur is not None
                              and abs(bh - cur) > max(0.01, 0.02 * max(bh, cur)))
                if superseded:
                    out.append(
                        f"**VERDICT [{r['task_id'][:10]}]:** ⚠️ NOT carried forward — "
                        f"you previously said **{bh:g}h**; this run re-estimates "
                        f"**{cur:g}h** (verdict superseded: re-segmented or pre-fix "
                        f"quarantine). Re-confirm or correct.{note}"
                    )
                else:
                    out.append(
                        f"**VERDICT [{r['task_id'][:10]}]:** ✅ reviewed: {verdict}{corr}{note}"
                    )
            else:
                out.append(f"**VERDICT [{r['task_id'][:10]}]:** _(comment here)_")
            out.append("")
            out.append("---")
            out.append("")
    return "\n".join(out)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--run-id", required=True)
    p.add_argument("--since")
    p.add_argument("--until")
    p.add_argument("--out", required=True)
    p.add_argument("--delegated-only", action="store_true",
                   help="Only delegated (agent-only) tasks — LLM-only estimates needing verdicts")
    p.add_argument("--top", type=int, default=0, help="Keep only the N largest by est_h")
    p.add_argument("--sample", type=int, default=0,
                   help="Random sample of N tasks across the whole est distribution (seeded)")
    p.add_argument("--seed", type=int, default=0, help="RNG seed for --sample / --stratified")
    p.add_argument("--stratified", type=int, default=0,
                   help="Sample N unreviewed tasks spread over size buckets and types (seeded)")
    p.add_argument("--min-batch", type=int, default=0,
                   help="With --stratified: at least this many day+ tasks under 1h hands-on")
    p.add_argument("--exclude-project", action="append", default=[],
                   help="Leave a project out of the worksheet (repeatable)")
    p.add_argument("--skip-task", action="append", default=[],
                   help="With --stratified: never draw this task id, e.g. one holding third-party detail (repeatable)")
    p.add_argument("--board-tasks", action="store_true",
                   help="Under each task, name the board task it lines up with and any others its events name")
    p.add_argument("--chronological", action="store_true",
                   help="One timeline across all projects (no per-project grouping); "
                        "marks tasks under 3 min hands-on with ●")
    p.add_argument("--no-explain", action="store_true",
                   help="Skip the per-task hands-on and wall-clock accounts (faster)")
    args = p.parse_args()
    run_dir = DATA_DIR / "runs" / args.run_id
    md = render(run_dir, args.since, args.until,
                delegated_only=args.delegated_only, top=args.top,
                sample=args.sample, seed=args.seed, stratified=args.stratified,
                min_batch=args.min_batch, exclude_projects=tuple(args.exclude_project),
                skip_tasks=tuple(args.skip_task), explain=not args.no_explain,
                chronological=args.chronological, board_tasks=args.board_tasks)
    Path(args.out).write_text(md)
    print(f"wrote verdict worksheet: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
