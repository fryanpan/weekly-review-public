"""Markdown digest renderer."""
from __future__ import annotations

from typing import Any

from .calendar_time import (SPEEDUP_FORMULA, board_task_speedups, median_speedup, speedup,
                            week_wall_clock_hours)
from .events import Event
from .segmentation import Task
from .enrichment import EnrichedTask
from .rework import ReworkCluster


def _fmt_hours(h: float) -> str:
    return f"{h:.2f}h"


def _leverage(num: float, denom: float) -> str:
    if denom <= 0:
        return "n/a"
    return f"{num / denom:.2f}"


def render_digest(
    *,
    period_start: str,
    period_end: str,
    tasks: list[Task],
    enriched: dict[str, EnrichedTask],
    statuses: dict[str, str],
    events: list[Event],
    active_hours_by_task: dict[str, float],
    agent_hours_by_task: dict[str, float],
    resumptions_by_task: dict[str, int],
    coverage: dict[str, Any],
    cognitive_wall_clock_hours: float,
    context_switches: int,
    last_calibrated_days: int | None,
    rework_clusters: list[ReworkCluster] | None = None,
    labor_hours_by_task: dict[str, float] | None = None,
    board_task_calendar: dict[str, dict[str, Any]] | None = None,
) -> str:
    """`labor_hours_by_task`: user ∪ agent, pauses removed, per unit.
    `board_task_calendar`: board task → {first_agent_ts, done_ts,
    wall_clock_hours} from calendar_time.board_task_wall_clock."""
    # Partition tasks by whether we have captured user input.
    # Tasks without any user_message event are "input-uncaptured" — shipped work
    # whose root-session transcript wasn't saved (common for Conductor.build weeks).
    def _task_has_user_input(t: Task) -> bool:
        return any(
            events[i].kind == "user_message"
            for i in t.event_ids
            if i < len(events)
        )

    input_captured = [t for t in tasks if _task_has_user_input(t)]
    input_uncaptured = [t for t in tasks if not _task_has_user_input(t)]
    uncaptured_ids = {t.task_id for t in input_uncaptured}

    total_human_hours = sum(enriched[tid].est_human_hours for tid in enriched)
    total_human_hours_captured = sum(
        enriched[tid].est_human_hours for tid in enriched if tid not in uncaptured_ids
    )
    total_human_hours_uncaptured = sum(
        enriched[tid].est_human_hours for tid in enriched if tid in uncaptured_ids
    )
    total_active_hours = sum(active_hours_by_task.values())
    total_agent_hours = sum(agent_hours_by_task.values())

    by_expertise = {"expert": 0.0, "competent": 0.0, "learning": 0.0}
    for tid, et in enriched.items():
        by_expertise.setdefault(et.bryan_domain_expertise, 0.0)
        by_expertise[et.bryan_domain_expertise] += et.est_human_hours

    lines: list[str] = []
    lines.append(f"# Week of {period_start} to {period_end}")
    lines.append("")
    lines.append("## Leverage")
    lines.append(f"- Tasks active this period: {len(tasks)} ({len(input_captured)} with captured input, {len(input_uncaptured)} input-uncaptured)")
    lines.append(f"- Σ est human hours (output): {_fmt_hours(total_human_hours)}  "
                 f"[captured: {_fmt_hours(total_human_hours_captured)}, input-uncaptured: {_fmt_hours(total_human_hours_uncaptured)}]")
    lines.append(f"- Σ your active hours: {_fmt_hours(total_active_hours)}")
    lines.append(
        f"- Σ cognitive wall-clock hours: {_fmt_hours(cognitive_wall_clock_hours)} "
        f"(active/cognitive ratio: {_leverage(total_active_hours, cognitive_wall_clock_hours)} — sanity check)"
    )
    if total_active_hours < 0.5:
        lines.append(f"- **leverage_user: n/a** — active hours too low to compute (input probably not captured this period)")
    else:
        lines.append(f"- **leverage_user (captured tasks only): {_leverage(total_human_hours_captured, total_active_hours)}**")
    lines.append(
        f"- Σ agent active hours: {_fmt_hours(total_agent_hours)}, "
        f"leverage_agent: {_leverage(total_human_hours, total_agent_hours)}"
    )
    if rework_clusters:
        rework_active = sum(c.bryan_active_hours for c in rework_clusters)
        rework_agent = sum(c.agent_active_hours for c in rework_clusters)
        net_active = max(total_active_hours - rework_active, 0.001)
        share_pct = (rework_active / total_active_hours * 100) if total_active_hours else 0
        lines.append(
            f"- Rework cost: {_fmt_hours(rework_active)} active "
            f"({share_pct:.0f}% of your time), {_fmt_hours(rework_agent)} agent "
            f"— see Rework clusters below"
        )
        if total_active_hours >= 0.5:
            lines.append(
                f"- leverage_user_excl_rework: {_leverage(total_human_hours_captured, net_active)} "
                f"(rework hours excluded from denominator)"
            )
    lines.append(
        f"- By expertise: expert={_fmt_hours(by_expertise['expert'])}, "
        f"competent={_fmt_hours(by_expertise.get('competent', 0))}, "
        f"learning={_fmt_hours(by_expertise.get('learning', 0))}"
    )
    lines.append("")

    lines.extend(_calendar_section(
        period_start, period_end, tasks, enriched,
        labor_hours_by_task or {}, board_task_calendar or {}))

    lines.append("## Coverage")
    # No target since 2026-09-18: a message is placed only when it is a comment
    # on a board task, so this is the share of his messages that ran through
    # the board, not a health check.
    lines.append(
        f"- {coverage['assigned']}/{coverage['user_message_events']} of his messages sit on a measured task "
        f"({coverage['coverage_percent']}%)"
    )
    if coverage.get("unassigned_by_project"):
        lines.append(f"- Unassigned by project: {coverage['unassigned_by_project']}")
    lines.append("")

    top_by_output = sorted(enriched.values(), key=lambda e: -e.est_human_hours)[:10]
    lines.append("## Top tasks by est human hours (what got done)")
    if not top_by_output:
        lines.append("- (no enriched tasks — run without --no-enrich)")
    for et in top_by_output:
        status = statuses.get(et.task_id, "?")
        lines.append(
            f"- [{status}] **{et.title}** — est {_fmt_hours(et.est_human_hours)} "
            f"(unc: {et.uncertainty}, exp: {et.bryan_domain_expertise})"
        )
        if et.summary:
            lines.append(f"  - {et.summary}")
        if et.effort_breakdown:
            lines.append(f"  - **Effort:** {et.effort_breakdown}")
        if et.bryan_role_summary:
            lines.append(f"  - **Your role:** {et.bryan_role_summary}")
        lines.append(f"  - _{et.rationale}_")
    lines.append("")

    top_by_active = sorted(tasks, key=lambda t: -active_hours_by_task.get(t.task_id, 0))[:10]
    lines.append("## Top tasks by your active hours (where your time went)")
    if not top_by_active:
        lines.append("- (no tasks)")
    for t in top_by_active:
        et = enriched.get(t.task_id)
        title = et.title if et else t.title
        lines.append(
            f"- {title} — active {_fmt_hours(active_hours_by_task.get(t.task_id, 0))}, "
            f"agent {_fmt_hours(agent_hours_by_task.get(t.task_id, 0))}"
        )
    lines.append("")

    avg_lev = (total_human_hours / total_active_hours) if total_active_hours else 0
    if avg_lev > 0 and enriched:
        low = []
        for t in tasks:
            a = active_hours_by_task.get(t.task_id, 0)
            et = enriched.get(t.task_id)
            if et is None or a <= 0:
                continue
            lev = et.est_human_hours / a
            if lev < 0.5 * avg_lev:
                low.append((t, et, lev))
        lines.append("## Low-leverage tasks (candidates to review)")
        if not low:
            lines.append("- (none)")
        for t, et, lev in low:
            lines.append(f"- {et.title} — L {lev:.2f} vs period avg {avg_lev:.2f}")
        lines.append("")

    if rework_clusters:
        lines.append("## Rework clusters (continuous improvement)")
        lines.append(
            "- Tasks where shipped work was found broken (frustration markers, revert commits, "
            "fix-of-fix branches). Top 5 by user-active + agent-active hours."
        )
        for i, c in enumerate(rework_clusters, 1):
            label = c.titles[0] if c.titles else c.project
            if len(c.titles) > 1:
                label = f"{label} (+{len(c.titles)-1} more)"
            lines.append(
                f"{i}. **{c.project}: {label}** — "
                f"active {_fmt_hours(c.bryan_active_hours)}, agent {_fmt_hours(c.agent_active_hours)}"
            )
            evidence = []
            if c.frustration_markers:
                evidence.append(f"{c.frustration_markers} frustration marker(s)")
            if c.revert_commits:
                evidence.append(f"{c.revert_commits} revert commit(s)")
            if c.revert_prs:
                evidence.append(f"{c.revert_prs} revert PR(s)")
            if c.fix_revert_branches:
                evidence.append(f"{c.fix_revert_branches} fix-revert branch(es)")
            if evidence:
                lines.append(f"   - Signals: {', '.join(evidence)}")
            if len(c.titles) > 1:
                others = "; ".join(c.titles[1:4])
                more = f"; +{len(c.titles)-4} more" if len(c.titles) > 4 else ""
                lines.append(f"   - Tasks: {others}{more}")
        lines.append("")

    lines.append("## Blocked on you")
    lines.append("- (PR review backlog — future enricher)")
    lines.append("")

    lines.append("## Context switches")
    lines.append(f"- Total this period: {context_switches}")
    lines.append("- Secondary goal: reduce per-day switches without hurting leverage.")
    lines.append("")

    resumed = [(tid, count) for tid, count in resumptions_by_task.items() if count > 0]
    lines.append("## Resumed tasks")
    if not resumed:
        lines.append("- (none with >24h gaps)")
    else:
        for tid, count in resumed:
            et = enriched.get(tid)
            title = et.title if et else tid
            lines.append(f"- {title} — {count} resumption(s)")
    lines.append("")

    high_unc = [et.title for et in enriched.values() if et.uncertainty == "high"]
    lines.append("## Open + low-confidence estimates")
    if not high_unc:
        lines.append("- (none)")
    for title in high_unc[:10]:
        lines.append(f"- {title}")
    if last_calibrated_days is not None:
        due = " (due — run `weekly-review --recalibrate`)" if last_calibrated_days >= 14 else ""
        lines.append(f"- Last calibrated: {last_calibrated_days} days ago{due}")
    else:
        lines.append("- Last calibrated: never — run `weekly-review --recalibrate` to seed anchors")
    lines.append("")

    lines.append("## Methodology")
    lines.append(f"- {SPEEDUP_FORMULA}")
    lines.append("")

    return "\n".join(lines)


