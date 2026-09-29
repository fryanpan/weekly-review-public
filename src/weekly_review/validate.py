"""Mechanical validity checks for a weekly-review run.

the user's checklist (2026-06-12 in-flight review) plus agent-proposed checks.
Each check prints PASS / REVIEW with the offending items. Run per-run.

Usage: uv run python3 scripts/mechanical_checks.py --run-id <run> [--avail-h N]
"""
from __future__ import annotations
import argparse, sqlite3, json, re
from bisect import bisect_right
from datetime import datetime
from pathlib import Path
from collections import defaultdict

from .runs import DATA_DIR

# Re-exported, never rebuilt: a hardcoded copy sends the CLI's run lookup and
# this module's to different directories the moment WEEKLY_REVIEW_DATA is set.
DATA = DATA_DIR / "runs"


def _parse(s):
    s = s.replace("Z", "+00:00")
    if "." in s:
        h, r = s.split(".", 1)
        u = "".join(c for c in r if c.isdigit())[:6]
        s = h + "." + u + r.lstrip("0123456789")
    return datetime.fromisoformat(s)


def _has_artifact(c, tid):
    return c.execute(
        "SELECT 1 FROM task_events te JOIN events ev USING(event_id) "
        "WHERE te.task_id=? AND ev.kind IN ('pr_merged','pr_opened','commit') LIMIT 1",
        (tid,),
    ).fetchone() is not None


# The user 2026-09-14, on the estimate-sample worksheet: "Any task that has a
# hands-on of 0 that wasn't fully agent-triggered and done end to end has a
# problem. Similarly, anything with wall clock of 0 is wrong." And: "roughly a
# day per 1000 LoC added, if it's real coding ... more than 2x off ... there's
# probably a problem." The helpers below take a connection so they can be
# tested on a small fixture DB; run_checks reports them.
NEAR_ZERO_HANDS_ON_H = 3 / 60   # the multiplier's floor
ZERO_PRINTED_H = 0.005          # rounds to 0.00h on the worksheet
READING_TOLERANCE_H = 1 / 3600  # a second of float drift between a task's hands-on and its answers' reading
LOC_PER_DAY = 1000
# The two sides of the rule answer to different numbers, because the two errors
# do not cost the same. The user, 2026-09-15: "It's fine if the estimator is
# systematically too low on est_h by a bit. That's conservative, and I'm fine
# erring on the side of being conservative. If I'm making decisions off of this
# data, I'm more likely to make bad decisions from the multiplier numbers being
# too high rather than too low (i.e. I'll go promise clients more than I can
# deliver, rather than everyone being pleasantly surprised)."
#
# So the HIGH side — an estimate far above what the code size implies, which
# inflates the multiplier — keeps his outside number of 1000. That is the whole
# reason the check can catch the estimator being wrong rather than merely
# inconsistent, and it is the direction that costs him a promise he cannot keep.
#
# The LOW side allows the systematic conservatism first, then still flags what
# is far out of whack. 1,838 is the estimator's own median over the 75 code
# tasks of the three era weeks, so measuring against it asks "is this task far
# below what this estimator usually does" rather than "is this estimator
# optimistic" — a weaker question, and the right one for a bias he has accepted.
# 9 of 9 flags on those weeks were on the low side; this clears 6 of them and
# keeps the 3 that are 4x under even the observed rate (one is 11,489 lines
# estimated at 3.5h, which is not conservatism, it is a task credited with a
# diff somebody else wrote).
LOC_PER_DAY_OBSERVED = 1838
LOC_TOLERANCE = 4.0  # the user loosened 2x to 4x on the failure-modes doc, 2026-09-14
DAY_H = 8
LOC_MIN_LINES = 200             # below this the rule says nothing
LOC_MAX_LINES = 20_000          # above this it is generated or vendored, not typed
LOC_MAX_LINES_PER_FILE = 400    # a generated file, lockfile or fixture
LOC_SWEEP_DELETE_SHARE = 0.8    # deletions near additions: a rename or search/replace sweep
BOUNDARY_WALL_H = 4
BOUNDARY_RATIO = 3
# B12b: a repo whose conversations link this many PRs, most of them absent from
# the ingested set, lost PRs at fetch time. Small counts are left alone because
# shared repos legitimately mention teammates' PRs the author filter drops.
PR_MENTION_MIN = 50
PR_MENTION_MISSING_SHARE = 0.5


def _has_table_column(c, table: str, column: str) -> bool:
    return column in {r[1] for r in c.execute(f"PRAGMA table_info({table})")}


def _board_task_of(md: dict) -> str | None:
    """Mirror of segmentation.board_task_of over a metadata dict."""
    ctx = md.get("context") if isinstance(md.get("context"), dict) else {}
    for v in (md.get("card"), md.get("task_id"), ctx.get("taskId")):
        if isinstance(v, str) and v.startswith("t-"):
            return v
    return None


def agent_only_board_tasks(c) -> set[str]:
    """Measured tasks whose board task an agent created and that the user never
    commented on, dictated to, or answered or raised a review item on.

    The user, 2026-09-14: "some tasks will have legitimately no hands on time if
    they were created by an agent and done fully by an agent with no review
    requests from me". An unknown creator is not exempt: the check stays strict
    where the board log cannot say."""
    if not _has_table_column(c, "tasks", "board_task") or not _has_table_column(c, "board_tasks", "creator_kind"):
        return set()
    facts = {bt: (ck, hr, hd) for bt, ck, hr, hd in c.execute(
        "SELECT board_task, creator_kind, his_review, his_dictation FROM board_tasks")}
    commented: set[str] = set()
    for (mj,) in c.execute(
            """SELECT metadata_json FROM events WHERE actor = 'user' AND (kind = 'user_message'
               OR (kind = 'workspace_touch' AND json_extract(metadata_json, '$.activity_type') IN ('comment', 'reply')))"""):
        bt = _board_task_of(json.loads(mj or "{}"))
        if bt:
            commented.add(bt)
    out = set()
    for tid, bt in c.execute("SELECT task_id, board_task FROM tasks WHERE board_task IS NOT NULL"):
        ck, hr, hd = facts.get(bt, (None, 0, 0))
        if ck == "agent" and not hr and not hd and bt not in commented:
            out.add(tid)
    return out


_ASK_BOARD_EVENTS = ("decision.answered", "decision.info_requested", "voice.request", "task.created")


def _his_asks(c) -> list[tuple[object, str | None, tuple | None]]:
    """His asks in the window, in time order: filing a board task, a review
    answer, a dictation or a comment. Each is (event, the measured task holding
    it, its modelled window of typing, speaking or reading, recomputed from the
    stored events the way hands-on scores them, or None)."""
    from .effort import is_workspace_event, workspace_windows
    from .events import Event
    host = dict(c.execute("SELECT event_id, task_id FROM task_events"))
    evs: list[Event] = []
    ids: list[str] = []
    for eid, ts, actor, project, sid, src, machine, kind, seq, mj in c.execute(
            """SELECT event_id, ts, actor, project, session_id, source_system, source_machine, kind, seq, metadata_json
               FROM events WHERE actor = 'user' ORDER BY ts"""):
        ev = Event(ts=ts, actor=actor, project=project, session_id=sid, source_system=src, source_machine=machine,
                   kind=kind, seq=seq, metadata=json.loads(mj or "{}"))
        if is_workspace_event(ev):
            evs.append(ev)
            ids.append(eid)
    windows = workspace_windows(evs)
    return [(ev, host.get(ids[idx]), windows.get(idx)) for idx, ev in enumerate(evs)
            if ev.metadata.get("board_event") in _ASK_BOARD_EVENTS or ev.kind == "user_message"]


def _union_hours(spans) -> float:
    total, cur_s, cur_e = 0.0, None, None
    for s, e in sorted(spans):
        if cur_e is None or s > cur_e:
            total += (cur_e - cur_s).total_seconds() if cur_e is not None else 0.0
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += (cur_e - cur_s).total_seconds()
    return total / 3600


def his_asks_by_board_task(c, asks=None) -> dict[str, dict]:
    """His asks in the window on each board task. Per board task: `hosts`, the
    measured tasks holding them; `hours`, the union of their modelled windows;
    `last`, the time of the latest board ask among them. A board task he asked
    nothing on in the window is absent."""
    from .effort import _parse_ts
    out: dict[str, dict] = {}
    for ev, host, span in (_his_asks(c) if asks is None else asks):
        bt = _board_task_of(ev.metadata)
        if not bt:
            continue
        a = out.setdefault(bt, {"hosts": set(), "spans": [], "last": None})
        a["hosts"].add(host)
        if span:
            a["spans"].append(span)
        if ev.metadata.get("board_event"):
            ts = _parse_ts(ev.ts)
            a["last"] = ts if a["last"] is None else max(a["last"], ts)
    for a in out.values():
        a["hours"] = _union_hours(a.pop("spans"))
    return out


# The unkeyed wakes an agent turn records, and his board events behind each.
_WAKE_ASKS = {"voice.request": ("voice.request",),
              "workspace.review_answered": ("decision.answered", "decision.info_requested")}


def unkeyed_asks_here(c, him_turns: dict[str, set], turn_wake: dict[tuple, tuple], asks=None) -> set[str]:
    """Measured tasks with no board task whose every turn he started was woken
    by an ask of his that sits on the task, and whose hands-on covers those
    asks' modelled time: legitimately small, as `asks_here_tasks` is for a
    board task.

    bf56f86e52, week of 2026-09-07: his 24-word dictation to a board, naming
    no task, woke a lead for a day of work. The dictation joined that work and
    scored its speaking; the rest of the day's turns were peers and notices.

    `him_turns` maps a task to the (session, turn) of each turn he started that
    worked on it; `turn_wake` maps a turn to its (board, wake, delivered at).
    A turn with no such wake was started by something he typed, and a wake
    whose ask cannot be found, or sits on another task, leaves the task a
    suspect."""
    from .effort import _parse_ts
    from .segmentation import COMMENT_WAKE_SKEW
    by_wake: dict[tuple, list] = defaultdict(list)
    for ev, host, span in (_his_asks(c) if asks is None else asks):
        ws, name = ev.metadata.get("workspace_id"), ev.metadata.get("board_event")
        for wake, names in _WAKE_ASKS.items():
            if ws and name in names:
                by_wake[(ws, wake)].append((_parse_ts(ev.ts), host, span))
    hands = dict(c.execute("SELECT task_id, active_hours FROM task_metrics"))
    out = set()
    for tid, turns in him_turns.items():
        spans, ok = [], bool(turns)
        for turn in turns:
            ws, wake, at = turn_wake.get(turn, (None, None, None))
            found = [(ts, h, s) for ts, h, s in by_wake.get((ws, wake), [])
                     if at is not None and -COMMENT_WAKE_SKEW <= _parse_ts(at) - ts <= COMMENT_WAKE_SKEW]
            ask = max(found, key=lambda x: x[0], default=None)
            if ask is None or ask[1] != tid:
                ok = False
                break
            if ask[2]:
                spans.append(ask[2])
        if ok and hands.get(tid, 0.0) + READING_TOLERANCE_H >= _union_hours(spans):
            out.add(tid)
    return out


def asks_here_tasks(c, asks=None) -> set[str]:
    """Measured tasks whose board task holds every ask he made on it, each
    scored: legitimately small, however little he wrote, said or read.

    The split check looks for his ask on one task and the work on another. When
    all his asks naming a board task sit on the one measured task, and its
    hands-on covers their modelled time, the ask is where the work is and the
    time is just short. His latest ask must be accounted for: before the window
    starts it was measured in its own week (6c1a34dc26, week of 2026-09-07: he
    answered on Sep 6 and the agent worked on through Sep 8); inside the window
    it must be among these asks, or an ask went missing and the task stays a
    suspect."""
    from .effort import _parse_ts
    if not _has_table_column(c, "board_tasks", "his_last_ask"):
        return set()
    period = c.execute("SELECT MIN(period_start) FROM run_meta").fetchone() \
        if _has_table_column(c, "run_meta", "period_start") else None
    # A bare date is the window's first UTC day; an unknown start credits no ask to an earlier week.
    start = None
    if period and period[0]:
        start = _parse_ts(period[0] if "T" in period[0] else period[0] + "T00:00:00+00:00")
    last_ask = {bt: _parse_ts(v) for bt, v in c.execute(
        "SELECT board_task, his_last_ask FROM board_tasks WHERE his_last_ask IS NOT NULL")}
    asks = his_asks_by_board_task(c, asks)
    hands = dict(c.execute("SELECT task_id, active_hours FROM task_metrics"))
    out = set()
    for tid, bt in c.execute("SELECT task_id, board_task FROM tasks WHERE board_task IS NOT NULL"):
        a = asks.get(bt)
        last = last_ask.get(bt)
        if a is None:
            if last is not None and start is not None and last < start:
                out.add(tid)
            continue
        accounted = last is None or (start is not None and last < start) or \
            (a["last"] is not None and abs((a["last"] - last).total_seconds()) < 1)
        if a["hosts"] == {tid} and accounted and hands.get(tid, 0.0) + READING_TOLERANCE_H >= a["hours"]:
            out.add(tid)
    return out


