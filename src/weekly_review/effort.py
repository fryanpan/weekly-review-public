"""Active hours, cognitive wall-clock, context switches, resumptions.

All computations are pure functions over Event lists. No hidden adjustments
to primary numbers — taxes and penalties stay out per spec §7.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta
from typing import Optional

from .events import Event
from .segmentation import Task


READ_WPM = 300
TYPE_WPM = 40
PER_MESSAGE_CAP_SEC = 20 * 60
COGNITIVE_GAP_SEC = 15 * 60
RESUMPTION_GAP_SEC = 24 * 60 * 60

# Agent-burst stitching: consecutive agent events (including the tool_call →
# tool_result gap during a long tool wait) are part of the same burst if they're
# within this many seconds. Set generously so long tool calls (slow builds,
# subagent dispatches, web fetches) DON'T split into zero-time fragments.
AGENT_BURST_STITCH_SEC = 30 * 60
# The same tolerance for a gap that is NOT a tool call running. The comment
# above is the whole justification for 30 minutes and it is an argument about
# tool calls only: a gap that opens at a tool_call and closes at its tool_result
# is evidence the worker was busy, and nothing else on this lane is. A quiet gap
# is the model emitting (seconds) or the session waiting to be woken (anything),
# and bridging it says the agent was working without evidence that it was.
#
# Measured over the three era weeks: a pending tool call almost never runs long
# — of 144,795 such gaps, 11 exceed 15 minutes. Quiet gaps have no knee at all,
# decaying smoothly from seconds to hours, so no value can be read off the data
# and this constant is a modelling choice like the presence tolerance. Set equal
# to the tolerance above, it reproduces every published figure to the digit.
# Sweeping it alone (agent hours) on 2026-04-27 / 08-17 / 09-07:
#   15min  −19.6% / −10.0% /  −8.9%
#   10min  −30.4% / −14.7% / −12.3%
#    5min  −43.0% / −20.3% / −16.3%
# It is not a level knob — April is sparse and September is dense, so lowering
# it tilts the Apr–Sep trend the OPPOSITE way from the lane cut below.
#
# **15 minutes — the user's ruling, 2026-09-15**, given that table: the gentlest
# setting that still removes the idle polling loops he flagged. It is a choice
# inside a range, not a measurement, which is why the run prints it.
#
# The table he ruled on was **wrong on the two dense weeks** and read −5.8% /
# −3.6% there. It was swept before the cap envelope in `_bursts` existed, so
# every row below 30 minutes was net of bursts splitting off the 4h cap and
# taking a second allowance — an error that cancels part of the reduction it is
# measuring and grows with fan-out, which is why sparse April is unmoved and
# September nearly tripled. The ordering of the options is unchanged and 15
# minutes is still the gentlest of them, so the ruling stands on the corrected
# figures; it is recorded here because a ruling read off a bad table is worth
# knowing about even when the answer survives.
AGENT_QUIET_STITCH_SEC = 15 * 60
# Hard cap on any single agent interval — guards against runaway tool runs
# (overnight builds, hung subprocesses) that would otherwise inflate the
# "agent ran while I was away" measure unbounded.
AGENT_INTERVAL_HARD_CAP_SEC = 4 * 60 * 60
# the user's hands-on interval is the modeled reading+typing window leading up to
# (and just past) each user_message timestamp.
HANDS_ON_GRACE_SEC = 30
# Kinds that record that something HAPPENED at an instant, not that work was
# being done across an interval. A board action (task.claimed, decision.answered
# and friends) is a milestone stamped by an agent whose actual work is already
# in the transcript stream, so charging it agent-busy time bills one worker
# twice from two instruments. Instants still attach to tasks, still carry the
# taskId the doc stream lacks, and still extend a task's wall-clock span; they
# just never create duration.
INSTANT_AGENT_KINDS = frozenset({"board_action"})
# An agent woken by board traffic it does not own and replying "(Holding.)" has
# declined to work, and a decline is a stamped moment. Deliberately narrow --
# wrapped in parentheses AND this short -- because a false positive here erases
# a real answer. `validate.polling_held_wall` matches on the same predicate, and
# imports it from here so the two cannot drift.
POLL_NOOP_MAX_CHARS = 120


def is_declined(ev: Event) -> bool:
    """True for an agent message that says, in parentheses, that it is not working.

    The shape is specific: an agent woken again and again by board flap answers
    "(Board flap - holding silent as before; state's current, nothing changed.)"
    Fifteen of those across 7.3h held 1.40h of wall clock on a task estimated at
    0.25h -- 71% of the task's span bought by an agent explaining it had nothing
    to do.
    """
    return (ev.actor == "agent" and ev.kind == "agent_message"
            and is_declined_text(ev.content_snippet))


def is_declined_text(snip: str | None) -> bool:
    """The text half of `is_declined`, for callers holding a row, not an Event.

    `validate.polling_held_wall` reads content_snippet straight out of SQL and
    must flag exactly what the scorer stops billing. Two copies of one rule
    drift, and nothing would say so.
    """
    s = (snip or "").strip()
    return len(s) <= POLL_NOOP_MAX_CHARS and s.startswith("(") and s.endswith(")")


def is_instant(ev: Event) -> bool:
    """True for a stamped moment: a record that something happened, not work.

    A transcript tool call that acts on a board card (moving it, posting on it)
    is the same board action seen from the agent's side, so it is an instant
    too. Once those calls follow their own card, a lead's status posts 20
    minutes apart stitched open bursts on card tasks it was not working:
    +71h (18%) of agent hours on one week. Its result is the same action
    coming back, so it is an instant as well.

    **A declined wake-up is one too (2026-09-15).** Same argument, pointed at
    the opposite case: a board action is a moment of work already counted
    elsewhere, and a parenthetical no-op is a moment of no work at all. Both
    extend a task's wall-clock span -- the task was alive then -- and neither
    may create duration. Measured over the three era weeks: 7 tasks hold such a
    reply, 2 change wall clock, and they are exactly the two the polling audit
    flags. Week-wide it removes 0.99h of 247h and 0.42h of 330h; on the two
    tasks themselves it removes 71% and 44% of their span. Zero tasks lose
    their last agent-work event, so no estimate is repriced by this.

    Whose stamp it is does not change what it is, so this asks nothing about
    the actor: `validate.NOT_INSTANT` has always been actor-blind, and
    segmentation needs the same question asked of a whole task."""
    if ev.kind in INSTANT_AGENT_KINDS:
        return True
    if is_declined(ev):
        return True
    md = ev.metadata if isinstance(ev.metadata, dict) else {}
    return bool(ev.kind in ("tool_call", "tool_result") and md.get("card"))


def is_agent_work(ev: Event) -> bool:
    """True for agent events that represent time spent, not a stamped moment."""
    return ev.actor == "agent" and not is_instant(ev)


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def active_time_seconds_for_user_message(user_msg: Event, prior_agent: Optional[Event]) -> float:
    if user_msg.kind != "user_message":
        return 0.0
    # Some sources pre-bake the active time (e.g. live-feedback review comments:
    # 2-min review allowance + typing, with no prior-agent-message to read).
    # Honor it directly, still subject to the per-message cap.
    fixed = user_msg.metadata.get("fixed_active_sec")
    if fixed is not None:
        return min(float(fixed), PER_MESSAGE_CAP_SEC)
    user_words = int(user_msg.metadata.get("words", 0))
    agent_words = int(prior_agent.metadata.get("words", 0)) if prior_agent else 0
    reading_sec = agent_words / READ_WPM * 60
    typing_sec = user_words / TYPE_WPM * 60
    total = reading_sec + typing_sec
    return min(total, PER_MESSAGE_CAP_SEC)


def _make_prior_agent(events: list[Event]):
    """The agent message a user message was replying to, for reading time.

    Walks backwards through the time-ordered stream inside the same session,
    stopping at a session boundary or at the previous user message.
    """
    order = sorted(range(len(events)), key=lambda i: events[i].ts)
    order_position = {idx: pos for pos, idx in enumerate(order)}

    def prior_agent(event_idx: int) -> Event | None:
        pos = order_position[event_idx]
        ev = events[event_idx]
        for j in range(pos - 1, -1, -1):
            prior = events[order[j]]
            if prior.session_id != ev.session_id:
                break
            if prior.kind == "agent_message":
                return prior
            if prior.kind == "user_message":
                break
        return None

    return prior_agent


def active_hours_by_task(tasks: list[Task], events: list[Event]) -> dict[str, float]:
    """Per-task hands-on hours = UNION of the active-time intervals implied by
    each user message (chat) and review comment (live-feedback), in hours.

    Unioning (not summing) the [msg_ts - active_time, msg_ts] windows means
    overlapping segments — e.g. a live-feedback review comment whose 2-min
    window overlaps a chat message's reading+typing window for the same work —
    are counted once, not twice (the user's anti-double-count requirement,
    2026-06-13). Because each window is also a the user interval in the wall-clock
    union, this also guarantees labor >= hands_on (the union invariant)."""
    prior_agent = _make_prior_agent(events)

    out: dict[str, float] = {}
    for task in tasks:
        intervals = _bryan_intervals_for_task(task, events, prior_agent)
        out[task.task_id] = _union_seconds(intervals) / 3600
    return out


# A pause shorter than this between two of the user's own events reads as "still
# working" rather than "stopped and came back". Calibrated 2026-08-17 against
# the user's own account of the Aug 10-16 week: the per-message model reported
# 10.4h, which he rejected as far too low. Sweeping the tolerance gave 9.9h at
# 5min, 18.3h at 10min, 25.3h at 15min and 37.4h at 30min — and 30min exceeds
# his stated ~30h capacity for the week, which rules out the loose end. 10min is
# the conservative choice inside the surviving range.
#
# CONFIRMED the same day, and by evidence that was never fitted to: the user had
# PLANNED 18h for that week before any of this was measured, and said the result
# read "pretty close". 10min gives 18.8h; 15min would have given 25.7h, which his
# own plan contradicts. An independent prior estimate is the strongest calibration
# signal available here — prefer it over any capacity-ceiling argument.
PRESENCE_STITCH_SEC = 10 * 60


def presence_hours_by_task(
    tasks: list[Task], events: list[Event], stitch_sec: float = PRESENCE_STITCH_SEC
) -> dict[str, float]:
    """Per-task hands-on hours measured as TIME PRESENT rather than time typing.

    `active_hours_by_task` charges only the modeled reading+typing window around
    each message, so everything between two messages is worth zero — watching a
    build, reading a diff, thinking about the answer, driving a device by hand.
    That makes it a keystroke measure wearing a working-time label, and it
    understated the user's week by roughly half.

    Here a run of his events with gaps under `stitch_sec` becomes one continuous
    interval spanning first→last. The modeled windows are unioned in as well,
    which is what keeps an ISOLATED message honest: on its own it would stitch to
    a zero-length span, so it keeps its typing+reading estimate instead. Every
    task is therefore >= its active_hours, never less.
    """
    prior_agent = _make_prior_agent(events)

    gap = timedelta(seconds=stitch_sec)
    out: dict[str, float] = {}
    for task in tasks:
        intervals = _bryan_intervals_for_task(task, events, prior_agent)
        stamps = sorted(
            _parse_ts(events[idx].ts)
            for idx in task.event_ids
            if idx < len(events) and events[idx].kind in ("user_message", "review_session")
        )
        if stamps:
            run_start = prev = stamps[0]
            for t in stamps[1:]:
                if t - prev > gap:
                    intervals.append((run_start, prev))
                    run_start = t
                prev = t
            intervals.append((run_start, prev))
        out[task.task_id] = _union_seconds(intervals) / 3600
    return out


# Two instruments, two tolerances — the user's ruling of 2026-09-08, verbatim:
# "Keep 10min for old data. We already picked 1min for new data in workspaces."
# Transcripts record messages, so the silence between two of them has to be
# modelled; the workspace records interaction directly and needs almost no
# bridging. `PRESENCE_STITCH_SEC` above is the transcript side; this is the
# workspace side.
WORKSPACE_SOURCES = frozenset({"live_feedback", "workspace_board", "meetings"})

# The contiguous-block rule, the user's words: "look instead for contiguous blocks
# on a single taskId that don't have more than a certain gap, and then consider
# that contiguous block as the time span. Don't extend it before or after."
#
# G points INWARD. It bridges every sub-G silence as work, so it can join two
# things he actually did and can never invent time outside them — which is why
# it is a better assumption than symmetric padding. It is still an assumption,
# and must not be described as "no tolerance".
#
# Why 60s, measured on 2026-08-31..09-06 (workspace side alone):
#
#   G      union    blocks   zero-length
#   0s    22.61h     2,951         1,672
#   30s   23.79h     1,831           711
#   60s   25.06h     1,726           654
#   120s  27.31h     1,625           621
#   300s  30.34h     1,561           608
#
# From 0 to 60s the total rises 2.45h while zero-length blocks fall by over a
# thousand: that stretch buys real joins. From 60s to 120s it rises another
# 2.25h while only 33 further blocks stop being zero, so the second stretch
# almost entirely lengthens blocks that already existed. G = 0 is the method's
# floor at 22.61h, within 1.5h of the 21.07h of raw measured session time.
WORKSPACE_BLOCK_GAP_SEC = 60


def is_workspace_event(ev: Event) -> bool:
    """True for events the workspace stores measure directly, not by transcript."""
    return ev.source_system in WORKSPACE_SOURCES


def workspace_attribution_key(ev: Event) -> str:
    """What a workspace event is contiguous WITH. Empty means it is invisible here.

    A board action is keyed on its `taskId`; a doc event on its task card is
    keyed on the same id (a task card IS a doc), which is what lets a decision
    answered on the board and a read of the same card join into one block. A
    doc that is not a task card can only be keyed on itself.

    The board is the last resort, and it exists because of what an empty key
    costs. An event with no key is not "blocked alone" — `workspace_blocks`
    skips it entirely, so it contributes nothing AND bridges nothing, and the
    total it produces is indistinguishable from the event never having been
    ingested. All 88 `voice.request` records are keyless: they carry no
    `taskId`, and 81 of them were dictated from the hub or the board rather than
    from a task page. Counting them as ingested while they were invisible here
    would have reported "+0.00h" as a measurement when it was a drop.

    Keying those on their board is the narrowest true statement the store
    supports — a request dictated on a board is contiguous with the other
    requests dictated on that board, and with nothing else, since every
    `task.*` and `decision.*` record has a real task id to key on. Priced
    before it shipped: **+0.08h over all history** on the person-side board
    union (0.5516h → 0.6349h).
    """
    md = ev.metadata if isinstance(ev.metadata, dict) else {}
    task_id = md.get("task_id")
    if task_id:
        return str(task_id)
    doc_id = md.get("doc_id")
    if doc_id:
        return f"doc:{doc_id}"
    workspace_id = md.get("workspace_id")
    if workspace_id:
        return f"board:{workspace_id}"
    return ""


# The workspace kinds that measure an interval rather than stamp a moment.
# Everything else here — a board action, a doc_open, an answered decision, a
# dictated voice request — is an instant, and an instant must be instant
# everywhere.
_SPANNING_WORKSPACE_KINDS = frozenset({"review_session", "meeting_segment"})


def _workspace_span(ev: Event) -> tuple[datetime, datetime]:
    """A workspace event's own extent. Everything is an instant but a read or a
    meeting.

    `review_session` is the one workspace record the plugin bounds on real
    scroll/typing interactions, so it contributes its own [start, end]. Nothing
    else is padded, which is the whole point of the rule.

    It uses `start_ts` (the session's first interaction) rather than
    `end - fixed_active_sec` (the interaction time inside it) because the rule's
    span is first event to last event, and a bounded read session is itself a
    block of interactions. The two differ by 1.9h over the week of 2026-08-31;
    the modelled-window metric keeps using the shorter one.

    `meeting_segment` is the same shape and a stronger claim: the ledger records
    when the recorder started and when it stopped, so its [start, end] is
    measured outright rather than inferred from interactions. Two consequences.
    It is exempt from `REVIEW_SESSION_CAP_SEC` — that cap exists because a read
    session can be left open in a background tab, and a recording that was never
    closed is refused by the extractor instead of being clamped. And a meeting
    with no turns at all still spans: silence on a recording is somebody
    listening, and `fixed_active_sec` is present for it, so the fallback below
    keeps the span even if a future writer drops `start_ts`.
    """
    end = _parse_ts(ev.ts)
    if ev.kind in _SPANNING_WORKSPACE_KINDS:
        md = ev.metadata if isinstance(ev.metadata, dict) else {}
        cap = float("inf") if ev.kind == "meeting_segment" else REVIEW_SESSION_CAP_SEC
        raw_start = md.get("start_ts")
        if raw_start:
            try:
                start = _parse_ts(str(raw_start))
            except ValueError:
                start = end
            if start > end:
                start, end = end, start
            if (end - start).total_seconds() <= cap:
                return (start, end)
        sec = min(float(md.get("fixed_active_sec") or 0.0), cap)
        if sec > 0:
            return (end - timedelta(seconds=sec), end)
    return (end, end)


def workspace_blocks(
    events: list[Event], gap_sec: float = WORKSPACE_BLOCK_GAP_SEC
) -> list[tuple[datetime, datetime, list[int]]]:
    """The user's contiguous blocks over the person's workspace events, with members.

    Walks ALL of his workspace events in time order and keeps one open block.
    The block stays open only while the next event carries the SAME attribution
    key and starts no more than `gap_sec` after the block's current end; any key
    switch cuts it, and so does any longer silence. A block's span is its first
    event to its last, with nothing added at either end.

    Cutting on a key switch — rather than grouping by key first and bridging
    within each group — is the conservative reading, and it is deliberate. Per-
    key grouping would bridge a gap during which he is on record doing something
    else, which is exactly the padding the rule exists to remove.

    The partition is global on purpose. Blocking inside one weekly-review task's
    own events would not see the events that interrupt it, so a task could
    bridge a gap the whole-week view knows he spent elsewhere — a per-task
    figure that cannot be reconciled with the week's. Each block carries the
    event indices that built it, and a task claims the blocks it has an event
    in; a block shared by two tasks is counted in both, which is the same
    per-task overcount presence already has and the week-wide union still bounds.

    Only `actor == "user"` events count: this is his hands-on time, and an
    agent's board action is already billed to the agent (see INSTANT_AGENT_KINDS).
    """
    spans: list[tuple[datetime, datetime, str, int]] = []
    for idx, ev in enumerate(events):
        if ev.actor != "user" or not is_workspace_event(ev):
            continue
        key = workspace_attribution_key(ev)
        if not key:
            continue
        start, end = _workspace_span(ev)
        spans.append((start, end, key, idx))
    spans.sort(key=lambda r: (r[0], r[1]))

    blocks: list[tuple[datetime, datetime, list[int]]] = []
    cur_key = ""
    cur_start = cur_end = None
    members: list[int] = []
    for start, end, key, idx in spans:
        if (cur_end is not None and key == cur_key
                and (start - cur_end).total_seconds() <= gap_sec):
            cur_end = max(cur_end, end)
            members.append(idx)
            continue
        if cur_end is not None:
            blocks.append((cur_start, cur_end, members))
        cur_key, cur_start, cur_end, members = key, start, end, [idx]
    if cur_end is not None:
        blocks.append((cur_start, cur_end, members))
    return blocks


def workspace_block_intervals(
    events: list[Event],
    event_ids: list[int] | None = None,
    gap_sec: float = WORKSPACE_BLOCK_GAP_SEC,
) -> list[tuple[datetime, datetime]]:
    """The blocks (from the whole-week partition) that `event_ids` takes part in.

    `event_ids=None` is the week itself: every block.
    """
    blocks = workspace_blocks(events, gap_sec)
    if event_ids is None:
        return [(s, e) for s, e, _ in blocks]
    wanted = set(event_ids)
    return [(s, e) for s, e, members in blocks if wanted.intersection(members)]


def _is_workspace_comment(ev: Event) -> bool:
    return ev.actor == "user" and ev.kind == "user_message" and is_workspace_event(ev)


COMMENT_LEAD_IN_SEC = 60
# A dictated request is spoken, not typed: conversational speech runs about 150
# words a minute. It follows the comment rule otherwise, so a request with
# nothing on the board leading to it gets the same one minute of working out
# what to ask. A modelling choice, like TYPE_WPM, and printed as one.
SPEAK_WPM = 150
# His other written board ask: a task he files is typed like a comment, title
# and body, and follows the same rule.
_BOARD_ASK_WPM = {"voice.request": SPEAK_WPM, "task.created": TYPE_WPM}


def _board_ask_wpm(ev: Event) -> float | None:
    md = ev.metadata if isinstance(ev.metadata, dict) else {}
    return _BOARD_ASK_WPM.get(str(md.get("board_event") or ""))


def md_activity(ev: Event) -> str:
    md = ev.metadata if isinstance(ev.metadata, dict) else {}
    return str(md.get("activity_type") or "")


def workspace_comment_windows(events: list[Event]) -> dict[int, tuple[datetime, datetime]]:
    """Each workspace comment's typing window, plus a lead-in when nothing led to it.

    The block rule scored a lone comment as an instant, so 329 words typed across
    nine comments came to 0.04h (f497b05f64). the user's rule, 2026-09-14: "Estimate
    how long it took to comment based on comment length. And if there's no
    activity around to indicate what led to the comment, please assume 1 minute
    before the comment of reviewing something (but coalesce all of these time
    spans)."

    So a comment spans its typing time (words at TYPE_WPM). If his own activity
    on the same doc or task — a read session, a doc open, another comment —
    reaches within COMMENT_LEAD_IN_SEC of that typing start, the window runs back
    to it and the union with that activity coalesces them. Otherwise the reading
    is unobserved and the window gets the one-minute lead-in. Keyed by event
    index so a task picks its own and the week takes them all.
    """
    by_key: dict[str, list[tuple[datetime, datetime, int]]] = {}
    comments: list[tuple[int, str]] = []
    for idx, ev in enumerate(events):
        if ev.actor != "user" or not is_workspace_event(ev):
            continue
        key = workspace_attribution_key(ev)
        if ev.kind == "user_message" or _board_ask_wpm(ev):
            comments.append((idx, key))
        elif md_activity(ev) in ("comment", "reply"):
            # The activity stream's copy of a comment, stamped the same moment:
            # the comment cannot be what led to itself.
            continue
        if key:
            start, end = _workspace_span(ev)
            by_key.setdefault(key, []).append((start, end, idx))
    for spans in by_key.values():
        spans.sort()
    lookback = timedelta(seconds=COMMENT_LEAD_IN_SEC + REVIEW_SESSION_CAP_SEC)
    out: dict[int, tuple[datetime, datetime]] = {}
    for idx, key in comments:
        ev = events[idx]
        md = ev.metadata if isinstance(ev.metadata, dict) else {}
        words = md.get("words")
        if words is None:
            words = len((ev.content_snippet or "").split())
        wpm = _board_ask_wpm(ev) or TYPE_WPM
        typing = min(float(words) / wpm * 60, PER_MESSAGE_CAP_SEC)
        end = _parse_ts(ev.ts)
        start = end - timedelta(seconds=typing)
        horizon = start - timedelta(seconds=COMMENT_LEAD_IN_SEC)
        spans = by_key.get(key, [])
        pos = bisect_right(spans, (end, datetime.max.replace(tzinfo=end.tzinfo), len(events)))
        led_to = None
        for s, e, other in reversed(spans[:pos]):
            if s < start - lookback:
                break
            if other != idx and e >= horizon:
                led_to = e if led_to is None else max(led_to, e)
        out[idx] = (min(start, led_to), end) if led_to is not None else (horizon, end)
    return out


REVIEW_READ_CAP_SEC = 120
REVIEW_ANSWER_EVENTS = frozenset({"decision.answered", "decision.info_requested"})


def review_answer_windows(events: list[Event]) -> dict[int, tuple[datetime, datetime]]:
    """Each review answer's reading time, cut where he was last somewhere else.

    An answer is a board instant, so reading the item before answering scored
    zero. the user's rule, 2026-09-14, for history until the board records when an
    item is first shown: "use a 2 minute cap on reading before the answer (but
    if I had any event in less than 2 minutes before on another review item or
    page, then it gets cut off)".

    So the window is the two minutes before the answer, starting no earlier
    than the end of his latest workspace event on a different key (another task
    or doc) or on a different review item. His activity on the same item or the
    same task page does not cut it; the union coalesces with it instead. Keyed
    by event index, like `workspace_comment_windows`.
    """
    mine: list[tuple[datetime, datetime, str, str, int]] = []
    answers: list[int] = []
    for idx, ev in enumerate(events):
        if ev.actor != "user" or not is_workspace_event(ev):
            continue
        md = ev.metadata if isinstance(ev.metadata, dict) else {}
        key = workspace_attribution_key(ev)
        item = str(md.get("review_item_id") or "")
        start, end = _workspace_span(ev)
        mine.append((start, end, key, item, idx))
        if md.get("board_event") in REVIEW_ANSWER_EVENTS:
            answers.append(idx)
    mine.sort()
    starts = [m[0] for m in mine]
    lookback = timedelta(seconds=REVIEW_READ_CAP_SEC + REVIEW_SESSION_CAP_SEC)
    out: dict[int, tuple[datetime, datetime]] = {}
    for idx in answers:
        ev = events[idx]
        md = ev.metadata if isinstance(ev.metadata, dict) else {}
        key = workspace_attribution_key(ev)
        item = str(md.get("review_item_id") or "")
        end = _parse_ts(ev.ts)
        begin = end - timedelta(seconds=REVIEW_READ_CAP_SEC)
        for s, e, k, i, other in reversed(mine[:bisect_right(starts, end)]):
            if s < end - lookback:
                break
            if other == idx or s >= end:
                continue
            elsewhere = k != key or (item and i and i != item)
            if elsewhere:
                begin = max(begin, min(e, end))
        out[idx] = (begin, end)
    return out


def workspace_windows(events: list[Event]) -> dict[int, tuple[datetime, datetime]]:
    """Comment typing windows and review-answer reading windows, by event index."""
    return {**workspace_comment_windows(events), **review_answer_windows(events)}


def hands_on_intervals_by_task(
    tasks: list[Task],
    events: list[Event],
    stitch_sec: float = PRESENCE_STITCH_SEC,
    gap_sec: float = WORKSPACE_BLOCK_GAP_SEC,
    comment_windows: bool = True,
) -> dict[str, tuple[list[tuple[datetime, datetime]], list[tuple[datetime, datetime]]]]:
    """Per task, the two instruments' intervals kept APART: (transcript, workspace).

    Returned separately rather than pre-unioned so a caller can report each side
    on its own and show that the union is no larger than the sum. Union them for
    the per-task figure; see `hands_on_hours_by_task`.

    The transcript side is the presence model unchanged — modelled reading and
    typing windows, stitched at `stitch_sec` — restricted to events that did not
    come from a workspace store. The workspace side is the contiguous-block rule,
    plus (with `comment_windows`) each comment's typing window and lead-in (see
    `workspace_comment_windows`), so a comment is no longer an instant, and each
    review answer's reading time (see `review_answer_windows`). Billing
    passes False: moving a billed figure is a separate call.
    """
    windows = workspace_windows(events) if comment_windows else {}
    prior_agent = _make_prior_agent(events)
    gap = timedelta(seconds=stitch_sec)
    # One global partition, reused by every task: the rule is defined on his
    # whole stream, and recomputing it per task would also be quadratic.
    blocks = workspace_blocks(events, gap_sec)
    blocks_of: dict[int, list[int]] = {}
    for b_idx, (_s, _e, members) in enumerate(blocks):
        for idx in members:
            blocks_of.setdefault(idx, []).append(b_idx)
    out: dict[str, tuple[list, list]] = {}
    for task in tasks:
        transcript = _bryan_intervals_for_task(
            task, events, prior_agent, include=lambda ev: not is_workspace_event(ev)
        )
        stamps = sorted(
            _parse_ts(events[idx].ts)
            for idx in task.event_ids
            if idx < len(events) and events[idx].kind == "user_message"
            and not is_workspace_event(events[idx])
        )
        if stamps:
            run_start = prev = stamps[0]
            for ts in stamps[1:]:
                if ts - prev > gap:
                    transcript.append((run_start, prev))
                    run_start = ts
                prev = ts
            transcript.append((run_start, prev))
        claimed = {b for idx in task.event_ids for b in blocks_of.get(idx, ())}
        workspace = [(blocks[b][0], blocks[b][1]) for b in sorted(claimed)]
        workspace += [windows[idx] for idx in task.event_ids if idx in windows]
        out[task.task_id] = (transcript, workspace)
    return out


def hands_on_hours_by_task(
    tasks: list[Task],
    events: list[Event],
    stitch_sec: float = PRESENCE_STITCH_SEC,
    gap_sec: float = WORKSPACE_BLOCK_GAP_SEC,
    comment_windows: bool = True,
) -> dict[str, float]:
    """Per-task hands-on hours: the two instruments UNIONED, never added.

    The transcript side keeps its 10-minute presence model unchanged. The
    workspace side is the contiguous-block rule at 60s. They are unioned
    because they describe the same hours from different angles — a moment spent
    driving an agent is frequently recorded on both — so adding them would
    double-count exactly the time he was most engaged.

    The union is therefore never larger than the sum, and a run prints both.
    """
    return {
        task_id: _union_seconds(transcript + workspace) / 3600
        for task_id, (transcript, workspace) in hands_on_intervals_by_task(
            tasks, events, stitch_sec, gap_sec, comment_windows
        ).items()
    }


def agent_active_hours_by_task(tasks: list[Task], events: list[Event]) -> dict[str, float]:
    """Sum wall-clock span of contiguous agent activity within each task.

    Excludes gaps > 5 min between agent events in the same session.
    """
    out: dict[str, float] = {}
    for task in tasks:
        attached = sorted(
            (events[i] for i in task.event_ids if i < len(events)),
            key=lambda e: e.ts,
        )
        total_sec = 0.0
        span_start: datetime | None = None
        last_ts: datetime | None = None
        for ev in attached:
            if not is_agent_work(ev):
                if span_start and last_ts:
                    total_sec += (last_ts - span_start).total_seconds()
                span_start = None
                last_ts = None
                continue
            ts = _parse_ts(ev.ts)
            if span_start is None:
                span_start = ts
                last_ts = ts
                continue
            if last_ts and (ts - last_ts).total_seconds() > 5 * 60:
                total_sec += (last_ts - span_start).total_seconds()
                span_start = ts
            last_ts = ts
        if span_start and last_ts:
            total_sec += (last_ts - span_start).total_seconds()
        out[task.task_id] = total_sec / 3600
    return out


def cognitive_wall_clock_seconds(events: list[Event]) -> float:
    """Sum durations of active spans where consecutive events are within 15 min."""
    if not events:
        return 0.0
    # Sort by parsed datetime, not raw string — different sources use different tz offsets.
    ordered = sorted(events, key=lambda e: _parse_ts(e.ts))
    total = 0.0
    span_start = _parse_ts(ordered[0].ts)
    last = span_start
    for ev in ordered[1:]:
        ts = _parse_ts(ev.ts)
        if (ts - last).total_seconds() > COGNITIVE_GAP_SEC:
            total += (last - span_start).total_seconds()
            span_start = ts
        last = ts
    total += (last - span_start).total_seconds()
    return total


def context_switch_count(events: list[Event]) -> int:
    """Count user-message transitions across projects within the same UTC date."""
    user_msgs = sorted(
        (e for e in events if e.kind == "user_message"),
        key=lambda e: _parse_ts(e.ts),
    )
    switches = 0
    for prev, curr in zip(user_msgs, user_msgs[1:]):
        if prev.project == curr.project:
            continue
        if _parse_ts(prev.ts).date() == _parse_ts(curr.ts).date():
            switches += 1
    return switches


def _union_seconds(intervals: list[tuple[datetime, datetime]]) -> float:
    """Sweep-line union of (start, end) intervals → total covered seconds.

    Used for "wall-clock active" = time when SOMETHING was happening (the user
    or agent), with overlap collapsed so we don't double-count.
    """
    if not intervals:
        return 0.0
    sorted_iv = sorted((a, b) for a, b in intervals if b > a)
    if not sorted_iv:
        return 0.0
    total = 0.0
    cur_start, cur_end = sorted_iv[0]
    for s, e in sorted_iv[1:]:
        if s <= cur_end:
            cur_end = max(cur_end, e)
        else:
            total += (cur_end - cur_start).total_seconds()
            cur_start, cur_end = s, e
    total += (cur_end - cur_start).total_seconds()
    return total


REVIEW_SESSION_CAP_SEC = 3600  # runaway guard for a single measured session


def _bryan_intervals_for_task(
    task: Task, events: list[Event], prior_agent_fn, include=None
) -> list[tuple[datetime, datetime]]:
    """One (start, end) per user_message, modeled as the reading + typing window
    leading up to the message timestamp. End is the timestamp itself plus a
    small grace so consecutive turns don't have invisible 0-length gaps.

    kind='review_session' events (live-feedback interaction-bounded read/edit
    sessions; measured, not modeled) contribute their full measured window.
    Overlap with a comment's modeled window unions away downstream, so a
    comment posted mid-review isn't double-counted."""
    out: list[tuple[datetime, datetime]] = []
    for idx in task.event_ids:
        if idx >= len(events):
            continue
        ev = events[idx]
        if include is not None and not include(ev):
            continue
        if ev.kind == "review_session":
            sec = min(float(ev.metadata.get("fixed_active_sec", 0.0)), REVIEW_SESSION_CAP_SEC)
            if sec <= 0:
                continue
            end = _parse_ts(ev.ts)
            out.append((end - timedelta(seconds=sec), end))
            continue
        if ev.kind != "user_message":
            continue
        active_sec = active_time_seconds_for_user_message(ev, prior_agent_fn(idx))
        end = _parse_ts(ev.ts)
        start = end - timedelta(seconds=active_sec)
        end = end + timedelta(seconds=HANDS_ON_GRACE_SEC)
        out.append((start, end))
    return out


