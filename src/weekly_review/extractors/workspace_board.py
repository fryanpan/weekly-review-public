"""Extract the claude-workspaces BOARD action log as first-class events.

The workspace writes two stores. `activity.jsonl` records what happened to
*documents* — reads, comments, replies — and the live-feedback extractor has
read it since June. `workspaces/<wsId>.events.jsonl` records what happened to
the *board*: tasks created, transitioned, archived, and decisions answered.
Nothing in this pipeline has ever opened it.

That is not a deduplication bug or a routing bug — it is a source that has
never been loaded, which is why it comes first in the 2026-09-08 plan. No
amount of fixing dedup recovers an event the pipeline never reads. In the week
of 2026-08-31 it costs 183 of the user's own answered decisions, each one a
recorded act of work carrying the `taskId` it was performed against.

Why this source is worth more than its volume suggests: **every task.* and
decision.* record carries a `taskId`** (measured 2026-09-12: 9,354 of 9,354,
zero missing) and **every one carries an `actor` with a `kind`** (9,354 of
9,354, 1,323 of them `person`). The doc stream has neither — it is keyed on
`docId` and its `actor` is a bare string. So this is the only store in the
workspace where "who did it" and "what it was done to" are both recorded, and
it is the anchor the doc-side join is built against.

Deliberately NOT modelled as time here. These events are instants, not
durations, and nothing in this module emits `fixed_active_sec`, so a board
action contributes zero hands-on hours today. Turning them into intervals is
the 60-second contiguous-block rule, which is its own task and depends on this
one. Ingesting first and attributing second is what keeps the two reviewable
apart: this change can only ADD events, never move an hour.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from ..events import Event
from .live_feedback import _route_doc, board_files, load_workspace_names, resolve_data_dir

# The families that describe somebody acting on the board. The other 23 event
# types in the file are server bookkeeping (`server.tick` alone is 74,542 of
# 97,398 records), agent liveness, or review-item plumbing. Scope per the user's
# approved plan, 2026-09-08; widening it is one entry here.
#
# `voice.` joined it on 2026-09-12 — the one entry that comment promised. A
# `voice.request` is the user dictating a request to the board: 88 of them between
# 2026-08-18 and 2026-09-11, each carrying `actor` with a `kind`, a `transcript`,
# a `route` and an `ack`. They live in the BOARD log, not in `activity.jsonl`
# where the task looked for them, which is why nothing had ever read one. Like
# every other record here a voice request is an instant and creates no duration
# of its own — what it does is bridge, and a dictated request is the densest
# possible evidence that he was at the desk and not at the keyboard.
BOARD_EVENT_PREFIXES = ("task.", "decision.", "voice.")

# actor.kind -> (Event.actor, Event.author_kind). The board records the
# distinction the doc stream cannot: work drafted under the user's identity by an
# agent is agent work, and his hands-on total must not absorb it.
_ACTOR_KINDS = {
    "person": ("user", "human"),
    "agent": ("agent", "agent"),
}


def board_event_files(data_dir: Path) -> list[Path]:
    """Every board's action log, deleted boards included, sorted so a run is
    reproducible (see board_files)."""
    return board_files(data_dir, ".events.jsonl")


def _snippet(rec: dict) -> str:
    """The most human-readable field the record happens to carry.

    Each family names its payload differently — an answered decision carries
    `answer` and `headline`, a transition carries `note`, an archive carries
    `reason`, a voice request carries `transcript`. Take the first that is
    present rather than special-casing 34 event types, and fall back to the
    event name so a snippet is never empty.

    `transcript` is the one spoken field this pipeline stores, and it is stored
    because of whose words it is: a voice request is the user dictating to his own
    board, which is the same act as typing to it, and his typed messages have
    always been kept. Meeting turns are the opposite case — other people, saying
    things to each other — and the meetings extractor keeps word counts only.
    """
    for key in ("answer", "note", "reason", "headline", "title", "text", "question",
                "transcript"):
        val = rec.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()[:500]
    task = rec.get("task")
    if isinstance(task, dict) and isinstance(task.get("title"), str):
        return task["title"][:500]
    return str(rec.get("event", ""))


def _event_from_record(projects, rec: dict, seq: int, board_name: str,
                       source_machine: str) -> Event | None:
    name = rec.get("event") or ""
    if not name.startswith(BOARD_EVENT_PREFIXES):
        return None
    actor = rec.get("actor")
    if not isinstance(actor, dict):
        return None
    mapped = _ACTOR_KINDS.get(str(actor.get("kind", "")).strip().lower())
    if mapped is None:
        return None  # an actor we can't classify must not silently become human
    ev_actor, author_kind = mapped
    ts_ms = rec.get("ts")
    if not ts_ms:
        return None
    # Every `task.*` and `decision.*` record carries a top-level `taskId` (9,354
    # of 9,354, measured 2026-09-12). A `voice.request` carries NONE of them, and
    # names its task — when it has one — inside `context` instead: 7 of 88 were
    # dictated from a task page and say `{"surface": "task", "taskId": "t-…"}`.
    # Reading only the top level would have dropped all 7 silently, which is the
    # store's own id going unread for the third time in this repo.
    context = rec.get("context") if isinstance(rec.get("context"), dict) else {}
    task_id = str(rec.get("taskId") or context.get("taskId") or "")
    workspace_id = rec.get("workspaceId") or ""
    # Board-first routing, the user's ruling of 2026-09-08: the board that owns the
    # task decides the project. A board event has no working directory, so the
    # board is the ONLY signal — and the title is deliberately withheld from the
    # keyword fallback. Passing it routed sibling events on one board to two
    # different projects (measured: 2 of 27 on "Week of Aug 10 — Team Lead" went
    # to writing-tool because their titles said "blog"; 1 of 90 on "Team
    # Lead" went to client-a). A board belongs to one project or to none;
    # per-event drift inside one board is a guess wearing a measurement's
    # clothes. A board whose name matches nothing lands in `unknown`, which the
    # coverage report surfaces, rather than being silently assigned.
    project = _route_doc(projects, "", f"task:{task_id}", "", board_name, "")
    return Event(
        ts=datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat(),
        actor=ev_actor,
        project=project,
        # One session per board, so the seq below is unique within it.
        session_id=f"workspace-board:{workspace_id}",
        source_system="workspace_board",
        source_machine=source_machine,
        kind="board_action",
        seq=seq,
        content_snippet=_snippet(rec),
        metadata={
            "board_event": name,
            "task_id": task_id,
            "workspace_id": workspace_id,
            # Which review item an answer is to (588 of 673 answers carry it),
            # so two answers on one task are still two items read.
            **({"review_item_id": str(rec["reviewItemId"])} if rec.get("reviewItemId") else {}),
            "board_name": board_name,
            "actor_kind": str(actor.get("kind", "")).strip().lower(),
            # Where he was standing when he said it: `hub` (73), `board` (8) or
            # `task` (7). Only the last names a task, which is why the other 81
            # fall back to their board below.
            **({"surface": str(context["surface"])} if context.get("surface") else {}),
            # A dictation's length, so it can be given its speaking time; the
            # snippet is cut at 500 characters and would undercount a long one.
            **({"words": len(rec["transcript"].split())}
               if isinstance(rec.get("transcript"), str) else {}),
            # A task's title and body, for the typing time of writing it.
            **({"words": sum(len(v.split()) for v in (rec["task"].get("title"), rec["task"].get("body"))
                             if isinstance(v, str))}
               if name == "task.created" and isinstance(rec.get("task"), dict) else {}),
            "via": "workspace-board-log",
        },
        author_id=str(actor.get("id", "") or ""),
        author_kind=author_kind,
    )


def extract_board_events(
    projects,
    data_dir: Path | None = None,
    source_machine: str = "workspace_board",
) -> Iterator[Event]:
    """Yield one Event per board action, keyed on its `taskId`.

    `seq` is the record's line index inside its own board file. The logs are
    append-only, so the index is stable across runs and the event_id
    (`workspace_board:workspace-board:<wsId>:<line>`) is stable with it. This
    is the fix the doc stream needs and does not have: live-feedback ids are
    `live_feedback:live-feedback:<docId>:0`, the same id for every visit, so
    dedup discards 82% of them. Here the position in the log is part of the
    identity, so two actions on one task never collide.
    """
    dd = data_dir or resolve_data_dir()
    board_names = load_workspace_names(dd)
    for path in board_event_files(dd):
        # `w-abc123.events.jsonl` -> `w-abc123`, used only when a record omits
        # its own workspaceId (none do today) and to name the board.
        file_ws_id = path.name[: -len(".events.jsonl")]
        board_name = board_names.get(file_ws_id, "")
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for seq, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            rec.setdefault("workspaceId", file_ws_id)
            name = board_names.get(rec.get("workspaceId") or "", board_name)
            ev = _event_from_record(projects, rec, seq, name, source_machine)
            if ev is not None:
                yield ev


def held_board_tasks(data_dir: Path | None = None) -> set[str]:
    """Every board task id a board store holds: live or deleted boards, from
    their task lists and their action logs.

    An agent testing the workspace app starts its own server on another port
    and creates boards and tasks there to test against. Those ids appear in
    its prompts and tool calls exactly like real ones, and no store keeps
    them. On the week of 2026-08-17 the live-feedback lead's builders ran
    servers on over 100 ports besides the real one; 5 measured tasks, 9h
    estimated, were cut onto 5 such tasks and away from the work the user asked
    for. A board task no store holds is not one of his."""
    dd = data_dir or resolve_data_dir()
    held: set[str] = set()
    for path in board_files(dd, ".tasks.json"):
        try:
            blob = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        for task in (blob.get("tasks") if isinstance(blob, dict) else None) or []:
            tid = task.get("id") if isinstance(task, dict) else None
            if isinstance(tid, str) and tid.startswith("t-"):
                held.add(tid)
    for path in board_event_files(dd):
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            context = rec.get("context") if isinstance(rec.get("context"), dict) else {}
            for tid in (rec.get("taskId"), context.get("taskId")):
                if isinstance(tid, str) and tid.startswith("t-"):
                    held.add(tid)
    return held


def drop_unheld_cards(events, held: set[str]) -> tuple[int, set[str]]:
    """Clear `card` on events naming a board task no store holds, keeping the
    id as `card_unheld` for audit. Returns (events changed, ids cleared).

    With no store at all (`held` empty) nothing is cleared: an install without
    the workspace app keeps every id it reads."""
    if not held:
        return 0, set()
    changed, ids = 0, set()
    for ev in events:
        md = ev.metadata if isinstance(ev.metadata, dict) else None
        card = md.get("card") if md else None
        if isinstance(card, str) and card not in held:
            md["card_unheld"] = md.pop("card")
            changed += 1
            ids.add(card)
    return changed, ids


def board_task_facts(data_dir: Path | None = None, until_ms: int | None = None) -> dict[str, dict]:
    """Per board task: who created it, and whether the user answered or raised a
    review item on it or dictated to it, over its life up to `until_ms`.

    the user's 2026-09-14 ruling: a board task an agent created and finished with
    no review request from him legitimately has no hands-on time. Creation
    usually predates the window being measured, so this reads the whole log
    rather than the windowed events. Housekeeping (move, archive, regroup) is
    not a reason to expect hands-on and is not recorded.

    `his_last_ask` is the epoch ms of his latest ask on it: creating it,
    answering or raising a review item, or dictating. An ask before the window
    being measured was measured in its own week; one inside it must be among
    the window's events, which is what lets a check tell the two apart.
    """
    dd = data_dir or resolve_data_dir()
    facts: dict[str, dict] = {}
    for path in board_event_files(dd):
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict) or not isinstance(rec.get("actor"), dict):
                continue
            ts = rec.get("ts")
            if until_ms is not None and (not isinstance(ts, (int, float)) or ts > until_ms):
                continue
            name = str(rec.get("event") or "")
            context = rec.get("context") if isinstance(rec.get("context"), dict) else {}
            tid = rec.get("taskId") or context.get("taskId")
            if not isinstance(tid, str) or not tid.startswith("t-"):
                continue
            kind = str(rec["actor"].get("kind", "")).strip().lower()
            f = facts.setdefault(tid, {"creator_kind": None, "his_review": False, "his_dictation": False,
                                       "his_last_ask": None})
            if name == "task.created":
                f["creator_kind"] = f["creator_kind"] or kind
            elif kind == "person" and name.startswith("decision."):
                f["his_review"] = True
            elif kind == "person" and name.startswith("voice."):
                f["his_dictation"] = True
            if kind == "person" and (name == "task.created" or name.startswith(("decision.", "voice."))) \
                    and isinstance(ts, (int, float)):
                f["his_last_ask"] = max(f["his_last_ask"] or 0, int(ts))
    # The goal comes from the task store, not the log: it is the task's current
    # goal, which is what the summary rolls up to (the user, 2026-09-17).
    for tid, goal in board_task_goals(dd).items():
        facts.setdefault(tid, {"creator_kind": None, "his_review": False, "his_dictation": False,
                               "his_last_ask": None})["goal"] = goal
    return facts


def _iter_store_tasks(data_dir: Path):
    """Every task record in every board's task store, deleted boards included."""
    for path in board_files(data_dir, ".tasks.json"):
        try:
            blob = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        for task in (blob.get("tasks") if isinstance(blob, dict) else None) or []:
            if isinstance(task, dict) and isinstance(task.get("id"), str) and task["id"].startswith("t-"):
                yield task


