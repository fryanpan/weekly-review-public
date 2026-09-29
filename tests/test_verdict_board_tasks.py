"""The user reviews a week against the board ("For Sept can you show me also which
task(s) on board lined up with each item?"). `--board-tasks` puts one line under
every measured task naming the board task the segmenter put it on, and any
other board tasks its events name — a measured task on no board task, or on
several, is the segmentation fault he is looking for.

The workspace store is not on the test machine, so titles are absent and the
line must still render from ids alone.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "make_verdict_worksheet.py"
spec = importlib.util.spec_from_file_location("make_verdict_worksheet_bt", SCRIPT)
mvw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mvw)

OWN = "aaaaaaaaaa01"      # segmenter put it on t-own; events also name t-other twice
NONE = "bbbbbbbbbb02"     # no board task at all
MANY = "cccccccccc03"     # no own task; events name five others (3 shown, +2 more)


def _run_dir(tmp_path: Path) -> Path:
    rd = tmp_path / "run-test"
    rd.mkdir()
    conn = sqlite3.connect(str(rd / "run.sqlite"))
    conn.executescript("""
        CREATE TABLE tasks (task_id TEXT, project TEXT, title TEXT, user_msg_count INT,
                            first_ts TEXT, last_ts TEXT, board_task TEXT);
        CREATE TABLE enriched (task_id TEXT, title TEXT, goal TEXT, summary TEXT,
                               est_human_hours REAL, uncertainty TEXT, type TEXT,
                               effort_breakdown TEXT, rationale TEXT, bryan_role_summary TEXT);
        CREATE TABLE task_metrics (task_id TEXT, active_hours REAL, labor_hours REAL);
        CREATE TABLE events (event_id TEXT, metadata_json TEXT);
        CREATE TABLE task_events (task_id TEXT, event_id TEXT);
    """)
    rows = [(OWN, "t-own", "2026-09-08T10:00:00"), (NONE, None, "2026-09-08T11:00:00"),
            (MANY, None, "2026-09-08T12:00:00")]
    for tid, bt, ts in rows:
        conn.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?)", (tid, "alpha", "t", 3, ts, ts, bt))
        conn.execute("INSERT INTO enriched VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (tid, "t", "why", "done", 1.0, "medium", "code", "", "", ""))
        conn.execute("INSERT INTO task_metrics VALUES (?,?,?)", (tid, 0.5, 1.0))

    n = 0

    def ev(tid: str, md: dict) -> None:
        nonlocal n
        n += 1
        conn.execute("INSERT INTO events VALUES (?,?)", (f"e{n}", json.dumps(md)))
        conn.execute("INSERT INTO task_events VALUES (?,?)", (tid, f"e{n}"))

    # Every key shape the board log uses, plus a distractor that must not count.
    ev(OWN, {"card": "t-own"})
    ev(OWN, {"task_id": "t-other"})
    ev(OWN, {"context": {"taskId": "t-other"}})
    ev(OWN, {"note": "mentions t-nowhere in prose only"})
    for i in range(5):
        for _ in range(5 - i):  # t-m0 named 5 times … t-m4 once
            ev(MANY, {"wake_task": f"t-m{i}"})
    conn.commit()
    conn.close()
    return rd


def _board_line(doc: str, tid: str) -> str:
    """The board line of the task whose VERDICT line carries this id: the
    heading names time and project, not the id, so walk back from the verdict."""
    lines = doc.splitlines()
    end = next(i for i, ln in enumerate(lines) if ln.startswith(f"**VERDICT [{tid[:10]}]"))
    start = max(i for i in range(end) if lines[i].startswith("## "))
    return next(ln for ln in lines[start:end] if ln.startswith("**Board task:**"))


def test_board_line_names_own_task_then_others_by_event_count(tmp_path, monkeypatch):
    monkeypatch.setattr(mvw, "DATA_DIR", tmp_path)
    doc = mvw.render(_run_dir(tmp_path), None, None, explain=False, board_tasks=True)
    line = _board_line(doc, OWN)
    assert line.startswith("**Board task:** ") and "`t-own`" in line
    assert "**Also names:** " in line and "`t-other`, 2 events" in line
    assert "t-nowhere" not in line  # prose mention is not a board-task key


def test_no_board_task_is_said_outright_not_left_blank(tmp_path, monkeypatch):
    monkeypatch.setattr(mvw, "DATA_DIR", tmp_path)
    doc = mvw.render(_run_dir(tmp_path), None, None, explain=False, board_tasks=True)
    assert _board_line(doc, NONE) == "**Board task:** _(none — the segmenter put this on no board task)_"


def test_many_named_tasks_are_capped_with_a_count(tmp_path, monkeypatch):
    monkeypatch.setattr(mvw, "DATA_DIR", tmp_path)
    doc = mvw.render(_run_dir(tmp_path), None, None, explain=False, board_tasks=True)
    line = _board_line(doc, MANY)
    assert line.startswith("**Board task:** _(none")
    also = line.split("**Also names:** ", 1)[1]
    assert [s for s in ("t-m0", "t-m1", "t-m2") if s in also] == ["t-m0", "t-m1", "t-m2"]
    assert "t-m3" not in also and also.endswith(", +2 more")
    assert also.index("t-m0") < also.index("t-m1") < also.index("t-m2")


def test_board_id_links_into_its_workspace_when_the_store_places_it(tmp_path, monkeypatch):
    """The user reads the worksheet on the board and opens each board task from
    it, so an id the store can place is a link into its workspace; an id the
    store cannot place stays a bare id."""
    monkeypatch.setattr(mvw, "DATA_DIR", tmp_path)
    monkeypatch.setattr(mvw, "board_titles", lambda: {"t-own": ("alpha", "Own task", "w-alpha")})
    doc = mvw.render(_run_dir(tmp_path), None, None, explain=False, board_tasks=True)
    line = _board_line(doc, OWN)
    assert "[alpha] Own task ([`t-own`](/workspaces/w-alpha?task=t-own))" in line
    also = line.split("**Also names:** ", 1)[1]
    assert "`t-other`, 2 events" in also and "/workspaces/" not in also


def test_flag_off_renders_no_board_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(mvw, "DATA_DIR", tmp_path)
    doc = mvw.render(_run_dir(tmp_path), None, None, explain=False)
    assert "**Board task:**" not in doc
