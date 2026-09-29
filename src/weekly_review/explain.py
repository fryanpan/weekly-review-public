"""Plain-language accounts of how a task's hands-on and wall-clock figures arose.

A reviewer can only judge a number they can take apart. These re-derive both
figures with the pipeline's own interval functions, count what they are made of,
and, when hands-on is near zero, say what the record shows about how the work
started. Each account stays under 100 words so it fits a review pass.
"""
from __future__ import annotations

import os
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timedelta

from .effort import (
    AGENT_BURST_STITCH_SEC,
    AGENT_QUIET_STITCH_SEC,
    AGENT_INTERVAL_HARD_CAP_SEC,
    PRESENCE_STITCH_SEC,
    REVIEW_ANSWER_EVENTS,
    REVIEW_READ_CAP_SEC,
    WORKSPACE_BLOCK_GAP_SEC,
    _agent_intervals_for_task,
    _bryan_intervals_for_task,
    _parse_ts,
    _union_seconds,
    is_agent_work,
    is_workspace_event,
    lane_task_index,
    review_answer_windows,
)
from .events import Event
from .segmentation import Task

MAX_WORDS = 100
NEAR_ZERO_H = 3 / 60      # the multiplier's floor: under this the median skips the task
TRIGGER_WINDOW = timedelta(minutes=30)
REVIEW_ITEM_EVENTS = frozenset({"decision.answered", "decision.info_requested",
                                "decision.answer_withdrawn"})


def fmt_dur(sec: float) -> str:
    sec = max(0.0, sec)
    if sec < 60:
        return f"{round(sec)} s"
    if sec < 3600:
        return f"{round(sec / 60)} min"
    if sec < 48 * 3600:
        h, m = divmod(round(sec / 60), 60)
        return f"{h}h {m}m" if m else f"{h}h"
    return f"{sec / 86400:.1f} days"


def _short(text: str, words: int = 8) -> str:
    parts = " ".join((text or "").split()).split(" ")
    return " ".join(parts[:words]) + ("…" if len(parts) > words else "")


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _doc_type(md: dict) -> str:
    if str(md.get("doc_id", "")).startswith("task:") or (md.get("task_id") and not md.get("source_url")):
        return "task card"
    name = os.path.basename(str(md.get("source_url") or ""))
    return name.rsplit(".", 1)[-1].lower() if "." in name else "doc"


class RunIndex:
    """Lookups every account needs, built once for a run."""

    def __init__(self, events: list[Event], tasks: dict[str, Task]):
        self.events = events
        self.tasks = tasks
        self.owner: dict[int, str] = {}
        for tid, t in tasks.items():
            for i in t.event_ids:
                self.owner.setdefault(i, tid)
        # Human transcript messages per session, time-ordered, for trigger lookups.
        self.typed: dict[str, list[tuple[datetime, int]]] = defaultdict(list)
        for i, ev in enumerate(events):
            if ev.kind == "user_message" and not is_workspace_event(ev):
                self.typed[ev.session_id].append((_parse_ts(ev.ts), i))
        for msgs in self.typed.values():
            msgs.sort()
        # An agent burst may not bridge a gap its own lane spent on another
        # task, so the account has to see every task, not just this one.
        self.lane_index = lane_task_index(list(tasks.values()), events)
        # Reading time before each review answer, keyed by event index.
        self.answer_windows = review_answer_windows(events)


def hands_on_counts(task: Task, events: list[Event]) -> dict:
    """What the subject did on the task, by kind."""
    c = {"messages": 0, "comments": 0, "resolved": 0, "review_items": 0,
         "board_actions": 0, "voice": 0, "meeting_sec": 0.0}
    docs: dict[str, str] = {}
    lf_comments = lf_words = 0
    for i in task.event_ids:
        ev = events[i]
        if ev.actor != "user":
            continue
        md = ev.metadata or {}
        if ev.kind == "user_message":
            if is_workspace_event(ev):
                lf_comments += 1
                lf_words += len((ev.content_snippet or "").split())
            else:
                c["messages"] += 1
        elif ev.kind == "workspace_touch":
            act = md.get("activity_type")
            if act in ("comment", "reply"):
                c["comments"] += 1
            elif act == "resolve":
                c["resolved"] += 1
            elif act == "doc_open":
                docs.setdefault(str(md.get("doc_id")), _doc_type(md))
        elif ev.kind == "review_session":
            docs.setdefault(str(md.get("doc_id")), _doc_type(md))
        elif ev.kind == "board_action":
            be = md.get("board_event", "")
            if be in REVIEW_ITEM_EVENTS:
                c["review_items"] += 1
            elif be == "voice.request":
                c["voice"] += 1
            else:
                c["board_actions"] += 1
        elif ev.kind == "meeting_segment":
            c["meeting_sec"] += float(md.get("fixed_active_sec") or 0)
    # The comment store and the activity stream both record a comment, but the
    # stream started later than the store, so neither alone is complete. The
    # larger count is the one that has not missed any.
    c["comments"] = max(c["comments"], lf_comments)
    c["comment_words"] = lf_words
    types: dict[str, int] = defaultdict(int)
    for t in docs.values():
        types[t] += 1
    c["files"] = len(docs)
    c["file_types"] = dict(sorted(types.items(), key=lambda kv: -kv[1]))
    return c


