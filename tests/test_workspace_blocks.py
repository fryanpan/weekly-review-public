"""The contiguous-block rule, and the union that keeps it honest.

The user's rule (2026-09-08): "look instead for contiguous blocks on a single
taskId that don't have more than a certain gap, and then consider that
contiguous block as the time span. Don't extend it before or after." Then union
— never sum — with Claude Code presence, which keeps its 10-minute windows.

The tests that matter most here are the negative ones: a rule that quietly pads
a block, or that adds the two instruments instead of unioning them, passes every
"the number went up" test anybody would think to write.
"""
import dataclasses
from datetime import datetime, timedelta, timezone
from pathlib import Path

from weekly_review.effort import (
    WORKSPACE_BLOCK_GAP_SEC,
    hands_on_hours_by_task,
    hands_on_intervals_by_task,
    presence_hours_by_task,
    workspace_attribution_key,
    workspace_block_intervals,
)
from weekly_review.events import Event
from weekly_review.segmentation import Task

T0 = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)


def _at(sec: float) -> str:
    return (T0 + timedelta(seconds=sec)).isoformat()


def _ws(sec, task_id=None, doc_id=None, kind="workspace_touch", dur=None,
        source="live_feedback", actor="user"):
    md = {}
    if task_id:
        md["task_id"] = task_id
    if doc_id:
        md["doc_id"] = doc_id
    if dur is not None:
        md["fixed_active_sec"] = dur
        md["start_ts"] = _at(sec - dur)
    return Event(
        ts=_at(sec), actor=actor, project="p", session_id="live-feedback:d",
        source_system=source, source_machine="m", kind=kind, seq=0,
        metadata=md,
    )


def _chat(sec, words=10):
    return Event(
        ts=_at(sec), actor="user", project="p", session_id="s1",
        source_system="claude_transcript", source_machine="m",
        kind="user_message", seq=int(sec), metadata={"words": words},
    )


def _task(event_ids, task_id="t1"):
    t = Task(task_id=task_id, project="p", title=task_id)
    for i in event_ids:
        t.event_ids.append(i)
    return t


def _hours(intervals):
    return sum((b - a).total_seconds() for a, b in intervals)


# --- the block rule ---------------------------------------------------------

def test_two_touches_15s_apart_are_one_block():
    """The task's own example: 15s apart, so one block, spanning exactly 15s."""
    evs = [_ws(0, task_id="t-a"), _ws(15, task_id="t-a")]
    blocks = workspace_block_intervals(evs)
    assert len(blocks) == 1
    assert _hours(blocks) == 15.0


def test_a_gap_longer_than_g_cuts_the_block():
    evs = [_ws(0, task_id="t-a"), _ws(WORKSPACE_BLOCK_GAP_SEC + 1, task_id="t-a")]
    blocks = workspace_block_intervals(evs)
    assert len(blocks) == 2
    assert _hours(blocks) == 0.0  # two instants, nothing added at either end


def test_a_gap_exactly_g_still_joins():
    evs = [_ws(0, task_id="t-a"), _ws(WORKSPACE_BLOCK_GAP_SEC, task_id="t-a")]
    assert len(workspace_block_intervals(evs)) == 1


def test_nothing_is_added_before_or_after_a_block():
    """The span is first event to last event. No padding, at either end.

    This is the negative test the rule exists for: a model that charges even a
    few seconds of "settling in" per block would pass every other test here and
    would inflate the week by the block count times that padding.
    """
    evs = [_ws(100, task_id="t-a"), _ws(120, task_id="t-a")]
    (start, end), = workspace_block_intervals(evs)
    assert start == T0 + timedelta(seconds=100)
    assert end == T0 + timedelta(seconds=120)


def test_a_lone_instant_is_worth_zero():
    assert _hours(workspace_block_intervals([_ws(0, task_id="t-a")])) == 0.0


def test_a_read_session_contributes_its_measured_span():
    """A bounded read session is itself a block of interactions."""
    evs = [_ws(300, task_id="t-a", kind="review_session", dur=120)]
    (start, end), = workspace_block_intervals(evs)
    assert (end - start).total_seconds() == 120


