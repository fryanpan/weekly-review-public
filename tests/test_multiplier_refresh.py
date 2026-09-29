"""The published multiplier tables must be reproducible from a run, month after month.

These pin the definitions the post quotes: the 3-minute floor on the per-task
median, pooled over every estimated task, Pacific-calendar bucketing, and the
source-arrival markers that stop a measurement step reading as a workflow change.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "multiplier_refresh.py"
spec = importlib.util.spec_from_file_location("multiplier_refresh", SCRIPT)
mr = importlib.util.module_from_spec(spec)
sys.modules["multiplier_refresh"] = mr
spec.loader.exec_module(mr)


def _row(est, hands_on, wall=1.0, transcript=None, typ="code", day=date(2026, 5, 6)):
    return {"task_id": f"t{est}-{hands_on}", "project": "p", "est": est, "type": typ,
            "hands_on": hands_on, "transcript": hands_on if transcript is None else transcript,
            "wall": wall, "day": day}


def test_median_skips_tasks_under_three_minutes_but_pooled_keeps_them():
    rows = [_row(2.0, 1.0), _row(3.0, 1.0), _row(4.0, 1.0), _row(5.0, 1.0), _row(100.0, 0.04)]
    h = mr.headline(rows)
    assert h["median"][3] == 4                      # the 0.04h task is outside the median
    assert h["median"][0] == 3.5
    assert round(h["pooled"], 1) == round(114 / 4.04, 1)  # but inside pooled


def test_quartiles_need_four_values():
    assert mr.quartiles([1.0, 2.0, 3.0]) is None
    assert mr.fmt(None) == "n<4"


def test_size_bucket_edges():
    assert mr.size_bucket(0.49) == "trivial <30m"
    assert mr.size_bucket(0.5) == "small 30m-2h"
    assert mr.size_bucket(8.0) == "day+ >=8h"


def test_markers_fall_in_their_own_week_only():
    assert mr.markers_between(date(2026, 8, 10), date(2026, 8, 17)) == ["Aug 13 board actions tied to tasks"]
    assert mr.markers_between(date(2026, 8, 31), date(2026, 9, 7)) == []
    # An arrival on the first day of the next period belongs to that period.
    assert mr.markers_between(date(2026, 8, 6), date(2026, 8, 13)) == []


def test_month_table_marks_every_arrival_in_the_month():
    rows = [_row(2.0 + i, 1.0, day=date(2026, 8, 20)) for i in range(4)]
    out = mr.render_tables(rows)
    aug = next(line for line in out.splitlines() if line.startswith("| 2026-08 |"))
    assert "Aug 13" in aug and "Aug 18" in aug and "Aug 28" in aug


def test_prior_run_headline_sits_beside_the_current_one():
    cur = [_row(4.0, 1.0) for _ in range(4)]
    prior = [_row(2.0, 1.0) for _ in range(4)]
    out = mr.render_tables(cur, prior, "new", "old")
    assert "| Measure | new | old |" in out
    assert "| Per-task median, hands-on | 4.0× (IQR 4.0–4.0, n=4) | 2.0× (IQR 2.0–2.0, n=4) |" in out


def test_concentration_shares_sum_to_the_whole():
    rows = [_row(10.0, 0.01), _row(1.0, 1.0), _row(3.0, 1.0), _row(10.0, 1.0), _row(40.0, 1.0)]
    out = mr.render_tables(rows)
    section = out.split("## Where pooled output concentrates")[1]
    shares = [int(line.split("|")[3].strip().rstrip("%")) for line in section.splitlines()
              if line.startswith("| ") and "%" in line]
    assert abs(sum(shares) - 100) <= 2  # rounding


def test_window_filters_by_the_day_the_task_started():
    rows = [_row(1.0, 1.0, day=date(2026, 9, 13)), _row(1.0, 1.0, day=date(2026, 9, 14)),
            _row(1.0, 1.0, day=date(2026, 10, 11)), _row(1.0, 1.0, day=date(2026, 10, 12))]
    kept = mr.in_window(rows, date(2026, 9, 14), date(2026, 10, 11))
    assert [r["day"] for r in kept] == [date(2026, 9, 14), date(2026, 10, 11)]


def test_rationale_mismatch_fires_at_three_times_only():
    assert mr.rationale_mismatch(1.0, "roughly 3h of senior work") == 3.0
    assert mr.rationale_mismatch(1.0, "about 2.5h") is None
    assert mr.rationale_mismatch(1.0, "no figure here") is None


def test_spend_prices_known_models_and_refuses_to_guess_others():
    assert mr.spend_usd("claude-sonnet-4-6", 1_000_000, 1_000_000, 0, 0) == 18.0
    # Cache reads are a tenth of input price; pricing them as input overstates a cached run 10x.
    assert round(mr.spend_usd("claude-sonnet-4-6", 0, 0, 1_000_000, 1_000_000), 2) == 4.05
    assert mr.spend_usd("some-other-model", 1, 1, 1, 1) is None
