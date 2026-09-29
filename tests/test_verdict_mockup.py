"""The verdict mockup is one HTML file built from run.sqlite; the harvester
turns the user's verdicts (worksheet lines or mockup comment threads) into
`estimate_reviews` rows. Both must agree on the `VERDICT [<id>]` marker,
and the harvester must never re-read a pre-filled line as a fresh verdict."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mock = _load("make_verdict_mockup")
harv = _load("harvest_verdicts")

A = "aaaaaaaaaa01"  # reviewed earlier at the same estimate
B = "bbbbbbbbbb02"  # reviewed earlier, re-estimated since (superseded)
C = "cccccccccc03"  # never reviewed


def _run_dir(tmp_path: Path, monkeypatch) -> Path:
    runs = tmp_path / "runs"
    rd = runs / "run-test"
    rd.mkdir(parents=True)
    conn = sqlite3.connect(str(rd / "run.sqlite"))
    conn.executescript("""
        CREATE TABLE tasks (task_id TEXT, project TEXT, title TEXT, user_msg_count INT,
                            first_ts TEXT, last_ts TEXT, board_task TEXT);
        CREATE TABLE enriched (task_id TEXT, title TEXT, goal TEXT, summary TEXT,
                               est_human_hours REAL, uncertainty TEXT, type TEXT,
                               effort_breakdown TEXT, rationale TEXT, bryan_role_summary TEXT);
        CREATE TABLE task_metrics (task_id TEXT, active_hours REAL, labor_hours REAL);
        CREATE TABLE events (event_id TEXT, ts TEXT, actor TEXT, project TEXT, session_id TEXT,
                             source_system TEXT, source_machine TEXT, kind TEXT, seq INT,
                             content_snippet TEXT, metadata_json TEXT);
        CREATE TABLE task_events (task_id TEXT, event_id TEXT);
        CREATE TABLE estimate_reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT,
                               run_id TEXT, reviewed_at TEXT, llm_est_hours REAL,
                               bryan_est_hours REAL, confidence TEXT, note TEXT);
    """)
    conn.executemany("INSERT INTO tasks VALUES (?,?,?,?,?,?,?)", [
        (A, "p1", "A", 1, "2026-09-08T10:00:00Z", "2026-09-08T11:00:00Z", "t-own"),
        (B, "p1", "B", 1, "2026-09-07T10:00:00Z", "2026-09-07T11:00:00Z", None),
        (C, "p2", "C </script>", 1, "2026-09-09T10:00:00Z", "2026-09-09T11:00:00Z", None),
    ])
    conn.executemany("INSERT INTO enriched VALUES (?,?,?,?,?,?,?,?,?,?)", [
        (A, "A", "why a", "did a", 2.0, "low", "code", "2h", "rat", "role"),
        (B, "B", "why b", "did b", 9.0, "high", "code", "", "rat", ""),
        (C, "C </script>", "", "did c", 0.5, "medium", "ops", "", "", ""),
    ])
    conn.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 ("e1", "2026-09-08T10:30:00Z", "user", "p1", "board", "workspace_board", "m",
                  "board_action", 0, "", json.dumps({"board_event": "decision.answered",
                                                     "task_id": "t-own", "review_item_id": "r-1"})))
    conn.execute("INSERT INTO task_events VALUES (?,?)", (A, "e1"))
    conn.executemany("INSERT INTO task_metrics VALUES (?,?,?)",
                     [(A, 0.5, 1.0), (B, 0.01, 2.0), (C, 0.2, 0.3)])
    conn.executemany("INSERT INTO estimate_reviews (task_id, run_id, reviewed_at, llm_est_hours, "
                     "bryan_est_hours, confidence, note) VALUES (?,?,?,?,?,?,?)", [
        (A, "run-old", "2026-09-01T00:00:00Z", 2.0, 2.0, "ok", "fine"),
        (B, "run-old", "2026-09-01T00:00:00Z", 3.0, 3.0, "ok", None),
    ])
    conn.commit()
    conn.close()
    monkeypatch.setattr(mock, "DATA_DIR", tmp_path)
    monkeypatch.setattr(harv, "DATA_DIR", tmp_path)
    # The worksheet module the mockup imports scans DATA_DIR/runs for verdicts.
    ws = mock._worksheet()
    monkeypatch.setattr(ws, "DATA_DIR", tmp_path)
    monkeypatch.setattr(mock, "_worksheet", lambda: ws)
    return rd


def test_model_groups_by_project_chronologically_and_tallies_reviews(tmp_path, monkeypatch):
    rd = _run_dir(tmp_path, monkeypatch)
    m = mock.build_model(rd)
    assert [p["name"] for p in m["projects"]] == ["p1", "p2"]  # p1 starts first (B, 09-07)
    assert m["projects"][0]["task_ids"] == [B, A]              # chronological inside p1
    # A is reviewed at its current estimate; B's 3h verdict no longer matches 9h.
    assert m["projects"][0]["reviewed"] == 1
    assert m["n_reviewed"] == 1
    by = {t["id"]: t for t in m["tasks"]}
    assert by[A]["verdict"]["superseded"] is False
    assert by[B]["verdict"]["superseded"] is True
    assert by[C]["verdict"] is None
    assert by[A]["board"]["own"]["id"] == "t-own"


def test_html_is_self_contained_and_escapes_data(tmp_path, monkeypatch):
    rd = _run_dir(tmp_path, monkeypatch)
    html = mock.render_html(mock.build_model(rd))
    assert "<script src=" not in html and "<link " not in html
    # The embedded JSON cannot close the script tag early.
    assert "</script>" in html and "<\\/script>" in html
    assert "C </script>" not in html
    assert "VERDICT [" in html  # the marker the harvester keys on


def test_hands_on_under_an_hour_renders_in_minutes(tmp_path, monkeypatch):
    """The user, 2026-09-17: "give hands-on time in minutes when they're under an
    hour long." 0.06h reads as nothing; "3 min" reads as three minutes."""
    rd = _run_dir(tmp_path, monkeypatch)
    html = mock.render_html(mock.build_model(rd))
    assert "const hm=x=>x==null?'?':x<1?`${Math.round(x*60)} min`:h(x)+'h';" in html
    # Every hands-on figure on the card goes through the minutes formatter;
    # the estimate and wall clock keep hours.
    assert "hands-on ${hm(t.hands_on)}" in html
    assert "tri(t,'hands','Hands-on',hm(t.hands_on))" in html
    assert "h(t.hands_on)+'h'" not in html
    assert "est ${h(t.est_h)}h" in html


