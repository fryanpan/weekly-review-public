"""Command-line entry point for weekly-review."""
from __future__ import annotations

import argparse
import functools
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import calibration as calib
from . import config as cfg_mod
from .coverage import coverage_report, rule_counts
from .digest import render_digest
from .epics import roll_up_epics
from .effort import (
    AGENT_BURST_STITCH_SEC,
    AGENT_QUIET_STITCH_SEC,
    PRESENCE_STITCH_SEC,
    active_hours_by_task,
    agent_active_hours_by_task,
    agent_lane_hours_by_task,
    dominant_model_by_task,
    hands_on_blocks_by_task,
    human_input_chars_by_task,
    hands_on_hours_by_task,
    hands_on_intervals_by_task,
    workspace_windows,
    presence_hours_by_task,
    workspace_block_intervals,
    _union_seconds,
    WORKSPACE_BLOCK_GAP_SEC,
    labor_and_agent_hours_by_task,
    wall_clock_hours_by_task,
    cognitive_wall_clock_seconds,
    context_switch_count,
    resumption_count,
)
from .artifact_credit import assign_artifact_credit
from .enrichment import enrich_task
from .events import Event, dedupe_by_event_id, write_events
from .extractors.claude_transcript import extract_from_jsonl, iter_transcript_files, recovered_generations
from .extractors.conductor_db import extract_from_conductor_db
from .extractors.git_log import extract_git_log
from .extractors.github_pr import extract_github_prs
from .extractors.linear_tasks import extract_from_pe_db_dump
from .extractors.slack import extract_from_slack_root
from .lifecycle import compute_statuses
from .llm import CachedLLMClient, LLMClient
from .overrides import apply_overrides
from . import persist
from .runs import DATA_DIR, RunManager
from .identity import load_subject_identity
from .segmentation import Task, attach_by_workspace_task, segment
from .task_identity import carry_forward_task_ids
from .workstream import find_workstreams, sibling_block


SCHEMA_VERSION = "v2"  # v2 (2026-09-18): wall_clock_hours is calendar time; labor_hours, agent_lane_hours added


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="weekly-review", description="Task-leverage weekly digest")
    p.add_argument("--since", help="ISO date, INCLUSIVE (from 00:00:00 UTC); default = 14 days ago")
    p.add_argument(
        "--until",
        help=(
            "ISO date, INCLUSIVE — the whole day is counted (to 23:59:59 UTC). "
            "So a Mon-Sun week is `--since 2026-08-03 --until 2026-08-09`, NOT "
            "`--until 2026-08-10`; passing the following Monday silently measures "
            "8 days and makes consecutive weeks overlap on the boundary day. "
            "Default = today."
        ),
    )
    p.add_argument("--inspect", action="store_true", help="Print a per-task inspection view (read-only)")
    p.add_argument(
        "--epics",
        action="store_true",
        help="Print the mechanical epic rollup (issue-ref/scope clustering), then exit",
    )
    p.add_argument(
        "--epics-llm",
        action="store_true",
        help="Print the LLM-semantic epic rollup (clusters on enriched titles); runs enrichment first",
    )
    p.add_argument(
        "--presence-stitch-min",
        type=float,
        default=PRESENCE_STITCH_SEC / 60,
        metavar="MIN",
        help=(
            "Gap tolerance for hands-on presence, in minutes (default: 10). Runs of the user's "
            "events closer together than this become one continuous interval. This is a "
            "MODELLING CHOICE, not a measurement: on Aug 10-16, 5min gives 9.9h, 10min 18.3h, "
            "15min 25.3h, 30min 37.4h. Sweep it rather than trusting one column."
        ),
    )
    p.add_argument(
        "--workspace-block-gap-sec",
        type=float,
        default=WORKSPACE_BLOCK_GAP_SEC,
        metavar="SEC",
        help=(
            "Gap tolerance for the workspace contiguous-block rule, in seconds (default: 60). "
            "Events on one taskId no further apart than this become one block spanning first "
            "event to last, with nothing added at either end. The workspace records "
            "interaction directly, so it needs a far tighter tolerance than the transcript "
            "side. Also a MODELLING CHOICE, and it points inward: on 2026-08-31..09-06 the "
            "workspace side reads 22.61h at 0s, 25.06h at 60s and 30.34h at 300s."
        ),
    )
    p.add_argument(
        "--allow-stale-archive",
        action="store_true",
        help=(
            "Measure even when the transcript archive holds no files touched inside the "
            "requested window. Off by default: a stalled archiver is indistinguishable "
            "from a quiet week in the output, and has silently produced one twice."
        ),
    )
    p.add_argument("--recalibrate", action="store_true", help="Interactive anchor-labeling flow")
    p.add_argument("--refresh-estimates", action="store_true", help="Invalidate LLM response cache before running")
    p.add_argument("--diff-runs", nargs=2, metavar=("OLD", "NEW"), help="Compare two runs by run_id")
    p.add_argument(
        "--review",
        metavar="TASK_ID",
        help="Interactively review an estimate for TASK_ID in the most recent run (or --run-id).",
    )
    p.add_argument("--run-id", help="Run id to target for --review or --inspect-db. Defaults to most recent.")
    p.add_argument(
        "--inspect-db",
        action="store_true",
        help="Open sqlite3 on the target run's DB (prints schema summary first).",
    )
    p.add_argument("--write", help="Write the digest to this path")
    p.add_argument("--source-machine", default="mac_mini")
    p.add_argument(
        "--transcripts-root",
        default=str(Path.home() / "dev" / "weekly-review-transcripts"),
        help=(
            "Parent dir holding archived transcripts + conductor DBs. "
            "Auto-discovers: each top-level subdir becomes an --extra-transcripts "
            "source tagged with the subdir name; conductor/*.db are ingested via "
            "--conductor-db. Directories named mac_mini_history/ or snapshots/ are "
            "skipped (they're the overwrite-protection store). "
            "Default: ~/dev/weekly-review-transcripts/"
        ),
    )
    p.add_argument(
        "--extra-transcripts",
        action="append",
        default=[],
        metavar="DIR:MACHINE",
        help=(
            "Additional transcript staging dir to scan (same layout as ~/.claude/projects/). "
            "Format: path[:source_machine_tag]. Repeatable. Typically unneeded if the "
            "directory lives under --transcripts-root."
        ),
    )
    p.add_argument(
        "--conductor-db",
        action="append",
        default=[],
        metavar="PATH:MACHINE:HOME",
        help=(
            "Path to a conductor.db SQLite backup to ingest root-session messages. "
            "Format: path[:source_machine_tag[:home_dir]]. Repeatable. "
            "HOME is the home dir the DB was written under (used to reconstruct the cwd "
            "for project routing). Defaults to your home dir if omitted."
        ),
    )
    p.add_argument("--model-enrich", default="claude-sonnet-4-6", help="Model for task enrichment")
    p.add_argument("--no-enrich", action="store_true", help="Skip LLM enrichment (faster, coarser)")
    p.add_argument("--enrich-workers", type=int, default=1, metavar="N",
                   help="Enrich N tasks concurrently (default 1). Each task is one independent "
                        "LLM call, so results are identical at any N; only wall time changes.")
    p.add_argument("--no-live-feedback", action="store_true",
                   help="Skip live-feedback review-comment ingestion (on by default — review comments count as hands-on)")
    p.add_argument("--live-feedback-url", default="http://localhost:8787",
                   help="Live-feedback server base URL (fallback if .ydoc data dir absent)")
    p.add_argument("--no-board-log", action="store_true",
                   help="Skip the claude-workspaces board action log (on by default — task.*, "
                        "decision.* and voice.* carry the taskId the doc stream lacks)")
    p.add_argument("--no-meetings", action="store_true",
                   help="Skip recorded meetings (on by default — a meeting is the only source "
                        "that measures time spent away from the keyboard)")
    p.add_argument("--validate", action="store_true",
                   help="Run mechanical validity checks on a run (default: most recent, or --run-id) and exit")
    p.add_argument("--avail-h", type=float, default=None,
                   help="Stated hands-on availability (hours) for the window, for the --validate ±25%% check")
    p.add_argument("--github-user", action="append", default=None,
                   help="GitHub login(s) that count as you, for authored-PR attribution. "
                        "Repeatable. Overrides WEEKLY_REVIEW_GITHUB_USER / identity.yaml.")
    p.add_argument("--billing-export", nargs="?", const="all", metavar="CLIENT_OR_PROJECT",
                   help="Render a billing export for --since..--until (both INCLUSIVE) from "
                        "--run-id, or the most recent run. Name a client or any one of its "
                        "project slugs; omit the value to export every client with work in the "
                        "run. Writes one file per client to --write, else prints. Carries no "
                        "client content: slugs, dates, hours and counts only.")
    p.add_argument("--allow-partial-sources", action="store_true",
                   help="Let --billing-export run against a run that never ingested one of the "
                        "instruments the published figure is built from. The gap is stamped into "
                        "the artifact; without this the export refuses, because a workspace "
                        "figure of 0.00h from an instrument that did not run is "
                        "indistinguishable from one that measured no work.")
    return p


def archive_window_coverage(root: Path, since_iso: str) -> tuple[int, datetime | None]:
    """(files touched at/after `since_iso`, newest mtime) for a transcript archive.

    Transcripts are append-only, so a session that was active during the window
    has an mtime at or after the window start. The converse doesn't hold — a file
    touched in-window may carry only older events — which is exactly why this is
    a NECESSARY-condition check and not an attempt to count events. Zero files
    means the archive cannot contain the window, full stop.
    """
    since_dt = datetime.fromisoformat(since_iso)
    if since_dt.tzinfo is None:
        since_dt = since_dt.replace(tzinfo=timezone.utc)
    in_window = 0
    newest: datetime | None = None
    for path in root.rglob("*.jsonl"):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if newest is None or mtime > newest:
            newest = mtime
        if mtime >= since_dt:
            in_window += 1
    return in_window, newest


def _iso_date(s: str) -> str:
    """Normalize a YYYY-MM-DD or ISO timestamp to a UTC ISO timestamp (at start of day if date-only)."""
    if "T" in s:
        return s
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc).isoformat()


