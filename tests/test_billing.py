"""A billing export leaves the building, so the tests are about what it refuses.

The hours arithmetic is `effort.py`'s and is tested there. What is new here is
that this artifact goes to somebody who is not the data subject, and the three
ways that goes wrong are: it carries his clients' material, it mixes two
clients, or it reports a confident number built from an instrument that never
ran. One test each, plus the era exemption that keeps the third from firing on
every historical week.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from weekly_review.billing import (
    IncompleteSources,
    PeriodNotCovered,
    build_exports,
    render_markdown,
)
from weekly_review.config import ProjectsConfig, ProjectSpec

# Two clients, one of which spans two slugs — the shape the live config has, and
# the reason the export keys on client rather than project.
CFG = ProjectsConfig(projects=[
    ProjectSpec(slug="acme-api", cwd_patterns=[], client="Acme", billable=True),
    ProjectSpec(slug="acme-web", cwd_patterns=[], client="Acme", billable=True),
    ProjectSpec(slug="goodworks", cwd_patterns=[], client="Goodworks", billable=False),
    ProjectSpec(slug="personal", cwd_patterns=[]),
])

SCHEMA = """
CREATE TABLE run_meta (run_id TEXT, schema_version TEXT, period_start TEXT,
    period_end TEXT, cognitive_wall_clock_hours REAL, context_switches INT,
    coverage_percent REAL, coverage_assigned INT, coverage_total INT,
    created_at TEXT);
CREATE TABLE events (event_id TEXT, ts TEXT, actor TEXT, project TEXT,
    session_id TEXT, source_system TEXT, source_machine TEXT, kind TEXT,
    seq INT, content_snippet TEXT, words INT, metadata_json TEXT);
CREATE TABLE tasks (task_id TEXT, project TEXT, title TEXT, summary TEXT,
    first_ts TEXT, last_ts TEXT, user_msg_count INT, event_count INT,
    issue_refs_json TEXT, rule_counts_json TEXT, delegated INT);
CREATE TABLE task_events (task_id TEXT, event_id TEXT, rule TEXT, detail TEXT);
CREATE TABLE task_metrics (task_id TEXT, run_id TEXT, active_hours REAL,
    agent_active_hours REAL, resumptions INT, wall_clock_hours REAL,
    agent_busy_hours REAL, hands_on_blocks INT, human_input_chars INT,
    dominant_model TEXT, labor_hours REAL, agent_lane_hours REAL);