def _agent_intervals_for_task(
    task: Task, events: list[Event], lane_index=None
) -> list[tuple[datetime, datetime]]:
    """Agent bursts: contiguous agent activity stitched with gaps ≤ 30min.

    Instant kinds (see INSTANT_AGENT_KINDS) are excluded — a board action has no
    duration and must not stitch a burst open around itself.

    Includes the tool_call → tool_result latency window — long tool runs are
    "agent busy" time even when no tokens are emitting. Hard-capped per burst
    at AGENT_INTERVAL_HARD_CAP_SEC to bound runaway tool runs (overnight builds).
    User messages do NOT close the agent burst by themselves — they only matter
    if they're followed by a long quiet period."""
    attached = sorted(
        (events[i] for i in task.event_ids if i < len(events)),
        key=lambda e: _parse_ts(e.ts),
    )
    agent_events = [ev for ev in attached if is_agent_work(ev)]
    return _bursts(agent_events, task.task_id, lane_index)


def _lane_of(ev: Event) -> str:
    return (ev.metadata or {}).get("agent_lane") or "main"


def lane_task_index(
    tasks: list[Task], events: list[Event]
) -> dict[tuple[str, str], tuple[list[datetime], list[str | None]]]:
    """Each (session, lane)'s whole stream in time order, with whose task it is.

    The key an agent burst has to check before it bridges a gap. An event
    attached to two tasks counts for the first, matching `RunIndex.owner`.
    """
    owner: dict[int, str] = {}
    for task in tasks:
        for i in task.event_ids:
            owner.setdefault(i, task.task_id)

    per: dict[tuple[str, str], list[tuple[datetime, str | None]]] = {}
    for i, ev in enumerate(events):
        try:
            ts = _parse_ts(ev.ts)
        except ValueError:
            continue
        per.setdefault((ev.session_id, _lane_of(ev)), []).append((ts, owner.get(i)))

    out: dict[tuple[str, str], tuple[list[datetime], list[str | None]]] = {}
    for key, rows in per.items():
        rows.sort(key=lambda r: r[0])
        out[key] = ([r[0] for r in rows], [r[1] for r in rows])
    return out


