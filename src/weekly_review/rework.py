"""Rework detection.

A "rework task" is one whose work was redoing something that was supposed to
already work. We detect it from explicit signals in the event stream — not
heuristics about "this task seems hard."

Signals (in order of strength):

  1. Revert commits / revert PRs. A commit or PR with "revert" in its subject
     line is a direct admission that prior work needs to be undone.

  2. User frustration markers. The user messages like "still broken", "bug is
     still there", "doesn't make sense", "didn't work" — these mark a moment
     where shipped work was found to not actually work.

  3. Explicit fix-of-fix branch names like fix/revert-* — a planned revert
     PR.

Rework hours are not a perfect measure of waste — some "rework" is just
discovery work in unfamiliar territory. But the top-5-by-cost list reliably
surfaces the loops worth examining.

Output is a list of ReworkCluster: same-project tasks within 72h of each
other where at least one task scores above threshold. We rank clusters by
(user-active + agent-active) hours descending and return the top N.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable

from .events import Event
from .segmentation import Task


# Regex set tuned for the user's voice. False-positive rate matters more than
# recall here: we don't want to flag every "this is confusing" as rework.
FRUSTRATION_MARKERS = [
    re.compile(r"\b(still|again)\s+(broken|not\s+working|happening|wrong|there|failing|crashing)\b", re.IGNORECASE),
    re.compile(r"\bbug\s+is\s+(still|back)\b", re.IGNORECASE),
    re.compile(r"\bdidn'?t\s+(work|fix)\b", re.IGNORECASE),
    re.compile(r"\bisn'?t\s+working\b", re.IGNORECASE),
    re.compile(r"\bdoesn'?t\s+(work|fix|make sense)\b", re.IGNORECASE),
    re.compile(r"\bwasn'?t\s+(working|fixed)\b", re.IGNORECASE),
    re.compile(r"\bsame\s+(bug|issue|problem|error)\b", re.IGNORECASE),
    re.compile(r"\bthat\s+didn'?t\s+(work|fix)\b", re.IGNORECASE),
    re.compile(r"\bafter\s+(latest\s+)?deploy.*still\b", re.IGNORECASE),
    re.compile(r"\bdefault\s+(to|back\s+to)\s+(the\s+)?old\b", re.IGNORECASE),
]
REVERT_RE = re.compile(r"\brevert\b", re.IGNORECASE)
FIX_REVERT_BRANCH_RE = re.compile(r"\bfix/revert[-_/]", re.IGNORECASE)

CLUSTER_GAP_HOURS = 72
DEFAULT_TOP_N = 5

# Score weights — see signal-strength ordering in module docstring.
W_FRUSTRATION = 3
W_REVERT = 5

# A task scores ≥ this to be considered a rework-flagged task. One frustration
# marker is enough to flag a task; whether the cluster surfaces depends on
# MIN_CLUSTER_SCORE below.
MIN_SCORE = 3

# A cluster surfaces in the digest only if its summed signal strength meets
# this threshold. Calibrated so a single isolated frustration marker (one task,
# one "still broken" message) doesn't surface — those are too noisy. Multi-task
# clusters with markers in each, OR a single task with a revert commit, both
# clear this bar.
MIN_CLUSTER_SCORE = 5


@dataclass
class ReworkSignals:
    task_id: str
    project: str
    frustration_markers: int = 0
    revert_commits: int = 0
    revert_prs: int = 0
    fix_revert_branches: int = 0

    @property
    def score(self) -> int:
        return (
            W_FRUSTRATION * self.frustration_markers
            + W_REVERT * (self.revert_commits + self.revert_prs)
            + W_REVERT * self.fix_revert_branches
        )

    @property
    def is_rework(self) -> bool:
        return self.score >= MIN_SCORE


@dataclass
class ReworkCluster:
    project: str
    task_ids: list[str] = field(default_factory=list)
    titles: list[str] = field(default_factory=list)
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    bryan_active_hours: float = 0.0
    agent_active_hours: float = 0.0
    frustration_markers: int = 0
    revert_commits: int = 0
    revert_prs: int = 0
    fix_revert_branches: int = 0

    @property
    def total_hours(self) -> float:
        return self.bryan_active_hours + self.agent_active_hours

    @property
    def signal_score(self) -> int:
        return (
            W_FRUSTRATION * self.frustration_markers
            + W_REVERT * (self.revert_commits + self.revert_prs + self.fix_revert_branches)
        )


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def score_task(task: Task, events: list[Event]) -> ReworkSignals:
    sig = ReworkSignals(task_id=task.task_id, project=task.project)
    for idx in task.event_ids:
        if idx >= len(events):
            continue
        ev = events[idx]
        text = ev.content_snippet or ""
        if ev.kind == "user_message" and ev.actor == "user":
            for pattern in FRUSTRATION_MARKERS:
                if pattern.search(text):
                    sig.frustration_markers += 1
                    break  # one match per message; avoid double-counting
        elif ev.kind == "commit":
            if REVERT_RE.search(text):
                sig.revert_commits += 1
        elif ev.kind in ("pr_opened", "pr_merged"):
            if REVERT_RE.search(text):
                sig.revert_prs += 1
            branch = (ev.metadata or {}).get("branch", "") if isinstance(ev.metadata, dict) else ""
            if branch and FIX_REVERT_BRANCH_RE.search(branch):
                sig.fix_revert_branches += 1
    return sig


def score_all_tasks(tasks: list[Task], events: list[Event]) -> dict[str, ReworkSignals]:
    return {t.task_id: score_task(t, events) for t in tasks}


def cluster_rework_tasks(
    tasks: list[Task],
    signals: dict[str, ReworkSignals],
    active_hours: dict[str, float],
    agent_hours: dict[str, float],
    enriched_titles: dict[str, str] | None = None,
) -> list[ReworkCluster]:
    """Group rework-flagged tasks by project, then by ≤ CLUSTER_GAP_HOURS gap."""
    enriched_titles = enriched_titles or {}
    rework_tasks = [t for t in tasks if signals[t.task_id].is_rework]
    by_project: dict[str, list[Task]] = {}
    for t in rework_tasks:
        by_project.setdefault(t.project, []).append(t)

    clusters: list[ReworkCluster] = []
    gap = timedelta(hours=CLUSTER_GAP_HOURS)
    for project, tlist in by_project.items():
        tlist_sorted = sorted(tlist, key=lambda x: x.first_event_ts or datetime.max.replace(tzinfo=__import__("datetime").timezone.utc))
        current: ReworkCluster | None = None
        for t in tlist_sorted:
            sig = signals[t.task_id]
            t_first = t.first_event_ts
            t_last = t.last_event_ts or t.first_event_ts
            if t_first is None:
                continue
            if current is None or (current.last_ts and t_first - current.last_ts > gap):
                if current is not None:
                    clusters.append(current)
                current = ReworkCluster(project=project, first_ts=t_first, last_ts=t_last)
            current.task_ids.append(t.task_id)
            current.titles.append(enriched_titles.get(t.task_id, t.title))
            if t_last and (current.last_ts is None or t_last > current.last_ts):
                current.last_ts = t_last
            current.bryan_active_hours += active_hours.get(t.task_id, 0.0)
            current.agent_active_hours += agent_hours.get(t.task_id, 0.0)
            current.frustration_markers += sig.frustration_markers
            current.revert_commits += sig.revert_commits
            current.revert_prs += sig.revert_prs
            current.fix_revert_branches += sig.fix_revert_branches
        if current is not None:
            clusters.append(current)
    return clusters


def top_rework_clusters(
    tasks: list[Task],
    events: list[Event],
    active_hours: dict[str, float],
    agent_hours: dict[str, float],
    enriched_titles: dict[str, str] | None = None,
    n: int = DEFAULT_TOP_N,
) -> tuple[list[ReworkCluster], dict[str, ReworkSignals]]:
    """Detect rework clusters, return top-N by total cost + the signal map."""
    signals = score_all_tasks(tasks, events)
    clusters = cluster_rework_tasks(tasks, signals, active_hours, agent_hours, enriched_titles)
    # Filter to clusters with enough corroborating signal (a cluster of one task
    # with one frustration marker is too noisy to surface; require either
    # multiple markers across tasks OR a revert commit/PR).
    clusters = [c for c in clusters if c.signal_score >= MIN_CLUSTER_SCORE]
    # Drop zero-hour clusters: a revert PR with no attached active or agent
    # hours adds noise. The signal is real but the cost story isn't.
    clusters = [c for c in clusters if c.total_hours > 0.0]
    clusters.sort(key=lambda c: -c.total_hours)
    return clusters[:n], signals


def total_rework_hours(
    clusters: Iterable[ReworkCluster],
) -> tuple[float, float]:
    """Return (Σ user-active rework hours, Σ agent-active rework hours)."""
    a = sum(c.bryan_active_hours for c in clusters)
    g = sum(c.agent_active_hours for c in clusters)
    return a, g


def rework_attributable_est_hours(
    clusters: Iterable[ReworkCluster],
    enriched: dict,
) -> float:
    """Return Σ est_human_hours for tasks inside rework clusters.

    Conservative: counts the full est_h of every task we flagged. A mixed-cause
    cluster (rework + new functionality landed in the same loop) will overstate
    rework-attributed hours by some fraction; we'd need a per-commit attribution
    to do better. The headline rework cost we surface in the digest is the
    user-active + agent-active hours instead, which are direct measurements.
    """
    total = 0.0
    for c in clusters:
        for tid in c.task_ids:
            et = enriched.get(tid)
            if et is not None:
                total += et.est_human_hours
    return total
