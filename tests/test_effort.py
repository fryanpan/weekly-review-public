from datetime import datetime, timedelta, timezone

import pytest

import weekly_review.effort as effort
from weekly_review.events import Event
from weekly_review.segmentation import Task, segment
from weekly_review.effort import (
    active_time_seconds_for_user_message,
    cognitive_wall_clock_seconds,
    context_switch_count,
    resumption_count,
    active_hours_by_task,
    agent_active_hours_by_task,
    agent_lane_hours_by_task,
    lane_task_index,
    labor_and_agent_hours_by_task,
    _agent_intervals_for_task,
)


def _ev(kind: str, actor: str, ts: str, project: str, seq: int, words: int = 10) -> Event:
    return Event(
        ts=ts, actor=actor, project=project, session_id="s",
        source_system="claude_transcript", source_machine="mm",
        kind=kind, seq=seq,
        content_snippet="x" * max(1, words),
        metadata={"words": words},
    )


def test_active_time_reading_plus_typing():
    prior_agent = _ev("agent_message", "agent", "2026-04-15T10:00:00Z", "wr", 0, words=300)
    user = _ev("user_message", "user", "2026-04-15T10:00:30Z", "wr", 1, words=40)
    sec = active_time_seconds_for_user_message(user, prior_agent)
    # reading: 300 / 300 wpm = 1 min = 60s; typing: 40 / 40 wpm = 1 min = 60s; total 120
    assert sec == 120.0


def test_active_time_capped_at_20_min():
    prior_agent = _ev("agent_message", "agent", "2026-04-15T10:00:00Z", "wr", 0, words=100_000)
    user = _ev("user_message", "user", "2026-04-15T10:00:30Z", "wr", 1, words=10)
    sec = active_time_seconds_for_user_message(user, prior_agent)
    assert sec == 20 * 60


def test_cognitive_wall_clock_spans():
    events = [
        _ev("user_message", "user", "2026-04-15T10:00:00Z", "wr", 0),
        _ev("agent_message", "agent", "2026-04-15T10:05:00Z", "wr", 1),
        _ev("user_message", "user", "2026-04-15T10:25:00Z", "wr", 2),  # 20 min gap => new span
        _ev("agent_message", "agent", "2026-04-15T10:26:00Z", "wr", 3),
    ]
    sec = cognitive_wall_clock_seconds(events)
    # span 1: 5 min; span 2: 1 min; total 6 min = 360
    assert sec == 6 * 60


def test_context_switch_count_same_day_different_project():
    events = [
        _ev("user_message", "user", "2026-04-15T10:00:00Z", "wr", 0),
        _ev("user_message", "user", "2026-04-15T11:00:00Z", "wr", 1),
        _ev("user_message", "user", "2026-04-15T12:00:00Z", "bike-map", 2),  # switch
        _ev("user_message", "user", "2026-04-15T13:00:00Z", "wr", 3),  # switch
        _ev("user_message", "user", "2026-04-16T09:00:00Z", "bike-map", 4),  # new day, not a switch
    ]
    count = context_switch_count(events)
    assert count == 2


def test_resumption_count_task_level():
    events = [
        _ev("user_message", "user", "2026-04-15T10:00:00Z", "wr", 0),
        _ev("user_message", "user", "2026-04-15T14:00:00Z", "wr", 1),  # same-day, no resumption
        _ev("user_message", "user", "2026-04-17T10:00:00Z", "wr", 2),  # >24h gap, resumption
        _ev("user_message", "user", "2026-04-19T10:00:00Z", "wr", 3),  # >24h gap, resumption
    ]
    task = Task(task_id="t1", project="wr", title="x", event_ids=[0, 1, 2, 3])
    assert resumption_count(task, events) == 2


def test_active_hours_by_task_sums_per_message():
    import dataclasses

    def card(e: Event) -> Event:
        # His comments on a board task: the one message shape segment() still
        # places (a plain transcript message starts no task).
        return dataclasses.replace(e, source_system="live_feedback",
                                   metadata={**e.metadata, "task_id": "t-AAA"})

    events = [
        card(_ev("user_message", "user", "2026-04-15T10:00:00Z", "wr", 0, words=40)),
        _ev("agent_message", "agent", "2026-04-15T10:00:30Z", "wr", 1, words=300),
        card(_ev("user_message", "user", "2026-04-15T10:02:00Z", "wr", 2, words=40)),
    ]
    tasks = segment(events)
    hours = active_hours_by_task(tasks, events)
    held = [t.task_id for t in tasks if 0 in t.event_ids]
    assert held and hours[held[0]] > 0


