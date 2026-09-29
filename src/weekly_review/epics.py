"""Epic rollup layer.

The segmenter produces ATOMIC tasks — one coherent sitting each (mean span
~0.8 days). That granularity is correct for measuring effort. But a human
reviewing a week thinks in FEATURES / OUTCOMES that span multiple sittings:
"the Maps plugin work" is really 10 atomic tasks across two weeks.

This module is an ADDITIVE grouping layer on top of atomic tasks. It does NOT
change segmentation — every atomic task is preserved and assigned to exactly
one epic (possibly a degenerate one-task epic). Epics let major features pop
out instead of being scattered across 10 rows.

Clustering signals, in priority order:

  1. Shared issue ref — atomic tasks that share a Linear/GitHub ticket
     (CA-2435, CA-2436, ...) belong to the same epic. Strongest signal.
     Issue refs are specific enough to cross project boundaries safely.

  2. Same project + topical token overlap — for tasks with NO issue ref,
     cluster within the same project when title/content token Jaccard clears
     a threshold AND they fall within EPIC_WINDOW_DAYS. NEVER crosses projects
     (generic tokens like "fix bug" overlap across unrelated projects). The
     `unknown` project is a heterogeneous sink — no topical clustering there.

  3. Shared shipped-artifact lineage — atomic tasks whose PRs/commits share a
     non-generic conventional-commit scope (respecting GENERIC_SCOPE_STOPWORDS)
     belong together. Same-project only.

Connected components over these signals (union-find) are epics. A singleton is
its own degenerate epic.

Guardrail (learn from a prior failure): a previous merge experiment collapsed
everything into one 82k-event mega-cluster because issue-ref pollution (hex
colors, biomedical codes) created bogus links. The regex is tightened now, but
we STILL cap epic size and refuse to merge components that look pathological —
too many members, or spanning the whole window across too many projects.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .enrichment import _extract_json
from .events import Event
from .segmentation import (
    GENERIC_SCOPE_STOPWORDS,
    Task,
    _conv_scope,
    _issue_refs,
    _tokens,
)

logger = logging.getLogger(__name__)


# --- tuning knobs ---

# Topical (Rule 2) thresholds. Higher than the segmenter's intra-sitting merge
# threshold (0.30) because epics group *across* sittings — we want a strong,
# unambiguous topical signal to link two separate sittings into one feature.
EPIC_JACCARD_THRESHOLD = float(os.environ.get("EPIC_JACCARD_THRESHOLD", "0.18"))
EPIC_MIN_SHARED_TOKENS = 2
# Alternative to Jaccard for longer topic_text (title+summary): if two tasks
# share at least this many significant tokens, link them regardless of Jaccard.
# Jaccard underweights overlap when the two token sets differ a lot in size
# (a terse title vs a rich summary), so absolute shared-count is the more
# robust same-feature signal once we cluster on enriched text.
EPIC_MIN_SHARED_TOKENS_STRONG = int(os.environ.get("EPIC_MIN_SHARED_TOKENS_STRONG", "4"))
# Project-level token down-weighting: a token appearing in >= this fraction of a
# project's tasks is generic project vocabulary, not a topic handle — drop it
# from the topical-link signal so unrelated features in a busy project don't
# bridge on boilerplate.
EPIC_PROJECT_DF_FRACTION = float(os.environ.get("EPIC_PROJECT_DF_FRACTION", "0.33"))
EPIC_PROJECT_DF_MIN_TASKS = 6  # below this, too few tasks to estimate DF reliably

# Epics can span the whole review window, unlike atomic tasks. Cap at 14 days
# so a project's entire history doesn't fuse purely on topical drift.
EPIC_WINDOW_DAYS = 14

# Guardrail caps. A component exceeding either of these is treated as bad
# clustering: log a warning and DON'T merge it — leave its members as separate
# (singleton-or-smaller) epics.
EPIC_MAX_MEMBERS = 25
EPIC_MAX_PROJECTS_WHEN_WINDOW_SPANNING = 5
# "Basically the whole window" — a component whose span covers this fraction of
# the observed task time-range counts as window-spanning for the project cap.
EPIC_WINDOW_SPAN_FRACTION = 0.9

# The heterogeneous sink project: issue-ref clustering within it is fine, but
# topical token clustering is not (too many unrelated atomic tasks share generic
# tokens there).
UNKNOWN_PROJECT = "unknown"

_DT_MIN = datetime.min.replace(tzinfo=timezone.utc)
_DT_MAX = datetime.max.replace(tzinfo=timezone.utc)


@dataclass
class Epic:
    epic_id: str
    title: str
    projects: set[str] = field(default_factory=set)
    member_task_ids: list[str] = field(default_factory=list)
    total_events: int = 0
    total_user_messages: int = 0
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    issue_refs: set[str] = field(default_factory=set)
    scopes: set[str] = field(default_factory=set)
    # How this epic's members got linked (for stats / debugging). Empty for a
    # singleton.
    link_reasons: set[str] = field(default_factory=set)

    @property
    def member_count(self) -> int:
        return len(self.member_task_ids)

    @property
    def span_days(self) -> float:
        if self.first_ts is None or self.last_ts is None:
            return 0.0
        return (self.last_ts - self.first_ts).total_seconds() / 86400.0


# --- per-task signal extraction ---


@dataclass
class _TaskSig:
    task_id: str
    project: str
    issue_refs: set[str]
    scopes: set[str]
    title_tokens: set[str]
    n_events: int
    n_user_messages: int
    first_ts: datetime | None
    last_ts: datetime | None
    # The single ref this task is assigned to for issue-ref clustering. Set by
    # roll_up_epics after counting ref frequency across the corpus. A task that
    # carries multiple refs is assigned to its DOMINANT ref (the one tagging the
    # most tasks) so it can't act as a transitive bridge fusing two otherwise-
    # disjoint ticket families. None if the task carries no issue ref.
    dominant_ref: str | None = None


def _task_user_message_count(task: Task, events: list[Event]) -> int:
    n = 0
    for idx in task.event_ids:
        if idx < len(events) and events[idx].kind == "user_message":
            n += 1
    return n


def _task_signature(task: Task, events: list[Event], topic_text: str | None = None) -> _TaskSig:
    """Collect epic-clustering evidence for one atomic task.

    issue_refs / scopes are gathered from the task's own field AND from the
    titles of any attached PR/commit artifacts (the "what shipped" lineage).
    title_tokens are the topical handle: prefer `topic_text` (the LLM-enriched
    outcome title + summary, which carries real topic words) over the task's
    conversational first-message title, which shares ~no tokens across sittings.
    """
    issue_refs: set[str] = set(task.issue_refs)
    scopes: set[str] = set()
    n_user = 0

    for idx in task.event_ids:
        if idx >= len(events):
            continue
        ev = events[idx]
        if ev.kind == "user_message":
            n_user += 1
        if ev.kind in ("pr_merged", "pr_opened", "commit"):
            snippet = ev.content_snippet or ""
            first_line = snippet.split("\n", 1)[0]
            scope = _conv_scope(snippet)
            if scope:
                scopes.add(scope)
            issue_refs |= _issue_refs(first_line)

    return _TaskSig(
        task_id=task.task_id,
        project=task.project,
        issue_refs=issue_refs,
        scopes=scopes,
        title_tokens=_tokens(topic_text if topic_text else task.title),
        n_events=len(task.event_ids),
        n_user_messages=n_user,
        first_ts=task.first_event_ts,
        last_ts=task.last_event_ts,
    )


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def _within_window(a: _TaskSig, b: _TaskSig, days: int) -> bool:
    """True if the two tasks' time ranges are within `days` of each other."""
    a_first = a.first_ts or a.last_ts
    a_last = a.last_ts or a.first_ts
    b_first = b.first_ts or b.last_ts
    b_last = b.last_ts or b.first_ts
    if a_first is None or b_first is None:
        return True  # no timing evidence — don't block on the window
    earlier_last = min(a_last, b_last)
    later_first = max(a_first, b_first)
    gap = (later_first - earlier_last).total_seconds() / 86400.0
    return gap <= days