# Over `tasks t LEFT JOIN enriched e`: an estimated task, or one whose estimate
# is missing and that B4d calls substantive (more than 20 events or a shipped
# artifact). A failed estimate must not hide a task from a check.
_ESTIMATED_OR_SUBSTANTIVE = (
    "(e.est_human_hours > 0 OR (e.task_id IS NULL AND (t.event_count > 20 OR EXISTS ("
    "SELECT 1 FROM task_events te JOIN events ev USING(event_id) "
    "WHERE te.task_id = t.task_id AND ev.kind IN ('pr_merged', 'pr_opened', 'commit')))))")


def _labor_col(c) -> str:
    """SQL for labor_hours (user ∪ agent, pauses removed) on alias `m`,
    whichever schema version wrote this run db."""
    from .persist import metric_columns
    return metric_columns(c)["labor"]


def zero_wall_tasks(c) -> list[tuple[str, float | None, str]]:
    """(task, est or None, title) for tasks with zero labor, estimated or
    substantive, estimated first by size."""
    return c.execute(
        f"""SELECT t.task_id, e.est_human_hours, COALESCE(e.title, t.title)
            FROM tasks t JOIN task_metrics m USING(task_id) LEFT JOIN enriched e USING(task_id)
            WHERE {_labor_col(c)} <= 0 AND {_ESTIMATED_OR_SUBSTANTIVE}
            ORDER BY e.est_human_hours IS NULL, e.est_human_hours DESC""").fetchall()


def zero_hands_on_suspects(c) -> dict[str, list[tuple[str, float | None]]]:
    """Estimated tasks under the hands-on floor, split by why they are there.

    `split`: an agent session on the task carries his message on ANOTHER task,
    so the ask and the work were cut apart. `agent_only`: would be `split`, but
    the task is a board task an agent created and he never commented on,
    dictated to or answered a review item on, which the user ruled legitimately
    zero on 2026-09-14; reported, not failed. `agent_triggered`: would be
    `split`, but the task has no board task and every turn of its agent work was
    started by a peer, a board notice or a schedule, which is agent-triggered
    end to end (the user's zero-hands-on rule); reported, not failed. `asks_here`:
    would be `split`, but every ask he made on its board task sits on it and
    scored (see `asks_here_tasks`); reported, not failed. `no_conversation`: no agent
    conversation at all, only artifacts or board actions. `own_messages`: the
    task holds his messages and still scored 0.00h, which is an interval bug;
    one that holds his messages and scored a minute or two is a short dispatch
    and is not returned.
    A task whose sessions hold no message of his anywhere was agent-triggered
    end to end and is legitimately zero, so it is not returned.
    """
    # A substantive task whose estimate failed is a suspect too, with est None:
    # week of 2026-09-07 ran out of API budget mid-run, and two split tasks,
    # one a day of work, hid behind a PASS because neither had an estimate.
    cand = {r[0]: r[1] for r in c.execute(
        f"""SELECT t.task_id, e.est_human_hours FROM tasks t JOIN task_metrics m USING(task_id)
            LEFT JOIN enriched e USING(task_id) WHERE m.active_hours < ? AND {_ESTIMATED_OR_SUBSTANTIVE}""",
        (NEAR_ZERO_HANDS_ON_H,))}
    out: dict[str, list[tuple[str, float]]] = {"split": [], "no_conversation": [], "own_messages": [], "agent_only": [],
                                               "agent_triggered": [], "asks_here": []}
    if not cand:
        return out
    msg_tasks_by_session: dict[str, set] = defaultdict(set)
    for tid, sid in c.execute(
            """SELECT te.task_id, ev.session_id FROM events ev JOIN task_events te USING(event_id)
               WHERE ev.actor = 'user' AND ev.kind = 'user_message'"""):
        msg_tasks_by_session[sid].add(tid)
    # A one-line "merge it" dispatch is honestly a minute or two, so only a task
    # whose messages scored what the worksheet prints as 0.00h is an interval bug.
    zero = {r[0] for r in c.execute("SELECT task_id FROM task_metrics WHERE active_hours < ?",
                                    (ZERO_PRINTED_H,))}
    own_msg = {t for tids in msg_tasks_by_session.values() for t in tids if t in cand}
    own_msg_zero = own_msg & zero
    agent_sessions: dict[str, set] = defaultdict(set)
    triggers: dict[str, set] = defaultdict(set)
    # A subagent lane's transcript never saw what started its work: it inherits
    # the trigger of the main-lane turn running when the lane began, the way
    # segmentation hands it that turn's comment and card.
    # A teammate starts a new stretch with each message the lead sent it, and
    # inherits that sending turn's trigger instead, as segmentation does.
    from datetime import datetime as _dt
    from .segmentation import LANE_DISPATCH_TOOLS, LANE_MESSAGE_DELIVERY
    # Each carries the (session, turn) it came from, so a turn he started can be
    # traced to the ask that woke it (see unkeyed_asks_here).
    main_trig: dict[str, list[tuple[_dt, str | None, tuple]]] = defaultdict(list)
    main_send: dict[str, list[tuple[_dt, str | None, tuple]]] = defaultdict(list)
    lane_stretch: dict[tuple[str, str], list[tuple[_dt, str | None, tuple | None]]] = defaultdict(list)
    turn_wake: dict[tuple, tuple] = {}
    for sid, lane, ts, kind, tool, trig, turn, wake, wake_ws, wake_at in c.execute(
            """SELECT session_id, json_extract(metadata_json, '$.agent_lane'), ts, kind,
                      json_extract(metadata_json, '$.tool_name'), json_extract(metadata_json, '$.trigger'),
                      json_extract(metadata_json, '$.turn'), json_extract(metadata_json, '$.ask_wake'),
                      json_extract(metadata_json, '$.ask_wake_ws'), json_extract(metadata_json, '$.ask_wake_at')
               FROM events WHERE actor = 'agent' AND source_system = 'claude_transcript'
                 AND json_extract(metadata_json, '$.agent_lane') IS NOT NULL ORDER BY ts"""):
        at = _dt.fromisoformat(ts.replace("Z", "+00:00"))
        if lane == "main":
            main_trig[sid].append((at, trig, (sid, turn)))
            if wake and wake_at and turn is not None:
                turn_wake.setdefault((sid, turn), (wake_ws, wake, wake_at))
            if kind == "tool_call" and tool in LANE_DISPATCH_TOOLS:
                main_send[sid].append((at, trig, (sid, turn)))
            continue
        stretches = lane_stretch[(sid, lane)]
        if not stretches:
            pos = bisect_right([t for t, _, _ in main_trig[sid]], at)
            stretches.append((at, *main_trig[sid][pos - 1][1:]) if pos else (at, None, None))
        elif kind == "subagent_input" and main_send[sid] and at - main_send[sid][-1][0] <= LANE_MESSAGE_DELIVERY:
            stretches.append((at, *main_send[sid][-1][1:]))

    def lane_origin(sid: str, lane: str, ts: str) -> tuple:
        stretches = lane_stretch[(sid, lane)]
        pos = bisect_right([t for t, _, _ in stretches], _dt.fromisoformat(ts.replace("Z", "+00:00")))
        return stretches[pos - 1][1:] if pos else (None, None)
    him_turns: dict[str, set] = defaultdict(set)
    for tid, sid, lane, ts, trig, turn in c.execute(
            f"""SELECT te.task_id, ev.session_id, json_extract(ev.metadata_json, '$.agent_lane'), ev.ts,
                      json_extract(ev.metadata_json, '$.trigger'), json_extract(ev.metadata_json, '$.turn')
               FROM task_events te JOIN events ev USING(event_id)
               WHERE ev.actor = 'agent' AND {NOT_INSTANT}"""):
        if tid in cand:
            agent_sessions[tid].add(sid)
            origin = (sid, turn) if lane == "main" else None
            if trig is None and lane and lane != "main" and (sid, lane) in lane_stretch:
                trig, origin = lane_origin(sid, lane, ts)
            triggers[tid].add(trig)
            if trig == "him":
                him_turns[tid].add(origin)
    agent_only = agent_only_board_tasks(c)
    # His asks all sit on the task and scored: a review answer's reading is two
    # minutes at most, cut where he was last somewhere else (the user, 2026-09-14),
    # a short dictation or task title less. A window can be seconds long, so
    # what is checked is that the task holds its asks' modelled time, not that
    # it printed above 0.00h (see asks_here_tasks).
    asks = _his_asks(c)
    asks_here = asks_here_tasks(c, asks)
    board = dict(c.execute("SELECT task_id, board_task FROM tasks")) if _has_table_column(c, "tasks", "board_task") else {}
    # With no board task, turns he started count when his ask that woke each
    # one sits here and scored; every other turn must be a peer's, a notice's
    # or a schedule's.
    unboarded = {t: v for t, v in him_turns.items() if not board.get(t) and triggers[t] <= {"agent", "him"}}
    asks_here |= unkeyed_asks_here(c, unboarded, turn_wake, asks)
    for tid, est in cand.items():
        if tid in own_msg:
            if tid in own_msg_zero:
                out["own_messages"].append((tid, est))
        elif not agent_sessions.get(tid):
            out["no_conversation"].append((tid, est))
        elif any(msg_tasks_by_session.get(s) for s in agent_sessions[tid]):
            # With no board task, what started the work decides: a peer, a board
            # notice or a schedule is agent-triggered end to end. Work with any
            # turn of unknown origin (a subagent lane) stays a split suspect.
            bucket = ("agent_only" if tid in agent_only else
                      "asks_here" if tid in asks_here else
                      "agent_triggered" if not board.get(tid) and triggers[tid] == {"agent"} else "split")
            out[bucket].append((tid, est))
    for v in out.values():
        v.sort(key=lambda x: (x[1] is not None, x[1] or 0), reverse=True)
    return out


def _est_sum(rows) -> str:
    """Σest over the estimated rows, naming how many had no estimate to sum."""
    missing = sum(1 for _, e in rows if e is None)
    return f"Σest {sum(e or 0 for _, e in rows):.0f}h" + (f", {missing} unestimated" if missing else "")


def _est_items(rows) -> list[str]:
    return [f"{t[:10]}: est {'?' if e is None else f'{e:g}h'}" for t, e in rows]


# Instants create no work, mirroring effort.is_agent_work: board actions, and an
# agent's tool call on a board card (the same action seen from its transcript).
NOT_INSTANT = ("ev.kind != 'board_action' AND NOT (ev.kind IN ('tool_call', 'tool_result') "
               "AND json_extract(ev.metadata_json, '$.card') IS NOT NULL)")
CONCURRENCY_WINDOW_SEC = 300
CONCURRENCY_LIMIT = 2