def test_hands_on_blocks_by_task_two_min_gap():
    from weekly_review.effort import hands_on_blocks_by_task
    events = [
        _ev("user_message", "user", "2026-04-15T10:00:00Z", "wr", 0),
        _ev("user_message", "user", "2026-04-15T10:01:30Z", "wr", 1),  # <2min -> same block
        _ev("user_message", "user", "2026-04-15T10:10:00Z", "wr", 2),  # new block
        _ev("agent_message", "agent", "2026-04-15T10:11:00Z", "wr", 3),  # not counted
    ]
    task = Task(task_id="t1", project="wr", title="x", event_ids=[0, 1, 2, 3])
    assert hands_on_blocks_by_task([task], events)["t1"] == 2


def test_human_input_chars_prefers_chars_metadata():
    from weekly_review.effort import human_input_chars_by_task
    with_chars = _ev("user_message", "user", "2026-04-15T10:00:00Z", "wr", 0, words=10)
    with_chars.metadata["chars"] = 137
    legacy = _ev("user_message", "user", "2026-04-15T10:05:00Z", "wr", 1, words=10)  # no chars -> 10*6
    agent = _ev("agent_message", "agent", "2026-04-15T10:06:00Z", "wr", 2, words=500)
    task = Task(task_id="t1", project="wr", title="x", event_ids=[0, 1, 2])
    assert human_input_chars_by_task([task], [with_chars, legacy, agent])["t1"] == 137 + 60


def test_dominant_model_by_task_majority_wins():
    from weekly_review.effort import dominant_model_by_task
    evs = []
    for i, m in enumerate(["claude-opus-4-8", "claude-opus-4-8", "claude-fable-5"]):
        e = _ev("agent_message", "agent", f"2026-04-15T10:0{i}:00Z", "wr", i)
        e.metadata["model"] = m
        evs.append(e)
    evs.append(_ev("user_message", "user", "2026-04-15T10:04:00Z", "wr", 3))
    task = Task(task_id="t1", project="wr", title="x", event_ids=[0, 1, 2, 3])
    assert dominant_model_by_task([task], evs)["t1"] == "claude-opus-4-8"


# --- agent labor hours (concurrent-agent fan-out) ---------------------------

def _lane_ev(ts: str, seq: int, lane: str) -> Event:
    """An agent event tagged with the lane (transcript) that produced it."""
    return Event(
        ts=ts, actor="agent", project="wr", session_id="s",
        source_system="claude_transcript", source_machine="mm",
        kind="agent_message", seq=seq,
        content_snippet="x",
        metadata={"words": 10, "agent_lane": lane},
    )


def _task_over(events: list[Event]) -> Task:
    return Task(
        task_id="t1", project="wr", title="t",
        event_ids=list(range(len(events))),
    )


def test_agent_labor_sums_concurrent_lanes_while_agent_hours_collapses_them():
    # Three agents each busy for the SAME 10-minute window.
    events: list[Event] = []
    for i, lane in enumerate(["agent-a", "agent-b", "agent-c"]):
        events.append(_lane_ev("2026-07-10T10:00:00Z", i * 2, lane))
        events.append(_lane_ev("2026-07-10T10:10:00Z", i * 2 + 1, lane))
    task = _task_over(events)

    labor = agent_lane_hours_by_task([task], events)["t1"]
    _wall, agent_h, _bryan = labor_and_agent_hours_by_task([task], events)

    # Elapsed: the three lanes interleave into one 10-min burst.
    assert agent_h["t1"] == pytest.approx(10 / 60, abs=0.01)
    # Labor: 3 lanes x 10 min = 0.5h. This is the whole point of the metric.
    assert labor == pytest.approx(0.5, abs=0.01)
    assert labor / agent_h["t1"] == pytest.approx(3.0, abs=0.05)


def test_agent_labor_equals_agent_hours_when_work_is_sequential():
    # One agent, one continuous burst — no fan-out, so no parallelism premium.
    events = [
        _lane_ev("2026-07-10T10:00:00Z", 0, "main"),
        _lane_ev("2026-07-10T10:10:00Z", 1, "main"),
    ]
    task = _task_over(events)
    labor = agent_lane_hours_by_task([task], events)["t1"]
    _wall, agent_h, _bryan = labor_and_agent_hours_by_task([task], events)
    assert labor == pytest.approx(agent_h["t1"], abs=0.001)