# --- union-find ---


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, i: int, j: int) -> None:
        ri, rj = self.find(i), self.find(j)
        if ri != rj:
            self.parent[max(ri, rj)] = min(ri, rj)


# --- linking ---


def _link_pair(a: _TaskSig, b: _TaskSig) -> str | None:
    """Return a reason string if a and b should be linked, else None.

    Signals in priority order. Issue-ref is the only cross-project signal;
    topical tokens and shared scopes are same-project only.
    """
    # Signal 1: shared DOMINANT issue ref — strongest, allowed cross-project.
    # We compare dominant refs (not the full ref sets) so a planning task that
    # cites two tickets joins only its dominant ticket family and never fuses
    # two distinct features into one mega-epic. A ticket is specific enough to
    # cross project boundaries, so this link ignores project equality.
    if a.dominant_ref is not None and a.dominant_ref == b.dominant_ref:
        return f"issue_ref={a.dominant_ref}"

    # Hard split: two tasks that each belong to a DIFFERENT ticket family are
    # different features by definition — never link them, even on heavy token
    # overlap. (Without this, CA-2435 forms work and CA-2436 maps work fuse
    # via shared "maps/plugin/app" tokens into one mega-epic.)
    if a.dominant_ref is not None and b.dominant_ref is not None and a.dominant_ref != b.dominant_ref:
        return None

    # Everything below requires the same project.
    if a.project != b.project:
        return None

    # Signal 3: shared non-generic conv-commit scope (shipped-artifact lineage).
    shared_scopes = (a.scopes & b.scopes) - GENERIC_SCOPE_STOPWORDS
    if shared_scopes:
        return f"scope={','.join(sorted(shared_scopes))}"

    # Signal 2: topical token overlap (NOT in the unknown sink).
    if a.project == UNKNOWN_PROJECT:
        return None
    shared_tokens = a.title_tokens & b.title_tokens
    n_shared = len(shared_tokens)
    sim = _jaccard(a.title_tokens, b.title_tokens)
    # Link if EITHER the Jaccard bar is cleared OR there's a strong absolute
    # overlap (robust when title and summary token-set sizes differ a lot).
    if n_shared >= EPIC_MIN_SHARED_TOKENS and sim >= EPIC_JACCARD_THRESHOLD:
        return f"topical_jaccard={sim:.2f}"
    if n_shared >= EPIC_MIN_SHARED_TOKENS_STRONG:
        return f"topical_shared={n_shared}"

    return None


