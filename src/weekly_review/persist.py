"""SQLite persistence for a run.

Writes a queryable snapshot of every level of output (events, tasks, enrichment
calls, task metrics, reviews) alongside the existing JSONL. SQLite is the
source-of-truth for derived data (tasks, enriched, metrics); JSONL is
source-of-truth for raw events (portable, append-only friendly).

All JSON-typed columns are stored as TEXT with a `json_valid` CHECK.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from .enrichment import EnrichedTask, EnrichmentCall
from .events import Event
from .segmentation import Task


SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id        TEXT PRIMARY KEY,
    ts              TEXT NOT NULL,
    actor           TEXT NOT NULL,
    project         TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    source_system   TEXT NOT NULL,
    source_machine  TEXT NOT NULL,
    kind            TEXT NOT NULL,
    seq             INTEGER NOT NULL,
    content_snippet TEXT,
    words           INTEGER,
    metadata_json   TEXT CHECK (metadata_json IS NULL OR json_valid(metadata_json))
);
CREATE INDEX IF NOT EXISTS events_project_ts ON events (project, ts);
CREATE INDEX IF NOT EXISTS events_kind_ts    ON events (kind, ts);
CREATE INDEX IF NOT EXISTS events_session    ON events (session_id, seq);

CREATE TABLE IF NOT EXISTS tasks (
    task_id           TEXT PRIMARY KEY,
    project           TEXT NOT NULL,
    title             TEXT NOT NULL,
    summary           TEXT,
    first_ts          TEXT,
    last_ts           TEXT,
    user_msg_count    INTEGER NOT NULL DEFAULT 0,
    event_count       INTEGER NOT NULL DEFAULT 0,
    issue_refs_json   TEXT CHECK (issue_refs_json IS NULL OR json_valid(issue_refs_json)),
    rule_counts_json  TEXT CHECK (rule_counts_json IS NULL OR json_valid(rule_counts_json)),
    -- Agent-only work stretch reconstructed by segmentation pass 5 (delegated
    -- fleet builds; hands-on legitimately ~0). Added 2026-07-09.
    delegated         INTEGER NOT NULL DEFAULT 0,
    -- The workspace board task this measured task stands for. Added 2026-09-14.
    board_task        TEXT,
    -- The kind of priced unit segmentation made: lane, lead_worktree,
    -- coordination, pr; empty for a conversation or board task. Added 2026-09-18.
    unit_kind         TEXT NOT NULL DEFAULT '',
    -- How board_task was found on a unit that is not the board task's own:
    -- lane_card, host, board_pr, pr_ref, dispatch, own_card, worktree.
    -- Empty when the unit is the board task's own or has none. Added 2026-09-18.
    board_task_source TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS tasks_project ON tasks (project);

-- What the board log says about each board task named in the run, read over
-- its whole life up to the window's end: who created it, and whether the user
-- answered or raised a review item on it or dictated to it. The split check
-- needs this because creation usually predates the window. Added 2026-09-14.
CREATE TABLE IF NOT EXISTS board_tasks (
    board_task     TEXT PRIMARY KEY,
    creator_kind   TEXT,
    his_review     INTEGER NOT NULL DEFAULT 0,
    his_dictation  INTEGER NOT NULL DEFAULT 0,
    his_last_ask   TEXT, -- ISO time of his latest create, answer or dictation
    goal           TEXT, -- workspace goal id; the summary item (the user, 2026-09-17)
    -- Calendar time per board task (method Step 7, 2026-09-18): first agent
    -- work on any unit linked to it, to its done transition. NULL when either
    -- end is unknown in this run.
    first_agent_ts   TEXT,
    done_ts          TEXT,
    wall_clock_hours REAL
);

CREATE TABLE IF NOT EXISTS task_events (
    task_id  TEXT NOT NULL,
    event_id TEXT NOT NULL,
    rule     TEXT,
    detail   TEXT,
    PRIMARY KEY (task_id, event_id),
    FOREIGN KEY (task_id)  REFERENCES tasks(task_id),
    FOREIGN KEY (event_id) REFERENCES events(event_id)
);
CREATE INDEX IF NOT EXISTS task_events_by_event ON task_events (event_id);

CREATE TABLE IF NOT EXISTS enriched (
    task_id                 TEXT PRIMARY KEY,
    run_id                  TEXT NOT NULL,
    title                   TEXT NOT NULL,
    goal                    TEXT,
    summary                 TEXT,
    est_human_hours         REAL NOT NULL,
    uncertainty             TEXT,
    bryan_domain_expertise  TEXT,
    scope                   TEXT,
    depth                   TEXT,
    type                    TEXT,
    rationale               TEXT,
    status                  TEXT,
    effort_breakdown        TEXT,
    bryan_role_summary      TEXT,
    what_went_well          TEXT,
    problems_encountered    TEXT,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

CREATE TABLE IF NOT EXISTS enrichment_calls (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id                 TEXT NOT NULL,
    run_id                  TEXT NOT NULL,
    model                   TEXT NOT NULL,
    prompt_system           TEXT NOT NULL,
    prompt_user             TEXT NOT NULL,
    response_raw            TEXT NOT NULL,
    response_parsed_json    TEXT CHECK (response_parsed_json IS NULL OR json_valid(response_parsed_json)),
    input_tokens            INTEGER NOT NULL DEFAULT 0,
    output_tokens           INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens       INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens   INTEGER NOT NULL DEFAULT 0,
    called_at               TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);
CREATE INDEX IF NOT EXISTS enrichment_calls_task ON enrichment_calls (task_id);

CREATE TABLE IF NOT EXISTS task_metrics (
    task_id              TEXT PRIMARY KEY,
    run_id               TEXT NOT NULL,
    active_hours         REAL NOT NULL DEFAULT 0,
    agent_active_hours   REAL NOT NULL DEFAULT 0,
    resumptions          INTEGER NOT NULL DEFAULT 0,
    -- wall_clock_hours is CALENDAR time since schema v2 (2026-09-18, method
    -- Step 7): first agent work on the unit to its last event, pauses
    -- included. In runs before v2 this same column held what is now
    -- labor_hours; persist.metric_columns() reads either correctly.
    wall_clock_hours     REAL NOT NULL DEFAULT 0,
    -- tool-latency-inclusive agent busy time (2026-06-10)
    agent_busy_hours     REAL NOT NULL DEFAULT 0,
    -- Interaction-shape metrics (added 2026-07-08, the user's ask): attention
    -- blocks (user msgs <2min apart = 1 block), total chars the user typed, and
    -- the model with the most agent events in the task. Old runs: 0/''.
    hands_on_blocks      INTEGER NOT NULL DEFAULT 0,
    human_input_chars    INTEGER NOT NULL DEFAULT 0,
    dominant_model       TEXT NOT NULL DEFAULT '',
    -- labor_hours: user ∪ agent intervals, pauses removed (added 2026-06-10
    -- as wall_clock_hours, renamed 2026-09-18). agent_lane_hours: per-lane
    -- agent busy time summed across concurrent agents (2026-07-12, formerly
    -- agent_labor_hours); lane ÷ busy = parallelism factor.
    labor_hours          REAL NOT NULL DEFAULT 0,
    agent_lane_hours     REAL NOT NULL DEFAULT 0,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

CREATE TABLE IF NOT EXISTS run_meta (
    run_id                       TEXT PRIMARY KEY,
    schema_version               TEXT,
    period_start                 TEXT,
    period_end                   TEXT,
    cognitive_wall_clock_hours   REAL,
    context_switches             INTEGER,
    coverage_percent             REAL,
    coverage_assigned            INTEGER,
    coverage_total               INTEGER,
    created_at                   TEXT NOT NULL
);

-- One row per artifact identity that appeared in MORE than one task: which
-- task owns the est credit, and which placements were demoted to context-only
-- in their enrichment prompts. Added 2026-07-28 (est double-count fix).
CREATE TABLE IF NOT EXISTS artifact_credit (
    run_id                   TEXT NOT NULL,
    identity                 TEXT NOT NULL,
    owner_task_id            TEXT NOT NULL,
    duplicate_task_ids_json  TEXT CHECK (duplicate_task_ids_json IS NULL OR json_valid(duplicate_task_ids_json)),
    PRIMARY KEY (run_id, identity)
);

-- One row per estimate the RUN replaced after enrichment produced it. Added
-- 2026-09-15 with decision pricing: a standalone decision is worth his
-- measured read-and-reply time, so its stored est no longer comes from the
-- rationale that sits beside it. Any check weighing an estimate against that
-- rationale -- or against the same task in an earlier run -- has to drop these
-- rows, exactly as it already drops the ones he reviewed himself, or it
-- reports the correction as a defect. It did: 71 of 71 flags across three
-- checks and three era weeks were this and nothing else.
CREATE TABLE IF NOT EXISTS estimate_corrections (
    run_id      TEXT NOT NULL,
    task_id     TEXT NOT NULL,
    kind        TEXT NOT NULL,
    before_h    REAL NOT NULL,
    after_h     REAL NOT NULL,
    PRIMARY KEY (run_id, task_id, kind)
);

CREATE TABLE IF NOT EXISTS estimate_reviews (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id           TEXT NOT NULL,
    run_id            TEXT NOT NULL,
    reviewed_at       TEXT NOT NULL,
    llm_est_hours     REAL,
    bryan_est_hours   REAL NOT NULL,
    confidence        TEXT,
    note              TEXT,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);
CREATE INDEX IF NOT EXISTS estimate_reviews_task ON estimate_reviews (task_id);
"""


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


