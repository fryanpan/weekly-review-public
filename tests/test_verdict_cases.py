import sqlite3

from weekly_review.verdict_cases import Host, check_case, run_cases, uncheckable


def host(**kw):
    base = dict(task_id="n1", share=1.0, spread=1, est=8.0, hands_on=0.5, wall=2.0,
                project="p", events=10)
    base.update(kw)
    return Host(**base)


def test_an_estimate_he_named_holds_within_twice_either_way():
    assert check_case({"est_h": 8}, None, host(est=4.0)) == []
    assert check_case({"est_h": 8}, None, host(est=16.0)) == []
    assert check_case({"est_h": 8}, None, host(est=3.9))
    assert check_case({"est_h": 8}, None, host(est=16.1))


def test_an_accepted_estimate_is_checked_against_the_one_he_accepted():
    assert check_case({"est_ok": True}, 0.25, host(est=0.5)) == []
    assert "he accepted 0.25h" in check_case({"est_ok": True}, 0.25, host(est=1.5))[0]


def test_a_split_task_cannot_be_judged_either_way():
    """19h fell to 1.5h when the work spread over 11 tasks, so a piece's estimate
    matching his number by luck must not read as the verdict holding — and it
    must not read as the verdict broken either, because no task speaks for the
    work he looked at."""
    why = uncheckable(host(est=8.0, share=0.49, spread=4), 100)
    assert why and "split 4 ways" in why
    assert uncheckable(host(share=0.5, events=50), 100) is None, "half is enough"


def test_a_host_that_swallowed_the_work_cannot_judge_the_verdict():
    """2dfac481c8: he said "Sounds fine" about 40 events of steering priced
    0.25h, with the build priced in the sibling. The same id later held 323
    events including the build, priced 18h. Share stayed 0.97 because his events
    were all still there, so nothing caught it and the check called his verdict
    broken over a 72x gap of its own making."""
    why = uncheckable(host(share=0.97, events=323), 40)
    assert why and "absorbed other work" in why and "8.1x" in why
    assert uncheckable(host(share=1.0, events=120), 40) is None, "3x is the bar, as for overrides"
    assert uncheckable(host(share=1.0, events=121), 40), "and past it does not judge"


def test_zero_hands_on_and_zero_wall_fail_a_must_be_nonzero_verdict():
    fails = check_case({"hands_on_min": 0, "wall_min": 0}, None, host(hands_on=0.0, wall=0.0))
    assert len(fails) == 2


def test_wall_ceiling_and_project():
    fails = check_case({"wall_max": 10, "project": "bike"}, None, host(wall=57.4, project="unknown"))
    assert any("wall 57.40h" in f for f in fails) and any("project unknown" in f for f in fails)
    assert check_case({"wall_max": 10, "project": "p"}, None, host(wall=10.0)) == []


def test_no_host_is_a_failure():
    assert check_case({"est_ok": True}, 1.0, None)


def _run_db(path, owners, est, events_present=None):
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE tasks (task_id TEXT, project TEXT);
        CREATE TABLE enriched (task_id TEXT, est_human_hours REAL);
        CREATE TABLE task_metrics (task_id TEXT, active_hours REAL, labor_hours REAL);
        CREATE TABLE task_events (task_id TEXT, event_id TEXT);
        CREATE TABLE events (event_id TEXT);""")
    for tid in set(owners.values()):
        c.execute("INSERT INTO tasks VALUES (?, 'p')", (tid,))
        c.execute("INSERT INTO enriched VALUES (?, ?)", (tid, est[tid]))
        c.execute("INSERT INTO task_metrics VALUES (?, 0.2, 1.0)", (tid,))
    for e, tid in owners.items():
        c.execute("INSERT INTO task_events VALUES (?, ?)", (tid, e))
    for e in (owners if events_present is None else events_present):
        c.execute("INSERT INTO events VALUES (?)", (e,))
    c.commit()
    return c


def test_run_cases_follows_the_events_to_their_new_task(tmp_path):
    (tmp_path / "old").mkdir()
    _run_db(tmp_path / "old/run.sqlite", {"e1": "t", "e2": "t", "e3": "t"}, {"t": 8.0}).close()
    new = _run_db(tmp_path / "new.sqlite", {"e1": "x", "e2": "x", "e3": "y"}, {"x": 1.0, "y": 30.0})
    [r] = run_cases(new, tmp_path, [{"anchor_run": "old", "anchor_task": "t", "expect": {"est_ok": True}}])
    assert r["host"] == "x"
    assert "est 1.0h, he accepted 8.0h" in r["fails"][0]


def test_a_missing_anchor_run_fails_rather_than_passing(tmp_path):
    new = _run_db(tmp_path / "new.sqlite", {"e1": "x"}, {"x": 1.0})
    [r] = run_cases(new, tmp_path, [{"anchor_run": "gone", "anchor_task": "t", "expect": {}}])
    assert r["fails"]


def test_a_case_outside_the_window_is_neither_pass_nor_fail(tmp_path):
    (tmp_path / "old").mkdir()
    _run_db(tmp_path / "old/run.sqlite", {"e1": "t", "e2": "t"}, {"t": 8.0}).close()
    new = _run_db(tmp_path / "new.sqlite", {"e9": "x"}, {"x": 1.0})
    [r] = run_cases(new, tmp_path, [{"anchor_run": "old", "anchor_task": "t", "expect": {"est_ok": True}}])
    assert r.get("out_of_window") and r["fails"] == []


def test_run_cases_reports_a_grown_host_as_uncheckable_not_as_broken(tmp_path):
    """End to end, because the defect was that a real failure list carried a
    case the harness had no business judging — so the fix has to show up in what
    `run_cases` returns, not only in the helper."""
    (tmp_path / "old").mkdir()
    _run_db(tmp_path / "old/run.sqlite", {"e1": "t", "e2": "t"}, {"t": 0.25}).close()
    grown = {f"f{i}": "x" for i in range(10)} | {"e1": "x", "e2": "x"}
    new = _run_db(tmp_path / "new.sqlite", grown, {"x": 18.0})
    [r] = run_cases(new, tmp_path, [{"anchor_run": "old", "anchor_task": "t",
                                     "expect": {"est_ok": True}}])
    assert r["fails"] == [], "18h against 0.25h is not his verdict failing"
    assert r["blocked"] and "absorbed other work" in r["blocked"]
    assert not r.get("out_of_window"), "the events are all here; the task is not"
