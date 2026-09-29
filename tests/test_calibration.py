import sqlite3
from pathlib import Path

from weekly_review.calibration import (
    apply_bias_correction,
    carry_forward_ok,
    fit_bias,
    load_anchors,
    load_review_overrides,
    load_review_overrides_with_context,
    save_anchors,
)
from weekly_review import persist


def test_load_save_anchors_roundtrip(tmp_path: Path):
    path = tmp_path / "anchors.yaml"
    anchors = [{"title": "x", "est_human_hours": 4.0, "dimensions": {"type": "code"}}]
    save_anchors(anchors, path)
    loaded = load_anchors(path)
    assert loaded == anchors


def test_fit_bias_returns_ratio_above_min_n():
    # 5 pairs, ratio = 1.5 each (within trim bounds)
    pairs = [(2.0, 3.0)] * 5
    bias = fit_bias(pairs)
    assert abs(bias["multiplier"] - 1.5) < 1e-6
    assert bias["n"] == 5


def test_apply_bias_correction():
    bias = {"multiplier": 1.5, "n": 5}
    assert apply_bias_correction(2.0, bias) == 3.0


def test_fit_bias_empty_returns_identity():
    bias = fit_bias([])
    assert bias["multiplier"] == 1.0
    assert bias["n"] == 0


def test_fit_bias_below_min_n_returns_identity():
    """Regression: 3 reviews on outlier mega-tasks (ratio ~0.22) used to crush
    every estimate by 78%. Now requires N>=5 before applying any multiplier."""
    pairs = [(320.0, 90.0), (320.0, 70.0), (300.0, 50.0)]  # all ratios in trim range
    bias = fit_bias(pairs)
    assert bias["multiplier"] == 1.0
    # The trim filter drops these (ratios 0.28, 0.22, 0.17 all below 0.5)
    assert bias["n"] == 0
    assert bias["trimmed"] == 3


def test_fit_bias_trims_extreme_ratios():
    # 5 ratios at 1.5, plus 2 extreme outliers (10x and 0.1x) — outliers should
    # not dominate the geometric mean.
    pairs = [(2.0, 3.0)] * 5 + [(1.0, 10.0), (10.0, 1.0)]
    bias = fit_bias(pairs)
    # The 5 in-range pairs survive; outliers trimmed
    assert bias["n"] == 5
    assert bias["trimmed"] == 2
    assert abs(bias["multiplier"] - 1.5) < 1e-6


def test_load_missing_file_returns_empty_list(tmp_path: Path):
    assert load_anchors(tmp_path / "does-not-exist.yaml") == []


def test_load_review_overrides_picks_latest_per_task(tmp_path: Path):
    db = tmp_path / "run.sqlite"
    with persist.connect(db) as conn:
        # No real tasks rows needed for this util — it queries estimate_reviews directly.
        persist.record_review(
            conn, task_id="taskA", run_id="r1",
            bryan_est_hours=10.0, llm_est_hours=20.0,
            reviewed_at="2026-04-01T00:00:00Z",
        )
        persist.record_review(
            conn, task_id="taskA", run_id="r2",
            bryan_est_hours=12.0, llm_est_hours=20.0,
            reviewed_at="2026-04-02T00:00:00Z",  # later
        )
        persist.record_review(
            conn, task_id="taskB", run_id="r1",
            bryan_est_hours=4.0, llm_est_hours=6.0,
            reviewed_at="2026-04-01T00:00:00Z",
        )
    overrides = load_review_overrides([db])
    # Latest review per task wins
    assert overrides == {"taskA": 12.0, "taskB": 4.0}


def test_load_review_overrides_skips_missing_dbs(tmp_path: Path):
    assert load_review_overrides([tmp_path / "missing.sqlite"]) == {}


def test_carry_forward_ok_gates_on_event_divergence():
    # Same-ish size -> carry.
    assert carry_forward_ok(100, 100) is True
    assert carry_forward_ok(100, 40) is True  # 2.5x, within 3x
    # A big-week task (2101 events) re-cut to a 100-event remnant -> don't carry.
    assert carry_forward_ok(2101, 100) is False
    # Growth is also divergence.
    assert carry_forward_ok(50, 300) is False
    # Unknown counts fail safe (don't carry).
    assert carry_forward_ok(None, 100) is False
    assert carry_forward_ok(100, None) is False
    assert carry_forward_ok(0, 100) is False


