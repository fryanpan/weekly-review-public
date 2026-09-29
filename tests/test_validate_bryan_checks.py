"""The user's 2026-09-14 verifier rules: zero hands-on, zero wall-clock, LoC per day.

Each rule has a case it must flag and a case it must leave alone, because a
check that flags everything passes every "it caught the bug" test.
"""
import json
import sqlite3

from weekly_review.persist import init_schema
from weekly_review.validate import (loc_estimate_mismatches, rationale_conclusion,
                                    zero_hands_on_suspects)
from tests.fakeids import wid


def _db():
    c = sqlite3.connect(":memory:")
    init_schema(c)
    return c


def _task(c, tid, est, hands_on, wall=1.0, typ="code"):
    c.execute("INSERT INTO tasks (task_id, project, title) VALUES (?, 'p', ?)", (tid, tid))
    c.execute("INSERT INTO enriched (task_id, run_id, title, est_human_hours, type) VALUES (?, 'r', ?, ?, ?)",
              (tid, tid, est, typ))
    c.execute("INSERT INTO task_metrics (task_id, run_id, active_hours, labor_hours) VALUES (?, 'r', ?, ?)",
              (tid, hands_on, wall))


_seq = iter(range(10_000))


def _event(c, tid, session, actor, kind, **md):
    n = next(_seq)
    eid = f"e{n}"
    c.execute("""INSERT INTO events (event_id, ts, actor, project, session_id, source_system, source_machine,
                 kind, seq, metadata_json) VALUES (?, '2026-09-01T12:00:00Z', ?, 'p', ?, 'claude_transcript',
                 'm', ?, ?, ?)""", (eid, actor, session, kind, n, json.dumps(md)))
    c.execute("INSERT INTO task_events (task_id, event_id) VALUES (?, ?)", (tid, eid))


def test_zero_hands_on_is_classified_by_why():
    c = _db()
    _task(c, "ask", est=1, hands_on=0.5)
    _event(c, "ask", "s1", "user", "user_message")
    _task(c, "split", est=10, hands_on=0.0)
    _event(c, "split", "s1", "agent", "agent_message")
    _task(c, "fragment", est=2, hands_on=0.0, wall=0.0)
    _event(c, "fragment", "s9", "artifact", "commit", insertions=10)
    _task(c, "bug", est=3, hands_on=0.0)
    _event(c, "bug", "s3", "user", "user_message")
    _event(c, "bug", "s3", "agent", "agent_message")
    _task(c, "autonomous", est=4, hands_on=0.0)
    _event(c, "autonomous", "s4", "agent", "agent_message")
    _task(c, "dispatch", est=4, hands_on=0.02)
    _event(c, "dispatch", "s5", "user", "user_message")
    _event(c, "dispatch", "s5", "agent", "agent_message")
    zh = zero_hands_on_suspects(c)
    assert zh["split"] == [("split", 10)]
    assert zh["no_conversation"] == [("fragment", 2)]
    assert zh["own_messages"] == [("bug", 3)]
    flagged = {t for rows in zh.values() for t, _ in rows}
    assert "autonomous" not in flagged, "agent-triggered end to end is legitimately zero"
    assert "dispatch" not in flagged, "a one-line dispatch is honestly a minute"
    assert "ask" not in flagged


def test_a_substantive_task_whose_estimate_failed_is_still_checked():
    """Week of 2026-09-07 hit the API limit mid-run; two split tasks, one a day
    of work, had no estimate and the split check read PASS."""
    from weekly_review.validate import _est_items, _est_sum, zero_wall_tasks
    c = _db()
    _task(c, "ask", est=1, hands_on=0.5)
    _event(c, "ask", "lead", "user", "user_message")
    for tid, events, artifact in (("big", 21, False), ("shipped", 1, True), ("tiny", 20, False)):
        c.execute("INSERT INTO tasks (task_id, project, title, event_count) VALUES (?, 'p', ?, ?)", (tid, tid, events))
        c.execute("INSERT INTO task_metrics (task_id, run_id, active_hours, labor_hours) VALUES (?, 'r', 0, 0)", (tid,))
        _event(c, tid, "lead", "agent", "commit" if artifact else "agent_message")
    _task(c, "estimated", est=2, hands_on=0.0, wall=0.0)
    _event(c, "estimated", "lead", "agent", "agent_message")
    split = zero_hands_on_suspects(c)["split"]
    assert split == [("estimated", 2), ("big", None), ("shipped", None)] or \
        split == [("estimated", 2), ("shipped", None), ("big", None)]
    assert sorted(t for t, _, _ in zero_wall_tasks(c)) == ["big", "estimated", "shipped"]
    assert _est_sum(split) == "Σest 2h, 2 unestimated"
    assert _est_items(split)[1].endswith("est ?")


def test_a_board_action_alone_is_not_a_conversation():
    c = _db()
    _task(c, "t", est=1, hands_on=0.0)
    _event(c, "t", "board", "agent", "board_action")
    assert zero_hands_on_suspects(c)["no_conversation"] == [("t", 1)]


def _pr(c, tid, number, additions, deletions=0, files=10, kind=None):
    """A PR the task produced: it opened it and it saw it merged, which is what
    the real stream holds. Pass `kind` for a task that did only one of those --
    an attempt it abandoned, or a merge it merely watched land."""
    for k in ((kind,) if kind else ("pr_opened", "pr_merged")):
        _event(c, tid, "gh", "artifact", k, additions=additions, deletions=deletions,
               changedFiles=files, number=number, repo="o/r")


def test_loc_rule_flags_the_two_sides_against_different_rates_and_dedupes_prs():
    """The user, 2026-09-15: "It's fine if the estimator is systematically too low
    on est_h by a bit. That's conservative... I'm more likely to make bad
    decisions from the multiplier numbers being too high rather than too low."

    So above, his 1000 lines a day; below, the estimator's own observed rate,
    which tolerates the bias he accepted and still catches what is far out of
    whack under it. A task 5x under his rule is conservative; one 15x under is a
    task credited with a diff somebody else wrote.
    """
    c = _db()
    _task(c, "fits", est=6, hands_on=1)            # 1000 lines = 8h; counted twice it would be 16h
    _pr(c, "fits", 1, 1000)
    _pr(c, "fits", 1, 1000, kind="pr_opened")      # same PR twice must not make 2000 lines
    _task(c, "lenient", est=3, hands_on=1)         # 2.7x under his rule
    _pr(c, "lenient", 4, 1000)
    _task(c, "conservative", est=1.5, hands_on=1)  # 5.3x under his rule, 2.9x under the observed rate
    _pr(c, "conservative", 2, 1000)
    _task(c, "far_under", est=0.5, hands_on=1)     # 16x under his rule, 8.7x under the observed rate
    _pr(c, "far_under", 5, 1000)
    _task(c, "high", est=40, hands_on=1)
    _pr(c, "high", 3, 1000)
    checked, flagged, under = loc_estimate_mismatches(c)
    assert checked == 5
    assert {t for t, *_ in flagged} == {"far_under", "high"}, \
        "conservative is accepted; far out of whack is not, in either direction"
    assert under == 4, "and the accepted conservatism stays counted, not hidden by the PASS"