def test_a_key_switch_cuts_the_block_even_inside_the_gap():
    """Two tasks touched 5s apart are two blocks, not one.

    Per-key grouping would bridge the gap and charge the time to whichever task
    won — but he is on record doing something else in that gap, which is exactly
    the padding the rule removes.
    """
    evs = [_ws(0, task_id="t-a"), _ws(5, task_id="t-b"), _ws(10, task_id="t-a")]
    assert len(workspace_block_intervals(evs)) == 3


def test_a_task_card_read_joins_a_board_action_on_the_same_task():
    """The identity that makes the two stores one instrument."""
    evs = [
        _ws(0, task_id="t-a", kind="board_action", source="workspace_board"),
        _ws(30, task_id="t-a", doc_id="task:t-a", kind="review_session", dur=20),
    ]
    blocks = workspace_block_intervals(evs)
    assert len(blocks) == 1
    assert _hours(blocks) == 30.0


def test_an_agents_board_action_is_not_his_hands_on():
    evs = [_ws(0, task_id="t-a", kind="board_action", source="workspace_board",
               actor="agent"),
           _ws(30, task_id="t-a", kind="board_action", source="workspace_board",
               actor="agent")]
    assert workspace_block_intervals(evs) == []


def test_a_transcript_event_never_enters_the_block_rule():
    assert workspace_block_intervals([_chat(0), _chat(30)]) == []


def test_an_event_with_no_attribution_key_is_dropped():
    assert workspace_block_intervals([_ws(0)]) == []


def test_the_key_prefers_the_task_over_the_doc():
    assert workspace_attribution_key(_ws(0, task_id="t-a", doc_id="d1")) == "t-a"
    assert workspace_attribution_key(_ws(0, doc_id="d1")) == "doc:d1"


def test_a_looser_g_never_measures_less():
    evs = [_ws(s, task_id="t-a", kind="review_session", dur=5)
           for s in (0, 40, 200, 1000)]
    totals = [_hours(workspace_block_intervals(evs, gap_sec=g))
              for g in (0, 30, 60, 300, 900)]
    assert totals == sorted(totals)


# --- the union --------------------------------------------------------------

def test_the_two_instruments_are_unioned_not_added():
    """Overlapping transcript and workspace time is counted once.

    A read session that runs through a chat message describes one moment from
    two angles. Summing them would double-count exactly the time he was most
    engaged — driving an agent while watching its doc.
    """
    evs = [_chat(600, words=100),
           _ws(600, task_id="t-a", kind="review_session", dur=600)]
    task = _task([0, 1])
    combined = hands_on_hours_by_task([task], evs)["t1"]
    transcript, workspace = hands_on_intervals_by_task([task], evs)["t1"]
    parts = _hours(transcript) + _hours(workspace)
    assert combined * 3600 < parts
    assert combined * 3600 <= parts + 1e-9


def test_disjoint_time_from_the_two_instruments_adds_up():
    evs = [_chat(0, words=40),
           _ws(100_000, task_id="t-a", kind="review_session", dur=600)]
    task = _task([0, 1])
    combined = hands_on_hours_by_task([task], evs)["t1"] * 3600
    transcript, workspace = hands_on_intervals_by_task([task], evs)["t1"]
    assert abs(combined - (_hours(transcript) + _hours(workspace))) < 1e-6


def test_the_union_is_never_larger_than_the_sum():
    evs = [_chat(0, words=40), _chat(120, words=40),
           _ws(60, task_id="t-a", kind="review_session", dur=90),
           _ws(130, task_id="t-a"),
           _ws(4000, task_id="t-b", kind="review_session", dur=30)]
    task = _task(list(range(len(evs))))
    combined = hands_on_hours_by_task([task], evs)["t1"] * 3600
    transcript, workspace = hands_on_intervals_by_task([task], evs)["t1"]
    assert combined <= _hours(transcript) + _hours(workspace) + 1e-9


def test_the_workspace_side_no_longer_gets_the_ten_minute_window():
    """Two instruments, two tolerances — the point of the ruling.

    Under presence, two read sessions nine minutes apart stitch into one
    nine-minute interval. Under the block rule they are two blocks worth their
    own measured duration and nothing more.
    """
    evs = [_ws(0, task_id="t-a", doc_id="task:t-a", kind="review_session", dur=10),
           _ws(540, task_id="t-a", doc_id="task:t-a", kind="review_session", dur=10)]
    task = _task([0, 1])
    assert presence_hours_by_task([task], evs)["t1"] * 3600 > 500
    assert hands_on_hours_by_task([task], evs)["t1"] * 3600 == 20