def test_each_task_says_what_its_hands_on_is_made_of(tmp_path, monkeypatch):
    """The user, 2026-09-17: "review your own hands-on time summaries for cases
    where you say no human activity happened, but there was still a hands-on
    time". The estimator's role text never sees board activity; this line is
    counted from the task's own events."""
    rd = _run_dir(tmp_path, monkeypatch)
    m = mock.build_model(rd)
    by = {t["id"]: t for t in m["tasks"]}
    assert "1 review item handled" in by[A]["hands_why"]
    assert "Review answers: 2 min of reading before 1 answer" in by[A]["hands_why"]
    assert not by[A]["hands_why"].startswith("**Hands-on")   # the page prints the figure itself
    assert "0 review items handled" in by[B]["hands_why"]
    html = mock.render_html(m)
    assert "f('What the hands-on is made of (measured)',t.hands_why)" in html


def test_top_views_rank_by_estimate_and_hands_on_and_state_their_share(tmp_path, monkeypatch):
    """The user, 2026-09-17: "let me review the top items that most affect the
    estimates (which ones have most est_h or hands-on time)"."""
    rd = _run_dir(tmp_path, monkeypatch)
    m = mock.build_model(rd)
    est, hands = mock.top_views(m["tasks"], n=2)
    assert est["task_ids"] == [B, A]          # 9h, 2h; C's 0.5h is cut
    assert hands["task_ids"] == [A, C]        # 0.5h, 0.2h; B's 0.01h is cut
    # The label carries the view's share of the week, so "top 2" is never
    # read as "the week".
    assert est["label"] == "Top 2 by estimate: 11.0h of 11.5h est (96%)"
    assert hands["label"] == "Top 2 by hands-on: 0.7h of 0.7h hands-on (99%)"
    assert est["reviewed"] == 1               # A; B's verdict is superseded
    # Projects are untouched: the views sit beside them, not inside them.
    assert [p["name"] for p in m["projects"]] == ["p1", "p2"]
    assert [v["name"] for v in m["views"]] == ["Top 3 by estimate", "Top 3 by hands-on"]