def _counts_sentence(c: dict) -> str:
    files = _plural(c["files"], "file") + " reviewed"
    if c["file_types"]:
        files += " (" + ", ".join(f"{n} {t}" for t, n in c["file_types"].items()) + ")"
    parts = [_plural(c["messages"], "message"), _plural(c["comments"], "comment"), files,
             _plural(c["review_items"], "review item") + " handled"]
    if c["board_actions"]:
        parts.append(_plural(c["board_actions"], "board action"))
    if c["voice"]:
        parts.append(_plural(c["voice"], "voice request"))
    if c["meeting_sec"]:
        parts.append(f"{fmt_dur(c['meeting_sec'])} of meetings")
    return "From you: " + ", ".join(parts) + "."


def _split_or_trigger(task: Task, idx: RunIndex) -> str:
    """What the record shows about where this task's instructions came from."""
    ev = idx.events
    agent = [i for i in task.event_ids if is_agent_work(ev[i]) and not is_workspace_event(ev[i])]
    if not agent:
        if any(ev[i].actor == "artifact" for i in task.event_ids):
            # Not "counted on another task": nothing checks that. The producing
            # session may sit on another task, fall outside the window, or be a
            # store the pipeline never reads (a scheduled job, another machine).
            return ("No agent conversation is attached: none was matched to its commits or "
                    "PRs. The conversation may be on another task, outside the window, or "
                    "not recorded at all, so its hands-on and wall-clock read zero.")
        return "No agent conversation is attached."
    start, end = _parse_ts(ev[agent[0]].ts), _parse_ts(ev[agent[-1]].ts)
    sessions = {ev[i].session_id for i in agent}
    elsewhere: dict[str, int] = defaultdict(int)
    last_before: tuple[datetime, int] | None = None
    for sid in sessions:
        msgs = idx.typed.get(sid, [])
        for ts, i in msgs[bisect_left(msgs, (start, -1)):]:
            if ts > end:
                break
            if idx.owner.get(i) not in (None, task.task_id):
                elsewhere[idx.owner[i]] += 1
        k = bisect_left(msgs, (start, -1))
        if k and (last_before is None or msgs[k - 1][0] > last_before[0]):
            last_before = msgs[k - 1]
    if elsewhere:
        other, n = max(elsewhere.items(), key=lambda kv: kv[1])
        return (f"While it ran you sent {_plural(n, 'message')} in the same session, counted on "
                f"\"{_short(idx.tasks[other].title)}\", so one piece of work was likely split in two.")
    if last_before and start - last_before[0] <= TRIGGER_WINDOW:
        ts, i = last_before
        where = idx.owner.get(i)
        on = ", which is counted on another task" if where and where != task.task_id else ""
        return (f"It started {fmt_dur((start - ts).total_seconds())} after your message "
                f"\"{_short(ev[i].content_snippet)}\"{on}.")
    gap = (f"Your last message in that session was {fmt_dur((start - last_before[0]).total_seconds())} earlier"
           if last_before else "You sent no message in that session before it")
    return (f"{gap}, so something else started it: a peer or team-lead message, a board "
            f"wake-up or a scheduled prompt. The pipeline doesn't count those as you.")


