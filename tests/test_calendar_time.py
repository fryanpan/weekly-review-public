"""Calendar wall clock and speedup (method Step 7, 2026-09-18).

`wall_clock_hours` is calendar time: 168 for a week; per board task, first
agent work to the done transition. Speedup = (est_h / 8) ÷ (wall / 24). The
figure the column held before — the user ∪ agent with pauses removed — is
`labor_hours`, and a v1 run db is still read under that meaning.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from weekly_review import persist
from weekly_review.calendar_time import (
    board_task_speedups, board_task_wall_clock, done_ts_by_board_task, median_speedup,
    speedup, week_wall_clock_hours,
)
from weekly_review.digest import render_digest
from weekly_review.effort import wall_clock_hours_by_task
from weekly_review.enrichment import EnrichedTask
from weekly_review.events import Event
from weekly_review.segmentation import Task

SRC = Path(__file__).resolve().parents[1] / "src" / "weekly_review"


def _ev(kind, actor, ts, seq, **meta):
    return Event(
        ts=ts, actor=actor, project="p", session_id="s",
        source_system="claude_transcript", source_machine="mm",
        kind=kind, seq=seq, content_snippet="x" * 10, metadata={"words": 10, **meta},
    )


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


# --- the formula -------------------------------------------------------------

def test_speedup_is_expert_days_over_calendar_days():
    # 16h of expert work is 2 expert days; done in 24 calendar hours = 1 day.
    assert speedup(16.0, 24.0) == pytest.approx(2.0)
    # A week: est / 56.
    assert speedup(1095.0, 168.0) == pytest.approx(1095.0 / 56)


def test_speedup_is_none_when_it_cannot_be_computed():
    assert speedup(4.0, 0.0) is None
    assert speedup(0.0, 10.0) is None


def test_a_week_is_168_hours_with_an_inclusive_end_date():
    assert week_wall_clock_hours(datetime(2026, 9, 7), datetime(2026, 9, 13)) == 168.0
    assert week_wall_clock_hours(datetime(2026, 9, 7), datetime(2026, 9, 7)) == 24.0


# --- per unit ------------------------------------------------------------------

def test_unit_wall_clock_runs_from_first_agent_work_to_last_event_pauses_included():
    events = [
        _ev("user_message", "user", "2026-09-08T09:00:00Z", 0),   # his ask: not the start
        _ev("agent_message", "agent", "2026-09-08T10:00:00Z", 1),  # first agent work
        _ev("agent_message", "agent", "2026-09-08T10:05:00Z", 2),
        _ev("user_message", "user", "2026-09-09T10:00:00Z", 3),   # a day later: counted
    ]
    task = Task(task_id="t1", project="p", title="t", event_ids=[0, 1, 2, 3])
    assert wall_clock_hours_by_task([task], events)["t1"] == pytest.approx(24.0)


def test_unit_with_no_agent_work_reads_zero():
    events = [_ev("user_message", "user", "2026-09-08T09:00:00Z", 0),
              _ev("user_message", "user", "2026-09-08T12:00:00Z", 1)]
    task = Task(task_id="t1", project="p", title="t", event_ids=[0, 1])
    assert wall_clock_hours_by_task([task], events)["t1"] == 0.0


def test_a_board_action_is_an_instant_not_agent_work():
    # A stamped board transition by an agent must not start the clock.
    events = [
        _ev("board_action", "agent", "2026-09-07T08:00:00Z", 0, action="task.claimed"),
        _ev("agent_message", "agent", "2026-09-08T10:00:00Z", 1),
        _ev("agent_message", "agent", "2026-09-08T11:00:00Z", 2),
    ]
    task = Task(task_id="t1", project="p", title="t", event_ids=[0, 1, 2])
    assert wall_clock_hours_by_task([task], events)["t1"] == pytest.approx(1.0)


# --- per board task ------------------------------------------------------------

def _bt(id_, transitions):
    return SimpleNamespace(id=id_, transitions=tuple(transitions))


def test_done_ts_is_the_last_done_transition():
    bts = [_bt("t-A", [(_ms("2026-09-07T00:00:00Z"), "todo"),
                       (_ms("2026-09-08T12:00:00Z"), "done"),
                       (_ms("2026-09-09T00:00:00Z"), "in-progress"),
                       (_ms("2026-09-10T12:00:00Z"), "done")]),
           _bt("t-B", [(_ms("2026-09-07T00:00:00Z"), "in-progress")])]
    done = done_ts_by_board_task(bts)
    assert done == {"t-A": _ms("2026-09-10T12:00:00Z")}


def test_board_task_span_is_first_agent_work_across_its_units_to_done():
    events = [
        _ev("agent_message", "agent", "2026-09-08T10:00:00Z", 0),  # unit 1, first
        _ev("agent_message", "agent", "2026-09-09T10:00:00Z", 1),  # unit 2, later
    ]
    u1 = Task(task_id="u1", project="p", title="a", event_ids=[0], board_task="t-A")
    u2 = Task(task_id="u2", project="p", title="b", event_ids=[1], board_task="t-A")
    done = {"t-A": _ms("2026-09-10T10:00:00Z")}
    cal = board_task_wall_clock([u1, u2], events, done)
    assert cal["t-A"]["wall_clock_hours"] == pytest.approx(48.0)
    assert cal["t-A"]["first_agent_ts"].startswith("2026-09-08T10:00")
    assert cal["t-A"]["done_ts"].startswith("2026-09-10T10:00")


def test_a_board_task_not_done_or_without_agent_work_has_no_span_not_a_zero():
    events = [_ev("agent_message", "agent", "2026-09-08T10:00:00Z", 0),
              _ev("user_message", "user", "2026-09-08T10:00:00Z", 1)]
    open_ = Task(task_id="u1", project="p", title="a", event_ids=[0], board_task="t-open")
    his = Task(task_id="u2", project="p", title="b", event_ids=[1], board_task="t-his")
    cal = board_task_wall_clock([open_, his], events, {"t-his": _ms("2026-09-09T00:00:00Z")})
    assert cal["t-open"]["wall_clock_hours"] is None and cal["t-open"]["first_agent_ts"]
    assert cal["t-his"]["wall_clock_hours"] is None and cal["t-his"]["first_agent_ts"] is None
    assert cal["t-his"]["done_ts"]


def test_board_task_rows_sum_est_over_units_and_take_the_median():
    u1 = Task(task_id="u1", project="p", title="a", event_ids=[], board_task="t-A")
    u2 = Task(task_id="u2", project="p", title="b", event_ids=[], board_task="t-A")
    u3 = Task(task_id="u3", project="p", title="c", event_ids=[], board_task="t-B")
    cal = {"t-A": {"wall_clock_hours": 24.0, "first_agent_ts": "x", "done_ts": "y"},
           "t-B": {"wall_clock_hours": None, "first_agent_ts": "x", "done_ts": None}}
    rows = board_task_speedups([u1, u2, u3], {"u1": 8.0, "u2": 8.0, "u3": 100.0}, cal)
    by = {r["board_task"]: r for r in rows}
    assert by["t-A"]["units"] == 2 and by["t-A"]["est_h"] == 16.0
    assert by["t-A"]["speedup"] == pytest.approx(2.0)
    assert by["t-B"]["speedup"] is None
    med, n = median_speedup(rows)
    assert (med, n) == (pytest.approx(2.0), 1)
    assert rows[0]["board_task"] == "t-B", "sorted by est, the unmeasured one still listed"


# --- the digest ----------------------------------------------------------------

def _digest(**kw):
    events = [_ev("user_message", "user", "2026-09-08T09:00:00Z", 0)]
    task = Task(task_id="u1", project="p", title="a", event_ids=[0], board_task="t-A")
    et = EnrichedTask(task_id="u1", title="a", est_human_hours=56.0, uncertainty="low",
                      bryan_domain_expertise="expert",
                      dimensions={"scope": "s", "depth": "d", "type": "code"}, rationale="r")
    base = dict(
        period_start="2026-09-07", period_end="2026-09-13", tasks=[task],
        enriched={"u1": et}, statuses={"u1": "shipped"}, events=events,
        active_hours_by_task={"u1": 0.5}, agent_hours_by_task={"u1": 0.2},
        resumptions_by_task={"u1": 0},
        coverage={"user_message_events": 1, "assigned": 1, "unassigned": 0,
                  "coverage_percent": 100.0, "unassigned_by_project": {}},
        cognitive_wall_clock_hours=1.0, context_switches=0, last_calibrated_days=None,
        labor_hours_by_task={"u1": 3.25},
        board_task_calendar={"t-A": {"wall_clock_hours": 12.0, "first_agent_ts": "x", "done_ts": "y"}},
    )
    base.update(kw)
    return render_digest(**base)


def test_digest_prints_labor_wall_clock_and_speedup_for_the_week():
    md = _digest()
    line = next(l for l in md.splitlines() if l.startswith("- Week:"))
    assert "labor_hours 3.25h" in line
    assert "wall_clock_hours 168h (7 calendar days)" in line
    assert "speedup 1.0x" in line          # 56h / 56
    assert "(= est / 56h: 7 days × 8 expert hours)" in line


def test_digest_prints_a_row_per_board_task_with_all_four_figures():
    md = _digest()
    row = next(l for l in md.splitlines() if l.startswith("| `t-A`"))
    # est 56h over 12 calendar hours: (56/8) / (12/24) = 14x
    assert row == "| `t-A` | 1 | 56.00h | 3.25h | 12.0h | 14.0x |"
    assert "median speedup 14.0x (n=1)" in md


def test_digest_says_not_done_rather_than_printing_a_zero_span():
    md = _digest(board_task_calendar={"t-A": {"wall_clock_hours": None, "first_agent_ts": "x", "done_ts": None}})
    row = next(l for l in md.splitlines() if l.startswith("| `t-A`"))
    assert "| not done | n/a |" in row
    assert "1 not done by the window's end" in md


def test_digest_methodology_footer_states_the_formula_and_both_names():
    md = _digest()
    footer = md.split("## Methodology")[1]
    assert "(est_h / 8) ÷ (wall_clock_hours / 24)" in footer
    assert "168 for a week" in footer
    assert "labor_hours" in footer and "pauses removed" in footer


# --- the run db: v1 read under the old meaning, v2 under the new -------------

def test_metric_columns_reads_a_v1_db_under_its_old_meaning():
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE run_meta (run_id TEXT, schema_version TEXT);
        INSERT INTO run_meta VALUES ('r', 'v1');
        CREATE TABLE task_metrics (task_id TEXT, active_hours REAL, wall_clock_hours REAL, agent_labor_hours REAL);
    """)
    assert persist.metric_columns(c) == {"labor": "m.wall_clock_hours", "lane": "m.agent_labor_hours", "wall": "NULL"}


