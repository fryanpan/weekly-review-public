"""Hours per client per period, in a form that can survive being invoiced against.

This is a different artifact from the digest. The digest is for the user; it can
carry his own messages, task titles and an LLM's prose, because the only reader
is the data subject. A billing record leaves the building. So this module is
built on two rules the digest does not need:

**Nothing derived from client content may appear.** Not a task title, not a
summary, not a snippet — an enricher's `title` and `summary` are written *from*
the client's material, so they are client content once removed, not a safe
paraphrase. What ships is a slug, a period, hours, and counts. "How the work
went with the agents" is therefore measured, never narrated: task counts, agent
hours, calendar time per hands-on hour. A number computed from a transcript is
not the transcript.

**The figure is built the same way as the published weekly figure.** A billing
hour that cannot be traced back to the week it came from is not defensible, and
the fastest way to break that is to compare a union against a sum. So the
client figure is a union of the same two instruments, drawn from the same
week-wide workspace partition, and the export prints the reconciliation rather
than asserting it.

**Deliberate non-conformance with the canonical metric block**
(`docs/product/specs/2026-06-10-canonical-metric-block.md`), recorded here the
way that spec records the digest's, rather than papered over. This export prints
four of the seven metrics and omits **expert hours** and both ratios built on it
(wall-clock speedup, hands-on leverage). Expert hours is an LLM estimate derived
from the enriched task — the client's own material, once removed — and a
client-facing artifact is the wrong place to publish "an expert would have taken
40h and you are being billed 6". The ratio that IS printed is
measured-over-measured (calendar time ÷ hands-on) and carries no estimate.
The required methodology footer is present and defines every number the export
prints, which is what that requirement exists for.

The reconciliation is not a subtraction, because the week's union does not
decompose by project: `workspace_attribution_key` keys a block on taskId /
docId / board and never on project, so one block can hold two projects' events
and be claimed by both. That term is measured here and printed. See
`Reconciliation`.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import ProjectsConfig, load_projects
from .effort import (
    WORKSPACE_SOURCES,
    _union_seconds,
    hands_on_intervals_by_task,
    workspace_blocks,
)
from .events import Event
from .runs import DATA_DIR
from .segmentation import Task

# DATA_DIR is re-exported from `runs` rather than rebuilt here: it honours
# WEEKLY_REVIEW_DATA, and a second hardcoded copy would send the CLI's run
# lookup and this module's database lookup to different directories the moment
# anyone set that variable.

# First date each instrument has any record at all, measured off the stores in
# the 22-week restatement (docs/research/2026-09-12-restated-weekly-series.md).
#
# A source is only "missing" from a period that STARTS on or after its era
# began. Without this, a billing export for any week before 2026-08-28 would be
# refused for having no meetings — and there are none to have, because the
# recorder had not been written. A check that fires on the absence of something
# that never existed is a check nobody can satisfy, and the first thing it would
# do is teach a reader to pass the escape hatch every time.
SOURCE_ERAS = {
    "live_feedback": "2026-04-17",
    "workspace_board": "2026-08-13",
    "meetings": "2026-08-28",
}

# Every field this module is allowed to put in an export. An allowlist, not a
# denylist, for the same reason the hands-on author filter is one: a denylist of
# content fields silently admits the next field somebody adds.
SAFE_FIELDS = frozenset({
    "client", "billing_status", "billable", "period_start", "period_end",
    "run_id", "projects", "hands_on_hours", "transcript_hours",
    "workspace_hours", "task_count", "delegated_task_count",
    "agent_hours", "agent_lane_hours", "wall_clock_hours",
    "wall_clock_per_hands_on", "reconciliation",
    # The tolerances are part of the figure, not trivia about it: the same week
    # is 22.61h at one setting and 30.34h at another. An artifact that states an
    # hours figure without them cannot be checked by the person reading it.
    "stitch_sec", "gap_sec",
})


class PeriodNotCovered(RuntimeError):
    """The run does not span the period asked for.

    Refusing beats exporting the overlap. A short period silently rendered as a
    whole one is a billing record that understates the work and reads as
    complete — and this repo has already published an 8-day week as a 7-day one
    because nothing checked the shape of the window.
    """


class IncompleteSources(RuntimeError):
    """The run never ingested an instrument the published figure is built from.

    Discovered on the first real run this module was pointed at: it held 65,901
    events and not one from `live_feedback`, `workspace_board` or `meetings`, so
    every client's workspace hours came out at exactly 0.00h. On the week in
    question the published figure is 24.70h, of which 15.09h is workspace — the
    export would have understated a client's hours by more than half and printed
    a reconciliation next to it.

    Zero from an instrument that did not run is indistinguishable from zero
    because he did no work, which is the whole reason this refuses rather than
    warns. `allow_partial_sources=True` is the escape hatch and it stamps the
    gap into the rendered artifact, because an invoice is the last place a
    silent undercount should be possible.
    """


@dataclass
class Reconciliation:
    """Why the client's hours and the week's hours are not the same number."""

    week_hands_on_hours: float
    """The week's own figure: union of both instruments over every event."""

    all_clients_union_hours: float
    """One union over every client's intervals together. Not a sum."""

    sum_of_client_hours: float
    """Each client's union, added up. Exceeds the above wherever two clients
    share a workspace block, because a block is keyed on task, not project."""

    non_client_hours: float
    """Week union minus the all-client union: his own projects, unbilled to
    anyone. This is normally the large term and it is not a discrepancy."""

    shared_between_clients_hours: float
    """`sum_of_client_hours - all_clients_union_hours`. A ceiling on the
    double-count if these figures were ever added together, which is exactly
    why they are printed rather than summed."""

    def explain(self) -> list[str]:
        lines = [
            f"Week hands-on (union, both instruments, every project): "
            f"{self.week_hands_on_hours:.2f}h",
            f"  of which on client work (union across all clients): "
            f"{self.all_clients_union_hours:.2f}h",
            f"  of which not billed to any client: {self.non_client_hours:.2f}h",
        ]
        if self.shared_between_clients_hours > 0.005:
            lines.append(
                f"Adding the per-client figures gives "
                f"{self.sum_of_client_hours:.2f}h, which is "
                f"{self.shared_between_clients_hours:.2f}h more than their union "
                f"— time inside a workspace block two clients both have an event "
                f"in. Each client's own figure is a union and is not inflated by "
                f"this; do not add them together."
            )
        else:
            lines.append(
                f"Per-client figures sum to {self.sum_of_client_hours:.2f}h, "
                f"equal to their union: no workspace block in this period is "
                f"shared between two clients."
            )
        return lines