def lanes_working_many_tasks(c, window_sec: int = CONCURRENCY_WINDOW_SEC,
                             limit: int = CONCURRENCY_LIMIT) -> list[tuple[str, str, int]]:
    """Lanes that had more than `limit` tasks in hand inside one window.

    Three things this does not count, each because it is not somebody working:

    A **span is not occupancy.** A task used to hold its lane from its first
    event to its last, so a board stamp days later kept a finished task open.
    At the worst flagged moment on the week of 2026-09-07 — 31 tasks "at once"
    — 24 of the 31 had no event within an hour either side. What counts here is
    an event inside the window.

    A **stamp is not work.** `NOT_INSTANT` is this file's own predicate: a
    `task_transition` tool call is the transcript twin of the `board_action`
    row it writes, and a status sweep puts one call and its result on ten cards
    in fifteen seconds. Counting them makes bookkeeping look like ten threads.

    That predicate also takes out the **board logs**, which were 9 of that
    week's 20 flags and one lane of 62: `workspace-board:<id>` is one synthetic
    session per board, so every task a board ever touched shares it. It needs
    no clause of its own — on all three era weeks every row on such a session
    is a `board_action` (1,370 and 3,351 of them) and no `board_action` arrives
    on any other session, so excluding the session id would be a second way of
    saying the same thing, and one no fixture could tell apart.

    The worker is the lane rather than the session because a session now runs
    about twenty of them. On the three era weeks that distinction costs almost
    nothing on its own (11 sessions became 12 lanes); it is the right key, not
    the fix.

    Returns (session_id, lane, max tasks in one window), worst first.
    """
    per: dict[tuple[str, str], list] = defaultdict(list)
    for tid, sid, lane, ts in c.execute(
            f"""SELECT te.task_id, ev.session_id,
                       json_extract(ev.metadata_json, '$.agent_lane'), ev.ts
                FROM task_events te JOIN events ev USING(event_id)
                WHERE ev.session_id NOT LIKE 'git:%' AND ev.session_id NOT LIKE 'github:%'
                  AND ev.session_id NOT LIKE 'linear%' AND ev.session_id NOT LIKE 'slack%'
                  AND ev.session_id != 'unknown' AND {NOT_INSTANT}"""):
        try:
            per[(sid, lane or "main")].append((_parse(ts), tid))
        except ValueError:
            pass

    flagged = []
    for (sid, lane), stream in per.items():
        if len({t for _, t in stream}) <= limit:
            continue
        stream.sort()
        # Widest count of distinct tasks over any `window_sec` of the lane's
        # own events — a sliding window rather than fixed buckets, so two
        # tasks a second apart cannot land either side of a boundary.
        held: dict[str, int] = defaultdict(int)
        left = mx = 0
        for right, (at, tid) in enumerate(stream):
            held[tid] += 1
            while (at - stream[left][0]).total_seconds() > window_sec:
                gone = stream[left][1]
                held[gone] -= 1
                if not held[gone]:
                    del held[gone]
                left += 1
            mx = max(mx, len(held))
        if mx > limit:
            flagged.append((sid, lane, mx))
    return sorted(flagged, key=lambda x: -x[2])

# How much parallelism on one project is realistic — and it is not one number,
# because how the user works changed. His ruling of 2026-09-15: "with workspaces,
# we're now often running 4+ tasks in parallel on some projects... Keep the
# threshold at 3 up until end of July 2026. And increase it to 25 starting in
# Aug 2026. Any time it goes much above that at the moment, there's likely a bug
# or something spinning out of control or a cost issue."
#
# So the check keeps its purpose — unrealistic parallelism — and gets a dated
# limit rather than a tuned one. The boundary is hard on purpose: a run is
# measured against the limit in force at the moment it measures, so a window
# spanning 31 July into 1 August is judged by both, and the era cannot quietly
# widen to swallow a week that fails.
PROJECT_CONCURRENCY_ERAS: tuple[tuple[str, int], ...] = (("2026-08-01", 25),)
PROJECT_CONCURRENCY_LIMIT = 3  # before the first era above


def project_concurrency_limit(at: datetime) -> int:
    """The limit in force at this moment. Eras are inclusive of their start day."""
    limit = PROJECT_CONCURRENCY_LIMIT
    for start, value in PROJECT_CONCURRENCY_ERAS:
        if at.strftime("%Y-%m-%d") >= start:
            limit = value
    return limit


def projects_working_many_tasks(c, window_sec: int = CONCURRENCY_WINDOW_SEC,
                                limit: int | None = None) -> list[tuple[str, int, int]]:
    """Projects that had more tasks being worked at once than the era allows.

    This counted overlapping task SPANS — first event to last — and reported one
    project running **79 tasks at once**. The lane check above abandoned spans
    for the reasons in its own docstring, and they apply here unchanged: a span
    is not occupancy, so a task that took a board stamp days after its work
    finished stays "open" all week, and a stamp is not work. Counting instead
    the distinct tasks with a real event inside one window gives 12 for that
    project (2026-08-17) and 16 for its worst week (09-07).

    Those are not overload, they are fan-out, which is why the limit is dated
    (see `PROJECT_CONCURRENCY_ERAS`). `limit` overrides the era, for tests and
    for asking what another threshold would have said.

    Returns (project, max tasks in one window, the limit that window was judged
    against), worst first.
    """
    per: dict[str, list] = defaultdict(list)
    for proj, tid, ts in c.execute(
            f"""SELECT t.project, te.task_id, ev.ts
                FROM task_events te JOIN events ev USING(event_id)
                JOIN tasks t ON t.task_id = te.task_id
                WHERE {NOT_INSTANT}"""):
        try:
            per[proj or ""].append((_parse(ts), tid))
        except ValueError:
            pass
    flagged = []
    for proj, stream in per.items():
        stream.sort()
        held: dict[str, int] = defaultdict(int)
        left = mx = worst_limit = 0
        for at, tid in stream:
            held[tid] += 1
            while (at - stream[left][0]).total_seconds() > window_sec:
                gone = stream[left][1]
                held[gone] -= 1
                if not held[gone]:
                    del held[gone]
                left += 1
            # Each window answers to the limit in force when it happened, so a
            # run straddling an era boundary is judged by both, not by whichever
            # end of it the run started on.
            cap = limit if limit is not None else project_concurrency_limit(at)
            if len(held) > cap and len(held) - cap > mx - worst_limit:
                mx, worst_limit = len(held), cap
        if mx:
            flagged.append((proj, mx, worst_limit))
    return sorted(flagged, key=lambda x: -(x[1] - x[2]))

OVERSIZE_DAYS = 2
OVERSIZE_LANES = 100
# A parenthetical aside under this length is an agent saying it has nothing to
# do; a real answer does not fit. Two of them is a pattern, one is an aside.
# The rule itself lives in `effort`, because the scorer now treats such a reply
# as an instant and this check has to flag exactly what the scorer stops
# billing. Two copies would drift and nothing would say so.
POLL_NOOP_MIN = 2
# ...and the check is about wall clock being HELD OPEN, so the replies have to
# span something. Two asides four minutes apart hold nothing open; on the three
# era weeks this is the whole difference between 0/3/2 and 0/1/1, and the three
# it drops carry 0.0h to 0.4h of span between them.
POLL_NOOP_SPAN_H = 1.0
DRIFT_RATIO = 2.0

# A rationale's own conclusion, for the est-vs-rationale checks (B4 / B4a).
#
# **The last hour-number in a rationale is not its conclusion (2026-09-15).**
# B4 read the LAST match of `_HOURS` and called it what the rationale concluded.
# Both of the check's live flags across all three era weeks were that read
# misfiring, and under a conclusion-aware read both agree with their estimate
# EXACTLY:
#
#   05b6b62147 (wC, est 14h): "...the rest average ~1h each -- totalling
#   approximately 14h." Reported as 14x apart. It is 1.0x.
#   679823a31d (wA, est 3.5h): "...roughly 3-4 hours total, centering around
#   3.5h." Reported as 14x apart. It is 1.0x.
#
# The fix is two independent things, and the mutation test says which does what:
#
#   - **The vocabulary was too small**, and that alone accounts for BOTH real
#     cases. "approximately" and "around" were not in the alternation, so each
#     rationale's actual conclusion was not merely late in the string, it was
#     invisible, and the check settled for an earlier figure.
#   - **The last figure is not the conclusion when an explicit total follows a
#     per-item rate.** No era week has that shape, so this half is carried by
#     its test alone -- deliberately, because 05b6b62147 misses it by one word
#     ("totalling approximately 14h" is both last AND a total) and the next
#     rationale phrased "in total 2h, i.e. ~40h each" would misread again.
#
# Neither half can soften the signature the check exists for: an 80h estimate
# over a rationale concluding ~1.5h with no total phrase still reads 1.5h, and
# `test_the_stale_eighty_hour_signature_still_reads_as_it_did` pins that.
_HOURS = re.compile(r"(?:≈|~|about|roughly|approximately|around|circa|=)\s*"
                    r"([0-9]+(?:\.[0-9]+)?)\s*h\b", re.I)
_TOTAL_HOURS = re.compile(
    r"\b(?:total(?:l?ing|s)?|in total|altogether|overall|comes? to|sums? to|"
    r"summing to|centering (?:at|around|on)|putting the total at)\b"
    # No digit and no sentence end may sit between the total word and its
    # figure. Rationales count other things too -- "Four PRs totalling ~1,300
    # lines added across 19 files: one is a trivial version bump (~0.5h)" read
    # as a 0.5h TOTAL on a 6.5h task when this gap allowed digits through, and
    # so did a second wC task. A total of lines is not a total of hours.
    r"[^.0-9]{0,40}?"
    r"(?:≈|~|about|roughly|approximately|around|circa|=|\bat\b)?\s*"
    r"([0-9]+(?:\.[0-9]+)?)\s*h\b", re.I)


def rationale_conclusion(text: str | None) -> float | None:
    """The hours the rationale itself concludes, or None if it names none.

    An explicit total wins over position; otherwise the last figure stands.
    """
    if not text:
        return None
    for pat in (_TOTAL_HOURS, _HOURS):
        found = pat.findall(text)
        if found:
            value = float(found[-1])
            if value > 0:
                return value
    return None


def oversized_tasks(c) -> list[tuple[str, float | None, int, int]]:
    """(task, est, days with agent work, agent lanes) for tasks too big for one
    boundary. Days are UTC dates carrying any non-board agent event.

    A board task is one piece of work however many days it runs (the user,
    2026-09-14), so only the lanes rule applies to it."""
    board = set()
    if _has_table_column(c, "tasks", "board_task"):
        board = {r[0] for r in c.execute("SELECT task_id FROM tasks WHERE board_task IS NOT NULL")}
    rows = c.execute(
        f"""SELECT te.task_id, COUNT(DISTINCT substr(ev.ts, 1, 10)) days,
                  COUNT(DISTINCT COALESCE(json_extract(ev.metadata_json, '$.agent_lane'), 'main')) lanes
           FROM task_events te JOIN events ev USING(event_id)
           WHERE ev.actor = 'agent' AND {NOT_INSTANT}
           GROUP BY te.task_id HAVING days > ? OR lanes > ?""", (OVERSIZE_DAYS, OVERSIZE_LANES)).fetchall()
    rows = [(t, d, l) for t, d, l in rows if l > OVERSIZE_LANES or t not in board]
    est = dict(c.execute("SELECT task_id, est_human_hours FROM enriched"))
    return sorted(((t, est.get(t), d, l) for t, d, l in rows), key=lambda r: -(r[1] or 0))


# the user's impact limit for a one-week run (2026-09-14): more than this many
# tasks or hours must be addressed rather than accepted.
IMPACT_TASKS_WEEK = 2
IMPACT_HOURS_WEEK = 5.0


def _own_hours(md: dict) -> float:
    return float(md.get("fixed_active_sec") or 0.0) / 3600


def board_task_audit(c) -> dict:
    """How events line up with board tasks (the user, 2026-09-14: "start from the
    board tasks and try as best as possible to assign events to tasks. And also
    audit for events that are unassigned").

    `mapping`: board tasks with work evidence (anything naming them except
    board housekeeping) and how many measured tasks stand for each; the rule
    is exactly one. `housekeeping_only`: board tasks named only by moves,
    archives and the like, which get no measured task. `misplaced`: events
    naming a board task that has a measured task but sitting on another task.
    `unassigned`: events on no task. Hours are the events' own recorded
    duration; instants carry none."""
    host: dict[str, str] = dict(c.execute("SELECT event_id, task_id FROM task_events"))
    measured: dict[str, set[str]] = defaultdict(set)
    if _has_table_column(c, "tasks", "board_task"):
        for tid, bt in c.execute("SELECT task_id, board_task FROM tasks WHERE board_task IS NOT NULL"):
            measured[bt].add(tid)
    evidenced: set[str] = set()
    named: set[str] = set()
    misplaced: list[tuple[str, str, str | None, float]] = []
    unassigned: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0, 0.0])
    naming_rows = []
    for eid, src, kind, actor, mj in c.execute("SELECT event_id, source_system, kind, actor, metadata_json FROM events"):
        md = json.loads(mj or "{}")
        if eid not in host:
            u = unassigned[(src, kind)]
            u[0] += 1
            u[1] += _own_hours(md)
        bt = _board_task_of(md)
        if not bt:
            continue
        named.add(bt)
        if kind in ("tool_call", "tool_result"):
            pass  # an agent's call naming the card is housekeeping, as in segmentation
        elif src != "workspace_board" or (actor == "user" and str(md.get("board_event") or "").startswith(("decision.", "voice."))):
            # His review answer or dictation; an agent answering its own item is housekeeping.
            evidenced.add(bt)
        naming_rows.append((eid, bt, _own_hours(md)))
    for eid, bt, h in naming_rows:
        if measured.get(bt) and host.get(eid) not in measured[bt]:
            misplaced.append((eid, bt, host.get(eid), h))
    mapping = {bt: len(measured.get(bt, ())) for bt in evidenced | set(measured)}
    return {
        "mapping": mapping,
        "housekeeping_only": sorted(named - evidenced - set(measured)),
        "misplaced": misplaced,
        "unassigned": {k: (n, h) for k, (n, h) in unassigned.items()},
    }