@functools.lru_cache(maxsize=8)
def _utc(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _in_window(ts: str, since_iso: str, until_iso: str) -> bool:
    """Whether an event's moment falls in the window, compared as instants.

    Git stamps commits in local time with an offset, so a string compare put a
    commit made at 23:53 on the window's last day, Pacific (06:53 the next day
    UTC), inside a UTC window that ended seven hours earlier, apart from the
    session that made it. Unparseable stamps keep the string compare."""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return since_iso <= ts <= until_iso
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return _utc(since_iso) <= dt <= _utc(until_iso)


def map_in_order(items, fn, workers: int = 1):
    """Yield (item, fn(item) or the Exception it raised), in INPUT order.

    Input order is what keeps a run with N workers byte-identical to a serial
    one: results are collected by position, never by completion, so every list
    built from them (enrichment_calls, the failure block) comes out the same.
    An exception is returned, not raised, so one bad task cannot abort the rest.
    """
    def call(item):
        try:
            return fn(item)
        except Exception as e:  # noqa: BLE001 — surfaced to the caller as a value
            return e

    if workers <= 1:
        for item in items:
            yield item, call(item)
        return
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=workers) as ex:
        yield from zip(items, ex.map(call, items))


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # Re-read CONFIG_DIR from the env at each main() call (allows tests to override)
    config_dir = cfg_mod.CONFIG_DIR
    # Auto-load ~/.config/weekly-review/env (KEY=VALUE lines). Shell exports
    # still win — only missing vars get filled in.
    cfg_mod.load_env_file()
    projects = cfg_mod.load_projects()
    if not projects.projects:
        print(
            f"error: no projects.yaml found at {config_dir}/projects.yaml — see examples/projects.yaml",
            file=sys.stderr,
        )
        return 2

    if args.diff_runs:
        return _cmd_diff_runs(args.diff_runs[0], args.diff_runs[1])
    if args.validate:
        return _cmd_validate(args)
    if args.inspect_db:
        return _cmd_inspect_db(args)
    if args.review:
        return _cmd_review(args)
    if args.recalibrate:
        return _cmd_recalibrate(args)
    if args.billing_export:
        return _cmd_billing_export(args, projects)
    return _cmd_digest(args, projects)


