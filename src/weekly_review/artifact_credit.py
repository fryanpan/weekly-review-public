"""One shipped artifact → one task's est credit.

The same PR emits pr_opened and pr_merged as separate events (and its commits
are separate events again); segmentation can route those to different tasks, a
repo registered under two project slugs ingests its commits twice, and a
ship-shard task holding just the merge event inherits the whole build's
diffstat. Each placement then receives a full independent estimate — the
dominant est_h inflation found by the 2026-07 100-estimate review (weekly
totals ran 25-60% hot, almost entirely from this).

This pass runs after segmentation, across ALL projects at once: group artifact
events by identity (repo+number for PRs, sha for commits), pick ONE owner task
per identity — the task holding the actual work evidence — and mark every
other placement a duplicate. Enrichment then presents duplicates to the
estimator as context that must NOT be counted toward the hours.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .events import Event
from .segmentation import Task


# A squash-merge commit records the PR it closed in its subject: "feat: x (#243)".
# Unifying that with the PR event is what lets a commit in one project and its
# PR in another resolve to ONE artifact — the cross-project twin that survived
# the first dedup pass (health-tool conversations vs rare-disease-benchmark PRs,
# ~30h in one week).
_PR_IN_SUBJECT_RE = re.compile(r"\(#(\d+)\)\s*$")


def event_repo(ev: Event) -> str:
    """Slug-independent repo name for an artifact event."""
    return repo_from_parts(ev.metadata or {}, ev.project)


def repo_from_parts(meta: dict, project: str) -> str:
    """Slug-independent repo name from an artifact event's raw fields.

    `repo_name` is stamped by both artifact extractors. Older runs only have
    `repo`, which is owner/name for PRs but the project SLUG for commits —
    fall back to its name part, then to the project.
    """
    name = meta.get("repo_name")
    if name:
        return str(name).lower()
    repo = meta.get("repo")
    if repo:
        return str(repo).rstrip("/").split("/")[-1].lower()
    return (project or "").lower()


def artifact_identity(ev: Event) -> str | None:
    """Stable cross-project identity for a shipped artifact, or None.

    PRs key on repo+number, so pr_opened and pr_merged of one PR share an
    identity. A squash commit naming its PR ("... (#243)") resolves to that
    SAME identity, which is what unifies a commit and its PR across projects.
    Other commits key on sha alone — short-sha collisions across unrelated
    repos inside one run window are vanishingly rare, and sha-only keying is
    what makes a repo ingested under two slugs dedupe correctly.
    """
    return identity_from_parts(ev.kind, ev.metadata or {},
                               ev.content_snippet or "", ev.project or "")


def identity_from_parts(kind: str, meta: dict, snippet: str, project: str) -> str | None:
    """`artifact_identity` off raw columns, for a reader holding rows not Events.

    The audits read `events` straight out of SQLite and have to key an artifact
    the same way the credit pass keyed it, or they adjudicate a different set of
    artifacts than the estimator was shown.
    """
    if kind.startswith("pr_"):
        number = meta.get("number")
        if number is None:
            return None
        return f"pr:{repo_from_parts(meta, project)}:{number}"
    if kind == "commit":
        m = _PR_IN_SUBJECT_RE.search(snippet or "")
        if m:
            return f"pr:{repo_from_parts(meta, project)}:{m.group(1)}"
        sha = meta.get("short_sha")
        return f"commit:{sha}" if sha else None
    return None



@dataclass
class ArtifactCredit:
    owners: dict[str, str] = field(default_factory=dict)  # identity -> owner task_id
    # task_id -> event indices whose artifact identity is owned by another task
    duplicates_by_task: dict[str, set[int]] = field(default_factory=dict)
    n_identities: int = 0
    n_duplicate_placements: int = 0


def _work_evidence(task: Task, events: list[Event]) -> int:
    """How much actual work (conversation + agent activity) a task holds.

    Artifact events are excluded: they are the thing being adjudicated, and a
    task that is nothing but artifacts (a ship shard, a cross-slug echo) must
    lose ownership to the task where the work happened.
    """
    n = 0
    for i in task.event_ids:
        if i < len(events) and events[i].actor != "artifact":
            n += 1
    return n


def assign_artifact_credit(tasks: list[Task], events: list[Event]) -> ArtifactCredit:
    """Pick one owner task per artifact identity; mark other placements duplicates.

    Owner = the holding task with the most non-artifact events (the
    conversation/agent work that produced the artifact). Ties break to the
    earlier-starting task, then task_id, for determinism. Runs across all
    projects so cross-slug and cross-project placements are adjudicated too.
    """
    # identity -> {task_id: [event indices]}, and task_id -> Task
    holders: dict[str, dict[str, list[int]]] = {}
    by_id: dict[str, Task] = {}
    for t in tasks:
        by_id[t.task_id] = t
        for i in t.event_ids:
            if i >= len(events):
                continue
            ident = artifact_identity(events[i])
            if ident is None:
                continue
            holders.setdefault(ident, {}).setdefault(t.task_id, []).append(i)

    credit = ArtifactCredit(n_identities=len(holders))
    evidence_cache: dict[str, int] = {}

    def evidence(tid: str) -> int:
        if tid not in evidence_cache:
            evidence_cache[tid] = _work_evidence(by_id[tid], events)
        return evidence_cache[tid]

    for ident, placements in holders.items():
        if len(placements) == 1:
            credit.owners[ident] = next(iter(placements))
            continue
        owner = max(
            placements,
            key=lambda tid: (
                evidence(tid),
                -(by_id[tid].first_event_ts.timestamp()
                  if by_id[tid].first_event_ts else float("inf")),
                tid,
            ),
        )
        credit.owners[ident] = owner
        for tid, idxs in placements.items():
            if tid == owner:
                continue
            credit.duplicates_by_task.setdefault(tid, set()).update(idxs)
            credit.n_duplicate_placements += len(idxs)
    return credit
