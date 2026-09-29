"""Attach the user's unlinked messages to board tasks (method Step 3, rule 3).

A comment on a board task, or on a doc exactly one board task links, is
placed by structure in `segmentation.segment`. Everything else — comments on
docs several tasks link or none, and terminal messages — has no link, and on
the week of 2026-09-07 that was 587 of 848 of his messages. For each one a
classifier is shown the board tasks in progress at that moment plus those
done in the previous 24 hours (goal, title, description) and picks one, or
answers "none", or says the message is a general status request, which goes
to the project's coordination unit for that day (the user's method, 2026-09-17).

What leaves this module: a placement per message and two counts the run
prints every time — how many the classifier placed, and the share it called
"none". A placement is recorded on the unit's trace under rule `classifier`
with the board task in `detail`, so a reader can find every attachment the
model made and check a sample.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .events import Event
from .segmentation import Task, _local_day, _new_task, _parse_ts

DONE_WINDOW_MS = 24 * 3600 * 1000
_BOARD_ID = re.compile(r"^t-[A-Za-z0-9_-]{12}$")
_COORD_TITLE = re.compile(r"^coordination (\d{4}-\d{2}-\d{2})$")


@dataclass(frozen=True)
class BoardTask:
    id: str
    workspace_id: str
    board: str
    title: str
    body: str
    goal_title: str
    transitions: tuple[tuple[int, str], ...]  # (epoch ms, status) in time order

    def status_at(self, at_ms: int) -> tuple[str, int | None]:
        """The task's status at `at_ms` and when it entered it, from the store's
        own transition list. A task with no transition before `at_ms` did not
        exist yet as far as the board knew."""
        status, since = "", None
        for ts, to in self.transitions:
            if ts > at_ms:
                break
            status, since = to, ts
        return status, since


def load_board_tasks(data_dir: Path | None = None) -> list[BoardTask]:
    """Every board task in the store with its board name, goal title and
    transitions. Deleted boards included, since the week being measured may
    have run on one."""
    from .extractors.live_feedback import board_files, resolve_data_dir
    from .extractors.workspace_board import _iter_store_tasks
    dd = data_dir or resolve_data_dir()
    names: dict[str, str] = {}
    goals: dict[tuple[str, str], str] = {}
    for path in board_files(dd, ".tasks.json"):
        try:
            ws = json.loads(path.read_text()).get("workspace") or {}
        except (OSError, ValueError):
            continue
        wid = ws.get("id")
        if not isinstance(wid, str):
            continue
        names[wid] = str(ws.get("name") or wid)
        for g in ws.get("goals") or []:
            if isinstance(g, dict) and isinstance(g.get("id"), str):
                goals[(wid, g["id"])] = str(g.get("title") or "")
    out: list[BoardTask] = []
    for t in _iter_store_tasks(dd):
        wid = str(t.get("workspaceId") or "")
        trs: list[tuple[int, str]] = []
        for tr in t.get("transitions") or []:
            if isinstance(tr, dict) and isinstance(tr.get("ts"), (int, float)) and isinstance(tr.get("to"), str):
                trs.append((int(tr["ts"]), tr["to"]))
        if not trs and isinstance(t.get("createdAt"), (int, float)):
            trs.append((int(t["createdAt"]), str(t.get("status") or "triage")))
        trs.sort()
        out.append(BoardTask(
            id=t["id"], workspace_id=wid, board=names.get(wid, wid),
            title=str(t.get("title") or ""), body=str(t.get("body") or ""),
            goal_title=goals.get((wid, str(t.get("goal") or "")), ""),
            transitions=tuple(trs)))
    return out


def candidates_at(board_tasks: list[BoardTask], at_ms: int, *, boards: set[str] | None = None,
                  done_window_ms: int = DONE_WINDOW_MS) -> list[tuple[BoardTask, str]]:
    """Board tasks in progress at `at_ms`, plus those done within the previous
    `done_window_ms`, each with a status label for the prompt. `boards` limits
    the set to those board names; None means every board."""
    out = []
    for bt in board_tasks:
        if boards is not None and bt.board not in boards:
            continue
        status, since = bt.status_at(at_ms)
        if status == "in-progress":
            out.append((bt, "in progress"))
        elif status == "done" and since is not None and at_ms - since <= done_window_ms:
            hours = (at_ms - since) / 3600000
            out.append((bt, f"done {hours:.0f}h earlier"))
    return out


@dataclass
class Placement:
    event_idx: int
    board_task: str | None = None     # the task the message is about
    status_request: bool = False      # a general "where are we" → coordination
    candidates: int = 0               # how many tasks the classifier saw


def his_unlinked_messages(tasks: list[Task], events: list[Event]) -> list[int]:
    """Indices of his messages that sit on no task after segmentation."""
    attached: set[int] = set()
    for t in tasks:
        attached.update(t.event_ids)
    return [i for i, e in enumerate(events)
            if e.kind == "user_message" and e.actor == "user" and i not in attached]


_SYSTEM = """You attach one person's messages to the task on their work board that each message is about.