def _cmd_billing_export(args, projects) -> int:
    """Hours for one client over one period, from a run that already exists.

    This reads a persisted run rather than measuring anything itself, so an
    invoice and the weekly digest it reconciles against come from the same
    numbers instead of two runs a few percent apart.
    """
    import sqlite3

    from .billing import (
        DATA_DIR as BILLING_DATA_DIR,
        IncompleteSources,
        PeriodNotCovered,
        build_exports,
        render_markdown,
    )

    mgr = RunManager()
    run_id = args.run_id
    if not run_id:
        runs = mgr.list_runs()
        if not runs:
            print("error: no runs found to export from", file=sys.stderr)
            return 2
        run_id = runs[0].run_id

    db = BILLING_DATA_DIR / "runs" / run_id / "run.sqlite"
    if not db.exists():
        print(f"error: {db} missing — that run has no database to export from",
              file=sys.stderr)
        return 2
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    meta = conn.execute("SELECT period_start, period_end FROM run_meta").fetchone()
    conn.close()
    if meta is None:
        print(f"error: {run_id} has no run_meta; its window cannot be proved",
              file=sys.stderr)
        return 2
    # The period defaults to the run's own window rather than to "14 days ago",
    # which is the digest's default and would silently ask for days this run
    # never covered.
    since = args.since or meta["period_start"]
    until = args.until or meta["period_end"]

    target = args.billing_export
    client = None if target == "all" else target
    if client and not any(p.client == client for p in projects.projects):
        # A project slug names its client. The export's unit is the client
        # because one client spans several slugs, and an invoice is per client —
        # so naming one slug exports that client's whole engagement, and says so.
        resolved = projects.client_for(client)
        if resolved is None:
            print(f"error: {target!r} is neither a client nor a project with a "
                  f"client in projects.yaml", file=sys.stderr)
            return 2
        print(f"note: {target!r} is a project of client {resolved!r}; exporting "
              f"that client's whole engagement, every slug of it.", file=sys.stderr)
        client = resolved

    try:
        exports = build_exports(
            run_id, period_start=since, period_end=until, client=client,
            projects_cfg=projects,
            stitch_sec=args.presence_stitch_min * 60,
            gap_sec=args.workspace_block_gap_sec,
            allow_partial_sources=args.allow_partial_sources,
        )
    except (PeriodNotCovered, IncompleteSources) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if not exports:
        who = f"client {client!r}" if client else "any client"
        print(f"no work for {who} in {run_id} over {since}..{until} "
              f"(inclusive). Nothing exported.", file=sys.stderr)
        return 1

    for exp in exports:
        md = render_markdown(exp)
        if args.write:
            path = Path(args.write)
            if len(exports) > 1:
                # One client per file, always. A file holding two clients is the
                # one thing this artifact must never be, and a single --write
                # path for a multi-client run would produce exactly that.
                slug = re.sub(r"[^a-z0-9]+", "-", exp.client.lower()).strip("-")
                path = path.with_name(f"{path.stem}-{slug}{path.suffix}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(md)
            print(f"wrote {exp.client} ({exp.hands_on_hours:.2f}h, "
                  f"{exp.billing_status}) to {path}", file=sys.stderr)
        else:
            print(md)
            print()
    return 0


def _cmd_validate(args) -> int:
    """Run the mechanical validity checks on a run (most recent or --run-id)."""
    from .validate import run_checks
    mgr = RunManager()
    run_id = args.run_id
    if not run_id:
        runs = mgr.list_runs()
        if not runs:
            print("error: no runs found to validate", file=sys.stderr)
            return 2
        run_id = runs[0].run_id  # most recent
    run_checks(run_id, args.avail_h)
    return 0


def _cmd_digest(args, projects) -> int:
    now = datetime.now(timezone.utc)
    since = args.since or (now - timedelta(days=14)).strftime("%Y-%m-%d")
    until = args.until or now.strftime("%Y-%m-%d")
    since_iso = _iso_date(since)
    # Until is inclusive for the end-of-day
    until_iso = _iso_date(until).replace("00:00:00", "23:59:59")

    mgr = RunManager()
    run = mgr.start_run(schema_version=SCHEMA_VERSION)
    print(f"starting run {run.run_id}", file=sys.stderr)

    # State the window in days, because `--until` being INCLUSIVE is easy to get
    # wrong in the direction that inflates the answer. Passing the following
    # Monday for a Mon-Sun week measures 8 days and makes consecutive weeks
    # overlap on the boundary day -- which published two hot weekly figures on
    # 2026-08-28 before anything caught it. Nothing else in the output shows the
    # window length, so it says so here, every run.
    _span_days = (
        datetime.fromisoformat(until_iso[:10]) - datetime.fromisoformat(since_iso[:10])
    ).days + 1
    _note = "" if _span_days % 7 == 0 else "  <-- not a whole number of weeks"
    print(
        f"window: {since} .. {until} inclusive = {_span_days} day(s){_note}",
        file=sys.stderr,
    )

    # 1. Extract
    all_events: list[Event] = []

    # 1a. Refresh the transcript archive, then read from IT (not the live
    # store). the user's call 2026-07-08: all analysis runs off the archive, and
    # every analysis request refreshes it first. The archive is a
    # monotonically-growing union (rsync --update --backup, no --delete), so
    # it covers transcripts Claude Code has since rotated out of the live
    # ~/.claude/projects — the live store is a subset of a fresh archive.
    archive_script = Path.home() / ".local" / "bin" / "archive-transcripts.sh"
    archive_mirror = Path(args.transcripts_root).expanduser() / args.source_machine
    if archive_script.exists():
        try:
            subprocess.run([str(archive_script)], check=True, capture_output=True, timeout=600)
            print(f"archive refreshed -> {archive_mirror}", file=sys.stderr)
        except (subprocess.SubprocessError, OSError) as e:
            print(f"warning: archive refresh failed ({e}); continuing with existing archive", file=sys.stderr)
    if archive_mirror.exists():
        primary = archive_mirror
        # The archiver has now died silently twice (Jul 15 and Aug 17). Each
        # time the script was fine, ran clean from a terminal, and left the
        # archive frozen. The cause is NOT a revoked Full Disk Access grant:
        # /bin/bash under launchd is denied on the second volume where the user's home
        # resolves, so the job dies at exec of the script and never reaches
        # ~/.claude/projects at all. An analysis reading a frozen archive reports a near-empty week
        # with 100% coverage and no warning — the most dangerous output this
        # tool can produce, because it looks like a finding. Treat "no files in
        # the window" as an error, never as a quiet week.
        in_window, newest = archive_window_coverage(archive_mirror, since_iso)
        if in_window == 0 and not args.allow_stale_archive:
            newest_str = newest.isoformat() if newest else "never"
            print(
                f"error: archive {archive_mirror} has NO transcripts touched since {since_iso}.\n"
                f"       newest archived file: {newest_str}\n"
                f"       This is a stalled archiver, not an empty week. Run\n"
                f"         {archive_script}\n"
                f"       from a terminal — it works there, because the scheduled copy does\n"
                f"       not: launchd's /bin/bash is denied on the volume the script lives\n"
                f"       on and dies at exec. Check StandardErrorPath on the LaunchAgent.\n"
                f"       Pass --allow-stale-archive to measure anyway.",
                file=sys.stderr,
            )
            return 2
        if newest is not None:
            until_dt = datetime.fromisoformat(until_iso)
            if until_dt.tzinfo is None:
                until_dt = until_dt.replace(tzinfo=timezone.utc)
            missing = (until_dt - newest).total_seconds() / 3600
            if missing > 24:
                print(
                    f"warning: archive's newest file is {missing:.0f}h before the end of the "
                    f"requested window — the tail of this window may be missing.",
                    file=sys.stderr,
                )
    else:
        # No archive on this machine — fall back to the live store.
        primary = Path.home() / ".claude" / "projects"
        print(f"warning: archive mirror {archive_mirror} missing; reading live store", file=sys.stderr)
    sources: list[tuple[Path, str]] = [(primary, args.source_machine)]

    # Auto-discover from --transcripts-root.
    # Each subdir (except guard dirs + the current machine's own mirror) becomes a
    # source tagged with subdir name. conductor/*.db handled separately below.
    # Skip <args.source_machine>/ because the default ~/.claude/projects/ scan
    # already covers it — otherwise events double-count.
    SKIP_DIRS = {"mac_mini_history", "snapshots", "conductor", args.source_machine}
    auto_conductor_dbs: list[tuple[Path, str, Path]] = []
    troot = Path(args.transcripts_root).expanduser()
    if troot.exists():
        for sub in sorted(troot.iterdir()):
            if not sub.is_dir():
                continue
            if sub.name in SKIP_DIRS:
                continue
            sources.append((sub, sub.name))
        # auto-pick conductor DBs
        conductor_dir = troot / "conductor"
        if conductor_dir.exists():
            for db in sorted(conductor_dir.glob("*.db")):
                # Source machine tag is derived from filename prefix (e.g. laptop-YYYY-MM-DD.db -> "laptop")
                tag = db.stem.split("-")[0] or "laptop"
                auto_conductor_dbs.append((db, tag, Path.home()))

    # Plus any additional staging dirs the user specified explicitly.
    for spec in (args.extra_transcripts or []):
        if ":" in spec:
            pth, tag = spec.rsplit(":", 1)
        else:
            pth, tag = spec, "extra"
        sources.append((Path(pth).expanduser(), tag))

    total_files = 0
    # Overwritten session transcripts survive only in the archive's history
    # store (see recovered_generations). Found before extraction so the current
    # copies of those sessions can record which records they already hold.
    tx_by_source = []
    for transcripts_dir, machine_tag in sources:
        if not transcripts_dir.exists():
            print(f"warning: transcript dir does not exist: {transcripts_dir}", file=sys.stderr)
            continue
        tx_by_source.append((list(iter_transcript_files(transcripts_dir, since_iso=since_iso)), machine_tag))
    top_level = [p for files, tag in tx_by_source if tag == args.source_machine
                 for p in files if "subagents" not in p.parts]
    recovered = recovered_generations(troot / "mac_mini_history", top_level, since_iso=since_iso)
    lost_sessions = {p.stem for p, _ in recovered}
    held_uuids: set = set()
    for tx_files, machine_tag in tx_by_source:
        total_files += len(tx_files)
        for jsonl in tx_files:
            seen = held_uuids if jsonl.stem in lost_sessions and "subagents" not in jsonl.parts else None
            for ev in extract_from_jsonl(jsonl, projects=projects, source_machine=machine_tag, seen_uuids=seen):
                if not _in_window(ev.ts, since_iso, until_iso):
                    continue
                if projects.is_excluded(ev.project):
                    continue
                all_events.append(ev)
    print(f"scanned {total_files} transcript files across {len(sources)} source(s)", file=sys.stderr)
    for snapshot, generation in recovered:
        kept = 0
        for ev in extract_from_jsonl(snapshot, projects=projects, source_machine=args.source_machine,
                                     seen_uuids=held_uuids, generation=generation):
            if not _in_window(ev.ts, since_iso, until_iso) or projects.is_excluded(ev.project):
                continue
            all_events.append(ev)
            kept += 1
        print(f"recovered overwritten transcript {snapshot.stem[:8]} {generation}: "
              f"{kept} events in window, from the archive history", file=sys.stderr)

    # Conductor.build SQLite DBs. Auto-discovered ones from transcripts-root come first,
    # then any explicitly passed via --conductor-db.
    conductor_specs: list[tuple[Path, str, Path]] = list(auto_conductor_dbs)
    for spec in (args.conductor_db or []):
        parts = spec.split(":")
        db_path = Path(parts[0]).expanduser()
        machine_tag = parts[1] if len(parts) >= 2 and parts[1] else "laptop"
        home_for_cwd = Path(parts[2]) if len(parts) >= 3 and parts[2] else Path.home()
        conductor_specs.append((db_path, machine_tag, home_for_cwd))

    for db_path, machine_tag, home_for_cwd in conductor_specs:
        if not db_path.exists():
            print(f"warning: conductor db not found: {db_path}", file=sys.stderr)
            continue
        print(f"ingesting conductor db: {db_path} (machine={machine_tag}, home={home_for_cwd})", file=sys.stderr)
        db_event_count = 0
        for ev in extract_from_conductor_db(
            db_path, projects=projects,
            source_machine=machine_tag, home_for_cwd=home_for_cwd,
        ):
            if not _in_window(ev.ts, since_iso, until_iso):
                continue
            if projects.is_excluded(ev.project):
                continue
            all_events.append(ev)
            db_event_count += 1
        print(f"  + {db_event_count} events from conductor db", file=sys.stderr)

    # Auto-discover product-engineer DB dumps under transcripts-root or its
    # sibling. The R2-mirror dir typically lives one level up. We support
    # both: a `pe_db/full_db_dump.json` under transcripts-root, OR the canonical
    # `~/dev/product_engineer_backup/db/full_db_dump.json`.
    pe_db_candidates = [
        troot / "pe_db" / "full_db_dump.json",
        Path.home() / "dev" / "product_engineer_backup" / "db" / "full_db_dump.json",
    ]
    for pe_db in pe_db_candidates:
        if not pe_db.exists():
            continue
        pe_count = 0
        for ev in extract_from_pe_db_dump(pe_db, projects=projects):
            if not _in_window(ev.ts, since_iso, until_iso):
                continue
            if projects.is_excluded(ev.project):
                continue
            all_events.append(ev)
            pe_count += 1
        print(f"  + {pe_count} events from product-engineer DB ({pe_db})", file=sys.stderr)
        break  # only one canonical source

    # Live-feedback review comments → hands-on events (ON by default; --no-live-feedback
    # to skip). Captures review time (mockup eyeballing, doc commenting) that happens in
    # the live-feedback plugin, not in Claude Code transcripts — parsed from the .ydoc
    # store (active + archived). The user made this the default 2026-06-13.
    if not getattr(args, "no_live_feedback", False):
        from .extractors.live_feedback import extract_live_feedback
        lf_count = 0
        for ev in extract_live_feedback(projects, base_url=args.live_feedback_url):
            if not _in_window(ev.ts, since_iso, until_iso):
                continue
            if projects.is_excluded(ev.project):
                continue
            all_events.append(ev)
            lf_count += 1
        print(f"  + {lf_count} events from live-feedback review comments", file=sys.stderr)

    # The claude-workspaces BOARD action log (ON by default; --no-board-log to skip).
    # A separate store from the doc activity stream above, and the only one where
    # both "who did it" (actor.kind) and "what it was done to" (taskId) are recorded
    # on every record. Never read before 2026-09-12; 183 of the user's own answered
    # decisions in the week of 2026-08-31 were invisible because of it. These are
    # instants, not intervals — they add no hands-on hours until the 60s
    # contiguous-block rule lands.
    if not getattr(args, "no_board_log", False):
        from .extractors.workspace_board import extract_board_events
        board_count = 0
        board_person = 0
        for ev in extract_board_events(projects):
            if not _in_window(ev.ts, since_iso, until_iso):
                continue
            if projects.is_excluded(ev.project):
                continue
            all_events.append(ev)
            board_count += 1
            if ev.actor == "user":
                board_person += 1
        print(f"  + {board_count} events from the workspace board log "
              f"({board_person} yours, {board_count - board_person} agent)", file=sys.stderr)

    # Recorded meetings (ON by default; --no-meetings to skip). The only source
    # in this pipeline that measures time spent away from the keyboard, so
    # without it an hour of talking is indistinguishable from an hour of nothing.
    # The coverage caveat prints beside the count, every run and not only when it
    # trips: a meeting total whose denominator is invisible cannot be checked.
    if not getattr(args, "no_meetings", False):
        from .extractors.meetings import extract_meetings, meeting_coverage
        meet_count = 0
        meet_sec = 0.0
        for ev in extract_meetings(projects):
            if not _in_window(ev.ts, since_iso, until_iso):
                continue
            if projects.is_excluded(ev.project):
                continue
            all_events.append(ev)
            meet_count += 1
            meet_sec += float(ev.metadata.get("fixed_active_sec") or 0.0)
        print(f"  + {meet_count} meeting segment(s) in window, {meet_sec / 3600:.2f}h "
              f"of recorded presence", file=sys.stderr)
        print(f"    {meeting_coverage().caveat}", file=sys.stderr)

    # Slack archive (from sync-slack-history.sh). Project routing comes from
    # ~/.config/weekly-review/slack_channels.yaml — events for unmapped
    # channels route to "unknown" and surface in the coverage report.
    slack_root = troot / "slack"
    if slack_root.is_dir():
        slack_count = 0
        for ev in extract_from_slack_root(slack_root, projects=projects):
            if not _in_window(ev.ts, since_iso, until_iso):
                continue
            if projects.is_excluded(ev.project):
                continue
            all_events.append(ev)
            slack_count += 1
        if slack_count:
            print(f"  + {slack_count} events from slack archive", file=sys.stderr)

    # Whose PRs count as the user's own output. Without this, shared client
    # repos attribute every teammate's PR to the user and inflate est_h.
    gh_authors = args.github_user or cfg_mod.github_usernames()
    if gh_authors:
        print(f"attributing PRs to: {', '.join(gh_authors)}", file=sys.stderr)
    else:
        print(
            "WARNING: no GitHub identity configured (WEEKLY_REVIEW_GITHUB_USER / "
            "identity.yaml / --github-user) — ALL authors' PRs on shared repos "
            "will be counted as yours. est_h will be inflated.",
            file=sys.stderr,
        )

    # And whose WORKSPACE activity counts as the user's own hands-on time. The
    # same question as the GitHub one and it gets the same treatment: print the
    # rule in force every run, warn loudly when there is none. A silent
    # identity filter is how one fragment dropped 29% of his own comments for a
    # month while the read-session path counted other people's reading.
    _identity = load_subject_identity()
    if _identity.configured:
        _eras = ", ".join(f"{e.start}..{e.end}" for e in _identity.anon_eras) or "none"
        print(
            f"workspace identity: {', '.join(_identity.name_fragments)} "
            f"(anonymous-handle eras: {_eras})",
            file=sys.stderr,
        )
    else:
        print(
            "WARNING: no workspace identity configured (WEEKLY_REVIEW_WORKSPACE_NAMES / "
            "identity.yaml) — EVERY non-agent person in the workspace store counts as "
            "yours, including anyone reading a doc you shared. hands-on will be "
            "inflated. See examples/identity.yaml.",
            file=sys.stderr,
        )

    pr_fetches: list[dict] = []
    for spec in projects.projects:
        # An excluded project's commits and PRs are its work as much as its
        # transcripts are: they came in as timeless tasks with an estimate.
        if spec.exclude:
            continue
        repo_path = _first_existing_repo(spec.cwd_patterns)
        if repo_path is None:
            continue
        for ev in extract_git_log(
            repo_path=repo_path, project=spec.slug,
            since=since_iso, source_machine=args.source_machine,
            diffstat_exclude=spec.diffstat_exclude,
        ):
            # git log --since has no --until equivalent here; filter out events
            # past until_iso so per-week runs don't absorb later commits.
            if not _in_window(ev.ts, since_iso, until_iso):
                continue
            all_events.append(ev)
        # Explicit repos: list overrides the single detected origin — org
        # projects (one client) span several repos and one remote missed ~90% of PRs.
        gh_repos = spec.repos or ([r] if (r := _detect_gh_repo(repo_path)) else [])
        for gh_repo in gh_repos:
            fetch_stats: dict = {}
            for ev in extract_github_prs(
                repo=gh_repo, project=spec.slug,
                since=since_iso, source_machine=args.source_machine,
                authors=gh_authors, stats=fetch_stats,
            ):
                # extract_github_prs filters >= since but not <= until — apply here.
                if not _in_window(ev.ts, since_iso, until_iso):
                    continue
                all_events.append(ev)
            pr_fetches.append({"project": spec.slug, **fetch_stats})

    # Whether every repo's PR fetch was complete, for the audits to read. A
    # fetch that silently returned a repo's newest 200 PRs is how a week kept
    # 15 of 262 merged PRs, and nothing in the run said so.
    (run.path / "pr_fetch.json").write_text(json.dumps(pr_fetches, indent=2))
    incomplete = [f for f in pr_fetches if f.get("truncated") or f.get("failed")]
    print(
        f"PR fetch: {sum(f.get('fetched', 0) for f in pr_fetches)} PRs from "
        f"{len(pr_fetches)} repo(s), {len(incomplete)} incomplete",
        file=sys.stderr,
    )

    raw_count = len(all_events)
    all_events = dedupe_by_event_id(all_events)
    if len(all_events) != raw_count:
        print(
            f"deduped {raw_count - len(all_events)} duplicate events "
            f"(kept {len(all_events)} unique)",
            file=sys.stderr,
        )
    print(f"extracted {len(all_events)} events", file=sys.stderr)
    # A board task no store holds is an agent's test board on its own server,
    # not one of his: it must not pull work away from the task he asked for.
    if not getattr(args, "no_board_log", False):
        from .extractors.workspace_board import drop_unheld_cards, held_board_tasks
        unheld_events, unheld_ids = drop_unheld_cards(all_events, held_board_tasks())
        print(f"  board task ids no board store holds (test servers): {len(unheld_ids)}, "
              f"cleared on {unheld_events} event(s)", file=sys.stderr)
    _persist_events(run, all_events)

    # 2. Segment + overrides + lifecycle
    manual_merges = cfg_mod.load_yaml("merges").get("merges", [])
    _sessionless: list = []
    _stamp_only: list = []
    _seg_stats: dict = {}
    # What the board store knows, for linking each priced unit to a board
    # task (method Step 5): which board tasks link each PR, and which ids
    # exist so a worktree merely named `t-something` is not taken for one.
    _board_links = None
    if not getattr(args, "no_board_log", False):
        from .extractors.workspace_board import board_task_goals, board_task_pr_links
        from .segmentation import BoardLinks
        _board_links = BoardLinks(pr_tasks=board_task_pr_links(), known=frozenset(board_task_goals()))
    tasks = segment(all_events, manual_merges=manual_merges, dropped=_sessionless,
                    stamp_only=_stamp_only, stats=_seg_stats, board_links=_board_links)
    # The user 2026-09-15: a commit with no session anywhere is not his work, and
    # the limitation is printed rather than hidden — this tool cannot see what a
    # scheduled job or a routine on another machine did.
    print(f"commits with no session within 30 min, not counted as work: {len(_sessionless)}"
          + (" (" + ", ".join(sorted(t.project for t in _sessionless)) + ")" if _sessionless else ""),
          file=sys.stderr)
    # A task made of nothing but stamps is a card somebody moved. Printed every
    # run, healthy or not: silence is not a value, and a reader who only sees
    # this line when it trips cannot tell "none today" from "the drop stopped
    # running". 28 such tasks survived on the weeks of 2026-08-17 and 09-07,
    # carrying 4.6h of estimated output and no evidence of any kind.
    print(f"tasks made only of board stamps, not counted as work: {len(_stamp_only)}"
          + (" (" + ", ".join(sorted(t.project for t in _stamp_only)) + ")" if _stamp_only else ""),
          file=sys.stderr)
    if manual_merges:
        print(f"applied manual-merge config ({len(manual_merges)} directive(s))", file=sys.stderr)
    from .segmentation import printed_artifact_credit
    _credit = printed_artifact_credit(tasks, all_events)
    print(f"printed PRs on a task that printed them: {_credit['prs_on_printer']}/{_credit['prs_printed']}; "
          f"printed commits: {_credit['commits_on_printer']}/{_credit['commits_printed']}", file=sys.stderr)
    # The expected value goes beside the number. It read 50/266 on every run
    # of the 2026-09-07 week and nobody took that for a failure; with the
    # identity joins ahead of proximity it reads 263/266.
    for _what, _on, _of in (("PRs", _credit["prs_on_printer"], _credit["prs_printed"]),
                            ("commits", _credit["commits_on_printer"], _credit["commits_printed"])):
        if _of >= 10 and _on < 0.9 * _of:
            print(f"WARNING: {_of - _on} of {_of} printed {_what} sit on a task that did not print them "
                  f"(expected under 10%). Their estimates are credited to somebody else's work.", file=sys.stderr)
    # One unit per merged PR (the user, 2026-09-17). Both counts every run: a
    # merged PR off every task is unpriced work, and a task holding two is
    # one prompt pricing two PRs, which under-prices (22 PRs in one prompt
    # read 80h; priced one at a time they read 0.75h plus their builders').
    from .segmentation import merged_pr_units
    _units = merged_pr_units(tasks, all_events)
    print(f"merged PRs on a task: {_units['on_a_task']}/{_units['merged_prs']}; "
          f"tasks holding two or more merged PRs: {_units['tasks_with_two_or_more']} (expected 0); "
          f"tasks split into PR units: {_seg_stats.get('tasks_split', 0)}, "
          f"units made: {_seg_stats.get('units_made', 0)}, "
          f"of which found a builder lane by worktree: {_seg_stats.get('units_with_a_lane', 0)} "
          f"(expected about a quarter: 24 of 96 units on the week of 2026-09-07; the rest are the lead's "
          f"own opening turn or the PR alone)",
          file=sys.stderr)
    if _units["tasks_with_two_or_more"]:
        print(f"WARNING: {_units['tasks_with_two_or_more']} task(s) hold two or more merged PRs; "
              f"each will be priced in one prompt.", file=sys.stderr)
    if _units["merged_prs"] >= 10 and _units["on_a_task"] < 0.9 * _units["merged_prs"]:
        print(f"WARNING: {_units['merged_prs'] - _units['on_a_task']} of {_units['merged_prs']} merged PRs "
              f"sit on no task (expected under 10%); their work is not priced.", file=sys.stderr)

    # A lead's own lane is never one group (the user, 2026-09-17). Its leftover
    # turns go to one coordination unit per project per calendar day, so no
    # unit pass 5 makes can span more than a day of a lead's events. A board
    # task's unit can, by design: it holds every turn about that board task.
    from .segmentation import lead_lane_report
    _lead = lead_lane_report(tasks, all_events)
    print(f"lead sessions: {_lead['lead_sessions']}, lead-lane events: {_lead['lead_lane_events']}; "
          f"coordination units per project (one per calendar day, local clock):", file=sys.stderr)
    for _p, _row in _lead["coordination"].items():
        print(f"  {_p}: {_row['units']} units, {_row['events']} events, "
              f"{_row['share']:.0%} of the project's lead-lane events", file=sys.stderr)
    _wa = _lead["widest_unit"]
    _w5 = _lead["widest_pass5_unit"]
    print(f"widest span of lead-lane events in one unit: "
          f"{_wa['hours']}h ({_wa['unit_kind']} {_wa['task_id']}; a board task's unit spans its whole life)"
          if _wa else "widest span of lead-lane events in one unit: none", file=sys.stderr)
    print(f"widest lane / worktree / coordination unit: "
          f"{_w5['hours']}h ({_w5['unit_kind']} {_w5['task_id']}) "
          f"(a coordination unit is one calendar day; 23.0h on the week of 2026-09-07)"
          if _w5 else "widest lane / worktree / coordination unit: none", file=sys.stderr)
    if _w5 and _w5["unit_kind"] == "coordination" and _w5["hours"] > 24:
        print(f"WARNING: a coordination unit spans {_w5['hours']}h; it is meant to be one calendar day.",
              file=sys.stderr)

    # How much of the doc-side stream can name its own task. The ticket put this
    # at 45%; measured over the whole store it is 16.8% of person read sessions
    # and 16.2% of comments, so the baseline is printed every run rather than
    # carried in a doc, and a regression in it shows up the day it happens.
    _doc_evs = [e for e in all_events if e.source_system == "live_feedback"]
    _resolved = sum(1 for e in _doc_evs
                    if (e.metadata or {}).get("task_resolution") == "task-card")
    _unstamped = sum(1 for e in _doc_evs if not (e.metadata or {}).get("task_resolution"))
    if _doc_evs:
        print(
            f"doc events resolving to a workspace task: {_resolved}/{len(_doc_evs)} "
            f"({100 * _resolved / len(_doc_evs):.1f}%); {_unstamped} carry no resolution "
            f"marker at all",
            file=sys.stderr,
        )

    # The workspace's own taskId is a hard join, so use it before anything
    # downstream is keyed by task_id. Runs after segment() because it attaches
    # to the task the evidence already chose, and never moves an attached event.
    join = attach_by_workspace_task(tasks, all_events)
    if join["attached"] or join["orphans_without_anchor"]:
        print(
            f"workspace task join: attached {join['attached']} event(s) by taskId; "
            f"{join['orphans_without_anchor']} left orphaned (no sibling in this "
            f"window); {join['split']} workspace task(s) span >1 task",
            file=sys.stderr,
        )
    # His messages with no structural link go to a classifier (method Step 3,
    # rule 3): it sees the board tasks in progress when he wrote, plus those
    # done in the previous 24h, and may answer "none". Both counts print every
    # run. Skipped with --no-enrich (it is an LLM call) or --no-board-log.
    _classifier_sample: list[dict[str, str]] = []
    if not args.no_enrich and not getattr(args, "no_board_log", False) and tasks:
        from .message_classifier import (attach_placements, classify_messages, his_unlinked_messages,
                                         load_board_tasks, sample_for_review)
        _unlinked = his_unlinked_messages(tasks, all_events)
        _board_tasks = load_board_tasks()
        _titles = {bt.id: bt.title for bt in _board_tasks}
        from .extractors.live_feedback import (load_board_projects, load_doc_boards, load_doc_roots,
                                               resolve_data_dir)
        _dd = resolve_data_dir()
        _doc_boards = load_doc_boards(_dd)
        _board_projects = load_board_projects(projects, _doc_boards, load_doc_roots(_dd))
        _boards_of_project: dict[str, set[str]] = {}
        for _b, _p in _board_projects.items():
            _boards_of_project.setdefault(_p, set()).add(_b)
        _cls_llm = CachedLLMClient(LLMClient(), cache_dir=Path(DATA_DIR) / "llm_cache")
        _placements, _cls = classify_messages(
            all_events, _unlinked, _board_tasks, llm=_cls_llm, model=args.model_enrich,
            board_of_event=lambda e: _doc_boards.get((e.metadata or {}).get("doc_id") or ""),
            boards_of_project=_boards_of_project)
        _att = attach_placements(tasks, all_events, _placements, _titles)
        _n = max(_cls["messages"], 1)
        print(f"classifier: {_cls['placed']} of {_cls['messages']} unlinked messages placed on a board task "
              f"({_cls['placed'] / _n:.0%}); \"none\": {_cls['none']} ({_cls['none'] / _n:.0%}), "
              f"of which status requests to coordination: {_cls['status_request']}, "
              f"with no candidate task at the time: {_cls['no_candidates']}; "
              f"{_cls['calls']} calls, {_cls['unparsed']} unparsed, {_cls['invalid_id']} invented ids; "
              f"units made: {_att['new_units']} board-task, {_att['new_coordination']} coordination",
              file=sys.stderr)
        _classifier_sample = sample_for_review(_placements, all_events, _titles)
    else:
        _why = "--no-enrich" if args.no_enrich else "--no-board-log" if getattr(args, "no_board_log", False) else "no tasks"
        print(f"classifier: skipped ({_why}); his unlinked messages stay unplaced", file=sys.stderr)
    overrides = cfg_mod.load_yaml("overrides")
    tasks = apply_overrides(tasks, overrides)

    # Re-key fresh task ids to prior-run ids when the event-set overlap shows
    # it's the same work — otherwise every re-segmentation orphans the review
    # verdicts keyed on the old id. Runs BEFORE anything downstream is keyed
    # by task_id. The user greenlit 2026-07-28.
    prior_dbs_desc = sorted(
        (r for r in mgr.list_runs() if r.run_id != run.run_id),
        key=lambda r: r.run_id, reverse=True,
    )
    rewrites = carry_forward_task_ids(
        tasks, all_events, [r.path / "run.sqlite" for r in prior_dbs_desc])
    if rewrites:
        print(
            f"task-id carry-forward: {len(rewrites)} id(s) inherited from prior runs "
            f"(min score {min(s for _, _, s in rewrites):.2f})",
            file=sys.stderr,
        )

    statuses = compute_statuses(tasks, all_events, now=now)
    print(f"segmented {len(tasks)} tasks", file=sys.stderr)

    # One artifact → one task's est credit. Runs across all projects so the
    # same PR/commit can't collect a full estimate in every task it touched
    # (ship shards, opened-vs-merged splits, cross-slug repo echoes).
    # Concurrent slices of one workstream. Artifact credit stops the same PR
    # being counted twice; this stops the same WORK being counted twice in
    # tasks that never held the artifact (the 2026-07-28 audit's "narrative
    # twins"). Disclosed to the estimator, not subtracted after the fact.
    workstreams = find_workstreams(tasks, all_events)
    if workstreams.n_groups:
        print(
            f"workstreams: {workstreams.n_groups} concurrent pair(s) disclosed across "
            f"{workstreams.n_tasks_with_siblings} task(s); "
            f"{len(workstreams.owners)} designated owner(s)",
            file=sys.stderr,
        )

    artifact_credit = assign_artifact_credit(tasks, all_events)
    if artifact_credit.n_duplicate_placements:
        print(
            f"artifact credit: {artifact_credit.n_identities} identities, "
            f"{artifact_credit.n_duplicate_placements} duplicate placement(s) demoted "
            f"across {len(artifact_credit.duplicates_by_task)} task(s)",
            file=sys.stderr,
        )

    if args.epics:
        epics = roll_up_epics(tasks, all_events)
        _print_epics_view(epics, len(tasks))
        return 0

    # 3. Effort / coverage / switches
    # Hands-on is PRESENCE, not keystrokes. `active_hours_by_task` charges only
    # the modeled reading+typing window around each message, so watching a build,
    # reading a diff, driving a device by hand and thinking all score zero. It
    # understated the user's week by roughly half and he rejected it on sight
    # (2026-08-17); `presence_hours_by_task` is what replaced it as the metric.
    # Keep the typing-only figure alongside — it's the floor, and printing both
    # is what stops a modelling choice from reading as a measurement.
    typing_only = active_hours_by_task(tasks, all_events)
    # Two instruments, two tolerances (the user, 2026-09-08). The transcript side
    # keeps the 10-minute presence model; the workspace side is the contiguous-
    # block rule at 60s, which adds no padding at either end of a block. They
    # are UNIONED, never added, because they describe the same hours from
    # different angles — a moment spent driving an agent is usually recorded on
    # both sides, and adding them would double-count precisely the time he was
    # most engaged. `presence_only` is kept so the run can name what the
    # published figure replaces instead of silently moving it.
    presence_only = presence_hours_by_task(
        tasks, all_events, stitch_sec=args.presence_stitch_min * 60
    )
    active = hands_on_hours_by_task(
        tasks, all_events, stitch_sec=args.presence_stitch_min * 60,
        gap_sec=args.workspace_block_gap_sec,
    )
    agent_h = agent_active_hours_by_task(tasks, all_events)
    # Wall-clock (overlap-resolved user ∪ agent) and tool-latency-inclusive
    # agent-busy. Added 2026-06-10 for the 6-metric block in the worksheet.
    # His hands-on intervals are part of labor: a task he read and answered a
    # review item on was happening while he did. (labor_hours was called
    # wall_clock_hours until 2026-09-18; wall clock is calendar time now.)
    _hands_on_iv = hands_on_intervals_by_task(
        tasks, all_events, stitch_sec=args.presence_stitch_min * 60,
        gap_sec=args.workspace_block_gap_sec,
    )
    labor_h, agent_busy_h, _bryan_h_check = labor_and_agent_hours_by_task(
        tasks, all_events, hands_on_intervals=_hands_on_iv)
    # Agent lane time: per-lane busy time summed across concurrent agents, so a
    # 16-agent workflow fan-out doesn't read as one agent's elapsed time.
    agent_lane_h = agent_lane_hours_by_task(tasks, all_events)
    # Calendar time per unit (method Step 7): first agent work to last event,
    # pauses included. Per board task it ends at the done transition instead.
    wall_h = wall_clock_hours_by_task(tasks, all_events)
    # The human lane's two tolerances print above; the agent lane's did not, and
    # it is the one nobody can see. A burst bridges a gap a tool call was
    # running up to AGENT_BURST_STITCH_SEC — evidence — and any other gap up to
    # AGENT_QUIET_STITCH_SEC, which is an assumption. Printing them apart is the
    # point: only one of them was ever argued for, and since the user set the quiet
    # one to 15 minutes on 2026-09-15 they no longer even agree. A reader who
    # cannot see which figure rests on evidence cannot check either.
    print(
        f"agent hours: {sum(agent_busy_h.values()):.1f}h elapsed / "
        f"{sum(agent_lane_h.values()):.1f}h "
        f"per-lane. A burst bridges a running tool call up to "
        f"{AGENT_BURST_STITCH_SEC // 60}min and a quiet gap up to "
        f"{AGENT_QUIET_STITCH_SEC // 60}min, and never bridges a gap its own "
        f"worker spent on another task. The quiet tolerance is a modelling "
        f"choice like the presence tolerance: the data has no knee to read it "
        f"off, and it moves a sparse week far more than a dense one.",
        file=sys.stderr,
    )
    cog_sec = cognitive_wall_clock_seconds(all_events)
    switches = context_switch_count(all_events)
    resumptions = {t.task_id: resumption_count(t, all_events) for t in tasks}
    coverage = coverage_report(tasks, all_events)
    print(f"messages on a measured task: {coverage['assigned']}/{coverage['user_message_events']} "
          f"({coverage['coverage_percent']}%; no target — the rest are messages the classifier answered "
          f"\"none\" on, or were not classified)", file=sys.stderr)
    # Every rule that placed an event this run, by count. The eight
    # message-grouping rules dropped on 2026-09-18 must never appear here.
    _rules = rule_counts(tasks)
    print("rules: " + ", ".join(f"{r}={n}" for r, n in _rules.items()), file=sys.stderr)
    # Coverage says every task got A project; it cannot say it got the RIGHT
    # one, because the proximity fallback always succeeds. The one thing that
    # DOES surface a routing break is how much measured time landed nowhere —
    # a repo with no slug, a worktree pattern that never matched, a keyword
    # table broken by a rename. On Aug 17-23 that was 26% of hands-on while
    # coverage read 100%. Keep it in front of whoever runs this.
    _unrouted = sum(active.get(t.task_id, 0.0) for t in tasks if t.project == "unknown")
    _routed_total = sum(active.values())
    if _routed_total > 0:
        _share = _unrouted / _routed_total
        # Printed every run, not only when it trips. A number that appears only
        # on failure gives a reader no way to tell "healthy" from "the check
        # stopped running" — which is how a 26% bucket sat behind 100% coverage.
        print(
            f"unrouted: {_unrouted:.2f}h of {_routed_total:.2f}h hands-on "
            f"({100 * _share:.1f}%) in project 'unknown'",
            file=sys.stderr,
        )
        if _share > 0.05:
            print(
                f"warning: {100 * _share:.0f}% of hands-on hours "
                f"({_unrouted:.1f}h) routed to project 'unknown'. Coverage does NOT catch "
                f"this — check projects.yaml for a missing slug or worktree pattern.",
                file=sys.stderr,
            )

    _typing_total = sum(typing_only.values())
    _hands_on_total = sum(active.values())
    _presence_total = sum(presence_only.values())

    # The two instruments measured on their own, week-wide rather than per task,
    # so the union is a real union across tasks and not a sum of per-task unions.
    _ws_blocks = workspace_block_intervals(
        all_events, gap_sec=args.workspace_block_gap_sec
    )
    _ws_hours = _union_seconds(_ws_blocks) / 3600
    _ws_sum = sum((b - a).total_seconds() for a, b in _ws_blocks) / 3600
    _tr_intervals = []
    _ws_attached = []
    for _tr, _ws in _hands_on_iv.values():
        _tr_intervals.extend(_tr)
        _ws_attached.extend(_ws)
    _tr_hours = _union_seconds(_tr_intervals) / 3600
    _ws_attached_hours = _union_seconds(_ws_attached) / 3600
    _comment_windows = list(workspace_windows(all_events).values())
    _combined = _union_seconds(_tr_intervals + _ws_blocks + _comment_windows) / 3600
    # The workspace INSTRUMENT is the block rule plus each comment's typing
    # window and each review answer's reading time. That is what
    # `hands_on_intervals_by_task` puts on the workspace side of every task, so
    # it is what the published figure is made of, and it is what has to be
    # weighed against the union below. `_ws_hours` is the block rule ALONE,
    # which is the right denominator for the two block lines above and the
    # wrong one for anything comparing this instrument against the whole --
    # summing it against a union of three terms reported INVARIANT VIOLATED on
    # every run whose comments fell outside a block (5.28h and 7.73h of the
    # Aug and Sep era weeks), which is a correct number wearing an alarm.
    _ws_all_hours = _union_seconds(_ws_blocks + _comment_windows) / 3600
    _sum_of_parts = _tr_hours + _ws_all_hours
    print(
        f"workspace blocks: {_ws_hours:.2f}h union over {len(_ws_blocks)} contiguous "
        f"block(s) @G={args.workspace_block_gap_sec:g}s "
        f"(Σ blocks {_ws_sum:.2f}h; cross-task overlap {_ws_sum - _ws_hours:.2f}h)",
        file=sys.stderr,
    )
    # The week-wide union counts every block; the per-task figure can only count
    # blocks whose events landed on a task. An event attached to no task is an
    # event whose hours nobody counts, so the difference is a real coverage hole
    # and not a rounding artifact -- print it rather than letting a reader
    # discover that the week is LARGER than the sum of its parts and assume the
    # union is broken.
    print(
        f"workspace attached: {_ws_attached_hours:.2f}h of {_ws_all_hours:.2f}h lands on a "
        f"task ({100 * _ws_attached_hours / _ws_all_hours if _ws_all_hours else 0:.0f}%); "
        f"{_ws_all_hours - _ws_attached_hours:.2f}h sits on workspace events no "
        f"weekly-review task claims, which is why the per-task sum below can be "
        f"SMALLER than the week-wide union",
        file=sys.stderr,
    )
    # The presence model measured the same way -- week-wide, one union -- so the
    # replacement claim is like-for-like. Comparing a week-wide union against a
    # per-task SUM would flatter whichever side happened to be summed.
    _whole_week = Task(task_id="__week__", project="all", title="__week__")
    _whole_week.event_ids.extend(range(len(all_events)))
    _presence_week = presence_hours_by_task(
        [_whole_week], all_events, stitch_sec=args.presence_stitch_min * 60
    )["__week__"]
    # Union and sum printed together, every run, so the claim
    # "unioned, never added" is checkable from the output rather than asserted.
    print(
        f"hands-on union: {_combined:.2f}h = transcripts {_tr_hours:.2f}h "
        f"∪ workspace {_ws_all_hours:.2f}h (blocks {_ws_hours:.2f}h ∪ comment and "
        f"answer windows); their SUM is {_sum_of_parts:.2f}h, so the "
        f"union is {_sum_of_parts - _combined:.2f}h smaller "
        f"({'OK' if _combined <= _sum_of_parts + 1e-9 else 'INVARIANT VIOLATED'})",
        file=sys.stderr,
    )
    # The published weekly figure, with what it replaces beside
    # it. Both are week-wide unions over the same events and the same window.
    print(
        f"WEEK hands-on: {_combined:.2f}h @ presence {args.presence_stitch_min:g}min "
        f"/ blocks {args.workspace_block_gap_sec:g}s. It REPLACES presence-only on "
        f"both instruments, which reads {_presence_week:.2f}h week-wide "
        f"({_combined - _presence_week:+.2f}h). Both are modelling choices, not "
        f"measurements: sweep with --presence-stitch-min and "
        f"--workspace-block-gap-sec before quoting either.",
        file=sys.stderr,
    )
    print(
        f"hands-on: {_hands_on_total:.1f}h (transcripts @{args.presence_stitch_min:g}min "
        f"presence ∪ workspace @{args.workspace_block_gap_sec:g}s blocks), summed per task. "
        f"It REPLACES presence-only, which reads {_presence_total:.1f}h on this window; "
        f"typing-only floor {_typing_total:.1f}h. Both tolerances are modelling "
        f"choices — sweep with --presence-stitch-min and --workspace-block-gap-sec",
        file=sys.stderr,
    )

    # 4. Enrich
    enriched: dict = {}
    enrichment_calls: list = []
    enrichment_failures: list[tuple[str, str, int, str]] = []
    if not args.no_enrich and tasks:
        anchors = calib.load_anchors(cfg_mod.CONFIG_DIR / "anchors.yaml")
        expertise_seed = cfg_mod.load_yaml("expertise").get("domains", {})
        # NO global bias multiplier. The LLM's errors aren't a single scaling
        # factor (a 4h fix and a 40h refactor have different error profiles),
        # and a multiplier built from a few reviews on outlier tasks crushes
        # everything else. Reviews instead override the displayed estimate
        # for the specific task they were filed against (see digest renderer).
        # Improvement comes from the prompt, not post-hoc correction.
        cache_dir = Path(DATA_DIR) / "llm_cache"
        if args.refresh_estimates and cache_dir.exists():
            for f in cache_dir.glob("*.json"):
                f.unlink()
        llm = CachedLLMClient(LLMClient(), cache_dir=cache_dir)
        tasks_by_id = {t.task_id: t for t in tasks}

        def _enrich_one(t):
            return enrich_task(
                t, all_events, anchors, expertise_seed, llm,
                model=args.model_enrich,
                duplicate_artifact_ids=artifact_credit.duplicates_by_task.get(t.task_id, set()),
                sibling_lines=sibling_block(
                    t, tasks_by_id, workstreams.siblings.get(t.task_id, []),
                    is_owner=t.task_id in workstreams.owners),
            )

        for i, (t, result) in enumerate(
                map_in_order(tasks, _enrich_one, workers=args.enrich_workers), 1):
            print(f"enriching task {i}/{len(tasks)}: {t.title[:40]}...", file=sys.stderr)
            if isinstance(result, Exception):
                enrichment_failures.append((t.task_id, t.title[:60], len(t.event_ids), str(result)))
                print(f"warning: enrichment failed for task {t.task_id}: {result}", file=sys.stderr)
                continue
            et, call = result
            enriched[t.task_id] = et
            enrichment_calls.append(call)

        # Per-task review override. If you've reviewed a task in any prior run,
        # the display value is your bryan_est_hours, not the LLM raw. No global
        # multiplier; estimates only improve task-by-task as you mark them up.
        prior_run_dbs = [r.path / "run.sqlite" for r in mgr.list_runs() if r.run_id != run.run_id]
        overrides = calib.load_review_overrides_full(prior_run_dbs)
        cur_event_counts = {t.task_id: len(t.event_ids) for t in tasks}
        cur_kinds = {t.task_id: t.unit_kind for t in tasks}
        n_overridden = 0
        n_skipped = 0
        n_kind_skipped = 0
        n_quarantined = 0
        for tid, et in enriched.items():
            if tid not in overrides:
                continue
            hours, base_ec, reviewed_at, base_kind = overrides[tid]
            # Verdicts recorded before the artifact-credit fix can encode the
            # very double-counting the fix removed. Drop one only when it sits
            # well ABOVE a fresh estimate that agrees with its own rationale.
            drop, why = calib.quarantine_pre_fix_override(
                hours, reviewed_at, et.est_human_hours, et.rationale)
            if drop:
                n_quarantined += 1
                print(f"quarantined {tid[:10]}: {why}", file=sys.stderr)
                continue
            if not calib.carry_forward_ok(base_ec, cur_event_counts.get(tid)):
                # Task was re-segmented since it was reviewed — the stable id now
                # covers materially different work, so the old verdict no longer
                # applies. Fall back to the fresh LLM estimate.
                n_skipped += 1
                print(
                    f"skipped stale review override for {tid[:10]} "
                    f"(events {base_ec} -> {cur_event_counts.get(tid)}); using LLM est",
                    file=sys.stderr,
                )
                continue
            cur_kind = cur_kinds.get(tid, "")
            if not calib.kind_unchanged(base_kind, cur_kind):
                # The id now sits on a different kind of unit (a conversation
                # became a coordination day, a lane became a PR unit). The
                # verdict priced the work the user looked at, not this. Print it
                # so the reviewer can re-record against the new unit.
                n_kind_skipped += 1
                print(
                    f"skipped review override for {tid[:10]} "
                    f"(kind {base_kind or 'conversation'} -> {cur_kind or 'conversation'}); "
                    f"using LLM est",
                    file=sys.stderr,
                )
                continue
            et.est_human_hours = hours
            n_overridden += 1
        if n_overridden or n_skipped or n_kind_skipped or n_quarantined:
            print(
                f"applied {n_overridden} review override(s) from prior runs"
                + (f", skipped {n_skipped} stale (re-segmented)" if n_skipped else "")
                + (f", skipped {n_kind_skipped} (unit kind changed)" if n_kind_skipped else "")
                + (f", quarantined {n_quarantined} pre-fix (inflated vs fresh)"
                   if n_quarantined else ""),
                file=sys.stderr,
            )

    # A standalone decision is worth the time to read and answer it, and nothing
    # more (the user, 2026-09-15 — see `calibration.decision_only_tasks`). The
    # estimator prices the agent's board stamps on these as an expert's
    # coordination; his own reading and replying is the only thing on them that
    # was measured, so it is what they are worth. Printed every run, not only
    # when it bites, because a correction nobody can see is one nobody checks.
    # Defined outside the `if` so the persist call below always has a list to
    # write, empty run or not. Each row says: this run replaced the estimator's
    # number on this task. The audits read them back and skip those tasks,
    # exactly as they already skip the ones the user reviewed himself -- without
    # the record, the correction is indistinguishable from an estimate that
    # disagrees with its own rationale, and gets reported as a defect.
    estimate_corrections: list[tuple[str, str, float, float]] = []
    if enriched:
        d_before = d_after = 0.0
        d_n = d_zero = 0
        for tid in calib.decision_only_tasks(tasks, all_events):
            et = enriched.get(tid)
            if et is None:
                continue
            d_before += et.est_human_hours
            before_h = et.est_human_hours
            et.est_human_hours = round(active.get(tid, 0.0), 4)
            estimate_corrections.append(
                (tid, "decision_only", before_h, et.est_human_hours))
            d_after += et.est_human_hours
            d_n += 1
            if et.est_human_hours <= 0:
                d_zero += 1
        # The zero count is named separately because those are not decisions he
        # made -- they are agent board stamps nobody touched, and they fall
        # under his rule that a zero hands-on which was not fully agent-
        # triggered AND done end to end has a problem. Pricing them at 0.0h is
        # his ruling read literally; leaving that invisible would not be.
        print(f"decision-only tasks priced at their decision time: {d_n} tasks, "
              f"Σest {d_before:.2f}h → {d_after:.2f}h"
              + (f" ({d_zero} measured no decision time at all)" if d_zero else ""),
              file=sys.stderr)

        # A task that fails enrichment silently drops out of every total, so a
        # run-to-run comparison reads the loss as an improvement. Never let that
        # hide in the scroll-back: repeat it as a block at the end.
        if enrichment_failures:
            print("\n" + "!" * 62, file=sys.stderr)
            print(
                f"!! {len(enrichment_failures)} TASK(S) HAVE NO ESTIMATE — they are "
                f"MISSING from every total in this run:", file=sys.stderr)
            for tid, title, n_ev, err in enrichment_failures:
                print(f"!!   {tid[:10]} ({n_ev} events) {title!r}: {err}", file=sys.stderr)
            print(
                "!! Do NOT compare this run's totals against another until these "
                "are resolved.", file=sys.stderr)
            print("!" * 62 + "\n", file=sys.stderr)

        # Persist enriched snapshot for --diff-runs and --recalibrate
        enriched_list = [
            {
                "task_id": tid,
                "title": et.title,
                "summary": et.summary,
                "est_human_hours": et.est_human_hours,
                "uncertainty": et.uncertainty,
                "bryan_domain_expertise": et.bryan_domain_expertise,
                "dimensions": et.dimensions,
                "rationale": et.rationale,
                "status": statuses.get(tid, "?"),
            }
            for tid, et in enriched.items()
        ]
        (run.path / "enriched.json").write_text(json.dumps(enriched_list, indent=2))

    # Each priced unit's board task, per project: the share of units linked
    # and the share of estimated output that rolls up under "no goal". Both
    # print every run so a reader can tell "healthy" from "the pass stopped
    # running". Sources tried in order; the count per source says which
    # evidence is doing the work.
    _link_line = ", ".join(f"{s}: {_seg_stats.get(f'linked_{s}', 0)}"
                           for s in ("board_pr", "pr_ref", "dispatch", "own_card", "worktree"))
    print(f"units linked to a board task by the link pass: {_seg_stats.get('units_linked', 0)}/"
          f"{_seg_stats.get('units_linkable', 0)} ({_link_line}; "
          f"abstained on two or more: {_seg_stats.get('link_ambiguous', 0)})", file=sys.stderr)
    # Coordination is its own line per project (the user, 2026-09-17): the share
    # of the project's estimated output that is a lead's dispatching, reviewing
    # and merging rather than a build. Printed every run.
    if enriched:
        print("coordination share of estimated output per project:", file=sys.stderr)
        for _proj in sorted({t.project for t in tasks}):
            _pt = [t for t in tasks if t.project == _proj and t.task_id in enriched]
            _est = sum(enriched[t.task_id].est_human_hours for t in _pt)
            _coord = sum(enriched[t.task_id].est_human_hours for t in _pt if t.unit_kind == "coordination")
            _n = sum(1 for t in _pt if t.unit_kind == "coordination")
            if _est > 0:
                print(f"  {_proj:32s} {_coord:6.1f}h of {_est:6.1f}h ({_coord / _est:.0%}) in {_n} coordination unit(s)",
                      file=sys.stderr)
        _no_est = [t for t in tasks if t.task_id not in enriched]
        print(f"units with no estimate: {len(_no_est)} of {len(tasks)} (expected 0)", file=sys.stderr)
    print("board task links per project (units with a board task / units; est hours in 'no goal'):",
          file=sys.stderr)
    for _proj in sorted({t.project for t in tasks}):
        _pt = [t for t in tasks if t.project == _proj]
        _with = sum(1 for t in _pt if t.board_task)
        _est = sum(enriched[t.task_id].est_human_hours for t in _pt if t.task_id in enriched)
        _no_goal = sum(enriched[t.task_id].est_human_hours for t in _pt
                       if t.task_id in enriched and not t.board_task)
        _est_part = (f"{_no_goal:.1f}h of {_est:.1f}h ({_no_goal / _est:.0%})" if _est > 0
                     else "no estimates")
        print(f"  {_proj:32s} {_with:4d}/{len(_pt):<4d} no goal: {_est_part}", file=sys.stderr)

    last_cal = _days_since_last_calibration(cfg_mod.CONFIG_DIR / "anchors.yaml")

    # 5. Persist queryable snapshot (events + tasks + enriched + metrics + calls)
    db_path = run.path / "run.sqlite"
    if _classifier_sample:
        # His own words, so it stays beside the run db (local) and is served
        # only on his private board.
        lines = ["# Classifier sample: 12 attachments drawn at random", "",
                 f"Run `{run.run_id}`. Each row is one of his messages the classifier placed on a board "
                 "task. `candidates` is how many tasks it was shown for that message.", "",
                 "| when | project | message | board task | candidates |", "|---|---|---|---|---|"]
        for r in _classifier_sample:
            msg = r["message"].replace("|", "\\|")
            lines.append(f"| {r['ts']} | {r['project']} | {msg} | `{r['board_task']}` {r['title']} | {r['candidates']} |")
        (run.path / "classifier-sample.md").write_text("\n".join(lines) + "\n")
        print(f"classifier sample: {run.path / 'classifier-sample.md'}", file=sys.stderr)
    # Calendar time per board task (method Step 7): first agent work on any of
    # its units to its done transition, read from the store's own transitions.
    _bt_calendar: dict[str, dict[str, Any]] = {}
    if not getattr(args, "no_board_log", False) and any(t.board_task for t in tasks):
        from .calendar_time import board_task_wall_clock, done_ts_by_board_task
        from .message_classifier import load_board_tasks
        _until_ms = int(datetime.fromisoformat(until_iso.replace("Z", "+00:00")).timestamp() * 1000)
        _bt_calendar = board_task_wall_clock(
            tasks, all_events, done_ts_by_board_task(load_board_tasks(), until_ms=_until_ms))
    with persist.connect(db_path) as conn:
        persist.write_events(conn, all_events)
        persist.write_tasks(conn, tasks, all_events)
        persist.write_artifact_credit(conn, run.run_id, artifact_credit, tasks, all_events)
        if not getattr(args, "no_board_log", False):
            # The split check exempts board tasks an agent created and finished
            # without him (the user, 2026-09-14); creation usually predates the window.
            from .extractors.workspace_board import board_task_facts
            named = {t.board_task for t in tasks if t.board_task}
            until_ms = int(datetime.fromisoformat(until_iso.replace("Z", "+00:00")).timestamp() * 1000)
            persist.write_board_tasks(conn, {bt: f for bt, f in board_task_facts(until_ms=until_ms).items()
                                             if bt in named}, calendar=_bt_calendar)
        persist.write_enriched(conn, run.run_id, enriched, statuses)
        persist.write_estimate_corrections(conn, run.run_id, estimate_corrections)
        persist.write_enrichment_calls(conn, run.run_id, enrichment_calls)
        persist.write_task_metrics(
            conn, run.run_id, active, agent_h, resumptions,
            labor_hours=labor_h, agent_busy_hours=agent_busy_h,
            hands_on_blocks=hands_on_blocks_by_task(tasks, all_events),
            human_input_chars=human_input_chars_by_task(tasks, all_events),
            dominant_model=dominant_model_by_task(tasks, all_events),
            agent_lane_hours=agent_lane_h,
            wall_clock_hours=wall_h,
        )
        persist.write_run_meta(
            conn,
            run_id=run.run_id,
            schema_version=SCHEMA_VERSION,
            period_start=since,
            period_end=until,
            cognitive_wall_clock_hours=cog_sec / 3600,
            context_switches=switches,
            coverage=coverage,
        )
    print(f"wrote run db: {db_path}", file=sys.stderr)

    # Both of the following READ the run DB, so they must come after it is
    # written. They used to sit above the persist block and therefore failed on
    # every single run ("no such table: run_meta" / "run.sqlite missing"),
    # swallowed into a warning by their own best-effort except. That silently
    # disabled the every-pass audits AND froze the BI export at its last
    # successful write. Do not move these back above the persist block.
    #
    # Refresh the flat exploration table (Tableau CSV + sqlite twin) so BI
    # connections at the stable path always see the latest run. Best-effort:
    # an export failure must not fail the run.
    try:
        from .export import ExportWouldTruncate, export_task_facts
        try:
            csv_path, _sq_path, n_facts = export_task_facts(run.run_id)
            print(f"exported task_facts ({n_facts} rows) -> {csv_path}", file=sys.stderr)
        except ExportWouldTruncate as e:
            # A narrow (--since) run must never replace a wider export. Keep the
            # existing BI table and be loud about why it wasn't refreshed.
            print(f"NOTE: task_facts NOT refreshed — {e}", file=sys.stderr)
    except Exception as e:
        print(f"warning: task_facts export failed: {e}", file=sys.stderr)

    # Data-quality audits on every pass (the user 2026-07-09). PASS/REVIEW lines
    # to stderr + audits.json next to the run DB. Best-effort.
    try:
        from .validate import run_checks
        n_review = run_checks(run.run_id, getattr(args, "avail_h", None), out=sys.stderr)
        if n_review:
            print(f"AUDITS: {n_review} check(s) need review — see audits.json", file=sys.stderr)
    except Exception as e:
        print(f"warning: data-quality audits failed: {e}", file=sys.stderr)

    if args.epics_llm:
        from .epics import roll_up_epics_llm
        # Path A (Frozen LLM): cluster via one LLM call over enriched titles.
        # The CachedLLMClient keys on (model, system, user, max_tokens, temp);
        # given the same task set and enriched titles, the prompt is
        # byte-identical → cache hit → frozen output. First-run lottery
        # mitigated by validating the cluster set before relying on epics.json.
        # The mechanical clusterer (roll_up_epics) over-merges multi-feature
        # projects on shared vocabulary (two unrelated features in one client repo).
        cache_dir = Path(DATA_DIR) / "llm_cache"
        cluster_llm = CachedLLMClient(LLMClient(), cache_dir=cache_dir)
        epics = roll_up_epics_llm(tasks, enriched, all_events, llm=cluster_llm)
        # Persist epic memberships so reports can group atomic tasks by feature.
        (run.path / "epics.json").write_text(json.dumps([
            {
                "epic_id": e.epic_id,
                "title": e.title,
                "projects": sorted(e.projects),
                "member_task_ids": e.member_task_ids,
                "total_events": e.total_events,
                "issue_refs": sorted(e.issue_refs),
            }
            for e in epics
        ], indent=2))
        print(f"wrote epics: {run.path / 'epics.json'} ({len(epics)} epics)", file=sys.stderr)
        # The audit pass above ran before epics.json existed, so audit #5
        # (epic-level est double-count) SKIPped. Re-run now that it can measure.
        try:
            from .validate import run_checks
            n_review2 = run_checks(run.run_id, getattr(args, "avail_h", None), out=sys.stderr)
            if n_review2:
                print(f"AUDITS (post-epics): {n_review2} check(s) need review", file=sys.stderr)
        except Exception as e:
            print(f"warning: post-epics audits failed: {e}", file=sys.stderr)
        _print_epics_view(epics, len(tasks))
        return 0

    from .rework import top_rework_clusters
    enriched_titles = {tid: et.title for tid, et in enriched.items()}
    rework_clusters, _rework_signals = top_rework_clusters(
        tasks, all_events, active, agent_h, enriched_titles=enriched_titles
    )

    md = render_digest(
        period_start=since,
        period_end=until,
        tasks=tasks,
        enriched=enriched,
        statuses=statuses,
        events=all_events,
        active_hours_by_task=active,
        agent_hours_by_task=agent_h,
        resumptions_by_task=resumptions,
        coverage=coverage,
        cognitive_wall_clock_hours=cog_sec / 3600,
        context_switches=switches,
        last_calibrated_days=last_cal,
        rework_clusters=rework_clusters,
        labor_hours_by_task=labor_h,
        board_task_calendar=_bt_calendar,
    )

    if args.inspect:
        _print_inspect_view(tasks, all_events, enriched, statuses)
        return 0

    if args.write:
        Path(args.write).parent.mkdir(parents=True, exist_ok=True)
        Path(args.write).write_text(md)
        print(f"wrote digest to {args.write}", file=sys.stderr)
    else:
        print(md)
    return 0


def _cmd_recalibrate(args) -> int:
    """Interactive anchor labeling against the most recent run's enriched tasks."""
    import random

    anchors_path = cfg_mod.CONFIG_DIR / "anchors.yaml"
    bias_path = cfg_mod.CONFIG_DIR / "bias.yaml"
    anchors = calib.load_anchors(anchors_path)

    mgr = RunManager()
    runs = mgr.list_runs()
    if not runs:
        print("no runs yet — run `weekly-review` first, then recalibrate.", file=sys.stderr)
        return 2
    latest = runs[0]
    enriched_path = latest.path / "enriched.json"
    if not enriched_path.exists():
        print(f"latest run {latest.run_id} has no enriched tasks (was --no-enrich used?)", file=sys.stderr)
        return 2
    enriched = json.loads(enriched_path.read_text())
    shipped = [e for e in enriched if e.get("status") == "shipped"]
    sample_pool = shipped or enriched
    sample = random.sample(sample_pool, min(5, len(sample_pool)))
    if not sample:
        print("no tasks to calibrate against.", file=sys.stderr)
        return 0

    pairs: list[tuple[float, float]] = []
    for et in sample:
        print(f"\n--- Task: {et['title']} ---")
        print(f"  Rationale: {et.get('rationale', '')}")
        print(f"  Dimensions: {et.get('dimensions', {})}")
        print(f"  LLM estimate: {et['est_human_hours']:.1f} hours ({et.get('uncertainty', '?')} uncertainty)")
        raw = input("  Your estimate in hours (blank to skip): ").strip()
        if not raw:
            continue
        try:
            truth = float(raw)
        except ValueError:
            print("  (invalid number, skipping)")
            continue
        pairs.append((et["est_human_hours"], truth))
        anchors.append({
            "title": et["title"],
            "est_human_hours": truth,
            "dimensions": et.get("dimensions", {}),
            "bryan_domain_expertise": et.get("bryan_domain_expertise"),
        })

    calib.save_anchors(anchors, anchors_path)
    bias = calib.fit_bias(pairs)
    calib.save_anchors([bias], bias_path)
    print(f"\nSaved {len(pairs)} new anchor(s) to {anchors_path}")
    print(f"Bias multiplier: {bias['multiplier']:.3f} (from {bias['n']} pairs in this run)")
    return 0


def _resolve_run(args) -> "Run | None":
    mgr = RunManager()
    runs = mgr.list_runs()
    if not runs:
        return None
    if args.run_id:
        for r in runs:
            if r.run_id == args.run_id:
                return r
        return None
    return runs[0]


def _cmd_review(args) -> int:
    """Interactive: show a task, its events, the LLM estimate, and record the user's."""
    run = _resolve_run(args)
    if run is None:
        print("no matching run.", file=sys.stderr)
        return 2
    db_path = run.path / "run.sqlite"
    if not db_path.exists():
        print(f"run {run.run_id} has no run.sqlite (pre-persistence run?).", file=sys.stderr)
        return 2
    with persist.connect(db_path) as conn:
        row = conn.execute(
            """SELECT t.task_id, t.project, t.title, t.summary, t.first_ts, t.last_ts,
                      t.user_msg_count, t.event_count,
                      e.est_human_hours, e.uncertainty, e.bryan_domain_expertise,
                      e.scope, e.depth, e.type, e.rationale, e.status
               FROM tasks t LEFT JOIN enriched e USING (task_id)
               WHERE t.task_id = ?""",
            (args.review,),
        ).fetchone()
        if row is None:
            print(f"task {args.review} not found in run {run.run_id}.", file=sys.stderr)
            return 2
        t = dict(row)
        print(f"\n## Task: {t['title']}  ({t['project']}, task_id={t['task_id']})")
        if t.get("summary"):
            print(f"   Summary: {t['summary']}")
        print(f"   Window: {t['first_ts']} → {t['last_ts']}")
        print(f"   Events: {t['event_count']} total, {t['user_msg_count']} user_message")
        if t.get("est_human_hours") is not None:
            print(
                f"   LLM estimate: {t['est_human_hours']:.2f}h "
                f"(unc={t['uncertainty']}, exp={t['bryan_domain_expertise']}, status={t['status']})"
            )
            print(f"   Dimensions: scope={t['scope']}, depth={t['depth']}, type={t['type']}")
            if t.get("rationale"):
                print(f"   Rationale: {t['rationale']}")
        else:
            print("   (no enrichment for this task)")

        user_msgs = conn.execute(
            """SELECT ev.ts, ev.content_snippet
               FROM task_events te JOIN events ev USING (event_id)
               WHERE te.task_id = ? AND ev.kind = 'user_message'
               ORDER BY ev.ts""",
            (args.review,),
        ).fetchall()
        print(f"\n   User messages ({len(user_msgs)}):")
        for m in user_msgs[:10]:
            snippet = (m["content_snippet"] or "").replace("\n", " ")[:220]
            print(f"   - [{m['ts']}] {snippet}")
        if len(user_msgs) > 10:
            print(f"   ... +{len(user_msgs) - 10} more")

        artifacts = conn.execute(
            """SELECT ev.ts, ev.kind, ev.content_snippet, ev.metadata_json
               FROM task_events te JOIN events ev USING (event_id)
               WHERE te.task_id = ? AND ev.actor = 'artifact'
               ORDER BY ev.ts""",
            (args.review,),
        ).fetchall()
        if artifacts:
            print(f"\n   Artifacts ({len(artifacts)}):")
            for a in artifacts[:10]:
                meta = json.loads(a["metadata_json"]) if a["metadata_json"] else {}
                extra = ""
                if a["kind"] in ("pr_merged", "pr_opened"):
                    extra = f" PR#{meta.get('number')} +{meta.get('additions')}/-{meta.get('deletions')}"
                print(f"   - [{a['ts']}] {a['kind']}{extra}: {(a['content_snippet'] or '')[:120]}")

        llm_est = t.get("est_human_hours")
        try:
            raw = input("\nYour estimate in hours (blank to cancel): ").strip()
        except EOFError:
            print("\n(cancelled)", file=sys.stderr)
            return 1
        if not raw:
            print("(cancelled)", file=sys.stderr)
            return 0
        try:
            bryan_hours = float(raw)
        except ValueError:
            print("invalid number.", file=sys.stderr)
            return 2
        confidence = input("Confidence (low/medium/high, blank = medium): ").strip() or "medium"
        note = input("Note (optional): ").strip() or None

        persist.record_review(
            conn,
            task_id=args.review,
            run_id=run.run_id,
            bryan_est_hours=bryan_hours,
            llm_est_hours=float(llm_est) if llm_est is not None else None,
            confidence=confidence,
            note=note,
        )
        conn.commit()
    if llm_est is not None and llm_est > 0:
        ratio = bryan_hours / llm_est
        print(f"\nRecorded. user={bryan_hours:.2f}h vs LLM={llm_est:.2f}h — ratio {ratio:.2f}x")
    else:
        print(f"\nRecorded user={bryan_hours:.2f}h (no LLM estimate to compare against).")
    return 0


def _cmd_inspect_db(args) -> int:
    """Open sqlite3 on the resolved run's DB after printing a schema summary."""
    run = _resolve_run(args)
    if run is None:
        print("no matching run.", file=sys.stderr)
        return 2
    db_path = run.path / "run.sqlite"
    if not db_path.exists():
        print(f"run {run.run_id} has no run.sqlite.", file=sys.stderr)
        return 2
    print(f"# {db_path}\n")
    with persist.connect(db_path) as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        for t in tables:
            name = t["name"]
            if name.startswith("sqlite_"):
                continue
            count = conn.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()["n"]
            cols = [c["name"] for c in conn.execute(f"PRAGMA table_info({name})").fetchall()]
            print(f"## {name}  ({count:,} rows)")
            print(f"   columns: {', '.join(cols)}\n")
    print("Launching sqlite3 — type .schema, .tables, or a query. Ctrl-D to quit.\n")
    subprocess.run(["sqlite3", "-header", "-column", str(db_path)], check=False)
    return 0


def _cmd_diff_runs(old_id: str, new_id: str) -> int:
    mgr = RunManager()
    runs = {r.run_id: r for r in mgr.list_runs()}
    if old_id not in runs or new_id not in runs:
        print("error: one or both run IDs not found", file=sys.stderr)
        return 2
    old_enriched = _load_json(runs[old_id].path / "enriched.json", default=[])
    new_enriched = _load_json(runs[new_id].path / "enriched.json", default=[])
    old_ids = {t["task_id"]: t for t in old_enriched}
    new_ids = {t["task_id"]: t for t in new_enriched}
    appeared = set(new_ids) - set(old_ids)
    disappeared = set(old_ids) - set(new_ids)
    shifted = [
        (tid, old_ids[tid]["est_human_hours"], new_ids[tid]["est_human_hours"])
        for tid in (set(old_ids) & set(new_ids))
        if abs(old_ids[tid]["est_human_hours"] - new_ids[tid]["est_human_hours"]) > 0.5
    ]

    print(f"# Diff: {old_id} → {new_id}")
    print(f"- appeared: {len(appeared)}")
    print(f"- disappeared: {len(disappeared)}")
    print(f"- shifted (>0.5h): {len(shifted)}")
    for tid, old_h, new_h in shifted[:20]:
        print(f"  - {new_ids[tid]['title']}: {old_h:.1f}h → {new_h:.1f}h")
    return 0


# --- helpers ---

def _first_existing_repo(cwd_patterns: list[str]) -> Path | None:
    """Find a plausible repo path from the list of cwd patterns."""
    for pat in cwd_patterns:
        # Direct path (no glob chars that wouldn't resolve literally)
        if "*" not in pat and "?" not in pat:
            candidate = Path(pat)
            if candidate.exists() and (candidate / ".git").exists():
                return candidate
    for pat in cwd_patterns:
        # Expand /Users/*/ to $HOME literally and trim at the next glob
        expanded = pat.replace("/Users/*/", str(Path.home()) + "/")
        base = expanded.split("*")[0].rstrip("/")
        if not base:
            continue
        candidate = Path(base)
        if candidate.exists() and ((candidate / ".git").exists() or (candidate / ".git").is_file()):
            return candidate
    return None


def _detect_gh_repo(repo_path: Path) -> str | None:
    r = subprocess.run(
        ["git", "-C", str(repo_path), "remote", "get-url", "origin"],
        capture_output=True, text=True, check=False,
    )
    url = (r.stdout or "").strip()
    if not url:
        return None
    if url.startswith("git@"):
        _, _, rest = url.partition(":")
    elif "github.com/" in url:
        rest = url.split("github.com/", 1)[1]
    else:
        return None
    return rest.removesuffix(".git")


def _persist_events(run, events: list[Event]) -> None:
    def iso_week(ts: str) -> str:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        iy, iw, _ = dt.isocalendar()
        return f"{iy}-W{iw:02d}"
    buckets: dict[tuple[str, str], list[Event]] = defaultdict(list)
    for ev in events:
        buckets[(ev.project, iso_week(ev.ts))].append(ev)
    for (project, week), evs in buckets.items():
        path = run.events_dir / project / f"{week}.jsonl"
        write_events(path, evs)


def _print_inspect_view(tasks, events, enriched, statuses) -> None:
    for t in tasks:
        status = statuses.get(t.task_id, "?")
        et = enriched.get(t.task_id)
        print(f"\n## [{status}] {t.title}   (task_id={t.task_id}, project={t.project})")
        if et:
            print(
                f"   est_human_hours: {et.est_human_hours:.2f}  "
                f"unc: {et.uncertainty}  exp: {et.bryan_domain_expertise}"
            )
            print(f"   rationale: {et.rationale}")
        print(f"   events ({len(t.event_ids)}):")
        for idx in t.event_ids[:20]:
            if idx >= len(events):
                continue
            ev = events[idx]
            snippet = (ev.content_snippet or "").replace("\n", " ")[:140]
            print(f"   - [{ev.ts}] {ev.kind}: {snippet}")


def _print_epics_view(epics, atomic_task_count: int) -> None:
    """Print the epic rollup: title, members, span, events, issue refs.

    Sorted by total events descending (roll_up_epics already returns this order).
    """
    multi = [e for e in epics if e.member_count > 1]
    singletons = len(epics) - len(multi)
    print(f"\n# Epic rollup — {len(epics)} epics from {atomic_task_count} atomic tasks")
    print(f"  ({len(multi)} multi-task epics, {singletons} singletons)\n")
    print(
        f"{'#':>3}  {'members':>7}  {'span_d':>6}  {'events':>6}  {'usr_msgs':>8}  "
        f"project(s) | issue_refs | title"
    )
    print("  " + "-" * 100)
    for i, e in enumerate(epics, 1):
        projects = ",".join(sorted(e.projects))
        refs = ",".join(sorted(e.issue_refs)) if e.issue_refs else "-"
        title = (e.title or "").replace("\n", " ")[:70]
        print(
            f"{i:>3}  {e.member_count:>7}  {e.span_days:>6.1f}  {e.total_events:>6}  "
            f"{e.total_user_messages:>8}  {projects} | {refs} | {title}"
        )


def _days_since_last_calibration(anchors_path: Path) -> int | None:
    if not anchors_path.exists():
        return None
    mtime = datetime.fromtimestamp(anchors_path.stat().st_mtime, tz=timezone.utc)
    return (datetime.now(timezone.utc) - mtime).days


def _load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text())


if __name__ == "__main__":
    sys.exit(main())