@dataclass
class ClientPeriodExport:
    client: str
    billable: bool
    period_start: str
    period_end: str
    run_id: str
    projects: list[str]
    hands_on_hours: float
    transcript_hours: float
    workspace_hours: float
    task_count: int
    delegated_task_count: int
    agent_hours: float
    agent_lane_hours: float
    wall_clock_hours: float  # calendar: union of the client's task spans, pauses included
    stitch_sec: float
    gap_sec: float
    reconciliation: Reconciliation = field(repr=False)
    missing_sources: tuple[str, ...] = ()
    """Instruments the published figure uses that this run never ingested. Empty
    is the only state in which these hours are comparable to a published week."""

    @property
    def billing_status(self) -> str:
        # A volunteer client must never read as revenue. There is no rate field
        # anywhere in this module, so there is no revenue figure to suppress —
        # but the status is stated outright so a reader of the artifact, not
        # just of the config, knows which this is.
        return "billable" if self.billable else "unbilled (volunteer / pro-bono)"

    @property
    def wall_clock_per_hands_on(self) -> float | None:
        """Calendar hours the client's work spanned per hour of his hands-on
        time. Not the digest's wall-clock speedup ((est_h / 8) ÷ (wall / 24)):
        a billing artifact carries no estimate."""
        if self.hands_on_hours <= 0:
            return None
        return self.wall_clock_hours / self.hands_on_hours


