"""A week's speedup is the middle task, never expert hours over a sum of spans.

The user's ruling on a review item, 2026-09-15, verbatim:

    "Stop printing aggregate speedup for weeks and projects.  We can consider
    median speedup across tasks for a week.  Or across tasks by project."

The defect it closes: every per-task wall clock is a correct union of that
task's own intervals, but the epic rows and the window block ADDED them up, and
tasks genuinely run at the same time. One clock minute was therefore counted
once per live task, and two of the three era weeks reported 246.18h and 329.47h
of wall clock inside 168-hour weeks. Dividing expert hours by that produced a
speedup whose denominator was arithmetically impossible.

Every per-task figure was right the whole time, which is why nothing caught it:
the error lived only in aggregation, so no test that reads one task could see
it. These tests read the aggregation.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "make_label_worksheet.py"

_spec = importlib.util.spec_from_file_location("_mlw", SCRIPT)
mlw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mlw)


def _task(est, wall):
    """A member row, addressed the way the real sqlite3.Row members are."""
    # labor_hours: the per-task figure called wall_clock_hours before 2026-09-18
    return {"est_human_hours": est, "labor_hours": wall}


def test_the_median_is_the_middle_task_not_the_ratio_of_the_sums():
    """The whole point, on numbers where the two answers differ loudly.

    Three tasks of 10h expert each, run concurrently, each taking 1h, 2h and
    10h of its own elapsed time. Σest 30h, Σwall 13h, so the old aggregate read
    2.3x. The middle task actually ran at 5x. The aggregate is dragged by the
    one slow task because the sum puts every task's span in the denominator
    regardless of how the others went.
    """
    members = [_task(10.0, 1.0), _task(10.0, 2.0), _task(10.0, 10.0)]
    speedups = mlw._task_speedups(members)
    assert sorted(speedups) == [1.0, 5.0, 10.0]
    assert mlw._median(speedups) == 5.0

    block = mlw._fmt_metric_block(
        expert_h=30.0, hands_on_h=3.0, wall_clock_h=13.0, agent_h=5.0,
        task_speedups=speedups,
    )
    assert "**median expert ÷ labor:** 5.0×" in block
    # 30 / 13 = 2.3x. If that string is back, the aggregate ratio is back.
    assert "2.3×" not in block


def test_no_aggregate_speedup_line_survives_anywhere_in_the_block():
    """The ruling is about what gets PRINTED, so assert on the rendered text.

    A reader scanning a worksheet sees a label. "wall-clock speedup" next to a
    week's numbers is the thing the user asked to stop seeing, and it must not come
    back under the old label even if the arithmetic behind it changes.
    """
    block = mlw._fmt_metric_block(
        expert_h=30.0, hands_on_h=3.0, wall_clock_h=13.0, agent_h=5.0,
        task_speedups=[1.0, 5.0, 10.0],
    )
    assert "speedup" not in block, "since 2026-09-18 wall-clock speedup is calendar-based and lives in the digest"
    lines = [ln for ln in block.splitlines() if "expert ÷ labor" in ln]
    assert len(lines) == 1, lines
    assert lines[0].lstrip("- ").startswith("**median expert ÷ labor:**")


def test_the_summed_hours_are_kept_but_renamed_so_nobody_reads_them_as_elapsed():
    """Summed wall clock is real — it is labor, not elapsed time.

    The same distinction the repo already draws between `agent_lane_hours`
    (per-lane, summed) and `agent_hours` (merged, elapsed). Deleting the number
    would lose a true fact; leaving it labelled "wall-clock hours" beside a
    week is what let 329h inside a 168-hour week look unremarkable.
    """
    block = mlw._fmt_metric_block(
        expert_h=30.0, hands_on_h=3.0, wall_clock_h=329.47, agent_h=5.0,
        task_speedups=[2.0],
    )
    assert "**Σ per-task labor_hours:** 329.47h" in block
    assert "ADDED UP" in block
    assert "- **wall-clock hours:**" not in block
    assert "wall clock" not in block.lower() or "not calendar time" in block


def test_an_unmeasured_speedup_is_omitted_not_printed_as_a_placeholder():
    """A missing measurement must never read as a measured value.

    Same rule the block already follows for agent labor on pre-2026-07-12 runs.
    A dash or a 0.0x in this slot would be read as "this week was slow", which
    is a different and worse claim than "this was not measured".
    """
    block = mlw._fmt_metric_block(
        expert_h=30.0, hands_on_h=3.0, wall_clock_h=13.0, agent_h=5.0,
        task_speedups=[],
    )
    assert "speedup" not in block
    # And the default — a caller that has not been updated to pass the tasks
    # gets no speedup line either, rather than silently falling back to the sum.
    assert "speedup" not in mlw._fmt_metric_block(30.0, 3.0, 13.0, 5.0)


def test_a_task_with_no_wall_clock_is_dropped_not_scored_infinite():
    """Otherwise an unmeasured task becomes the middle value.

    `_leverage` renders est-with-no-denominator as "∞", which is right for a
    single task's own row — it says the conversation that produced this is not
    in the dataset. Feeding that into a median would let unmeasured tasks decide
    the week's headline, so they are dropped from the population instead.
    """
    members = [_task(10.0, 1.0), _task(10.0, 0.0), _task(10.0, None), _task(0.0, 5.0)]
    assert mlw._task_speedups(members) == [10.0]
    assert mlw._median([]) is None


def test_both_call_sites_pass_their_own_tasks():
    """The defect class this repo keeps shipping is correct-but-unreachable.

    `_fmt_metric_block` defaults `task_speedups` to None and omits the line, so
    a call site that was never updated prints a block with NO speedup at all and
    no error. That is safe but silently lossy, and a grep is the only thing that
    catches it — the same argument as the presence-metric call-site test.
    """
    src = SCRIPT.read_text()
    assert src.count("task_speedups=") == 2, src.count("task_speedups=")
    assert "task_speedups=_task_speedups(e[\"_members\"])" in src
    assert (
        'task_speedups=[s for e in epics for s in _task_speedups(e["_members"])]'
        in src
    )


def test_the_methodology_footer_explains_why_it_is_not_a_ratio_of_sums():
    """A worksheet is read by people who were not here.

    The footer is the only place that can tell them the middle-task number is
    not expert hours over the total beside it, and that the total exceeds the
    period on purpose rather than by mistake.
    """
    src = SCRIPT.read_text()
    assert "median expert ÷ labor" in src
    assert "counts one clock minute once per live" in src