def test_agent_labor_degrades_to_main_lane_when_lane_untagged():
    # Runs predating lane tagging have no agent_lane in metadata. They must fall
    # back to a single "main" lane (== agent hours), never to zero.
    events = [
        _ev("agent_message", "agent", "2026-07-10T10:00:00Z", "wr", 0),
        _ev("agent_message", "agent", "2026-07-10T10:10:00Z", "wr", 1),
    ]
    task = _task_over(events)
    labor = agent_lane_hours_by_task([task], events)["t1"]
    _wall, agent_h, _bryan = labor_and_agent_hours_by_task([task], events)
    assert labor == pytest.approx(agent_h["t1"], abs=0.001)
    assert labor > 0


def test_agent_labor_ignores_user_events():
    events = [
        _ev("user_message", "user", "2026-07-10T10:00:00Z", "wr", 0),
        _ev("user_message", "user", "2026-07-10T10:30:00Z", "wr", 1),
    ]
    task = _task_over(events)
    assert agent_lane_hours_by_task([task], events)["t1"] == 0.0


def test_agent_labor_separates_concurrent_sessions_sharing_the_main_lane():
    # Two Claude Code sessions running at once. Both call their main loop "main";
    # keying lanes on the stem alone would merge them and erase the parallelism.
    def ev(ts, seq, session):
        return Event(
            ts=ts, actor="agent", project="wr", session_id=session,
            source_system="claude_transcript", source_machine="mm",
            kind="agent_message", seq=seq, content_snippet="x",
            metadata={"words": 10, "agent_lane": "main"},
        )

    events = [
        ev("2026-07-10T10:00:00Z", 0, "sess-a"),
        ev("2026-07-10T10:10:00Z", 1, "sess-a"),
        ev("2026-07-10T10:00:00Z", 2, "sess-b"),
        ev("2026-07-10T10:10:00Z", 3, "sess-b"),
    ]
    task = _task_over(events)
    labor = agent_lane_hours_by_task([task], events)["t1"]
    # Two sessions x 10 min of overlapping work = 0.33h of labor, not 0.17h.
    assert labor == pytest.approx(2 * 10 / 60, abs=0.01)


def test_blocked_parent_earns_no_labor_while_children_run():
    # The orchestrator dispatches at 10:00 and gets its result at 10:40. It is
    # BLOCKED for that whole window, so it must not be billed as a 3rd worker.
    def ev(kind, ts, seq, lane):
        return Event(
            ts=ts, actor="agent", project="wr", session_id="s",
            source_system="claude_transcript", source_machine="mm",
            kind=kind, seq=seq, content_snippet="x",
            metadata={"words": 5, "agent_lane": lane},
        )

    events = [
        ev("tool_call", "2026-07-10T10:00:00Z", 0, "main"),
        ev("agent_message", "2026-07-10T10:05:00Z", 1, "agent-a"),
        ev("agent_message", "2026-07-10T10:15:00Z", 2, "agent-a"),
        ev("agent_message", "2026-07-10T10:05:00Z", 3, "agent-b"),
        ev("agent_message", "2026-07-10T10:15:00Z", 4, "agent-b"),
        ev("tool_result", "2026-07-10T10:40:00Z", 5, "main"),
    ]
    task = _task_over(events)
    labor = agent_lane_hours_by_task([task], events)["t1"]
    # Two children x 10 min = 0.33h, and the parent earns nothing: its dispatch
    # and its result are 40 min apart, past even the tool-call tolerance, so its
    # own lane has no working interval. It is NOT billed a 3rd worker's 40 min.
    assert labor == pytest.approx(2 * 10 / 60, abs=0.01)


def test_short_dispatch_does_not_bill_the_blocked_parent_as_an_extra_worker():
    # The case the blocked-time subtraction actually fixes: a dispatch SHORTER
    # than the stitch, where the parent's tool_call -> tool_result would
    # otherwise stitch into one continuous burst spanning the whole child window.
    def ev(kind, ts, seq, lane):
        return Event(
            ts=ts, actor="agent", project="wr", session_id="s",
            source_system="claude_transcript", source_machine="mm",
            kind=kind, seq=seq, content_snippet="x",
            metadata={"words": 5, "agent_lane": lane},
        )

    events = [
        ev("tool_call", "2026-07-10T10:00:00Z", 0, "main"),
        ev("agent_message", "2026-07-10T10:00:00Z", 1, "agent-a"),
        ev("agent_message", "2026-07-10T10:10:00Z", 2, "agent-a"),
        ev("agent_message", "2026-07-10T10:00:00Z", 3, "agent-b"),
        ev("agent_message", "2026-07-10T10:10:00Z", 4, "agent-b"),
        ev("tool_result", "2026-07-10T10:10:00Z", 5, "main"),
    ]
    task = _task_over(events)
    labor = agent_lane_hours_by_task([task], events)["t1"]
    # Two children x 10 min = 0.33h. The parent's 10-min burst is entirely
    # covered by its children, so it adds nothing — 2 workers, not 3.
    assert labor == pytest.approx(2 * 10 / 60, abs=0.01)