def test_metric_columns_on_a_v2_db_and_on_a_fixture_without_run_meta():
    c = sqlite3.connect(":memory:")
    persist.init_schema(c)
    assert persist.metric_columns(c) == {"labor": "m.labor_hours", "lane": "m.agent_lane_hours", "wall": "m.wall_clock_hours"}
    f = sqlite3.connect(":memory:")
    f.execute("CREATE TABLE task_metrics (task_id TEXT, active_hours REAL, labor_hours REAL)")
    assert persist.metric_columns(f)["labor"] == "m.labor_hours"


def test_write_task_metrics_stores_labor_lane_and_calendar_wall_separately():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    persist.init_schema(c)
    persist.write_task_metrics(
        c, "r", {"t": 1.0}, {"t": 2.0}, {"t": 0},
        labor_hours={"t": 3.0}, agent_lane_hours={"t": 4.0}, wall_clock_hours={"t": 48.0},
    )
    r = c.execute("SELECT labor_hours, agent_lane_hours, wall_clock_hours FROM task_metrics").fetchone()
    assert tuple(r) == (3.0, 4.0, 48.0)


def test_write_board_tasks_stores_the_calendar_span():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    persist.init_schema(c)
    persist.write_board_tasks(c, {"t-A": {"creator_kind": "person"}},
                              calendar={"t-A": {"first_agent_ts": "a", "done_ts": "b", "wall_clock_hours": 5.5}})
    r = c.execute("SELECT first_agent_ts, done_ts, wall_clock_hours FROM board_tasks").fetchone()
    assert tuple(r) == ("a", "b", 5.5)