def test_load_review_overrides_with_context_captures_baseline_events(tmp_path: Path):
    db = tmp_path / "run.sqlite"
    with persist.connect(db) as conn:
        conn.execute(
            "INSERT INTO tasks (task_id, project, title, event_count) VALUES (?,?,?,?)",
            ("taskA", "proj", "big week", 2101),
        )
        persist.record_review(
            conn, task_id="taskA", run_id="r1",
            bryan_est_hours=80.0, llm_est_hours=80.0,
            reviewed_at="2026-04-26T00:00:00Z",
        )
        # A task with a review but no matching tasks row -> baseline None.
        persist.record_review(
            conn, task_id="taskB", run_id="r1",
            bryan_est_hours=4.0, llm_est_hours=6.0,
            reviewed_at="2026-04-26T00:00:00Z",
        )
    ctx = load_review_overrides_with_context([db])
    assert ctx["taskA"] == (80.0, 2101)
    assert ctx["taskB"] == (4.0, None)
    # The re-segmented remnant (100 events now) must NOT inherit the 80h verdict.
    hours, base_ec = ctx["taskA"]
    assert carry_forward_ok(base_ec, 100) is False


# --- pre-fix verdict quarantine (2026-07-28) ---------------------------------

def _q(override, reviewed_at, fresh, rationale):
    from weekly_review.calibration import quarantine_pre_fix_override
    return quarantine_pre_fix_override(override, reviewed_at, fresh, rationale)


def test_quarantines_inflated_pre_fix_verdict_over_self_consistent_fresh():
    # The May 18 case: 7h verdict over a fresh 0.5h whose rationale says 0.5h.
    drop, why = _q(7.0, "2026-06-20T10:00:00Z", 0.5, "A short Q&A with nothing built ≈ 0.5h.")
    assert drop is True
    assert "pre-fix verdict 7h" in why


def test_keeps_downward_correction():
    """The user cutting an inflated estimate is exactly what review is for."""
    drop, _ = _q(0.5, "2026-06-20T10:00:00Z", 6.0, "Big refactor ≈ 6h.")
    assert drop is False


def test_keeps_post_fix_verdict():
    drop, _ = _q(7.0, "2026-07-29T10:00:00Z", 0.5, "Nothing built ≈ 0.5h.")
    assert drop is False


def test_keeps_verdict_when_fresh_estimate_contradicts_its_own_rationale():
    """Never discard a human number in favour of a self-inconsistent LLM one."""
    drop, _ = _q(7.0, "2026-06-20T10:00:00Z", 0.5, "Substantial build ≈ 9h.")
    assert drop is False


def test_keeps_verdict_with_unparseable_rationale():
    drop, _ = _q(7.0, "2026-06-20T10:00:00Z", 0.5, "It was a quick one.")
    assert drop is False
    assert _q(7.0, "2026-06-20T10:00:00Z", 0.5, None)[0] is False


def test_keeps_verdict_below_the_ratio_bar():
    # 0.8h over a fresh 0.5h — mild, within normal reviewer disagreement.
    drop, _ = _q(0.8, "2026-06-20T10:00:00Z", 0.5, "Quick fix ≈ 0.5h.")
    assert drop is False


def test_keeps_verdict_with_missing_timestamp_or_zero_hours():
    assert _q(7.0, None, 0.5, "≈ 0.5h.")[0] is False
    assert _q(7.0, "2026-06-20T10:00:00Z", 0.0, "≈ 0.5h.")[0] is False
    assert _q(0.0, "2026-06-20T10:00:00Z", 0.5, "≈ 0.5h.")[0] is False


def test_rationale_hours_takes_the_concluding_figure():
    from weekly_review.calibration import rationale_hours
    assert rationale_hours("Roughly 2h of design, then build ≈ 6h.") == 6.0
    assert rationale_hours("no hours here") is None


def test_load_review_overrides_full_carries_timestamp(tmp_path: Path):
    from weekly_review.calibration import load_review_overrides_full
    db = tmp_path / "run.sqlite"
    with persist.connect(db) as conn:
        conn.execute(
            "INSERT INTO tasks (task_id, project, title, event_count) VALUES (?,?,?,?)",
            ("taskA", "proj", "t", 50),
        )
        persist.record_review(conn, task_id="taskA", run_id="r1", bryan_est_hours=7.0,
                              llm_est_hours=1.0, reviewed_at="2026-06-20T10:00:00Z")
    full = load_review_overrides_full([db])
    assert full["taskA"] == (7.0, 50, "2026-06-20T10:00:00Z", "")


# --- verdict on a unit whose kind changed (2026-09-18) -----------------------

