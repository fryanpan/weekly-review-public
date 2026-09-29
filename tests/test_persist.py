"""Persistence (SQLite) integration tests.

Goal: prove the full pipeline — events → tasks → enriched → metrics → reviews
— round-trips through SQLite with FK integrity.
"""
from __future__ import annotations

import sqlite3

import pytest

from weekly_review import persist
from weekly_review.enrichment import EnrichedTask, EnrichmentCall
from weekly_review.events import Event
from weekly_review.segmentation import segment


def _uev(ts: str, project: str, text: str, session_id: str, seq: int) -> Event:
    return Event(
        ts=ts, actor="user", project=project, session_id=session_id,
        source_system="claude_transcript", source_machine="mm",
        kind="user_message", seq=seq,
        content_snippet=text, metadata={"words": len(text.split())},
    )


def _card(ts: str, project: str, text: str, session_id: str, seq: int, card: str = "t-AAA") -> Event:
    """His comment on a board task: the one message shape that still starts a
    task (a plain transcript message is placed by nothing)."""
    return Event(
        ts=ts, actor="user", project=project, session_id=session_id,
        source_system="live_feedback", source_machine="mm",
        kind="user_message", seq=seq,
        content_snippet=text, metadata={"words": len(text.split()), "task_id": card},
    )


def _aev(ts: str, project: str, text: str, session_id: str, seq: int) -> Event:
    return Event(
        ts=ts, actor="agent", project=project, session_id=session_id,
        source_system="claude_transcript", source_machine="mm",
        kind="agent_message", seq=seq,
        content_snippet=text, metadata={"words": len(text.split()), "model": "test"},
    )


def test_event_id_is_stable_and_unique():
    e1 = _uev("2026-04-15T10:00:00Z", "wr", "hi", "s1", 0)
    e1b = _uev("2026-04-15T10:00:00Z", "wr", "hi", "s1", 0)
    e2 = _uev("2026-04-15T10:00:00Z", "wr", "hi", "s1", 1)
    assert e1.event_id == e1b.event_id
    assert e1.event_id != e2.event_id
    assert e1.event_id == "claude_transcript:s1:0"


def test_stable_task_id_survives_resegmentation():
    events = [
        _card("2026-04-15T10:00:00Z", "wr", "digest renderer", "s1", 0),
        _card("2026-04-15T11:00:00Z", "wr", "more digest work", "s1", 1),
    ]
    t1 = segment(events)
    t2 = segment(list(events))
    assert len(t1) == 1 and len(t2) == 1
    assert t1[0].task_id == t2[0].task_id


def test_persist_full_pipeline(tmp_path):
    events = [
        _uev("2026-04-15T10:00:00Z", "wr", "let's build the digest", "s1", 0),
        _aev("2026-04-15T10:01:00Z", "wr", "sure, here's a plan", "s1", 1),
        _uev("2026-04-15T10:05:00Z", "wr", "looks good, go", "s1", 2),
    ]
    tasks = segment(events)
    assert tasks, "segmenter must produce at least one task"
    tid = tasks[0].task_id

    enriched = {
        tid: EnrichedTask(
            task_id=tid,
            title="Build the digest",
            est_human_hours=4.0,
            uncertainty="medium",
            bryan_domain_expertise="expert",
            dimensions={"scope": "single-feature", "depth": "careful design", "type": "code"},
            rationale="typical feature sized task",
            summary="Goal: build digest. Outcome: shipped v1 renderer.",
        )
    }
    call = EnrichmentCall(
        task_id=tid, model="claude-sonnet-4-6",
        prompt_system="sys", prompt_user="user",
        response_raw='{"title":"Build the digest"}',
        response_parsed={"title": "Build the digest", "est_human_hours": 4.0},
        input_tokens=100, output_tokens=50,
    )

    db_path = tmp_path / "run.sqlite"
    with persist.connect(db_path) as conn:
        persist.write_events(conn, events)
        persist.write_tasks(conn, tasks, events)
        persist.write_enriched(conn, "run-test", enriched, {tid: "shipped"})
        persist.write_enrichment_calls(conn, "run-test", [call])
        persist.write_task_metrics(conn, "run-test", {tid: 0.5}, {tid: 0.1}, {tid: 0})
        persist.write_run_meta(
            conn,
            run_id="run-test",
            schema_version="v1",
            period_start="2026-04-14",
            period_end="2026-04-15",
            cognitive_wall_clock_hours=1.5,
            context_switches=0,
            coverage={"coverage_percent": 100.0, "assigned": 2, "user_message_events": 2},
        )

    # Re-open to verify durability and query shape
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        n_events = conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]
        assert n_events == 3

        n_tasks = conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"]
        assert n_tasks == 1

        # FK: every task_event row must reference a real event + task
        orphans = conn.execute(
            """SELECT COUNT(*) AS n FROM task_events te
               WHERE NOT EXISTS (SELECT 1 FROM events  e WHERE e.event_id = te.event_id)
                  OR NOT EXISTS (SELECT 1 FROM tasks   t WHERE t.task_id  = te.task_id)"""
        ).fetchone()["n"]
        assert orphans == 0

        # Enriched row joins back via task_id
        row = conn.execute(
            """SELECT t.title AS task_title, e.title AS llm_title, e.summary, e.scope,
                      e.est_human_hours, e.status
               FROM tasks t JOIN enriched e USING (task_id)"""
        ).fetchone()
        assert row["llm_title"] == "Build the digest"
        assert row["scope"] == "single-feature"
        assert row["status"] == "shipped"
        assert row["summary"].startswith("Goal: build digest")

        # tasks.summary got updated from the LLM summary
        t_summary = conn.execute(
            "SELECT summary FROM tasks WHERE task_id = ?", (tid,)
        ).fetchone()["summary"]
        assert t_summary.startswith("Goal: build digest")

        # Enrichment call captured the prompt + response
        call_row = conn.execute(
            "SELECT prompt_system, response_raw FROM enrichment_calls WHERE task_id = ?", (tid,)
        ).fetchone()
        assert call_row["prompt_system"] == "sys"
        assert "Build the digest" in call_row["response_raw"]

        # Metrics row exists
        m = conn.execute(
            "SELECT active_hours, agent_active_hours FROM task_metrics WHERE task_id = ?", (tid,)
        ).fetchone()
        assert m["active_hours"] == 0.5

        # Run meta recorded
        rm = conn.execute("SELECT coverage_percent FROM run_meta WHERE run_id = 'run-test'").fetchone()
        assert rm["coverage_percent"] == 100.0