def test_solo_parent_still_earns_labor_when_no_children():
    # No fan-out: the main lane bills normally, so the metric stays comparable
    # across weeks that happen to have no subagents.
    events = [
        _lane_ev("2026-07-10T10:00:00Z", 0, "main"),
        _lane_ev("2026-07-10T10:10:00Z", 1, "main"),
    ]
    task = _task_over(events)
    assert agent_lane_hours_by_task([task], events)["t1"] == pytest.approx(10 / 60, abs=0.01)


def test_presence_counts_the_gaps_between_messages():
    """Two messages 8 minutes apart are one continuous stretch of work.

    The per-message model charges only the typing+reading windows and scores the
    8-minute gap as zero; presence spans it. This is the correction the user forced
    on 2026-08-17, when the per-message number came back at roughly half his
    real week.
    """
    from weekly_review.effort import presence_hours_by_task

    events = [
        _ev("user_message", "user", "2026-04-15T10:00:00Z", "wr", 0, words=40),
        _ev("user_message", "user", "2026-04-15T10:08:00Z", "wr", 1, words=40),
    ]
    task = Task(task_id="t1", project="wr", title="x", event_ids=[0, 1])
    presence = presence_hours_by_task([task], events)["t1"]
    active = active_hours_by_task([task], events)["t1"]

    # 8min span, plus the 60s of typing that precedes the first message and the
    # 30s grace after the last = 9.5 min.
    assert presence == pytest.approx(9.5 / 60, abs=0.01)
    assert presence > active


def test_presence_breaks_across_a_long_gap():
    """A gap wider than the tolerance is a real break, not thinking time."""
    from weekly_review.effort import presence_hours_by_task

    events = [
        _ev("user_message", "user", "2026-04-15T10:00:00Z", "wr", 0, words=40),
        _ev("user_message", "user", "2026-04-15T10:05:00Z", "wr", 1, words=40),
        _ev("user_message", "user", "2026-04-15T14:00:00Z", "wr", 2, words=40),  # 4h later
    ]
    task = Task(task_id="t1", project="wr", title="x", event_ids=[0, 1, 2])
    presence = presence_hours_by_task([task], events)["t1"]

    # The 4h lunch break must NOT be billed as work.
    assert presence < 0.3


def test_presence_never_below_active_for_an_isolated_message():
    """A lone message stitches to a zero-length span, so it must fall back to
    its modeled typing time rather than reporting nothing."""
    from weekly_review.effort import presence_hours_by_task

    events = [
        _ev("agent_message", "agent", "2026-04-15T10:00:00Z", "wr", 0, words=300),
        _ev("user_message", "user", "2026-04-15T10:05:00Z", "wr", 1, words=400),
    ]
    task = Task(task_id="t1", project="wr", title="x", event_ids=[0, 1])
    presence = presence_hours_by_task([task], events)["t1"]
    active = active_hours_by_task([task], events)["t1"]

    assert active > 0
    assert presence >= active


def test_presence_tolerance_is_configurable():
    from weekly_review.effort import presence_hours_by_task

    events = [
        _ev("user_message", "user", "2026-04-15T10:00:00Z", "wr", 0, words=40),
        _ev("user_message", "user", "2026-04-15T10:12:00Z", "wr", 1, words=40),
    ]
    task = Task(task_id="t1", project="wr", title="x", event_ids=[0, 1])

    tight = presence_hours_by_task([task], events, stitch_sec=10 * 60)["t1"]
    loose = presence_hours_by_task([task], events, stitch_sec=15 * 60)["t1"]

    # At 10min the 12min gap is a break, so only the two modeled windows count
    # (1.5 min each). At 15min it stitches into one 12min span + 1.5min of
    # lead-in and grace.
    assert tight == pytest.approx(3 / 60, abs=0.01)
    assert loose == pytest.approx(13.5 / 60, abs=0.01)
    assert loose > tight


# --- A burst may not bridge a gap its own worker spent elsewhere -------------
#
# `_bursts` stitched a task's agent events with gaps under 30 minutes while
# looking only at that task's own stream, so a lane interleaving twenty tasks
# billed each of them for the time it spent on the other nineteen. The human
# lane already refuses this shape (run-length blocking cuts on any key switch);
# these hold the agent lane to the same rule. Measured on the stored events:
# 2026-04-27 -0.4% of agent hours, 2026-08-17 -3.0%, 2026-09-07 -11.7%.


