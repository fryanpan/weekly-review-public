"""Anchor management + systematic-bias correction.

Bias = geometric mean of (truth_hours / llm_hours) across anchor pairs.
Applied as a single multiplier to the LLM's raw est_human_hours.

Pairs come from two sources:
  - anchors.yaml (interactive `--recalibrate` flow)
  - estimate_reviews table in run.sqlite (interactive `--review` flow)

Both are unioned before fitting; newer reviews don't displace older anchors.
"""
from __future__ import annotations

import math
import random
import re
import sqlite3
from pathlib import Path
from typing import Iterable

import yaml


def load_anchors(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text())
    if data is None:
        return []
    return data


def save_anchors(anchors: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(anchors, sort_keys=False))


MIN_PAIRS_FOR_BIAS = 5     # Don't apply a global multiplier with fewer than this many pairs.
RATIO_TRIM_LO = 0.5        # Drop ratios below this — likely a single anomalous review.
RATIO_TRIM_HI = 2.0        # Drop ratios above this — same.


def fit_bias(pairs: Iterable[tuple[float, float]]) -> dict:
    """Compute a global bias multiplier from (llm, truth) hour pairs.

    Returns {"multiplier": float, "n": int, "n_raw": int, "trimmed": int}.

    Guards learned the hard way:
      - Below MIN_PAIRS_FOR_BIAS, return identity. A multiplier built from 1-3
        reviews of unusually large tasks (e.g. AI-baseline reviews on 320h
        anchors) was deflating every other estimate by 70%+ — see
        commit history "AI-seeded baseline reviews on V7 mega-anchors".
      - Trim ratios outside [RATIO_TRIM_LO, RATIO_TRIM_HI]. Genuine outliers
        usually mean the single task is mis-segmented or the LLM hallucinated
        scope, not that the average estimate is off by 5x.

    Empty input => identity, n=0.
    """
    ratios: list[float] = []
    n_raw = 0
    for llm_hours, truth_hours in pairs:
        if llm_hours <= 0 or truth_hours <= 0:
            continue
        n_raw += 1
        r = truth_hours / llm_hours
        if RATIO_TRIM_LO <= r <= RATIO_TRIM_HI:
            ratios.append(r)
    trimmed = n_raw - len(ratios)
    if len(ratios) < MIN_PAIRS_FOR_BIAS:
        return {"multiplier": 1.0, "n": len(ratios), "n_raw": n_raw, "trimmed": trimmed}
    log_mean = sum(math.log(r) for r in ratios) / len(ratios)
    return {"multiplier": math.exp(log_mean), "n": len(ratios), "n_raw": n_raw, "trimmed": trimmed}


def apply_bias_correction(llm_hours: float, bias: dict | None) -> float:
    mult = (bias or {}).get("multiplier", 1.0)
    return llm_hours * mult


def pick_calibration_sample(enriched_tasks: list, n: int = 5) -> list:
    """Pick a random sample of shipped tasks for recalibration."""
    if len(enriched_tasks) <= n:
        return list(enriched_tasks)
    return random.sample(enriched_tasks, n)


def pairs_from_reviews(db_paths: Iterable[Path]) -> list[tuple[float, float]]:
    """Read (llm_hours, bryan_hours) pairs from estimate_reviews tables.

    Walks each run.sqlite in db_paths. Silently skips DBs without the table
    (older runs that predate persistence).
    """
    out: list[tuple[float, float]] = []
    for p in db_paths:
        if not p.exists():
            continue
        try:
            conn = sqlite3.connect(str(p))
            try:
                cur = conn.execute(
                    """SELECT llm_est_hours, bryan_est_hours
                       FROM estimate_reviews
                       WHERE llm_est_hours IS NOT NULL AND bryan_est_hours IS NOT NULL"""
                )
                for llm_h, bryan_h in cur:
                    if llm_h and bryan_h and llm_h > 0 and bryan_h > 0:
                        out.append((float(llm_h), float(bryan_h)))
            finally:
                conn.close()
        except sqlite3.DatabaseError:
            continue
    return out