def _fmt_wall(h: float) -> str:
    return f"{h:.1f}h" if h < 48 else f"{h / 24:.1f}d ({h:.0f}h)"


def _fmt_speedup(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.1f}x"


def _calendar_section(
    period_start: str, period_end: str, tasks: list[Task], enriched: dict[str, EnrichedTask],
    labor_hours_by_task: dict[str, float], board_task_calendar: dict[str, dict[str, Any]],
) -> list[str]:
    """Calendar time and speedup for the week and per board task (method
    Step 7). Every row prints est, labor, wall clock and speedup so a reader
    can recompute the multiplier from the row."""
    from datetime import datetime
    est = {tid: et.est_human_hours for tid, et in enriched.items()}
    total_est = sum(est.values())
    total_labor = sum(labor_hours_by_task.values())
    try:
        week_wall = week_wall_clock_hours(datetime.fromisoformat(period_start[:10]),
                                         datetime.fromisoformat(period_end[:10]))
    except ValueError:
        week_wall = 0.0
    out: list[str] = ["## Calendar time and speedup"]
    out.append(
        f"- Week: est {_fmt_hours(total_est)}, labor_hours {_fmt_hours(total_labor)}, "
        f"wall_clock_hours {week_wall:.0f}h ({week_wall / 24:.0f} calendar days), "
        f"speedup {_fmt_speedup(speedup(total_est, week_wall))} "
        f"(= est / {week_wall / 3:.0f}h: {week_wall / 24:.0f} days × 8 expert hours)"
    )
    rows = board_task_speedups(tasks, est, board_task_calendar)
    med, n = median_speedup(rows)
    not_done = sum(1 for r in rows if r["why_no_span"] == "not done")
    no_agent = sum(1 for r in rows if r["why_no_span"] == "no agent work")
    done_first = sum(1 for r in rows if r["why_no_span"] == "done before its agent work")
    out.append(
        f"- Board tasks: {len(rows)} with units this week; {n} have a calendar span "
        f"(first agent work in this window → done by the window's end), median speedup "
        f"{_fmt_speedup(med)} (n={n}); {not_done} not done by the window's end, {no_agent} with "
        f"no agent work in the window, {done_first} done before their agent work here. "
        f"A board task begun in an earlier week measures only from this window's first agent work."
    )
    labor_by_bt: dict[str, float] = {}
    for t in tasks:
        bt = getattr(t, "board_task", "") or ""
        if bt:
            labor_by_bt[bt] = labor_by_bt.get(bt, 0.0) + labor_hours_by_task.get(t.task_id, 0.0)
    if rows:
        out.append("")
        out.append("| board task | units | est | labor_hours | wall_clock_hours | speedup |")
        out.append("|---|---|---|---|---|---|")
        for r in rows[:25]:
            wall = r["wall_clock_hours"]
            wall_s = r["why_no_span"] if wall is None else _fmt_wall(wall)
            out.append(
                f"| `{r['board_task']}` | {r['units']} | {_fmt_hours(r['est_h'])} | "
                f"{_fmt_hours(labor_by_bt.get(r['board_task'], 0.0))} | {wall_s} | "
                f"{_fmt_speedup(r['speedup'])} |"
            )
        if len(rows) > 25:
            out.append(f"| … {len(rows) - 25} more | | | | | |")
    out.append("")
    return out