# Columns added to existing tables after their first release. CREATE TABLE IF NOT
# EXISTS is a no-op on a DB that already has the table, so a new column has to be
# ALTERed in or every write against a pre-existing DB fails on the column count.
_ADDED_COLUMNS: list[tuple[str, str, str]] = [
    # (table, column, DDL type + default)
    ("tasks", "board_task", "TEXT"),
    ("board_tasks", "goal", "TEXT"),
    ("board_tasks", "first_agent_ts", "TEXT"),
    ("board_tasks", "done_ts", "TEXT"),
    ("board_tasks", "wall_clock_hours", "REAL"),
    ("tasks", "unit_kind", "TEXT NOT NULL DEFAULT ''"),
    ("tasks", "board_task_source", "TEXT NOT NULL DEFAULT ''"),
    # schema v2 (2026-09-18). A v1 db opened here gains them empty; its
    # run_meta.schema_version still says v1, so metric_columns() keeps reading
    # its labor from the old wall_clock_hours column.
    ("task_metrics", "labor_hours", "REAL NOT NULL DEFAULT 0"),
    ("task_metrics", "agent_lane_hours", "REAL NOT NULL DEFAULT 0"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, decl in _ADDED_COLUMNS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not cols:
            continue  # table doesn't exist here; SCHEMA will have made it
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


# Runs written before 2026-09-18 ("v1") stored labor time (user ∪ agent,
# pauses removed) in task_metrics.wall_clock_hours and lane time in
# agent_labor_hours. From "v2" wall_clock_hours is calendar time and the two
# older figures have their own columns. This is the ONE place that reads a v1
# column under its old meaning; every reader of a run db goes through it.
def metric_columns(conn: sqlite3.Connection) -> dict[str, str]:
    """SQL expressions, on alias `m` for task_metrics, for `labor`, `lane`
    and `wall` (calendar) — `NULL` where this run never measured one."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(task_metrics)")}
    try:
        row = conn.execute("SELECT schema_version FROM run_meta LIMIT 1").fetchone()
        version = str(row[0]) if row and row[0] else ""
    except sqlite3.DatabaseError:
        version = ""
    if not version:
        # No readable run_meta (a fixture, or a db written before run_meta
        # existed). agent_labor_hours is only ever CREATEd by v1 and never
        # added by _migrate, so its presence marks a v1 db even after connect()
        # has added empty labor_hours columns to it; labor_hours without it is v2.
        version = "v1" if "agent_labor_hours" in cols or "labor_hours" not in cols else "v2"
    if version == "v1":
        return {"labor": "m.wall_clock_hours" if "wall_clock_hours" in cols else "NULL",
                "lane": "m.agent_labor_hours" if "agent_labor_hours" in cols else "NULL",
                "wall": "NULL"}
    return {"labor": "m.labor_hours", "lane": "m.agent_lane_hours", "wall": "m.wall_clock_hours"}


def _json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=_json_default)


def _json_default(value: Any) -> Any:
    if isinstance(value, set):
        return sorted(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"not JSON-serializable: {type(value).__name__}")


def write_events(conn: sqlite3.Connection, events: Iterable[Event]) -> int:
    rows = []
    for ev in events:
        meta = dict(ev.metadata or {})
        words = meta.get("words")
        rows.append(
            (
                ev.event_id,
                ev.ts,
                ev.actor,
                ev.project,
                ev.session_id,
                ev.source_system,
                ev.source_machine,
                ev.kind,
                ev.seq,
                ev.content_snippet,
                int(words) if isinstance(words, int) else None,
                _json(meta) if meta else None,
            )
        )
    conn.executemany(
        """INSERT OR REPLACE INTO events
           (event_id, ts, actor, project, session_id, source_system,
            source_machine, kind, seq, content_snippet, words, metadata_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    return len(rows)


def write_tasks(
    conn: sqlite3.Connection,
    tasks: list[Task],
    events: list[Event],
) -> None:
    task_rows = []
    task_event_rows = []
    for t in tasks:
        rule_counts: dict[str, int] = {}
        user_msgs = 0
        first_ts = None
        last_ts = None
        seen_event_ids: set[str] = set()
        summary = ""
        for step in t.segmentation_trace:
            rule = step.get("rule", "?")
            rule_counts[rule] = rule_counts.get(rule, 0) + 1
            idx = step.get("event_idx")
            # idx == -1 is a sentinel on bookkeeping trace steps (merged_from,
            # merged_pr_unit, split_to_pr_units, id carry-forward); Python
            # negative-indexing would resolve it to events[-1] (the
            # chronologically-last event), falsely stamping that event onto the
            # task and dragging its last_ts to the end of the window.
            if idx is None or idx < 0 or idx >= len(events):
                continue
            ev = events[idx]
            if ev.event_id in seen_event_ids:
                continue
            seen_event_ids.add(ev.event_id)
            task_event_rows.append((t.task_id, ev.event_id, step.get("rule"), step.get("detail")))
            if ev.kind == "user_message":
                user_msgs += 1
            if first_ts is None or ev.ts < first_ts:
                first_ts = ev.ts
            if last_ts is None or ev.ts > last_ts:
                last_ts = ev.ts
        task_rows.append(
            (
                t.task_id,
                t.project,
                t.title,
                summary,
                first_ts,
                last_ts,
                user_msgs,
                len(seen_event_ids),
                _json(sorted(t.issue_refs)) if t.issue_refs else None,
                _json(rule_counts),
                1 if getattr(t, "delegated", False) else 0,
                getattr(t, "board_task", None),
                getattr(t, "unit_kind", "") or "",
                getattr(t, "link_source", "") or "",
            )
        )
    conn.executemany(
        """INSERT OR REPLACE INTO tasks
           (task_id, project, title, summary, first_ts, last_ts,
            user_msg_count, event_count, issue_refs_json, rule_counts_json,
            delegated, board_task, unit_kind, board_task_source)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        task_rows,
    )
    conn.executemany(
        """INSERT OR REPLACE INTO task_events
           (task_id, event_id, rule, detail) VALUES (?,?,?,?)""",
        task_event_rows,
    )


def write_board_tasks(conn: sqlite3.Connection, facts: dict[str, dict[str, Any]],
                      calendar: dict[str, dict[str, Any]] | None = None) -> int:
    """`calendar` maps board task → {first_agent_ts, done_ts, wall_clock_hours}
    (see calendar_time.board_task_wall_clock); absent keys write NULL."""
    calendar = calendar or {}
    conn.executemany(
        """INSERT OR REPLACE INTO board_tasks (board_task, creator_kind, his_review, his_dictation, his_last_ask, goal,
                                             first_agent_ts, done_ts, wall_clock_hours)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        [(bt, f.get("creator_kind"), int(bool(f.get("his_review"))), int(bool(f.get("his_dictation"))),
          datetime.fromtimestamp(f["his_last_ask"] / 1000, tz=timezone.utc).isoformat()
          if f.get("his_last_ask") else None,
          f.get("goal") or None,
          (calendar.get(bt) or {}).get("first_agent_ts"),
          (calendar.get(bt) or {}).get("done_ts"),
          (calendar.get(bt) or {}).get("wall_clock_hours"))
         for bt, f in facts.items()],
    )
    return len(facts)


def write_artifact_credit(
    conn: sqlite3.Connection,
    run_id: str,
    credit,  # ArtifactCredit; untyped to avoid a circular import at module load
    tasks: list[Task],
    events: list[Event],
) -> int:
    """Persist the contested artifact identities (>1 holding task) for audit.

    Single-holder identities are omitted — they carry no dedup decision.
    """
    # Rebuild identity -> {task_id: [event ids]} to know each identity's
    # duplicate placements (ArtifactCredit stores duplicates per task).
    from .artifact_credit import artifact_identity

    dup_tasks_by_identity: dict[str, set[str]] = {}
    for t in tasks:
        dup_idx = credit.duplicates_by_task.get(t.task_id)
        if not dup_idx:
            continue
        for i in dup_idx:
            if i < len(events):
                ident = artifact_identity(events[i])
                if ident:
                    dup_tasks_by_identity.setdefault(ident, set()).add(t.task_id)

    rows = [
        (run_id, ident, credit.owners.get(ident, ""), _json(sorted(dups)))
        for ident, dups in sorted(dup_tasks_by_identity.items())
    ]
    conn.executemany(
        """INSERT OR REPLACE INTO artifact_credit
           (run_id, identity, owner_task_id, duplicate_task_ids_json)
           VALUES (?,?,?,?)""",
        rows,
    )
    return len(rows)


def write_enriched(
    conn: sqlite3.Connection,
    run_id: str,
    enriched: dict[str, EnrichedTask],
    statuses: dict[str, str],
) -> None:
    rows = []
    for tid, et in enriched.items():
        dims = et.dimensions or {}
        rows.append(
            (
                tid,
                run_id,
                et.title,
                et.goal,
                et.summary,
                float(et.est_human_hours),
                et.uncertainty,
                et.bryan_domain_expertise,
                dims.get("scope"),
                dims.get("depth"),
                dims.get("type"),
                et.rationale,
                statuses.get(tid),
                et.effort_breakdown,
                et.bryan_role_summary,
                et.what_went_well,
                et.problems_encountered,
            )
        )
    conn.executemany(
        """INSERT OR REPLACE INTO enriched
           (task_id, run_id, title, goal, summary, est_human_hours, uncertainty,
            bryan_domain_expertise, scope, depth, type, rationale, status,
            effort_breakdown, bryan_role_summary, what_went_well, problems_encountered)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    # Mirror the LLM-written summary onto tasks.summary so it's the canonical
    # description even when queried without joining enriched.
    for tid, et in enriched.items():
        if et.summary:
            conn.execute(
                "UPDATE tasks SET summary = ? WHERE task_id = ?",
                (et.summary, tid),
            )


def write_enrichment_calls(
    conn: sqlite3.Connection,
    run_id: str,
    calls: Iterable[EnrichmentCall],
    called_at: str | None = None,
) -> int:
    called_at = called_at or datetime.now(timezone.utc).isoformat()
    rows = []
    for c in calls:
        rows.append(
            (
                c.task_id,
                run_id,
                c.model,
                c.prompt_system,
                c.prompt_user,
                c.response_raw,
                _json(c.response_parsed),
                c.input_tokens,
                c.output_tokens,
                c.cache_read_tokens,
                c.cache_creation_tokens,
                called_at,
            )
        )
    conn.executemany(
        """INSERT INTO enrichment_calls
           (task_id, run_id, model, prompt_system, prompt_user, response_raw,
            response_parsed_json, input_tokens, output_tokens,
            cache_read_tokens, cache_creation_tokens, called_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    return len(rows)


def write_task_metrics(
    conn: sqlite3.Connection,
    run_id: str,
    active_hours: dict[str, float],
    agent_hours: dict[str, float],
    resumptions: dict[str, int],
    labor_hours: dict[str, float] | None = None,
    agent_busy_hours: dict[str, float] | None = None,
    hands_on_blocks: dict[str, int] | None = None,
    human_input_chars: dict[str, int] | None = None,
    dominant_model: dict[str, str] | None = None,
    agent_lane_hours: dict[str, float] | None = None,
    wall_clock_hours: dict[str, float] | None = None,
) -> None:
    """`labor_hours`: user ∪ agent, pauses removed. `wall_clock_hours`:
    calendar time, first agent work to last event. `agent_lane_hours`: per-lane
    agent time summed."""
    labor_hours = labor_hours or {}
    agent_busy_hours = agent_busy_hours or {}
    hands_on_blocks = hands_on_blocks or {}
    human_input_chars = human_input_chars or {}
    dominant_model = dominant_model or {}
    agent_lane_hours = agent_lane_hours or {}
    wall_clock_hours = wall_clock_hours or {}
    task_ids = (
        set(active_hours) | set(agent_hours) | set(resumptions)
        | set(labor_hours) | set(agent_busy_hours) | set(agent_lane_hours) | set(wall_clock_hours)
    )
    rows = [
        (
            tid,
            run_id,
            float(active_hours.get(tid, 0.0)),
            float(agent_hours.get(tid, 0.0)),
            int(resumptions.get(tid, 0)),
            float(wall_clock_hours.get(tid, 0.0)),
            float(agent_busy_hours.get(tid, 0.0)),
            int(hands_on_blocks.get(tid, 0)),
            int(human_input_chars.get(tid, 0)),
            str(dominant_model.get(tid, "")),
            float(labor_hours.get(tid, 0.0)),
            float(agent_lane_hours.get(tid, 0.0)),
        )
        for tid in task_ids
    ]
    conn.executemany(
        """INSERT OR REPLACE INTO task_metrics
           (task_id, run_id, active_hours, agent_active_hours, resumptions,
            wall_clock_hours, agent_busy_hours,
            hands_on_blocks, human_input_chars, dominant_model,
            labor_hours, agent_lane_hours)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )


def write_run_meta(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    schema_version: str,
    period_start: str,
    period_end: str,
    cognitive_wall_clock_hours: float,
    context_switches: int,
    coverage: dict[str, Any],
    created_at: str | None = None,
) -> None:
    created_at = created_at or datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT OR REPLACE INTO run_meta
           (run_id, schema_version, period_start, period_end,
            cognitive_wall_clock_hours, context_switches,
            coverage_percent, coverage_assigned, coverage_total, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id,
            schema_version,
            period_start,
            period_end,
            float(cognitive_wall_clock_hours),
            int(context_switches),
            float(coverage.get("coverage_percent", 0.0)),
            int(coverage.get("assigned", 0)),
            int(coverage.get("user_message_events", 0)),
            created_at,
        ),
    )


def write_estimate_corrections(
    conn: sqlite3.Connection, run_id: str, rows
) -> None:
    """Record estimates this run replaced: (task_id, kind, before_h, after_h)."""
    conn.executemany(
        "INSERT OR REPLACE INTO estimate_corrections "
        "(run_id, task_id, kind, before_h, after_h) VALUES (?, ?, ?, ?, ?)",
        [(run_id, t, k, float(b), float(a)) for t, k, b, a in rows],
    )


def record_review(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    run_id: str,
    bryan_est_hours: float,
    llm_est_hours: float | None = None,
    confidence: str | None = None,
    note: str | None = None,
    reviewed_at: str | None = None,
) -> int:
    reviewed_at = reviewed_at or datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO estimate_reviews
           (task_id, run_id, reviewed_at, llm_est_hours, bryan_est_hours,
            confidence, note)
           VALUES (?,?,?,?,?,?,?)""",
        (task_id, run_id, reviewed_at, llm_est_hours, float(bryan_est_hours), confidence, note),
    )
    return cur.lastrowid


def load_reviews(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cur = conn.execute(
        """SELECT task_id, run_id, reviewed_at, llm_est_hours, bryan_est_hours,
                  confidence, note
           FROM estimate_reviews"""
    )
    return [dict(row) for row in cur]