def test_the_transcript_side_keeps_its_ten_minute_window():
    """Unchanged: chat messages nine minutes apart are still one interval."""
    evs = [_chat(0, words=1), _chat(540, words=1)]
    task = _task([0, 1])
    assert hands_on_hours_by_task([task], evs)["t1"] * 3600 >= 540


def test_no_events_is_zero_not_an_error():
    assert hands_on_hours_by_task([_task([])], []) == {"t1": 0.0}
    assert workspace_block_intervals([]) == []


def test_a_tasks_blocks_come_from_the_whole_weeks_partition():
    """A task cannot bridge a gap the week knows he spent elsewhere.

    Blocking inside one task's own events would not see the interrupting event,
    so the task would report one long block where the week reports two short
    ones — a per-task figure that cannot be reconciled with the week's.
    """
    evs = [_ws(0, task_id="t-a"), _ws(30, task_id="t-b"), _ws(50, task_id="t-a")]
    mine = _task([0, 2])
    blocks = workspace_block_intervals(evs, mine.event_ids)
    assert len(blocks) == 2
    assert _hours(blocks) == 0.0


def test_a_block_shared_by_two_tasks_is_claimed_by_both():
    """Documented, not silently averaged: the week-wide union is what bounds it."""
    evs = [_ws(0, task_id="t-a", kind="review_session", dur=30),
           _ws(20, task_id="t-a", kind="review_session", dur=10)]
    a, b = _task([0], "ta"), _task([1], "tb")
    per_task = hands_on_hours_by_task([a, b], evs)
    assert per_task["ta"] == per_task["tb"] > 0
    week = _hours(workspace_block_intervals(evs)) / 3600
    assert per_task["ta"] + per_task["tb"] > week


# --- comment windows (the user, 2026-09-14) -------------------------------------

def _comment(sec, words, task_id="t-a"):
    e = _ws(sec, task_id=task_id, kind="user_message")
    e.metadata["fixed_active_sec"] = 120 + words / 40 * 60
    e.metadata["words"] = words
    return e


def test_a_lone_comment_counts_its_typing_and_a_minute_of_reading():
    """Nothing on the doc led to it, so the reading is assumed: one minute.
    40 words at 40 wpm is 60s of typing."""
    evs = [_comment(1000, words=40)]
    assert hands_on_hours_by_task([_task([0])], evs)["t1"] * 3600 == 120


def test_activity_just_before_a_comment_replaces_the_assumed_minute():
    """Read 600..900, typing starts at 940: the 40s between coalesce, no extra minute."""
    evs = [_ws(900, task_id="t-a", kind="review_session", dur=300), _comment(1000, words=40)]
    assert hands_on_hours_by_task([_task([0, 1])], evs)["t1"] * 3600 == 400


def test_activity_long_before_a_comment_does_not_count_as_leading_to_it():
    """Read ends at 800, typing starts at 940: 140s is more than a minute, so
    the comment gets its own minute and the gap stays uncounted."""
    evs = [_ws(800, task_id="t-a", kind="review_session", dur=200), _comment(1000, words=40)]
    assert hands_on_hours_by_task([_task([0, 1])], evs)["t1"] * 3600 == 200 + 120


def test_a_read_session_around_the_comment_covers_its_reading():
    evs = [_ws(1100, task_id="t-a", kind="review_session", dur=200), _comment(1000, words=40)]
    assert hands_on_hours_by_task([_task([0, 1])], evs)["t1"] * 3600 == 200


def test_back_to_back_comments_coalesce_instead_of_each_taking_a_minute():
    evs = [_comment(1000, words=40), _comment(1030, words=40)]
    # first 880..1000; second types 970..1030 and the first comment led to it
    assert hands_on_hours_by_task([_task([0, 1])], evs)["t1"] * 3600 == 150


def test_activity_on_another_doc_does_not_lead_to_the_comment():
    evs = [_ws(950, task_id="t-b", kind="review_session", dur=100), _comment(1000, words=40)]
    _tr, workspace = hands_on_intervals_by_task([_task([1])], evs)["t1"]
    assert (T0 + timedelta(seconds=880), T0 + timedelta(seconds=1000)) in workspace
    assert hands_on_hours_by_task([_task([1])], evs)["t1"] * 3600 == 120


