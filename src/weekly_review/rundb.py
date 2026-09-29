"""Rebuild a persisted run's events and tasks, so a report can re-derive a metric.

Hands-on and wall-clock are persisted as totals only. A report that explains a
figure (or checks it) has to recompute the intervals with the same functions the
pipeline used, which needs the in-memory `Event` / `Task` shapes back.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .events import Event
from .segmentation import Task


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_run(db_path: Path) -> tuple[list[Event], dict[str, Task]]:
    """Events sorted by time, and tasks whose `event_ids` index into that list."""
    c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    evs, ids = [], []
    for eid, ts, actor, project, sid, ss, sm, kind, seq, snip, meta in c.execute(
            "select event_id, ts, actor, project, session_id, source_system, source_machine, "
            "kind, seq, content_snippet, metadata_json from events"):
        evs.append(Event(ts=ts, actor=actor, project=project, session_id=sid, source_system=ss,
                         source_machine=sm, kind=kind, seq=seq, content_snippet=snip or "",
                         metadata=json.loads(meta) if meta else {}))
        ids.append(eid)
    order = sorted(range(len(evs)), key=lambda i: _dt(evs[i].ts))
    evs = [evs[i] for i in order]
    index = {ids[i]: n for n, i in enumerate(order)}

    tcols = {r[1] for r in c.execute("PRAGMA table_info(tasks)")}
    deleg = "delegated" if "delegated" in tcols else "0"
    kind = "unit_kind" if "unit_kind" in tcols else "''"
    board = "board_task" if "board_task" in tcols else "NULL"
    src = "board_task_source" if "board_task_source" in tcols else "''"
    tasks: dict[str, Task] = {}
    for tid, project, title, first_ts, last_ts, d, uk, bt, ls in c.execute(
            f"select task_id, project, title, first_ts, last_ts, {deleg}, {kind}, {board}, {src} from tasks"):
        tasks[tid] = Task(task_id=tid, project=project, title=title, delegated=bool(d), unit_kind=uk or "",
                          board_task=bt or None, link_source=ls or "")
        tasks[tid].first_event_ts = _dt(first_ts) if first_ts else None
        tasks[tid].last_event_ts = _dt(last_ts) if last_ts else None
    for tid, eid in c.execute("select task_id, event_id from task_events"):
        if tid in tasks and eid in index:
            tasks[tid].event_ids.append(index[eid])
    for t in tasks.values():
        t.event_ids.sort()
    c.close()
    return evs, tasks