def test_loc_rule_does_not_price_a_diff_the_estimator_was_told_to_ignore():
    """The two sides of this comparison have to cover the same artifacts.

    One shipped PR is owned by ONE task; enrichment shows every other holder
    "CREDITED TO ANOTHER TASK — do not count", so their estimate is for the
    small increment they own. Counting the whole diffstat against that estimate
    compares an estimate over owned artifacts with lines over all attached
    ones, and the gap only ever points one way. Measured on the three era weeks
    of 2026-09: 14 of 14 flags read "estimate too low" and none read too high;
    5 of the 14 were this, including a task whose own rationale said "a trivial
    icon swap — 9 lines added" being priced at 1,930.
    """
    c = _db()
    _task(c, "owner", est=8, hands_on=1)
    _pr(c, "owner", 7, 1000)
    _task(c, "merged_it", est=0.5, hands_on=1)      # holds the same PR, owns none of it
    _pr(c, "merged_it", 7, 1000)
    _task(c, "its_own", est=0.5, hands_on=1)        # an undisputed PR, genuinely off
    _pr(c, "its_own", 8, 1000)
    c.execute("""INSERT INTO artifact_credit (run_id, identity, owner_task_id, duplicate_task_ids_json)
                 VALUES ('r', 'pr:r:7', 'owner', '["merged_it"]')""")

    checked, flagged, _ = loc_estimate_mismatches(c)
    assert {t for t, *_ in flagged} == {"its_own"}, \
        "a demoted placement must not be priced; an undisputed one still must be"
    assert checked == 2, "the demoted holder drops out of the denominator too"


def test_loc_rule_skips_what_it_cannot_speak_for():
    c = _db()
    _task(c, "tiny", est=40, hands_on=1)
    _pr(c, "tiny", 1, 100)
    _task(c, "vendored", est=1, hands_on=1)
    _pr(c, "vendored", 2, 50_000, files=500)
    _task(c, "generated", est=1, hands_on=1)
    _pr(c, "generated", 3, 5000, files=2)
    _task(c, "sweep", est=1, hands_on=1)
    _pr(c, "sweep", 4, 3000, deletions=2900)
    _task(c, "research", est=40, hands_on=1, typ="research")
    _pr(c, "research", 5, 1000)
    assert loc_estimate_mismatches(c) == (0, [], 0)


def test_loc_rule_uses_commits_only_without_a_pr():
    c = _db()
    _task(c, "commits", est=8, hands_on=1)
    _event(c, "commits", "git", "artifact", "commit", insertions=600, deletions=0, files_changed=5)
    _event(c, "commits", "git", "artifact", "commit", insertions=400, deletions=0, files_changed=5)
    _task(c, "both", est=8, hands_on=1)
    _pr(c, "both", 9, 1000)
    _event(c, "both", "git", "artifact", "commit", insertions=5000, deletions=0, files_changed=50)
    assert loc_estimate_mismatches(c) == (2, [], 0)


def _timed(c, tid, ts, actor="agent", kind="tool_call", lane=None):
    n = next(_seq)
    md = {"agent_lane": lane} if lane else {}
    c.execute("""INSERT INTO events (event_id, ts, actor, project, session_id, source_system, source_machine,
                 kind, seq, metadata_json) VALUES (?, ?, ?, 'p', 's', 'claude_transcript', 'm', ?, ?, ?)""",
              (f"e{n}", ts, actor, kind, n, json.dumps(md)))
    c.execute("INSERT INTO task_events (task_id, event_id) VALUES (?, ?)", (tid, f"e{n}"))


def test_oversized_flags_many_days_or_many_lanes_and_leaves_a_normal_task():
    from weekly_review.validate import oversized_tasks
    c = _db()
    for tid in ("week", "fanout", "normal"):
        _task(c, tid, 5.0, 0.1)
    for d in ("01", "02", "03"):
        _timed(c, "week", f"2026-09-{d}T10:00:00Z")
    for i in range(101):
        _timed(c, "fanout", "2026-09-01T10:00:00Z", lane=f"agent-{i}")
    for d in ("01", "02"):
        _timed(c, "normal", f"2026-09-{d}T10:00:00Z")
    _timed(c, "normal", "2026-09-05T10:00:00Z", kind="board_action")
    _timed(c, "normal", "2026-09-06T10:00:00Z")
    c.execute("""UPDATE events SET metadata_json = '{"card": "t-AAA"}'
                 WHERE event_id = (SELECT MAX(event_id) FROM task_events WHERE task_id = 'normal')""")
    assert {r[0] for r in oversized_tasks(c)} == {"week", "fanout"}, "a status post on its card is not a day of work"


def test_work_unit_spread_flags_a_split_card_a_split_thread_and_a_sprint_task():
    from weekly_review.validate import work_unit_spread
    c = _db()
    for tid in ("a", "b", "c", "sprint", "ok1", "ok2"):
        _task(c, tid, 1.0, 0.1)
    for tid in ("a", "b", "c"):                              # one card's work on 3 tasks
        _event(c, tid, "s", "user", "user_message", task_id="t-SPLIT")
    _event(c, "a", "lf", "user", "user_message", doc_id="d-1", thread_id="th")
    _event(c, "b", "lf", "user", "user_message", doc_id="d-1", thread_id="th")
    _event(c, "c", "s", "agent", "tool_call", card="t-OTHER", doc_id="d-1", thread_id="th")  # not his comment
    for i in range(11):                                      # one task holding 11 cards
        _event(c, "sprint", "lead", "agent", "subagent_input", card=f"t-S{i}")
    _event(c, "ok1", "board", "user", "workspace_touch", task_id="t-TWO")
    _event(c, "ok2", "board", "user", "workspace_touch", task_id="t-TWO")  # 2 tasks is allowed
    _event(c, "ok1", "board", "user", "workspace_touch", task_id=wid("not-a-card"))
    spread, broad = work_unit_spread(c)
    assert spread == [("t-SPLIT", 3)]
    assert broad == [("sprint", 11)]
    _event(c, "c", "lf", "user", "user_message", doc_id="d-1", thread_id="th")
    assert ("d-1#th", 3) in work_unit_spread(c)[0]


def test_a_board_sweep_is_not_eleven_pieces_of_work():
    """A stamp is not work, so the card check may not read one as a merge.

    `task_transition` writes a `board_action` row AND leaves its own tool call
    and result in the transcript, so one sweep of the board lands three rows on
    each card it touched in about fifteen seconds. Read straight, the worst task
    on the week of 2026-08-17 "held" 53 board cards; it has exactly one it did
    anything to but stamp. Every card finding on both dense weeks — 13 and 7 —
    was this, and the concurrency check next door already refuses the same rows
    for the same reason.
    """
    from weekly_review.validate import work_unit_spread
    c = _db()
    _task(c, "sweeper", 1.0, 0.1)
    for tid in ("split1", "split2", "split3"):
        _task(c, tid, 1.0, 0.1)
    for i in range(20):                       # one status sweep across 20 cards
        _event(c, "sweeper", "lead", "agent", "tool_call", card=f"t-C{i}")
        _event(c, "sweeper", "lead", "agent", "tool_result", card=f"t-C{i}")
        _event(c, "sweeper", "board", "agent", "board_action", task_id=f"t-C{i}")
    for tid in ("split1", "split2", "split3"):  # and the same card stamped on 3 tasks
        _event(c, tid, "board", "agent", "board_action", task_id="t-STAMPED")
    assert work_unit_spread(c) == ([], []), "bookkeeping is not a split or a merge"

    # but the check still reads everything that is not a stamp
    _event(c, "sweeper", "lead", "agent", "subagent_input", card="t-REAL")
    for tid in ("split1", "split2", "split3"):
        _event(c, tid, "s", "user", "user_message", task_id="t-STAMPED")
    spread, broad = work_unit_spread(c)
    assert spread == [("t-STAMPED", 3)] and broad == []