def _maybe_ts(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_events(conn: sqlite3.Connection) -> tuple[list[Event], dict[str, int]]:
    """Rebuild the run's event list, and an event_id -> index map.

    Indices are rebuilt here rather than trusted from row order: `Task.event_ids`
    are positions in the list the pipeline held, so the map has to be keyed on
    the stored `event_id` and never on the order sqlite happens to return.
    """
    events: list[Event] = []
    index: dict[str, int] = {}
    rows = conn.execute(
        "SELECT event_id, ts, actor, project, session_id, source_system, "
        "source_machine, kind, seq, content_snippet, metadata_json FROM events"
    ).fetchall()
    for r in rows:
        try:
            md = json.loads(r["metadata_json"]) if r["metadata_json"] else {}
        except (TypeError, ValueError):
            md = {}
        ev = Event(
            ts=r["ts"], actor=r["actor"], project=r["project"] or "",
            session_id=r["session_id"] or "", source_system=r["source_system"] or "",
            source_machine=r["source_machine"] or "", kind=r["kind"] or "",
            seq=r["seq"] or 0, content_snippet=r["content_snippet"] or "",
            metadata=md,
        )
        index[r["event_id"]] = len(events)
        events.append(ev)
    return events, index


def _parse_tasks(conn: sqlite3.Connection, index: dict[str, int]) -> list[Task]:
    tasks: dict[str, Task] = {}
    for r in conn.execute(
        "SELECT task_id, project, title, delegated, first_ts, last_ts FROM tasks"
    ).fetchall():
        tasks[r["task_id"]] = Task(
            task_id=r["task_id"], project=r["project"] or "",
            first_event_ts=_maybe_ts(r["first_ts"]),
            last_event_ts=_maybe_ts(r["last_ts"]),
            # The title is client content on a client task. It is loaded because
            # Task requires it and segmentation-adjacent code reads it; it is in
            # SAFE_FIELDS' complement and never reaches an export.
            title=r["title"] or "",
            delegated=bool(r["delegated"]),
        )
    for r in conn.execute("SELECT task_id, event_id FROM task_events").fetchall():
        t = tasks.get(r["task_id"])
        idx = index.get(r["event_id"])
        if t is not None and idx is not None:
            t.event_ids.append(idx)
    return list(tasks.values())


def _task_metrics(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    # agent_lane_hours was agent_labor_hours before schema v2 (2026-09-18);
    # metric_columns resolves whichever this run db has.
    from .persist import metric_columns
    lane = metric_columns(conn)["lane"]
    return {
        r["task_id"]: r
        for r in conn.execute(
            f"SELECT m.*, {lane} AS agent_lane_hours FROM task_metrics m").fetchall()
    }


def _client_union(
    client_tasks: list[Task], events: list[Event], stitch_sec: float, gap_sec: float,
    per_task: dict[str, tuple[list, list]],
) -> tuple[float, float, float, list[tuple[datetime, datetime]]]:
    tr: list[tuple[datetime, datetime]] = []
    ws: list[tuple[datetime, datetime]] = []
    for t in client_tasks:
        a, b = per_task.get(t.task_id, ([], []))
        tr.extend(a)
        ws.extend(b)
    return (
        _union_seconds(tr + ws) / 3600,
        _union_seconds(tr) / 3600,
        _union_seconds(ws) / 3600,
        tr + ws,
    )


def build_exports(
    run_id: str,
    *,
    period_start: str,
    period_end: str,
    client: str | None = None,
    projects_cfg: ProjectsConfig | None = None,
    stitch_sec: float = 600.0,
    gap_sec: float = 60.0,
    data_dir: Path | None = None,
    allow_partial_sources: bool = False,
) -> list[ClientPeriodExport]:
    """One export per client with work in the run, or just `client` if named.

    `period_start` / `period_end` are inclusive ISO dates and must lie inside
    the run's own window, or `PeriodNotCovered` is raised. A run missing any
    instrument the published figure is built from raises `IncompleteSources`
    unless `allow_partial_sources`.
    """
    root = data_dir or DATA_DIR
    db = root / "runs" / run_id / "run.sqlite"
    if not db.exists():
        raise FileNotFoundError(f"{db} missing")
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row

    meta = conn.execute(
        "SELECT period_start, period_end FROM run_meta"
    ).fetchone()
    if meta is None:
        raise PeriodNotCovered(f"{run_id} has no run_meta; cannot prove its window")
    if period_start < meta["period_start"] or period_end > meta["period_end"]:
        raise PeriodNotCovered(
            f"{run_id} covers {meta['period_start']}..{meta['period_end']} "
            f"(inclusive); asked for {period_start}..{period_end}. Re-run the "
            f"pipeline over the period rather than exporting the overlap — a "
            f"short period rendered as a whole one understates the invoice and "
            f"reads as complete."
        )

    # What the run actually holds, read off the events rather than off a flag.
    # run.yaml records only run_id, schema_version and created_at — it does not
    # say which sources were asked for — and an artifact proves what ran where a
    # flag records what was intended.
    present = {
        r[0] for r in conn.execute("SELECT DISTINCT source_system FROM events")
    }
    missing = tuple(sorted(
        s for s in WORKSPACE_SOURCES - present
        if period_end >= SOURCE_ERAS.get(s, "0000-00-00")
    ))
    if missing and not allow_partial_sources:
        raise IncompleteSources(
            f"{run_id} ingested no {', '.join(missing)} events, so every "
            f"client's workspace hours would render as exactly 0.00h — "
            f"indistinguishable from him having done none. Re-run the pipeline "
            f"over this period with those sources on, or pass "
            f"allow_partial_sources=True to export the transcript-only figure "
            f"with the gap stated in the artifact."
        )

    cfg = projects_cfg or load_projects()
    events, index = _parse_events(conn)
    tasks = _parse_tasks(conn, index)
    metrics = _task_metrics(conn)

    per_task = hands_on_intervals_by_task(
        tasks, events, stitch_sec=stitch_sec, gap_sec=gap_sec,
        # A billed figure moves only on its own decision, so the 2026-09-14
        # comment-window change to hands-on does not reach billing.
        comment_windows=False,
    )

    # The week's own figure, built exactly as cli.py builds it: transcript
    # intervals from every task, unioned with the week-wide workspace partition.
    all_tr: list[tuple[datetime, datetime]] = []
    for a, _b in per_task.values():
        all_tr.extend(a)
    week_blocks = [(s, e) for s, e, _m in workspace_blocks(events, gap_sec)]
    week_hours = _union_seconds(all_tr + week_blocks) / 3600

    by_client: dict[str, list[Task]] = {}
    for t in tasks:
        c = cfg.client_for(t.project)
        if c:
            by_client.setdefault(c, []).append(t)

    every_client_interval: list[tuple[datetime, datetime]] = []
    hours_by_client: dict[str, tuple[float, float, float]] = {}
    for c, ts in by_client.items():
        combined, tr_h, ws_h, intervals = _client_union(
            ts, events, stitch_sec, gap_sec, per_task
        )
        hours_by_client[c] = (combined, tr_h, ws_h)
        every_client_interval.extend(intervals)

    all_clients_union = _union_seconds(every_client_interval) / 3600
    sum_of_clients = sum(v[0] for v in hours_by_client.values())
    recon = Reconciliation(
        week_hands_on_hours=week_hours,
        all_clients_union_hours=all_clients_union,
        sum_of_client_hours=sum_of_clients,
        non_client_hours=max(0.0, week_hours - all_clients_union),
        shared_between_clients_hours=max(0.0, sum_of_clients - all_clients_union),
    )

    out: list[ClientPeriodExport] = []
    for c, client_tasks in sorted(by_client.items()):
        if client is not None and c != client:
            continue
        combined, tr_h, ws_h = hours_by_client[c]
        slugs = sorted({t.project for t in client_tasks})
        billable = all(
            next((p.billable for p in cfg.projects if p.slug == s), True)
            for s in slugs
        )
        agent = sum(
            float(metrics[t.task_id]["agent_active_hours"] or 0.0)
            for t in client_tasks if t.task_id in metrics
        )
        lane = sum(
            float(metrics[t.task_id]["agent_lane_hours"] or 0.0)
            for t in client_tasks if t.task_id in metrics
        )
        # Unioned, not summed. Two of a client's tasks that were alive at the
        # same time occupy the same wall-clock, and summing their spans then
        # dividing by a union of hands-on would inflate the ratio on exactly the
        # weeks with the most parallelism — a union against a sum flatters
        # whichever side was summed.
        wall = _union_seconds([
            (t.first_event_ts, t.last_event_ts) for t in client_tasks
            if t.first_event_ts and t.last_event_ts
            and t.last_event_ts >= t.first_event_ts
        ]) / 3600
        out.append(ClientPeriodExport(
            client=c, billable=billable,
            period_start=period_start, period_end=period_end, run_id=run_id,
            projects=slugs,
            hands_on_hours=combined, transcript_hours=tr_h, workspace_hours=ws_h,
            task_count=len(client_tasks),
            delegated_task_count=sum(1 for t in client_tasks if t.delegated),
            agent_hours=agent, agent_lane_hours=lane, wall_clock_hours=wall,
            stitch_sec=stitch_sec, gap_sec=gap_sec,
            reconciliation=recon, missing_sources=missing,
        ))
    return out


def render_markdown(exp: ClientPeriodExport) -> str:
    """The artifact. One client, one period, no client content."""
    speedup = exp.wall_clock_per_hands_on
    lines = [
        f"# {exp.client} — {exp.period_start} to {exp.period_end}",
        "",
        f"**{exp.hands_on_hours:.2f} hands-on hours** · {exp.billing_status}",
        "",
    ]
    if exp.missing_sources:
        lines += [
            f"> **Incomplete: this figure is a floor, not the hours.** The run "
            f"behind it never ingested {', '.join(exp.missing_sources)}, so any "
            f"time recorded only by those instruments is missing and the "
            f"workspace line below reads 0.00h whether or not there was work. "
            f"Do not invoice against this without re-running the period with "
            f"every source on.",
            "",
        ]
    lines += [
        "| | |",
        "| --- | --- |",
        f"| Period (inclusive) | {exp.period_start} .. {exp.period_end} |",
        f"| Projects | {', '.join(exp.projects)} |",
        f"| Hands-on hours | {exp.hands_on_hours:.2f}h |",
        f"| — from Claude Code transcripts | {exp.transcript_hours:.2f}h |",
        f"| — from workspace activity | {exp.workspace_hours:.2f}h |",
        f"| Billing status | {exp.billing_status} |",
        "",
        "## How the work went with the agents",
        "",
        f"- Tasks worked: {exp.task_count} "
        f"({exp.delegated_task_count} run start-to-finish by agents)",
        f"- Agent hours on those tasks: {exp.agent_hours:.2f}h "
        f"(per-lane {exp.agent_lane_hours:.2f}h)",
        f"- Calendar time across those tasks: {exp.wall_clock_hours:.2f}h",
    ]
    if speedup is not None:
        lines.append(
            f"- Wall-clock per hands-on hour: {speedup:.1f}x — for every hour "
            f"at the keyboard, this much elapsed on the work."
        )
    lines += [
        "",
        "## Reconciliation with the weekly figure",
        "",
        "These hours are built the same way as the published weekly figure: a "
        "union of modelled transcript presence with measured workspace blocks, "
        "drawn from the same week-wide partition. They are a union, never a sum, "
        "so they cannot double-count time spent on two tasks at once.",
        "",
    ]
    lines += [f"- {line}" for line in exp.reconciliation.explain()]
    lines += [
        "",
        "## Methodology — what each number means",
        "",
        "- **Hands-on hours** — presence, not keystrokes: time the work was "
        "being attended to, including reading a diff, watching a build and "
        f"reviewing, not only typing. Two instruments, unioned so overlap is "
        f"counted once. Transcript presence stitches a run of messages less "
        f"than {exp.stitch_sec / 60:g} minutes apart; workspace activity is "
        f"blocked into runs of events less than {exp.gap_sec:g} seconds apart, "
        f"spanning first event to last and adding nothing at either end. Those "
        f"two tolerances are what makes it a modelling choice inside a range "
        f"rather than a stopwatch — they are stated because the figure moves "
        f"with them — and run-to-run variance is about ±2%.",
        "- **Tasks worked** — units of work segmented from the record, not "
        "tickets. *Run start-to-finish by agents* counts those with no "
        "hands-on steering in the middle.",
        "- **Agent hours** — elapsed time agents were working on these tasks, "
        "all of them merged into one timeline, so ten agents at once read as "
        "one hour per hour. **Per-lane agent hours** sums each agent separately, so "
        "the same hour with ten agents reads as ten.",
        "- **Elapsed wall-clock** — first to last moment each task was alive, "
        "unioned across the client's tasks. Overlapping tasks are counted once.",
        "- Expert-hours estimates and the leverage ratios built on them are "
        "deliberately not in this export.",
        "",
        f"Source run: `{exp.run_id}`.",
        "",
        "_No client material appears in this export: it carries project slugs, "
        "dates, hours and counts only._",
    ]
    return "\n".join(lines)