def test_billing_can_keep_the_old_figure():
    evs = [_comment(1000, words=40)]
    assert hands_on_hours_by_task([_task([0])], evs, comment_windows=False)["t1"] == 0.0


def test_agent_comments_and_transcript_messages_get_no_workspace_window():
    agent = dataclasses.replace(_comment(1000, words=40), actor="agent")
    evs = [agent, _chat(5000, words=40)]
    _tr, workspace = hands_on_intervals_by_task([_task([0, 1])], evs)["t1"]
    assert workspace == []


def test_the_intervals_carry_the_comment_window_by_default():
    """The worksheet and multiplier refresh call this without the flag."""
    _tr, workspace = hands_on_intervals_by_task([_task([0])], [_comment(1000, words=40)])["t1"]
    assert _hours(workspace) == 120


def test_billing_and_the_week_total_are_wired_as_decided():
    """Call-site checks: billing keeps the old figure, the week union gains the windows."""
    root = Path(__file__).resolve().parents[1] / "src" / "weekly_review"
    assert "comment_windows=False" in (root / "billing.py").read_text()
    cli = (root / "cli.py").read_text()
    assert "_union_seconds(_tr_intervals + _ws_blocks + _comment_windows)" in cli


def test_the_activity_streams_copy_of_the_comment_does_not_lead_to_it():
    touch = _ws(1000, task_id="t-a")
    touch.metadata["activity_type"] = "comment"
    evs = [touch, _comment(1000, words=40)]
    assert hands_on_hours_by_task([_task([0, 1])], evs)["t1"] * 3600 == 120


# --- review answer reading (the user, 2026-09-14) --------------------------------

def _answer(sec, task_id="t-a", item=None, event="decision.answered"):
    e = _ws(sec, task_id=task_id, kind="board_action", source="workspace_board")
    e.metadata["board_event"] = event
    if item:
        e.metadata["review_item_id"] = item
    return e


def test_a_lone_review_answer_counts_two_minutes_of_reading():
    assert hands_on_hours_by_task([_task([0])], [_answer(1000)])["t1"] * 3600 == 120


def test_asking_for_more_info_is_an_answer_too():
    evs = [_answer(1000, event="decision.info_requested")]
    assert hands_on_hours_by_task([_task([0])], evs)["t1"] * 3600 == 120


def test_an_answer_on_another_review_item_cuts_the_reading_short():
    """Answered r-1 at 920, r-2 at 1000 on the same task: r-2 read for 80s.
    (80s apart, so no 60s block joins them and the window is all that counts.)"""
    evs = [_answer(920, item="r-1"), _answer(1000, item="r-2")]
    assert hands_on_intervals_by_task([_task([1])], evs)["t1"][1] == [
        (T0 + timedelta(seconds=1000), T0 + timedelta(seconds=1000)),
        (T0 + timedelta(seconds=920), T0 + timedelta(seconds=1000)),
    ]


def test_activity_on_another_page_cuts_the_reading_short():
    evs = [_ws(930, task_id="t-b", kind="review_session", dur=100), _answer(1000)]
    _tr, workspace = hands_on_intervals_by_task([_task([1])], evs)["t1"]
    assert (T0 + timedelta(seconds=930), T0 + timedelta(seconds=1000)) in workspace


def test_activity_on_the_same_task_does_not_cut_the_reading():
    evs = [_ws(930, task_id="t-a", kind="review_session", dur=100), _answer(1000)]
    _tr, workspace = hands_on_intervals_by_task([_task([1])], evs)["t1"]
    assert (T0 + timedelta(seconds=880), T0 + timedelta(seconds=1000)) in workspace


def test_activity_more_than_two_minutes_before_does_not_cut():
    evs = [_ws(800, task_id="t-b", kind="review_session", dur=100), _answer(1000)]
    _tr, workspace = hands_on_intervals_by_task([_task([1])], evs)["t1"]
    assert (T0 + timedelta(seconds=880), T0 + timedelta(seconds=1000)) in workspace


def test_an_agents_answer_gets_no_reading_window():
    agent = dataclasses.replace(_answer(1000), actor="agent")
    _tr, workspace = hands_on_intervals_by_task([_task([0])], [agent])["t1"]
    assert workspace == []


