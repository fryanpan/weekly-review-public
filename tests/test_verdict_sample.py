"""The estimate-review sample must reach the tasks that carry pooled output.

A plain random draw of 30 from ~2,000 tasks lands mostly on trivial and small
work, while day+ commit batches hold half the estimated output. These tests pin
the stratification so a reviewer's verdicts can actually move the headline.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "make_verdict_worksheet.py"
spec = importlib.util.spec_from_file_location("make_verdict_worksheet", SCRIPT)
mvw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mvw)

TYPES = ["code", "research", "documentation", "ops"]


def _rows():
    rows = []
    sizes = [0.25] * 400 + [1.0] * 300 + [4.0] * 200 + [20.0] * 40
    for i, est in enumerate(sizes):
        rows.append({
            "task_id": f"t{i:05d}", "est_human_hours": est, "type": TYPES[i % 4],
            # Only a third are low hands-on, so 8 batches in 10 day+ picks cannot happen by chance.
            "hands_on": 2.0 if i % 3 else 0.2, "first_ts": f"2026-05-{1 + i % 28:02d}T00:00:00",
        })
    return rows


def test_every_size_bucket_is_represented_and_day_plus_gets_a_third():
    picked = mvw.stratified_sample(_rows(), 30, seed=1)
    buckets = [mvw.size_bucket(r["est_human_hours"]) for r in picked]
    assert len(picked) == 30
    assert buckets.count("day+") == 10
    assert all(buckets.count(b) >= 6 for b in ("trivial", "small", "moderate"))


def test_types_are_spread_inside_a_bucket():
    picked = mvw.stratified_sample(_rows(), 30, seed=2)
    trivial_types = {r["type"] for r in picked if mvw.size_bucket(r["est_human_hours"]) == "trivial"}
    assert trivial_types == set(TYPES)


def test_min_batch_draws_low_hands_on_day_plus_tasks():
    picked = mvw.stratified_sample(_rows(), 30, seed=3, min_batch=8)
    batches = [r for r in picked
               if mvw.size_bucket(r["est_human_hours"]) == "day+" and r["hands_on"] < mvw.BATCH_HANDS_ON_H]
    assert len(batches) >= 8


def test_already_reviewed_tasks_are_never_drawn():
    rows = _rows()
    skip = {r["task_id"] for r in rows if r["est_human_hours"] == 20.0}
    picked = mvw.stratified_sample(rows, 30, seed=4, skip_ids=skip)
    assert not {r["task_id"] for r in picked} & skip
    assert len(picked) == 30  # day+ exhausted, so nothing fills it; the other buckets keep their quotas
    assert sum(mvw.size_bucket(r["est_human_hours"]) == "day+" for r in picked) == 0


def test_same_seed_reproduces_the_same_sample():
    a = [r["task_id"] for r in mvw.stratified_sample(_rows(), 30, seed=5, min_batch=8)]
    b = [r["task_id"] for r in mvw.stratified_sample(_rows(), 30, seed=5, min_batch=8)]
    assert a == b
