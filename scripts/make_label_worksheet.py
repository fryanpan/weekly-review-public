"""Generate a labeling worksheet from a persisted weekly-review run.

Reads the latest run's SQLite DB (or one passed via --run-id) and writes a
markdown worksheet sorted by est_human_hours DESC, with per-task labeling
slots the user can fill in (TRUTH_h, SEG_FLAG, NOTES).
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

from weekly_review.persist import metric_columns
from weekly_review.rework import FRUSTRATION_MARKERS, REVERT_RE
from weekly_review.runs import RunManager


# "Keep going" / status-poke messages — times the user had to nudge a stalled agent.
NUDGE_MARKERS = [
    re.compile(r"\bkeep\s+going\b", re.IGNORECASE),
    re.compile(r"\bkeep\s+at\s+it\b", re.IGNORECASE),
    re.compile(r"\b(please\s+)?(continue|proceed|carry\s+on|go\s+on)\b", re.IGNORECASE),
    re.compile(r"\bwhy\s+(did|have)\s+you\s+(stop|stopped|paus)", re.IGNORECASE),
    re.compile(r"\byou\s+(stopped|paused|stalled)\b", re.IGNORECASE),
    re.compile(r"\bdon'?t\s+stop\b", re.IGNORECASE),
    re.compile(r"\bwhy\s+are\s+you\s+(waiting|stopping|stalling|idle)\b", re.IGNORECASE),
    re.compile(r"\bare\s+you\s+(still\s+)?(working|going|there|stuck)\b", re.IGNORECASE),
    re.compile(r"\bwhat'?s\s+(the\s+status|going\s+on|happening)\b", re.IGNORECASE),
    re.compile(r"\bwhere\s+are\s+you\s+at\b", re.IGNORECASE),
    re.compile(r"\bisn'?t\s+this\s+\w+\s+already\b", re.IGNORECASE),
]


def _task_signals(conn: sqlite3.Connection, task_id: str) -> dict:
    """Per-task measurements computed from the event stream: rework markers,
    reverts, and 'keep going' nudges."""
    rows = conn.execute(
        """
        SELECT ev.kind, ev.actor, ev.content_snippet
          FROM task_events te JOIN events ev ON ev.event_id = te.event_id
         WHERE te.task_id = ?
        """,
        (task_id,),
    ).fetchall()
    frustration = reverts = nudges = 0
    for r in rows:
        text = r["content_snippet"] or ""
        if r["kind"] == "user_message" and r["actor"] == "user":
            if any(p.search(text) for p in FRUSTRATION_MARKERS):
                frustration += 1
            if any(p.search(text) for p in NUDGE_MARKERS):
                nudges += 1
        elif r["kind"] in ("commit", "pr_opened", "pr_merged") and REVERT_RE.search(text):
            reverts += 1
    return {"frustration": frustration, "reverts": reverts, "nudges": nudges}


def _leverage(est_h, active_h) -> str:
    if not active_h or active_h <= 0:
        return "—" if not est_h else "∞"
    return f"{est_h / active_h:.1f}×"


def _task_speedups(members) -> list[float]:
    """Per-task expert ÷ labor_hours, for the tasks where both are measured.
    (labor_hours was called wall_clock_hours until 2026-09-18; calendar
    wall-clock speedup is the digest's, per method Step 7.)

    Each task's own labor is a union of its intervals and is correct, which
    is the whole reason a median over these is sound where a ratio of the sums
    is not. Tasks with no labor are dropped rather than scored infinite:
    an unmeasured task must not become the middle value.
    """
    out = []
    for r in members:
        est = r["est_human_hours"] or 0.0
        wall = r["labor_hours"] or 0.0
        if est > 0 and wall > 0:
            out.append(est / wall)
    return out


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _fmt_metric_block(
    expert_h: float,
    hands_on_h: float,
    wall_clock_h: float,
    agent_h: float,
    agent_labor_h: float | None = None,
    task_speedups: list[float] | None = None,
) -> str:
    """Canonical metric block for epic/week scopes. Same format everywhere.

    Lines (in order):
      • expert hours (est_h, METR-50% expert baseline)
      • hands-on hours (the user's modeled active time)
      • labor hours (user ∪ agent, overlap-resolved, pauses removed — called
        wall-clock hours until 2026-09-18)
      • agent hours (tool-latency-inclusive agent busy ELAPSED time)
      • agent lane hours + parallelism (only when measured — see below)
      • median expert ÷ labor across the member tasks (NOT expert ÷ Σ
        labor — see below)
      • hands-on leverage = expert / hands-on

    There is deliberately no aggregate wall-clock speedup. Summing per-task wall
    clock counts one clock minute once per live task, so on a fanned-out week the
    total exceeds the week itself -- 246.18h and 329.47h inside 168-hour weeks on
    two of the three era weeks. Dividing expert hours by that is a ratio with an
    arithmetically impossible denominator. The user's ruling on a review item
    (2026-09-15): "Stop printing aggregate speedup for weeks and projects. We can
    consider median speedup across tasks for a week. Or across tasks by project."

    The summed hours themselves are still worth printing -- they are wall-clock
    LABOR, the same distinction the repo already draws between agent_lane_hours
    (per-lane, summed) and agent_hours (merged, elapsed) -- so the line is kept
    and renamed, and only the ratio built on it is gone.

    agent labor is per-agent busy time SUMMED across concurrent lanes, where
    agent hours merges all agents into one timeline. A 16-agent workflow fan-out
    shows up here and nowhere else. It's omitted (not printed as 0) for runs that
    predate lane tagging, so a missing line never reads as "no fan-out".
    """
    lines = [
        f"- **expert hours:** {expert_h:.1f}h (METR-50%, no familiarity multiplier)",
        f"- **hands-on hours:** {hands_on_h:.2f}h (user-active: reading + typing, modeled)",
        f"- **Σ per-task labor_hours:** {wall_clock_h:.2f}h (each task's user ∪ agent time, pauses removed, ADDED UP — concurrent tasks double-count; not calendar time)",
        f"- **agent hours:** {agent_h:.2f}h (tool-latency-inclusive agent busy time, elapsed)",
    ]
    # `is not None`, not truthiness: a measured 0.0 is a real value and must not
    # be suppressed as if it were "not measured". Only None means unmeasured.
    if agent_labor_h is not None:
        par = f"{agent_labor_h / agent_h:.1f}×" if agent_h > 0 else "—"
        lines.append(
            f"- **agent lane hours:** {agent_labor_h:.2f}h "
            f"(summed across concurrent agents; parallelism {par})"
        )
    # Omitted, never printed as a placeholder, for the same reason agent labor
    # is omitted above: a missing measurement must not read as a measured value.
    med = _median(task_speedups or [])
    if med is not None:
        lines.append(
            f"- **median expert ÷ labor:** {med:.1f}× "
            f"(middle task of {len(task_speedups)}; expert ÷ labor_hours per task, "
            f"then the median — NOT a ratio of the sums above)"
        )
    lines.append(
        f"- **hands-on leverage:** {_leverage(expert_h, hands_on_h)} (expert ÷ hands-on)"
    )
    return "\n".join(lines)


# Member tasks below this est_h are collapsed into a one-line tail in the
# epic-grouped detailed view (commits, docs, tweaks — not worth their own line).
MIN_DETAIL_H = 2.0


def _truncate(s: str | None, n: int) -> str:
    s = (s or "").replace("\n", " ").replace("|", "/")
    return s if len(s) <= n else s[: n - 1] + "…"


def _query(conn: sqlite3.Connection):
    conn.row_factory = sqlite3.Row
    # labor / lane / wall resolve per schema version (persist.metric_columns).
    # NULL (not 0) when a run never measured one, so "not measured" stays
    # distinguishable from a genuinely-measured 0.0 all the way to the renderer.
    mc = metric_columns(conn)
    labor_col = mc["lane"]
    rows = conn.execute(
        f"""
        SELECT t.task_id, t.project, t.title, t.summary, t.first_ts, t.last_ts,
               t.event_count, t.user_msg_count, t.issue_refs_json, t.rule_counts_json,
               e.title AS llm_title, e.summary AS llm_summary,
               e.est_human_hours, e.uncertainty, e.bryan_domain_expertise,
               e.scope, e.depth, e.type, e.rationale, e.status,
               e.effort_breakdown, e.bryan_role_summary,
               e.what_went_well, e.problems_encountered,
               m.active_hours, m.agent_active_hours, m.resumptions,
               COALESCE({mc['labor']}, 0) AS labor_hours,
               COALESCE(m.agent_busy_hours, 0) AS agent_busy_hours,
               {labor_col} AS agent_lane_hours
          FROM tasks t
          LEFT JOIN enriched e ON e.task_id = t.task_id
          LEFT JOIN task_metrics m ON m.task_id = t.task_id
         ORDER BY COALESCE(e.est_human_hours, 0) DESC, t.event_count DESC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def _task_events(conn: sqlite3.Connection, task_id: str, limit_each: int = 5):
    first = conn.execute(
        """
        SELECT ev.ts, ev.kind, ev.actor, ev.content_snippet
          FROM task_events te JOIN events ev ON ev.event_id = te.event_id
         WHERE te.task_id = ?
         ORDER BY ev.ts ASC LIMIT ?
        """,
        (task_id, limit_each),
    ).fetchall()
    last = conn.execute(
        """
        SELECT ev.ts, ev.kind, ev.actor, ev.content_snippet
          FROM task_events te JOIN events ev ON ev.event_id = te.event_id
         WHERE te.task_id = ?
         ORDER BY ev.ts DESC LIMIT ?
        """,
        (task_id, limit_each),
    ).fetchall()
    return list(first), list(reversed(last))


def _first_sentences(text: str | None, n: int = 2, cap: int = 240) -> str:
    """Condense effort_breakdown to its first n sentences (for the table blurb)."""
    if not text:
        return ""
    text = text.replace("\n", " ").replace("|", "/").strip()
    sentences = re.split(r"(?<=[.;])\s+", text)
    blurb = " ".join(sentences[:n]).strip()
    if len(blurb) > cap:
        blurb = blurb[: cap - 1].rstrip() + "…"
    return blurb


def render_review(run_dir: Path, since: str | None = None, until: str | None = None) -> str:
    """A reading-oriented weekly review: outcome titles, est expert-hours, and
    the top effort components that drove each estimate."""
    db_path = run_dir / "run.sqlite"
    if not db_path.exists():
        raise FileNotFoundError(f"no run.sqlite at {db_path}")

    out: list[str] = []
    with sqlite3.connect(db_path) as conn:
        rows = _query(conn)
        # Only show tasks that got enriched (have an outcome title + est_h).
        rows = [r for r in rows if r["est_human_hours"] is not None]
        total_h = sum(r["est_human_hours"] or 0 for r in rows)

        win = f" ({since or '…'} → {until or '…'})" if (since or until) else ""
        out.append(f"# Weekly Review{win}")
        out.append("")
        out.append(
            f"{len(rows)} tasks · **{total_h:.0f} estimated expert-hours** total "
            "(expert-baseline; not the user's actual time). Sorted by est_h desc."
        )
        out.append("")
        out.append("## Summary")
        out.append("")
        out.append("| # | project | outcome | est_h | top effort components |")
        out.append("|---|---------|---------|-------|------------------------|")
        for i, r in enumerate(rows, 1):
            title = _truncate(r["llm_title"] or r["title"], 70)
            est = f"{r['est_human_hours']:.0f}"
            blurb = _first_sentences(r["effort_breakdown"], n=2, cap=240) or "—"
            out.append(
                f"| {i} | {_truncate(r['project'], 16)} | {title} | {est} | {blurb} |"
            )
        out.append("")
        out.append("## Detailed view")
        out.append("")
        for i, r in enumerate(rows, 1):
            title = r["llm_title"] or r["title"]
            out.append(f"### #{i} — {title}")
            out.append("")
            dims = " / ".join(filter(None, [r["scope"], r["depth"], r["type"]])) or "—"
            est = f"{r['est_human_hours']:.0f}"
            meta = (
                f"**{est} expert-hours** (uncertainty: {r['uncertainty'] or '—'}) · "
                f"project `{r['project']}` · {dims}"
            )
            if r["bryan_domain_expertise"]:
                meta += f" · expertise: {r['bryan_domain_expertise']}"
            out.append(meta)
            out.append("")
            if r["effort_breakdown"]:
                out.append("**Top effort components** (what drove the estimate):")
                out.append(f"{r['effort_breakdown'].strip()}")
                out.append("")
            if r["rationale"]:
                out.append(f"_Estimate arithmetic:_ {r['rationale'].strip()}")
                out.append("")
            work = r["llm_summary"] or r["summary"]
            if work:
                out.append("**Work done:**")
                out.append(f"{work.strip()}")
                out.append("")
            if r["bryan_role_summary"]:
                out.append(f"_The user's role:_ {r['bryan_role_summary'].strip()}")
                out.append("")
            out.append("---")
            out.append("")

    return "\n".join(out)


def render_review_epics(run_dir: Path, since: str | None = None, until: str | None = None) -> str:
    """Epic-grouped weekly review: one section per feature/outcome, with member
    atomic tasks (and their est-hours + effort components) rolled up underneath.
    Reads epics.json (written by `weekly-review --epics-llm`) for memberships."""
    db_path = run_dir / "run.sqlite"
    epics_path = run_dir / "epics.json"
    if not db_path.exists():
        raise FileNotFoundError(f"no run.sqlite at {db_path}")
    if not epics_path.exists():
        raise FileNotFoundError(
            f"no epics.json at {epics_path} — run `weekly-review --epics-llm` first"
        )

    epics = json.loads(epics_path.read_text())
    out: list[str] = []
    with sqlite3.connect(db_path) as conn:
        rows = {r["task_id"]: r for r in _query(conn)}
        signals = {tid: _task_signals(conn, tid) for tid in rows}

        def hands_on(r) -> float:
            return r["active_hours"] or 0.0

        def wall_clock(r) -> float:
            return r["labor_hours"] or 0.0

        def agent_busy(r) -> float:
            return r["agent_busy_hours"] or 0.0

        def agent_labor(r) -> float | None:
            # None = this run predates lane tagging. 0.0 = measured, genuinely zero.
            return r["agent_lane_hours"]

        def sum_labor(rs) -> float | None:
            vals = [agent_labor(r) for r in rs]
            if all(v is None for v in vals):
                return None
            return sum(v or 0.0 for v in vals)

        # Artifact detection per task (does it carry a PR/commit event?) — used
        # by the est_h double-count dedup below.
        def _has_artifact(tid: str) -> bool:
            return conn.execute(
                "SELECT 1 FROM task_events te JOIN events ev ON ev.event_id = te.event_id "
                "WHERE te.task_id = ? AND ev.kind IN ('pr_merged','pr_opened','commit') LIMIT 1",
                (tid,),
            ).fetchone() is not None

        def _epic_est_h_dedup(members) -> float:
            """Deduplicated epic est_h. A commits-only 'shipped deliverable' task
            and the conversation tasks that produced it are two VIEWS of the same
            work; naively summing double-counts (the user found this 2026-06-12 —
            Forms v1 counted 120h ship + 28h+18h+6h conversations = ~180h for
            ~120h of real work). Rule: partition members into conversation tasks
            (user_msg_count > 0) and artifact-only tasks (0 msgs, has commit/PR).
            Take max(Σconversation, Σartifact-only) — the larger VIEW, not the
            sum — plus any tasks in neither bucket. Falls back to a plain sum
            for all-conversation or all-artifact epics (no double-count there)."""
            C = [r for r in members if (r["user_msg_count"] or 0) > 0]
            A = [r for r in members if (r["user_msg_count"] or 0) == 0 and _has_artifact(r["task_id"])]
            other = [r for r in members if r not in C and r not in A]
            sumC = sum(r["est_human_hours"] or 0.0 for r in C)
            sumA = sum(r["est_human_hours"] or 0.0 for r in A)
            sumO = sum(r["est_human_hours"] or 0.0 for r in other)
            if sumC > 0 and sumA > 0:
                return max(sumC, sumA) + sumO  # both views present → dedup
            return sumC + sumA + sumO  # single view → plain sum

        # Per-epic rollups: all 6 metrics + rework/nudge signals.
        for e in epics:
            members = sorted(
                [rows[t] for t in e["member_task_ids"] if t in rows],
                key=lambda r: -(r["est_human_hours"] or 0.0),
            )
            e["_members"] = members
            e["_est_h_naive"] = sum(r["est_human_hours"] or 0.0 for r in members)
            e["_est_h"] = _epic_est_h_dedup(members)
            e["_hands_on"] = sum(hands_on(r) for r in members)
            e["_wall_clock"] = sum(wall_clock(r) for r in members)
            e["_agent_busy"] = sum(agent_busy(r) for r in members)
            e["_agent_labor"] = sum_labor(members)
            e["_rework"] = sum(
                signals[r["task_id"]]["frustration"] + signals[r["task_id"]]["reverts"]
                for r in members
            )
            e["_nudges"] = sum(signals[r["task_id"]]["nudges"] for r in members)
        epics = [e for e in epics if e["_members"]]
        epics.sort(key=lambda e: -e["_est_h"])

        total_h = sum(e["_est_h"] for e in epics)
        total_hands_on = sum(e["_hands_on"] for e in epics)
        total_wall_clock = sum(e["_wall_clock"] for e in epics)
        total_agent_busy = sum(e["_agent_busy"] for e in epics)
        _labors = [e["_agent_labor"] for e in epics]
        total_agent_labor = (
            None if all(v is None for v in _labors)
            else sum(v or 0.0 for v in _labors)
        )
        total_rework = sum(e["_rework"] for e in epics)
        total_nudges = sum(e["_nudges"] for e in epics)

        win = f" ({since or '…'} → {until or '…'})" if (since or until) else ""
        out.append(f"# Weekly Review — by feature{win}")
        out.append("")
        out.append(
            f"{len(epics)} features · **{total_h:.0f} expert-hours** of output vs "
            f"**{total_hands_on:.1f}h hands-on** (user-active) → "
            f"**{_leverage(total_h, total_hands_on)} leverage**. "
            f"{total_rework} rework/frustration signals · {total_nudges} 'keep going' nudges across the window."
        )
        out.append("")
        out.append("**Window-level metrics:**")
        out.append("")
        out.append(
            _fmt_metric_block(
                total_h, total_hands_on, total_wall_clock, total_agent_busy,
                total_agent_labor,
                task_speedups=[s for e in epics for s in _task_speedups(e["_members"])],
            )
        )
        out.append("")
        out.append("_See methodology footer for column definitions and known caveats._")
        out.append("")
        out.append("## Summary")
        out.append("")
        out.append("| # | feature / outcome | project(s) | est_h | hands-on | Σ labor | leverage | rework | nudges | top effort components |")
        out.append("|---|-------------------|-----------|-------|----------|---------|----------|--------|--------|------------------------|")
        for i, e in enumerate(epics, 1):
            title = _truncate(e["title"], 50)
            projs = _truncate("/".join(e["projects"]), 16)
            top = e["_members"][0]
            blurb = _first_sentences(top["effort_breakdown"], n=1, cap=140) or "—"
            out.append(
                f"| {i} | {title} | {projs} | {e['_est_h']:.0f} | {e['_hands_on']:.1f}h | "
                f"{e['_wall_clock']:.1f}h | "
                f"{_leverage(e['_est_h'], e['_hands_on'])} | {e['_rework'] or '·'} | "
                f"{e['_nudges'] or '·'} | {blurb} |"
            )
        out.append("")
        out.append("## Detailed view")
        out.append("")
        for i, e in enumerate(epics, 1):
            refs = (" · " + ", ".join(e["issue_refs"])) if e["issue_refs"] else ""
            out.append(f"### #{i} — {e['title']}")
            out.append("")
            out.append(
                f"{len(e['_members'])} tasks · `{'/'.join(e['projects'])}`{refs}"
            )
            out.append("")
            out.append("**Epic metrics:**")
            out.append("")
            out.append(
                _fmt_metric_block(
                    e["_est_h"], e["_hands_on"], e["_wall_clock"], e["_agent_busy"],
                    e["_agent_labor"],
                    task_speedups=_task_speedups(e["_members"]),
                )
            )
            out.append("")
            # Problem signals for this feature.
            problem_bits = []
            if e["_rework"]:
                problem_bits.append(f"**{e['_rework']} rework/frustration signals**")
            if e["_nudges"]:
                problem_bits.append(f"**{e['_nudges']} 'keep going' nudges**")
            if problem_bits:
                out.append("_Friction:_ " + " · ".join(problem_bits))
                out.append("")

            substantive = [r for r in e["_members"] if (r["est_human_hours"] or 0) >= MIN_DETAIL_H]
            tail = [r for r in e["_members"] if (r["est_human_hours"] or 0) < MIN_DETAIL_H]
            out.append("**Key tasks** (expert / hands-on / labor · outcome):")
            for r in substantive:
                est = r["est_human_hours"] or 0.0
                ho = hands_on(r)
                wc = wall_clock(r)
                title = r["llm_title"] or r["title"]
                sig = signals[r["task_id"]]
                flags = []
                if sig["frustration"] or sig["reverts"]:
                    flags.append(f"{sig['frustration'] + sig['reverts']} rework")
                if sig["nudges"]:
                    flags.append(f"{sig['nudges']} nudges")
                flag_str = f" _[{', '.join(flags)}]_" if flags else ""
                # Per-task line: just 3 metrics (expert / hands-on / labor)
                # — the full metric block lives at the epic header.
                out.append(
                    f"- **{est:.0f}h expert** / {ho:.2f}h hands-on / "
                    f"{wc:.2f}h labor · {title}{flag_str}"
                )
                # The countables the estimate came from, in FULL. This used to be
                # squeezed to a 160-char fragment on the line above, which left a
                # reviewer unable to check the number against its parts.
                comp = (r["effort_breakdown"] or "").strip()
                out.append(
                    f"  - _Breakdown:_ {comp}" if comp
                    else "  - _Breakdown: none recorded — estimate is unaudited._"
                )
                why = (r["rationale"] or "").strip()
                if why:
                    out.append(f"  - _Why {est:.0f}h:_ {why}")
            if tail:
                tail_h = sum(r["est_human_hours"] or 0 for r in tail)
                out.append(
                    f"- _+{len(tail)} smaller tasks (commits, docs, tweaks) · ~{tail_h:.0f}h total_"
                )
            out.append("")
            # What went well / what didn't, aggregated from the substantive members.
            def _collect(field):
                seen, items = set(), []
                for r in (substantive or e["_members"][:3]):
                    v = (r[field] or "").strip()
                    low = v.lower()
                    if v and low not in ("routine", "none observed", "none", "n/a") and low not in seen:
                        seen.add(low)
                        items.append((r["llm_title"] or r["title"], v))
                return items
            wins = _collect("what_went_well")
            probs = _collect("problems_encountered")
            if wins:
                out.append("**What went well:**")
                for t, v in wins[:5]:
                    out.append(f"- {v}")
                out.append("")
            if probs:
                out.append("**Problems encountered:**")
                for t, v in probs[:5]:
                    out.append(f"- {v}")
                out.append("")
            out.append("---")
            out.append("")

        # Methodology footer — same canonical definitions everywhere a reader
        # encounters one of these numbers in a weekly-review artifact.
        out.append("## Methodology — what each metric means")
        out.append("")
        out.append(
            "- **expert hours (est_h)** — METR-50% expert baseline. Time at which "
            "an expert in the relevant domain would succeed at this work with ~50% "
            "reliability. Derived from countables (new components vs tweaks, new "
            "integrations, LoC, test coverage; for research: pages read & written, "
            "# sources; for UX: pages designed, variants/screen). No domain-"
            "familiarity multiplier."
        )
        out.append(
            "- **hands-on hours** — the user's modeled active time per user message: "
            "agent prior-output read at 300 WPM + the user's typed reply at 40 WPM, "
            "capped at 20 min/turn. Captures steering + review + cleanup. "
            "Conservative — the user skims long output, so this overstates attention, "
            "which makes hands-on leverage a conservative number."
        )
        out.append(
            "- **labor hours** — union of the user and agent active intervals "
            "with overlap collapsed (sweep-line). The user intervals are the modeled "
            "reading+typing windows ending at each user message. Agent intervals "
            "are agent-event bursts stitched with ≤30-min gaps and capped at 4 h "
            "per burst (so a hung overnight tool doesn't blow up the number). "
            "Reads as \"time during which something was happening on this work.\" "
            "Called wall-clock hours until 2026-09-18; wall clock is now calendar "
            "time and lives in the digest with its speedup."
        )
        out.append(
            "- **agent hours** — sum of agent intervals (same definition as in "
            "labor above; tool-call→tool_result latency included). Reads as "
            "\"time the agent was actively driving on my behalf,\" which captures "
            "the user's lived experience of agents running for hours while he is away."
        )
        out.append(
            "- **median expert ÷ labor** — for each task, expert hours ÷ that "
            "task's own labor; this is the middle value across the tasks. It "
            "is NOT expert hours ÷ the Σ labor above: tasks run at the same "
            "time, so adding their spans counts one clock minute once per live "
            "task and the total can exceed the period itself. Per-task labor "
            "is a correct union, which is what makes the median sound. This is "
            "not the wall-clock speedup, which is (est_h / 8) ÷ (calendar hours / 24)."
        )
        out.append(
            "- **hands-on leverage** = expert hours ÷ hands-on hours. How much "
            "expert output the user got per hour of his keyboard attention."
        )
        out.append("")
        out.append(
            "**Known limits:** (1) tasks anchored only to PRs/commits with no "
            "user_message events show hands-on = 0; these are typically Slack-"
            "dispatched product-engineer runs whose transcripts aren't joined to "
            "the resulting PR. (2) Long tool calls beyond the 4h hard cap (rare) "
            "are clipped in labor and agent hours."
        )
        out.append("")

    return "\n".join(out)


def render_worksheet(run_dir: Path, since: str | None = None, until: str | None = None) -> str:
    db_path = run_dir / "run.sqlite"
    if not db_path.exists():
        raise FileNotFoundError(f"no run.sqlite at {db_path}")

    out: list[str] = []
    with sqlite3.connect(db_path) as conn:
        rows = _query(conn)

        header = f"# Labeling worksheet — run {run_dir.name}"
        if since or until:
            header += f" ({since or '…'} → {until or '…'})"
        out.append(header)
        out.append("")
        out.append(
            "**How to label:** fill `TRUTH_h` with your real hands-on hours, "
            "`SEG` = Y if the task should split or merge (explain in NOTES), "
            "`EST` = Y if the est_h is clearly wrong (note direction in NOTES)."
        )
        out.append("")
        out.append(f"Total tasks: {len(rows)}.")
        out.append("")

        # Summary table — prefers LLM-enriched title (semantically what the task
        # was), falling back to auto first-message title. The per-task block
        # headers below keep the auto title verbatim so existing thread anchors
        # on "#N — <first msg>" don't drift across regenerates.
        out.append("## Summary (sorted by est_h DESC)")
        out.append("")
        out.append("| # | project | title | est_h | active_h | agent_h | evts | first_ts | TRUTH_h | SEG | EST | NOTES |")
        out.append("|---|---------|-------|-------|----------|---------|------|----------|---------|-----|-----|-------|")
        # Pull enriched titles via a dedicated query — sqlite Row in `r` already has
        # both because the JOIN selected e.title AS... actually no, the column name
        # collided with t.title. Re-query for the LLM titles map.
        llm_title_rows = conn.execute(
            "SELECT task_id, title FROM enriched"
        ).fetchall()
        llm_title_by_id = {row["task_id"]: row["title"] for row in llm_title_rows}
        for i, r in enumerate(rows, 1):
            display_title = llm_title_by_id.get(r["task_id"]) or r["title"]
            title = _truncate(display_title, 60)
            est = f"{r['est_human_hours']:.2f}" if r["est_human_hours"] is not None else "—"
            active = f"{r['active_hours']:.2f}" if r["active_hours"] is not None else "—"
            agent = f"{r['agent_active_hours']:.2f}" if r["agent_active_hours"] is not None else "—"
            evts = str(r["event_count"] or 0)
            first_ts = (r["first_ts"] or "")[:16]
            out.append(
                f"| {i} | {_truncate(r['project'], 18)} | {title} | {est} | {active} | {agent} | {evts} | {first_ts} |  |  |  |  |"
            )
        out.append("")

        # Per-task detail
        out.append("## Per-task detail")
        out.append("")
        for i, r in enumerate(rows, 1):
            first, last = _task_events(conn, r["task_id"], 5)
            out.append(f"### #{i} — {r['title']}")
            out.append("")
            est = f"{r['est_human_hours']:.2f}" if r["est_human_hours"] is not None else "—"
            out.append(
                f"- **project**: `{r['project']}` · **task_id**: `{r['task_id']}`"
            )
            out.append(
                f"- **est_h**: {est}  (uncertainty: {r['uncertainty'] or '—'}, expertise: {r['bryan_domain_expertise'] or '—'})"
            )
            act_h = r["active_hours"] if r["active_hours"] is not None else 0.0
            agt_h = r["agent_active_hours"] if r["agent_active_hours"] is not None else 0.0
            out.append(
                f"- **active_h / agent_h**: {act_h:.2f} / {agt_h:.2f}"
                f"  (events: {r['event_count'] or 0}, resumptions: {r['resumptions'] or 0})"
            )
            out.append(
                f"- **window**: {r['first_ts'] or '—'} → {r['last_ts'] or '—'}"
            )
            scope = " / ".join(filter(None, [r["scope"], r["depth"], r["type"]]))
            if scope:
                out.append(f"- **scope/depth/type**: {scope}")
            if r["status"]:
                out.append(f"- **status**: {r['status']}")
            if r["issue_refs_json"]:
                refs = json.loads(r["issue_refs_json"]) or []
                if refs:
                    out.append(f"- **issue refs**: {', '.join(refs)}")
            if r["rule_counts_json"]:
                rc = json.loads(r["rule_counts_json"]) or {}
                if rc:
                    top = sorted(rc.items(), key=lambda kv: -kv[1])[:5]
                    out.append("- **segmentation rules**: " + ", ".join(f"{k}={v}" for k, v in top))
            if r["rationale"]:
                out.append("")
                out.append(f"  _rationale_: {_truncate(r['rationale'], 600)}")
            if r["summary"]:
                out.append("")
                out.append(f"  _summary_: {_truncate(r['summary'], 600)}")

            if first:
                out.append("")
                out.append("**First events:**")
                for ev in first:
                    out.append(
                        f"- `{ev['ts'][:19]}` {ev['actor']}/{ev['kind']}: {_truncate(ev['content_snippet'], 160)}"
                    )
            if last and last != first:
                out.append("")
                out.append("**Last events:**")
                for ev in last:
                    out.append(
                        f"- `{ev['ts'][:19]}` {ev['actor']}/{ev['kind']}: {_truncate(ev['content_snippet'], 160)}"
                    )

            out.append("")
            out.append("**Labels:**")
            out.append("- TRUTH_h: ")
            out.append("- SEG: ")
            out.append("- EST: ")
            out.append("- NOTES: ")
            out.append("")
            out.append("---")
            out.append("")

    return "\n".join(out)


def main():
    p = argparse.ArgumentParser(description="Generate the user's labeling worksheet from a run DB.")
    p.add_argument("--run-id", help="Run ID; defaults to latest")
    p.add_argument("--since", help="Display only — what window the run covered")
    p.add_argument("--until", help="Display only")
    p.add_argument("--out", required=True, help="Output markdown path")
    p.add_argument("--mode", choices=["label", "review", "review-epics"], default="label",
                   help="label = TRUTH_h/SEG/EST worksheet; review = per-task outcome report; "
                        "review-epics = features (epics) with member tasks rolled up")
    args = p.parse_args()

    mgr = RunManager()
    runs = mgr.list_runs()
    if not runs:
        print("no runs found", file=sys.stderr)
        return 2
    if args.run_id:
        match = [r for r in runs if r.run_id == args.run_id]
        if not match:
            print(f"run {args.run_id} not found", file=sys.stderr)
            return 2
        run = match[0]
    else:
        run = runs[0]

    renderer = {
        "review": render_review,
        "review-epics": render_review_epics,
        "label": render_worksheet,
    }[args.mode]
    md = renderer(run.path, since=args.since, until=args.until)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)
    print(f"wrote {args.mode} to {out} (run={run.run_id})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