def _lane_ev_on(ts: str, seq: int, lane: str = "main", session: str = "s",
                kind: str = "agent_message") -> Event:
    return Event(
        ts=ts, actor="agent", project="wr", session_id=session,
        source_system="claude_transcript", source_machine="mm",
        kind=kind, seq=seq, content_snippet="x",
        metadata={"words": 10, "agent_lane": lane},
    )


def _two_tasks(events: list[Event], mine: list[int], theirs: list[int]):
    return [Task(task_id="t1", project="wr", title="mine", event_ids=mine),
            Task(task_id="t2", project="wr", title="theirs", event_ids=theirs)]


def _agent_h(tasks, events):
    return labor_and_agent_hours_by_task(tasks, events)[1]


def test_a_gap_the_worker_spent_on_another_task_is_not_bridged():
    # One lane, ten minutes, and in the middle of it the worker was demonstrably
    # on t2. Billing t1 for those ten minutes charges one worker to two tasks at
    # once — the thing the concurrency audit exists to detect. The window sits
    # inside AGENT_QUIET_STITCH_SEC so the cut is the only thing that can act.
    events = [_lane_ev_on("2026-07-10T10:00:00Z", 0),
              _lane_ev_on("2026-07-10T10:05:00Z", 1),
              _lane_ev_on("2026-07-10T10:10:00Z", 2)]
    agent = _agent_h(_two_tasks(events, [0, 2], [1]), events)
    assert agent["t1"] == pytest.approx(0.0, abs=0.001)


def test_a_gap_the_worker_spent_on_nothing_is_still_bridged():
    # The control that keeps the rule honest: with the same two events and
    # nothing in between, the burst stitches. The lane cut is about who the
    # worker was serving, not about how long it may sit quiet — that is
    # AGENT_QUIET_STITCH_SEC, and this gap is well inside it.
    events = [_lane_ev_on("2026-07-10T10:00:00Z", 0),
              _lane_ev_on("2026-07-10T10:10:00Z", 1)]
    task = Task(task_id="t1", project="wr", title="mine", event_ids=[0, 1])
    assert _agent_h([task], events)["t1"] == pytest.approx(10 / 60, abs=0.001)


def test_an_unclaimed_event_in_the_gap_does_not_cut():
    # An event nobody attached is not evidence the worker left. Cutting on it
    # would make the metric a function of segmentation coverage.
    events = [_lane_ev_on("2026-07-10T10:00:00Z", 0),
              _lane_ev_on("2026-07-10T10:05:00Z", 1),
              _lane_ev_on("2026-07-10T10:10:00Z", 2)]
    task = Task(task_id="t1", project="wr", title="mine", event_ids=[0, 2])
    assert _agent_h([task], events)["t1"] == pytest.approx(10 / 60, abs=0.001)


def test_a_different_lane_working_does_not_cut_this_one():
    # The cut is keyed on the lane of the gap's left edge — the worker whose
    # burst is being asked to stay open. A sibling agent working on t2 is a
    # different worker, and agent hours merge every lane into one timeline on
    # purpose, so this burst survives.
    events = [_lane_ev_on("2026-07-10T10:00:00Z", 0, "main"),
              _lane_ev_on("2026-07-10T10:05:00Z", 1, "agent-b"),
              _lane_ev_on("2026-07-10T10:10:00Z", 2, "main")]
    agent = _agent_h(_two_tasks(events, [0, 2], [1]), events)
    assert agent["t1"] == pytest.approx(10 / 60, abs=0.001)


def test_another_session_working_does_not_cut_this_one():
    # Two Claude Code sessions both label their main loop "main". Keying on the
    # bare lane stem would let one session's work cut the other's burst.
    events = [_lane_ev_on("2026-07-10T10:00:00Z", 0, "main", "s"),
              _lane_ev_on("2026-07-10T10:05:00Z", 1, "main", "other"),
              _lane_ev_on("2026-07-10T10:10:00Z", 2, "main", "s")]
    agent = _agent_h(_two_tasks(events, [0, 2], [1]), events)
    assert agent["t1"] == pytest.approx(10 / 60, abs=0.001)