def _component_projects(members: list[_TaskSig]) -> set[str]:
    return {m.project for m in members}


def _component_span_days(members: list[_TaskSig]) -> float:
    firsts = [m.first_ts for m in members if m.first_ts is not None]
    lasts = [m.last_ts for m in members if m.last_ts is not None]
    if not firsts or not lasts:
        return 0.0
    return (max(lasts) - min(firsts)).total_seconds() / 86400.0


def _is_pathological(members: list[_TaskSig], observed_span_days: float) -> str | None:
    """Return a warning string if this component looks like bad clustering.

    Two trip conditions (from the prior 82k-event mega-cluster failure):
      - too many member tasks (> EPIC_MAX_MEMBERS), OR
      - spans basically the whole observed window AND touches > N distinct
        projects (a real feature lives in one or two projects).
    """
    if len(members) > EPIC_MAX_MEMBERS:
        return (
            f"{len(members)} members exceeds cap {EPIC_MAX_MEMBERS}"
        )
    projects = _component_projects(members)
    span = _component_span_days(members)
    window_spanning = (
        observed_span_days > 0
        and span >= observed_span_days * EPIC_WINDOW_SPAN_FRACTION
    )
    if window_spanning and len(projects) > EPIC_MAX_PROJECTS_WHEN_WINDOW_SPANNING:
        return (
            f"spans {span:.1f}d (~whole window) across {len(projects)} projects "
            f"({', '.join(sorted(projects))})"
        )
    return None


def _epic_title(
    members: list[_TaskSig],
    tasks_by_id: dict[str, Task],
    enriched_titles: dict[str, str] | None = None,
) -> str:
    """Derive an epic title from the highest-est-output member.

    Prefer the LLM-enriched outcome title of the lead member (the busiest one,
    or the busiest carrying the dominant ref) — that's a clean feature handle
    like "Shipped Forms v4 benchmark...", not the conversational first-message
    title. Prefix the dominant issue ref when present.
    """
    def lead_title(tid: str) -> str:
        if enriched_titles and enriched_titles.get(tid):
            return enriched_titles[tid]
        t = tasks_by_id.get(tid)
        return t.title if t else ""

    ref_counts: dict[str, int] = {}
    for m in members:
        for ref in m.issue_refs:
            ref_counts[ref] = ref_counts.get(ref, 0) + 1
    if ref_counts:
        dominant = max(sorted(ref_counts), key=lambda r: ref_counts[r])
        carriers = [m for m in members if dominant in m.issue_refs]
        lead = max(carriers, key=lambda m: m.n_events)
        return f"{dominant}: {lead_title(lead.task_id)}".strip().rstrip(":")
    lead = max(members, key=lambda m: m.n_events)
    return lead_title(lead.task_id) or "(untitled epic)"