"""

# Deliberately client-confidential: if any of these strings reaches the rendered
# artifact, the export has leaked its subject's client material.
SECRET_TITLE = "Migrate Acme's patient intake billing codes"
SECRET_SNIPPET = "the Acme contract says we owe them the ICD-10 crosswalk by Friday"


def _run(tmp_path: Path, run_id: str, *, start: str, end: str, sources, tasks):
    d = tmp_path / "runs" / run_id
    d.mkdir(parents=True)
    conn = sqlite3.connect(str(d / "run.sqlite"))
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO run_meta (run_id, schema_version, period_start, period_end) VALUES (?,'v2',?,?)",
        (run_id, start, end),
    )
    eid = 0
    for task_id, project, title, stamps in tasks:
        first, last = stamps[0], stamps[-1]
        conn.execute(
            "INSERT INTO tasks (task_id, project, title, first_ts, last_ts, "
            "delegated) VALUES (?,?,?,?,?,0)",
            (task_id, project, title, first, last),
        )
        conn.execute(
            "INSERT INTO task_metrics (task_id, run_id, agent_active_hours, "
            "agent_lane_hours, labor_hours) VALUES (?,?,?,?,?)",
            (task_id, run_id, 2.0, 3.0, 4.0),
        )
        for ts in stamps:
            key = f"e{eid}"
            conn.execute(
                "INSERT INTO events (event_id, ts, actor, project, session_id, "
                "source_system, source_machine, kind, seq, content_snippet, "
                "words, metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, ts, "user", project, "s1", "claude_transcript", "mm",
                 "user_message", eid, SECRET_SNIPPET, 12, json.dumps({"words": 12})),
            )
            conn.execute(
                "INSERT INTO task_events (task_id, event_id) VALUES (?,?)",
                (task_id, key),
            )
            eid += 1
    # One event per extra source, so the source-presence check sees it.
    for src in sources:
        conn.execute(
            "INSERT INTO events (event_id, ts, actor, project, session_id, "
            "source_system, source_machine, kind, seq, content_snippet, words, "
            "metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"x{src}", f"{start}T09:00:00Z", "user", "personal", "s9", src, "mm",
             "doc_open", 0, "", 0, json.dumps({})),
        )
    conn.commit()
    conn.close()
    return d


def _full(tmp_path, **kw):
    """A run over a period where every instrument existed and ran."""
    return _run(
        tmp_path, "run-full", start="2026-09-01", end="2026-09-07",
        sources=["live_feedback", "workspace_board", "meetings"],
        tasks=kw.get("tasks", [
            ("t-acme-1", "acme-api", SECRET_TITLE,
             ["2026-09-01T10:00:00Z", "2026-09-01T10:20:00Z"]),
            ("t-acme-2", "acme-web", "Acme web nav rebuild",
             ["2026-09-02T11:00:00Z", "2026-09-02T11:15:00Z"]),
            ("t-good-1", "goodworks", "Goodworks volunteer portal",
             ["2026-09-03T14:00:00Z", "2026-09-03T14:30:00Z"]),
            ("t-mine-1", "personal", "My own thing",
             ["2026-09-04T09:00:00Z", "2026-09-04T09:45:00Z"]),
        ]),
    )


def _build(tmp_path, **kw):
    _full(tmp_path)
    return build_exports(
        "run-full", period_start="2026-09-01", period_end="2026-09-07",
        projects_cfg=CFG, data_dir=tmp_path, **kw
    )


def test_one_client_never_appears_in_anothers_export(tmp_path):
    """Isolation is the guarantee the artifact is useless without."""
    acme = _build(tmp_path, client="Acme")
    assert [e.client for e in acme] == ["Acme"]
    text = render_markdown(acme[0])
    for foreign in ("Goodworks", "goodworks", "personal"):
        assert foreign not in text, f"{foreign!r} leaked into Acme's export"
    # And a client's own slugs are aggregated, not split: Acme is one invoice,
    # not two, even though its work lives in two projects.
    assert acme[0].projects == ["acme-api", "acme-web"]


def test_export_carries_no_client_content(tmp_path):
    """Slugs, dates, hours and counts — never a title or a message."""
    for exp in _build(tmp_path):
        text = render_markdown(exp)
        assert SECRET_TITLE not in text
        assert SECRET_SNIPPET not in text
        # A task title is client content on a client task, so no fragment of one
        # may survive either. "Migrate" is the distinctive verb from the title.
        assert "Migrate" not in text
        assert "intake" not in text


def test_volunteer_client_renders_unbilled_and_carries_no_revenue(tmp_path):
    exps = {e.client: e for e in _build(tmp_path)}
    assert exps["Goodworks"].billable is False
    assert exps["Goodworks"].billing_status == "unbilled (volunteer / pro-bono)"
    assert exps["Acme"].billing_status == "billable"
    text = render_markdown(exps["Goodworks"])
    assert "unbilled (volunteer / pro-bono)" in text
    # There is no rate anywhere in this module, so there is no revenue figure to
    # suppress. Assert the absence, because the day somebody adds a rate is the
    # day a volunteer client could start reading as money. Money-shaped tokens,
    # not the bare word "rate": "separately" contains it, and a check that fires
    # on ordinary prose gets deleted rather than fixed.
    for money in ("$", "£", "€", "hourly rate", "invoice total", "amount due",
                  "total due", "subtotal"):
        assert money not in text.lower()


def test_reconciliation_measures_the_overlap_rather_than_assuming_it(tmp_path):
    exps = _build(tmp_path)
    r = exps[0].reconciliation
    # Every export shares one reconciliation: it is a fact about the period.
    assert all(e.reconciliation is r for e in exps)
    assert r.week_hands_on_hours > 0
    # The client union never exceeds the week, and the non-client remainder is
    # the difference. Personal work is in the fixture, so it is non-zero.
    assert r.all_clients_union_hours <= r.week_hands_on_hours + 1e-9
    assert r.non_client_hours > 0
    # Sum >= union always; here the clients do not overlap, so they are equal.
    assert r.sum_of_client_hours + 1e-9 >= r.all_clients_union_hours
    assert r.shared_between_clients_hours == pytest.approx(0.0, abs=1e-6)
    assert any("not billed to any client" in ln for ln in r.explain())


def test_the_artifact_states_the_tolerances_its_figure_moves_with(tmp_path):
    """An hours figure without its knobs cannot be checked by its reader.

    The same week measures 22.61h at one blocking gap and 30.34h at another, so
    the tolerances are part of the number. They are rendered from what the export
    was actually built with, never from a sentence repeating the defaults — that
    is the version that goes on lying after somebody passes a different value.
    """
    _full(tmp_path)
    exp = build_exports(
        "run-full", period_start="2026-09-01", period_end="2026-09-07",
        projects_cfg=CFG, data_dir=tmp_path, stitch_sec=900.0, gap_sec=30.0,
    )[0]
    text = render_markdown(exp)
    assert "## Methodology — what each number means" in text
    assert "15 minutes apart" in text
    assert "30 seconds apart" in text
    # And expert hours, the one metric of the canonical seven that is an LLM
    # estimate off the client's own material, stays out.
    for absent in ("expert hours:", "est_h", "expert-hours estimate of"):
        assert absent not in text.lower()
    # "leverage" appears once, in the sentence saying it is absent. A count, not
    # a ban: the artifact has to be able to name what it left out.
    leverage_lines = [l for l in text.lower().splitlines() if "leverage" in l]
    assert len(leverage_lines) == 1
    assert "deliberately not in this export" in leverage_lines[0]


def test_period_outside_the_run_is_refused(tmp_path):
    _full(tmp_path)
    with pytest.raises(PeriodNotCovered):
        build_exports("run-full", period_start="2026-08-25",
                      period_end="2026-09-07", projects_cfg=CFG, data_dir=tmp_path)


def test_missing_instrument_that_should_have_run_is_refused(tmp_path):
    """A transcript-only run over a workspace-era period must not export."""
    _run(tmp_path, "run-thin", start="2026-09-01", end="2026-09-07", sources=[],
         tasks=[("t1", "acme-api", "x", ["2026-09-01T10:00:00Z"])])
    with pytest.raises(IncompleteSources) as e:
        build_exports("run-thin", period_start="2026-09-01",
                      period_end="2026-09-07", projects_cfg=CFG, data_dir=tmp_path)
    assert "workspace_board" in str(e.value)

    # The escape hatch exports, and says so in the artifact itself.
    exps = build_exports("run-thin", period_start="2026-09-01",
                         period_end="2026-09-07", projects_cfg=CFG,
                         data_dir=tmp_path, allow_partial_sources=True)
    assert exps[0].missing_sources
    assert "floor, not the hours" in render_markdown(exps[0])


def _cli(monkeypatch, tmp_path, argv):
    """Run the real CLI against a fixture run, with every data root redirected.

    `billing.DATA_DIR` and `runs.DATA_DIR` are patched separately because they
    are separate module globals: `cli` re-reads billing's inside the command, so
    patching billing covers it, but `RunManager` reads runs'. A test that patched
    only one would pass while the shipped command read two different directories.
    """
    import yaml
    from weekly_review import billing, cli
    from weekly_review import config as cfg_mod
    from weekly_review import runs as runs_mod

    cfgdir = tmp_path / "config"
    cfgdir.mkdir(exist_ok=True)
    (cfgdir / "projects.yaml").write_text(yaml.safe_dump({"projects": [
        {"slug": p.slug, "cwd_patterns": [], "client": p.client,
         "billable": p.billable} if p.client else
        {"slug": p.slug, "cwd_patterns": []}
        for p in CFG.projects
    ]}))
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", cfgdir)
    monkeypatch.setattr(billing, "DATA_DIR", tmp_path)
    monkeypatch.setattr(runs_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr("sys.argv", ["weekly-review", *argv])
    return cli.main()


def test_the_command_exports_a_named_client_for_a_named_period(monkeypatch, tmp_path):
    """The review doc (2026-09-12) is satisfiable by a command, not only by an import.

    A function can be correct, tested and never reached — this repo shipped a
    hands-on metric that nothing called for two days. So the call site is
    exercised, not grepped.
    """
    _full(tmp_path)
    out = tmp_path / "invoice.md"
    rc = _cli(monkeypatch, tmp_path, [
        "--billing-export", "Acme", "--run-id", "run-full",
        "--since", "2026-09-01", "--until", "2026-09-07", "--write", str(out),
    ])
    assert rc == 0
    text = out.read_text()
    assert "# Acme — 2026-09-01 to 2026-09-07" in text
    assert "Goodworks" not in text
    assert SECRET_TITLE not in text

    # A project slug names its client, and exports that client's whole
    # engagement rather than one slug of it.
    out2 = tmp_path / "by-slug.md"
    assert _cli(monkeypatch, tmp_path, [
        "--billing-export", "acme-web", "--run-id", "run-full",
        "--since", "2026-09-01", "--until", "2026-09-07", "--write", str(out2),
    ]) == 0
    assert out2.read_text() == text


def test_the_command_writes_one_file_per_client(monkeypatch, tmp_path):
    """A file holding two clients is the one thing this artifact must not be."""
    _full(tmp_path)
    rc = _cli(monkeypatch, tmp_path, [
        "--billing-export", "--run-id", "run-full",
        "--since", "2026-09-01", "--until", "2026-09-07",
        "--write", str(tmp_path / "period.md"),
    ])
    assert rc == 0
    written = sorted(p.name for p in tmp_path.glob("period-*.md"))
    assert written == ["period-acme.md", "period-goodworks.md"]
    assert "Goodworks" not in (tmp_path / "period-acme.md").read_text()
    assert "Acme" not in (tmp_path / "period-goodworks.md").read_text()


def test_the_command_refuses_a_run_missing_an_instrument(monkeypatch, tmp_path):
    _run(tmp_path, "run-thin", start="2026-09-01", end="2026-09-07", sources=[],
         tasks=[("t1", "acme-api", "x", ["2026-09-01T10:00:00Z"])])
    argv = ["--billing-export", "Acme", "--run-id", "run-thin",
            "--since", "2026-09-01", "--until", "2026-09-07"]
    assert _cli(monkeypatch, tmp_path, argv) == 2
    assert _cli(monkeypatch, tmp_path, argv + ["--allow-partial-sources"]) == 0


def test_an_instrument_that_did_not_exist_yet_is_not_missing(tmp_path):
    """The era exemption, and it is the test that keeps the guard usable.

    A week in June has no meetings and no board because neither had been built.
    Refusing it would make every historical invoice require the escape hatch,
    which would teach a reader to pass the flag without reading it.
    """
    _run(tmp_path, "run-june", start="2026-06-01", end="2026-06-07",
         sources=["live_feedback"],
         tasks=[("t1", "acme-api", "x", ["2026-06-01T10:00:00Z",
                                         "2026-06-01T10:10:00Z"])])
    exps = build_exports("run-june", period_start="2026-06-01",
                         period_end="2026-06-07", projects_cfg=CFG,
                         data_dir=tmp_path)
    assert exps[0].missing_sources == ()
    assert "floor, not the hours" not in render_markdown(exps[0])

    # But live_feedback existed by June, so its absence still refuses.
    _run(tmp_path, "run-june-thin", start="2026-06-01", end="2026-06-07",
         sources=[], tasks=[("t1", "acme-api", "x", ["2026-06-01T10:00:00Z"])])
    with pytest.raises(IncompleteSources):
        build_exports("run-june-thin", period_start="2026-06-01",
                      period_end="2026-06-07", projects_cfg=CFG, data_dir=tmp_path)