def explain_hands_on(task: Task, idx: RunIndex, hands_on_h: float,
                     transcript_iv: list, workspace_iv: list) -> str:
    c = hands_on_counts(task, idx.events)
    # Under an hour reads in minutes (the user, 2026-09-17): "0.06h" hides what it is.
    head = f"**Hands-on — {fmt_dur(hands_on_h * 3600)}.**"
    tr_h, ws_h = _union_seconds(transcript_iv) / 3600, _union_seconds(workspace_iv) / 3600
    body = [_counts_sentence(c)]
    has_workspace = any(idx.events[i].actor == "user" and is_workspace_event(idx.events[i])
                        for i in task.event_ids)
    if tr_h:
        body.append(f"Messages: {fmt_dur(tr_h * 3600)} (reading and typing time per message, "
                    f"joined under {PRESENCE_STITCH_SEC // 60} min apart).")
    if ws_h:
        # Name the comment rule only on a task that has comments.
        typing = (", plus each comment's typing time and, when nothing on that doc led up "
                  "to it, 1 min of reading before it") if c["comments"] else ""
        body.append(f"Workspace: {fmt_dur(ws_h * 3600)} (activity blocks, gaps under "
                    f"{WORKSPACE_BLOCK_GAP_SEC}s{typing}).")
    if tr_h and ws_h:
        body.append("Overlap counted once.")
    # The user, 2026-09-17: a task he never steered showed 2-4 min and nothing on
    # the page said it was the reading before his review answers.
    answered = [i for i in task.event_ids if i in idx.answer_windows
                and (idx.events[i].metadata or {}).get("board_event") in REVIEW_ANSWER_EVENTS]
    if answered:
        read = sum((e - b).total_seconds() for b, e in (idx.answer_windows[i] for i in answered))
        body.append(f"Review answers: {fmt_dur(read)} of reading before "
                    f"{_plural(len(answered), 'answer')} (up to {REVIEW_READ_CAP_SEC // 60} min each, "
                    f"less when you were on another item or page just before).")
    if has_workspace and not c["comments"] and not answered and ws_h < NEAR_ZERO_H:
        body.append(f"Clicks count as instants: they add time only when other activity on "
                    f"the task falls under {WORKSPACE_BLOCK_GAP_SEC}s away.")
    if hands_on_h < NEAR_ZERO_H:
        body.append(_split_or_trigger(task, idx))
        if task.delegated:
            body.append("Segmentation classed it as a delegated agent-only stretch.")
    return _cap(head + " " + " ".join(body))


def explain_wall_clock(task: Task, idx: RunIndex, wall_h: float, prior_agent) -> str:
    ev = idx.events
    bursts = _agent_intervals_for_task(task, ev, idx.lane_index)
    mine = _bryan_intervals_for_task(task, ev, prior_agent)
    total = _union_seconds(bursts + mine)
    agent_sec = _union_seconds(bursts)
    capped = sum(1 for s, e in bursts if (e - s).total_seconds() >= AGENT_INTERVAL_HARD_CAP_SEC)
    stamps = [_parse_ts(ev[i].ts) for i in task.event_ids]
    span = (max(stamps) - min(stamps)).total_seconds() if stamps else 0.0
    parts = [f"**Wall clock — {wall_h:.2f}h.** Time when anything happened on this task, "
             f"overlaps counted once."]
    if bursts:
        cap = f", {capped} hit the cap" if capped else ""
        parts.append(f"The agent worked in {_plural(len(bursts), 'burst')} (a running tool "
                     f"call joins across {AGENT_BURST_STITCH_SEC // 60} min, a quiet gap "
                     f"across {AGENT_QUIET_STITCH_SEC // 60} min; each capped at "
                     f"{AGENT_INTERVAL_HARD_CAP_SEC // 3600}h{cap}) totalling {fmt_dur(agent_sec)}.")
    else:
        parts.append("No agent working time: commits, PRs and board actions are instants.")
    if total - agent_sec > 30:
        parts.append(f"Modelled reading and typing windows for your messages and comments "
                     f"added {fmt_dur(total - agent_sec)}.")
    quiet = span - total
    if quiet >= 60:
        parts.append(f"First to last event spans {fmt_dur(span)}; the {fmt_dur(quiet)} "
                     f"of quiet between is not counted.")
    if abs(total / 3600 - wall_h) > 1 / 60:
        parts.append(f"(Recomputed {total / 3600:.2f}h; the stored figure differs.)")
    return _cap(" ".join(parts))


def _cap(text: str) -> str:
    words = text.split()
    return text if len(words) <= MAX_WORDS else " ".join(words[:MAX_WORDS]) + "…"