# --- dictated requests ---------------------------------------------------------

def _dictation(sec, words, workspace="w-1", actor="user"):
    e = _ws(sec, kind="board_action", source="workspace_board", actor=actor)
    e.metadata.update(board_event="voice.request", workspace_id=workspace, words=words)
    return e


def test_a_lone_dictation_counts_its_speaking_and_a_minute_of_working_out_what_to_ask():
    """150 words at 150 wpm is 60s spoken, plus the comment rule's minute."""
    assert hands_on_hours_by_task([_task([0])], [_dictation(1000, 150)])["t1"] * 3600 == 120


def test_a_dictation_is_spoken_not_typed():
    """300 words typed would be 450s; spoken it is 120s, plus the minute."""
    assert hands_on_hours_by_task([_task([0])], [_dictation(1000, 300)])["t1"] * 3600 == 180


def test_activity_on_the_board_just_before_a_dictation_replaces_the_minute():
    """He moved a task on the board at 900; speaking starts at 940, so the
    window runs back to 900 instead of taking the minute back to 880."""
    moved = _ws(900, kind="board_action", source="workspace_board")
    moved.metadata.update(board_event="task.transitioned", workspace_id="w-1")
    evs = [moved, _dictation(1000, 150)]
    assert hands_on_hours_by_task([_task([0, 1])], evs)["t1"] * 3600 == 100


def test_a_task_he_files_counts_its_typing_like_a_comment():
    """40 words of title and body at 40 wpm is 60s, plus the minute."""
    filed = _ws(1000, task_id="t-new", kind="board_action", source="workspace_board")
    filed.metadata.update(board_event="task.created", workspace_id="w-1", words=40)
    assert hands_on_hours_by_task([_task([0])], [filed])["t1"] * 3600 == 120


def test_an_agents_dictation_gets_no_speaking_window():
    _tr, workspace = hands_on_intervals_by_task([_task([0])], [_dictation(1000, 150, actor="agent")])["t1"]
    assert workspace == []


def test_the_week_total_carries_the_reading_windows():
    cli = (Path(__file__).resolve().parents[1] / "src" / "weekly_review" / "cli.py").read_text()
    assert "_comment_windows = list(workspace_windows(all_events).values())" in cli


def test_a_comment_window_can_fall_outside_every_block():
    """The premise the week-wide check rests on, pinned.

    A comment's typing window and a review answer's reading time are part of
    the workspace instrument, not decoration on the block rule: a lone comment
    with no touch near it makes a block of zero length and a window of real
    minutes. So a week's workspace hours are blocks UNION windows, and any
    check that weighs the instrument against the whole has to count it that way
    or it is comparing two different instruments.
    """
    from weekly_review.effort import workspace_block_intervals, workspace_windows

    evs = [_ws(0, task_id="t-a", kind="user_message", actor="user")]
    evs[0].metadata["words"] = 120
    blocks = workspace_block_intervals(evs)
    windows = list(workspace_windows(evs).values())
    assert _hours(blocks) == 0.0, "a lone comment is a zero-length block"
    assert _hours(windows) > 0.0, "and it is not zero time"


def test_the_week_wide_check_weighs_what_the_union_unions():
    """A union of three terms against a sum of two always reads as a violation.

    `_combined` unions transcripts, workspace blocks AND the comment/answer
    windows. The sum it is weighed against used to carry the block rule alone,
    so every run whose comments fell outside a block printed INVARIANT VIOLATED
    on a figure that was correct -- 5.28h of the Aug era week and 7.73h of the
    Sep one. A check that cries wolf on the headline number is worse than no
    check, because the next reader stops reading the line.

    Asserted against the source because the expressions are inline in a 600-line
    command function; the defect was never in a function that could be called.
    """
    import inspect

    from weekly_review import cli

    src = inspect.getsource(cli)
    assert "_combined = _union_seconds(_tr_intervals + _ws_blocks + _comment_windows)" in src
    assert "_ws_all_hours = _union_seconds(_ws_blocks + _comment_windows)" in src
    assert "_sum_of_parts = _tr_hours + _ws_all_hours" in src
    assert "_sum_of_parts = _tr_hours + _ws_hours" not in src, (
        "the sum is back to the block rule alone, which the union does not use")