def test_top_views_drop_tasks_with_nothing_to_rank(tmp_path, monkeypatch):
    rd = _run_dir(tmp_path, monkeypatch)
    tasks = mock.build_model(rd)["tasks"]
    for t in tasks:
        t["hands_on"] = 0 if t["id"] != A else 0.5
    hands = mock.top_views(tasks, n=3)[1]
    assert hands["task_ids"] == [A]
    assert hands["name"] == "Top 1 by hands-on"


def test_page_keeps_a_top_view_open_while_stepping_across_projects(tmp_path, monkeypatch):
    rd = _run_dir(tmp_path, monkeypatch)
    html = mock.render_html(mock.build_model(rd))
    # Views are selectable groups alongside projects.
    assert "(D.views||[]).concat(D.projects).forEach(p=>P[p.name]=p);" in html
    # Selecting a task only leaves the group when the task is not in it; the
    # old project test switched away from a top view on every cross-project step.
    assert "if(!curP||!P[curP].task_ids.includes(id)){curP=t.project;rail();}" in html
    assert "if(t.project!==curP)" not in html
    # First load opens the biggest-estimate view; a ?vtask= link still wins.
    assert "const g0=P[grp0]||(D.views||[])[0]||D.projects[0];" in html
    assert "if(linked&&T[linked]&&!(g0&&g0.task_ids.includes(linked))){selectT(linked);}" in html


def test_harvest_worksheet_skips_prefilled_and_records_fresh(tmp_path, monkeypatch):
    rd = _run_dir(tmp_path, monkeypatch)
    md = "\n".join([
        f"**VERDICT [{A[:10]}]:** ✅ reviewed: ok — fine",
        f"**VERDICT [{B[:10]}]:** ⚠️ NOT carried forward — you previously said **3h**",
        f"**VERDICT [{C[:10]}]:** HIGH — more like 0.1h",
        "**VERDICT [dddddddddd]:** _(comment here)_",
    ])
    rows = harv.harvest(rd, harv.from_worksheet(md))
    assert [r["task_id"] for r in rows] == [C]
    assert rows[0]["confidence"] == "high" and rows[0]["bryan_h"] == 0.1
    conn = sqlite3.connect(str(rd / "run.sqlite"))
    got = conn.execute("SELECT task_id, bryan_est_hours, llm_est_hours FROM estimate_reviews "
                       "WHERE run_id='run-test'").fetchall()
    assert got == [(C, 0.1, 0.5)]


def test_harvest_threads_reads_id_from_anchor_and_verdict_from_first_comment(tmp_path, monkeypatch):
    rd = _run_dir(tmp_path, monkeypatch)
    threads = {"threads": [
        {"id": "th1", "anchor": {"quote": f"VERDICT [{B[:10]}]: (comment here)"},
         "comments": [{"author": "Alex", "text": "LOW — was a full day"},
                      {"author": "Weekly Review", "text": "noted"}]},
        {"id": "th2", "anchor": {"quote": "Why"}, "comments": [{"text": "unrelated"}]},
    ]}
    pairs = harv.from_threads(threads)
    assert pairs == [(B[:10], "LOW — was a full day", None)]
    rows = harv.harvest(rd, pairs, dry_run=True)
    assert rows[0]["task_id"] == B and rows[0]["confidence"] == "low" and rows[0]["bryan_h"] == 8.0