def _epic_id(members: list[_TaskSig]) -> str:
    """Stable id: prefix `epic-` + the smallest member task_id (sorted)."""
    ids = sorted(m.task_id for m in members)
    return f"epic-{ids[0]}"


def _build_epic(
    members: list[_TaskSig],
    tasks_by_id: dict[str, Task],
    reasons: set[str],
    enriched_titles: dict[str, str] | None = None,
) -> Epic:
    epic = Epic(
        epic_id=_epic_id(members),
        title=_epic_title(members, tasks_by_id, enriched_titles),
    )
    for m in members:
        epic.member_task_ids.append(m.task_id)
        epic.projects.add(m.project)
        epic.total_events += m.n_events
        epic.total_user_messages += m.n_user_messages
        epic.issue_refs |= m.issue_refs
        epic.scopes |= m.scopes
        if m.first_ts is not None and (epic.first_ts is None or m.first_ts < epic.first_ts):
            epic.first_ts = m.first_ts
        if m.last_ts is not None and (epic.last_ts is None or m.last_ts > epic.last_ts):
            epic.last_ts = m.last_ts
    epic.link_reasons = set(reasons)
    # Stable member ordering: chronological by first_ts.
    sig_by_id = {m.task_id: m for m in members}
    epic.member_task_ids.sort(
        key=lambda tid: sig_by_id[tid].first_ts or _DT_MAX
    )
    return epic


EPIC_CLUSTER_SYSTEM = """You group a developer's atomic work-sessions into EPICS.

An atomic work-session is one coherent sitting (~hours). An EPIC is a feature,
deliverable, or outcome that spans multiple sessions — e.g. "ship the maps
plugin end-to-end" might be 8 sessions across two weeks.

You are given a numbered list of sessions: index, project, an outcome-focused
title, and any issue refs. Group them into epics.

Rules:
- Prefer grouping within the same project. Only group across projects when the
  title content makes it unmistakable they're the same deliverable.
- A session that's clearly standalone is its own epic (a one-session epic is
  fine — don't force-merge).
- Group by the WORK/OUTCOME, not by surface word overlap. "Fixed maps region
  selection" and "documented maps PMTiles slicing" are the same maps epic;
  "fixed maps bug" and "fixed dashboard bug" are NOT (different deliverables).
- Every session index must appear in exactly one epic.
- Give each epic a short outcome-focused title (<=10 words).

Return ONLY valid JSON: {"epics": [{"title": str, "members": [int, ...]}, ...]}"""


def roll_up_epics_llm(
    tasks: list[Task],
    enriched: dict,
    events: list[Event],
    llm,
    model: str = "claude-sonnet-4-6",
) -> list[Epic]:
    """Cluster atomic tasks into epics via one LLM pass over enriched titles.

    Mechanical signals (issue-ref, scope, token overlap) are too sparse to
    recover semantic features — most work never cites its ticket and atomic
    titles are conversational openers. The enriched (outcome-focused) titles
    DO carry the topic, so we let the model group on those. Blind to any
    expected-epic list. Falls back to roll_up_epics() if the LLM call fails.
    """
    if not tasks:
        return []

    tasks_by_id = {t.task_id: t for t in tasks}
    sigs = [_task_signature(t, events) for t in tasks]

    lines = []
    for i, t in enumerate(tasks):
        et = enriched.get(t.task_id)
        title = (getattr(et, "title", None) or t.title).replace("\n", " ")[:100]
        refs = ",".join(sorted(t.issue_refs)) or "-"
        lines.append(f"{i}\t{t.project}\t{title}\t{refs}")
    listing = "\n".join(lines)

    user = (
        f"{len(tasks)} atomic work-sessions (index, project, title, issue_refs):\n\n"
        f"{listing}\n\nGroup them into epics. Return the JSON described."
    )
    try:
        result = llm.complete(
            system=EPIC_CLUSTER_SYSTEM, user=user, model=model,
            max_tokens=16384, temperature=0.0,
        )
        obj = _extract_json(result["text"])
        groups = obj.get("epics", [])
    except Exception:
        return roll_up_epics(tasks, events)

    sig_by_idx = {i: sigs[i] for i in range(len(sigs))}
    assigned: set[int] = set()
    epics: list[Epic] = []
    for g in groups:
        members_idx = [i for i in g.get("members", []) if isinstance(i, int) and 0 <= i < len(sigs)]
        members_idx = [i for i in members_idx if i not in assigned]
        if not members_idx:
            continue
        assigned.update(members_idx)
        members = [sig_by_idx[i] for i in members_idx]
        epic = _build_epic(members, tasks_by_id, reasons={"llm_semantic"})
        llm_title = (g.get("title") or "").strip()
        if llm_title:
            epic.title = llm_title
        epics.append(epic)

    # Any session the LLM dropped becomes its own singleton epic — invariant:
    # every task lands in exactly one epic.
    for i in range(len(sigs)):
        if i not in assigned:
            epics.append(_build_epic([sigs[i]], tasks_by_id, reasons=set()))

    epics.sort(key=lambda e: e.total_events, reverse=True)
    return epics