def board_task_goals(data_dir: Path | None = None) -> dict[str, str]:
    """board task id -> the goal it sits under, from the task store.

    the user's 2026-09-17 call: a workspace goal is the summary item he reviews
    each week, so every measured task on a board task rolls up by this id.
    Measured on the week of 2026-09-07: all 306 measured tasks with a board
    task had one (1,659 of 1,659 store records carry `goal`). A task with no
    goal is absent from the map, never mapped to "".
    """
    goals: dict[str, str] = {}
    for task in _iter_store_tasks(data_dir or resolve_data_dir()):
        goal = task.get("goal")
        if isinstance(goal, str) and goal:
            goals[task["id"]] = goal
    return goals


_PR_URL = re.compile(r"github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")


def board_task_pr_links(data_dir: Path | None = None) -> dict[tuple[str, str], frozenset[str]]:
    """(owner/repo lowercased, PR number) -> the board tasks whose record names
    that PR anywhere: a `url` link, the body, a note, a done-when proof, a
    transition note.

    Step 5 of the method (2026-09-17): a unit holding a PR that exactly one
    board task names is linked to that task, and so to its goal. The map
    carries every naming task so the caller can abstain when there are two
    (25 of 298 PRs on the store as of 2026-09-18 are named by more than one:
    a builder's task and the lead's rollout task, usually).
    """
    links: dict[tuple[str, str], set[str]] = {}
    for task in _iter_store_tasks(data_dir or resolve_data_dir()):
        for m in _PR_URL.finditer(json.dumps(task)):
            links.setdefault((m.group(1).lower(), m.group(2)), set()).add(task["id"])
    return {k: frozenset(v) for k, v in links.items()}


def doc_task_links(data_dir: Path | None = None) -> dict[str, frozenset[str]]:
    """doc id -> the board tasks whose `links` name it.

    the user's 2026-09-17 rule for his messages: a comment on a doc or mockup
    linked to exactly ONE board task belongs to that task, with no judgment
    needed; a doc several tasks link (one board-wide doc is linked by 354)
    tells you nothing about which, and falls through to the classifier. The
    map carries every link so the caller can tell "one" from "many"; over the
    whole store 82 docs are linked by one task and 53 by more.
    """
    links: dict[str, set[str]] = {}
    for task in _iter_store_tasks(data_dir or resolve_data_dir()):
        for link in task.get("links") or []:
            if not isinstance(link, dict) or link.get("kind") not in ("doc", "thread"):
                continue
            doc_id = link.get("docId")
            if isinstance(doc_id, str) and doc_id:
                links.setdefault(doc_id, set()).add(task["id"])
    return {d: frozenset(ts) for d, ts in links.items()}