def test_parse_verdict_grammar():
    p = harv.parse_verdict
    assert p("ok", 2.0) == {"confidence": "ok", "bryan_h": 2.0, "note": "ok"}
    assert p("*OK.  But how did this split*", 120.0)["bryan_h"] == 120.0
    # ok keeps the estimate even when the note mentions a number: the 0.18h
    # he is discussing is the hands-on figure, not a corrected estimate.
    assert p("ok — not much more than 0.18h for this", 14.0)["bryan_h"] == 14.0
    assert p("HIGH — more like 3h", 9.0)["bryan_h"] == 3.0
    assert p("high, maybe 1.5 hours", 9.0)["bryan_h"] == 1.5
    assert p("LOW — was a full day", 1.0)["bryan_h"] == 8.0
    assert p("split this in two", 4.0) == {"confidence": "note", "bryan_h": 4.0, "note": "split this in two"}
    assert p("✅ reviewed: ok", 2.0) is None
    assert p("_(comment here)_", 2.0) is None


def test_parse_verdict_structured_form_from_submit_button():
    """The page's Submit composes 'estimate: X · hands-on: Y — note'. The
    estimate verdict sets confidence and hours; hands-on rides along."""
    p = harv.parse_verdict
    got = p("estimate: high · hands-on: ok — more like 2h", 9.0)
    assert got == {"confidence": "high", "bryan_h": 2.0, "hands_on": "ok",
                   "note": "estimate: high · hands-on: ok — more like 2h"}
    assert p("estimate: ok", 9.0) == {"confidence": "ok", "bryan_h": 9.0, "note": "estimate: ok"}
    real = ("estimate: ok · hands-on: ok — the planning came earlier. It's probably not "
            "that much more than 0.18h (twelve minutes) for this topic.")
    assert p(real, 14.0)["bryan_h"] == 14.0 and p(real, 14.0)["confidence"] == "ok"
    assert p("hands-on: ok — about 0.3h of reading", 2.0)["bryan_h"] == 2.0
    assert p("estimate: low — a full day", 1.0)["bryan_h"] == 8.0
    # A figure on the wrong side of the estimate is part of his argument, not
    # his correction: "low" never lowers an estimate and "high" never raises it.
    assert p("estimate: low · hands-on: ok — this averages 3.6k lines per 8h workday", 18.0)["bryan_h"] == 18.0
    assert p("estimate: high — the agent spent 40h on it", 9.0)["bryan_h"] == 9.0
    assert p("estimate: low — more like 30h", 18.0)["bryan_h"] == 30.0
    # Hands-on only: nothing said about the estimate, so it is a note at LLM hours.
    only = p("hands-on: low — all afternoon on this", 3.0)
    assert only["confidence"] == "note" and only["bryan_h"] == 3.0 and only["hands_on"] == "low"


def test_html_carries_the_verdict_form(tmp_path, monkeypatch):
    rd = _run_dir(tmp_path, monkeypatch)
    html = mock.render_html(mock.build_model(rd))
    for needle in ("tri(t,'est'", "tri(t,'hands'", 'class="vsubmit"', "postNewThread", "estimate: "):
        assert needle in html
    # The board only injects its comment widget when the page does NOT name the
    # widget's tag (server: EMBED_IN_SCRIPT), so the tag must never appear literally.
    assert "claude-feedback-widget" not in html and "FeedbackWidget.init" not in html


def test_parse_breakdown_groups_counts_and_ranks_components():
    """The user's form: 'LoC +9465 / −474', 'Pages: 12 read / 3 written', and the
    biggest components first. Zero counts are noise and are dropped."""
    text = ("[sources reviewed: ~16 Legistar searches + 6 amendment checks] · [pages read: ~12] · "
            "[pages written: ~3] · [files touched: 60] · [LoC added: 9465] · [LoC removed: 474] · "
            "[test files added: 0] · note rewritten (commit 7da8bda, 1 file); "
            "Legistar checks (commit eaaed1a, 30 files); gate lifted (commit 82c9c0e, 6 files).")
    b = mock.parse_breakdown(text)
    assert dict(b["stats"])["LoC"] == "+9465 / −474"
    assert dict(b["stats"])["Pages"] == "~12 read / ~3 written"
    assert dict(b["stats"])["Files"] == "60"
    assert dict(b["stats"])["Search"].startswith("~16 Legistar")
    assert "Tests" not in dict(b["stats"])
    assert [c["files"] for c in b["components"]] == [30, 6, 1]
    assert mock.parse_breakdown("[LoC added: ~200] · [LoC removed: ~10]")["stats"] == [["LoC", "≈ +200 / −10"]]
    assert mock.parse_breakdown("") == {"stats": [], "components": []}