def roll_up_epics(
    tasks: list[Task],
    events: list[Event],
    enriched: dict | None = None,
) -> list[Epic]:
    """Group atomic tasks into epics via connected-components over signals.

    Fully deterministic: union-find over shared issue-ref (dominant), shared
    non-generic conv-commit scope, and topical token-Jaccard. When `enriched`
    is provided (task_id -> object with .title/.summary), topical tokens come
    from the LLM outcome-title + summary rather than the conversational
    first-message title — that's what makes same-feature sittings link reliably
    AND reproducibly (no LLM call at clustering time).

    Atomic tasks are never modified. Returns one Epic per connected component;
    a task with no links is its own (degenerate) epic. Components that trip the
    size/sprawl guardrail are NOT merged — their members become singleton epics
    instead — and a warning is logged. Output is sorted by total_events desc.
    """
    if not tasks:
        return []

    def _topic_text(tid: str) -> str | None:
        if not enriched:
            return None
        et = enriched.get(tid)
        if et is None:
            return None
        title = getattr(et, "title", "") or ""
        summary = getattr(et, "summary", "") or ""
        return f"{title} {summary}".strip() or None

    enriched_titles = {}
    if enriched:
        for tid, et in enriched.items():
            t = getattr(et, "title", "") or ""
            if t:
                enriched_titles[tid] = t

    tasks_by_id = {t.task_id: t for t in tasks}
    sigs = [_task_signature(t, events, topic_text=_topic_text(t.task_id)) for t in tasks]
    n = len(sigs)

    # Project-level token down-weighting (TF/DF). Within a busy multi-feature
    # project (e.g. client-a = forms + maps + xkcd), tasks share generic
    # project vocabulary — "shipped", "plugin", "v1", "mockup", "decisions" —
    # which token-bridges unrelated features into one mega-epic. Drop tokens
    # that appear in >= EPIC_PROJECT_DF_FRACTION of a project's tasks from the
    # topical-link signal, so only DISCRIMINATIVE tokens (pmtiles, maplibre,
    # region / benchmark, detector, ocr) count. Issue-ref and scope links are
    # unaffected (they don't use title_tokens).
    proj_task_counts: dict[str, int] = {}
    proj_token_df: dict[str, dict[str, int]] = {}
    for s in sigs:
        proj_task_counts[s.project] = proj_task_counts.get(s.project, 0) + 1
        df = proj_token_df.setdefault(s.project, {})
        for tok in s.title_tokens:
            df[tok] = df.get(tok, 0) + 1
    for s in sigs:
        total = proj_task_counts.get(s.project, 1)
        if total < EPIC_PROJECT_DF_MIN_TASKS:
            continue  # too few tasks to estimate DF; keep all tokens
        df = proj_token_df.get(s.project, {})
        cutoff = total * EPIC_PROJECT_DF_FRACTION
        discriminative = {tok for tok in s.title_tokens if df.get(tok, 0) < cutoff}
        s.title_tokens = discriminative

    # Assign each task a single DOMINANT issue ref. Corpus-wide ref frequency
    # decides dominance: a task carrying {CA-2435, CA-2436} joins whichever
    # ticket tags more tasks overall, so a multi-ref planning note doesn't
    # transitively merge two distinct ticket families. Tie-break: more tasks
    # wins, then alphabetical for determinism.
    ref_counts: dict[str, int] = {}
    for s in sigs:
        for ref in s.issue_refs:
            ref_counts[ref] = ref_counts.get(ref, 0) + 1
    for s in sigs:
        if s.issue_refs:
            s.dominant_ref = max(
                sorted(s.issue_refs), key=lambda r: ref_counts.get(r, 0)
            )

    # Observed task time-range — used by the guardrail's "whole window" check.
    firsts = [s.first_ts for s in sigs if s.first_ts is not None]
    lasts = [s.last_ts for s in sigs if s.last_ts is not None]
    observed_span_days = (
        (max(lasts) - min(firsts)).total_seconds() / 86400.0
        if firsts and lasts else 0.0
    )

    uf = _UnionFind(n)
    # reason recorded per linked pair, keyed by (i, j) with i < j
    pair_reasons: dict[tuple[int, int], str] = {}

    for i in range(n):
        for j in range(i + 1, n):
            a, b = sigs[i], sigs[j]
            reason = _link_pair(a, b)
            if reason is None:
                continue
            # Topical / scope links must respect the epic window. Issue-ref
            # links are time-independent (a ticket is a ticket regardless of gap).
            if not reason.startswith("issue_ref"):
                if not _within_window(a, b, EPIC_WINDOW_DAYS):
                    continue
            uf.union(i, j)
            pair_reasons[(i, j)] = reason

    # Group indices by component root.
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)

    # Ref-family split: a no-ref task can transitively bridge two distinct ticket
    # families via shared project vocabulary (e.g. a client-a task sharing
    # "plugin/app" tokens links CA-2435 forms work to CA-2436 maps work). If
    # a component carries 2+ distinct dominant refs, split it along ref lines.
    # Each ref keeps its own members; a no-ref member joins the ref-group it
    # shares the most tokens with (>= strong threshold), else becomes a singleton.
    split_groups: dict[int, list[int]] = {}
    next_key = 0
    for root, idxs in groups.items():
        refs = {sigs[i].dominant_ref for i in idxs if sigs[i].dominant_ref}
        if len(refs) < 2:
            split_groups[next_key] = idxs
            next_key += 1
            continue
        by_ref: dict[str, list[int]] = {r: [] for r in refs}
        noref = []
        for i in idxs:
            dr = sigs[i].dominant_ref
            (by_ref[dr] if dr else noref).append(i)
        for i in noref:
            best_ref, best_overlap = None, 0
            for r, members_i in by_ref.items():
                ov = max(
                    (len(sigs[i].title_tokens & sigs[j].title_tokens) for j in members_i),
                    default=0,
                )
                if ov > best_overlap:
                    best_ref, best_overlap = r, ov
            if best_ref is not None and best_overlap >= EPIC_MIN_SHARED_TOKENS_STRONG:
                by_ref[best_ref].append(i)
            else:
                split_groups[next_key] = [i]  # own singleton
                next_key += 1
        for r, members_i in by_ref.items():
            split_groups[next_key] = members_i
            next_key += 1
    groups = split_groups

    epics: list[Epic] = []
    for root, idxs in groups.items():
        members = [sigs[i] for i in idxs]
        if len(members) > 1:
            warn = _is_pathological(members, observed_span_days)
            if warn is not None:
                logger.warning(
                    "epic guardrail tripped (component root=%s): %s — leaving %d members "
                    "as separate epics",
                    members[0].task_id, warn, len(members),
                )
                # Demote: each member becomes its own singleton epic.
                for m in members:
                    epics.append(_build_epic([m], tasks_by_id, reasons=set(), enriched_titles=enriched_titles))
                continue
        # Collect the reasons that linked this component (for stats).
        reasons: set[str] = set()
        idx_set = set(idxs)
        for (i, j), r in pair_reasons.items():
            if i in idx_set and j in idx_set:
                reasons.add(r.split("=", 1)[0])  # category only: issue_ref / scope / topical
        epics.append(_build_epic(members, tasks_by_id, reasons, enriched_titles=enriched_titles))

    epics.sort(key=lambda e: -e.total_events)
    return epics