def polling_held_wall(c) -> list[tuple[str, int, float, float]]:
    """(task, no-op replies, wall h, the span they cover) for a task whose wall
    clock is held open by an agent that answered it was doing nothing.

    **This replaced a ratio that was not a ratio (2026-09-15).** The old rule
    divided distinct clock minutes carrying an event — counted over the task's
    whole SPAN — by `labor_hours`, which `effort.py` builds as a UNION of
    stitched intervals with the long silences taken out. Two time bases, so the
    quotient is a share of nothing: over the three era weeks it runs 0.27 to
    2.41, and 13 tasks score above 1.0. What it really measures is events per
    minute, which is a property of the era and not of the work — the median
    moved 0.54 on the April week to 0.77 and 0.78 on the August and September
    ones as board actions, workspace touches and parallel lanes thickened the
    stream. A fixed 0.50 cutoff therefore lands on April's MEDIAN and flags a
    third of that week (7 of 21) against 1 of 71 and 3 of 91 later.

    Reading all eleven settled it: nine had the agent busy for 86-100% of their
    own wall clock, opened on one of his messages and closed on the agent's —
    ordinary sessions. What the stitch bridged was him taking 8 to 29 minutes to
    reply (the largest bucket on five of them) and tool latency up to 10
    minutes. A minute-count cannot tell either from a poll, and neither can the
    obvious replacement: the gap between two agent events is model generation
    about as often as it is idling, so a rule scoring agent-to-agent silence
    flags 1/5/12 with almost no overlap with the old one. Two rules disagreeing
    that completely means neither is measuring the thing.

    So stop proxying and read what the agent said. One flagged task was the real
    defect, and its stream names the mechanism: woken again and again by board
    flap, the agent replied "(Holding.)" fifteen times across 7.3h, holding
    1.40h of wall clock on a task estimated at 0.25h. A short parenthetical
    reply is an agent declining to work, and it still extends the burst — the
    same argument as `INSTANT_AGENT_KINDS`, that a stamped moment must not
    create duration. Matching is deliberately narrow (wrapped in parentheses,
    under 120 characters) because a false positive here silences a real answer.
    It is not the lenient direction: it clears ten tasks that were shown one by
    one not to be polling, and it catches `26e3e48981` on the September week —
    eleven "(waiting)" replies over 5.1h — which the old rule missed because
    0.97h of wall clock sat under its 1h floor. Over the three era weeks it
    reports 0 / 1 / 1 against the old 7 / 1 / 3, and the one week they agree on
    is the only week the old rule was right about."""
    from .effort import _parse_ts     # git is local-TZ, transcripts are UTC-Z
    from .effort import is_declined_text
    noop: dict[str, list[str]] = defaultdict(list)
    for tid, ts, snip in c.execute(
            """SELECT te.task_id, ev.ts, ev.content_snippet FROM task_events te
               JOIN events ev USING(event_id)
               WHERE ev.actor = 'agent' AND ev.kind = 'agent_message'"""):
        if is_declined_text(snip):
            noop[tid].append(ts)
    walls = dict(c.execute(f"SELECT task_id, {_labor_col(c)} FROM task_metrics m"))
    out = []
    for tid, stamps in noop.items():
        if len(stamps) < POLL_NOOP_MIN:
            continue
        lo, hi = min(stamps), max(stamps)
        span = (_parse_ts(hi) - _parse_ts(lo)).total_seconds() / 3600
        if span >= POLL_NOOP_SPAN_H:
            out.append((tid, len(stamps), walls.get(tid, 0.0), span))
    return sorted(out, key=lambda r: -r[1])


CARD_SPREAD_TASKS = 2
CARDS_PER_TASK = 10
# A lane handing off to the next chapter of a ticket switches once or twice; a
# lane whose single conversation was cut into two tasks switches all day. The
# gap between the two on the three era weeks is 2 against 189.
DUP_LANE_SWITCHES = 2


def work_unit_spread(c) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    """(board cards or doc threads on >2 tasks, tasks holding >10 cards).

    A board card or a comment thread is one piece of work, so it landing on
    many tasks is a split, and one task holding many cards is a merge.

    **A stamp is not work**, so `NOT_INSTANT` applies here for the same reason
    the concurrency check gives next door: a `task_transition` tool call is the
    transcript twin of the `board_action` row it writes, and one sweep of the
    board puts a call and its result on ten cards in fifteen seconds. Counting
    those made bookkeeping look like merged work — the worst task on the week of
    2026-08-17 "held" 53 cards and has ONE it did anything but stamp, and every
    card finding on both dense weeks (13 and 7) was this. It is not a check with
    nothing to read afterwards: about a third of the card evidence survives
    (151 and 191 of 439 and 471 task-card pairs), as dispatch prompts naming a
    card and the user's own reading, commenting and typing on one."""
    unit_tasks: dict[str, set[str]] = defaultdict(set)
    task_cards: dict[str, set[str]] = defaultdict(set)
    for tid, card, doc, thread in c.execute(
            f"""SELECT te.task_id,
                      COALESCE(json_extract(ev.metadata_json, '$.task_id'),
                               json_extract(ev.metadata_json, '$.card'),
                               json_extract(ev.metadata_json, '$.context.taskId')),
                      CASE WHEN ev.kind = 'user_message' THEN json_extract(ev.metadata_json, '$.doc_id') END,
                      CASE WHEN ev.kind = 'user_message' THEN json_extract(ev.metadata_json, '$.thread_id') END
               FROM task_events te JOIN events ev USING(event_id)
               WHERE {NOT_INSTANT}"""):
        if isinstance(card, str) and card.startswith("t-"):
            unit_tasks[card].add(tid)
            task_cards[tid].add(card)
        if doc and thread:
            unit_tasks[f"{doc}#{thread}"].add(tid)
    spread = sorted(((u, len(v)) for u, v in unit_tasks.items() if len(v) > CARD_SPREAD_TASKS), key=lambda r: -r[1])
    broad = sorted(((t, len(v)) for t, v in task_cards.items() if len(v) > CARDS_PER_TASK), key=lambda r: -r[1])
    return spread, broad


DUP_TITLE_JACCARD = 0.6
_DUP_STOP = {"the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with",
             "fix", "fixed", "add", "added", "update", "updated"}


def duplicate_work_pairs(c) -> list[str]:
    """Same-project task pairs that look like one piece of work reported twice.

    Three conditions, and the third is what makes the finding mean what it says:
    overlapping windows, a shared issue ref or a >=60% title overlap, AND a lane
    that alternates between the two tasks.

    The first two alone flagged one client ticket worked over three days as six
    duplicate pairs. A task's window runs from its first event to its last, so
    anything long-running overlaps its whole project, and a ticket picked up
    again tomorrow is not the same work done twice — none of those six shared a
    single shipped artifact. What a cut conversation actually looks like is a
    lane switching back and forth: on the week of 2026-09-07 one lane alternated
    189 times across 439 events between two tasks, against a worst case of 2 on
    every one of the six (measured 2026-09-15).
    """
    lane_seq: dict[tuple, list] = defaultdict(list)
    for tid, sid, lane in c.execute(
            """SELECT te.task_id, ev.session_id,
                      json_extract(ev.metadata_json, '$.agent_lane')
               FROM task_events te JOIN events ev USING(event_id) ORDER BY ev.ts"""):
        seq = lane_seq[(sid, lane or "main")]
        if not seq or seq[-1] != tid:
            seq.append(tid)  # run-length: only the switches matter
    lanes_of: dict[str, set] = defaultdict(set)
    for key, seq in lane_seq.items():
        for tid in seq:
            lanes_of[tid].add(key)

    def switches(x: str, y: str) -> int:
        """Most times any single lane alternated between these two tasks."""
        worst = 0
        for key in lanes_of[x] & lanes_of[y]:
            seq = [t for t in lane_seq[key] if t in (x, y)]
            worst = max(worst, sum(1 for p, q in zip(seq, seq[1:]) if p != q))
        return worst

    def toks(t):
        return {w for w in (t or "").lower().split()
                if len(w) > 3 and w not in _DUP_STOP}

    by_proj: dict[str, list] = defaultdict(list)
    for tid, proj, title, first, last, refs in c.execute(
            """SELECT task_id, project, title, first_ts, last_ts, issue_refs_json
               FROM tasks WHERE first_ts IS NOT NULL"""):
        by_proj[proj].append((first, last, tid, title, set(json.loads(refs or "[]"))))

    out = []
    for proj, rows_ in by_proj.items():
        rows_.sort()
        for i, (_fa, la, ta_id, ta_title, ra) in enumerate(rows_):
            for fb, _lb, tb_id, tb_title, rb in rows_[i + 1:]:
                if fb > la:
                    break
                wa, wb = toks(ta_title), toks(tb_title)
                jac = len(wa & wb) / len(wa | wb) if (wa and wb) else 0
                if not ((ra and rb and ra & rb) or jac >= DUP_TITLE_JACCARD):
                    continue
                sw = switches(ta_id, tb_id)
                if sw <= DUP_LANE_SWITCHES:
                    continue
                why = f"refs {sorted(ra & rb)}" if (ra & rb) else f"title {jac:.0%}"
                out.append(f"{proj}: {ta_id[:8]} ~ {tb_id[:8]} "
                           f"({why}, one lane alternates {sw}x)")
    return out


def previous_run_same_window(run_id: str, start: str, end: str, runs_dir: Path | None = None) -> str | None:
    """The newest earlier run over exactly this window, if any."""
    runs_dir = runs_dir or DATA
    for d in sorted((p for p in runs_dir.iterdir() if p.name < run_id), reverse=True):
        db = d / "run.sqlite"
        if not db.exists():
            continue
        try:
            with sqlite3.connect(db) as p:
                row = p.execute("SELECT period_start, period_end FROM run_meta").fetchone()
        except sqlite3.Error:
            continue
        if row and tuple(row) == (start, end):
            return d.name
    return None


def repriced_task_ids(c) -> set[str]:
    """Tasks whose stored estimate the RUN replaced, not the estimator produced.

    Decision pricing (PR #79) sets a standalone decision's `est_human_hours` to
    the user's measured read-and-reply time, so the number beside the rationale is
    no longer the rationale's conclusion and never will be. Any check that
    weighs an estimate against its own rationale, or against the same task in an
    earlier run, has to drop these exactly as it already drops the tasks he
    reviewed himself. It did not, and that was the entire flagged population:
    71 of 71 items across three checks and three era weeks.

    Empty for a run written before the table existed, which is the right answer
    — those runs carry no corrections to exempt.
    """
    try:
        return {row[0] for row in c.execute(
            "SELECT DISTINCT task_id FROM estimate_corrections")}
    except sqlite3.OperationalError:
        return set()


def estimate_drift(c, prev_db: Path) -> list[tuple[str, float, float]]:
    """(task, previous est, est) for tasks whose events are unchanged since the
    previous run (same id, event count within 10%) but whose estimate moved >2x.

    A task repriced by EITHER run is skipped: the drift is the correction
    landing, and a correction is the thing this check exists to let through.
    """
    with sqlite3.connect(prev_db) as p:
        before = {t: (n, e) for t, n, e in p.execute(
            "SELECT t.task_id, t.event_count, e.est_human_hours FROM tasks t JOIN enriched e USING(task_id)")}
        skip = repriced_task_ids(p)
    skip |= repriced_task_ids(c)
    out = []
    for t, n, e in c.execute(
            "SELECT t.task_id, t.event_count, e.est_human_hours FROM tasks t JOIN enriched e USING(task_id)"):
        if t not in before or t in skip:
            continue
        n0, e0 = before[t]
        if not n0 or abs(n - n0) > 0.1 * n0 or not e0 or not e:
            continue
        if max(e, e0) > DRIFT_RATIO * min(e, e0):
            out.append((t, e0, e))
    return sorted(out, key=lambda r: -max(r[1], r[2]))


