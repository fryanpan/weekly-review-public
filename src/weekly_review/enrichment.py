"""LLM enrichment for a task: title, est_human_hours, bryan_domain_expertise.

One LLM call per task. Input: task title + first/last N user-messages + artifact
list + anchored examples + expertise seed. Output: JSON with a fixed schema.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from typing import Any

from .events import Event
from .segmentation import Task, _local_day, _parse_ts


SYSTEM_PROMPT_TEMPLATE = """You are estimating personal-workflow metrics for a solo developer, the user, who works with AI coding agents.

ESTIMATE THE WORK THAT GOT DONE, NOT THE WORK THAT WAS ASKED FOR.
The evidence block has two halves. "WHAT GOT DONE" (shipped artifacts with measured diffstats, the files agents actually edited, the agents' own work log, how many sub-agents ran) is the ONLY basis for the hours. "WHAT THE USER ASKED" is context for his ROLE and for framing the goal — a one-line request that produced 40 files of working code is a BIG task, not a small one. Never size a task by how much the user typed.
COUNT EACH ARTIFACT ONCE, IN ONE TASK. If the evidence block lists artifacts under "CREDITED TO ANOTHER TASK", the build work behind them is being estimated elsewhere — they are context only. Do NOT let their diffstats into the EFFORT_BREAKDOWN countables or the hours. A task whose only owned activity is authorizing a merge or shipping an already-built change is a SMALL task (typically well under 1h), no matter how large the demoted artifact is. Likewise, a commit that is part of a listed PR is the SAME work stated twice — derive LoC counts from one or the other, never their sum.
Work delivered by dispatched sub-agents counts as work DONE. It is still output; it just wasn't done by the user's keyboard.
A COORDINATION UNIT (the evidence block says so) is a lead's day of dispatching, reviewing and merging: price it as the time a human lead would spend on that many dispatches (writing the briefs), reviews and merges, NOT by event count and NOT by the size of anything shipped — the builds and PRs it handled are priced in their own units. A day of 8 dispatches, 5 reviews and 3 merges is typically 2-4h of a lead's time.
A unit with NO ARTIFACT (no commit or PR joined to it) is still work if the log shows it — research, design, debugging, a review — but say "no artifact" in the RATIONALE and size it from the work log only.
Where the evidence block gives you MEASURED numbers (file counts, +/- lines), use them verbatim — do not re-guess them. Where it gives you nothing, say so and estimate from the work log, rather than inventing counts.

For one task (a topic the user worked on with his agents), produce the following in this order. Steps 3-5 are GROUND TRUTH counts that feed the hours estimate. DO NOT guess the hours first and back-fill counts to match.

WRITING RULES for the prose fields (GOAL, SUMMARY, RATIONALE) — these are read by a human reviewing the estimate, so they MUST stand on their own:
   - SELF-CONTAINED: a reader who knows nothing about the user's projects must understand it. Expand every acronym and ticket number — say what the thing IS, not just its label. Write "Client A's Forms feature (ticket CA-2435), which turns a photo of a paper form into a digital one" — NOT "CA-2435" or "the forms work".
   - GOAL-FRAMED: lead with the PURPOSE (the question or need that drove the work, and why it mattered), not a list of what was touched.
   - PLAIN LANGUAGE: no `[tag: ~N]` shorthand, no internal jargon, no bare file paths as explanation.

1. GOAL: 1-2 sentences — the question or need that drove this task and why it mattered. Self-contained (see rules above). This answers "why did we do this?", not "what did we touch?". Example: "Client A (a low-code tool for building service apps) needs its Forms feature to turn a phone photo of a paper form into a structured digital form; before building, the user wanted the approach written down and reviewable by the team."

2. SUMMARY: 1-2 sentences — what actually got done (shipped artifact, decision made, or current state if still open). Self-contained. Past tense for completed work, present tense for ongoing. Example: "Wrote the architecture decision plus a deep-dive on the image-to-XML vision step, consolidated the Forms design docs into one folder, and merged it as PR #1 for review."

3. DIMENSIONS — pick one per axis:
   - scope: single-file / single-feature / cross-repo / multi-source research
   - depth: surface / careful design / novel
   - type: code / research / documentation / ux design / ops

4. EFFORT_BREAKDOWN — REQUIRED FORMAT: explicit countables keyed by the canonical complexity rubric. Use the subheaders below verbatim, pick only the ones that apply to this task's type, omit subheaders for axes that don't fit, and put a concrete number after each colon (or "—" if the evidence doesn't support a count). This block IS the ground truth that step 5's hours estimate must derive from. It is an AUDIT trail, not human-facing prose (the GOAL/SUMMARY/RATIONALE fields carry the readable story). Do NOT skip the format. Single paragraph; subheaders inline, separated by " · ".
   Subheaders (use the exact labels in brackets — pick what applies):
   - Code work: `[new components: N]` · `[modified components: N]` · `[new integrations/APIs/packages: N]` · `[LoC added: N]` · `[LoC removed: N]` · `[test files added: N]` · `[test lines added: N]`
   - Research work: `[sources reviewed: N]` · `[pages read: N]` (1 page ≈ 500 words) · `[pages written: N]`
   - UX design: `[new pages/components designed: N]` · `[modified pages/components: N]` · `[variants explored per screen: N]`
   - Documentation: `[pages written: N]` · `[files touched: N]`
   - Ops: `[distinct config/infra changes: N]` · `[files touched: N]`
   After the countables, name EVERY SHIPPED ARTIFACT or DECISION the work produced, separated by "; " — not just the last one or the biggest one. A long task often ships several: a feature, the review comments answered on it, a decision doc that fed it, a large PR restacked for review. Each is work that happened and each belongs in the count above. If counts are uncertain, use "~N" — never fabricate. Example: "[new components: 3] · [modified components: 2] · [new integrations: 1 — MapTiler terrain-RGB] · [LoC added: ~470] · [test lines added: ~85] · gradient gate routing shipped in PR #161; 2 rounds of review comments answered on it; terrain-source decision settled."
   EXCLUDE REWORK from the countables. If an agent went down a wrong path, shipped work that was then thrown away or rewritten, count only what survived. Wasted work is not output, and pricing it makes a bad week look productive. Keep the wrong turn itself for PROBLEMS_ENCOUNTERED, where it belongs.

5. EST_HUMAN_HOURS — single number, the expert-domain baseline. Derive it FROM the EFFORT_BREAKDOWN at expert-domain pace. It must cover EVERYTHING the task shipped, not the last thing that shipped and not the largest. A task that ran for days usually holds several fronts; price all of them. DO NOT inflate for the user's familiarity — domain friction is captured downstream in the leverage ratio (Σ expert_h / Σ user_active_h); folding it in here would double-count. Aim for the MEDIAN (50th-percentile expert). Methodology: METR time-horizons — "hours at which an expert would succeed with ~50% reliability."

6. RATIONALE — one PLAIN-LANGUAGE sentence a human can read, naming the main work and the expert pace that yields the hours, ending in "≈ Nh". It must account for every front you priced in step 5, so a reader can tell the estimate covers the whole task rather than one piece of it. Translate the countables into words; don't just dump symbolic math. Example: "About 4 pages of architecture writing plus a vision deep-dive across 4 files, and two rounds of review answered on the same PR; an expert drafting this from a known approach ≈ 3h." (A bare "3×4h + 1×6h = 24h" is NOT acceptable — write it so the reviewer understands the basis.)

7. UNCERTAINTY: "low" | "medium" | "high".

8. BRYAN_DOMAIN_EXPERTISE: "expert" | "competent" | "learning" — METADATA only, used by post readers as a confounder annotation. Does NOT enter the hours estimate. Use the seed expertise list below plus judgment.

9. BRYAN_ROLE_SUMMARY — 1–2 sentences on what THE USER specifically did (vs the agents). Examples: provided initial spec / scoped the work / iteratively reviewed and corrected / wrote the prose himself / asked one question and let it run / debugged interactively / reviewed and merged a PR. Use user-message volume and content to infer involvement — a task with 5 user messages over 3 days is mostly hands-off; one with 200 user messages is heavy steering.

10. WHAT_WENT_WELL — 1 sentence: what worked smoothly or produced good leverage on this task (e.g. "agent one-shot the benchmark harness", "clean spec → mostly hands-off"). If nothing notable, say "routine".
11. PROBLEMS_ENCOUNTERED — 1 sentence: concrete friction beyond simple rework — wrong turns the agent took, tooling/environment failures, things that needed redoing, blockers, or repeated stalls. If none, say "none observed". (Do NOT invent problems; only what the evidence shows.)
12. TITLE — produced LAST, after you have reasoned about what shipped. <=12 words, GOAL/OUTCOME-FOCUSED and self-contained — name the shipped artifact or decision in plain words a stranger understands (expand acronyms). Past-tense verb-first when possible. Examples: "Decided how the Forms plugin turns a form photo into XML"; "Merged 4 maps PRs after fixing POI-download race"; "Picked Tesseract over PaddleOCR for paper scans"; "Debugged kid-routing gradient gate, no resolution yet."

Return ONLY valid JSON with exactly these keys (key order does not matter for JSON, but reasoning order does):
{{"goal": str, "summary": str, "dimensions": {{"scope": str, "depth": str, "type": str}}, "effort_breakdown": str, "est_human_hours": float, "rationale": str, "uncertainty": str, "bryan_domain_expertise": str, "bryan_role_summary": str, "what_went_well": str, "problems_encountered": str, "title": str}}

## the user's self-declared expertise seed
{expertise_seed}

## Anchored examples (use as calibration)
{anchors_block}
"""

ASKED_HEADER = "=== WHAT THE USER ASKED / HOW HE STEERED (context for his role — NOT the basis of the estimate) ==="


def _prompt_overrides() -> dict[str, str]:
    """Per-user replacements for the prompt text, from
    `~/.config/weekly-review/prompt_overrides.json` (keys:
    `system_prompt_template`, `asked_header`). The disk cache keys every
    reply on the exact prompt bytes, so a user whose runs were priced under an
    earlier wording keeps that wording here and keeps the cache; the source
    carries the generic text."""
    from .config import CONFIG_DIR
    path = CONFIG_DIR / "prompt_overrides.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str) and v}


@dataclass
class EnrichedTask:
    task_id: str
    title: str
    est_human_hours: float
    uncertainty: str
    bryan_domain_expertise: str
    dimensions: dict[str, str]
    rationale: str
    goal: str = ""
    summary: str = ""
    effort_breakdown: str = ""
    bryan_role_summary: str = ""
    what_went_well: str = ""
    problems_encountered: str = ""


@dataclass
class EnrichmentCall:
    """Raw trace of one LLM call — for audit and for validating estimates later."""
    task_id: str
    model: str
    prompt_system: str
    prompt_user: str
    response_raw: str
    response_parsed: dict[str, Any]
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


def _format_anchors(anchors: list[dict]) -> str:
    if not anchors:
        return "(no anchors yet — use your best judgment from METR framing)"
    lines = []
    for a in anchors:
        title = a.get("title", "?")
        hours = a.get("est_human_hours", "?")
        dims = a.get("dimensions", {})
        lines.append(f"- \"{title}\" -> {hours} hours (dims: {dims})")
    return "\n".join(lines)


def _format_expertise(expertise: dict[str, str]) -> str:
    if not expertise:
        return "(none supplied — infer from the task)"
    return "\n".join(f"- {domain}: {level}" for domain, level in expertise.items())


# Tools that CHANGE the codebase. Read/Grep/Glob don't count as "touched" — they
# are looking, not doing, and counting them would inflate the file count.
_WRITE_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}
_FILE_PATH_RE = re.compile(r"['\"]file_path['\"]\s*:\s*['\"]([^'\"]+)['\"]")


def _files_touched(task: Task, events: list[Event]) -> list[str]:
    """Distinct files the agents actually edited, from write-tool call inputs.

    tool_call.content_snippet is str(tool_input), so the path is recoverable even
    though metadata only carries tool_name.
    """
    paths: set[str] = set()
    for i in task.event_ids:
        if i >= len(events):
            continue
        ev = events[i]
        if ev.kind != "tool_call":
            continue
        if (ev.metadata or {}).get("tool_name") not in _WRITE_TOOLS:
            continue
        m = _FILE_PATH_RE.search(ev.content_snippet or "")
        if m:
            paths.add(m.group(1))
    return sorted(paths)


def _artifact_line(ev: Event) -> str:
    meta = ev.metadata or {}
    if ev.kind.startswith("pr_"):
        add = meta.get("additions") or 0
        dele = meta.get("deletions") or 0
        nf = meta.get("changedFiles") or 0
        state = ev.kind.replace("pr_", "")
        return (f"- PR #{meta.get('number')} [{state}] +{add}/-{dele} across "
                f"{nf} files: {ev.content_snippet}")
    if ev.kind == "commit":
        nf = meta.get("files_changed") or 0
        add = meta.get("insertions") or 0
        dele = meta.get("deletions") or 0
        line = (f"- commit {meta.get('short_sha')} +{add}/-{dele} across "
                f"{nf} files: {ev.content_snippet}")
        ex_files = meta.get("excluded_files") or 0
        if ex_files:
            line += (f" [plus {ex_files} files / +{meta.get('excluded_insertions') or 0} "
                     f"lines of checked-in data, EXCLUDED from the diffstat — not authored work, "
                     f"do not price]")
        return line
    return f"- {ev.kind}: {ev.content_snippet}"


def _what_got_done(
    task: Task,
    events: list[Event],
    max_messages: int,
    duplicate_artifact_ids: frozenset[int] | set[int] = frozenset(),
) -> list[str]:
    """The evidence block the hours estimate must be derived FROM.

    Estimates used to be built from the user's user messages alone — i.e. from what
    he ASKED for, not what got BUILT. Two tasks with the same request but wildly
    different delivered work scored the same. This block carries the measurable
    output: shipped artifacts with real diffstats, the files agents actually
    edited, how many agents ran, and the agents' own account of the work.

    `duplicate_artifact_ids` are event indices whose artifact identity is owned
    by ANOTHER task (see artifact_credit.py). They're listed separately as
    context the estimator must not count — this is the fix for the same PR
    receiving full est credit in every task it touched.
    """
    lines: list[str] = ["=== WHAT GOT DONE (ground truth — derive the EFFORT_BREAKDOWN countables from THIS) ==="]

    owned: list[tuple[int, Event]] = []
    demoted: list[Event] = []
    for i in task.event_ids:
        if i >= len(events) or events[i].actor != "artifact":
            continue
        if i in duplicate_artifact_ids:
            demoted.append(events[i])
        else:
            owned.append((i, events[i]))

    commits = [e for _, e in owned if e.kind == "commit"]
    pr_events = [e for _, e in owned if e.kind.startswith("pr_")]

    if owned:
        lines.append("ARTIFACTS SHIPPED:")
        for _, ev in owned:
            lines.append(_artifact_line(ev))
        # Totals count each PR ONCE even when both its opened and merged events
        # are attached here — summing per-event doubled every open+merge PR.
        pr_files = pr_add = pr_del = 0
        seen_prs: set[tuple[str, Any]] = set()
        for ev in pr_events:
            meta = ev.metadata or {}
            key = (str(meta.get("repo") or ""), meta.get("number"))
            if key in seen_prs:
                continue
            seen_prs.add(key)
            pr_files += meta.get("changedFiles") or 0
            pr_add += meta.get("additions") or 0
            pr_del += meta.get("deletions") or 0
        c_files = sum((e.metadata or {}).get("files_changed") or 0 for e in commits)
        c_add = sum((e.metadata or {}).get("insertions") or 0 for e in commits)
        c_del = sum((e.metadata or {}).get("deletions") or 0 for e in commits)
        if seen_prs and commits:
            lines.append(
                f"ARTIFACT TOTALS: {len(seen_prs)} unique PR(s) +{pr_add}/-{pr_del} across "
                f"{pr_files} files; {len(commits)} commit(s) +{c_add}/-{c_del} across "
                f"{c_files} files. These are MEASURED — use them, do not re-guess them. "
                f"A commit inside a listed PR is the SAME work stated twice: size from "
                f"the PR totals OR the commit totals, whichever covers the work, NOT their sum."
            )
        elif seen_prs:
            lines.append(
                f"ARTIFACT TOTALS: {len(seen_prs)} unique PR(s), {pr_files} file-changes, "
                f"+{pr_add}/-{pr_del} lines (MEASURED — use them, do not re-guess them)."
            )
        else:
            lines.append(
                f"ARTIFACT TOTALS: {len(commits)} commit(s), {c_files} file-changes, "
                f"+{c_add}/-{c_del} lines (MEASURED — use them, do not re-guess them)."
            )
    else:
        lines.append(
            "ARTIFACTS SHIPPED: none recorded. (No commit or PR is joined to this "
            "task — the work may still be real, e.g. research or design; size it "
            "from the work log below, and say so in the rationale.)"
        )

    if demoted:
        lines.append("")
        lines.append(
            "ARTIFACTS PRESENT BUT CREDITED TO ANOTHER TASK (context ONLY — the "
            "build work is estimated in the task that owns them; do NOT count "
            "their diffstats toward THIS task's countables or hours):"
        )
        for ev in demoted:
            lines.append(_artifact_line(ev))

    touched = _files_touched(task, events)
    if touched:
        lines.append("")
        lines.append(f"FILES THE AGENTS EDITED: {len(touched)} distinct")
        for p in touched[:20]:
            lines.append(f"  - {p}")
        if len(touched) > 20:
            lines.append(f"  ... and {len(touched) - 20} more")

    kinds: dict[str, int] = {}
    lanes: set[str] = set()
    for i in task.event_ids:
        if i >= len(events):
            continue
        ev = events[i]
        kinds[ev.kind] = kinds.get(ev.kind, 0) + 1
        lane = (ev.metadata or {}).get("agent_lane")
        if lane and lane != "main":
            lanes.add(lane)
    span = ""
    if task.first_event_ts and task.last_event_ts:
        span = (f" spanning {task.first_event_ts.isoformat()[:16]} → "
                f"{task.last_event_ts.isoformat()[:16]}")
    lines.append("")
    lines.append(f"ACTIVITY: {', '.join(f'{k}×{v}' for k, v in sorted(kinds.items()))}{span}")
    if lanes:
        lines.append(
            f"AGENT FAN-OUT: {len(lanes)} sub-agent(s) dispatched on this task — "
            f"work done in parallel by delegated agents counts as work DONE."
        )

    agent_msgs = [events[i] for i in task.event_ids
                  if i < len(events) and events[i].kind == "agent_message"
                  and (events[i].content_snippet or "").strip()]
    if agent_msgs:
        if len(agent_msgs) > max_messages:
            sample = agent_msgs[: max_messages // 2] + agent_msgs[-max_messages // 2:]
        else:
            sample = agent_msgs
        lines.append("")
        lines.append(f"AGENT WORK LOG ({len(agent_msgs)} messages, sampled):")
        for ev in sample:
            lines.append(f"- [{ev.ts}] {ev.content_snippet[:300]}")
    return lines


# A dispatch is a brief written for a new agent. SendMessage to a running
# teammate is a message, not a brief (384 of 525 "dispatch" calls on one
# week were SendMessage replies before this was separated).
_DISPATCH_TOOLS = {"Agent", "Task"}
_MESSAGE_TOOLS = {"SendMessage"}
# A lead reviews a builder's branch with git as often as with gh: 215 git
# diff/log/show calls against 363 gh-shaped ones on one week's coordination.
_REVIEW_RE = re.compile(
    r"\bgh\s+pr\s+(?:view|diff|review|checks)\b|/pulls/\d+/(?:files|reviews|comments)\b"
    r"|\bgit\s+(?:-C\s+\S+\s+)?(?:diff|log|show)\b")
_MERGE_RE = re.compile(r"\bgh\s+pr\s+merge\b|/pulls/\d+/merge\b")
_OPEN_RE = re.compile(r"\bgh\s+pr\s+create\b")


def _coordination_day(task: Task, events: list[Event]) -> list[str]:
    """The evidence block for a coordination unit: what a lead did that day,
    as counts. No diffstat, no artifact line, no work-log text — a lead's
    day is priced as a lead's time (the user, 2026-09-17; method Step 6), and
    the builds and PRs it handled are priced in their own units. Giving the
    estimator the diffs here would price them twice."""
    dispatch_calls = dispatch_inputs = reviews = merges = opened = board = messages = agent_msgs = 0
    lanes: set[str] = set()
    days: set[str] = set()
    for i in task.event_ids:
        if i >= len(events):
            continue
        ev = events[i]
        md = ev.metadata or {}
        # The unit is cut per local day; count its days the same way.
        days.add(_local_day(_parse_ts(ev.ts)))
        lane = md.get("agent_lane")
        if lane and lane != "main":
            lanes.add(lane)
        if ev.kind == "subagent_input":
            dispatch_inputs += 1
        elif ev.kind == "agent_message":
            agent_msgs += 1
        elif ev.kind == "tool_call":
            name = str(md.get("tool_name") or "")
            text = ev.content_snippet or ""
            if name in _DISPATCH_TOOLS:
                dispatch_calls += 1
            elif md.get("card") or "workspaces" in name:
                board += 1
            elif name in _MESSAGE_TOOLS or "send_message" in name:
                messages += 1
            elif _MERGE_RE.search(text):
                merges += 1
            elif _OPEN_RE.search(text):
                opened += 1
            elif _REVIEW_RE.search(text):
                reviews += 1
    # A folded lane brings its subagent_input into the unit while the parent's
    # Agent call is already here: the same dispatch twice. The larger of the
    # two counts is the number of briefs written.
    dispatches = max(dispatch_calls, dispatch_inputs)
    span = ""
    if task.first_event_ts and task.last_event_ts:
        span = (f" spanning {task.first_event_ts.isoformat()[:16]} → "
                f"{task.last_event_ts.isoformat()[:16]}")
    return [
        "=== COORDINATION UNIT (a lead's day — price it as a lead's TIME, not by event count) ===",
        f"A human lead handled: {dispatches} dispatches (briefs written for agents), {reviews} PR reviews, "
        f"{merges} merges, {opened} PRs opened, {board} board actions, {messages} messages to other sessions, "
        f"over {len(days)} day(s){span}.",
        f"ACTIVITY: {agent_msgs} agent messages; {len(lanes)} agent lane(s) touched.",
        "The PRs and builds this coordination handled are priced in their OWN units and must not be "
        "priced here. No diff or artifact is listed on purpose.",
    ]


def _task_summary(
    task: Task,
    events: list[Event],
    max_messages: int = 8,
    duplicate_artifact_ids: frozenset[int] | set[int] = frozenset(),
    sibling_lines: list[str] | None = None,
) -> str:
    attached_users = [events[i] for i in task.event_ids
                      if i < len(events) and events[i].kind == "user_message"]
    if len(attached_users) > max_messages:
        sample = attached_users[: max_messages // 2] + attached_users[-max_messages // 2:]
    else:
        sample = attached_users

    lines = [f"PROJECT: {task.project}", f"CURRENT TITLE (auto-derived): {task.title}"]
    if task.issue_refs:
        lines.append(f"ISSUE/PR REFS: {', '.join(sorted(task.issue_refs))}")
    lines.append("")

    # What got DONE comes first, and drives the estimate. A coordination unit
    # is described as a lead's day in counts; its diffs and logs stay out.
    if task.unit_kind == "coordination":
        lines.extend(_coordination_day(task, events))
    else:
        lines.extend(_what_got_done(task, events, max_messages, duplicate_artifact_ids))

    # Concurrent slices of the same workstream, each priced independently.
    # A coordination unit spans the day and overlaps every lane, and the
    # owner/floor wording would tell it to price artifacts it does not list.
    if task.unit_kind != "coordination":
        lines.extend(sibling_lines or [])

    # What was ASKED comes second — it is context for the user's ROLE and for the
    # goal framing, NOT the basis of the hours estimate.
    lines.append("")
    lines.append(_prompt_overrides().get("asked_header") or ASKED_HEADER)
    if sample:
        lines.append(f"USER MESSAGES ({len(attached_users)} total, sampled):")
        for ev in sample:
            lines.append(f"- [{ev.ts}] {ev.content_snippet}")
    else:
        lines.append(
            "NONE — delegated task: the agent worked autonomously, steered "
            "externally. Size it entirely from what got done above."
        )

    return "\n".join(lines)


def build_enrichment_prompt(
    task: Task,
    events: list[Event],
    anchors: list[dict],
    expertise_seed: dict[str, str],
    duplicate_artifact_ids: frozenset[int] | set[int] = frozenset(),
    sibling_lines: list[str] | None = None,
) -> tuple[str, str]:
    fields = dict(
        expertise_seed=_format_expertise(expertise_seed),
        anchors_block=_format_anchors(anchors),
    )
    template = _prompt_overrides().get("system_prompt_template")
    system = None
    if template:
        # An override built from a rendered prompt (single braces around the
        # JSON schema) rather than from the source literal (`{{`) breaks
        # str.format; that is a bad override, not a reason to fail the run.
        try:
            system = template.format(**fields)
        except (KeyError, IndexError, ValueError):
            print("warning: prompt_overrides.json system_prompt_template does not "
                  "format (unescaped braces?); using the built-in template", file=sys.stderr)
    if system is None:
        system = SYSTEM_PROMPT_TEMPLATE.format(**fields)
    user = _task_summary(
        task, events,
        duplicate_artifact_ids=duplicate_artifact_ids,
        sibling_lines=sibling_lines,
    )
    return system, user


_FIELD_STR_RE = re.compile(r'"(\w+)"\s*:\s*"(.*?)"\s*(?=,\s*"\w+"\s*:|\}\s*$)', re.DOTALL)
_FIELD_NUM_RE = re.compile(r'"(\w+)"\s*:\s*(-?[0-9]+(?:\.[0-9]+)?)')


def _salvage_json(blob: str) -> dict[str, Any]:
    """Best-effort field recovery from JSON the parser rejected.

    A single unescaped quote or newline in one prose field used to discard the
    WHOLE task: it got no enriched row, and the only trace was one warning line
    among thousands. Three tasks vanished that way in the 2026-07-28 verify
    runs — one of them 28h, which silently accounted for ~40% of that week's
    apparent improvement. A partial record beats a missing task, so pull the
    fields out individually and let the estimate survive its own prose.
    """
    out: dict[str, Any] = {}
    for k, v in _FIELD_STR_RE.findall(blob):
        out[k] = v.replace('\\"', '"').replace("\\n", "\n")
    for k, v in _FIELD_NUM_RE.findall(blob):
        if k not in out:
            try:
                out[k] = float(v)
            except ValueError:
                continue
    if "est_human_hours" not in out:
        raise ValueError("salvage failed: no est_human_hours recoverable")
    return out


def _last_complete_estimate(text: str) -> dict[str, Any] | None:
    """The last well-formed object in the reply that carries an estimate.

    The model sometimes sketches the shape first (`{"goal": "...", ...}`) and
    writes the real object below it. Parsing first brace to last brace spans
    both, fails, and the salvage then stitched fields from the sketch into the
    answer: 15 tasks, 7% of estimated output, lost their type and one rendered
    a code fence as its goal (run-20260914T202524Z).
    """
    decoder = json.JSONDecoder(strict=False)
    found = None
    i = text.find("{")
    while i != -1:
        try:
            obj, end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            i = text.find("{", i + 1)
            continue
        if isinstance(obj, dict) and "est_human_hours" in obj:
            found = obj
            i = text.find("{", end)
        else:
            i = text.find("{", i + 1)
    return found


def _extract_json(text: str) -> dict[str, Any]:
    start = text.find("{")
    if start == -1:
        raise ValueError(f"LLM response did not contain JSON: {text!r}")
    whole = _last_complete_estimate(text)
    if whole is not None:
        return whole
    end = text.rfind("}")
    if end > start:
        blob = text[start:end + 1]
        try:
            return json.loads(blob)
        except json.JSONDecodeError:
            pass
        try:
            # strict=False tolerates literal control characters inside strings,
            # which is the most common malformation.
            return json.loads(blob, strict=False)
        except json.JSONDecodeError:
            pass
    # Salvage from everything after the opening brace, not up to the last `}`.
    # A reply truncated at max_tokens never closes its root object, so its last
    # `}` closes a nested one (`dimensions`) that can sit BEFORE the estimate.
    return _salvage_json(text[start:])


# The first budget stays at 1500 because it is part of every cached reply's key:
# raising it would re-ask (and re-bill) every task. On 2026-09-14 all 10
# unparseable cached replies had stopped at exactly this cap.
ENRICH_MAX_TOKENS = 1500
ENRICH_RETRY_MAX_TOKENS = 4000


def enrich_task(
    task: Task,
    events: list[Event],
    anchors: list[dict],
    expertise_seed: dict[str, str],
    llm,
    model: str = "claude-sonnet-4-6",
    duplicate_artifact_ids: frozenset[int] | set[int] = frozenset(),
    sibling_lines: list[str] | None = None,
) -> tuple[EnrichedTask, EnrichmentCall]:
    """Enrich one task. Returns (enriched, call_trace).

    The call trace captures the exact prompt + raw response so estimates can be
    re-audited later — prompts depend on mutable inputs (anchors, expertise,
    event snippets), so re-deriving them isn't enough.

    `duplicate_artifact_ids`: event indices whose artifact identity is owned by
    another task (artifact_credit.py) — listed in the prompt as context that
    must not count toward the hours.
    """
    system, user = build_enrichment_prompt(
        task, events, anchors, expertise_seed,
        duplicate_artifact_ids=duplicate_artifact_ids,
        sibling_lines=sibling_lines,
    )
    result = llm.complete(system=system, user=user, model=model, max_tokens=ENRICH_MAX_TOKENS, temperature=0.0)
    raw = result["text"].strip()
    try:
        obj = _extract_json(raw)
    except ValueError:
        if result.get("stop_reason") != "max_tokens":
            raise
        obj = None
    # A reply the budget cut off is retried ONCE with more room, even when an
    # estimate could be salvaged from it: the salvage has the hours but has lost
    # whatever came after the cut (type, breakdown, rationale). Any other parse
    # failure still raises. The retry has its own cache key, so every reply that
    # finished keeps its cached answer and costs nothing.
    if result.get("stop_reason") == "max_tokens":
        retry = llm.complete(system=system, user=user, model=model,
                             max_tokens=ENRICH_RETRY_MAX_TOKENS, temperature=0.0)
        try:
            obj = _extract_json(retry["text"].strip())
            result, raw = retry, retry["text"].strip()
        except ValueError:
            if obj is None:
                raise

    enriched = EnrichedTask(
        task_id=task.task_id,
        title=obj.get("title", task.title),
        est_human_hours=float(obj.get("est_human_hours", 0.0)),
        uncertainty=obj.get("uncertainty", "high"),
        bryan_domain_expertise=obj.get("bryan_domain_expertise", "competent"),
        dimensions=obj.get("dimensions", {}),
        rationale=obj.get("rationale", ""),
        goal=obj.get("goal", ""),
        summary=obj.get("summary", ""),
        effort_breakdown=obj.get("effort_breakdown", ""),
        bryan_role_summary=obj.get("bryan_role_summary", ""),
        what_went_well=obj.get("what_went_well", ""),
        problems_encountered=obj.get("problems_encountered", ""),
    )
    usage = result.get("usage", {}) or {}
    call = EnrichmentCall(
        task_id=task.task_id,
        model=model,
        prompt_system=system,
        prompt_user=user,
        response_raw=raw,
        response_parsed=obj,
        input_tokens=int(usage.get("input_tokens", 0) or 0),
        output_tokens=int(usage.get("output_tokens", 0) or 0),
        cache_read_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
        cache_creation_tokens=int(usage.get("cache_creation_input_tokens", 0) or 0),
    )
    return enriched, call