def load_review_overrides(db_paths: Iterable[Path]) -> dict[str, float]:
    """Build a {task_id: bryan_est_hours} map from estimate_reviews across runs.

    Latest review per task_id wins. Reviews are per-task ground truth — the
    canonical estimate for that specific task — and replace the LLM raw value
    at display time. No global bias multiplier; estimates only improve task-
    by-task as you mark them up.
    """
    out: dict[str, tuple[str, float]] = {}  # task_id -> (reviewed_at, hours)
    for p in db_paths:
        if not p.exists():
            continue
        try:
            conn = sqlite3.connect(str(p))
            try:
                cur = conn.execute(
                    """SELECT task_id, reviewed_at, bryan_est_hours
                       FROM estimate_reviews
                       WHERE bryan_est_hours IS NOT NULL"""
                )
                for tid, ts, hrs in cur:
                    if not tid or hrs is None or hrs <= 0:
                        continue
                    prior = out.get(tid)
                    if prior is None or ts > prior[0]:
                        out[tid] = (ts or "", float(hrs))
            finally:
                conn.close()
        except sqlite3.DatabaseError:
            continue
    return {tid: hrs for tid, (_ts, hrs) in out.items()}


# A verdict is recorded against a task as it was segmented at review time. If a
# later run re-segments that task (orphan-adoption, atomic split/merge), the
# stable task_id can survive on a fragment that covers far less — or more —
# work. Carrying the old verdict onto the changed task is wrong: an 80h
# "whole-week MVP" verdict once got stamped onto a re-cut ~60-line remnant.
# Guard by comparing the task's event_count then vs now.
OVERRIDE_DIVERGENCE_RATIO = 3.0


def carry_forward_ok(baseline_events: int | None, current_events: int | None,
                     max_ratio: float = OVERRIDE_DIVERGENCE_RATIO) -> bool:
    """Whether a verdict from a prior run still applies to the current task.

    False when the task's event_count has grown or shrunk by more than
    max_ratio× since it was reviewed — a signal the stable id now refers to
    materially different work. Missing counts on either side are treated as
    unknown → don't carry (fail safe).
    """
    if not baseline_events or not current_events:
        return False
    hi, lo = max(baseline_events, current_events), min(baseline_events, current_events)
    return (hi / lo) <= max_ratio


def load_review_overrides_with_context(
    db_paths: Iterable[Path],
) -> dict[str, tuple[float, int | None]]:
    """{task_id: (bryan_est_hours, baseline_event_count)} — see load_review_overrides_full."""
    return {
        tid: (hours, base_ec)
        for tid, (hours, base_ec, _ts, _kind) in load_review_overrides_full(db_paths).items()
    }


def kind_unchanged(baseline_kind: str | None, current_kind: str) -> bool:
    """Whether a verdict still describes the unit its id now sits on.

    A verdict prices the work the user looked at. Re-segmentation can move the
    same id onto a unit of another kind — a 40-event conversation became a
    coordination day and its 4.5h verdict priced 62 board stamps whose own
    rationale said 0.3h; the 3× event guard let it through. None means the
    recording run predates `unit_kind` and cannot be checked, so the verdict
    carries as before rather than dropping every pre-2026-09-18 review.
    """
    if baseline_kind is None:
        return True
    return (baseline_kind or "") == (current_kind or "")