def test_interleaved_tasks_no_longer_bill_the_worker_twice():
    # The property that matters in aggregate. One lane alternates between two
    # tasks over 35 minutes. Summed per task the old rule reported 50 minutes of
    # agent time from a worker that was present for 35.
    stamps = ["10:00", "10:05", "10:10", "10:15", "10:20", "10:25", "10:30", "10:35"]
    events = [_lane_ev_on(f"2026-07-10T{s}:00Z", i) for i, s in enumerate(stamps)]
    tasks = _two_tasks(events, [0, 1, 4, 5], [2, 3, 6, 7])
    agent = _agent_h(tasks, events)
    assert agent["t1"] == pytest.approx(10 / 60, abs=0.001)
    assert agent["t2"] == pytest.approx(10 / 60, abs=0.001)
    # Never more than the lane was actually present for.
    assert agent["t1"] + agent["t2"] <= 35 / 60 + 1e-9


def test_agent_labor_is_cut_the_same_way():
    # agent_labor_hours bursts each lane separately and sums them, so it has the
    # same hole and needs the same rule.
    events = [_lane_ev_on("2026-07-10T10:00:00Z", 0, "agent-a"),
              _lane_ev_on("2026-07-10T10:05:00Z", 1, "agent-a"),
              _lane_ev_on("2026-07-10T10:10:00Z", 2, "agent-a")]
    tasks = _two_tasks(events, [0, 2], [1])
    assert agent_lane_hours_by_task(tasks, events)["t1"] == pytest.approx(0.0, abs=0.001)


def test_the_cut_cannot_hand_out_a_second_four_hour_allowance():
    # The 4h cap is per burst, so splitting one burst into three hands out three
    # allowances and the "fix" INVENTS hours. Applied the obvious way this added
    # 14.5h to the week of 2026-08-17 and 26.7h to 2026-09-07, one task going
    # 4.03h -> 11.64h. The cut is applied inside the capped stitch instead, so it
    # can only remove time.
    #
    # Nine hours of t1 work at ten-minute intervals — one burst, capped to 4h —
    # with t2 touched twice inside it. The touches have to sit BETWEEN t1's own
    # events rather than replace them, or the hole they leave exceeds the
    # 30-minute stitch and the burst splits for the ordinary reason. The first
    # version of this test made that mistake and proved nothing about the cap.
    events, mine, theirs = [], [], []
    for i in range(55):
        h, m = divmod(i * 10, 60)
        events.append(_lane_ev_on(f"2026-07-10T{10 + h:02d}:{m:02d}:00Z", len(events)))
        mine.append(len(events) - 1)
        if (h, m) in ((3, 0), (6, 0)):
            events.append(_lane_ev_on(f"2026-07-10T{10 + h:02d}:05:00Z", len(events)))
            theirs.append(len(events) - 1)
    tasks = _two_tasks(events, mine, theirs)
    agent = _agent_h(tasks, events)
    assert agent["t1"] <= 4.0 + 1e-9
    # And it really did have to be cut: uncut this is exactly the 4h cap.
    assert agent["t1"] < 4.0


def test_one_agent_event_still_reports_a_burst():
    # A lone agent event is a zero-length burst. It buys no time, but it is how
    # the wall-clock account knows the agent was here at all — drop it and the
    # account tells a reviewer "no agent working time: commits, PRs and board
    # actions are instants" about a task that has an agent message on it.
    events = [_lane_ev_on("2026-07-10T10:00:00Z", 0)]
    task = Task(task_id="t1", project="wr", title="mine", event_ids=[0])
    ivs = _agent_intervals_for_task(task, events, lane_task_index([task], events))
    assert len(ivs) == 1
    assert ivs[0][0] == ivs[0][1]


def test_an_event_on_two_tasks_counts_for_the_first():
    # Segmentation can attach one event to two tasks. The index resolves it the
    # way the explain index does — first task wins — so an event t1 also owns
    # never reads as "the worker left t1".
    #
    # It has to be a non-agent event to test anything: an AGENT event attached
    # to t1 is one of t1's own burst events, so it is never inside one of t1's
    # gaps and the tie never arises. The first version of this test used one and
    # passed either way.
    events = [_lane_ev_on("2026-07-10T10:00:00Z", 0),
              _ev("user_message", "user", "2026-07-10T10:05:00Z", "wr", 1),
              _lane_ev_on("2026-07-10T10:10:00Z", 2)]
    tasks = _two_tasks(events, [0, 1, 2], [1])
    assert _agent_h(tasks, events)["t1"] == pytest.approx(10 / 60, abs=0.001)