def _demoted_placements(c) -> set[tuple[str, str]]:
    """(task_id, artifact identity) pairs the credit pass demoted to context-only.

    `artifact_credit` holds one row per contested identity: the owner task, and
    the tasks whose placement of it was demoted. Enrichment renders a demoted
    placement as "CREDITED TO ANOTHER TASK — do not count", so any audit that
    compares an estimate against a diffstat has to drop the same rows, or it is
    pricing artifacts the estimator was told to ignore.
    """
    import json as _json
    out: set[tuple[str, str]] = set()
    try:
        rows = c.execute(
            "SELECT identity, duplicate_task_ids_json FROM artifact_credit").fetchall()
    except sqlite3.OperationalError:
        return out  # a run written before the credit table existed
    for ident, dups in rows:
        for tid in _json.loads(dups or "[]"):
            out.add((tid, ident))
    return out


def loc_estimate_mismatches(c) -> tuple[int, list[tuple[str, int, float, float]], int]:
    """Code tasks whose estimate is far off one day per 1000 lines added.

    The two sides are judged against different rates and the reason is in
    `LOC_PER_DAY_OBSERVED`: above, his 1000; below, the estimator's own observed
    rate, because he has accepted the conservative bias and not the bug it
    hides. `loc_h` in the returned rows is always the 1000-line figure, so the
    report line and the worksheet keep comparing against the rule he set.

    Lines come from the task's PRs, each (repo, number) counted once because
    opened and merged both carry the diffstat; commits count only when the task
    has no PR. Two further rules decide which of a task's PRs it may be priced
    against, because the comparison presumes the task produced the diff:

    - **A merged PR wins over an unmerged one.** A task holding both #13 (opened,
      abandoned, 1,718 lines) and #14 (opened and merged, 828 lines, same title)
      is holding one change under two numbers; the estimator priced 828 and the
      check summed 2,546. Mirrors the commits-only-without-a-PR rule above.
    - **A PR the task only saw merged is not priced against it** unless the task
      also holds a commit. Opening it or committing to it is participation;
      watching it land is not. Two such holdings exist across the three era
      weeks and both are rollup PRs — one is 11,489 lines titled "all past
      measurement work" against a task with 0.66h of wall clock.

    Neither rule loosens a threshold; both say which artifact the rule can speak
    for. Together they take the flags on the era weeks from 1/2/6 to 0/0/1.

    **Artifacts the credit pass gave to another task are skipped**:
    the estimate on the other side of the comparison was made with those
    explicitly excluded, so counting them compares two different denominators —
    and the difference only ever points one way, which is why every flag on the
    three era weeks of 2026-09 read "estimate too low" and none read too high.
    Diffs the rule cannot speak for are skipped too: too small, too large to be
    typed, generated-looking (lines per file), or a sweep where deletions nearly
    match additions. Returns (tasks checked, flagged rows of
    (task_id, lines, est_h, loc_h), tasks estimated under his 1000/day rule).

    That last count is reported even when nothing is flagged. The bias it
    measures is one he accepted, and an accepted bias that stops being counted
    is one nobody can notice changing.
    """
    import json as _json
    from .artifact_credit import identity_from_parts
    typ = {t: (e, ty) for t, e, ty in c.execute("SELECT task_id, est_human_hours, type FROM enriched")}
    demoted = _demoted_placements(c)
    prs: dict[str, dict] = defaultdict(dict)
    pr_merged: dict[str, set] = defaultdict(set)
    pr_opened: dict[str, set] = defaultdict(set)
    commits: dict[str, list] = defaultdict(lambda: [0, 0, 0])
    for tid, kind, md, snip, proj in c.execute(
            """SELECT te.task_id, ev.kind, ev.metadata_json, ev.content_snippet, ev.project
               FROM task_events te JOIN events ev USING(event_id)
               WHERE ev.kind IN ('pr_merged', 'pr_opened', 'commit')"""):
        if typ.get(tid, (0, ""))[1] != "code":
            continue
        m = _json.loads(md or "{}")
        if (tid, identity_from_parts(kind, m, snip or "", proj or "")) in demoted:
            continue
        if kind == "commit":
            x = commits[tid]
            x[0] += m.get("insertions") or 0
            x[1] += m.get("deletions") or 0
            x[2] += m.get("files_changed") or 0
        elif m.get("additions") is not None:
            key = (m.get("repo"), m.get("number"))
            prs[tid][key] = (m["additions"], m.get("deletions") or 0,
                             m.get("changedFiles") or 0)
            (pr_merged if kind == "pr_merged" else pr_opened)[tid].add(key)
    for tid, held in prs.items():
        if pr_merged[tid]:                      # a superseded attempt is not a second change
            held = {k: v for k, v in held.items() if k in pr_merged[tid]}
        if not commits.get(tid):                # watching a PR land is not producing it
            held = {k: v for k, v in held.items() if k in pr_opened[tid]}
        prs[tid] = held
    checked, flagged, under = 0, [], 0
    for tid in set(prs) | set(commits):
        rows = list(prs[tid].values()) if prs.get(tid) else [tuple(commits[tid])]
        added = sum(r[0] for r in rows)
        deleted = sum(r[1] for r in rows)
        files = sum(r[2] for r in rows)
        if not LOC_MIN_LINES <= added <= LOC_MAX_LINES:
            continue
        if files and added / files > LOC_MAX_LINES_PER_FILE:
            continue
        if deleted >= LOC_SWEEP_DELETE_SHARE * added:
            continue
        checked += 1
        est = typ[tid][0]
        loc_h = added / LOC_PER_DAY * DAY_H
        floor_h = added / LOC_PER_DAY_OBSERVED * DAY_H / LOC_TOLERANCE
        under += est < loc_h
        if est < floor_h or est > loc_h * LOC_TOLERANCE:
            flagged.append((tid, added, est, loc_h))
    flagged.sort(key=lambda r: -max(r[2] / r[3], r[3] / max(r[2], 0.1)))
    return checked, flagged, under


_PULL_URL = re.compile(r"github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")


def pr_mentions_missing(c, fetched_repos=()) -> list[tuple[str, int, int]]:
    """Repos whose conversations link far more PRs than the run ingested.

    Network-free cross-check on the PR fetch: agents and the user paste PR URLs as
    they work, so a repo whose transcripts link hundreds of PRs the run never
    ingested lost them upstream. It would have caught the newest-200 cap, which
    kept 198 claude-workspaces-plugin PRs against 770 linked. Returns
    (repo, linked, linked but not ingested) for flagged repos.

    A repo is in scope when the run fetched it (`fetched_repos`, from
    pr_fetch.json) or ingested any PR from it. The fetched list matters: a fetch
    that lost every PR in the window leaves no ingested row to find the repo by.
    """
    ingested: dict[str, set[int]] = defaultdict(set)
    for sid, num in c.execute(
            """SELECT session_id, json_extract(metadata_json, '$.number') FROM events
               WHERE kind IN ('pr_opened', 'pr_merged')"""):
        if num is not None and ":" in sid:
            ingested[sid.split(":", 1)[1].lower()].add(int(num))
    linked: dict[str, set[int]] = defaultdict(set)
    for (snip,) in c.execute(
            """SELECT content_snippet FROM events
               WHERE actor IN ('agent', 'user') AND content_snippet LIKE '%/pull/%'"""):
        for repo, n in _PULL_URL.findall(snip or ""):
            linked[repo.lower()].add(int(n))
    scope = set(ingested) | {r.lower() for r in fetched_repos}
    out = []
    for repo, nums in linked.items():
        if repo not in scope or len(nums) < PR_MENTION_MIN:
            continue  # a repo the run does not fetch is out of scope here
        missing = len(nums - ingested[repo])
        if missing > PR_MENTION_MISSING_SHARE * len(nums):
            out.append((repo, len(nums), missing))
    return sorted(out, key=lambda r: -r[2])


def pr_fetch_problems(run_path: Path) -> list[str] | None:
    """Repos whose PR fetch failed or hit the search cap; None if unrecorded."""
    f = run_path / "pr_fetch.json"
    if not f.exists():
        return None
    return [
        f"{r.get('repo')}: " + ("fetch failed" if r.get("failed") else
                                f"{len(r.get('truncated') or [])} range(s) at the result cap")
        for r in json.loads(f.read_text())
        if r.get("failed") or r.get("truncated")
    ]


def _first_record_ts(p: Path, lines: int = 64) -> str | None:
    best = None
    try:
        with p.open("rb") as f:
            for i, line in enumerate(f):
                if i >= lines:
                    break
                try:
                    ts = json.loads(line).get("timestamp")
                except (json.JSONDecodeError, AttributeError):
                    continue
                if ts and (best is None or ts < best):
                    best = ts
    except OSError:
        return None
    return best


def overwritten_sessions(mirror: Path, history: Path, start: str, end: str) -> dict:
    """{session: (snapshot, lo, hi, records)} for each top-level transcript
    whose history snapshot begins more than an hour before every current mirror
    copy does, where the records the mirror lost, [snapshot start, current
    start), overlap the window [start, end] (dates, end inclusive). `records`
    counts the snapshot's user/assistant records inside that overlap."""
    snaps: dict[str, list] = defaultdict(list)
    for p in history.glob("*/*/*.jsonl"):
        snaps[p.stem].append(p)
    current: dict[str, str] = {}
    for p in mirror.glob("*/*.jsonl"):
        if p.stem in snaps:
            ts = _first_record_ts(p)
            if ts and (p.stem not in current or ts < current[p.stem]):
                current[p.stem] = ts
    out = {}
    end_excl = f"{end}T23:59:60"
    for sid, paths in snaps.items():
        cur = current.get(sid)
        if not cur:
            continue
        older = [(p, ts) for p in paths if (ts := _first_record_ts(p)) and ts[:13] < cur[:13]]
        if not older:
            continue
        snap, snap_ts = max(older, key=lambda x: x[0].stat().st_size)
        lo, hi = max(snap_ts, start), min(cur, end_excl)
        if lo >= hi:
            continue
        records = 0
        with snap.open("rb") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("type") in ("user", "assistant") and lo <= (r.get("timestamp") or "") < hi:
                    records += 1
        out[sid] = (snap.parent.parent.name, lo, hi, records)
    return out