def test_kind_unchanged_skips_a_verdict_when_the_units_kind_moved():
    from weekly_review.calibration import kind_unchanged
    # The 6eb59851ed case: reviewed as a conversation, now a coordination day.
    assert kind_unchanged("", "coordination") is False
    assert kind_unchanged("lane", "pr") is False
    # Same kind carries as before; '' and None-as-current both mean conversation.
    assert kind_unchanged("", "") is True
    assert kind_unchanged("lane", "lane") is True
    assert kind_unchanged("", None) is True
    # A recording run that predates unit_kind cannot be checked: carry.
    assert kind_unchanged(None, "coordination") is True


def test_load_review_overrides_full_carries_the_recorded_unit_kind(tmp_path: Path):
    from weekly_review.calibration import load_review_overrides_full
    db = tmp_path / "run.sqlite"
    with persist.connect(db) as conn:
        conn.execute(
            "INSERT INTO tasks (task_id, project, title, event_count, unit_kind) VALUES (?,?,?,?,?)",
            ("taskA", "proj", "t", 40, "lane"),
        )
        persist.record_review(conn, task_id="taskA", run_id="r1", bryan_est_hours=2.0,
                              llm_est_hours=1.0, reviewed_at="2026-09-10T10:00:00Z")
    assert load_review_overrides_full([db])["taskA"] == (2.0, 40, "2026-09-10T10:00:00Z", "lane")


def test_load_review_overrides_full_infers_kind_from_a_pre_kind_db(tmp_path: Path):
    """Every verdict the user recorded before 2026-09-18 sits in a run db with no
    unit_kind column. There, a non-delegated task was a conversation by
    construction, so it reads as ""; a delegated one could have been a lane,
    so it reads as None and the kind check abstains. Neither may crash."""
    import sqlite3
    from weekly_review.calibration import load_review_overrides_full
    db = tmp_path / "run.sqlite"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE tasks (task_id TEXT PRIMARY KEY, project TEXT, title TEXT,
            event_count INTEGER, delegated INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE estimate_reviews (task_id TEXT, run_id TEXT, reviewed_at TEXT,
            llm_est_hours REAL, bryan_est_hours REAL, confidence TEXT, note TEXT);
        INSERT INTO tasks VALUES ('conv', 'proj', 't', 40, 0);
        INSERT INTO tasks VALUES ('deleg', 'proj', 't', 300, 1);
        INSERT INTO estimate_reviews VALUES ('conv', 'r0', '2026-09-16T00:00:00Z', 0.3, 4.5, 'high', NULL);
        INSERT INTO estimate_reviews VALUES ('deleg', 'r0', '2026-09-16T00:00:00Z', 2.0, 3.0, 'high', NULL);
    """)
    conn.commit(); conn.close()
    full = load_review_overrides_full([db])
    assert full["conv"] == (4.5, 40, "2026-09-16T00:00:00Z", "")
    assert full["deleg"] == (3.0, 300, "2026-09-16T00:00:00Z", None)
    # `weekly-review review --run-id <old>` opens the db through persist.connect,
    # whose migration adds an EMPTY unit_kind column. That column must not be
    # read as recorded kinds: the delegated task would then read "" and a
    # later lane verdict would be dropped as "conversation -> lane".
    with persist.connect(db):
        pass
    cols = {r[1] for r in sqlite3.connect(str(db)).execute("pragma table_info(tasks)")}
    assert "unit_kind" in cols  # the migration did run
    assert load_review_overrides_full([db]) == full


def test_load_review_overrides_full_survives_a_db_with_neither_column(tmp_path: Path):
    import sqlite3
    from weekly_review.calibration import load_review_overrides_full
    db = tmp_path / "run.sqlite"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE tasks (task_id TEXT PRIMARY KEY, project TEXT, title TEXT, event_count INTEGER);
        CREATE TABLE estimate_reviews (task_id TEXT, run_id TEXT, reviewed_at TEXT,
            llm_est_hours REAL, bryan_est_hours REAL, confidence TEXT, note TEXT);
        INSERT INTO tasks VALUES ('taskA', 'proj', 't', 40);
        INSERT INTO estimate_reviews VALUES ('taskA', 'r0', '2026-06-01T00:00:00Z', 1.0, 3.0, 'high', NULL);
    """)
    conn.commit(); conn.close()
    assert load_review_overrides_full([db])["taskA"] == (3.0, 40, "2026-06-01T00:00:00Z", None)