def _spanned(c, tid, first, last, refs=(), title=None):
    c.execute("""INSERT INTO tasks (task_id, project, title, first_ts, last_ts, issue_refs_json)
                 VALUES (?, 'p', ?, ?, ?, ?)""",
              (tid, title or tid, first, last, json.dumps(list(refs))))


def _in_lane(c, tid, session, lane, ts):
    n = next(_seq)
    eid = f"L{n}"
    c.execute("""INSERT INTO events (event_id, ts, actor, project, session_id, source_system,
                 source_machine, kind, seq, metadata_json)
                 VALUES (?, ?, 'agent', 'p', ?, 'claude_transcript', 'm', 'agent_message', ?, ?)""",
              (eid, ts, session, n, json.dumps({"agent_lane": lane})))
    c.execute("INSERT INTO task_events (task_id, event_id) VALUES (?, ?)", (tid, eid))


def test_one_ticket_over_several_days_is_not_duplicate_work():
    """A shared ticket and an overlapping window are not evidence of duplication.

    A task's window is its first event to its last, so anything long-running
    overlaps its whole project, and a ticket picked up again tomorrow is the
    normal shape of a multi-day ticket. Read that way, one client ticket worked
    over three days on the week of 2026-08-17 read as SIX duplicate pairs, none
    of which shared a single shipped artifact. What a cut conversation looks
    like instead is a lane alternating between the two tasks — 189 times in one
    lane on the week of 2026-09-07, against a worst case of 2 on all six.
    """
    from weekly_review.validate import duplicate_work_pairs
    c = _db()

    # chapter one, then chapter two: the lane hands over once and never returns
    _spanned(c, "mon", "2026-08-17T09:00:00Z", "2026-08-18T17:00:00Z", refs=["TIC-1"])
    _spanned(c, "tue", "2026-08-18T09:00:00Z", "2026-08-19T17:00:00Z", refs=["TIC-1"])
    for h in (9, 10, 11):
        _in_lane(c, "mon", "s1", "main", f"2026-08-17T{h:02d}:00:00Z")
    for h in (9, 10, 11):
        _in_lane(c, "tue", "s1", "main", f"2026-08-18T{h:02d}:00:00Z")
    assert duplicate_work_pairs(c) == [], "consecutive chapters of one ticket"

    # one conversation cut in two: the same lane alternates all morning
    _spanned(c, "cutA", "2026-09-07T09:00:00Z", "2026-09-07T17:00:00Z", refs=["TIC-2"])
    _spanned(c, "cutB", "2026-09-07T09:05:00Z", "2026-09-07T17:00:00Z", refs=["TIC-2"])
    for i in range(8):
        _in_lane(c, "cutA" if i % 2 == 0 else "cutB", "s2", "main",
                 f"2026-09-07T09:{i * 5:02d}:00Z")
    pairs = duplicate_work_pairs(c)
    assert len(pairs) == 1 and "cutA" in pairs[0] and "cutB" in pairs[0]
    assert "alternates 7x" in pairs[0], pairs[0]


def _said(c, tid, ts, text):
    n = next(_seq)
    c.execute("""INSERT INTO events (event_id, ts, actor, project, session_id, source_system,
                 source_machine, kind, seq, content_snippet, metadata_json)
                 VALUES (?, ?, 'agent', 'p', 's', 'claude_transcript', 'm', 'agent_message', ?, ?, '{}')""",
              (f"S{n}", ts, n, text))
    c.execute("INSERT INTO task_events (task_id, event_id) VALUES (?, ?)", (tid, f"S{n}"))


def test_polling_flags_an_agent_that_answered_it_was_doing_nothing():
    """The mechanism, not a proxy for it.

    `poll` is 7d441259fd's shape: woken by board flap through an evening, the
    agent replies "(Holding.)" and the 30-minute stitch joins those replies into
    hours of wall clock. `busy` is what the old minute-density rule could not
    tell apart from it — an agent working steadily whose events are simply
    sparse in time, which on the April week described a third of every task with
    an hour of wall clock."""
    from weekly_review.validate import polling_held_wall
    c = _db()
    _task(c, "poll", 1.0, 0.0, wall=2.0)
    _task(c, "busy", 1.0, 0.0, wall=2.0)
    for h, txt in ((10, "(Holding.)"), (11, "(Flap noted; no change. Holding.)"),
                   (12, "(Still flapping — holding.)")):
        _said(c, "poll", f"2026-09-01T{h}:00:00Z", txt)
    for ts in ("10:00", "10:20", "10:40", "11:00", "11:20", "11:40", "12:00"):
        _timed(c, "busy", f"2026-09-01T{ts}:00Z")
    _said(c, "busy", "2026-09-01T12:30:00Z",
          "(I read the whole module before touching it.) The route table now owns "
          "the front door, so both servers share one handler and the duplicated "
          "branch in server.ts is gone. Tests pass.")
    assert [r[0] for r in polling_held_wall(c)] == ["poll"]
    assert polling_held_wall(c)[0][1] == 3, "counts the replies, not the events"


def test_asides_that_hold_no_wall_clock_open_are_not_a_polling_agent():
    """Two ways of not being the defect, and the check has to clear both.

    `aside` writes one parenthetical and then answers, which is how anyone
    writes. `quick` writes two four minutes apart — a pattern, but it holds no
    wall clock open, and being held open is what the check is about. Without the
    span floor the era weeks report 0/3/2 instead of 0/1/1, and the two extra
    tasks cover 0.0h and 0.4h between them."""
    from weekly_review.validate import polling_held_wall
    c = _db()
    _task(c, "aside", 1.0, 0.0, wall=2.0)
    _said(c, "aside", "2026-09-01T10:00:00Z", "(one moment)")
    _said(c, "aside", "2026-09-01T10:05:00Z", "Done — the roster is up and Notion has it.")
    _task(c, "quick", 1.0, 0.0, wall=2.0)
    _said(c, "quick", "2026-09-01T10:00:00Z", "(checking)")
    _said(c, "quick", "2026-09-01T10:04:00Z", "(still checking)")
    assert polling_held_wall(c) == []


def test_estimate_drift_flags_unchanged_work_that_moved_over_2x(tmp_path):
    from weekly_review.validate import estimate_drift
    prev = tmp_path / "prev.sqlite"
    p = sqlite3.connect(prev)
    init_schema(p)
    c = _db()
    for db, ests in ((p, {"a": 0.25, "b": 4.0, "grown": 1.0}), (c, {"a": 1.5, "b": 6.0, "grown": 9.0})):
        for tid, e in ests.items():
            n = 500 if (db is c and tid == "grown") else 100
            db.execute("INSERT INTO tasks (task_id, project, title, event_count) VALUES (?, 'p', ?, ?)", (tid, tid, n))
            db.execute("INSERT INTO enriched (task_id, run_id, title, est_human_hours) VALUES (?, 'r', ?, ?)",
                       (tid, tid, e))
    p.commit()
    p.close()
    assert estimate_drift(c, prev) == [("a", 0.25, 1.5)]