def test_a_running_tool_call_bridges_a_gap_a_quiet_stretch_may_not(monkeypatch):
    # The whole point of the split. Same two events, same 20-minute gap, and the
    # only difference is whether a tool call was running across it. With the
    # tolerances set apart, one bridges and the other does not.
    monkeypatch.setattr(effort, "AGENT_QUIET_STITCH_SEC", 5 * 60)
    pending = [_lane_ev_on("2026-07-10T10:00:00Z", 0, kind="tool_call"),
               _lane_ev_on("2026-07-10T10:20:00Z", 1, kind="tool_result")]
    quiet = [_lane_ev_on("2026-07-10T10:00:00Z", 0),
             _lane_ev_on("2026-07-10T10:20:00Z", 1)]
    task = [Task(task_id="t1", project="wr", title="mine", event_ids=[0, 1])]
    assert _agent_h(task, pending)["t1"] == pytest.approx(1 / 3, abs=0.001)
    assert _agent_h(task, quiet)["t1"] == pytest.approx(0.0, abs=0.001)


def test_a_scheduled_wake_does_not_hold_a_burst_open(monkeypatch):
    # The shape this was written for: a loop wakes every 20 minutes, says
    # "Waiting.", and sleeps. It emits, so the lane cut cannot see it and nothing
    # that looks for silence can either — one measured task was 2.81h of agent
    # time made entirely of these. Only the quiet tolerance reaches it.
    monkeypatch.setattr(effort, "AGENT_QUIET_STITCH_SEC", 5 * 60)
    events = [_lane_ev_on(f"2026-07-10T{10 + i // 3:02}:{(i * 20) % 60:02}:00Z", i)
              for i in range(6)]
    task = [Task(task_id="t1", project="wr", title="mine", event_ids=list(range(6)))]
    assert _agent_h(task, events)["t1"] == pytest.approx(0.0, abs=0.001)


def test_bryans_fifteen_minutes_is_what_actually_runs():
    # A constant nobody reaches is the defect this repo has shipped before, and
    # a ruling recorded only in a commit message is the same thing in prose. He
    # chose 15 minutes on 2026-09-15 from the swept table; assert the value AND
    # that a 20-minute quiet gap — which bridged before the ruling — now cuts.
    assert effort.AGENT_QUIET_STITCH_SEC == 15 * 60
    events = [_lane_ev_on("2026-07-10T10:00:00Z", 0),
              _lane_ev_on("2026-07-10T10:20:00Z", 1)]
    task = [Task(task_id="t1", project="wr", title="mine", event_ids=[0, 1])]
    assert _agent_h(task, events)["t1"] == pytest.approx(0.0, abs=0.001)


def test_cutting_a_capped_burst_cannot_hand_out_a_second_allowance():
    # The hazard the lane cut already documents, reached by the other cut: the
    # 4h cap is PER BURST, so a cut that splits one capped burst into three
    # hands out three allowances and a tolerance that can only shorten work
    # makes a task longer. Measured at 15 min before this was fixed: 12 fan-out
    # tasks rose by 24.7h across the weeks of 2026-08-17 and 09-07, one of them
    # sitting exactly on the cap (4.00h -> 7.31h).
    #
    # Six hours of events five minutes apart, with a 20-minute quiet gap at the
    # 2h and 4h marks. Stitched at the tool-call tolerance the whole thing is
    # one burst, capped to 4h. The quiet tolerance cuts it into three bursts of
    # 2h, 1h40 and 1h40 — 5h20 if each gets its own allowance.
    marks = (list(range(0, 121, 5)) + list(range(140, 241, 5))
             + list(range(260, 361, 5)))
    base = datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc)
    events = [_lane_ev_on((base + timedelta(minutes=m)).isoformat().replace("+00:00", "Z"), i)
              for i, m in enumerate(marks)]
    task = [Task(task_id="t1", project="wr", title="mine",
                 event_ids=list(range(len(events))))]
    hours = _agent_h(task, events)["t1"]

    # The envelope is the pre-cut shape, so the answer is the first two bursts
    # whole and the third clipped where the 4h allowance ends: 2h + 1h40 + 0.
    assert hours == pytest.approx(220 / 60, abs=0.001)
    assert hours < 4.0, "a cut may never exceed the cap it was capped by"


def test_only_a_call_followed_by_its_result_counts_as_pending():
    # Kept narrow on purpose. A result followed by the next call is the model
    # deciding what to do next, and a call followed by a message is a call whose
    # result never arrived — neither is a tool running, and reading either as
    # evidence would hand the generous tolerance back to every gap.
    call = _lane_ev_on("2026-07-10T10:00:00Z", 0, kind="tool_call")
    result = _lane_ev_on("2026-07-10T10:00:05Z", 1, kind="tool_result")
    msg = _lane_ev_on("2026-07-10T10:00:10Z", 2)
    assert effort._is_pending_tool_call(call, result)
    assert not effort._is_pending_tool_call(result, call)
    assert not effort._is_pending_tool_call(call, msg)