def _lane_served_another_task(prev: Event, curr: Event, task_id, lane_index) -> bool:
    """Did the worker spend this gap on a different task?

    Keyed on the lane of the gap's LEFT edge — the worker whose burst is being
    asked to stay open. Unattached events say nothing: an event nobody claimed
    is not evidence the lane left.
    """
    if lane_index is None or task_id is None:
        return False
    times, owners = lane_index.get((prev.session_id, _lane_of(prev)), ((), ()))
    if not times:
        return False
    lo = bisect_right(times, _parse_ts(prev.ts))
    hi = bisect_left(times, _parse_ts(curr.ts))
    return any(o is not None and o != task_id for o in owners[lo:hi])


def _is_pending_tool_call(prev: Event, curr: Event) -> bool:
    """Is this gap a tool call running?

    The one gap on the agent lane that is evidence of work rather than an
    assumption about it. Kept deliberately narrow: the tool_call must be the
    event immediately before the tool_result on the same lane, which is the
    shape the extractor emits for a call and its return.
    """
    return prev.kind == "tool_call" and curr.kind == "tool_result"


def _stitch(agent_events: list[Event], task_id, lane_index,
            quiet_tol: int | None = None) -> list[tuple[datetime, datetime]]:
    """Run-length blocks over the stream: a gap wider than the stitch cuts, and
    so does a gap the worker spent on another task.

    A gap a tool call was running gets the full tolerance; any other gap gets
    the quiet one, because the generous value was only ever argued for the
    former.

    `quiet_tol` overrides that quiet value. Its only caller is the cap envelope
    in `_bursts`, which needs the shape the stream had before either cut existed.
    """
    quiet = AGENT_QUIET_STITCH_SEC if quiet_tol is None else quiet_tol
    out: list[tuple[datetime, datetime]] = []
    start = last = _parse_ts(agent_events[0].ts)
    for prev, ev in zip(agent_events, agent_events[1:]):
        ts = _parse_ts(ev.ts)
        tol = AGENT_BURST_STITCH_SEC if _is_pending_tool_call(prev, ev) else quiet
        if (ts - last).total_seconds() > tol \
                or _lane_served_another_task(prev, ev, task_id, lane_index):
            out.append((start, last))
            start = ts
        last = ts
    out.append((start, last))
    return out