def _raw(c, tid, actor, kind, source="claude_transcript", session="s", ts="2026-09-01T12:00:00Z", **md):
    """An event, attached to `tid` unless it is None."""
    n = next(_seq)
    eid = f"e{n}"
    c.execute("""INSERT INTO events (event_id, ts, actor, project, session_id, source_system, source_machine,
                 kind, seq, metadata_json) VALUES (?, ?, ?, 'p', ?, ?, 'm', ?, ?, ?)""",
              (eid, ts, actor, session, source, kind, n, json.dumps(md)))
    if tid:
        c.execute("INSERT INTO task_events (task_id, event_id) VALUES (?, ?)", (tid, eid))
    return eid


def _board_task(c, tid, bt, creator, review=0, dictation=0, last=None):
    c.execute("UPDATE tasks SET board_task = ? WHERE task_id = ?", (bt, tid))
    if creator:
        c.execute("INSERT OR REPLACE INTO board_tasks (board_task, creator_kind, his_review, his_dictation, his_last_ask)"
                  " VALUES (?, ?, ?, ?, ?)", (bt, creator, review, dictation, last))


def test_split_exempts_only_agent_created_board_tasks_he_never_asked_about():
    """The user, 2026-09-14: an agent-created board task done fully by agents with
    no review request from him legitimately has no hands-on time."""
    c = _db()
    _task(c, "ask", est=1, hands_on=0.5)
    _raw(c, "ask", "user", "user_message", session="lead")
    cases = {
        "agent_only": ("t-A", "agent", 0, 0),
        "he_created": ("t-B", "person", 0, 0),
        "he_answered": ("t-C", "agent", 1, 0),
        "he_dictated": ("t-D", "agent", 0, 1),
        "he_commented": ("t-E", "agent", 0, 0),
        "he_replied": ("t-G", "agent", 0, 0),
        "unknown_creator": ("t-F", None, 0, 0),
    }
    for tid, (bt, creator, review, dictation) in cases.items():
        _task(c, tid, est=5, hands_on=0.0)
        _board_task(c, tid, bt, creator, review, dictation)
        _raw(c, tid, "agent", "agent_message", session="lead")
    _task(c, "no_board", est=5, hands_on=0.0)
    _raw(c, "no_board", "agent", "agent_message", session="lead")
    # His comment on t-E and reply on t-G sit on another task: they still count.
    _raw(c, "ask", "user", "user_message", source="live_feedback", session="lf", task_id="t-E")
    _raw(c, "ask", "user", "workspace_touch", source="live_feedback", session="lf", task_id="t-G",
         activity_type="reply")
    # Opening the doc is reading, not a request for hands-on.
    _raw(c, "ask", "user", "workspace_touch", source="live_feedback", session="lf", task_id="t-A",
         activity_type="doc_open")
    zh = zero_hands_on_suspects(c)
    assert [t for t, _ in zh["agent_only"]] == ["agent_only"]
    assert {t for t, _ in zh["split"]} == {"he_created", "he_answered", "he_dictated", "he_commented",
                                            "he_replied", "unknown_creator", "no_board"}


def test_a_board_task_holding_every_ask_he_made_on_it_is_legitimately_small():
    """Two minutes of reading an answer is under the 3-minute floor honestly, and
    so is ten seconds when he answered right after acting elsewhere, or a short
    task title typed. A task holding less than its asks' modelled time lost it,
    an ask on another task is a split, and an ask the board log has inside the
    window that the window's events lack went missing."""
    c = _db()
    c.execute("INSERT INTO run_meta (run_id, period_start, period_end, created_at) VALUES ('r', '2026-09-01', '2026-09-07', 'x')")
    _task(c, "ask", est=1, hands_on=0.5)
    _raw(c, "ask", "user", "user_message", session="lead")
    answer, create = "decision.answered", "task.created"
    # name: (board task, creator, review, dictation, hands-on h, his ask in the window, where it sits, last ask)
    cases = {
        "read_answer": ("t-A", "agent", 1, 0, 2 / 60, answer, "here", None),
        "brief_answer": ("t-F", "agent", 1, 0, 10 / 3600, answer, "here", None),
        "lost_reading": ("t-B", "agent", 1, 0, 0.0, answer, "here", None),
        "short_of_reading": ("t-G", "agent", 1, 0, 1 / 60, answer, "here", None),
        "he_created": ("t-C", "person", 0, 0, 75 / 3600, create, "here", None),
        "he_created_untimed": ("t-H", "person", 0, 0, 10 / 3600, create, "here", None),
        "answered_elsewhere": ("t-I", "agent", 1, 0, 2 / 60, answer, "ask", None),
        "answered_last_week": ("t-J", "agent", 1, 0, 0.0, None, None, "2026-08-30T22:00:00+00:00"),
        "created_last_week": ("t-K", "person", 0, 0, 0.0, None, None, "2026-08-31T23:59:00+00:00"),
        "later_ask_missing": ("t-D", "agent", 1, 1, 2 / 60, answer, "here", "2026-09-01T20:00:00+00:00"),
        "asked_this_week_unseen": ("t-L", "agent", 1, 0, 0.0, None, None, "2026-09-02T09:00:00+00:00"),
        "never_answered": ("t-E", "agent", 0, 0, 2 / 60, None, None, None),
    }
    for n, (tid, (bt, creator, review, dictation, h, ask, where, last)) in enumerate(cases.items()):
        ts = f"2026-09-01T{n:02d}:30:00Z"
        _task(c, tid, est=5, hands_on=h)
        _board_task(c, tid, bt, creator, review, dictation, last or (ts.replace("Z", "+00:00") if ask else None))
        _raw(c, tid, "agent", "agent_message", session="lead")
        if ask:
            _raw(c, tid if where == "here" else where, "user", "board_action", source="workspace_board", session="board",
                 ts=ts, board_event=ask, task_id=bt, review_item_id=f"r-{n}", words=10)
    # He commented on another doc ten seconds before answering t-F.
    _raw(c, None, "user", "workspace_touch", source="live_feedback", session="doc", ts="2026-09-01T01:29:50Z",
         activity_type="comment", doc_id="d-other")
    zh = zero_hands_on_suspects(c)
    assert sorted(t for t, _ in zh["asks_here"]) == ["answered_last_week", "brief_answer", "created_last_week",
                                                      "he_created", "read_answer"]
    assert [t for t, _ in zh["agent_only"]] == ["never_answered"]
    assert {t for t, _ in zh["split"]} == {"lost_reading", "short_of_reading", "he_created_untimed", "answered_elsewhere",
                                            "later_ask_missing", "asked_this_week_unseen"}