def test_record_review_and_join_back(tmp_path):
    events = [_card("2026-04-15T10:00:00Z", "wr", "hello", "s1", 0)]
    tasks = segment(events)
    tid = tasks[0].task_id

    db_path = tmp_path / "run.sqlite"
    with persist.connect(db_path) as conn:
        persist.write_events(conn, events)
        persist.write_tasks(conn, tasks, events)
        persist.write_enriched(
            conn, "run-1",
            {tid: EnrichedTask(
                task_id=tid, title="t", est_human_hours=10.0,
                uncertainty="high", bryan_domain_expertise="learning",
                dimensions={}, rationale="", summary="",
            )},
            {tid: "open"},
        )
        rid = persist.record_review(
            conn, task_id=tid, run_id="run-1",
            bryan_est_hours=3.5, llm_est_hours=10.0,
            confidence="medium", note="LLM overestimated complexity",
        )
        assert rid > 0

    # Review joins back to the task & enriched rows cleanly.
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT r.bryan_est_hours, r.llm_est_hours, e.title, e.est_human_hours
               FROM estimate_reviews r JOIN enriched e USING (task_id)"""
        ).fetchone()
        assert row["bryan_est_hours"] == 3.5
        assert row["llm_est_hours"] == 10.0
        assert row["title"] == "t"


def test_pairs_from_reviews_reads_multiple_dbs(tmp_path):
    from weekly_review.calibration import pairs_from_reviews

    def _make_db(path, llm_h, bryan_h):
        events = [_card("2026-04-15T10:00:00Z", "wr", "x", "s1", 0)]
        tasks = segment(events)
        tid = tasks[0].task_id
        with persist.connect(path) as conn:
            persist.write_events(conn, events)
            persist.write_tasks(conn, tasks, events)
            persist.record_review(
                conn, task_id=tid, run_id="r",
                bryan_est_hours=bryan_h, llm_est_hours=llm_h,
            )

    db1 = tmp_path / "r1" / "run.sqlite"
    db2 = tmp_path / "r2" / "run.sqlite"
    _make_db(db1, llm_h=10.0, bryan_h=2.0)
    _make_db(db2, llm_h=4.0, bryan_h=3.0)

    pairs = pairs_from_reviews([db1, db2, tmp_path / "missing.sqlite"])
    assert sorted(pairs) == [(4.0, 3.0), (10.0, 2.0)]


def test_pairs_from_reviews_skips_missing_table(tmp_path):
    from weekly_review.calibration import pairs_from_reviews

    # A legit sqlite file with no estimate_reviews table
    db = tmp_path / "bare.sqlite"
    with sqlite3.connect(str(db)) as conn:
        conn.execute("CREATE TABLE foo (x int)")
    # Should not raise
    assert pairs_from_reviews([db]) == []


def test_estimate_corrections_round_trip_and_re_running_a_run_replaces(tmp_path):
    """One row per (run, task, kind), and a re-write replaces rather than doubles.

    The key matters: a run that writes its corrections twice -- a retry, a
    resumed pass -- must not leave two rows saying different things about the
    same estimate, because the audit line sums `before_h` and would report a
    week's corrections at twice their size.
    """
    from weekly_review import persist

    db = tmp_path / "run.sqlite"
    with persist.connect(db) as conn:
        persist.write_estimate_corrections(conn, "r1", [
            ("t-a", "decision_only", 3.0, 0.02),
            ("t-b", "decision_only", 1.0, 0.5),
        ])
        persist.write_estimate_corrections(conn, "r1", [
            ("t-a", "decision_only", 3.0, 0.03),
        ])
    with sqlite3.connect(str(db)) as conn:
        rows = sorted(conn.execute(
            "SELECT task_id, kind, before_h, after_h FROM estimate_corrections"))
    assert rows == [("t-a", "decision_only", 3.0, 0.03),
                    ("t-b", "decision_only", 1.0, 0.5)]


def test_writing_no_corrections_is_not_an_error(tmp_path):
    """A run that enriched nothing still reaches the persist call."""
    from weekly_review import persist

    db = tmp_path / "run.sqlite"
    with persist.connect(db) as conn:
        persist.write_estimate_corrections(conn, "r1", [])
        assert conn.execute(
            "SELECT COUNT(*) FROM estimate_corrections").fetchone()[0] == 0
