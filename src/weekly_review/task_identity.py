"""Carry task ids forward across re-segmentation by event-set overlap.

_stable_task_id hashes (project, first-event ts, seed text) — cheap and
deterministic, but any re-segmentation that changes a task's FIRST event
(orphan adoption, split/merge, a new filter rule) mints a fresh id for what is
substantially the same work. Review verdicts key on task_id, so every such
change orphans review history (~13% of ids churned between two real runs).

Fix (the user greenlit 2026-07-28): after segmentation, match current tasks
against prior runs' task→event-id sets and inherit the prior id when the
overlap is strong. Prototype on the two runs that lost 44 ids: threshold 0.5
recovers 82%; looser thresholds buy almost nothing — the remainder are genuine
splits that SHOULD get new ids.

Score per (new task, old task) pair, from the prototype:
    j   = |new ∩ old| / |new ∪ old|          (Jaccard)
    cov = |new ∩ old| / |old|                 (how much of the OLD task is here)
    s   = max(j, cov if j >= 0.2 else 0)
cov handles the absorb case — a new task that swallowed an old small task
whole keeps its id even though Jaccard is diluted by the new task's extra
events; the j >= 0.2 gate stops a mega-task claiming every tiny id it grazed.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

from .events import Event
from .segmentation import Task

CARRY_FORWARD_THRESHOLD = 0.5
COVERAGE_MIN_JACCARD = 0.2
# Prior runs to scan, newest first. Ids chain run-to-run, so a short scan
# window covers deep history; the cap keeps startup O(recent runs).
MAX_PRIOR_RUNS = 12


def _load_prior_task_events(db_path: Path) -> dict[str, set[str]]:
    """{task_id: set(event_id)} from a prior run DB; {} if unreadable."""
    if not db_path.exists():
        return {}
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            out: dict[str, set[str]] = {}
            for tid, eid in conn.execute("SELECT task_id, event_id FROM task_events"):
                out.setdefault(tid, set()).add(eid)
            return out
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return {}


def _score(new_evs: set[str], old_evs: set[str]) -> float:
    inter = len(new_evs & old_evs)
    if not inter:
        return 0.0
    j = inter / len(new_evs | old_evs)
    cov = inter / len(old_evs)
    return max(j, cov if j >= COVERAGE_MIN_JACCARD else 0.0)


def carry_forward_task_ids(
    tasks: list[Task],
    events: list[Event],
    prior_db_paths: Iterable[Path],
    threshold: float = CARRY_FORWARD_THRESHOLD,
) -> list[tuple[str, str, float]]:
    """Rewrite fresh task ids to prior-run ids where the work is the same.

    `prior_db_paths` must be ordered newest first; the newest run that
    recognizes a task wins (ids chain run-to-run). Only tasks whose id is
    UNKNOWN to every scanned prior run are candidates — a stable id is never
    rewritten. Each prior id is claimed at most once (greedy, strongest match
    first), and never by a task when that id still exists in the current run.

    Returns [(old_id_before_rewrite, inherited_id, score)] and mutates
    task.task_id + segmentation_trace in place.
    """
    dbs = [(_p, _load_prior_task_events(_p)) for _p in list(prior_db_paths)[:MAX_PRIOR_RUNS]]
    dbs = [(p, m) for p, m in dbs if m]
    if not dbs:
        return []

    known_prior_ids: set[str] = set()
    for _, m in dbs:
        known_prior_ids |= set(m)

    current_ids = {t.task_id for t in tasks}
    ev_sets_by_obj = {
        id(t): {events[i].event_id for i in t.event_ids if i < len(events)}
        for t in tasks
    }
    # Only genuinely-new ids are candidates for re-keying. Keyed by object —
    # a task's id changes once matched, so ids are unusable as loop keys.
    candidates = [t for t in tasks if t.task_id not in known_prior_ids]

    claimed: set[str] = set(current_ids)  # an inherited id must collide with nothing current
    rewrites: list[tuple[str, str, float]] = []
    matched_objs: set[int] = set()  # id(task) of candidates already re-keyed

    for db_path, old_map in dbs:
        scored: list[tuple[float, str, int, str]] = []  # (score, orig_tid, id(task), old_tid)
        for t in candidates:
            if id(t) in matched_objs:
                continue
            nevs = ev_sets_by_obj[id(t)]
            if not nevs:
                continue
            for old_tid, oevs in old_map.items():
                if old_tid in claimed:
                    continue
                s = _score(nevs, oevs)
                if s >= threshold:
                    scored.append((s, t.task_id, id(t), old_tid))
        # Greedy strongest-first within this run so two new tasks can't both
        # claim one old id (ties break on ids for determinism).
        by_obj = {id(t): t for t in candidates}
        for s, orig_tid, obj, old_tid in sorted(scored, key=lambda x: (-x[0], x[1], x[3])):
            if obj in matched_objs or old_tid in claimed:
                continue
            task = by_obj[obj]
            task.segmentation_trace.append({
                "event_idx": -1,
                "rule": "id_carry_forward",
                "detail": f"{orig_tid} -> {old_tid} score={s:.2f} via {db_path.parent.name}",
            })
            task.task_id = old_tid
            matched_objs.add(obj)
            claimed.add(old_tid)
            rewrites.append((orig_tid, old_tid, s))
    return rewrites