def test_work_with_no_board_task_that_only_agents_started_is_not_a_split():
    """Week of 2026-09-07: 30 split suspects with no board task were long agent
    stretches started by peers, board notices and schedules."""
    c = _db()
    _task(c, "ask", est=1, hands_on=0.5)
    _raw(c, "ask", "user", "user_message", session="lead")
    cases = {"by_agents": ["agent", "agent"], "by_him": ["agent", "him"], "unknown": ["agent", None],
             "carded_by_agents": ["agent"]}
    for tid, trigs in cases.items():
        _task(c, tid, est=5, hands_on=0.0)
        for trig in trigs:
            _raw(c, tid, "agent", "agent_message", session="lead", **({"trigger": trig} if trig else {}))
    _board_task(c, "carded_by_agents", "t-H", "person")
    zh = zero_hands_on_suspects(c)
    assert [t for t, _ in zh["agent_triggered"]] == ["by_agents"]
    assert {t for t, _ in zh["split"]} == {"by_him", "unknown", "carded_by_agents"}, \
        "a board task he created needs hands-on whoever woke the agent"


def test_work_with_no_board_task_that_his_dictation_started_holds_it_and_is_legitimately_small():
    """bf56f86e52, week of 2026-09-07: his dictation to a board, naming no task,
    woke a lead for a day of work. With the dictation on the work and scored,
    the rest of the day's turns are peers' and notices'."""
    c = _db()
    _task(c, "ask", est=1, hands_on=0.5)
    _raw(c, "ask", "user", "user_message", session="lead")
    # name: (hands-on h, where the dictation sits, its board, the wake's board, wake delay s, lane, typed turn)
    cases = {
        "dictated_here": (2 / 60, "here", "w-1", "w-1", 1, False, False),
        "dictated_to_a_mate": (2 / 60, "here", "w-1", "w-1", 1, True, False),
        "dictated_elsewhere": (2 / 60, "ask", "w-1", "w-1", 1, False, False),
        "lost_speaking": (0.0, "here", "w-1", "w-1", 1, False, False),
        "other_board": (2 / 60, "here", "w-1", "w-2", 1, False, False),
        "wake_long_after": (2 / 60, "here", "w-1", "w-1", 300, False, False),
        "typed_turn_too": (2 / 60, "here", "w-1", "w-1", 1, False, True),
        "unknown_turn_too": (2 / 60, "here", "w-1", "w-1", 1, False, None),
        "on_a_board_task": (2 / 60, "here", "w-1", "w-1", 1, False, False),
    }
    for n, (tid, (h, where, ws, wake_ws, delay, lane, typed)) in enumerate(cases.items()):
        spoke = f"2026-09-01T{n:02d}:30:00Z"
        woke = f"2026-09-01T{n:02d}:{30 + delay // 60:02d}:{delay % 60:02d}Z"
        _task(c, tid, est=5, hands_on=h)
        _raw(c, tid if where == "here" else where, "user", "board_action", source="workspace_board", session="board",
             ts=spoke, board_event="voice.request", workspace_id=ws, words=24)
        _raw(c, tid, "agent", "agent_message", session="lead", ts=f"2026-09-01T{n:02d}:00:00Z", agent_lane="main",
             trigger="agent", turn=n * 10)
        turn = {"agent_lane": "main", "trigger": "him", "turn": n * 10 + 1, "ask_wake": "voice.request",
                "ask_wake_ws": wake_ws, "ask_wake_at": woke}
        _raw(c, None if lane else tid, "agent", "tool_call", session="lead", ts=woke, **turn)
        if lane:
            _raw(c, tid, "agent", "tool_call", session="lead", ts=f"2026-09-01T{n:02d}:45:00Z", agent_lane=f"agent-{n}")
        if typed is not False:
            _raw(c, tid, "agent", "agent_message", session="lead", ts=f"2026-09-01T{n:02d}:50:00Z", agent_lane="main",
                 turn=n * 10 + 2, **({"trigger": "him"} if typed else {}))
    # A board task he created needs its own asks, whatever woke the agent.
    _board_task(c, "on_a_board_task", "t-Z", "person")
    zh = zero_hands_on_suspects(c)
    assert sorted(t for t, _ in zh["asks_here"]) == ["dictated_here", "dictated_to_a_mate"]
    assert {t for t, _ in zh["split"]} == {"dictated_elsewhere", "lost_speaking", "other_board", "wake_long_after",
                                            "typed_turn_too", "unknown_turn_too", "on_a_board_task"}


def test_a_subagent_lane_inherits_the_trigger_of_the_turn_that_dispatched_it():
    c = _db()
    _task(c, "ask", est=1, hands_on=0.5)
    _raw(c, "ask", "user", "user_message", session="lead")
    _raw(c, None, "agent", "agent_message", session="lead", ts="2026-09-01T10:00:00Z", agent_lane="main", trigger="agent")
    _raw(c, None, "agent", "agent_message", session="lead", ts="2026-09-01T11:00:00Z", agent_lane="main", trigger="him")
    for tid, start in (("from_a_peer", "2026-09-01T10:30:00Z"), ("from_his_ask", "2026-09-01T11:30:00Z")):
        _task(c, tid, est=5, hands_on=0.0)
        _raw(c, tid, "agent", "agent_message", session="lead", ts="2026-09-01T09:00:00Z", agent_lane="main", trigger="agent")
        _raw(c, tid, "agent", "tool_call", session="lead", ts=start, agent_lane=f"agent-{tid}")
    zh = zero_hands_on_suspects(c)
    assert [t for t, _ in zh["agent_triggered"]] == ["from_a_peer"]
    assert [t for t, _ in zh["split"]] == ["from_his_ask"]


def test_a_teammate_inherits_the_trigger_of_the_turn_that_messaged_it():
    """155f6b4728: a teammate a peer's turn started was messaged by his turn."""
    c = _db()
    _task(c, "ask", est=1, hands_on=0.5)
    _raw(c, "ask", "user", "user_message", session="lead")
    _raw(c, None, "agent", "agent_message", session="lead", ts="2026-09-01T10:00:00Z", agent_lane="main", trigger="agent")
    _raw(c, None, "agent", "tool_call", session="lead", ts="2026-09-01T11:00:00Z", agent_lane="main", trigger="him",
         tool_name="SendMessage")
    _raw(c, None, "agent", "tool_call", session="lead", ts="2026-09-01T12:00:00Z", agent_lane="main", trigger="him",
         tool_name="SendMessage")
    for tid, got in (("messaged_by_him", "2026-09-01T11:00:01Z"), ("from_another_mate", "2026-09-01T12:05:00Z")):
        _task(c, tid, est=5, hands_on=0.0)
        lane = f"agent-{tid}"
        _raw(c, tid, "agent", "agent_message", session="lead", ts="2026-09-01T09:00:00Z", agent_lane="main", trigger="agent")
        _raw(c, tid, "agent", "tool_call", session="lead", ts="2026-09-01T10:30:00Z", agent_lane=lane)
        _raw(c, None, "agent", "subagent_input", session="lead", ts=got, agent_lane=lane)
        _raw(c, tid, "agent", "tool_call", session="lead", ts="2026-09-01T12:30:00Z", agent_lane=lane)
    zh = zero_hands_on_suspects(c)
    assert [t for t, _ in zh["split"]] == ["messaged_by_him"]
    assert [t for t, _ in zh["agent_triggered"]] == ["from_another_mate"]


