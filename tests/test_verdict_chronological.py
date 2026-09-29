"""The user reviews the verdict worksheet top-to-bottom to spot duplicates and
segmentation faults ("Don't split. I want the tasks in chronological order so
I can spot dupes and segmentation issues"). Grouping by project puts a task cut
in two on different pages when the halves landed on different slugs.

`--chronological` must therefore produce ONE group in start-time order across
every project, keep the project visible on each task heading (it was the group
heading before), and mark sub-3-minute hands-on tasks so the sub-floor
population can be seen inline rather than in a separate table.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "make_verdict_worksheet.py"
spec = importlib.util.spec_from_file_location("make_verdict_worksheet", SCRIPT)
mvw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mvw)

# (task_id, project, first_ts, est_h, hands_on_h). Two projects interleaved in
# time so project-grouping and time-ordering disagree on every adjacent pair.
TASKS = [
    ("aaaaaaaaaa01", "alpha", "2026-09-08T10:00:00", 1.0, 0.50),
    ("bbbbbbbbbb02", "beta",  "2026-09-08T11:00:00", 0.5, 0.01),
    ("aaaaaaaaaa03", "alpha", "2026-09-08T12:00:00", 2.0, 0.02),
    ("bbbbbbbbbb04", "beta",  "2026-09-08T13:00:00", 4.0, 1.00),
]


def _run_dir(tmp_path: Path) -> Path:
    rd = tmp_path / "run-test"
    rd.mkdir()
    conn = sqlite3.connect(str(rd / "run.sqlite"))
    conn.executescript("""
        CREATE TABLE tasks (task_id TEXT, project TEXT, title TEXT,
                            user_msg_count INT, first_ts TEXT, last_ts TEXT);
        CREATE TABLE enriched (task_id TEXT, title TEXT, goal TEXT, summary TEXT,
                               est_human_hours REAL, uncertainty TEXT, type TEXT,
                               effort_breakdown TEXT, rationale TEXT, bryan_role_summary TEXT);
        CREATE TABLE task_metrics (task_id TEXT, active_hours REAL, labor_hours REAL);
    """)
    for tid, proj, ts, est, ho in TASKS:
        conn.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?)", (tid, proj, f"title {tid[-2:]}", 3, ts, ts))
        conn.execute("INSERT INTO enriched VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (tid, f"title {tid[-2:]}", "why", "done", est, "medium", "code", "", "", ""))
        conn.execute("INSERT INTO task_metrics VALUES (?,?,?)", (tid, ho, 1.0))
    conn.commit()
    conn.close()
    return rd


def _task_headings(doc: str) -> list[str]:
    return [ln for ln in doc.splitlines() if ln.startswith("## ")]


def test_chronological_is_one_group_in_start_time_order(tmp_path, monkeypatch):
    monkeypatch.setattr(mvw, "DATA_DIR", tmp_path)  # no prior verdicts to pre-fill
    doc = mvw.render(_run_dir(tmp_path), None, None, explain=False, chronological=True)
    groups = [ln for ln in doc.splitlines() if ln.startswith("# ") and "tasks below" in ln]
    assert len(groups) == 1, groups
    assert "4 tasks below" in groups[0]
    heads = _task_headings(doc)
    times = [h.split(" · ")[0].lstrip("#● ").strip() for h in heads]
    assert times == sorted(times)
    assert times == ["2026-09-08 10:00", "2026-09-08 11:00", "2026-09-08 12:00", "2026-09-08 13:00"]


def test_chronological_keeps_project_on_each_heading_and_marks_sub_floor(tmp_path, monkeypatch):
    monkeypatch.setattr(mvw, "DATA_DIR", tmp_path)
    doc = mvw.render(_run_dir(tmp_path), None, None, explain=False, chronological=True)
    heads = _task_headings(doc)
    assert [("[alpha]" in h, "[beta]" in h) for h in heads] == [
        (True, False), (False, True), (True, False), (False, True)]
    # 0.01h and 0.02h are under the 3-minute floor; 0.5h and 1.0h are not.
    assert [h.startswith("## ● ") for h in heads] == [False, True, True, False]


def test_default_grouping_is_unchanged_by_the_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(mvw, "DATA_DIR", tmp_path)
    doc = mvw.render(_run_dir(tmp_path), None, None, explain=False)
    groups = [ln for ln in doc.splitlines() if ln.startswith("# ") and "tasks below" in ln]
    assert [g.split("  ·")[0] for g in groups] == ["# alpha", "# beta"]
    assert not any("[alpha]" in h or "[beta]" in h for h in _task_headings(doc))