You are given the board tasks that were in progress when the messages were written (plus any finished in the previous 24 hours), each with its goal, title and description, and then the messages. For each message answer with the id of the ONE task it is about.

Rules:
- Answer "none" when no listed task fits. Do not guess: a message about work that is not on the list is "none".
- A message that asks for overall status, what is next, or a summary across tasks (not about one task) is a status request: answer "none" and set status_request true.
- A reply in a thread about a task is about that task even if it does not name it.
- Judge from the message text against the task titles and descriptions. Never invent a task id.

Answer ONLY a JSON array, one object per message, in the message order given:
[{"m": <message number>, "task": "<task id or none>", "status_request": <true|false>}]"""


def build_prompt(messages: list[tuple[int, Event]], cands: list[tuple[BoardTask, str]],
                 message_number: dict[int, int]) -> str:
    lines = ["BOARD TASKS (id | status | goal | title | description):"]
    for bt, label in cands:
        body = " ".join(bt.body.split())[:400]
        lines.append(f"- {bt.id} | {label} | goal: {bt.goal_title or '(none)'} | {bt.title} | {body}")
    lines.append("")
    lines.append("MESSAGES:")
    for idx, ev in messages:
        md = ev.metadata or {}
        where = "terminal" if ev.source_system == "claude_transcript" else f"comment on doc {md.get('doc_id', '?')}"
        text = " ".join((ev.content_snippet or "").split())[:600]
        lines.append(f"{message_number[idx]}. [{ev.ts[:16]}] ({where}) {text}")
    return "\n".join(lines)


def _parse_answer(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < 0:
        return []
    try:
        arr = json.loads(text[start:end + 1])
    except ValueError:
        return []
    return [a for a in arr if isinstance(a, dict)] if isinstance(arr, list) else []


def classify_messages(events: list[Event], idxs: list[int], board_tasks: list[BoardTask], *,
                      llm, model: str, board_of_event: Callable[[Event], str | None] | None = None,
                      boards_of_project: dict[str, set[str]] | None = None,
                      max_batch: int = 25) -> tuple[list[Placement], dict[str, Any]]:
    """One call per (project, local day, batch of up to `max_batch` messages);
    the candidate set is the union over the batch's message times, plus any
    task a message's doc names (`task_candidates`).
    `board_of_event` maps a message to its board name when known;
    `boards_of_project` maps a project to the boards that hold its work. With
    neither, every board is a candidate."""
    known = {bt.id: bt for bt in board_tasks}
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i in idxs:
        groups[(events[i].project, _local_day(_parse_ts(events[i].ts)))].append(i)
    placements: list[Placement] = []
    stats = {"calls": 0, "messages": len(idxs), "placed": 0, "none": 0, "status_request": 0,
             "no_candidates": 0, "unparsed": 0, "invalid_id": 0}
    for (project, _day), members in sorted(groups.items()):
        for start in range(0, len(members), max_batch):
            batch = members[start:start + max_batch]
            # Boards per message, unioned: a comment sees its doc's board, a
            # terminal message the boards that hold its project's work; a
            # message with neither sees every board, and so does the batch.
            boards: set[str] | None = set()
            for i in batch:
                b = board_of_event(events[i]) if board_of_event is not None else None
                if b is None and boards_of_project:
                    mine = boards_of_project.get(project)
                    if mine:
                        boards.update(mine)
                        continue
                if b is None:
                    boards = None
                    break
                boards.add(b)
            cands: dict[str, tuple[BoardTask, str]] = {}
            for i in batch:
                at_ms = int(_parse_ts(events[i].ts).timestamp() * 1000)
                for bt, label in candidates_at(board_tasks, at_ms, boards=boards):
                    cands.setdefault(bt.id, (bt, label))
            # A doc several tasks link already names its candidates.
            for i in batch:
                for c in (events[i].metadata or {}).get("task_candidates") or []:
                    if isinstance(c, str) and c in known and c not in cands:
                        cands[c] = (known[c], "linked from this doc")
            if not cands:
                for i in batch:
                    placements.append(Placement(event_idx=i, candidates=0))
                stats["no_candidates"] += len(batch)
                stats["none"] += len(batch)
                continue
            number = {i: n + 1 for n, i in enumerate(batch)}
            user = build_prompt([(i, events[i]) for i in batch], list(cands.values()), number)
            stats["calls"] += 1
            resp = llm.complete(system=_SYSTEM, user=user, model=model, max_tokens=2048, temperature=0.0)
            answers: dict[int, dict[str, Any]] = {}
            for a in _parse_answer(resp.get("text", "")):
                try:
                    answers[int(a.get("m"))] = a
                except (TypeError, ValueError):
                    continue
            for i in batch:
                a = answers.get(number[i])
                p = Placement(event_idx=i, candidates=len(cands))
                if a is None:
                    stats["unparsed"] += 1
                    stats["none"] += 1
                else:
                    tid = a.get("task")
                    if isinstance(tid, str) and tid in cands:
                        p.board_task = tid
                        stats["placed"] += 1
                    elif isinstance(tid, str) and tid.lower() != "none" and _BOARD_ID.match(tid):
                        stats["invalid_id"] += 1   # an id it was not shown
                        stats["none"] += 1
                    else:
                        stats["none"] += 1
                    if not p.board_task and bool(a.get("status_request")):
                        p.status_request = True
                        stats["status_request"] += 1
                placements.append(p)
    return placements, stats


def attach_placements(tasks: list[Task], events: list[Event], placements: list[Placement],
                      titles: dict[str, str] | None = None) -> dict[str, int]:
    """Put each placed message on the unit that carries its board task — the
    conversation unit when there is one, else the largest unit on that board
    task, else a new unit for it — and each status request on the project's
    coordination unit for that local day, made if absent. Never moves an
    attached event. Records rule `classifier` on the trace."""
    titles = titles or {}
    by_card: dict[str, list[Task]] = defaultdict(list)
    coord: dict[tuple[str, str], Task] = {}
    for t in tasks:
        if t.board_task:
            by_card[t.board_task].append(t)
        if t.unit_kind == "coordination":
            # Keyed by the day segmentation named it, not a recomputed
            # timestamp: a PR join can pull first_event_ts onto the day before.
            m = _COORD_TITLE.match(t.title)
            if m:
                coord[(t.project, m.group(1))] = t
    stats = {"to_board_task": 0, "new_units": 0, "to_coordination": 0, "new_coordination": 0}
    for p in placements:
        ev = events[p.event_idx]
        ts = _parse_ts(ev.ts)
        if p.board_task:
            host = None
            units = by_card.get(p.board_task) or []
            plain = [u for u in units if not u.unit_kind]
            if plain:
                host = max(plain, key=lambda u: len(u.event_ids))
            elif units:
                host = max(units, key=lambda u: len(u.event_ids))
            if host is None:
                host = _new_task(ev.project, titles.get(p.board_task) or f"board task {p.board_task}",
                                 ev.ts, p.board_task, set())
                host.board_task = p.board_task  # its own unit: link_source stays empty
                tasks.append(host)
                by_card[p.board_task].append(host)
                stats["new_units"] += 1
            host.touch(p.event_idx, ts, rule="classifier", detail=p.board_task, session_id=ev.session_id)
            host.delegated = False
            stats["to_board_task"] += 1
        elif p.status_request:
            key = (ev.project, _local_day(ts))
            host = coord.get(key)
            if host is None:
                host = _new_task(ev.project, f"coordination {key[1]}", ev.ts, f"coordination:{key[1]}", set())
                host.unit_kind = "coordination"
                tasks.append(host)
                coord[key] = host
                stats["new_coordination"] += 1
            host.touch(p.event_idx, ts, rule="classifier", detail="status_request", session_id=ev.session_id)
            host.delegated = False  # it now holds his message
            stats["to_coordination"] += 1
    return stats


def sample_for_review(placements: list[Placement], events: list[Event], titles: dict[str, str],
                      n: int = 12, seed: int = 0) -> list[dict[str, str]]:
    """`n` placed messages drawn at random for the owner to check: when, what
    he wrote (his own words), and the board task the classifier chose."""
    import random
    placed = [p for p in placements if p.board_task]
    rng = random.Random(seed)
    pick = rng.sample(placed, min(n, len(placed)))
    rows = []
    for p in sorted(pick, key=lambda p: events[p.event_idx].ts):
        ev = events[p.event_idx]
        rows.append({"ts": ev.ts[:16], "project": ev.project,
                     "message": " ".join((ev.content_snippet or "").split())[:300],
                     "board_task": p.board_task or "", "title": titles.get(p.board_task or "", ""),
                     "candidates": str(p.candidates)})
    return rows