def test_a_multi_day_board_task_is_not_oversized_but_its_lanes_still_are():
    from weekly_review.validate import oversized_tasks
    c = _db()
    for tid in ("card_week", "card_fanout", "plain_week"):
        _task(c, tid, 5.0, 0.1)
    _board_task(c, "card_week", "t-W", "agent")
    _board_task(c, "card_fanout", "t-F", "agent")
    for d in ("01", "02", "03"):
        _timed(c, "card_week", f"2026-09-{d}T10:00:00Z")
        _timed(c, "plain_week", f"2026-09-{d}T10:00:00Z")
    for i in range(101):
        _timed(c, "card_fanout", "2026-09-01T10:00:00Z", lane=f"agent-{i}")
    assert {r[0] for r in oversized_tasks(c)} == {"card_fanout", "plain_week"}


def test_board_task_audit_maps_board_tasks_and_accounts_for_every_event():
    from weekly_review.validate import board_task_audit
    c = _db()
    for tid in ("one", "twin1", "twin2", "other"):
        _task(c, tid, 1.0, 0.1)
    _board_task(c, "one", "t-ONE", "agent")
    _board_task(c, "twin1", "t-TWO", "agent")
    _board_task(c, "twin2", "t-TWO", "agent")
    _raw(c, "one", "user", "user_message", source="live_feedback", task_id="t-ONE")
    _raw(c, "twin1", "agent", "tool_call", card="t-TWO")
    # An agent moving a card elsewhere is housekeeping: it asks for no measured task.
    _raw(c, "other", "agent", "tool_call", card="t-CALL")
    # Evidence for a board task with no measured task at all.
    _raw(c, "other", "user", "board_action", source="workspace_board", task_id="t-LOST",
         board_event="decision.answered")
    # Housekeeping only: no measured task expected.
    _raw(c, None, "user", "board_action", source="workspace_board", task_id="t-HK", board_event="task.archived")
    # An agent answering its own review item is housekeeping too (4c6932f3a0).
    _raw(c, None, "agent", "board_action", source="workspace_board", task_id="t-SELF", board_event="decision.answered")
    # A read of t-ONE that landed elsewhere, and one that landed nowhere.
    _raw(c, "other", "user", "review_session", source="live_feedback", task_id="t-ONE", fixed_active_sec=1800)
    _raw(c, None, "user", "review_session", source="live_feedback", task_id="t-ONE", fixed_active_sec=900)
    _raw(c, None, "user", "meeting_segment", source="meetings", fixed_active_sec=3600)
    a = board_task_audit(c)
    assert a["mapping"] == {"t-ONE": 1, "t-TWO": 2, "t-LOST": 0}
    assert a["housekeeping_only"] == ["t-CALL", "t-HK", "t-SELF"]
    assert sorted(((bt, host or "", h) for _, bt, host, h in a["misplaced"])) == [
        ("t-ONE", "", 0.25), ("t-ONE", "other", 0.5)]
    assert a["unassigned"][("meetings", "meeting_segment")] == (1, 1.0)
    assert a["unassigned"][("live_feedback", "review_session")] == (1, 0.25)
    assert a["unassigned"][("workspace_board", "board_action")] == (2, 0.0)


def _when(minute, second=0):
    return f"2026-09-10T10:{minute:02d}:{second:02d}Z"


def _three(c):
    for tid in ("one", "two", "three"):
        _task(c, tid, 1.0, 0.1)


def test_a_lane_working_three_tasks_in_one_window_is_flagged():
    from weekly_review.validate import lanes_working_many_tasks
    c = _db()
    _three(c)
    for i, tid in enumerate(("one", "two", "three")):
        _raw(c, tid, "agent", "tool_call", session="s", ts=_when(0, i))
    assert lanes_working_many_tasks(c) == [("s", "main", 3)]


def test_three_tasks_whose_spans_overlap_but_whose_work_does_not():
    """A span said a task held its lane from first event to last, so a stamp
    days later kept a finished task open: at the worst flagged moment on the
    week of 2026-09-07, 24 of the 31 "concurrent" tasks had no event within an
    hour either side."""
    from weekly_review.validate import lanes_working_many_tasks
    c = _db()
    _three(c)
    for tid, (start, end) in (("one", (0, 30)), ("two", (10, 40)), ("three", (20, 50))):
        _raw(c, tid, "agent", "tool_call", session="s", ts=_when(start))
        _raw(c, tid, "agent", "tool_call", session="s", ts=_when(end))
    assert lanes_working_many_tasks(c) == []


def _four(c):
    for tid in ("one", "two", "three", "four"):
        _task(c, tid, 1.0, 0.1)


def test_a_project_is_not_running_79_tasks_because_79_rows_are_open():
    """The project check counted overlapping task SPANS and reported 79 at once.

    A span runs from a task's first event to its last, so a task that took a
    board stamp days after its work finished stays "open" all week — the same
    thing the lane check next door abandoned spans for. Counting tasks with a
    real event inside one window gives 12 for that project on the week of
    2026-08-17 and 16 on 09-07: still over the limit, and now a number about
    who was working rather than about how long rows stay open.
    """
    from weekly_review.validate import projects_working_many_tasks
    c = _db()
    _four(c)
    # four spans that all overlap, whose work never does
    for tid, (a, b) in (("one", (0, 30)), ("two", (10, 40)),
                        ("three", (20, 50)), ("four", (26, 56))):
        _raw(c, tid, "agent", "tool_call", ts=_when(a))
        _raw(c, tid, "agent", "tool_call", ts=_when(b))
    # The fixture's clock sits in the 25-task era, so the limit that makes this
    # test about spans is passed in. The era itself is tested below.
    assert projects_working_many_tasks(c, limit=3) == [], "a span is not occupancy"

    # and four that really are in hand at once
    for i, tid in enumerate(("one", "two", "three", "four")):
        _raw(c, tid, "agent", "tool_call", ts=_when(59, i))
    assert projects_working_many_tasks(c, limit=3) == [("p", 4, 3)]


def test_a_status_sweep_is_not_a_project_working_four_tasks():
    """A stamp is not work here either — one sweep touches every open task."""
    from weekly_review.validate import projects_working_many_tasks
    c = _db()
    _four(c)
    for i, tid in enumerate(("one", "two", "three", "four")):
        _raw(c, tid, "agent", "board_action", source="workspace_board",
             ts=_when(10, i), task_id=f"t-{tid}")
        _raw(c, tid, "agent", "tool_call", ts=_when(10, i), card=f"t-{tid}")
    assert projects_working_many_tasks(c, limit=3) == []


def test_a_board_log_is_not_a_worker():
    """`workspace-board:<id>` is one synthetic session per board, so every task
    the board ever touched shares it — 9 of the week of 2026-09-07's 20 flags,
    one lane of 62. Every row on such a session is a `board_action`, so the
    instant rule is what excuses it; there is no clause naming the session."""
    from weekly_review.validate import lanes_working_many_tasks
    c = _db()
    _three(c)
    for i, tid in enumerate(("one", "two", "three")):
        _raw(c, tid, "user", "board_action", source="workspace_board",
             session="workspace-board:w-1", ts=_when(0, i))
    assert lanes_working_many_tasks(c) == []