def test_export_speedup_uses_the_step_7_formula_and_is_null_on_v1_runs():
    from weekly_review import export
    assert "labor_hours" in export.COLUMNS and "agent_lane_hours" in export.COLUMNS
    assert "agent_labor_hours" not in export.COLUMNS
    src = (SRC / "export.py").read_text()
    assert "speedup(est, wall)" in src
    assert "metric_columns(conn)" in src


def test_cli_passes_labor_and_calendar_into_the_digest_and_the_db():
    """Reachability: a rename that leaves the digest reading the old figure
    under the new name is the defect this task exists to remove."""
    src = (SRC / "cli.py").read_text()
    assert 'SCHEMA_VERSION = "v2"' in src
    assert re.search(r"labor_h, agent_busy_h, \w+ = labor_and_agent_hours_by_task\(", src)
    assert "wall_h = wall_clock_hours_by_task(tasks, all_events)" in src
    assert "labor_hours=labor_h" in src and "agent_lane_hours=agent_lane_h" in src
    assert "wall_clock_hours=wall_h" in src
    assert "labor_hours_by_task=labor_h" in src and "board_task_calendar=_bt_calendar" in src
    assert "calendar=_bt_calendar" in src


def test_no_source_file_reads_wall_clock_hours_under_the_old_meaning():
    """Every reader of a run db resolves labor through persist.metric_columns;
    the only literal `m.wall_clock_hours` / `agent_labor_hours` left is inside it."""
    hits = []
    for path in list(SRC.rglob("*.py")) + list((SRC.parents[1] / "scripts").glob("*.py")):
        if path.name == "persist.py":
            continue
        for i, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if "agent_labor_hours" in code or re.search(r"m\.wall_clock_hours", code):
                hits.append(f"{path.name}:{i}: {line.strip()}")
    assert hits == [], hits