def load_review_overrides_full(
    db_paths: Iterable[Path],
) -> dict[str, tuple[float, int | None, str, str | None]]:
    """Like load_review_overrides, but pairs each override with the task's
    event_count and unit_kind in the run where the winning verdict was
    recorded, plus that verdict's timestamp.

    Returns {task_id: (bryan_est_hours, baseline_event_count, reviewed_at,
    baseline_unit_kind)}. The baselines are looked up from the tasks table of
    the same DB that supplied the latest review, so callers can gate the
    override against the current task via carry_forward_ok() and
    kind_unchanged(). event_count is None if that DB has no tasks row.
    unit_kind is None if that DB has no tasks row, or predates the column and
    the task was delegated (kind unknowable); a pre-column non-delegated task
    reads as "" (conversation). reviewed_at gates the pre-fix quarantine.
    """
    out: dict[str, tuple[str, float, int | None, str | None]] = {}  # tid -> (reviewed_at, hours, base_ec, base_kind)
    for p in db_paths:
        if not p.exists():
            continue
        try:
            conn = sqlite3.connect(str(p))
            try:
                have_tasks = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'"
                ).fetchone() is not None
                task_cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")} if have_tasks else set()
                have_delegated = "delegated" in task_cols
                have_kind = "unit_kind" in task_cols
                if have_kind and have_delegated:
                    # persist.connect() adds an empty unit_kind column to any
                    # older db it opens, so the column's presence proves
                    # nothing. In a db that recorded kinds, every delegated
                    # task has one (lanes, PR and lead units are all built
                    # delegated with a kind); a column whose delegated rows
                    # are all '' was added after the fact and is unreadable.
                    any_delegated = conn.execute(
                        "SELECT 1 FROM tasks WHERE delegated=1 LIMIT 1").fetchone() is not None
                    kinded_delegated = conn.execute(
                        "SELECT 1 FROM tasks WHERE delegated=1 AND unit_kind<>'' LIMIT 1"
                    ).fetchone() is not None
                    have_kind = kinded_delegated or not any_delegated
                cur = conn.execute(
                    """SELECT task_id, reviewed_at, bryan_est_hours
                       FROM estimate_reviews
                       WHERE bryan_est_hours IS NOT NULL"""
                )
                for tid, ts, hrs in cur.fetchall():
                    if not tid or hrs is None or hrs <= 0:
                        continue
                    prior = out.get(tid)
                    if prior is not None and (ts or "") <= prior[0]:
                        continue
                    base_ec = None
                    base_kind = None
                    if have_tasks:
                        kind_col = "unit_kind" if have_kind else "NULL"
                        deleg_col = "delegated" if have_delegated else "NULL"
                        row = conn.execute(
                            f"SELECT event_count, {kind_col}, {deleg_col} FROM tasks WHERE task_id=?",
                            (tid,),
                        ).fetchone()
                        if row is not None:
                            base_ec = row[0]
                            if have_kind:
                                base_kind = row[1] or ""
                            elif row[2] == 0:
                                # Before unit_kind existed, a task with his own
                                # messages (delegated = 0) was a conversation by
                                # construction: lanes and PR units were the
                                # delegated ones. Every verdict the user recorded
                                # before 2026-09-18 sits on one of these (159 of
                                # 159), so reading them as conversations is what
                                # lets the kind check see a later move.
                                base_kind = ""
                    out[tid] = (ts or "", float(hrs), base_ec, base_kind)
            finally:
                conn.close()
        except sqlite3.DatabaseError:
            continue
    return {tid: (hrs, base_ec, ts, kind) for tid, (ts, hrs, base_ec, kind) in out.items()}


# Verdicts recorded BEFORE the artifact-credit fix were formed against evidence
# that credited one artifact to several tasks, so a shard's "confirmed" hours
# can encode a whole build's work. Carried forward, they stamp that inflation
# back over a now-corrected fresh estimate (May 18 audit: 13 of 82 tasks; worst
# case a 7h verdict over a fresh 0.5h whose own rationale said 0.5h).
ARTIFACT_CREDIT_FIX_CUTOFF = "2026-07-28"
# Only quarantine when the override is materially larger than the fresh
# estimate. Downward verdicts are the user cutting an inflated number — exactly
# what review is for — and must always survive.
QUARANTINE_MIN_RATIO = 2.0
# ...and only when the fresh estimate is self-consistent (its own rationale
# concludes roughly the same hours), so we never discard a human verdict in
# favour of an LLM number that disagrees with itself.
FRESH_SELF_CONSISTENT_RATIO = 1.5

# "≈ 6h" and "about 6 hours" must both parse: an unparseable rationale makes
# the quarantine fail safe (keep the override), so a spelled-out "hours" was
# silently protecting stale verdicts (May-18 audit found c39e01d6be this way).
# "approximately 38h" and "roughly 12–16h total" are concluding figures too.
# Missing them, the parser fell back to the last itemised part ("~3h" of a
# 14h total) and flagged self-consistent estimates as 3x off their rationale.
_RATIONALE_HOURS_RE = re.compile(
    r"(?:≈|~|about|roughly|approximately|approx\.?|totall?ing|totals?|=)\s*"
    r"([0-9]+(?:\.[0-9]+)?)(?:\s*(?:–|-|to)\s*([0-9]+(?:\.[0-9]+)?))?\s*(?:h\b|hr?s?\b|hours?\b)",
    re.IGNORECASE)


def rationale_hours(rationale: str | None) -> float | None:
    """The hours figure the LLM's own rationale concludes with, if any.

    A range concludes at its midpoint."""
    ms = _RATIONALE_HOURS_RE.findall(rationale or "")
    if not ms:
        return None
    lo, hi = ms[-1]
    try:
        val = (float(lo) + float(hi)) / 2 if hi else float(lo)
    except ValueError:
        return None
    return val if val > 0 else None