def test_a_status_sweep_is_not_three_threads():
    """One `task_transition` call and its result on each of three cards in as
    many seconds is bookkeeping. The only difference between the two halves of
    this fixture is whether the call names a card."""
    from weekly_review.validate import lanes_working_many_tasks
    c = _db()
    _three(c)
    for i, tid in enumerate(("one", "two", "three")):
        _raw(c, tid, "agent", "tool_call", session="s", ts=_when(0, i),
             card=f"t-{i}", tool_name="task_transition")
        _raw(c, tid, "agent", "tool_result", session="s", ts=_when(0, i), card=f"t-{i}")
    assert lanes_working_many_tasks(c) == []

    d = _db()
    _three(d)
    for i, tid in enumerate(("one", "two", "three")):
        _raw(d, tid, "agent", "tool_call", session="s", ts=_when(0, i),
             tool_name="Bash")
        _raw(d, tid, "agent", "tool_result", session="s", ts=_when(0, i))
    assert lanes_working_many_tasks(d) == [("s", "main", 3)]


def test_each_lane_of_a_session_is_its_own_worker():
    """A session runs about twenty lanes; two of them holding two tasks each is
    not one worker holding four."""
    from weekly_review.validate import lanes_working_many_tasks
    c = _db()
    for tid in ("one", "two", "three", "four"):
        _task(c, tid, 1.0, 0.1)
    for i, tid in enumerate(("one", "two")):
        _raw(c, tid, "agent", "tool_call", session="s", ts=_when(0, i), agent_lane="agent-a")
    for i, tid in enumerate(("three", "four")):
        _raw(c, tid, "agent", "tool_call", session="s", ts=_when(0, i), agent_lane="agent-b")
    assert lanes_working_many_tasks(c) == []

    _raw(c, "three", "agent", "tool_call", session="s", ts=_when(0, 5), agent_lane="agent-a")
    assert lanes_working_many_tasks(c) == [("s", "agent-a", 3)]


def test_the_window_slides_so_a_boundary_cannot_hide_a_third_task():
    """Three touches inside two seconds, laid across a five-minute boundary.
    Fixed buckets would split them and report two."""
    from weekly_review.validate import lanes_working_many_tasks
    c = _db()
    _three(c)
    _raw(c, "one", "agent", "tool_call", session="s", ts=_when(4, 59))
    _raw(c, "two", "agent", "tool_call", session="s", ts=_when(5, 0))
    _raw(c, "three", "agent", "tool_call", session="s", ts=_when(5, 1))
    assert lanes_working_many_tasks(c) == [("s", "main", 3)]


def test_a_gap_wider_than_the_window_is_not_concurrency():
    from weekly_review.validate import lanes_working_many_tasks
    c = _db()
    _three(c)
    _raw(c, "one", "agent", "tool_call", session="s", ts=_when(0))
    _raw(c, "two", "agent", "tool_call", session="s", ts=_when(4))
    _raw(c, "three", "agent", "tool_call", session="s", ts=_when(9))
    assert lanes_working_many_tasks(c) == []


def _four_at(c, day, minute=10):
    """Four tasks on one project, all with a real event inside one window."""
    _four(c)
    for i, tid in enumerate(("one", "two", "three", "four")):
        _raw(c, tid, "agent", "tool_call", ts=f"{day}T10:{minute:02d}:{i:02d}Z")


def test_the_project_limit_is_dated_and_its_boundary_is_hard():
    """The user, 2026-09-15: "Keep the threshold at 3 up until end of July 2026.
    And increase it to 25 starting in Aug 2026."

    Four tasks at once is a flag in July and ordinary in August. The day either
    side of the boundary is tested because the one thing that must never happen
    is an era quietly widening to swallow a week that would otherwise fail.
    """
    from weekly_review.validate import projects_working_many_tasks

    c = _db()
    _four_at(c, "2026-07-31")
    assert projects_working_many_tasks(c) == [("p", 4, 3)], "31 July is the old era"

    c = _db()
    _four_at(c, "2026-08-01")
    assert projects_working_many_tasks(c) == [], "1 August is inclusive of the new era"

    # ...and 26 at once is a flag in either era, because the point of the check
    # is still unrealistic parallelism, not a number nobody can exceed.
    c = _db()
    for i in range(26):
        _task(c, f"t{i}", 1.0, 0.1)
        _raw(c, f"t{i}", "agent", "tool_call", ts=f"2026-08-01T10:10:{i:02d}Z")
    assert projects_working_many_tasks(c) == [("p", 26, 25)]


def test_a_window_is_judged_by_the_limit_in_force_when_it_happened():
    """A run spanning the boundary is judged by both limits, not by whichever
    end it started on — so four tasks in July still flag in a window that also
    contains an August day whose own concurrency is fine."""
    from weekly_review.validate import projects_working_many_tasks

    c = _db()
    _four(c)
    for i, tid in enumerate(("one", "two", "three", "four")):
        _raw(c, tid, "agent", "tool_call", ts=f"2026-07-30T10:10:{i:02d}Z")
        _raw(c, tid, "agent", "tool_call", ts=f"2026-08-05T10:10:{i:02d}Z")
    assert projects_working_many_tasks(c) == [("p", 4, 3)]


def test_a_superseded_pr_is_not_a_second_change():
    """Week of 2026-09-07: one task held #13 (opened, abandoned, 1718 lines) and
    #14 (opened and merged, 828 lines) under the same title. The estimator priced
    828 and its rationale said so; the check summed 2546 and flagged it."""
    c = _db()
    _task(c, "relanded", est=2.5, hands_on=1)
    _pr(c, "relanded", 13, 1718, kind="pr_opened")
    _pr(c, "relanded", 14, 828)
    checked, flagged, _ = loc_estimate_mismatches(c)
    assert checked == 1 and not flagged, "the abandoned attempt is not priced"

    # A task with no merged PR at all still gets priced on what it opened —
    # work in flight is work.
    c = _db()
    _task(c, "in_flight", est=0.2, hands_on=1)
    _pr(c, "in_flight", 13, 1718, kind="pr_opened")
    assert [t for t, *_ in loc_estimate_mismatches(c)[1]] == ["in_flight"]


def test_a_pr_a_task_only_saw_merged_is_not_priced_against_it():
    """The comparison presumes the task produced the diff.

    Week of 2026-09-07: a task holding one `pr_merged` and nothing else — no
    opening, no commit — carried a rollup PR of 11,489 lines titled "all past
    measurement work" against 0.66h of measured wall clock. Opening it or
    committing to it is participation; watching it land is not.
    """
    c = _db()
    _task(c, "watcher", est=3.5, hands_on=1)
    _pr(c, "watcher", 7, 11_489, files=60, kind="pr_merged")
    assert loc_estimate_mismatches(c) == (0, [], 0)

    # It is priced when the task opened it...
    c = _db()
    _task(c, "opener", est=3.5, hands_on=1)
    _pr(c, "opener", 7, 11_489, files=60)
    assert [t for t, *_ in loc_estimate_mismatches(c)[1]] == ["opener"]

    # ...and when it has its own commits, even if it only saw the merge.
    c = _db()
    _task(c, "committer", est=3.5, hands_on=1)
    _pr(c, "committer", 7, 11_489, files=60, kind="pr_merged")
    _event(c, "committer", "git", "artifact", "commit", insertions=400,
           deletions=0, files_changed=5)
    assert [t for t, *_ in loc_estimate_mismatches(c)[1]] == ["committer"]