def test_agent_labor_uses_the_quiet_tolerance_too(monkeypatch):
    # Three agent-duration paths read the stitch and the per-lane one is the
    # easiest to leave behind; a fix that lands on agent hours alone leaves the
    # fan-out number reporting the wakes it just removed from the other.
    monkeypatch.setattr(effort, "AGENT_QUIET_STITCH_SEC", 5 * 60)
    events = [_lane_ev_on("2026-07-10T10:00:00Z", 0),
              _lane_ev_on("2026-07-10T10:20:00Z", 1)]
    task = [Task(task_id="t1", project="wr", title="mine", event_ids=[0, 1])]
    assert agent_lane_hours_by_task(task, events)["t1"] == pytest.approx(0.0, abs=0.001)


def test_an_instant_is_an_instant_whoever_stamped_it():
    """A board action, and the transcript twin of the same action.

    `validate.NOT_INSTANT` has always been actor-blind, and segmentation needs
    the same question asked of a whole task, so `is_instant` asks nothing about
    the actor while `is_agent_work` adds the actor test back on top.
    """
    from weekly_review.effort import is_instant, is_agent_work

    def ev(kind, actor, **md):
        return Event(ts="2026-09-11T22:00:00Z", actor=actor, project="p", session_id="s",
                     source_system="claude_transcript", source_machine="mm", kind=kind, seq=0,
                     content_snippet="x", metadata=md)

    assert is_instant(ev("board_action", "agent"))
    assert is_instant(ev("board_action", "user")), "whose stamp it is does not change what it is"
    assert is_instant(ev("tool_call", "agent", card="t-AAA"))
    assert is_instant(ev("tool_result", "agent", card="t-AAA"))
    assert not is_instant(ev("tool_call", "agent")), "a call naming no card is work"
    assert not is_instant(ev("agent_message", "agent", card="t-AAA")), "saying so is not a stamp"
    assert not is_instant(ev("user_message", "user"))

    # is_agent_work is is_instant plus the actor, and only it asks about actor.
    assert is_agent_work(ev("tool_call", "agent"))
    assert not is_agent_work(ev("tool_call", "user")), "his keystrokes are not agent hours"
    assert not is_agent_work(ev("board_action", "agent"))


def _decl(text, actor="agent", kind="agent_message"):
    from weekly_review.events import Event
    return Event(ts="2026-08-21T18:49:00Z", actor=actor, project="p", session_id="s",
                 source_system="claude_transcript", source_machine="mm",
                 kind=kind, seq=1, content_snippet=text)


def test_an_agent_declining_a_wake_up_is_a_stamped_moment_not_work():
    """An agent woken by board flap it does not own, answering "(Holding.)".

    Fifteen of those across 7.3h held 1.40h of wall clock on a task estimated
    at 0.25h -- 71% of that task's span bought by an agent explaining that it
    had nothing to do. Same argument as a board action: it says the task was
    alive at that moment, so it still extends the span, and it may not create
    duration.
    """
    from weekly_review.effort import is_agent_work, is_declined, is_instant

    holding = _decl("(Board flap - holding silent as before; nothing changed.)")
    assert is_declined(holding)
    assert is_instant(holding)
    assert not is_agent_work(holding), "a decline is not time spent"


def test_the_decline_rule_is_narrow_on_purpose():
    """A false positive here erases a real answer, so both halves must hold:
    wrapped in parentheses AND short enough that no real answer fits."""
    from weekly_review.effort import is_declined

    assert not is_declined(_decl("Holding for now, nothing changed.")), \
        "no parentheses: an unwrapped short reply is an answer"
    assert not is_declined(_decl("(" + "a" * 200 + ")")), \
        "long enough to be a real answer, parentheses or not"
    assert not is_declined(_decl("(Noted.) Here is the actual answer you asked for.")), \
        "opens in parentheses but does not end there -- it says something after"
    assert not is_declined(_decl("(Holding.)", actor="user")), \
        "his own aside is not an agent declining to work"
    assert not is_declined(_decl("(Holding.)", kind="tool_call")), \
        "only a message can decline"


def test_the_scorer_and_the_audit_ask_one_question():
    """`validate.polling_held_wall` has to flag exactly what the scorer stops
    billing. Two copies of one rule drift apart and nothing says so."""
    import inspect
    from weekly_review import validate
    from weekly_review.effort import is_declined_text

    assert is_declined_text("(Flap noted; no change. Holding.)")
    assert not is_declined_text(None)
    assert not is_declined_text("")

    src = inspect.getsource(validate.polling_held_wall)
    assert "is_declined_text(" in src, "the audit restated the rule instead of importing it"