# --- review fixes (2026-09-18) ---------------------------------------------------

def test_a_done_transition_after_the_window_does_not_count():
    bts = [_bt("t-A", [(_ms("2026-09-08T00:00:00Z"), "in-progress"),
                       (_ms("2026-09-15T00:00:00Z"), "done")])]
    assert done_ts_by_board_task(bts, until_ms=_ms("2026-09-13T23:59:59Z")) == {}
    assert done_ts_by_board_task(bts) == {"t-A": _ms("2026-09-15T00:00:00Z")}


def test_done_before_its_first_agent_work_here_is_no_span_not_zero():
    events = [_ev("agent_message", "agent", "2026-09-10T10:00:00Z", 0)]
    u = Task(task_id="u1", project="p", title="a", event_ids=[0], board_task="t-A")
    cal = board_task_wall_clock([u], events, {"t-A": _ms("2026-09-09T10:00:00Z")})
    assert cal["t-A"]["wall_clock_hours"] is None
    rows = board_task_speedups([u], {"u1": 2.0}, cal)
    assert rows[0]["why_no_span"] == "done before its agent work"
    md = _digest(board_task_calendar=cal)
    assert "| done before its agent work | n/a |" in md
    assert "1 done before their agent work here" in md


def test_a_v1_db_opened_by_connect_is_still_read_as_v1(tmp_path):
    db = tmp_path / "run.sqlite"
    c = sqlite3.connect(db)
    c.executescript("""
        CREATE TABLE run_meta (run_id TEXT, schema_version TEXT);
        CREATE TABLE task_metrics (task_id TEXT, run_id TEXT, active_hours REAL,
            agent_active_hours REAL, resumptions INT, wall_clock_hours REAL,
            agent_busy_hours REAL, hands_on_blocks INT, human_input_chars INT,
            dominant_model TEXT, agent_labor_hours REAL);
        INSERT INTO task_metrics VALUES ('t', 'r', 1, 1, 0, 7.0, 1, 0, 0, '', 9.0);
    """)
    c.commit(); c.close()
    with persist.connect(db) as conn:  # _migrate adds empty labor_hours / agent_lane_hours
        cols = {r[1] for r in conn.execute("PRAGMA table_info(task_metrics)")}
        assert "labor_hours" in cols
        mc = persist.metric_columns(conn)  # run_meta empty: decided by columns
        assert mc == {"labor": "m.wall_clock_hours", "lane": "m.agent_labor_hours", "wall": "NULL"}
        row = conn.execute(f"SELECT {mc['labor']}, {mc['lane']} FROM task_metrics m").fetchone()
        assert tuple(row) == (7.0, 9.0)


def test_worksheet_never_labels_labor_as_wall_clock():
    src = (SRC.parents[1] / "scripts" / "make_label_worksheet.py").read_text()
    rendered = [l for l in src.splitlines() if ("wall-clock" in l or "wall clock" in l) and '"' in l]
    for l in rendered:
        assert "calendar" in l or "2026-09-18" in l or "wall-clock speedup, which" in l, l