def run_checks(run_id: str, avail_h: float | None, out=None) -> int:
    """Run all audits; print PASS/REVIEW lines, write audits.json to the run
    dir, return the number of REVIEW findings. Auto-runs after every pipeline
    pass (the user 2026-07-09: "run these audits after every single pass")."""
    import sys
    out = out or sys.stdout
    results: list[dict] = []

    def report(name: str, ok: bool, detail: str = "", items: list | None = None,
               skipped: bool = False) -> None:
        # A check that did not run is reported as SKIP, never PASS. Reporting it
        # as PASS makes an absent input read as a clean result, which is how a
        # check nobody is running goes on looking healthy for months.
        status = "SKIP" if skipped else ("PASS" if ok else "REVIEW")
        results.append({"name": name, "status": status, "detail": detail, "items": (items or [])[:20]})
        line = f"{len(results)}. {name}: {status}" + (f" — {detail}" if detail else "")
        print(line, file=out)
        for it in (items or [])[:6]:
            print(f"     {it}", file=out)

    rp = DATA / run_id
    c = sqlite3.connect(str(rp / "run.sqlite")); c.row_factory = sqlite3.Row
    period = c.execute("SELECT period_start, period_end FROM run_meta").fetchone()
    print(f"# Data-quality audits — {run_id} ({period['period_start']} → {period['period_end']})\n", file=out)

    # CHECK 1 — no event attributed to >1 task
    dups = c.execute("SELECT event_id FROM task_events GROUP BY event_id HAVING COUNT(DISTINCT task_id) > 1").fetchall()
    report("event double-attribution (each event in ≤1 task)", not dups, f"{len(dups)} events in >1 task" if dups else "")

    # CHECK 2 — one worker had more than two tasks in hand at the same time
    flagged = lanes_working_many_tasks(c)
    report("≤2 tasks worked at once in one lane", not flagged,
           f"{len(flagged)} lanes exceed (window {CONCURRENCY_WINDOW_SEC // 60}min)"
           if flagged else f"window {CONCURRENCY_WINDOW_SEC // 60}min",
           [f"{sid[:16]}/{lane}: max {mx}" for sid, lane, mx in flagged])

    # CHECK 3 — weekly hands-on sums within 25% of stated availability
    total_on = c.execute("SELECT SUM(active_hours) FROM task_metrics").fetchone()[0] or 0
    if avail_h:
        off = abs(total_on - avail_h) / avail_h
        report("hands-on vs stated availability (±25%)", off <= 0.25,
               f"Σ={total_on:.1f}h vs stated {avail_h}h (off {off*100:.0f}%)")
    else:
        report("hands-on vs stated availability", True, f"Σ={total_on:.1f}h (no --avail-h given; informational)")

    # CHECK 4 — labor >= hands_on per task (union ⊇ component). labor_hours was
    # the column called wall_clock_hours before schema v2 (2026-09-18).
    cols = {x[1] for x in c.execute("PRAGMA table_info(task_metrics)")}
    labor = _labor_col(c)
    if labor != "NULL":
        # TRUE BY CONSTRUCTION since 2026-09-18: cli.py passes the hands-on
        # intervals into labor_and_agent_hours_by_task, which unions them in, so
        # `labor < hands-on` cannot happen and a PASS here is not evidence of
        # anything. Reported as SKIP so it stops counting toward the PASS total.
        #
        # The obvious dual (`labor ≤ hands-on + agent`) is NOT the replacement:
        # labor unions the full presence intervals while active_hours stores the
        # block-rule-trimmed figure, so it fires on 34 correct tasks in
        # run-20260928T082502Z. A falsifiable union invariant needs the
        # components labor is actually built from, and none of them is stored.
        n_rows = c.execute("SELECT COUNT(*) FROM task_metrics").fetchone()[0]
        report("labor ≥ hands-on invariant", True,
               f"structural — labor unions hands-on in, so this cannot fail "
               f"({n_rows} tasks unchecked)", skipped=True)
    else:
        report("labor ≥ hands-on invariant", True, "old schema", skipped=True)

    # CHECK 5 — est_h double-count detector (the user 2026-06-12)
    # Per epic, flag when BOTH the conversation-view and artifact-view est_h are
    # substantial (>5h) — that's the signature of the same work estimated twice.
    epics_path = rp / "epics.json"
    if epics_path.exists():
        epics = json.loads(epics_path.read_text())
        est = {r["task_id"]: (r["est_human_hours"] or 0) for r in c.execute("SELECT task_id, est_human_hours FROM enriched")}
        um = {r["task_id"]: (r["user_msg_count"] or 0) for r in c.execute("SELECT task_id, user_msg_count FROM tasks")}
        dc = []
        naive_total = dedup_total = 0.0
        for e in epics:
            mem = [m for m in e["member_task_ids"] if m in est and est[m] > 0]
            if not mem: continue
            C = [m for m in mem if um.get(m, 0) > 0]
            A = [m for m in mem if um.get(m, 0) == 0 and _has_artifact(c, m)]
            other = [m for m in mem if m not in C and m not in A]
            sumC = sum(est[m] for m in C); sumA = sum(est[m] for m in A); sumO = sum(est[m] for m in other)
            naive = sumC + sumA + sumO
            dedup = (max(sumC, sumA) + sumO) if (sumC > 0 and sumA > 0) else naive
            naive_total += naive; dedup_total += dedup
            if sumC > 5 and sumA > 5:
                dc.append((e["title"][:42], naive, dedup, sumC, sumA))
        removed = naive_total - dedup_total
        report("est_h double-count across conv/artifact views", not dc,
               f"{len(dc)} epics, {removed:.0f}h / {100*removed/naive_total:.0f}% double-counted" if dc else "",
               [f"{t}: {nv:.0f}h → {dd:.0f}h (conv {sc:.0f}, artifact {sa:.0f})"
                for t, nv, dd, sc, sa in sorted(dc, key=lambda x: -(x[1]-x[2]))])
    else:
        report("est_h double-count across conv/artifact views", True,
               "no epics.json — check did not run", skipped=True)

    # CHECK 6 — orphaned PRs (merged in window, no task attached)
    orphans = c.execute("""SELECT ev.event_id FROM events ev
                           LEFT JOIN task_events te ON te.event_id = ev.event_id
                           WHERE ev.kind = 'pr_merged' AND te.task_id IS NULL""").fetchall()
    report("no orphaned merged-PRs", not orphans, f"{len(orphans)} unattached" if orphans else "")

    # CHECK 7 — tail-share: % of est_h in zero-user-message tasks (attribution-suspect)
    rows = c.execute("""SELECT e.est_human_hours est, t.user_msg_count um
                        FROM tasks t JOIN enriched e USING(task_id) WHERE e.est_human_hours IS NOT NULL""").fetchall()
    tot = sum(r["est"] for r in rows) or 1
    zero = sum(r["est"] for r in rows if (r["um"] or 0) == 0)
    pct = 100 * zero / tot
    report("est_h share in 0-user-msg tasks < 60% (delegated work is legit)", pct < 60, f"{pct:.0f}%")

    # --- the user's audits (2026-07-09) + bug-derived checks ---

    # B1 — >95% of events assigned to tasks (per the user; catches orphaned work
    # like the Client A delegated-build gap where 85% of agent events dropped)
    # ignore_task projects (eval harnesses): their orphaned events are expected
    # — exclude from the denominator so real attribution gaps stay visible.
    try:
        from .config import load_projects
        ignored = [pr.slug for pr in load_projects().projects if getattr(pr, "ignore_task", False)]
    except Exception:
        ignored = []
    tot_ev = c.execute("SELECT COUNT(*) FROM events").fetchone()[0] or 1
    att_ev = c.execute("SELECT COUNT(DISTINCT event_id) FROM task_events").fetchone()[0]
    ign_orph = 0
    if ignored:
        qm = ",".join("?" * len(ignored))
        ign_orph = c.execute(
            f"""SELECT COUNT(*) FROM events ev LEFT JOIN task_events te ON te.event_id=ev.event_id
                WHERE te.task_id IS NULL AND ev.project IN ({qm})""", ignored).fetchone()[0]
    denom = max(tot_ev - ign_orph, 1)
    pct_att = 100 * att_ev / denom
    worst = c.execute(f"""SELECT ev.project, COUNT(*) n FROM events ev
                         LEFT JOIN task_events te ON te.event_id=ev.event_id
                         WHERE te.task_id IS NULL{' AND ev.project NOT IN (' + qm + ')' if ignored else ''}
                         GROUP BY ev.project ORDER BY n DESC LIMIT 3""", ignored).fetchall()
    note = f" ({ign_orph} expected harness orphans excluded: {', '.join(ignored)})" if ign_orph else ""
    report(">95% of events assigned to tasks", pct_att > 95,
           f"{pct_att:.1f}% attached ({denom - att_ev} orphaned){note}",
           [f"{r['project']}: {r['n']} orphaned" for r in worst])

    # B2 — realistic parallelism per PROJECT at any moment. The limit is dated,
    # not tuned (the user, 2026-09-15): 3 through July 2026, 25 from August, when
    # workspaces made 4+ tasks on one project ordinary. Each window answers to
    # the limit in force when it happened, so the line names the limit it used.
    proj_flags = projects_working_many_tasks(c)
    eras = " / ".join([f"{PROJECT_CONCURRENCY_LIMIT} before {PROJECT_CONCURRENCY_ERAS[0][0]}"]
                      + [f"{v} from {s}" for s, v in PROJECT_CONCURRENCY_ERAS])
    report(f"concurrent tasks per project within the era limit ({eras})", not proj_flags,
           f"{len(proj_flags)} projects exceed" if proj_flags else "",
           [f"{pj}: max {mx} concurrent against a limit of {lim}" for pj, mx, lim in proj_flags])

    # B3 — one piece of work, one task.
    dup_pairs = duplicate_work_pairs(c)
    report("no duplicate-work task pairs (a lane alternating + shared refs/title)", not dup_pairs,
           f"{len(dup_pairs)} suspect pairs" if dup_pairs else "", dup_pairs)

    # B4 — est vs the LLM's own rationale agree within 3x (caught the stale-80h
    # override: enriched est said 80h, rationale said ~1.5h)
    mismatches = []
    reviewed_ids = set()
    for _db in DATA.glob("run-*/run.sqlite"):  # verdicts live in the run where they were recorded
        try:
            _c2 = sqlite3.connect(str(_db))
            reviewed_ids |= {row[0] for row in _c2.execute(
                "SELECT DISTINCT task_id FROM estimate_reviews WHERE bryan_est_hours IS NOT NULL")}
            _c2.close()
        except sqlite3.DatabaseError:
            continue
    repriced = repriced_task_ids(c)
    mild = []
    for r in c.execute("SELECT task_id, est_human_hours est, rationale FROM enriched WHERE rationale IS NOT NULL AND est_human_hours > 0"):
        if r["task_id"] in reviewed_ids:
            continue  # the user's correction legitimately overrides the rationale
        if r["task_id"] in repriced:
            continue  # so does the run's own correction — same reason
        concl = rationale_conclusion(r["rationale"])
        if not concl: continue
        ratio = max(r["est"], concl) / min(r["est"], concl)
        if ratio >= 3:
            mismatches.append(f"{r['task_id'][:10]}: est {r['est']}h vs rationale ~{concl}h")
        elif ratio >= 2:
            # 2026-07 audit: divergences under the 3x bar existed and read as
            # real drift, not rounding — surface them on their own line.
            mild.append(f"{r['task_id'][:10]}: est {r['est']}h vs rationale ~{concl}h ({ratio:.1f}x)")
    report("est agrees with its own rationale (within 3x)", not mismatches,
           f"{len(mismatches)} internally-inconsistent estimates" if mismatches else "", mismatches)
    report("est/rationale mild drift (2-3x)", not mild,
           f"{len(mild)} mildly-divergent estimates" if mild else "", mild)

    # B4-0 — informational, and it prints every run whether or not it bites.
    # Three checks above skip these tasks. An exemption nobody can see is an
    # exemption nobody can check, which is how "don't tune the checkers to be
    # lenient" gets violated by accident rather than by decision. The line says
    # how many estimates the run replaced and what it did to their total, so a
    # reader can tell a correction that priced 34 decisions from one that
    # quietly swallowed the week.
    corr = c.execute(
        "SELECT kind, COUNT(*) n, SUM(before_h) b, SUM(after_h) a "
        "FROM estimate_corrections GROUP BY kind").fetchall() \
        if repriced else []
    report("estimates the run replaced (exempt from the three checks above)", True,
           "; ".join(f"{r['kind']}: {r['n']} tasks, Σest {r['b']:.2f}h → {r['a']:.2f}h"
                     for r in corr) or "none")

    # B4a — reviewed override far ABOVE the LLM's own rationale. A legitimate
    # The user correction is almost always DOWNWARD (the LLM runs hot); an applied
    # override ≥3x above what the rationale itself concluded is the stale-echo
    # signature (Apr 27 audit: 3 of 4 pre-filled ✅ tasks stored 60-80h against
    # rationales saying 2-6h). One-sided on purpose — downward overrides are
    # the normal case and must not flag.
    high_over = []
    for r in c.execute("SELECT task_id, est_human_hours est, rationale FROM enriched WHERE rationale IS NOT NULL AND est_human_hours > 0"):
        if r["task_id"] not in reviewed_ids:
            continue
        concl = rationale_conclusion(r["rationale"])
        if concl and r["est"] / concl >= 3:
            high_over.append(f"{r['task_id'][:10]}: applied override {r['est']}h vs LLM rationale ~{concl}h")
    report("no reviewed override ≥3x ABOVE the LLM's own rationale (stale-echo signature)",
           not high_over, f"{len(high_over)} suspect override(s)" if high_over else "", high_over)

    # B4b — review-override echo suspects. The ✅-re-parse failure mode records
    # a LATER "agreement" verdict (bryan_est == llm_est) that clobbers an
    # EARLIER human correction via latest-wins (429aa5d582: a 6h correction
    # flipped back to 80h this way). No automated writer exists for reviews, so
    # the backstop lives here: flag any task whose WINNING review is an
    # agreement while an earlier review corrected to a different number.
    echo = []
    revs_by_task: dict[str, list] = {}
    for _db in DATA.glob("run-*/run.sqlite"):
        try:
            _c3 = sqlite3.connect(str(_db)); _c3.row_factory = sqlite3.Row
            for row in _c3.execute(
                "SELECT task_id, reviewed_at, llm_est_hours, bryan_est_hours "
                "FROM estimate_reviews WHERE bryan_est_hours IS NOT NULL"):
                revs_by_task.setdefault(row["task_id"], []).append(
                    (row["reviewed_at"] or "", row["llm_est_hours"], row["bryan_est_hours"]))
            _c3.close()
        except sqlite3.DatabaseError:
            continue
    for tid, revs in revs_by_task.items():
        if len(revs) < 2:
            continue
        revs.sort()
        _, llm_last, bry_last = revs[-1]
        if llm_last is None or abs(bry_last - llm_last) > 1e-6:
            continue  # winning review is itself a correction — fine
        clobbered = [b for _, l, b in revs[:-1]
                     if l is not None and abs(b - l) > 1e-6 and abs(b - bry_last) > 1e-6]
        if clobbered:
            echo.append(f"{tid[:10]}: latest 'agreement' {bry_last}h overrides earlier correction {clobbered[-1]}h")
    report("no review-override echo suspects (agreement clobbering a correction)", not echo,
           f"{len(echo)} suspect override(s)" if echo else "", echo)

    # B4c — est below the task's OWN measured hands-on. est_human_hours is what
    # an expert would need WITHOUT AI; hands-on is what the user actually typed
    # WITH it. est < hands-on means leverage < 1 — the agent made him slower —
    # which is possible but rare enough that it usually means the estimate was
    # pushed down wrongly. Introduced after workstream disclosure (2026-07-28)
    # over-corrected three Apr-13 tasks below their own keyboard time.
    # A flag, never a silent floor: adjusting the number invisibly is exactly
    # the opaque-correction pattern these metrics are supposed to avoid.
    # The count alone cannot tell a re-run of the 2026-07-28 over-correction
    # (a day of work priced under its own keyboard time) from the shape that
    # actually turns up: a steering task, where the estimator prices the
    # artifact and drops the deciding, so 15 review sessions and an 8-line
    # rename score est 0.1h against 0.95h of presence. Both are "N tasks"; one
    # is hours and one is minutes. So report the gap in hours beside the count.
    below_handson, below_gap_h = [], 0.0
    for r in c.execute(
        """SELECT e.task_id, e.est_human_hours est, m.active_hours ho
           FROM enriched e JOIN task_metrics m USING(task_id)
           WHERE e.est_human_hours > 0 AND m.active_hours > 0
             AND e.est_human_hours < m.active_hours
           ORDER BY (m.active_hours - e.est_human_hours) DESC"""):
        below_gap_h += r["ho"] - r["est"]
        below_handson.append(
            f"{r['task_id'][:10]}: est {r['est']:g}h < hands-on {r['ho']:.2f}h (leverage {r['est']/r['ho']:.2f}×)")
    report("no task estimated below its own measured hands-on time", not below_handson,
           f"{len(below_handson)} sub-unity-leverage tasks, {below_gap_h:.2f}h below in total"
           if below_handson else "",
           below_handson)

    # B4d — every substantive task carries an estimate. A task that failed
    # enrichment silently vanishes from all totals, so a run-over-run
    # comparison scores the LOSS as an improvement (2026-07-28: a 28h task
    # dropped on a JSON parse error and accounted for ~40% of that week's
    # apparent drop). Substantive = >20 events OR holds a shipped artifact:
    # that 28h task had only 19 events, because artifact shards are event-thin
    # and estimate-heavy — event count alone is the wrong proxy.
    missing_est = [
        f"{r['task_id'][:10]}: {r['event_count']} events"
        f"{', HOLDS ARTIFACT' if r['n_art'] else ''}, no estimate — {(r['title'] or '')[:50]}"
        for r in c.execute(
            """SELECT t.task_id, t.event_count, t.title,
                      (SELECT COUNT(*) FROM task_events te JOIN events ev USING(event_id)
                       WHERE te.task_id = t.task_id
                         AND ev.kind IN ('pr_merged','pr_opened','commit')) AS n_art
               FROM tasks t LEFT JOIN enriched e ON e.task_id = t.task_id
               WHERE e.task_id IS NULL AND (t.event_count > 20 OR n_art > 0)
               ORDER BY n_art DESC, t.event_count DESC""")
    ]
    report("every substantive task has an estimate", not missing_est,
           f"{len(missing_est)} task(s) missing enrichment" if missing_est else "",
           missing_est)

    # B5 — delegated invariant: delegated tasks have zero user messages
    tcols = {x[1] for x in c.execute("PRAGMA table_info(tasks)")}
    if "delegated" in tcols:
        bad_d = c.execute("SELECT task_id FROM tasks WHERE delegated=1 AND user_msg_count > 0").fetchall()
        report("delegated tasks carry no user messages", not bad_d,
               f"{len(bad_d)} delegated tasks have user msgs" if bad_d else "")

    # B6 — timestamp sanity: first_ts <= last_ts everywhere (tz-sort bug class)
    bad_ts = c.execute("SELECT task_id FROM tasks WHERE first_ts > last_ts").fetchall()
    report("task timestamps ordered (first ≤ last)", not bad_ts,
           f"{len(bad_ts)} inverted" if bad_ts else "")

    # B7 — excluded projects leak no events (exclude: true drops at extraction)
    try:
        from .config import load_projects
        excluded = [pr.slug for pr in load_projects().projects if pr.exclude]
        if excluded:
            qmarks = ",".join("?" * len(excluded))
            n_leak = c.execute(f"SELECT COUNT(*) FROM events WHERE project IN ({qmarks})", excluded).fetchone()[0]
            report("excluded projects leak no events", n_leak == 0,
                   f"{n_leak} events from excluded projects" if n_leak else "")
    except Exception as exc:
        # Previously `pass`: any failure here reported as no finding, which is
        # indistinguishable from the check passing.
        report("excluded projects leak no events", True,
               f"could not run: {type(exc).__name__}: {exc}"[:200], skipped=True)

    # B8 — tasks with an estimate but zero wall-clock (the artifact-fragment
    # bug class: split-off commits with no time attached). Any estimate, not
    # only ≥1h: "anything with wall clock of 0 is wrong" (the user 2026-09-14).
    zero_wall_rows = zero_wall_tasks(c)
    zero_wall = [f"{t[:10]}: est {'?' if e is None else f'{e:g}h'}, wall 0 — {(title or '')[:50]}"
                 for t, e, title in zero_wall_rows]
    report("no estimated task with zero wall-clock", not zero_wall,
           f"{len(zero_wall)} time-less estimated tasks, {_est_sum([(t, e) for t, e, _ in zero_wall_rows])}"
           if zero_wall else "",
           zero_wall)

    # B8b — zero hands-on is legitimate only when an agent was triggered and
    # finished without him. A split or an artifact-only fragment means his time
    # sits on another task and this task's multiplier is infinite.
    zh = zero_hands_on_suspects(c)
    for key, label in (("split", "no split tasks (his ask on one task, the work on another)"),
                       ("no_conversation", "no estimated fragment without a conversation"),
                       ("own_messages", "no task holding his messages with zero hands-on")):
        rows = zh[key]
        report(label, not rows,
               f"{len(rows)} tasks under {NEAR_ZERO_HANDS_ON_H * 60:.0f} min hands-on, {_est_sum(rows)}" if rows else "",
               _est_items(rows))
    exempt = zh["agent_only"]
    report("agent-created board tasks with no comment, dictation or review item from him (legitimately zero)",
           True, f"{len(exempt)} tasks, {_est_sum(exempt)}", _est_items(exempt))
    started = zh["agent_triggered"]
    report("work with no board task that only peers, board notices or schedules started (legitimately zero)",
           True, f"{len(started)} tasks, {_est_sum(started)}", _est_items(started))
    answered = zh["asks_here"]
    report("tasks holding every ask he made on them, each scored or from an earlier week (legitimately small)",
           True, f"{len(answered)} tasks, {_est_sum(answered)}", _est_items(answered))

    # B8j — one measured task per board task, and every event accounted for
    # (the user, 2026-09-14). Limits are his one-week impact limit.
    bta = board_task_audit(c)
    bad_map = sorted((bt, n) for bt, n in bta["mapping"].items() if n != 1)
    report("every board task with work maps to exactly one measured task", not bad_map,
           # "0 of 236 board tasks" reads as none of them mapped. Say what the
           # number counts, because a PASS line nobody can parse is not a PASS.
           f"{len(bad_map)} of {len(bta['mapping'])} board tasks map to the wrong number of measured tasks; "
           f"{len(bta['housekeeping_only'])} housekeeping-only board tasks get none",
           [f"{bt}: {n} measured tasks" for bt, n in bad_map])
    mis = bta["misplaced"]
    mis_tasks = {h for _, _, h, _ in mis if h}
    mis_h = sum(h for *_, h in mis)
    mis_by = defaultdict(lambda: [0, 0.0])
    for _, bt, h_task, h in mis:
        mis_by[(bt, h_task)][0] += 1
        mis_by[(bt, h_task)][1] += h
    report(f"events naming a board task sit on its measured task (≤{IMPACT_TASKS_WEEK} tasks, ≤{IMPACT_HOURS_WEEK:g}h elsewhere)",
           len(mis_tasks) <= IMPACT_TASKS_WEEK and mis_h <= IMPACT_HOURS_WEEK,
           f"{len(mis)} events on {len(mis_tasks)} other tasks or none, {mis_h:.2f}h",
           [f"{bt} → {(t or 'no task')[:10]}: {n} events, {h:.2f}h"
            for (bt, t), (n, h) in sorted(mis_by.items(), key=lambda kv: (-kv[1][1], -kv[1][0]))])
    un = bta["unassigned"]
    un_h = sum(h for _, h in un.values())
    report(f"events on no task carry ≤{IMPACT_HOURS_WEEK:g}h", un_h <= IMPACT_HOURS_WEEK,
           f"{sum(n for n, _ in un.values())} events, {un_h:.2f}h",
           [f"{s}/{k}: {n} events, {h:.2f}h" for (s, k), (n, h) in sorted(un.items(), key=lambda kv: (-kv[1][1], -kv[1][0]))])

    # B8c — one day per 1000 lines added, for code tasks (the user 2026-09-14),
    # judged asymmetrically since 2026-09-15 (see LOC_PER_DAY_OBSERVED). The
    # count of tasks sitting under his rule is printed even when none is
    # flagged: the bias he accepted stays measurable rather than disappearing
    # into a PASS, so nobody later reads a green line as "no conservatism here".
    loc_checked, loc_off, loc_under = loc_estimate_mismatches(c)
    report(f"code estimates within {LOC_TOLERANCE:g}x of one day per 1000 lines added "
           f"(under-estimates judged against the observed {LOC_PER_DAY_OBSERVED}/day)", not loc_off,
           f"{len(loc_off)} of {loc_checked} checked code tasks off by >{LOC_TOLERANCE:g}x"
           f"; {loc_under} under his 1000/day rule (accepted as conservative)" if loc_off
           else f"{loc_checked} checked; {loc_under} under his 1000/day rule (accepted as conservative)",
           [f"{t[:10]}: {n} lines → {lh:.0f}h by LoC, est {e:g}h" for t, n, e, lh in loc_off])

    # B8d — labor far above the estimate usually means the task boundary
    # swallowed a long orchestrator session, not that the work was slow.
    boundary = [f"{r[0][:10]}: labor {r[2]:.1f}h vs est {r[1]:g}h — {(r[3] or '')[:50]}" for r in c.execute(
        f"""SELECT e.task_id, e.est_human_hours, {labor}, e.title
           FROM enriched e JOIN task_metrics m USING(task_id)
           WHERE {labor} >= ? AND {labor} > ? * e.est_human_hours
           ORDER BY {labor} DESC""", (BOUNDARY_WALL_H, BOUNDARY_RATIO))]
    report(f"no task with labor ≥{BOUNDARY_WALL_H}h and >{BOUNDARY_RATIO}x its estimate (boundary suspect)",
           not boundary, f"{len(boundary)} tasks" if boundary else "", boundary)

    # B8e — one task too big for one boundary: a board lead's week-long sprint
    # became one task titled for a single PR review (45d47f240b, 80k events).
    big = oversized_tasks(c)
    report(f"no task spanning >{OVERSIZE_DAYS} days of agent work or >{OVERSIZE_LANES} agent lanes",
           not big, f"{len(big)} tasks, Σest {sum(r[1] or 0 for r in big):.0f}h" if big else "",
           [f"{t[:10]}: {d} days, {l} lanes, est {e}h" for t, e, d, l in big])

    # B8f — an agent woken by board traffic it does not own, answering that it
    # is doing nothing. Bursts join events <30 min apart, so fifteen of those
    # across 7.3h used to read as hours of work (7d441259fd, 1.40h of wall clock
    # on a 0.25h task). `effort.is_declined` now makes such a reply an instant,
    # so it no longer creates duration: that task's wall clock fell to 0.40h and
    # 26e3e48981's to 0.54h, and the two weeks lost 0.99h and 0.42h in total.
    #
    # The check stays, and it is no longer reporting mis-scored time: it reports
    # that an agent was woken repeatedly by work that was not its own, which is
    # worth seeing and is not something the scorer can fix. The old
    # minute-density form divided two different time bases; see
    # polling_held_wall for why it went and what reading its eleven flags found.
    polled = polling_held_wall(c)
    report("no agent repeatedly woken by board traffic it does not own",
           not polled, f"{len(polled)} tasks" if polled else "",
           [f"{t[:10]}: {n} declined wake-ups over {s:.1f}h; they no longer create "
            f"duration, and the task's remaining wall clock is {w:.1f}h"
            for t, n, w, s in polled])

    # B8i — one piece of work is one task. A card's work sat on 23 tasks and a
    # lead's sprint across 92 cards sat on one; both passed B8e (Sep 14 review).
    spread, broad = work_unit_spread(c)
    report(f"no board card or doc thread on >{CARD_SPREAD_TASKS} tasks", not spread,
           f"{len(spread)} cards/threads" if spread else "", [f"{u}: {n} tasks" for u, n in spread])
    report(f"no task holding >{CARDS_PER_TASK} board cards", not broad,
           f"{len(broad)} tasks" if broad else "", [f"{t[:10]}: {n} cards" for t, n in broad])

    # B8g — the same work estimated twice should get the same estimate. Three
    # tasks the user accepted moved 3-6x between runs with no change in the work.
    prev = previous_run_same_window(run_id, period["period_start"], period["period_end"])
    if prev:
        drift = estimate_drift(c, DATA / prev / "run.sqlite")
        report(f"estimates stable within 2x for unchanged tasks (vs {prev})", not drift,
               f"{len(drift)} tasks" if drift else "",
               [f"{t[:10]}: {a:g}h → {b:g}h" for t, a, b in drift])

    # B8h — every estimate carries a work type; a garbled reply lost it on 15.
    untyped = [r[0] for r in c.execute(
        "SELECT task_id FROM enriched WHERE COALESCE(type, '') IN ('', 'unknown')")]
    report("every estimate carries a work type", not untyped,
           f"{len(untyped)} tasks" if untyped else "", [t[:10] for t in untyped])

    # B12 — the PR fetch was complete. A fetch capped at a repo's newest 200 PRs
    # kept 15 of 262 merged in one week and every other audit passed.
    fetch_bad = pr_fetch_problems(rp)
    report("PR fetch complete for every repo", fetch_bad == [],
           "no pr_fetch.json — run predates the complete fetch; PR counts may be truncated"
           if fetch_bad is None else (f"{len(fetch_bad)} repos incomplete" if fetch_bad else ""),
           fetch_bad or [])
    fetch_file = rp / "pr_fetch.json"
    fetched = [r.get("repo") or "" for r in json.loads(fetch_file.read_text())] if fetch_file.exists() else []
    mention_gap = pr_mentions_missing(c, fetched)
    report("PRs linked in conversations are ingested", not mention_gap,
           f"{len(mention_gap)} repos missing most linked PRs" if mention_gap else "",
           [f"{r}: {m} of {n} linked PRs not ingested" for r, n, m in mention_gap])

    # B9 — labor >= agent-busy per task (agent bursts are subsets of the
    # user∪agent union; a violation means the interval math broke) (the user 2026-07-09)
    if "agent_busy_hours" in cols and labor != "NULL":
        bad_ag = c.execute(
            f"SELECT task_id FROM task_metrics m WHERE {labor} + 0.001 < agent_busy_hours").fetchall()
        report("labor ≥ agent-busy invariant", not bad_ag,
               f"{len(bad_ag)} violations" if bad_ag else "")

    # B10 — archive freshness: every data source archived up to now (the user
    # 2026-07-09). The hourly LaunchAgent died silently for 2 months once;
    # this check makes staleness loud on every run.
    import os, time
    now = time.time()
    troot = Path.home() / "dev" / "weekly-review-transcripts"

    def newest_mtime(d: Path, pattern: str = "**/*") -> float:
        best = 0.0
        if d.exists():
            for f in d.glob(pattern):
                if f.is_file():
                    best = max(best, f.stat().st_mtime)
        return best

    stale = []
    fresh_notes = []
    # 1) mac_mini transcript mirror must track the live store (lag < 24h)
    live_new = newest_mtime(Path.home() / ".claude" / "projects", "**/*.jsonl")
    mirror_new = newest_mtime(troot / "mac_mini", "**/*.jsonl")
    lag_h = (live_new - mirror_new) / 3600 if live_new else 0
    if lag_h > 24:
        stale.append(f"mac_mini transcript mirror lags live store by {lag_h:.0f}h")
    else:
        fresh_notes.append(f"transcript mirror lag {max(lag_h,0):.1f}h")
    # 2) live-feedback activity stream should be writing (server runs continuously)
    # Ask the extractor where the live store IS rather than hardcoding a path:
    # the plugin moved its data dir on 2026-09-01 and the old one still exists,
    # so a fixed path checks the freshness of a directory nothing writes to.
    # The 7d threshold also let a hard stop hide for a week — a stream that a
    # running server appends to continuously is stale at 2d, not 7.
    from .extractors.live_feedback import resolve_data_dir

    act = resolve_data_dir() / "activity.jsonl"
    if act.exists():
        age_d = (now - act.stat().st_mtime) / 86400
        if age_d > 2:
            stale.append(f"live-feedback activity.jsonl idle {age_d:.0f}d ({act.parent})")
    else:
        stale.append(f"live-feedback activity.jsonl missing ({act.parent})")
    # frozen-by-design sources are intentionally not freshness-checked:
    # laptop (pre-Apr snapshot; the user works remotely INTO the mini), conductor
    # backups, r2 (era ended Apr 12), slack (no longer an active channel —
    # The user 2026-07-09).
    report("archives fresh (transcripts, activity stream)", not stale,
           "; ".join(fresh_notes) if not stale else f"{len(stale)} stale source(s)", stale)

    # B11 — unarchived backlog. B10 asks "is the archive fresh", which is only
    # ever asked from inside a pipeline run that just refreshed it — a gate
    # downstream of its own remedy. This asks the independent question: how much
    # of the live store has NEVER been copied, and how old is the oldest such
    # file? Claude Code rotates the live store at roughly 16 weeks, so an
    # unarchived file older than that window is permanently lost. Anything under
    # a few days is just the current session.
    #
    # It reads a byte rather than trusting stat, deliberately. Under launchd on
    # this machine, an Apple platform binary can stat a path on a second volume
    # and still read zero bytes from it (measured by the lead agent 2026-08-25:
    # bash/ls/head/rsync all denied, a Developer-ID-signed bun works). A
    # stat-only probe would therefore pass on a volume it cannot read — the
    # false-green this check exists to prevent. Keep the open().
    live_dir = Path.home() / ".claude" / "projects"
    mirror_dir = troot / "mac_mini"
    if live_dir.exists() and mirror_dir.exists():
        archived = {f.name for f in mirror_dir.glob("**/*.jsonl")}
        backlog = [f for f in live_dir.glob("**/*.jsonl") if f.name not in archived]
        unreadable = 0
        oldest_d = 0.0
        for f in backlog:
            try:
                with f.open("rb") as fh:
                    fh.read(1)
            except OSError:
                unreadable += 1
                continue
            oldest_d = max(oldest_d, (now - f.stat().st_mtime) / 86400)
        # 16 weeks is the observed live-store retention; alarm well before it.
        ok = not backlog or (oldest_d < 21 and unreadable == 0)
        if unreadable:
            detail = f"{unreadable} of {len(backlog)} unarchived files unreadable — cannot audit"
        elif backlog:
            detail = f"{len(backlog)} unarchived, oldest {oldest_d:.0f}d (live store rotates ~16w)"
        else:
            detail = "live store fully archived"
        report("no unarchived backlog aging toward rotation", ok, detail)

    # B11b — name-matching says a session has SOME copy, not a CURRENT one.
    # Transcripts are append-only, so an active session keeps growing after it
    # is archived and the archive silently falls behind while still matching by
    # name. That is invisible to B11 by construction, and it is worst during an
    # outage: the long-running sessions are exactly the ones that grow AND the
    # ones that already have a name in the mirror.
    #
    # Growth in a file still being written is expected lag, not loss. The sound
    # invariant is about SETTLED files: if a transcript stopped growing before
    # the archiver last ran, the archiver had its chance and a shortfall is a
    # real gap. Anything still newer than the last archive pass is just lag.
    #
    # Key by max size per name, NOT by a name->path dict. 256 session ids appear
    # under more than one project dir in this archive (worktrees, renames, and
    # a session that moved between projects), so a naive dict lets whichever
    # copy the glob yields last win — which reported a fabricated 136MB gap on
    # 2026-08-25 when a 15KB copy under one project dir shadowed the same
    # session's fully-archived 130MB copy under another. Note the shadow is a
    # SMALL file, not an empty one: there are zero zero-byte transcripts in this
    # archive, so a search for empty stubs finds nothing and proves nothing.
    if live_dir.exists() and mirror_dir.exists():
        biggest: dict[str, int] = {}
        newest_arch = 0.0
        for f in mirror_dir.glob("**/*.jsonl"):
            st = f.stat()
            if st.st_size >= biggest.get(f.name, -1):
                biggest[f.name] = st.st_size
            newest_arch = max(newest_arch, st.st_mtime)
        settled_short = 0
        settled_bytes = 0
        lag_bytes = 0
        for f in live_dir.glob("**/*.jsonl"):
            have = biggest.get(f.name)
            if have is None:
                continue  # no copy at all — that is B11's job, not this one
            st = f.stat()
            if st.st_size <= have:
                continue
            if st.st_mtime < newest_arch:
                settled_short += 1
                settled_bytes += st.st_size - have
            else:
                lag_bytes += st.st_size - have
        report("archived copies current for settled transcripts", settled_short == 0,
               f"{settled_short} settled files short by {settled_bytes/1e6:.1f}MB"
               if settled_short else
               f"0 short; {lag_bytes/1e6:.0f}MB of in-flight write lag (expected)")

    # B11c — an overwritten transcript is still measured. See
    # recovered_generations: on 2026-08-17 the claude-workspaces lead's main loop
    # was replaced by another transcript and survived only in the history store,
    # so four months of weeks lost its turns while its subagent lanes stayed.
    # This reads timestamps, not the uuids the recovery keys on, so it is a
    # second instrument rather than the recovery checking itself.
    history_dir = troot / "mac_mini_history"
    if mirror_dir.exists() and history_dir.exists():
        lost = overwritten_sessions(mirror_dir, history_dir, period["period_start"], period["period_end"])
        missing = []
        for sid, (snap, lo, hi, records) in sorted(lost.items()):
            got = c.execute("SELECT COUNT(*) FROM events WHERE session_id = ? AND ts >= ? AND ts < ?",
                            (sid, lo, hi)).fetchone()[0]
            if records and not got:
                missing.append(f"{sid[:8]}: {records} records {lo[:16]}→{hi[:16]} in {snap}, 0 events")
        report("overwritten transcripts recovered from the archive history", not missing,
               f"{len(missing)} of {len(lost)} overwritten sessions unmeasured in this window"
               if missing else f"{len(lost)} overwritten sessions overlap this window, all measured",
               missing)

    # the user's recorded verdicts, re-checked on the tasks that now hold the work
    # he reviewed. The counts above passed while those exact tasks got worse.
    from .verdict_cases import load_cases, run_cases
    cases = load_cases()
    if cases:
        vr = run_cases(c, DATA, cases)
        # "0 of 3 hold" read as his judgement being wrong on two of the three
        # era weeks, and not one of those was that: the task boundary had moved
        # under the case. A verdict nobody can check is its own outcome.
        judged = [r for r in vr if not r.get("blocked")]
        failing = [r for r in judged if r["fails"]]
        stuck = [r for r in vr if r.get("blocked") and not r.get("out_of_window")]
        report("the user's recorded verdicts still hold", bool(judged) and not failing,
               f"{len(judged) - len(failing)} of {len(judged)} hold; "
               f"{len(vr) - len(judged) - len(stuck)} outside this window; "
               f"{len(stuck)} no longer checkable"
               + ("" if judged else " (nothing judged: not a pass)"),
               [f"{r['case']} → {r['host']}: {'; '.join(r['fails'])}" for r in failing]
               + [f"{r['case']} → {r['host']}: not checkable — {r['blocked']}" for r in stuck])

    # write structured results next to the run DB
    n_review = sum(1 for r in results if r["status"] == "REVIEW")
    (rp / "audits.json").write_text(json.dumps(
        {"run_id": run_id, "n_pass": len(results) - n_review, "n_review": n_review, "checks": results},
        indent=1))
    print(f"\n{len(results) - n_review} PASS / {n_review} REVIEW — details in {rp / 'audits.json'}", file=out)
    return n_review