def test_harvest_threads_reads_id_from_anchor_data_attrs(tmp_path, monkeypatch):
    """A comment anchored on a task's title row (not the VERDICT box) still
    names the task: the mockup stamps data-task-id on every row and field."""
    rd = _run_dir(tmp_path, monkeypatch)
    threads = {"threads": [
        {"id": "th1",
         "anchor": {"kind": "element", "fingerprint": {"tag": "DIV", "classes": ["title"],
                    "text": "C </script>", "dataAttrs": {"data-task-id": C}}},
         "comments": [{"author": "Alex", "text": "HIGH — more like 0.1h"}]},
    ]}
    assert harv.from_threads(threads) == [(C, "HIGH — more like 0.1h", None)]


def test_harvest_threads_skips_prose_comments_but_keeps_form_and_keyword_verdicts(tmp_path, monkeypatch):
    """A comment on a task row that names no verdict is a comment. The
    worksheet path still records free prose, because there he typed it into
    the VERDICT slot."""
    rd = _run_dir(tmp_path, monkeypatch)
    def th(i, tid, text):
        return {"id": f"th{i}",
                "anchor": {"kind": "element", "fingerprint": {"dataAttrs": {"data-task-id": tid}}},
                "comments": [{"author": "Alex", "text": text, "ts": 1_789_600_000_000 + i}]}
    threads = {"threads": [
        th(1, C, "Can you give me more room for the content here"),
        th(2, C, "Why is there no board task?"),
        th(3, C, "hands-on: ok"),
        th(4, B, "OK. But how did this split"),
    ]}
    rows = harv.harvest(rd, harv.from_threads(threads), dry_run=True)
    assert [(r["task_id"], r["confidence"]) for r in rows] == [(C, "note"), (B, "ok")]
    assert rows[0]["hands_on"] == "ok"
    # same prose via the worksheet IS a verdict (existing behaviour, unchanged)
    md = f"**VERDICT [{C[:10]}]:** split this in two"
    assert [r["confidence"] for r in harv.harvest(rd, harv.from_worksheet(md), dry_run=True)] == ["note"]


def test_harvest_threads_newer_submit_wins_regardless_of_list_order(tmp_path, monkeypatch):
    """Every Submit on the page opens a new thread, so a task the user corrects
    has two. reviewed_at must be the submit's own `ts`, not the harvest
    time: otherwise the winner is whichever thread list_threads happened to
    list last, and the readers (latest reviewed_at wins) can resurrect the
    verdict he replaced."""
    from weekly_review.calibration import load_review_overrides
    rd = _run_dir(tmp_path, monkeypatch)
    newer = 1_789_622_700_000  # epoch ms
    older = 1_789_622_500_000
    threads = {"threads": [
        # newer submit listed FIRST — the order the fix must not depend on
        {"id": "th2", "anchor": {"quote": f"VERDICT [{C[:10]}]: (comment here)"},
         "comments": [{"author": "Alex", "text": "estimate: ok · hands-on: ok", "ts": newer}]},
        {"id": "th1", "anchor": {"quote": f"VERDICT [{C[:10]}]: (comment here)"},
         "comments": [{"author": "Alex", "text": "HIGH — more like 0.1h", "ts": older}]},
    ]}
    pairs = harv.from_threads(threads)
    assert [pr[2] for pr in pairs] == ["2026-09-17T05:25:00+00:00", "2026-09-17T05:21:40+00:00"]
    rows = harv.harvest(rd, pairs)
    assert [r["bryan_h"] for r in rows] == [0.5, 0.1]  # ok keeps the LLM 0.5h
    conn = sqlite3.connect(str(rd / "run.sqlite"))
    got = conn.execute("SELECT reviewed_at, bryan_est_hours FROM estimate_reviews "
                       "WHERE task_id=? ORDER BY id", (C,)).fetchall()
    assert got == [("2026-09-17T05:25:00+00:00", 0.5), ("2026-09-17T05:21:40+00:00", 0.1)]
    # the reader every consumer uses picks the newer submit, not the last insert
    assert load_review_overrides([rd / "run.sqlite"])[C] == 0.5
