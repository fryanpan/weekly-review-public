"""Concurrent slices of one workstream — the narrative-twin dedup.

The artifact-credit pass guarantees each PR/commit is est-credited once. The
2026-07-28 random-week audit showed the double-counting simply moved up a
level: the same work gets priced in two tasks where the duplicate's claim is
TEXT-ONLY (no artifact attached, so there is nothing to demote). Four shapes,
one signature — the tasks OVERLAP IN TIME within a project:

  - a session cut into two tasks, each priced for the whole evening
  - a commit-bundle task spanning days, absorbing the diffstat of commits whose
    generating conversations are separately priced
  - a delegation shell priced alongside the worker session doing the work
  - a steering task priced alongside its own delegated fan-out

Sequential work does NOT overlap: a task that ends before the next begins is a
different sitting. So temporal overlap inside one project is the discriminator,
and it flags every suspect the audit named by hand (Apr 13's 60h mega-task with
its three generating conversations; Jul 13's 40h career-tool bundle with its
build sessions; Jun 29's health-tool and rare-disease pairs).

The fix mirrors artifact credit, which is proven: disclose, don't subtract.
Each task's enrichment prompt lists its concurrent siblings and instructs the
estimator to price ONLY its own slice. Symmetric — no task is designated owner,
because unlike an artifact a workstream has no single rightful holder; each
slice legitimately owns its own increment.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .artifact_credit import event_repo
from .events import Event
from .segmentation import Task

# Overlap as a fraction of the SHORTER task's span. A short shard sitting
# inside a long task scores 1.0 (fully concurrent) — that's the mega-task case.
MIN_OVERLAP_FRACTION = 0.5
# Both tasks must be long enough for "overlap" to mean anything; two instants
# at the same second are not evidence of a shared workstream.
MIN_SPAN_SEC = 5 * 60
# Cap disclosure so a busy project can't blow up the prompt. Largest siblings
# first — those are the ones whose work a shard might re-price.
MAX_SIBLINGS_DISCLOSED = 6


# Two artifact-thin shards seconds apart (a commit shard and its PR shard) have
# near-zero spans, so an overlap FRACTION can never fire on them — 1s of overlap
# on a 0s span is undefined. Pair them by proximity instead.
NEAR_ZERO_SPAN_SEC = 60
NEAR_ZERO_PROXIMITY_SEC = 15 * 60


@dataclass
class Workstreams:
    # task_id -> sibling task_ids that run concurrently in the same repo scope
    siblings: dict[str, list[str]] = field(default_factory=dict)
    # task_ids designated OWNER of their workstream group — they must price the
    # work fully; only non-owners defer.
    owners: set[str] = field(default_factory=set)
    n_groups: int = 0

    @property
    def n_tasks_with_siblings(self) -> int:
        return len(self.siblings)


def _span(t: Task) -> tuple[datetime, datetime] | None:
    if t.first_event_ts is None or t.last_event_ts is None:
        return None
    return t.first_event_ts, t.last_event_ts


def _holds_artifact(task: Task, events: list[Event] | None) -> bool:
    if not events:
        return False
    return any(i < len(events) and events[i].actor == "artifact" for i in task.event_ids)


def _scope_key(task: Task, events: list[Event] | None) -> str:
    """The bucket within which two tasks can be slices of one workstream.

    Project slug is the wrong bucket. rare-disease-benchmark SPLIT OUT of
    health-tool, so one stretch of work puts its PRs under one slug and the
    conversation that produced them under another; same-project scoping is
    structurally blind to it (~30h in one audited week). Prefer the repo the
    task's own artifacts belong to, which is slug-independent and stays correct
    on both sides of a split. Tasks with no artifacts keep their project slug.
    """
    if events:
        for i in task.event_ids:
            if i < len(events) and events[i].actor == "artifact":
                repo = event_repo(events[i])
                if repo:
                    return f"repo:{repo}"
    return f"project:{task.project}"


def find_workstreams(tasks: list[Task], events: list[Event] | None = None) -> Workstreams:
    """Group tasks whose event spans overlap materially within one repo scope.

    Passing `events` enables repo scoping (see _scope_key); without it the
    grouping falls back to project slugs, which is the pre-2026-07-29 behaviour.
    """
    buckets: dict[str, list[Task]] = {}
    for t in tasks:
        if _span(t) is not None:
            buckets.setdefault(_scope_key(t, events), []).append(t)
    # A repo-scoped task and a project-scoped task in the same stretch of work
    # must still meet: the conversation side has no artifact, so it buckets by
    # project while the PR side buckets by repo. Cross-index the two.
    for t in tasks:
        if _span(t) is None:
            continue
        pk = f"project:{t.project}"
        sk = _scope_key(t, events)
        if sk != pk:
            buckets.setdefault(pk, [])
            if t not in buckets[pk]:
                buckets[pk].append(t)

    out = Workstreams()
    groups_seen: set[frozenset[str]] = set()
    pair_keys: set[frozenset[str]] = set()
    for _scope, plist in buckets.items():
        plist.sort(key=lambda t: t.first_event_ts)
        for i, a in enumerate(plist):
            a_start, a_end = _span(a)
            a_span = (a_end - a_start).total_seconds()
            for b in plist[i + 1:]:
                b_start, b_end = _span(b)
                b_span = (b_end - b_start).total_seconds()
                key = frozenset((a.task_id, b.task_id))
                if key in pair_keys or a.task_id == b.task_id:
                    continue
                # Zero-span pairing is restricted to ARTIFACT-holding shards on
                # both sides — that is the digest case (a commit shard and its
                # PR shard, seconds apart). Without that gate, any two brief
                # unrelated tasks in a busy project would pair on proximity.
                tiny = (
                    a_span <= NEAR_ZERO_SPAN_SEC and b_span <= NEAR_ZERO_SPAN_SEC
                    and _holds_artifact(a, events) and _holds_artifact(b, events)
                )
                if tiny:
                    # Zero-span shard pair: judge by proximity, not fraction.
                    gap = abs((b_start - a_start).total_seconds())
                    if gap > NEAR_ZERO_PROXIMITY_SEC:
                        continue
                else:
                    if b_start >= a_end:
                        break  # sorted by start: no later task can overlap a
                    if a_span < MIN_SPAN_SEC or b_span < MIN_SPAN_SEC:
                        continue
                    overlap = (min(a_end, b_end) - max(a_start, b_start)).total_seconds()
                    if overlap <= 0:
                        continue
                    if overlap / min(a_span, b_span) < MIN_OVERLAP_FRACTION:
                        continue
                pair_keys.add(key)
                out.siblings.setdefault(a.task_id, []).append(b.task_id)
                out.siblings.setdefault(b.task_id, []).append(a.task_id)
                groups_seen.add(key)
    out.n_groups = len(groups_seen)
    out.owners = _designate_owners(tasks, out.siblings, events)
    return out


def _designate_owners(
    tasks: list[Task],
    siblings: dict[str, list[str]],
    events: list[Event] | None,
) -> set[str]:
    """One owner per connected workstream group.

    Symmetric disclosure has two failure modes the audit caught: two
    substantive-looking slices each hold their ground (nothing dedupes), or both
    defer and a real diffstat is priced by NOBODY — PR #214, +8,370 lines, went
    unowned that way. Both are fixed by naming an owner: the owner prices the
    shared work, everyone else defers to it.

    Owner = most artifact evidence, then most events. Artifact-weighted because
    the task holding the shipped diffstat is the one that can defensibly size
    the deliverable.
    """
    by_id = {t.task_id: t for t in tasks}
    seen: set[str] = set()
    owners: set[str] = set()
    for tid in siblings:
        if tid in seen:
            continue
        # connected component over the sibling graph
        stack, group = [tid], []
        while stack:
            cur = stack.pop()
            if cur in seen or cur not in by_id:
                continue
            seen.add(cur)
            group.append(cur)
            stack.extend(siblings.get(cur, []))
        if not group:
            continue

        def score(t_id: str) -> tuple[int, int, str]:
            t = by_id[t_id]
            n_art = 0
            if events:
                n_art = sum(1 for i in t.event_ids
                            if i < len(events) and events[i].actor == "artifact")
            return (n_art, len(t.event_ids), t_id)

        owners.add(max(group, key=score))
    return owners


def sibling_block(
    task: Task,
    tasks_by_id: dict[str, Task],
    siblings: list[str],
    est_by_id: dict[str, float] | None = None,
    is_owner: bool = False,
) -> list[str]:
    """Prompt lines describing this task's concurrent siblings.

    Deliberately omits sibling estimates by default: quoting another task's
    hours invites anchoring ("mine should be similar") rather than the intended
    behavior, which is to price this slice on its own evidence and not restate
    the siblings' work.
    """
    if not siblings:
        return []
    ranked = sorted(
        (tasks_by_id[s] for s in siblings if s in tasks_by_id),
        key=lambda t: -len(t.event_ids),
    )[:MAX_SIBLINGS_DISCLOSED]
    if not ranked:
        return []
    if is_owner:
        lines = [
            "",
            "=== CONCURRENT SLICES OF THE SAME WORKSTREAM — THIS TASK HOLDS THE ARTIFACTS ===",
            "The tasks below ran concurrently with this one on the same work, and "
            "each is priced separately. Two rules, and they are different: "
            "(a) the artifacts listed under ARTIFACTS SHIPPED above are credited "
            "to THIS task, so price them HERE — no sibling will, and work you "
            "leave out is counted nowhere. (b) Everything else still follows the "
            "normal rule: price only what THIS task's own evidence shows, at "
            "expert pace. Owning the artifacts is not licence to size the whole "
            "workstream — these siblings' own labour is theirs:",
        ]
    else:
        lines = [
            "",
            "=== CONCURRENT SLICES OF THE SAME WORKSTREAM (each is priced separately — do NOT re-price their work) ===",
            "The tasks below ran at the same time as this one, on the same work. "
            "They are separate slices of one stretch of work, and each is receiving "
            "its own independent estimate. Price ONLY the increment that belongs to "
            "THIS task. Do not size the whole session, the whole feature, or the "
            "whole deliverable — the rest of it is already being counted in these:",
        ]
    for s in ranked:
        span = ""
        if s.first_event_ts and s.last_event_ts:
            span = (f" [{s.first_event_ts.isoformat()[:16]} → "
                    f"{s.last_event_ts.isoformat()[:16]}]")
        lines.append(f"- \"{s.title[:100]}\"{span} ({len(s.event_ids)} events)")
    if not is_owner:
        lines.append(
            "If THIS task's own evidence is only a merge, a hand-off, a status "
            "check, or a restatement of what a sibling built, it is a SMALL task "
            "(well under 1h) even when the sibling's work was large."
        )
    lines.append(
        "FLOOR: deferring to a sibling never makes work vanish. This task still "
        "took at least the effort its OWN event record shows — the messages "
        "exchanged, the files edited, the turns spent here. Never price it below "
        "that, and never at ~0 just because a sibling is large."
    )
    return lines