def test_a_stated_total_beats_the_last_figure_in_the_rationale():
    """Both live flags of the est-vs-rationale check, on all three era weeks,
    were this misread -- and under a conclusion-aware read both agree with
    their estimate exactly. The rationales are the real ones."""
    job_search = ("Thirteen commits touching 44 file-changes with 1,625 lines added. An "
                  "expert developer would need roughly 1-2 hours per substantive change "
                  "area -- the firstSeen recovery and the review-card UI are the heaviest "
                  "pieces (~2h each), the rest average ~1h each -- totalling approximately "
                  "14h.")
    assert rationale_conclusion(job_search) == 14.0, "a per-item rate is not the conclusion"

    confluence = ("Roughly 6 pages of structured notes across 4 files, plus debugging a "
                  "Confluence API rendering issue -- an expert technical writer doing this "
                  "cold would take roughly 3-4 hours total, centering around 3.5h.")
    assert rationale_conclusion(confluence) == 3.5


def test_the_stale_eighty_hour_signature_still_reads_as_it_did():
    """The check exists to catch an 80h estimate sitting over a rationale that
    concluded ~1.5h. Preferring a stated total must not soften that, so this is
    the negative test: no total phrase, so the last figure still stands."""
    assert rationale_conclusion("A one-line gate and its PR; an expert would spend "
                                "about 90 minutes on it, call it ~1.5h.") == 1.5
    # ...and a rationale that states a total gets read at its total even when a
    # bigger figure sits later in the sentence as a per-item rate.
    assert rationale_conclusion("Two areas at ~40h each is not what happened here; in "
                                "total this comes to 2h.") == 2.0


def test_a_rationale_naming_no_hours_is_not_judged():
    """Silence is not agreement and it is not disagreement: the check has to
    skip, or every unpriced rationale reads as a mismatch against 0."""
    assert rationale_conclusion("Substantial refactor across the extractor registry.") is None
    assert rationale_conclusion("") is None
    assert rationale_conclusion(None) is None
    assert rationale_conclusion("roughly 0h of work") is None, "zero is not a conclusion"


def _correction(c, tid, before_h, after_h, kind="decision_only", run_id="r"):
    c.execute("INSERT OR REPLACE INTO estimate_corrections "
              "(run_id, task_id, kind, before_h, after_h) VALUES (?, ?, ?, ?, ?)",
              (run_id, tid, kind, before_h, after_h))


def test_a_repriced_estimate_is_not_drift(tmp_path):
    """The correction landing is what this check must let through.

    Both tasks move the same way and by the same ratio between the two runs.
    `decided` was repriced by the run — its stored est is the user's measured
    read-and-reply time, not the estimator's number — so the movement is the
    fix arriving, not the estimator being unreproducible. `real` is the case
    the check exists for and still has to fire, which is the only thing that
    makes the exemption an exemption rather than a mute button.
    """
    from weekly_review.validate import estimate_drift
    prev = tmp_path / "prev.sqlite"
    p = sqlite3.connect(prev)
    init_schema(p)
    c = _db()
    for db, ests in ((p, {"decided": 0.25, "real": 0.25}),
                     (c, {"decided": 1.5, "real": 1.5})):
        for tid, e in ests.items():
            db.execute("INSERT INTO tasks (task_id, project, title, event_count) "
                       "VALUES (?, 'p', ?, 100)", (tid, tid))
            db.execute("INSERT INTO enriched (task_id, run_id, title, est_human_hours) "
                       "VALUES (?, 'r', ?, ?)", (tid, tid, e))
    _correction(c, "decided", 1.5, 0.0044)
    p.commit()
    p.close()
    assert estimate_drift(c, prev) == [("real", 0.25, 1.5)]


def test_a_task_repriced_by_the_PREVIOUS_run_is_exempt_too(tmp_path):
    """The correction can sit on either side of the comparison.

    Re-running a week whose earlier run applied the correction reads the same
    2x, with the record in the older DB. Reading only the current run's table
    would exempt half the population and leave the other half flagged, which is
    a worse output than not exempting at all -- it looks like a partial defect.
    """
    from weekly_review.validate import estimate_drift
    prev = tmp_path / "prev.sqlite"
    p = sqlite3.connect(prev)
    init_schema(p)
    c = _db()
    for db, e in ((p, 0.0044), (c, 1.5)):
        db.execute("INSERT INTO tasks (task_id, project, title, event_count) "
                   "VALUES ('decided', 'p', 'decided', 100)")
        db.execute("INSERT INTO enriched (task_id, run_id, title, est_human_hours) "
                   "VALUES ('decided', 'r', 'decided', ?)", (e,))
    _correction(p, "decided", 1.5, 0.0044)
    p.commit()
    p.close()
    assert estimate_drift(c, prev) == []


def test_a_run_written_before_the_table_existed_exempts_nothing():
    """`OperationalError` has to mean "no corrections", not "skip the check".

    Every run before today has no table, and the honest reading of that is that
    it recorded no corrections -- so its tasks all stay in scope. Swallowing the
    error into a set that exempts everything would silently disable three checks
    on the entire back catalogue.
    """
    from weekly_review.validate import repriced_task_ids
    c = sqlite3.connect(":memory:")
    assert repriced_task_ids(c) == set()


def test_the_rationale_checks_drop_what_the_run_corrected():
    """Pinned at the call site, because the defect was never in a function.

    `repriced_task_ids` can be correct, tested and never reached; the two
    rationale checks live inline in `run_checks`, so the only cheap way to
    assert they consult it is to read the source. 71 of 71 flagged items across
    three checks and three era weeks were this correction being reported as a
    defect.
    """
    import inspect

    from weekly_review import validate

    src = inspect.getsource(validate)
    assert "repriced = repriced_task_ids(c)" in src
    assert 'if r["task_id"] in repriced:' in src


def test_the_run_records_every_estimate_it_replaced():
    """Also a call site: the table is worthless if nothing writes to it.

    The list is built where the repricing happens and handed to persist inside
    the same `with` block as everything else the run stores. It is defined
    OUTSIDE `if enriched:` so a run with no enrichment writes an empty list
    rather than raising NameError on the persist call.
    """
    import inspect

    from weekly_review import cli

    src = inspect.getsource(cli)
    assert "estimate_corrections: list[tuple[str, str, float, float]] = []" in src
    assert ("persist.write_estimate_corrections(conn, run.run_id, estimate_corrections)"
            in src)
    defined = src.index("estimate_corrections: list[tuple[str, str, float, float]] = []")
    guarded = src.index("for tid in calib.decision_only_tasks(tasks, all_events):")
    assert defined < src.rindex("if enriched:", 0, guarded), (
        "the list must exist even when the run enriched nothing")


def test_the_exemption_is_printed_every_run_not_only_when_it_bites():
    """An exemption nobody can see is one nobody can check.

    Three checks now skip a population, and the user's standing rule is not to
    tune the checkers to be lenient. The audit line naming how many estimates
    the run replaced, and what it did to their total, is what lets a reader
    tell a correction that priced 34 decisions from one that swallowed a week.
    It reports PASS with a detail, like the availability line does, so it
    prints on a healthy run too.
    """
    import inspect

    from weekly_review import validate

    src = inspect.getsource(validate)
    assert ('report("estimates the run replaced (exempt from the three checks above)", True,'
            in src)
    assert "FROM estimate_corrections GROUP BY kind" in src