def test_cli_override_loop_checks_unit_kind_before_applying_a_verdict():
    """The defect this guards against is a verdict applied to a unit of another
    kind. The loader returning the kind proves nothing unless the call site
    reads it, so assert the call site: the kind check must sit between the
    override lookup and the assignment."""
    import re
    src = (Path(__file__).resolve().parents[1] / "src/weekly_review/cli.py").read_text()
    start = src.index("overrides = calib.load_review_overrides_full(")
    end = src.index("et.est_human_hours = hours", start)
    loop = src[start:end]
    assert "calib.kind_unchanged(" in loop, "override applied without a unit_kind check"
    assert re.search(r"kind \{.*\} -> \{.*\}", loop), "kind skip must print old and new kind"


def test_rationale_hours_reads_an_itemised_total_not_its_last_part():
    from weekly_review.calibration import rationale_hours
    assert rationale_hours("about 3h on the refactor and ~1h on Sentry, totaling approximately 7h.") == 7.0
    assert rationale_hours("modal work (~5h) and polish (~3h); roughly 12–16h total") == 14.0
    assert rationale_hours("8h for switching, 6h for margins — totals approximately 38h") == 38.0


def test_rationale_hours_parses_spelled_out_hours():
    """'about 6 hours' must parse — an unparseable rationale keeps a stale
    override alive, so the regex gap silently protected inflated verdicts."""
    from weekly_review.calibration import rationale_hours
    assert rationale_hours("An expert doing this ≈ 6h.") == 6.0
    assert rationale_hours("An expert would need about 6 hours.") == 6.0
    assert rationale_hours("roughly 2.5 hrs of work") == 2.5
    assert rationale_hours("about 1 hour of review") == 1.0


def test_quarantine_fires_on_spelled_out_rationale():
    drop, _ = _q(6.0, "2026-06-20T10:00:00Z", 2.0, "An expert would need about 2 hours.")
    assert drop is True


def _dev(i, actor, kind, card=None):
    from weekly_review.events import Event
    return Event(ts=f"2026-09-11T20:{i:02d}:00Z", source_system="s", seq=i, session_id="s",
                 project="p", actor=actor, kind=kind, source_machine="m",
                 content_snippet="x", metadata={"card": card} if card else {})


def _dt(tid, ids):
    from weekly_review.segmentation import Task
    t = Task(task_id=tid, title=tid, project="p")
    t.event_ids = list(ids)
    return t


def test_a_standalone_decision_holds_no_agent_work_and_no_artifact():
    """The user, 2026-09-15: price these at the decision time and nothing more.

    The population is the one `validate`'s `no_conversation` bucket reports, so
    the predicate has to be the same one -- `effort.is_agent_work`. A board
    stamp is not work, and neither is the agent's own tool call ON a card,
    which is that stamp seen from the transcript side.
    """
    from weekly_review.calibration import decision_only_tasks

    events = [_dev(0, "user", "board_action"),        # he answered the decision
              _dev(1, "agent", "board_action"),       # an agent stamped the card
              _dev(2, "agent", "tool_call", card="t-A"),   # the same stamp, its side
              _dev(3, "user", "review_session"),      # he read it
              _dev(4, "agent", "agent_message"),      # REAL agent work
              _dev(5, "agent", "tool_call")]          # and a tool call off any card
    decision = _dt("decision", [0, 1, 2, 3])
    worked = _dt("worked", [0, 4])
    tooled = _dt("tooled", [0, 5])
    assert decision_only_tasks([decision, worked, tooled], events) == {"decision"}


def test_an_artifact_alone_disqualifies_a_task_from_the_decision_price():
    """Something shipped is something shipped, whatever else is missing.
    Pricing that at his reading time would zero out real output."""
    from weekly_review.calibration import decision_only_tasks

    events = [_dev(0, "user", "board_action"), _dev(1, "artifact", "commit")]
    assert decision_only_tasks([_dt("shipped", [0, 1])], events) == set()
    assert decision_only_tasks([_dt("bare", [0])], events) == {"bare"}


def test_an_empty_task_is_not_quietly_a_decision():
    """A task holding nothing has no decision time either. It IS returned, and
    the caller prices it at its measured hands-on, which for an empty task is
    0.0h -- pinned here so that behaviour is a choice rather than an accident."""
    from weekly_review.calibration import decision_only_tasks
    assert decision_only_tasks([_dt("empty", [])], []) == {"empty"}


def test_the_pipeline_applies_the_decision_price():
    """A correction that ships without a call site is a correction that never
    runs; this repo has shipped that defect before."""
    import inspect
    from weekly_review import cli

    src = inspect.getsource(cli)
    assert "decision_only_tasks(" in src, "the ruling is not wired into the run"
    assert "decision-only tasks priced at their decision time" in src, (
        "the run must print what the correction moved, every run")