def _intersect(a: list[tuple[datetime, datetime]],
               b: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """Overlap of two sorted lists of disjoint intervals."""
    out, i, j = [], 0, 0
    while i < len(a) and j < len(b):
        s, e = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if s <= e:   # a zero-length burst still says the agent was here
            out.append((s, e))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def _bursts(agent_events: list[Event], task_id: str | None = None,
            lane_index=None) -> list[tuple[datetime, datetime]]:
    """Gap-stitch a time-sorted agent event stream into capped busy intervals.

    A gap is bridged only if the worker did not spend it elsewhere. Without
    `lane_index` the stitch sees only this task's own events, so a lane that
    interleaves twenty tasks bills each of them for the time it spent on the
    other nineteen — the same defect the human lane's run-length blocking
    already rejects ("any key switch cuts the block"). Measured on the stored
    events, April is untouched (one task per lane) while the week of 2026-09-07
    sheds 20.2% of its agent hours, so the error contaminates a trend rather
    than a level.

    Cutting leaves the time between the two tasks' events claimed by neither,
    which is the same conservative choice the block rule makes: a worker's
    switch is visible at its next event, not at the moment it turned away.

    **Both cuts are applied inside the capped stitch, not instead of it**, so
    neither can ever add time. The 4h cap is per burst, so splitting one burst
    into three hands out three allowances: applied the obvious way the lane cut
    ADDED 14.5h on the week of 2026-08-17 and 26.7h on 2026-09-07, one task
    going from 4.03h to 11.64h — a fix that manufactures hours out of a runaway
    guard. The quiet tolerance splits bursts the same way and so has the same
    hazard: at 15 minutes it raised 12 fan-out tasks by 24.7h, one of them
    starting exactly on the cap (4.00h -> 7.31h). The week-wide totals still
    fell, which is what hid it — a tolerance that can only shorten work made
    the busiest tasks longer, under a net reduction that looked correct.
    So the envelope is stitched at the TOOL-CALL tolerance with no lane
    cut — the shape the stream had before either cut existed — and everything
    else intersects into it. The cap goes on meaning one continuous stretch of
    presence, and each cut is a subset of the old answer by construction.
    """
    if not agent_events:
        return []

    envelope = [(s, min(e, s + timedelta(seconds=AGENT_INTERVAL_HARD_CAP_SEC)))
                for s, e in _stitch(agent_events, None, None,
                                    quiet_tol=AGENT_BURST_STITCH_SEC)]
    return _intersect(_stitch(agent_events, task_id, lane_index), envelope)


def _subtract_seconds(
    base: list[tuple[datetime, datetime]], cut: list[tuple[datetime, datetime]]
) -> float:
    """Seconds covered by `base` but NOT by `cut`."""
    if not base:
        return 0.0
    covered = _union_seconds(base)
    both = _union_seconds(base) + _union_seconds(cut) - _union_seconds(base + cut)
    return max(0.0, covered - both)


def agent_lane_hours_by_task(
    tasks: list[Task], events: list[Event]
) -> dict[str, float]:
    """Agent LANE time: per-lane busy time summed across concurrent agents.
    (Renamed 2026-09-18; "agent labor" until then.)

    Companion to the `agent_h` returned by labor_and_agent_hours_by_task,
    which merges every agent's events into one timeline and therefore measures
    ELAPSED agent time — 16 workflow agents running concurrently for 10 minutes
    read as ~10 agent-minutes there, the same as one agent working alone.

    Here each agent (the main loop plus every dispatched subagent / workflow
    agent / team member, identified by metadata["agent_lane"]) is burst-stitched
    on its own and the results are SUMMED, so that same fan-out reads as
    ~160 agent-minutes. agent_lane_h ÷ agent_h is the parallelism factor.

    Events with no lane recorded (non-transcript sources; runs predating lane
    tagging) fall back to the "main" lane, which makes this degrade to agent_h
    rather than to zero.

    Lanes are keyed by (session_id, lane), not lane alone: two Claude Code
    sessions running concurrently BOTH label their main loop "main", and keying
    on the bare stem would merge them into one lane and silently erase exactly
    the parallelism this metric exists to measure.

    A dispatching parent does NOT earn labor while it sits blocked on its
    children (the user's call, 2026-07-12): an idle orchestrator is not a worker,
    and counting it would report 16 fanned-out agents as 17. Blocked time is
    detected as "a child lane in this session was active", which covers Task,
    Workflow and team dispatches alike without depending on tool names.
    """
    index = lane_task_index(tasks, events)
    out: dict[str, float] = {}
    for task in tasks:
        attached = sorted(
            (events[i] for i in task.event_ids if i < len(events)),
            key=lambda e: _parse_ts(e.ts),
        )
        lanes: dict[tuple[str, str], list[Event]] = {}
        for ev in attached:
            if not is_agent_work(ev):
                continue
            lanes.setdefault((ev.session_id, _lane_of(ev)), []).append(ev)

        # Child lanes bill their full burst time.
        child_iv_by_session: dict[str, list[tuple[datetime, datetime]]] = {}
        total = 0.0
        for (session, lane), lane_events in lanes.items():
            if lane == "main":
                continue
            bursts = _bursts(lane_events, task.task_id, index)
            total += sum((e - s).total_seconds() for s, e in bursts)
            child_iv_by_session.setdefault(session, []).extend(bursts)

        # A main lane bills only the part of its bursts that no child of the same
        # session was covering — i.e. the time it was working, not waiting.
        for (session, lane), lane_events in lanes.items():
            if lane != "main":
                continue
            total += _subtract_seconds(
                _bursts(lane_events, task.task_id, index),
                child_iv_by_session.get(session, []),
            )

        out[task.task_id] = total / 3600
    return out


def labor_and_agent_hours_by_task(
    tasks: list[Task], events: list[Event],
    hands_on_intervals: dict[str, tuple[list, list]] | None = None,
) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    """Returns (labor_h, agent_h, bryan_h) per task.

    - labor_h: union of user + agent intervals, overlap collapsed, pauses
      removed. "Time during which something was happening on this task."
      Called wall_clock_hours until 2026-09-18; calendar time is now
      `wall_clock_hours_by_task`. With `hands_on_intervals` (from
      `hands_on_intervals_by_task`) his hands-on intervals join the union, so
      labor is never below hands-on. Without them a task whose only work was
      his reading and answering a review item had hands-on and no labor: 14
      estimated tasks on the week of 2026-09-07.
    - agent_h: sum of agent bursts (tool-latency-inclusive, hard-capped).
      "Time the agent was actively driving" (≈ what reads as 'agent ran while
      I was away' when overlapped with idle the user).
    - bryan_h: sum of the user's modeled reading+typing intervals.
      Same as active_hours_by_task; returned here for one-stop access.
    """
    prior_agent = _make_prior_agent(events)
    index = lane_task_index(tasks, events)

    labor: dict[str, float] = {}
    agent: dict[str, float] = {}
    bryan: dict[str, float] = {}
    for task in tasks:
        b_iv = _bryan_intervals_for_task(task, events, prior_agent)
        a_iv = _agent_intervals_for_task(task, events, index)
        tr_iv, ws_iv = (hands_on_intervals or {}).get(task.task_id, ([], []))
        labor[task.task_id] = _union_seconds(b_iv + a_iv + list(tr_iv) + list(ws_iv)) / 3600
        agent[task.task_id] = sum((e - s).total_seconds() for s, e in a_iv) / 3600
        bryan[task.task_id] = sum((e - s).total_seconds() for s, e in b_iv) / 3600
    return labor, agent, bryan


def first_agent_work_ts(task: Task, events: list[Event]) -> datetime | None:
    """Timestamp of the first agent work event attached to the task, or None
    when no agent ever worked on it (his messages and board actions only)."""
    first = None
    for i in task.event_ids:
        if i >= len(events) or not is_agent_work(events[i]):
            continue
        ts = _parse_ts(events[i].ts)
        if first is None or ts < first:
            first = ts
    return first


def wall_clock_hours_by_task(tasks: list[Task], events: list[Event]) -> dict[str, float]:
    """Calendar time per unit (method Step 7, 2026-09-18): first agent work on
    the unit to its last event, pauses included. Nights and weekends count.
    A unit with no agent work reads 0. Per board task the end is the done
    transition instead; see calendar_time.board_task_wall_clock."""
    out: dict[str, float] = {}
    for task in tasks:
        start = first_agent_work_ts(task, events)
        if start is None:
            out[task.task_id] = 0.0
            continue
        last = max(_parse_ts(events[i].ts) for i in task.event_ids if i < len(events))
        out[task.task_id] = max(0.0, (last - start).total_seconds()) / 3600
    return out


def resumption_count(task: Task, events: list[Event]) -> int:
    """Count gaps > 24h between consecutive events attached to this task."""
    attached = sorted(
        (events[i] for i in task.event_ids if i < len(events)),
        key=lambda e: e.ts,
    )
    count = 0
    for prev, curr in zip(attached, attached[1:]):
        gap = (_parse_ts(curr.ts) - _parse_ts(prev.ts)).total_seconds()
        if gap > RESUMPTION_GAP_SEC:
            count += 1
    return count


# --- Per-task interaction-shape metrics (added 2026-07-08, the user's ask) ---

HANDS_ON_BLOCK_GAP_SEC = 120  # user messages <2min apart = one attention block


def hands_on_blocks_by_task(tasks: list[Task], events: list[Event]) -> dict[str, int]:
    """Number of hands-on attention blocks per task.

    Consecutive user messages less than HANDS_ON_BLOCK_GAP_SEC apart count as
    one block — measures how batched the user's steering is (fewer blocks per
    task at equal message count = better batching)."""
    out: dict[str, int] = {}
    for task in tasks:
        times = sorted(
            _parse_ts(events[i].ts)
            for i in task.event_ids
            if i < len(events) and events[i].kind == "user_message"
        )
        blocks = 0
        prev = None
        for t in times:
            if prev is None or (t - prev).total_seconds() >= HANDS_ON_BLOCK_GAP_SEC:
                blocks += 1
            prev = t
        out[task.task_id] = blocks
    return out


def human_input_chars_by_task(tasks: list[Task], events: list[Event]) -> dict[str, int]:
    """Total characters the user typed per task.

    Uses the extractor's `chars` metadata (full pre-truncation length). Events
    from before the field existed fall back to words*6 (~avg English word+space)."""
    out: dict[str, int] = {}
    for task in tasks:
        total = 0
        for i in task.event_ids:
            if i >= len(events) or events[i].kind != "user_message":
                continue
            md = events[i].metadata
            chars = md.get("chars")
            total += int(chars) if chars is not None else int(md.get("words", 0)) * 6
        out[task.task_id] = total
    return out


def dominant_model_by_task(tasks: list[Task], events: list[Event]) -> dict[str, str]:
    """Model with the most agent events in the task, e.g. 'claude-opus-4-8'.

    Mixed-model tasks (main loop on one model, subagents on another) resolve
    to the busiest model; '' when no agent event carries a model tag."""
    out: dict[str, str] = {}
    for task in tasks:
        counts: dict[str, int] = {}
        for i in task.event_ids:
            if i >= len(events) or events[i].actor != "agent":
                continue
            m = events[i].metadata.get("model") or ""
            if m and m != "<synthetic>":
                counts[m] = counts.get(m, 0) + 1
        out[task.task_id] = max(counts, key=counts.get) if counts else ""
    return out