def quarantine_pre_fix_override(
    override_hours: float,
    reviewed_at: str | None,
    fresh_hours: float,
    fresh_rationale: str | None,
    cutoff: str = ARTIFACT_CREDIT_FIX_CUTOFF,
) -> tuple[bool, str]:
    """Whether to DROP a carried-forward verdict as pre-fix inflation.

    Returns (quarantine?, reason). All four conditions must hold:
      1. the verdict predates the artifact-credit fix,
      2. it is at least QUARANTINE_MIN_RATIO× ABOVE the fresh estimate
         (downward corrections always survive — that's the human's job),
      3. the fresh estimate agrees with its own rationale, so we're trusting a
         self-consistent number rather than an unmoored one,
      4. both numbers are usable (> 0).

    Conservative by construction: an unparseable rationale, a missing
    timestamp, or a downward verdict all mean "keep the human's number."
    """
    if override_hours <= 0 or fresh_hours <= 0:
        return False, ""
    if not reviewed_at or reviewed_at >= cutoff:
        return False, ""
    if override_hours / fresh_hours < QUARANTINE_MIN_RATIO:
        return False, ""
    concl = rationale_hours(fresh_rationale)
    if concl is None:
        return False, ""
    if max(fresh_hours, concl) / min(fresh_hours, concl) > FRESH_SELF_CONSISTENT_RATIO:
        return False, ""
    return True, (
        f"pre-fix verdict {override_hours:g}h ≥{QUARANTINE_MIN_RATIO:g}× the fresh "
        f"{fresh_hours:g}h (rationale ~{concl:g}h), reviewed {reviewed_at[:10]}"
    )


def pairs_from_anchors(anchors: list[dict], enriched_by_title: dict[str, float]) -> list[tuple[float, float]]:
    """Turn anchor entries into (llm_hours, truth_hours) pairs.

    Anchors store truth_hours. We need an LLM estimate on the same task to form
    a ratio — match by title against the provided enriched snapshot.
    """
    pairs: list[tuple[float, float]] = []
    for a in anchors or []:
        truth = a.get("est_human_hours")
        title = a.get("title")
        if truth is None or title is None:
            continue
        llm_h = enriched_by_title.get(title)
        if llm_h and llm_h > 0:
            pairs.append((float(llm_h), float(truth)))
    return pairs


def decision_only_tasks(tasks, events) -> set[str]:
    """Tasks that are a standalone decision and nothing else.

    **The user's ruling, 2026-09-15** (a review item): *"This is an
    artifact of what we're doing now, and in general over time we should have
    very very few of these -- decisions should never just exist independently.
    They should be part of a doc or task. ... Anyway, I think the right way to
    handle these is count them for the decision time (time to read and reply).
    And then that's it. So I choose 'Price the decision'."*

    87 tasks across the three era weeks are one board decision and nothing
    else: no agent turn, no commit, no PR. The estimator, reading the AGENT's
    board stamps as an expert's coordination, priced them 5-20 minutes each --
    Sigma est 17.35h of invented numerator. His ruling replaces that with the
    one thing actually measured on them: the time he spent reading and
    replying, 0.0-14.2 min each, Sigma 2.89h.

    The predicate is `effort.is_agent_work`, the same one `validate`'s
    `no_conversation` bucket uses, so the two cannot disagree about what counts
    as work. **The populations are still different, deliberately**: the audit
    bucket also requires hands-on below `NEAR_ZERO_HANDS_ON_H`, because it is
    hunting tasks with no measured effort at all, and this correction asks
    nothing about hands-on -- a decision is priced at its decision time
    whatever that time turns out to be. So the audit sees 73 of these 87 and
    the 14 it skips are the ones he spent real minutes on.

    A task with any real agent work behind it is not a standalone decision
    however little of it there was, which is why the test for that is the one
    that fails first when the predicate slips.

    **15 of the 87 measure ZERO decision time**, and pricing those at 0.0h is
    the ruling read literally rather than a judgement about them. They are
    agent board stamps he never touched -- "Administrative board task
    placeholder", "Autonomous board triage action" -- so they are not decisions
    he made, and they are also not work: no human, no agent turn, no artifact.
    They fall under his standing rule that a zero hands-on which was not fully
    agent-triggered AND done end to end has a problem. The caller prints their
    count every run rather than letting 15 estimates fall to zero silently.
    """
    from .effort import is_agent_work
    out = set()
    for t in tasks:
        has_work = False
        for i in t.event_ids:
            ev = events[i]
            if is_agent_work(ev) or ev.actor == "artifact":
                has_work = True
                break
        if not has_work:
            out.add(t.task_id)
    return out
