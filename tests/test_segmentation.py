import dataclasses
from datetime import datetime

from weekly_review.events import Event
from weekly_review.segmentation import (BoardLinks, merged_pr_units, printed_artifact_credit, Task,
                                        _issue_refs, _join_dictations_to_their_work,
                                        _link_units_to_board_tasks, _one_unit_per_merged_pr, segment)
from tests.fakeids import tid


def _uev(ts: str, project: str, text: str, seq: int, session_id: str = "s1") -> Event:
    return Event(
        ts=ts, actor="user", project=project, session_id=session_id,
        source_system="claude_transcript", source_machine="mm",
        kind="user_message", seq=seq,
        content_snippet=text, metadata={"words": len(text.split())},
    )


def _pr(ts: str, project: str, title: str, number: int, seq: int, repo: str | None = None) -> Event:
    md = {"number": number, "additions": 1, "deletions": 0}
    if repo:
        md["repo"] = repo
    return Event(
        ts=ts, actor="artifact", project=project, session_id=f"gh:{project}",
        source_system="github", source_machine="mm",
        kind="pr_merged", seq=seq,
        content_snippet=title, metadata=md,
    )


def _agent(ts: str, project: str, text: str, seq: int, session_id: str = "s1") -> Event:
    return Event(
        ts=ts, actor="agent", project=project, session_id=session_id,
        source_system="claude_transcript", source_machine="mm",
        kind="agent_message", seq=seq,
        content_snippet=text, metadata={"words": len(text.split())},
    )


def _pr_opened(ts: str, project: str, title: str, number: int, seq: int, repo: str | None = None) -> Event:
    md = {"number": number}
    if repo:
        md["repo"] = repo
    return Event(
        ts=ts, actor="artifact", project=project, session_id=f"gh:{project}",
        source_system="github", source_machine="mm",
        kind="pr_opened", seq=seq,
        content_snippet=title, metadata=md,
    )


def test_touch_does_not_pull_last_event_ts_backward():
    """Regression: pass 3 attaches non-user events in chronological order and
    those events may predate the user messages already on the task. The bug
    we caught (task `bed8d391a5` absorbing 285 pre-Apr-12 PRs) was that
    Task.touch unconditionally overwrote last_event_ts, pulling tasks
    backward in time and making them attract unrelated events. last_event_ts
    must only advance forward."""
    from datetime import datetime, timezone
    from weekly_review.segmentation import Task

    t = Task(task_id="t", project="p", title="x")
    later = datetime(2026, 4, 22, 10, 0, tzinfo=timezone.utc)
    earlier = datetime(2026, 3, 31, 10, 0, tzinfo=timezone.utc)
    # Simulate pass 1 (later board comment) then pass 3 (earlier artifact).
    t.touch(0, later, rule="board_card")
    assert t.last_event_ts == later
    t.touch(1, earlier, rule="same_artifact")
    # last_event_ts must still be `later` — pulling backward to `earlier`
    # would make this task look "fresh" and pull in unrelated events.
    assert t.last_event_ts == later
    # first_event_ts, however, MUST advance backward (we now know the task
    # really started earlier than we first thought).
    assert t.first_event_ts == earlier


# ---- _issue_refs() regex tightening (Change 3, Exp F) ------------------------

def test_issue_refs_matches_real_linear_tickets():
    """Real ticket-style identifiers with known and future-friendly prefixes match."""
    assert _issue_refs("fix CLA-2435 and BRY-42 today") == {"CLA-2435", "BRY-42"}
    # Future-friendly: an unknown 2-4-letter prefix is still accepted.
    assert _issue_refs("see BLOC-7 and JME-1001") == {"BLOC-7", "JME-1001"}


def test_issue_refs_ignores_hex_colors_and_bare_decimals():
    """Hex colors and bare `#NNN` counters must not look like ticket refs."""
    assert _issue_refs("background: #374151; border: #111827") == set()
    assert _issue_refs("see PR #1 and issue #42 — also #100300") == set()


def test_issue_refs_ignores_biomedical_vocabulary():
    """`COVID-19`, `IL-6`, `ICD-10` etc. must not be parsed as tickets."""
    assert _issue_refs("COVID-19 cohort with IL-6 elevation and ICD-10 codes") == set()
    assert _issue_refs("GLP-1 agonists, CTLA-4 blockade, PD-1 inhibitors") == set()
    # And mixed with a real ticket — only the ticket survives.
    assert _issue_refs("CLA-2435 covers HLH-2004 criteria") == {"CLA-2435"}


def test_issue_refs_ignores_extended_denylist():
    """Extended denylist: scoring instruments, policy codes, and generic 2-letter
    prefixes must not match as tickets."""
    # Scoring instruments / outcome scales
    assert _issue_refs("SF-36 quality of life with ECOG-2 status") == set()
    assert _issue_refs("MMSE-28 score and GCS-15 baseline") == set()
    # Generic vocabulary
    assert _issue_refs("PR-1 and RT-PCR results") == set()
    # Genetics / development
    assert _issue_refs("MEN-2 syndrome, SDG-3 indicator, WHO-5 wellbeing") == set()



def _commit(ts: str, project: str, title: str, sha: str, seq: int, repo: str | None = None) -> Event:
    md = {"short_sha": sha}
    if repo:
        md["repo"] = repo
    return Event(
        ts=ts, actor="artifact", project=project, session_id=f"git:{project}",
        source_system="git", source_machine="mm",
        kind="commit", seq=seq, content_snippet=title, metadata=md,
    )


def test_manual_merge_absorbs_into_predecessor():
    # Two board tasks, one measured task each; his directive fuses them.
    events = [
        _card_msg("2026-04-15T00:18:00Z", "family-bike-map", "t-AAA", 0),
        _card_msg("2026-04-15T01:42:00Z", "family-bike-map", "t-BBB", 1),
    ]
    assert len(segment(events)) == 2
    merges = [{"project": "family-bike-map", "absorb_first_ts": "2026-04-15T01:42"}]
    merged = segment(events, manual_merges=merges)
    assert len(merged) == 1
    t = merged[0]
    assert t.first_event_ts.isoformat().startswith("2026-04-15T00:18")  # earlier id kept
    assert len(t.event_ids) == 2  # both events absorbed


def test_manual_merge_explicit_target():
    events = [
        _card_msg("2026-04-15T00:18:00Z", "family-bike-map", "t-AAA", 0),
        _card_msg("2026-04-15T01:42:00Z", "family-bike-map", "t-BBB", 1),
    ]
    merges = [{"project": "family-bike-map", "absorb_first_ts": "2026-04-15T01:42",
               "into_first_ts": "2026-04-15T00:18"}]
    assert len(segment(events, manual_merges=merges)) == 1


def test_manual_merge_noop_when_absorb_missing():
    events = [_card_msg("2026-04-15T00:18:00Z", "family-bike-map", "t-AAA", 0)]
    merges = [{"project": "family-bike-map", "absorb_first_ts": "2026-09-09T09:09"}]
    assert len(segment(events, manual_merges=merges)) == 1  # directive is a no-op


def test_an_unlinked_message_starts_no_task():
    """A message with no board task is placed by nothing (the user, 2026-09-17):
    `new_task_default` never fires, and the reply that answers it goes to the
    day's coordination rather than to a task made of his message."""
    events = [
        _uev("2026-04-15T10:00:00Z", "weekly-review", "let's build the digest", 0, session_id="s1"),
        _agent("2026-04-15T10:00:30Z", "weekly-review", "starting on the digest", 1, session_id="s1"),
    ]
    tasks = segment(events)
    assert not any(0 in t.event_ids for t in tasks), [(t.title, t.event_ids) for t in tasks]
    assert not any(step["rule"] == "new_task_default" for t in tasks for step in t.segmentation_trace)
    assert [t.unit_kind for t in tasks if 1 in t.event_ids] == ["coordination"]


def test_delegated_agent_work_reconstructed_as_task():
    # a dispatched lane with no user messages at all (fleet-delegated build)
    events = []
    for i in range(20):
        e = _agent(f"2026-06-10T0{i//10}:{i%10*5:02d}:00Z", "client-a",
                   f"building maps plugin step {i}", i, session_id="fleet-1")
        events.append(dataclasses.replace(e, metadata={**e.metadata, "agent_lane": "agent-maps"}))
    tasks = segment(events)
    assert len(tasks) == 1
    t = tasks[0]
    assert t.delegated is True
    assert t.unit_kind == "lane"
    assert len(t.event_ids) == 20
    assert "maps plugin" in t.title


def test_a_lane_under_the_floor_folds_into_that_days_coordination():
    # 3 stray lane events: below DELEGATED_MIN_EVENTS, so no unit of their own,
    # and not dropped either: they go to the day's coordination unit.
    events = []
    for i in range(3):
        e = _agent(f"2026-06-10T00:0{i}:00Z", "client-a", "stray", i, session_id="fleet-2")
        events.append(dataclasses.replace(e, metadata={**e.metadata, "agent_lane": "agent-stray"}))
    tasks = segment(events)
    assert [t.unit_kind for t in tasks] == ["coordination"]
    assert sorted(tasks[0].event_ids) == [0, 1, 2]


def test_delegated_not_created_when_events_attach_normally():
    # work he asked for on a board task attaches by `ask_turn`; pass 5 stays out
    events = [_asked_msg("2026-06-10T00:00:00Z", "client-a", "t-AAA", 0, turn=1, session_id="s1")]
    for i in range(1, 21):
        e = _agent(f"2026-06-10T00:{i:02d}:00Z", "client-a", f"working {i}", i)
        e.metadata.update({"agent_lane": "main", "turn": 1 + i, "ask_turn": 1})
        events.append(e)
    tasks = segment(events)
    assert len(tasks) == 1
    assert tasks[0].delegated is False


_HT = "https://github.com/arivera/health-tool/pull/255"


def _turned(ev: Event, turn: int) -> Event:
    """The event stamped with its turn, so pass 9 can carry the opening turn
    into the PR's unit rather than leaving it on the day's coordination."""
    return dataclasses.replace(ev, metadata={**ev.metadata, "turn": turn})


def test_a_pr_joins_the_task_that_linked_it_even_in_another_project():
    """health-tool PRs were ingested under a second slug and sat as timeless
    fragments while the session that opened them held the time. The session's
    turn is placed by pass 5 (its day's coordination); the join reads it, and
    the merged PR's unit carries the turn that opened it."""
    events = [
        _turned(_agent("2026-07-25T22:59:00Z", "health-tool", f"Opened {_HT}", 0), 1),
        _pr("2026-07-25T23:06:00Z", "rare-disease-benchmark", "Calorie logger phase one", 255, 1,
            repo="arivera/health-tool"),
    ]
    host = {i: t.task_id for t in segment(events) for i in t.event_ids}
    assert host[1] == host[0]


def test_a_later_conversation_citing_an_old_pr_does_not_absorb_it():
    events = [
        _pr("2026-07-25T23:06:00Z", "rare-disease-benchmark", "Calorie logger phase one", 255, 0,
            repo="arivera/health-tool"),
        _agent("2026-07-30T20:00:00Z", "health-tool", f"looking back at {_HT} for context", 1),
    ]
    host = {i: t.task_id for t in segment(events) for i in t.event_ids}
    assert host[0] != host[1]


def test_the_task_linking_a_pr_most_often_gets_it():
    events = [
        _turned(_agent("2026-07-25T22:50:00Z", "health-tool", f"Opened {_HT}", 0, session_id="s1"), 1),
        _turned(_agent("2026-07-25T22:55:00Z", "health-tool", f"CI green on {_HT}", 1, session_id="s1"), 2),
        _turned(_agent("2026-07-25T23:05:00Z", "ai-team-lead", f"In flight: {_HT}", 2, session_id="s2"), 1),
        _pr("2026-07-25T23:06:00Z", "rare-disease-benchmark", "Calorie logger phase one", 255, 3,
            repo="arivera/health-tool"),
    ]
    host = {i: t.task_id for t in segment(events) for i in t.event_ids}
    assert host[3] == host[0]
    assert host[3] != host[2]


def test_a_pr_nobody_linked_stays_its_own_task():
    events = [
        _agent("2026-07-25T22:59:00Z", "health-tool", "Opened https://github.com/arivera/health-tool/pull/254", 0),
        _pr("2026-07-25T23:06:00Z", "rare-disease-benchmark", "Calorie logger phase one", 255, 1,
            repo="arivera/health-tool"),
    ]
    host = {i: t.task_id for t in segment(events) for i in t.event_ids}
    assert host[1] != host[0]


def _printed(ev: Event, kind: str = "tool_result", **md) -> Event:
    return dataclasses.replace(ev, kind=kind, metadata={**ev.metadata, **md})


def test_a_pr_printed_past_the_snippet_joins_the_task_that_opened_it():
    """A batch that opens several PRs prints most URLs after the 500-character
    snippet; the whole result's PR identities still join."""
    events = [
        _printed(_agent("2026-07-25T22:59:00Z", "health-tool", "created pull requests", 0),
                 printed_prs=["arivera/health-tool#255"], turn=1),
        _pr("2026-07-25T23:06:00Z", "rare-disease-benchmark", "Calorie logger phase one", 255, 1,
            repo="arivera/health-tool"),
    ]
    host = {i: t.task_id for t in segment(events) for i in t.event_ids}
    assert host[1] == host[0]


def test_a_commit_joins_the_task_whose_tool_result_printed_it_as_it_was_made():
    """25 commits on the week of Sep 7 sat as timeless fragments while the
    session that made them, in another project, printed their hashes."""
    events = [
        _printed(_agent("2026-09-09T13:50:20Z", "notes-repo", "[main abc1234] x", 0),
                 printed_shas=["abc1234"]),
        _commit("2026-09-09T06:50:05-07:00", "assistant-b", "Quarterly ledger rollup", "abc1234", 1),
    ]
    host = {i: t.task_id for t in segment(events) for i in t.event_ids}
    assert host[1] == host[0]


def test_an_agent_only_session_that_commits_gets_its_commit():
    events = [
        _printed(_agent("2026-09-09T13:40:00Z", "notes-repo", "starting the woken task", 0, session_id="w"),
                 kind="agent_message", card="t-woken"),
        _printed(_agent("2026-09-09T13:50:20Z", "notes-repo", "committed", 1, session_id="w"),
                 printed_shas=["abc1234"], card="t-woken"),
        _commit("2026-09-09T13:50:05Z", "assistant-b", "Quarterly ledger rollup", "abc1234", 2),
    ]
    host = {i: t.task_id for t in segment(events) for i in t.event_ids}
    assert host[2] == host[0]


def test_a_commit_adopts_the_turn_that_printed_it_when_no_task_holds_that_turn():
    """00b49fbc55, 75414308e7: the woken turn that committed was on no task,
    so the commit had no wall clock beside the work that made it."""
    def turn_ev(ts, text, seq, turn, kind="tool_call", **md):
        e = _printed(_agent(ts, "notes-repo", text, seq, session_id="w"), kind=kind, **md)
        e.metadata.update({"agent_lane": "main", "turn": turn})
        return e

    def run(print_at="2026-09-09T13:50:20Z", host_too=False, bash="git commit -m 'Refresh archive'"):
        events = [
            turn_ev("2026-09-09T13:40:00Z", "refreshing the archive", 0, turn=3, kind="agent_message"),
            # The turn committed, so pass 5 makes it the session's own-edits
            # unit for the day rather than folding it into coordination with
            # the later wake.
            turn_ev("2026-09-09T13:45:00Z", bash, 1, turn=3, tool_name="Bash"),
            turn_ev(print_at, "[main abc1234] x", 2, turn=3, kind="tool_result", printed_shas=["abc1234"]),
            turn_ev("2026-09-09T16:00:00Z", "a later wake", 3, turn=4),
            _commit("2026-09-09T13:50:05Z", "notes-repo", "Refresh archive", "abc1234", 4),
        ]
        if host_too:
            events += [_printed(_agent("2026-09-09T13:50:10Z", "career-tool", "commit", 5, session_id="b"),
                                printed_shas=["abc1234"])]
        tasks = segment(events)
        return tasks, {i: t for t in tasks for i in t.event_ids}

    tasks, host = run()
    assert host[4] is host[0] is host[1] is host[2], "the commit adopts the turn that printed it"
    assert host.get(3) is not host[4], "only that turn"
    assert host[3].unit_kind == "coordination" and host[4].unit_kind == "lead_worktree"
    # A turn that only listed the log an hour later did not make the commit,
    # and as coordination it hosts no artifact by proximity either.
    _, late = run(print_at="2026-09-09T15:00:00Z", bash="git log --oneline -5")
    assert late.get(2) is not late[4], "a later listing is not the turn that made it"
    _, both = run(host_too=True)
    assert both[4] is both[5] and both.get(2) is not both[4], "a task that printed it wins over a loose turn"


def test_a_rebuilt_commit_joins_the_same_project_task_whose_command_ran_at_its_author_time():
    """fc493d0: rebuilt onto main before landing, so the hash its session printed
    was another; its author second matched that session's tool result."""
    def run(result_at="2026-09-08T06:00:58Z", project="ai-team-lead", printer=False):
        events = [
            # A woken session with no message of his.
            _printed(_agent("2026-09-08T05:56:00Z", "ai-team-lead", "shipping the budget watch", 0, session_id="a"),
                     kind="agent_message", card="t-woken"),
            _printed(_agent(result_at, project, "[main fba5060] x", 1, session_id="a"),
                     printed_shas=["fba5060"], card="t-woken"),
            _commit("2026-09-07T23:00:58-07:00", "ai-team-lead", "Budget watch", "fc493d0", 2),
        ]
        if printer:
            events.append(_printed(_agent("2026-09-08T06:01:30Z", "career-tool", "fc493d0", 3, session_id="b"),
                                   printed_shas=["fc493d0"]))
        tasks = segment(events)
        host = {i: t.task_id for t in tasks for i in t.event_ids}
        # Which pass absorbed the commit's own task: a merge records its reason,
        # and only the host that ends up holding the commit is asked.
        holder = next(t for t in tasks if 2 in t.event_ids)
        why = " ".join(tr["detail"] for tr in holder.segmentation_trace if tr["rule"] == "merged_from")
        return host, why

    host, why = run()
    assert host[2] == host[0] and "commit_print_join" in why, "the command that made it"
    assert "commit_print_join" in run(result_at="2026-09-08T06:00:53Z")[1], "5s either side"
    far_host, far_why = run(result_at="2026-09-08T06:01:10Z")
    assert "commit_print_join" not in far_why, "a result 12s off is not the command that made it"
    assert far_host[2] != far_host[0], "and nothing places it by being live nearby: it stays its own task"
    assert "commit_print_join" not in run(project="writing-tool")[1], (
        "only a result in the commit's own project"
    )
    both = run(printer=True)[0]
    assert both[2] == both[3] != both[0], "the task that printed its hash wins"


def test_a_later_git_log_listing_a_commit_does_not_absorb_it():
    events = [
        _commit("2026-09-09T13:50:05Z", "assistant-b", "Quarterly ledger rollup", "abc1234", 0),
        _printed(_agent("2026-09-09T15:00:30Z", "notes-repo", "abc1234 Quarterly", 1),
                 printed_shas=["abc1234"]),
    ]
    dropped: list = []
    tasks = segment(events, dropped=dropped)
    host = {i: t.task_id for t in tasks + dropped for i in t.event_ids}
    assert host[0] != host[1]


def test_the_session_printing_a_commit_most_often_gets_it():
    events = [
        _printed(_agent("2026-09-09T13:50:06Z", "notes-repo", "log", 0, session_id="a"),
                 printed_shas=["abc1234"]),
        _printed(_agent("2026-09-09T13:50:10Z", "career-tool", "commit", 1, session_id="b"),
                 printed_shas=["abc1234"]),
        _printed(_agent("2026-09-09T13:52:00Z", "career-tool", "push", 2, session_id="b"),
                 printed_shas=["abc1234"]),
        _commit("2026-09-09T13:50:05Z", "assistant-b", "Quarterly ledger rollup", "abc1234", 3),
    ]
    host = {i: t.task_id for t in segment(events) for i in t.event_ids}
    assert host[3] == host[1] != host[0]


def test_the_run_counts_printed_artifacts_credited_to_the_task_that_printed_them():
    from weekly_review.segmentation import printed_artifact_credit
    events = [
        _uev("2026-09-09T13:00:00Z", "notes-repo", "write up the survey", 0),
        _printed(_agent("2026-09-09T13:50:20Z", "notes-repo", "made", 1),
                 printed_shas=["abc1234"], printed_prs=["arivera/notes#7"]),
        _commit("2026-09-09T13:50:05Z", "assistant-b", "Quarterly ledger rollup", "abc1234", 2),
        _pr("2026-09-09T13:55:00Z", "assistant-b", "Quarterly ledger", 7, 3, repo="arivera/notes"),
        _commit("2026-09-09T13:51:00Z", "assistant-b", "Unprinted", "fff9999", 4),
        _uev("2026-09-10T13:00:00Z", "career-tool", "update the tracker", 5),
    ]
    tasks = segment(events)
    assert printed_artifact_credit(tasks, events) == {
        "prs_printed": 1, "prs_on_printer": 1, "commits_printed": 1, "commits_on_printer": 1}
    # The same artifacts left on a task of their own are printed but not credited.
    apart = [dataclasses.replace(t, event_ids=[i]) for t in tasks for i in t.event_ids]
    assert printed_artifact_credit(apart, events) == {
        "prs_printed": 1, "prs_on_printer": 0, "commits_printed": 1, "commits_on_printer": 0}
    # Events on no task at all are never credited to each other.
    assert printed_artifact_credit([], events)["commits_on_printer"] == 0


def test_a_pr_merged_on_another_task_is_not_credited_and_a_late_log_is_not_a_print():
    from weekly_review.segmentation import printed_artifact_credit
    events = [
        _printed(_agent("2026-09-09T13:50:20Z", "notes-repo", "made", 0),
                 printed_prs=["arivera/notes#7"], printed_shas=["abc1234"]),
        _pr("2026-09-09T13:55:00Z", "assistant-b", "Quarterly ledger", 7, 1, repo="arivera/notes"),
        dataclasses.replace(_pr("2026-09-09T15:00:00Z", "assistant-b", "Quarterly ledger", 7, 2,
                                repo="arivera/notes"), kind="pr_merged"),
        _commit("2026-09-09T11:00:05Z", "assistant-b", "Earlier commit", "abc1234", 3),
    ]
    t = Task(task_id="x", project="notes-repo", title="any task")
    tasks = [dataclasses.replace(t, event_ids=[0, 1]), dataclasses.replace(t, event_ids=[2, 3])]
    assert printed_artifact_credit(tasks, events) == {
        "prs_printed": 1, "prs_on_printer": 0, "commits_printed": 0, "commits_on_printer": 0}


def test_the_pipeline_prints_the_printed_artifact_credit_after_segmenting():
    import inspect
    from weekly_review import cli
    src = inspect.getsource(cli)
    call = src.find("printed_artifact_credit(tasks, all_events)")
    assert src.find("tasks = segment(all_events") < call < src.find("attach_by_workspace_task(tasks, all_events)")


def test_commits_sharing_words_do_not_chain_onto_each_other():
    """With no conversation between them, two commits sharing title words are
    two pieces of work. Chaining them let an Aug 3 commit collect three days of
    commits by shared words ("server", "share") and hand all of it, as an 80h
    estimate, to a five-minute conversation (run-20260914T191649Z)."""
    proj = "claude-live-feedback-plugin"
    events = [
        _commit("2026-08-03T09:00:00Z", proj, "Voice lookup falls back to the lead agent", "aaa1111", 0),
        _commit("2026-08-04T09:00:00Z", proj, "Voice lookup resolves places by rank", "bbb2222", 1),
        _commit("2026-08-05T09:00:00Z", proj, "Voice lookup reads the goal list", "ccc3333", 2),
    ]
    dropped: list = []
    tasks = segment(events, dropped=dropped)
    assert sorted(t.event_ids for t in tasks + dropped) == [[0], [1], [2]], (
        f"expected three standalone commits; got {[(t.title, t.event_ids) for t in tasks + dropped]}"
    )


def test_a_prs_squash_commit_stays_with_the_pr_without_a_conversation():
    proj = "claude-workspaces-plugin"
    events = [
        _pr_opened("2026-08-29T07:45:00Z", proj, "Voice: a lookup that resolves nothing falls back", 454, 0),
        _commit("2026-08-29T07:52:30Z", proj, "Voice: a lookup that resolves nothing falls back (#454)",
                "ddd4444", 1),
    ]
    tasks = segment(events)
    assert len(tasks) == 1 and sorted(tasks[0].event_ids) == [0, 1], (
        f"a PR and its squash commit are one artifact; got {[(t.title, t.event_ids) for t in tasks]}"
    )


def test_a_short_generic_title_is_not_an_identity():
    proj = "claude-workspaces-plugin"
    events = [
        _commit("2026-08-29T07:45:00Z", proj, "fix tests", "aaa1111", 0),
        _commit("2026-08-29T20:45:00Z", proj, "fix tests", "bbb2222", 1),
    ]
    dropped: list = []
    tasks = segment(events, dropped=dropped)
    assert len(tasks + dropped) == 2, (
        f"two 'fix tests' commits are not one artifact; got {[t.event_ids for t in tasks + dropped]}"
    )


def _comment(ts: str, project: str, doc: str, thread: str, seq: int) -> Event:
    return Event(
        ts=ts, actor="user", project=project, session_id=f"live-feedback:{doc}",
        source_system="live_feedback", source_machine="mm",
        kind="user_message", seq=seq, content_snippet="the mockup header clips",
        # The doc is a task card: the one message shape that still starts a task.
        metadata={"words": 4, "doc_id": doc, "thread_id": thread, "task_id": "t-DOC"},
    )


def test_an_agent_turn_answering_a_doc_comment_joins_the_comments_task():
    """e3838bffd1: the lead's replies to a mockup comment followed the lead's
    own last message, an unrelated question from hours earlier."""
    proj = "claude-workspaces-plugin"
    woken = _agent("2026-09-12T00:15:40Z", proj, "replied on the thread", 2, session_id="lead")
    woken.metadata["wake"] = "d-M|th1"
    events = [
        _agent("2026-09-11T21:00:00Z", proj, "why is the queue empty", 0, session_id="lead"),
        _comment("2026-09-12T00:15:30Z", proj, "d-M", "th1", 1),
        woken,
        _agent("2026-09-12T00:40:00Z", proj, "queue investigation continues", 3, session_id="lead"),
    ]
    host = {i: t.task_id for t in segment(events) for i in t.event_ids}
    assert host[2] == host[1]
    assert host[1] != host[0]
    assert host.get(3) != host[1], "an unstamped turn does not follow the comment"


def test_a_subagent_dispatched_while_answering_a_comment_joins_the_comments_task():
    proj = "claude-workspaces-plugin"

    def lane_ev(ts, text, seq, lane, wake=None):
        e = _agent(ts, proj, text, seq, session_id="lead")
        e.metadata["agent_lane"] = lane
        if wake:
            e.metadata["wake"] = wake
        return e

    events = [
        lane_ev("2026-09-11T21:00:00Z", "why is the queue empty", 0, "main"),
        _comment("2026-09-12T00:15:30Z", proj, "d-M", "th1", 1),
        lane_ev("2026-09-12T00:15:40Z", "dispatching a reviewer", 2, "main", wake="d-M|th1"),
        lane_ev("2026-09-12T00:15:45Z", "reviewing the mockup", 3, "agent-abc"),
        lane_ev("2026-09-12T00:19:00Z", "review done", 4, "agent-abc"),
        lane_ev("2026-09-12T00:40:00Z", "queue check", 5, "main"),
        lane_ev("2026-09-12T00:40:05Z", "queue subagent", 6, "agent-def"),
    ]
    host = {i: t.task_id for t in segment(events) for i in t.event_ids}
    assert host[3] == host[4] == host[1]
    assert host.get(6) != host[1], "a subagent started from an unstamped turn does not follow the comment"


def test_a_teammate_messaged_while_answering_a_comment_does_that_work_for_the_comment():
    """155f6b4728, week of 2026-09-07: his comment's turn messaged a teammate
    started the day before, and the teammate's 260 events inherited that day."""
    proj = "claude-workspaces-plugin"

    def lane_ev(ts, text, seq, lane, kind="agent_message", wake=None, tool=None):
        e = dataclasses.replace(_agent(ts, proj, text, seq, session_id="lead"), kind=kind)
        e.metadata["agent_lane"] = lane
        if wake:
            e.metadata["wake"] = wake
        if tool:
            e.metadata["tool_name"] = tool
        return e

    def run(sender="lead", delay_s=1):
        send_ts = "2026-09-12T00:15:40Z"
        got = f"2026-09-12T00:{15 + (40 + delay_s) // 60:02d}:{(40 + delay_s) % 60:02d}Z"
        send = lane_ev(send_ts, "sending the mate the fix", 3, "main", kind="tool_call", wake="d-M|th1",
                       tool="SendMessage" if sender == "lead" else "Bash")
        events = [
            lane_ev("2026-09-11T21:00:00Z", "why is the queue empty", 0, "main"),
            lane_ev("2026-09-11T21:05:00Z", "starting the mate", 1, "main", kind="tool_call", tool="Agent"),
            lane_ev("2026-09-11T21:05:02Z", "mate working the queue", 2, "agent-mate"),
            _comment("2026-09-12T00:15:30Z", proj, "d-M", "th1", 4),
            send,
            lane_ev(got, "the fix he asked for", 5, "agent-mate", kind="subagent_input"),
            lane_ev("2026-09-12T00:20:00Z", "fixing it", 6, "agent-mate", kind="tool_call"),
            lane_ev("2026-09-12T00:25:00Z", "fixed it", 7, "agent-mate"),
            lane_ev("2026-09-12T00:15:35Z", "mate still on the queue", 8, "agent-mate"),
        ]
        return {i: t.task_id for t in segment(events) for i in t.event_ids}

    host = run()
    assert host[6] == host[7] == host[4] != host[0], "the message starts a stretch that follows his comment"
    assert host[2] != host[4] and host[8] != host[4], "the mate's earlier stretch keeps its own start"
    other = run(sender="mate")
    assert other[6] != other[4], "a message the lead did not send starts nothing"
    late = run(delay_s=120)
    assert late[6] != late[4], "a send long before the delivery did not deliver it"


def _card_msg(ts: str, project: str, card: str, seq: int) -> Event:
    return Event(
        ts=ts, actor="user", project=project, session_id=f"live-feedback:task:{card}",
        source_system="live_feedback", source_machine="mm",
        kind="user_message", seq=seq, content_snippet="the export drops a week",
        metadata={"words": 5, "task_id": card},
    )


def _asked_msg(ts: str, project: str, card: str, seq: int, turn: int, session_id: str = "lead") -> Event:
    """His comment on a board task, typed in the session that answers it: the one
    message shape a surviving rule places, carrying the turn `ask_turn` refers to."""
    e = _card_msg(ts, project, card, seq)
    return dataclasses.replace(e, session_id=session_id, metadata={**e.metadata, "turn": turn})


def _turn_ev(ts, proj, text, seq, turn, lane="main", card=None):
    e = _agent(ts, proj, text, seq, session_id="lead")
    e.metadata.update({"agent_lane": lane, "turn": turn, **({"card": card} if card else {})})
    return e


def test_an_agent_turn_working_one_board_card_joins_the_cards_task():
    """9e669d1bdc: a lead's sprint turns across 92 cards followed its last typed
    message and became one 8,533-event task."""
    proj = "claude-workspaces-plugin"
    events = [
        _uev("2026-09-11T21:00:00Z", proj, "run the sprint", 0, session_id="lead"),
        _card_msg("2026-09-11T22:00:00Z", proj, "t-AAA", 1),
        _turn_ev("2026-09-11T23:00:00Z", proj, "moving t-AAA", 2, turn=5, card="t-AAA"),
        _turn_ev("2026-09-11T23:05:00Z", proj, "built the export fix", 3, turn=5),
        _turn_ev("2026-09-11T23:30:00Z", proj, "sprint bookkeeping", 4, turn=6),
    ]
    host = {i: t.task_id for t in segment(events) for i in t.event_ids}
    assert host[2] == host[3] == host[1], "the whole turn follows the card it worked"
    assert 0 not in host, "his typed message starts no task"
    assert host.get(4) != host[1], "a turn naming no card does not follow the card"


def test_a_turn_naming_no_card_works_the_board_task_its_wake_named():
    """Week of 2026-09-07: stalled and review-answered wakes on t-X started
    turns that worked t-X without a single card call, and followed the session."""
    proj = "claude-workspaces-plugin"
    events = [
        _uev("2026-09-11T21:00:00Z", proj, "run the sprint", 0, session_id="lead"),
        _card_msg("2026-09-11T22:00:00Z", proj, "t-AAA", 1),
        _turn_ev("2026-09-11T23:00:00Z", proj, "resuming the export fix", 2, turn=5),
        _turn_ev("2026-09-11T23:05:00Z", proj, "pushed the export fix", 3, turn=5),
        _turn_ev("2026-09-11T23:30:00Z", proj, "moving t-BBB", 4, turn=6, card="t-BBB"),
        _turn_ev("2026-09-11T23:31:00Z", proj, "building t-BBB", 5, turn=6),
    ]
    for e in events[2:]:
        e.metadata["wake_task"] = "t-AAA"
    events.append(_turn_ev("2026-09-11T23:50:00Z", proj, "reran the quiet task's tests", 6, turn=7))
    events[-1] = dataclasses.replace(events[-1], kind="tool_call")
    events[-1].metadata["wake_task"] = "t-CCC"
    events.append(_turn_ev("2026-09-11T23:55:00Z", proj, "still blocked on review", 7, turn=8))
    events[-1].metadata["wake_task"] = "t-DDD"
    tasks = segment(events)
    host = {i: t.task_id for t in tasks for i in t.event_ids}
    assert host[2] == host[3] == host[1], "the turn follows the board task its wake named"
    assert 0 not in host, "his typed message starts no task"
    assert host[4] == host[5] != host[1], "a card the turn worked beats the wake"
    assert host[6] != host[1], "a wake turn that used a tool gives its board task a measured task"
    assert any(t.board_task == "t-CCC" for t in tasks)
    assert host.get(7) not in (host[1], host[4], host[6]), "a reply-only wake turn is bookkeeping"
    assert not any(t.board_task == "t-DDD" for t in tasks)


def test_a_turn_his_unkeyed_answer_or_dictation_started_works_the_task_he_answered_on():
    """b2ae175ee1: a review answer's delivery carried only the board, so the work
    it started sat on a session task with no hands-on, apart from his answer."""
    proj = "claude-workspaces-plugin"

    def his(ts, name, card, seq, ws="w-1"):
        return Event(ts=ts, actor="user", project=proj, session_id=f"workspace-board:{ws}",
                     source_system="workspace_board", source_machine="mm", kind="board_action", seq=seq,
                     content_snippet=name, metadata={"board_event": name, "workspace_id": ws,
                                                     **({"task_id": card} if card else {})})

    def run(board_event, card, wake="workspace.review_answered", answered="2026-09-11T22:59:10Z", ws="w-1"):
        events = [
            _uev("2026-09-11T21:00:00Z", proj, "run the sprint", 0, session_id="lead"),
            his(answered, board_event, card, 1, ws=ws),
            dataclasses.replace(_turn_ev("2026-09-11T23:00:10Z", proj, "acting on his answer", 2, turn=5), kind="tool_call"),
            _turn_ev("2026-09-11T23:05:00Z", proj, "done acting on it", 3, turn=5),
        ]
        for e in events[2:]:
            e.metadata.update({"trigger": "him", "ask_wake": wake, "ask_wake_ws": "w-1",
                               "ask_wake_at": "2026-09-11T23:00:00Z"})
        return {i: t.task_id for t in segment(events) for i in t.event_ids}

    # His typed message (index 0) is on no task; "did not start it" reads as
    # the turn landing apart from his answer.
    host = run("decision.answered", "t-AAA")
    assert host[2] == host[3] == host[1] and 0 not in host, "the turn joins the task he answered on"
    info = run("decision.info_requested", "t-AAA")
    assert info[2] == info[1], "asking for more information is an answer too"
    spoke = run("voice.request", "t-AAA", wake="voice.request")
    assert spoke[2] == spoke[1], "a dictation on a task page names its task"
    other_kind = run("voice.request", "t-AAA")
    assert other_kind[2] != other_kind.get(1), "a dictation is not the answer a review wake delivered"
    stale = run("decision.answered", "t-AAA", answered="2026-09-11T22:50:00Z")
    assert stale[2] != stale.get(1), "an answer long before the wake did not start it"
    other_board = run("decision.answered", "t-AAA", ws="w-2")
    assert other_board[2] != other_board.get(1), "an answer on another board did not start it"

    agent_answer = dataclasses.replace(his("2026-09-11T22:59:50Z", "decision.answered", "t-BBB", 9), actor="agent")
    events = [
        _uev("2026-09-11T21:00:00Z", proj, "run the sprint", 0, session_id="lead"),
        his("2026-09-11T22:59:10Z", "decision.answered", "t-AAA", 1),
        dataclasses.replace(_turn_ev("2026-09-11T23:00:10Z", proj, "acting on his answer", 2, turn=5), kind="tool_call"),
        _card_msg("2026-09-11T22:00:00Z", proj, "t-BBB", 3),
        agent_answer,
    ]
    events[2].metadata.update({"trigger": "him", "ask_wake": "workspace.review_answered", "ask_wake_ws": "w-1",
                               "ask_wake_at": "2026-09-11T23:00:00Z"})
    mixed = {i: t.task_id for t in segment(events) for i in t.event_ids}
    assert mixed[2] == mixed[1] != mixed[3], "an agent's answer did not deliver his"

    # He answered by replying on the item's thread; the board recorded no decision.
    def replied(board_of_card, actor="user"):
        reply = dataclasses.replace(_card_msg("2026-09-11T22:59:57Z", proj, "t-AAA", 1), actor=actor)
        events = [
            _uev("2026-09-11T21:00:00Z", proj, "run the sprint", 0, session_id="lead"),
            reply,
            dataclasses.replace(_turn_ev("2026-09-11T23:00:10Z", proj, "acting on his reply", 2, turn=5), kind="tool_call"),
            his("2026-09-11T20:00:00Z", "task.claimed", "t-AAA", 3, ws=board_of_card),
        ]
        events[2].metadata.update({"trigger": "him", "ask_wake": "workspace.review_answered", "ask_wake_ws": "w-1",
                                   "ask_wake_at": "2026-09-11T23:00:00Z"})
        return {i: t.task_id for t in segment(events) for i in t.event_ids}

    by_reply = replied("w-1")
    assert by_reply[2] == by_reply[1], "his reply on the card answered the item"
    elsewhere = replied("w-2")
    assert elsewhere[2] != elsewhere.get(1), "a reply on another board's card did not"
    agent_reply = replied("w-1", actor="agent")
    assert agent_reply[2] != agent_reply.get(1), "an agent's reply on the card was not his answer"

    # bf56f86e52: dictated from the hub, so neither the dictation nor its wake
    # names a task, and the work went to the session.
    def dictated(wake="voice.request", ws="w-1"):
        events = [
            _uev("2026-09-11T21:00:00Z", proj, "run the sprint", 0, session_id="lead"),
            his("2026-09-11T22:59:10Z", "voice.request", None, 1, ws=ws),
            dataclasses.replace(_turn_ev("2026-09-11T23:00:10Z", proj, "moving t-BBB", 2, turn=5, card="t-BBB"),
                                kind="tool_call"),
            dataclasses.replace(_turn_ev("2026-09-11T23:05:00Z", proj, "built t-BBB", 3, turn=5), kind="tool_call"),
            _uev("2026-09-11T22:59:00Z", proj, "still running the sprint", 4, session_id="lead"),
        ]
        for e in events[2:]:
            e.metadata.update({"trigger": "him", "ask_wake": wake, "ask_wake_ws": "w-1",
                               "ask_wake_at": "2026-09-11T23:00:00Z"})
        return {i: t.task_id for t in segment(events) for i in t.event_ids}

    hub = dictated()
    assert hub[1] == hub[2] == hub[3], "a dictation naming no task joins the work it started"
    not_it = dictated(wake="workspace.review_answered")
    assert not_it.get(1) != not_it[2], "a review wake was not delivering the dictation"
    far = dictated(ws="w-2")
    assert far.get(1) != far[2], "a dictation on another board did not start it"

    # The dict comprehensions above read one task per event, so they cannot see
    # a dictation left on two. Audit 1 is a hard invariant; check it directly.
    holders: dict[int, int] = {}
    for t in segment([
            _uev("2026-09-11T21:00:00Z", proj, "run the sprint", 0, session_id="lead"),
            his("2026-09-11T22:59:10Z", "voice.request", None, 1, ws="w-1"),
            dataclasses.replace(_turn_ev("2026-09-11T23:00:10Z", proj, "moving t-BBB", 2,
                                         turn=5, card="t-BBB"), kind="tool_call"),
    ]):
        for i in t.event_ids:
            holders[i] = holders.get(i, 0) + 1
    assert all(n == 1 for n in holders.values()), f"an event billed twice: {holders}"


def test_a_dictation_already_on_two_tasks_is_taken_off_both():
    """Week of 2026-08-17: a 28-word hub dictation naming no task was held by a
    proximity attach AND by this pass, so both tasks billed its speaking time.

    The pass reads its holders into a dict keyed by event, which keeps one task
    per event. Given two, it removed the dictation from whichever the
    comprehension wrote last and copied it onto the host -- still two tasks. The
    upstream duplicate is not reproduced here: what this asserts is that the
    join leaves exactly one holder whatever it is handed.
    """
    ts = datetime.fromisoformat("2026-09-11T23:00:00+00:00")
    events = [Event(ts="2026-09-11T23:00:00Z", source_system="workspace_board", seq=0,
                    session_id="b", project="p", actor="user", kind="board_action",
                    source_machine="mm", content_snippet="hey tapping on home takes me back",
                    metadata={"board_event": "voice.request", "words": 7}),
              Event(ts="2026-09-11T23:01:00Z", source_system="claude_transcript", seq=1,
                    session_id="s", project="p", actor="agent", kind="tool_call",
                    source_machine="mm", content_snippet="doing it", metadata={}),
              Event(ts="2026-09-11T23:02:00Z", source_system="claude_transcript", seq=2,
                    session_id="s", project="p", actor="agent", kind="tool_call",
                    source_machine="mm", content_snippet="unrelated", metadata={})]
    a, b, work = (Task(task_id="a", title="a", project="p"),
                  Task(task_id="b", title="b", project="p"),
                  Task(task_id="w", title="w", project="p"))
    for t in (a, b):
        t.touch(0, ts, rule="card_turn")
    a.touch(2, ts, rule="filler")       # keeps `a` alive once the dictation leaves
    work.touch(1, ts, rule="work")
    by_project = {"p": [a, b, work]}
    assert _join_dictations_to_their_work(by_project, events, {0: [1]}) == 1
    held = [t.task_id for t in by_project["p"] if 0 in t.event_ids]
    assert held == ["w"], f"the dictation is on {held}, not on exactly its work's host"
    assert b not in by_project["p"], "a task left holding nothing is gone"


def test_overnight_work_on_a_turn_he_typed_stays_with_his_message():
    """Week of 2026-09-07: 17 tasks (70h est) of work he asked for ran past the
    6h session gap and were rebuilt as delegated tasks with no hands-on."""
    proj = "weekly-review"

    def run(stamp: bool):
        msg = _asked_msg("2026-09-12T00:00:00Z", proj, "t-AAA", 0, turn=1, session_id="lead")
        work = []
        for n in range(20):
            e = _agent(f"2026-09-12T09:{n * 2:02d}:00Z", proj, f"sweep week {n}", n + 1, session_id="lead")
            e.metadata.update({"agent_lane": "main", "turn": 2 + n, **({"ask_turn": 1} if stamp else {})})
            work.append(e)
        return {i: t.task_id for t in segment([msg, *work]) for i in t.event_ids}

    stamped = run(True)
    assert all(stamped.get(i) == stamped[0] for i in range(1, 21))
    unstamped = run(False)
    assert unstamped.get(1) != unstamped[0], "without the ask, work past the gap is still delegated"


def test_a_turn_naming_two_cards_is_triage_but_each_card_call_follows_its_card():
    """de3f8893: 735 card tool calls in a lead's triage turns sat on its broad
    session tasks, 52-69 cards each, instead of on their cards' tasks."""
    proj = "claude-workspaces-plugin"
    events = [
        _uev("2026-09-11T21:00:00Z", proj, "triage the board", 0, session_id="lead"),
        _card_msg("2026-09-11T22:00:00Z", proj, "t-AAA", 1),
        _card_msg("2026-09-11T22:01:00Z", proj, "t-BBB", 2),
        _turn_ev("2026-09-11T23:00:00Z", proj, "moving t-AAA", 3, turn=5, card="t-AAA"),
        _turn_ev("2026-09-11T23:00:30Z", proj, "moving t-BBB", 4, turn=5, card="t-BBB"),
        _turn_ev("2026-09-11T23:01:00Z", proj, "reading the board", 5, turn=5),
    ]
    host = {i: t.task_id for t in segment(events) for i in t.event_ids}
    assert host[3] == host[1] and host[4] == host[2], "each card call joins its own card"
    assert host.get(5) not in (host[1], host[2]), "the rest of a triage turn joins neither card"


def test_a_wake_naming_one_of_a_triage_turns_cards_takes_the_rest_of_the_turn():
    proj = "claude-workspaces-plugin"

    def run(wake, trigger="agent"):
        events = [
            _uev("2026-09-11T21:00:00Z", proj, "triage the board", 0, session_id="lead"),
            _card_msg("2026-09-11T22:00:00Z", proj, "t-AAA", 1),
            _card_msg("2026-09-11T22:01:00Z", proj, "t-BBB", 2),
            _turn_ev("2026-09-11T23:00:00Z", proj, "moving t-AAA", 3, turn=5, card="t-AAA"),
            _turn_ev("2026-09-11T23:00:30Z", proj, "filing t-BBB", 4, turn=5, card="t-BBB"),
            dataclasses.replace(_turn_ev("2026-09-11T23:01:00Z", proj, "fixing what he answered", 5, turn=5),
                                kind="tool_call"),
        ]
        for e in events[3:]:
            e.metadata["wake_task"] = wake
            e.metadata["trigger"] = trigger
        return {i: t.task_id for t in segment(events) for i in t.event_ids}

    host = run("t-AAA")
    assert host[3] == host[1] and host[4] == host[2], "each card call still joins its own card"
    assert host[5] == host[1], "the unbracketed work follows the card the wake named"
    other = run("t-CCC")
    assert other.get(5) not in (other[1], other[2]), "a board notice naming none of the turn's cards breaks no tie"
    his = run("t-CCC", trigger="him")
    assert his[3] == his[1] and his[4] == his[2], "each card call still joins its own card"
    assert his[5] not in (his[1], his[2]), "his answer on t-CCC takes the work it started"

    moves_only = [
        _uev("2026-09-11T21:00:00Z", proj, "triage the board", 0, session_id="lead"),
        _card_msg("2026-09-11T22:00:00Z", proj, "t-AAA", 1),
        _turn_ev("2026-09-11T23:00:00Z", proj, "moving t-AAA", 2, turn=5, card="t-AAA"),
        dataclasses.replace(_turn_ev("2026-09-11T23:00:30Z", proj, "moving t-EEE", 3, turn=5, card="t-EEE"),
                            kind="tool_call"),
    ]
    for e in moves_only[2:]:
        e.metadata["wake_task"] = "t-EEE"
    quiet_tasks = segment(moves_only)
    quiet = {i: t.task_id for t in quiet_tasks for i in t.event_ids}
    assert quiet.get(3) != quiet[1] and not any(t.board_task == "t-EEE" for t in quiet_tasks), (
        "card moves alone make no measured task for the card the wake named")


def test_a_subagent_started_on_a_turn_he_typed_stays_with_his_message_overnight():
    proj = "weekly-review"
    msg = _asked_msg("2026-09-12T00:00:00Z", proj, "t-AAA", 0, turn=1, session_id="lead")
    lead = _agent("2026-09-12T00:00:10Z", proj, "dispatching the sweep", 1, session_id="lead")
    lead.metadata.update({"agent_lane": "main", "turn": 2, "ask_turn": 1})
    work = []
    for n in range(20):
        e = _agent(f"2026-09-12T09:{n * 2:02d}:00Z", proj, f"sweep week {n}", n + 2, session_id="lead")
        e.metadata.update({"agent_lane": "agent-sweep", "turn": 1 + n})
        work.append(e)
    work.insert(0, dataclasses.replace(work[0], ts="2026-09-12T00:00:20Z", seq=99))
    work[0].metadata.update(work[1].metadata)
    host = {i: t.task_id for t in segment([msg, lead, *work]) for i in t.event_ids}
    assert all(host.get(i) == host[0] for i in range(2, 23))


def test_his_comments_on_one_card_are_one_task_that_no_other_card_joins():
    """A board task (2026-09-17): his 24 comments on one card became 6+ tasks after idle gaps,
    and a second card's first comment a minute later joined the first card's task."""
    proj = "claude-workspaces-plugin"
    events = [
        _card_msg("2026-09-08T10:00:00Z", proj, "t-AAA", 0),
        _card_msg("2026-09-08T10:01:00Z", proj, "t-BBB", 1),
        _card_msg("2026-09-08T19:00:00Z", proj, "t-AAA", 2),
        _card_msg("2026-09-10T09:00:00Z", proj, "t-AAA", 3),
        _card_msg("2026-09-10T11:00:00Z", proj, "t-CCC", 4),
        _uev("2026-09-10T11:01:00Z", proj, "unrelated terminal ask", 5, session_id="s-new"),
    ]
    host = {i: t.task_id for t in segment(events) for i in t.event_ids}
    assert host[0] == host[2] == host[3], "one card, one task, across idle gaps"
    assert host[1] != host[0], "another card never joins by proximity"
    assert host.get(5) != host[4], "a terminal message never joins a card's task by proximity"


def test_a_subagent_dispatched_from_a_card_turn_joins_the_cards_task():
    proj = "claude-workspaces-plugin"
    events = [
        _uev("2026-09-11T21:00:00Z", proj, "run the sprint", 0, session_id="lead"),
        _card_msg("2026-09-11T22:00:00Z", proj, "t-AAA", 1),
        _turn_ev("2026-09-11T23:00:00Z", proj, "dispatching a builder", 2, turn=5, card="t-AAA"),
        _turn_ev("2026-09-11T23:00:05Z", proj, "building", 3, turn=1, lane="agent-abc"),
        _turn_ev("2026-09-11T23:40:00Z", proj, "idle check", 4, turn=6),
        _turn_ev("2026-09-11T23:40:05Z", proj, "other subagent", 5, turn=1, lane="agent-def"),
    ]
    host = {i: t.task_id for t in segment(events) for i in t.event_ids}
    assert host[3] == host[1]
    assert host.get(5) != host[1], "a subagent started from a turn naming no card does not follow it"


def _board(ts, proj, card, seq):
    return Event(ts=ts, actor="agent", project=proj, session_id="workspace-board:w-1",
                 source_system="workspace_board", source_machine="mm", kind="board_action", seq=seq,
                 content_snippet="task.claimed", metadata={"board_event": "task.claimed", "task_id": card})


def test_a_subagent_whose_prompt_names_a_card_joins_it_even_from_a_multi_card_turn():
    proj = "claude-workspaces-plugin"
    from dataclasses import replace
    prompt = _turn_ev("2026-09-11T23:00:05Z", proj, "build t-AAA", 3, turn=1, lane="agent-abc", card="t-AAA")
    prompt = replace(prompt, kind="subagent_input")
    events = [
        _uev("2026-09-11T21:00:00Z", proj, "run the sprint", 0, session_id="lead"),
        _card_msg("2026-09-11T22:00:00Z", proj, "t-AAA", 1),
        _turn_ev("2026-09-11T23:00:00Z", proj, "dispatching two builders", 2, turn=5, card="t-AAA"),
        prompt,
        _turn_ev("2026-09-11T23:00:10Z", proj, "dispatching the second", 4, turn=5, card="t-BBB"),
        _turn_ev("2026-09-11T23:20:00Z", proj, "building", 5, turn=1, lane="agent-abc"),
    ]
    host = {i: t.task_id for t in segment(events) for i in t.event_ids}
    assert host[5] == host[1]
    assert host[2] == host[1], "a call naming its card follows it, even in a multi-card turn"


def test_a_teammate_lane_follows_each_message_that_assigns_it_a_board_task():
    """Week of 2026-08-17: a teammate told "Next task: t-X" kept billing its first
    card, and one told "You are implementing t-X" billed a card it touched once."""
    proj = "claude-workspaces-plugin"
    from dataclasses import replace

    def msg(ts, card, seq):
        return replace(_turn_ev(ts, proj, f"implement {card}", seq, turn=None, lane="agent-mate", card=card),
                       kind="subagent_input")

    events = [
        _uev("2026-09-11T21:00:00Z", proj, "run the sprint", 0, session_id="lead"),
        msg("2026-09-11T22:00:00Z", "t-AAA", 1),
        _turn_ev("2026-09-11T22:05:00Z", proj, "building the first", 2, turn=1, lane="agent-mate"),
        replace(_turn_ev("2026-09-11T22:06:00Z", proj, "read t-CCC", 3, turn=2, lane="agent-mate", card="t-CCC"),
                kind="tool_call"),
        _turn_ev("2026-09-11T22:10:00Z", proj, "still on the first", 4, turn=3, lane="agent-mate"),
        msg("2026-09-11T23:00:00Z", "t-BBB", 5),
        _turn_ev("2026-09-11T23:05:00Z", proj, "building the second", 6, turn=4, lane="agent-mate"),
    ]
    tasks = segment(events)
    board = {i: t.board_task for t in tasks for i in t.event_ids}
    assert board[2] == board[4] == board[1] == "t-AAA", "one stray call does not take the lane"
    assert board[6] == board[5] == "t-BBB", "a new assignment starts a new stretch"


def test_card_work_he_never_wrote_on_is_one_task_across_sessions_with_its_board_actions():
    proj = "claude-workspaces-plugin"

    from dataclasses import replace

    def other(ts, text, seq, session, turn, card=None):
        return replace(_turn_ev(ts, proj, text, seq, turn=turn, card=card), session_id=session)

    events = [
        _uev("2026-09-11T21:00:00Z", proj, "run the sprint", 0, session_id="lead"),
        _card_msg("2026-09-11T21:30:00Z", proj, "t-HIS", 1),
        _board("2026-09-11T22:00:00Z", proj, "t-CCC", 2),
        other("2026-09-11T22:00:10Z", "claimed t-CCC", 3, "lead", turn=2, card="t-CCC"),
        other("2026-09-12T09:00:00Z", "finishing t-CCC", 4, "builder2", turn=1, card="t-CCC"),
        _board("2026-09-12T09:05:00Z", proj, "t-DDD", 5),
    ]
    tasks = segment(events)
    host = {i: t.task_id for t in tasks for i in t.event_ids}
    assert host[2] == host[3] == host[4], "one card, two sessions, its claim: one task"
    assert 0 not in host, "his typed message starts no task"
    assert not any(t.board_task == "t-DDD" for t in tasks), \
        "a card only touched on the board makes no task of its own"
    assert [t.board_task for t in tasks if host[4] == t.task_id] == ["t-CCC"]
    assert sum(t.board_task == "t-HIS" for t in tasks) == 1


def test_a_comment_turn_hours_after_the_comment_is_not_pulled_back_to_it():
    proj = "claude-workspaces-plugin"
    woken = _agent("2026-09-12T09:00:00Z", proj, "late reply", 2, session_id="lead")
    woken.metadata["wake"] = "d-M|th1"
    events = [
        _comment("2026-09-12T00:15:30Z", proj, "d-M", "th1", 0),
        _agent("2026-09-12T08:50:00Z", proj, "status please", 1, session_id="lead"),
        woken,
    ]
    host = {i: t.task_id for t in segment(events) for i in t.event_ids}
    assert host.get(2) != host[0]


def test_work_his_comment_started_stays_with_it_however_long_it_ran():
    """Week of 2026-09-07: work his doc comments started ran on through build
    notifications past 6h and was rebuilt as delegated work with no hands-on."""
    proj = "claude-workspaces-plugin"
    late = []
    for n in range(20):
        e = _agent(f"2026-09-12T09:{n * 2:02d}:00Z", proj, f"still fixing {n}", 2 + n, session_id="lead")
        e.metadata.update({"wake": "d-M|th1", "trigger": "him", "agent_lane": "main", "turn": 3 + n})
        late.append(e)
    events = [
        _comment("2026-09-12T00:15:30Z", proj, "d-M", "th1", 0),
        _uev("2026-09-11T20:00:00Z", proj, "earlier chat", 1, session_id="lead"),
        *late,
    ]
    host = {i: t.task_id for t in segment(events) for i in t.event_ids}
    assert all(host.get(i) == host[0] for i in range(2, 22))


def test_work_his_resolve_started_joins_the_task_where_he_resolved_it():
    """A thread he resolved without commenting on still names where his hand was."""
    proj = "claude-workspaces-plugin"
    late = []
    for n in range(5):
        e = _agent(f"2026-09-12T00:{16 + n}:00Z", proj, f"closing out {n}", 3 + n, session_id="lead")
        e.metadata.update({"wake": "d-M|th1", "trigger": "him", "agent_lane": "main", "turn": 3})
        late.append(e)

    def run(activity, actor="user"):
        touch = dataclasses.replace(_comment("2026-09-12T00:15:30Z", proj, "d-M", "th1", 0),
                                    kind="workspace_touch", actor=actor)
        touch.metadata["activity_type"] = activity
        read = _comment("2026-09-12T00:10:00Z", proj, "d-M", "th9", 1)
        events = [touch, read, _uev("2026-09-11T20:00:00Z", proj, "earlier chat", 2, session_id="lead"), *late]
        return {i: t.task_id for t in segment(events) for i in t.event_ids}

    host = run("resolve")
    assert all(host.get(i) == host[0] for i in range(3, 8))
    host = run("reopen")
    assert all(host.get(i) == host[0] for i in range(3, 8))
    opened = run("doc_open")
    assert not any(opened.get(i) == opened[0] for i in range(3, 8)), "opening a doc is not his hand on the thread"


def test_a_turn_that_only_moves_a_card_makes_no_measured_task_but_one_that_works_does():
    """Week of 2026-09-07: 101 measured tasks held nothing but an agent's
    status and transition calls on a card, each with an estimate and 0 wall."""
    from dataclasses import replace
    proj = "claude-workspaces-plugin"

    def call(ts, card, seq, turn):
        return replace(_turn_ev(ts, proj, f"task_transition {card}", seq, turn=turn, card=card), kind="tool_call")

    events = [
        _uev("2026-09-11T21:00:00Z", proj, "run the sprint", 0, session_id="lead"),
        call("2026-09-11T23:00:00Z", "t-MOV", 1, turn=5),
        call("2026-09-11T23:10:00Z", "t-WRK", 2, turn=6),
        replace(_turn_ev("2026-09-11T23:12:00Z", proj, "fixed the parser", 3, turn=6), kind="tool_call"),
        # 7b4dd5cc2e, 9458d4dff0, bd01c34a42: a move, its result, and a line saying so.
        call("2026-09-11T23:20:00Z", "t-SAY", 4, turn=7),
        replace(_turn_ev("2026-09-11T23:20:01Z", proj, "moved it", 5, turn=7), kind="tool_result"),
        _turn_ev("2026-09-11T23:20:05Z", proj, "moved t-SAY to done", 6, turn=7),
    ]
    tasks = segment(events)
    host = {i: t for t in tasks for i in t.event_ids}
    assert not any(t.board_task == "t-MOV" for t in tasks)
    assert host[2].board_task == "t-WRK" and host[3] is host[2]
    assert not any(t.board_task == "t-SAY" for t in tasks), "saying what it moved is not work"


def test_an_agent_answering_its_own_review_item_is_not_work_on_the_task():
    """4c6932f3a0: an agent's decision.answered made a measured task of instants."""
    from weekly_review.segmentation import is_board_work_evidence

    def answer(actor, name="decision.answered"):
        return Event(ts="2026-09-08T23:38:30Z", actor=actor, project="p", session_id="workspace-board:w-1",
                     source_system="workspace_board", source_machine="mm", kind="board_action", seq=0,
                     content_snippet=name, metadata={"board_event": name, "workspace_id": "w-1", "task_id": "t-AAA"})

    assert is_board_work_evidence(answer("user"))
    assert is_board_work_evidence(answer("user", "voice.request"))
    assert not is_board_work_evidence(answer("agent"))
    assert not is_board_work_evidence(answer("agent", "voice.request"))
    assert not is_board_work_evidence(answer("user", "task.transitioned")), "his moving a card is housekeeping"


def test_work_between_two_calls_on_one_card_in_a_triage_turn_follows_that_card():
    from dataclasses import replace
    proj = "claude-workspaces-plugin"

    def call(ts, card, seq):
        return replace(_turn_ev(ts, proj, f"revise_review_item {card}", seq, turn=5, card=card), kind="tool_call")

    events = [
        _uev("2026-09-11T21:00:00Z", proj, "triage the board", 0, session_id="lead"),
        _card_msg("2026-09-11T22:00:00Z", proj, "t-AAA", 1),
        call("2026-09-11T23:00:00Z", "t-AAA", 2),
        _turn_ev("2026-09-11T23:00:10Z", proj, "rewording the item", 3, turn=5),
        call("2026-09-11T23:00:20Z", "t-AAA", 4),
        _turn_ev("2026-09-11T23:00:30Z", proj, "reading the board", 5, turn=5),
        call("2026-09-11T23:00:40Z", "t-BBB", 6),
        _turn_ev("2026-09-11T23:00:50Z", proj, "after the last call", 7, turn=5),
        call("2026-09-11T23:01:00Z", "t-CCC", 8),
        replace(_turn_ev("2026-09-11T23:01:10Z", proj, "fixing its item", 9, turn=5), kind="tool_call"),
        call("2026-09-11T23:01:20Z", "t-CCC", 10),
        # bd01c34a42: another call's result between a call and its own result.
        call("2026-09-11T23:02:00Z", "t-DDD", 11),
        replace(_turn_ev("2026-09-11T23:02:01Z", proj, "Bash output", 12, turn=5), kind="tool_result"),
        replace(call("2026-09-11T23:02:01Z", "t-DDD", 13), kind="tool_result"),
    ]
    tasks = segment(events)
    host = {i: t.task_id for t in tasks for i in t.event_ids}
    assert [t.board_task for t in tasks if 9 in t.event_ids] == ["t-CCC"], "bracketed work alone earns a measured task"
    assert host[3] == host[2] == host[1], "bracketed by two calls on its card"
    cards = {t.task_id for t in tasks if t.board_task}
    assert host.get(5) not in cards, "between two different cards it joins neither"
    assert host.get(7) not in cards, "after the last call it joins neither"
    assert not any(t.board_task == "t-DDD" for t in tasks), "a stray result between a call and its result is not work"


def test_a_commit_with_no_session_anywhere_is_not_a_measured_task():
    """The user 2026-09-15: a scheduled job's commit and a routine on another
    machine are not his work, and the run says how many it dropped. The witness
    is any session at all, in any project — being at his desk on something else
    is what tells a rebuilt commit from a job that ran while he slept, so the
    boundary is read here against a session in another project, which cannot
    adopt the commit and leaves the window as the only thing deciding."""
    def run(gap_min):
        events = [
            _commit("2026-09-07T15:00:49Z", "notes-repo", "Refresh subscriptions archive", "7895846", 0),
            _uev(f"2026-09-07T{15 + gap_min // 60:02d}:{gap_min % 60:02d}:49Z", "bike-map",
                 "plan tomorrow's ride", 1, session_id="a"),
        ]
        dropped: list = []
        tasks = segment(events, dropped=dropped)
        return tasks, dropped

    tasks, dropped = run(31)
    assert [t.project for t in dropped] == ["notes-repo"], "no session within half an hour"
    assert not any(0 in t.event_ids for t in tasks)
    tasks, dropped = run(29)
    assert dropped == [], "a session 29 min away, even on another project, is a witness"
    assert any(0 in t.event_ids for t in tasks)


def test_a_projects_own_session_is_the_witness_where_it_has_one():
    """The fleet runs about twenty sessions at once, so "a session anywhere"
    is nearly always true: the two digest commits that survived the week of
    2026-09-07 had 502 and 1,773 events inside half an hour and not one of
    them in notes-repo. A session in client-b cannot have written a
    notes-repo digest. So where a project has sessions of its own, they
    are the witness, and the daily cron that commits while he is elsewhere is
    dropped however busy the rest of the fleet is."""
    def run(cron_alone: bool):
        events = [
            # notes-repo committing beside its own session: this is what
            # says the project is instrumented and its silences mean something.
            _uev("2026-09-05T10:00:00Z", "notes-repo", "pull today's sources", 0, session_id="rn"),
            _commit("2026-09-05T10:10:00Z", "notes-repo", "Add sources for 2026-09-05", "aaa1111", 1),
            # Two days later the scheduled digest commits on its own.
            _commit("2026-09-07T15:00:49Z", "notes-repo", "Refresh subscriptions archive", "7895846", 2),
        ]
        if cron_alone:
            events.append(_uev("2026-09-07T15:08:17Z", "client-b", "look at the survey draft", 3,
                               session_id="sf"))
        else:
            events.append(_uev("2026-09-07T15:08:17Z", "notes-repo", "check what it picked up", 3,
                               session_id="rn2"))
        dropped: list = []
        tasks = segment(events, dropped=dropped)
        return tasks, dropped

    tasks, dropped = run(cron_alone=True)
    assert [t.title for t in dropped] == ["Refresh subscriptions archive"], (
        "a busy fleet is not a witness for a project that has sessions of its own"
    )
    assert not any(2 in t.event_ids for t in tasks)
    tasks, dropped = run(cron_alone=False)
    assert dropped == [], "its own project's session, eight minutes away, is a witness"


def test_a_project_with_no_sessions_of_its_own_keeps_its_commits():
    """assistant-b on the week of 2026-08-17 is ten commits and no
    other event at all — real work whose sessions route to some other slug, not
    a scheduled job. A project that is never seen committing beside its own
    sessions cannot be read for silence, so the fleet-wide window decides and
    the commits stay."""
    events = [
        _commit("2026-08-18T17:00:00Z", "assistant-b",
                "sf-activity-sweeps: add Randall Museum", "bbb2222", 0),
        _commit("2026-08-18T17:20:00Z", "assistant-b",
                "packing skill: pull items from a trip dossier", "ccc3333", 1),
        _uev("2026-08-18T17:05:00Z", "weekly-review", "how did last week come out", 2, session_id="wr"),
    ]
    dropped: list = []
    tasks = segment(events, dropped=dropped)
    assert dropped == [], "ten commits and no sessions is a routing hole, not a cron job"
    assert {0, 1} <= {i for t in tasks for i in t.event_ids}


ROLLOUT_TITLE = "rule: add security-posture (fleet-wide secret handling)"


def _rollout(straggler_at: str, straggler_project: str = "writing-tool",
             linked: int = 2, straggler_title: str = ROLLOUT_TITLE) -> list[Event]:
    """One lead session opening the same rule PR across several repos.

    It prints the URLs of the ones in `linked` and not the straggler's, which
    is the whole of the 2026-04-28 case: which URLs survived into the text is
    the only thing telling the straggler from its siblings.
    """
    def opened(ts: str, project: str, title: str, seq: int) -> Event:
        return Event(
            ts=ts, actor="artifact", project=project, session_id=f"gh:{project}",
            source_system="github", source_machine="mm", kind="pr_opened", seq=seq,
            content_snippet=title, metadata={"number": 7, "repo": f"arivera/{project}"},
        )

    events = [_uev("2026-04-28T08:54:00Z", "ai-team-lead", "roll the secret-handling rule out everywhere", 0)]
    repos = ["career-tool", "notes-repo", "finance-tool"][:linked]
    at = ["2026-04-28T09:33:30Z", "2026-04-28T09:34:13Z", "2026-04-28T09:34:35Z"][:linked]
    seq = 1
    for repo, ts in zip(repos, at):
        events.append(_agent(ts, "ai-team-lead",
                             f"opened https://github.com/arivera/{repo}/pull/7", seq))
        seq += 1
        events.append(opened(ts, repo, ROLLOUT_TITLE, seq))
        seq += 1
    # The straggler: same rollout, its URL never printed, and no session of its
    # own anywhere near it.
    events.append(opened(straggler_at, straggler_project, straggler_title, seq))
    return events


def _straggler_host(events: list[Event]) -> str | None:
    tasks = segment(events)
    holder = {i: t for t in tasks for i in t.event_ids}
    return holder[len(events) - 1].project if (len(events) - 1) in holder else None


def test_a_rollouts_straggler_joins_the_session_that_opened_the_burst():
    """2026-04-28: one ai-team-lead session opened ten identically-titled PRs
    across ten repos in 65 seconds. Six printed their URLs and joined it by the
    link, three were claimed by their own project's session, and the tenth —
    writing-tool #10, 54 lines — printed nothing and had no session of its
    own awake, so it sat as its own task with an estimate and no wall clock.
    A PR of that title timestamped between two the session is already holding
    was opened by the same run of the same script."""
    assert _straggler_host(_rollout("2026-04-28T09:33:42Z")) == "ai-team-lead"


def test_a_lone_same_titled_pr_is_not_a_burst():
    """One PR of a title is a zero-width span, so nothing can be inside it. A
    session that opened a single rule PR has not been shown opening a run of
    them, and a same-titled PR elsewhere may be somebody repeating the chore."""
    assert _straggler_host(_rollout("2026-04-28T09:33:42Z", linked=1)) == "writing-tool"


def test_a_task_spanning_two_rollouts_joins_neither():
    """An artifact-only task holding PRs from two different bursts is not one
    act, and handing it to either host would move the other's work with it. The
    burst is only allowed to claim a task that is entirely its own."""
    def opened(ts, project, title, seq):
        return Event(
            ts=ts, actor="artifact", project=project, session_id=f"gh:{project}",
            source_system="github", source_machine="mm", kind="pr_opened", seq=seq,
            content_snippet=title, metadata={"number": 7, "repo": f"arivera/{project}"},
        )

    OTHER = "chore: bump the shared client to 2.1"
    events = [_uev("2026-04-28T08:54:00Z", "ai-team-lead", "roll both changes out everywhere", 0)]
    seq = 1
    # The lead is on record opening both titles, each spanning the straggler.
    for title in (ROLLOUT_TITLE, OTHER):
        for repo, ts in (("career-tool", "2026-04-28T09:33:00Z"),
                         ("notes-repo", "2026-04-28T09:35:00Z")):
            events.append(_agent(ts, "ai-team-lead",
                                 f"opened https://github.com/arivera/{repo}/pull/7", seq))
            seq += 1
            events.append(opened(ts, repo, title, seq))
            seq += 1
    # Two unprinted PRs in one repo, one from each rollout, merged into a task
    # of their own by project proximity.
    events.append(opened("2026-04-28T09:34:00Z", "writing-tool", ROLLOUT_TITLE, seq))
    events.append(opened("2026-04-28T09:34:10Z", "writing-tool", OTHER, seq + 1))
    tasks = segment(events)
    holder = {i: t for t in tasks for i in t.event_ids}
    straggler = holder[len(events) - 1]
    assert holder[len(events) - 2] is straggler, "the fixture needs both PRs in one task"
    assert straggler.project == "writing-tool", "two acts in one task belong to neither burst"


def test_a_pr_outside_the_burst_is_not_its_sibling():
    """Strictly inside the span the session is on record for, so there is no
    window to tune. An hour later is a separate act — and a rollout's own first
    or last PR is out of reach if it is also the unprinted one, which is the
    price of having no constant here."""
    assert _straggler_host(_rollout("2026-04-28T10:34:35Z")) == "writing-tool"


def test_a_different_title_in_the_same_minute_is_a_different_act():
    """The burst is a title and a span together. A PR that merely lands in the
    same minute is other work that happened to ship then."""
    assert _straggler_host(
        _rollout("2026-04-28T09:33:42Z", straggler_title="fix(widget): drop the hostname gate")
    ) == "writing-tool"


def test_a_stray_event_is_not_enough_to_make_a_project_readable():
    """What makes a project's silence mean something is that its commits have
    been seen landing beside its sessions — not that it has an event somewhere.
    A half-routed project, where one stray touch lands under the slug and every
    commit lands hours from anything, is the routing hole again with one more
    event in it, and reading it as instrumented would drop the same real work."""
    events = [
        _uev("2026-08-18T09:00:00Z", "assistant-b", "what's on this weekend", 0, session_id="ot"),
        _commit("2026-08-18T17:00:00Z", "assistant-b",
                "sf-activity-sweeps: add Randall Museum", "bbb2222", 1),
        _commit("2026-08-18T17:20:00Z", "assistant-b",
                "packing skill: pull items from a trip dossier", "ccc3333", 2),
        _uev("2026-08-18T17:05:00Z", "weekly-review", "how did last week come out", 3, session_id="wr"),
    ]
    dropped: list = []
    segment(events, dropped=dropped)
    assert dropped == [], "one touch eight hours from every commit proves nothing about the project"


def test_one_commit_beside_its_own_session_is_what_makes_a_project_readable():
    """The narrow rule turns on for a project the moment it is seen putting a
    commit next to its own work — one co-occurrence, not a threshold. Without
    that pairing the same digest commit keeps the benefit of the doubt."""
    def run(paired: bool):
        # The only difference between the two runs is which project the 09-05
        # session belongs to, so the earlier commit keeps a witness either way
        # and only the pairing that makes notes-repo readable changes.
        events = [
            _uev("2026-09-05T10:00:00Z", "notes-repo" if paired else "client-b",
                 "pull today's sources", 0, session_id="rn"),
            _commit("2026-09-05T10:10:00Z", "notes-repo", "Add sources", "aaa1111", 1),
            _commit("2026-09-07T15:00:49Z", "notes-repo", "Refresh subscriptions archive", "7895846", 2),
            _uev("2026-09-07T15:08:17Z", "client-b", "look at the survey draft", 3, session_id="sf"),
        ]
        dropped: list = []
        segment(events, dropped=dropped)
        return [t.title for t in dropped]

    assert run(paired=True) == ["Refresh subscriptions archive"], (
        "one commit beside a notes-repo session makes its silences readable"
    )
    assert run(paired=False) == [], "no commit has ever sat beside a notes-repo session here"


def _lane(project: str, start_min: int, n: int, seq0: int, session_id: str = "lane") -> list[Event]:
    """A delegated lane: agent turns with no message of his, long enough for
    pass 5 to rebuild it as a task (>= 15 events over >= 10 minutes)."""
    out = []
    for k in range(n):
        m = start_min + k
        e = _agent(f"2026-04-28T{9 + m // 60:02d}:{m % 60:02d}:00Z", project,
                   f"turn {k} of the build", seq0 + k, session_id=session_id)
        out.append(dataclasses.replace(e, metadata={**e.metadata, "agent_lane": f"agent-{session_id}"}))
    return out


def test_a_renamed_repo_still_joins_the_session_that_opened_its_pr():
    """2026-04-29: the session printed `.../claude-live-feedback-plugin/pull/6`
    and the API reports that PR under `claude-workspaces-plugin`, because the
    repo was renamed. Keyed on (repo, number) the join never matched, and a
    1,009-line PR sat as its own task with an estimate and no time. The
    conversation here is 10h before the PR, past the window adoption uses, so
    only the link can attach it."""
    def run(second_repo=False):
        events = [
            _turned(_agent("2026-04-28T22:30:00Z", "claude-live-feedback-plugin",
                           "opened https://github.com/arivera/claude-live-feedback-plugin/pull/6", 0), 1),
            _pr("2026-04-29T08:41:53Z", "claude-live-feedback-plugin", "feat: block-deletion API", 6, 1,
                repo="arivera/claude-workspaces-plugin"),
        ]
        if second_repo:
            # A second repo under the same project: now the bare name does not
            # say which repo the old URL meant, so nothing is aliased.
            events.append(_pr("2026-04-29T09:00:00Z", "claude-live-feedback-plugin",
                              "chore: bump the shared client", 6, 2, repo="arivera/claude-workspaces-server"))
        return {i: t.task_id for t in segment(events) for i in t.event_ids}

    host = run()
    assert host[1] == host[0], "the PR joins the session that printed its URL under the old name"
    two = run(second_repo=True)
    assert two[1] != two[0] and two[2] != two[0], (
        "two repos under one project are never folded into each other — with a PR 6 in "
        "each of them, the old name says nothing about which one it meant"
    )


def test_a_live_repo_name_is_never_aliased_away():
    """The alias is read only off a name no artifact came from, and only when
    that bare name is a project whose work all comes from one repo. Here
    `arivera/career-tool` is a real repo shipping its own PRs AND the bare name
    of a project that ships from `arivera/career-tool-app`: the printed URL
    means the repo, so PR 6 of one is never credited to PR 6 of the other."""
    events = [
        _turned(_agent("2026-04-28T22:30:00Z", "recruiting",
                       "opened https://github.com/arivera/career-tool/pull/6", 0), 1),
        _pr("2026-04-29T08:00:00Z", "recruiting", "Applications inbox", 6, 1, repo="arivera/career-tool"),
        _pr("2026-04-29T08:41:53Z", "career-tool", "Tracker rewrite", 6, 2, repo="arivera/career-tool-app"),
    ]
    host = {i: t.task_id for t in segment(events) for i in t.event_ids}
    assert host[1] == host[0], "the repo it named is the repo it meant"
    assert host[2] != host[0], "the other project's PR 6 is not this one"


def test_an_unrelated_repo_url_aliases_to_nothing():
    """A printed URL for a repo that is neither known nor a project's name is
    somebody else's repo — a dependency, a fork, a colleague's. It names no
    project, so it aliases to nothing and claims no PR."""
    events = [
        _turned(_agent("2026-04-28T22:30:00Z", "weekly-review",
                       "the fix is in https://github.com/someone-else/a-library/pull/6", 0), 1),
        _pr("2026-04-29T08:41:53Z", "claude-live-feedback-plugin", "feat: block-deletion API", 6, 1,
            repo="arivera/claude-workspaces-plugin"),
    ]
    host = {i: t.task_id for t in segment(events) for i in t.event_ids}
    assert host[1] != host[0], "a library's PR 6 is not the plugin's PR 6"


def test_the_run_says_how_many_commits_it_dropped():
    """Call-site check: a limitation nobody prints is a limitation nobody knows
    about, and a silent drop looks the same as a week with less work in it."""
    from pathlib import Path
    src = (Path(__file__).parent.parent / "src" / "weekly_review" / "cli.py").read_text()
    assert "dropped=_sessionless" in src, "cli.py must ask segment() for what it dropped"
    assert "commits with no session within 30 min" in src, "and must print the count"


def test_a_merged_pr_with_no_session_stays_a_measured_task():
    """The ruling is about commits. A PR was opened by somebody's session, even
    when that session is outside the window, so an artifact-only PR task is a
    thin measurement, not a machine's."""
    events = [
        _pr("2026-09-07T15:00:49Z", "notes-repo", "Refresh the subscriptions archive", 41, 0),
        _uev("2026-09-07T19:00:00Z", "notes-repo", "what landed today?", 1, session_id="a"),
    ]
    dropped: list = []
    tasks = segment(events, dropped=dropped)
    assert dropped == [], f"a PR is not a commit; got {[t.event_ids for t in dropped]}"
    assert any(0 in t.event_ids for t in tasks)


def test_a_lane_that_only_stamped_a_card_makes_no_measured_task():
    """28 tasks on the weeks of 2026-08-17 and 09-07 held nothing but stamps.

    `is_board_work_evidence` gates three of the four routes to a card task on
    the lane having done something; the fourth — a lane merely assigned a card —
    is not gated, so a dispatch whose real work fell outside the window still
    raised a task with an estimate and no evidence. The drop is a post-condition
    on the finished task, so a fifth route would land in the same net.
    """
    from dataclasses import replace
    proj = "claude-workspaces-plugin"

    def call(ts, card, seq, turn):
        return replace(_turn_ev(ts, proj, f"task_transition {card}", seq, turn=turn,
                                lane="agent-mate", card=card), kind="tool_call")

    events = [
        _uev("2026-09-11T21:00:00Z", proj, "run the sprint", 0, session_id="lead"),
        _board("2026-09-11T22:00:00Z", proj, "t-AAA", 1),
        call("2026-09-11T22:00:10Z", "t-AAA", 2, turn=1),
        call("2026-09-11T22:30:00Z", "t-AAA", 3, turn=2),
    ]
    stamp_only: list = []
    tasks = segment(events, stamp_only=stamp_only)
    assert not any(t.board_task == "t-AAA" for t in tasks), "a stamp is not work"
    assert [t.board_task for t in stamp_only] == ["t-AAA"], "and the drop is reported"


def test_one_real_event_is_enough_to_keep_a_card_task():
    """The negative half: reading the card is evidence and survives. 16 tasks on
    the week of 09-07 are his review sessions and touches — small but real."""
    from dataclasses import replace
    proj = "claude-workspaces-plugin"

    def call(ts, card, seq, turn):
        return replace(_turn_ev(ts, proj, f"task_transition {card}", seq, turn=turn,
                                lane="agent-mate", card=card), kind="tool_call")

    events = [
        _uev("2026-09-11T21:00:00Z", proj, "run the sprint", 0, session_id="lead"),
        _board("2026-09-11T22:00:00Z", proj, "t-AAA", 1),
        call("2026-09-11T22:00:10Z", "t-AAA", 2, turn=1),
        _turn_ev("2026-09-11T22:10:00Z", proj, "wrote the parser", 3, turn=1, lane="agent-mate"),
        call("2026-09-11T22:30:00Z", "t-AAA", 4, turn=2),
    ]
    stamp_only: list = []
    tasks = segment(events, stamp_only=stamp_only)
    assert any(t.board_task == "t-AAA" for t in tasks)
    assert stamp_only == []


_NOTES = "https://github.com/arivera/notes/pull/9"


def test_the_session_that_linked_a_pr_outranks_the_one_live_nearest_to_it():
    """Week of 2026-09-07: proximity adoption ran before the identity join, and
    the join only moves a task of nothing but artifacts. 51 of 264 merged PRs
    sat on a task that mentions them; one conversation held 22 on 11 agent
    events. The builder linked the PR an hour before it merged; another
    conversation in the same project was mid-turn when it landed."""
    events = [
        _turned(_agent("2026-09-08T22:00:00Z", "notes", f"Opened {_NOTES}", 0, session_id="builder"), 1),
        _turned(_agent("2026-09-08T23:05:00Z", "notes", "sidebar renamed", 1, session_id="other"), 1),
        _pr("2026-09-08T23:06:00Z", "notes", "Calorie logger phase one", 9, 2, repo="arivera/notes"),
    ]
    host = {i: t.task_id for t in segment(events) for i in t.event_ids}
    assert host[2] == host[0], "the PR belongs to the session that linked it"
    assert host[2] != host[1], "a conversation live one minute before it landed did not build it"


def test_a_delegated_build_keeps_the_pr_it_linked_beside_a_live_conversation():
    """Proximity adoption now runs after the delegated builds exist. A build
    holding the PR it linked has turns of its own and is no orphan: adopting it
    would move a day of agent work into whichever conversation was live."""
    lane = _lane("notes", 15, 16, 0)  # 09:15 .. 09:30, no message of his
    lane[8] = dataclasses.replace(lane[8], content_snippet=f"Opened {_NOTES}")
    events = lane + [
        # Another session's turn in the same project, minutes before the PR.
        _turned(_agent("2026-04-28T09:05:00Z", "notes", "sidebar renamed", 20, session_id="other"), 1),
        _pr("2026-04-28T09:08:00Z", "notes", "Calorie logger phase one", 9, 21, repo="arivera/notes"),
    ]
    host = {i: t.task_id for t in segment(events) for i in t.event_ids}
    assert host[17] == host[0], "the lane that linked the PR holds it"
    assert host[0] != host[16], "and the lane stays its own task"


def _builder(lane: str, start_min: int, n: int, seq0: int, text: str = "turn of the build",
             kind: str = "agent_message", session_id: str = "s1", project: str = "notes") -> list[Event]:
    """A builder lane a lead dispatched, inside the lead's own session."""
    out = []
    for k in range(n):
        m = start_min + k
        e = _agent(f"2026-04-28T{9 + m // 60:02d}:{m % 60:02d}:00Z", project, f"{text} {k}", seq0 + k,
                   session_id=session_id)
        out.append(dataclasses.replace(e, kind=kind, metadata={**e.metadata, "agent_lane": lane}))
    return out


def test_a_builder_lane_is_its_own_task_not_part_of_his_last_message():
    """2026-09-08: his one message at 22:41 was answered by 22:57, and its task
    held 12 builders and 6 PRs through 05:00 (b689a63a57, est 28h)."""
    events = [
        _uev("2026-04-28T09:00:00Z", "notes", "please check the deploy status", 0),
        _agent("2026-04-28T09:01:00Z", "notes", "the deploy is healthy", 1),
        *_builder("agent-abuilder-1", 5, 16, 2),
    ]
    tasks = segment(events)
    host = {i: t for t in tasks for i in t.event_ids}
    assert 0 not in host, "his message starts no task"
    assert host[2] is not host.get(1), "the builder is not part of the reply's unit"
    assert host[2].delegated and all(host[i] is host[2] for i in range(2, 18))


def test_two_builders_in_one_session_are_two_tasks():
    """2026-09-11: grouped per session, one lead's day was one task of 49
    lanes, 12,717 events and 27 merged PRs (d41afbd69b)."""
    events = [*_builder("agent-abuilder-1", 5, 16, 0, session_id="lead"),
              *_builder("agent-abuilder-2", 6, 16, 16, session_id="lead")]
    tasks = segment(events)
    assert sorted(sorted(t.event_ids) for t in tasks) == [list(range(16)), list(range(16, 32))]


def test_a_builder_task_is_titled_by_what_the_lead_called_the_work():
    lane = _builder("agent-abuilder-1", 5, 16, 0, session_id="lead")
    lane[0] = dataclasses.replace(
        lane[0], kind="subagent_input",
        content_snippet='<teammate-message teammate_id="team-lead" summary="Build the latency fixes">\nRead the brief')
    tasks = segment(lane)
    assert [t.title for t in tasks] == ["Build the latency fixes"]


def test_the_task_that_linked_a_pr_first_holds_it_over_the_one_that_linked_it_most():
    """The first link is `gh pr create` printing the URL. The lead who reviews
    and merges the PR links it more often, and later."""
    events = [
        _printed(_agent("2026-04-28T09:30:00Z", "notes", f"opened {_NOTES}", 0, session_id="builder"), turn=1),
        *[_printed(_agent(f"2026-04-28T09:{40 + k}:00Z", "fleet", f"reviewing {_NOTES}", 1 + k, session_id="lead"),
                   turn=1 + k)
          for k in range(3)],
        _pr("2026-04-28T09:50:00Z", "other-slug", "Phase one", 9, 4, repo="arivera/notes"),
    ]
    host = {i: t.task_id for t in segment(events) for i in t.event_ids}
    assert host[4] == host[0] != host[1]


def _worktree_fixture(pr_worktree: str) -> list[Event]:
    call = _builder("agent-abuilder-1", 5, 16, 2, kind="tool_call",
                    text="git -C /repo/.claude/worktrees/reland status")
    push = _printed(_agent("2026-04-28T09:25:00Z", "notes",
                           f"git -C /repo/.claude/worktrees/{pr_worktree} push && gh pr create", 18),
                    kind="tool_call", agent_lane="main", turn=2)
    return [
        # His message (index 0) is on no task; the lead's turns are the day's
        # coordination until the PR's unit claims the one that opened it.
        _uev("2026-04-28T09:00:00Z", "notes", "please land the rebuild today", 0),
        _turned(_agent("2026-04-28T09:01:00Z", "notes", "dispatching a builder", 1), 1),
        *call,
        push,
        _printed(_agent("2026-04-28T09:25:30Z", "notes", _NOTES, 19), agent_lane="main", turn=2),
        _pr("2026-04-28T09:25:31Z", "notes", "Phase one", 9, 20, repo="arivera/notes"),
    ]


def test_a_builder_joins_the_pr_the_lead_opened_from_its_worktree():
    """2026-09-08 23:47: the lead's main lane pushed the builder's branch and
    ran `gh pr create`, so the PR sat on the lead's turn and the build on a
    lane task with no PR: one piece of work, priced twice."""
    events = _worktree_fixture("reland")
    host = {i: t.task_id for t in segment(events) for i in t.event_ids}
    assert host[20] == host[19] == host[18], "the PR holds the turn that opened it"
    assert all(host[i] == host[20] for i in range(2, 18)), "the builder joins the task holding its PR"
    assert host[1] != host[20], "the dispatch turn stays the day's coordination"


def test_a_builder_does_not_join_a_pr_opened_from_another_worktree():
    events = _worktree_fixture("something-else")
    host = {i: t.task_id for t in segment(events) for i in t.event_ids}
    assert host[20] == host[19] == host[18]
    assert host[2] != host[20]


def _squash_fixture(title: str, merged_at: str = "2026-04-28T10:00:00Z") -> list[Event]:
    return [
        # His two messages (0, 3) are on no task; the builder's turn and the
        # lead's turn are each their project's coordination for the day.
        _uev("2026-04-28T09:00:00Z", "notes", "build the calorie logger", 0, session_id="builder"),
        _printed(_agent("2026-04-28T09:30:00Z", "notes", f"opened {_NOTES}", 1, session_id="builder"), turn=1),
        _pr(merged_at, "notes", "Calorie logger phase one", 9, 2, repo="arivera/notes"),
        _uev("2026-04-28T09:50:00Z", "fleet", "merge whatever is green", 3, session_id="lead"),
        _printed(_agent("2026-04-28T10:00:20Z", "fleet", "merged, main is at abc1234", 4, session_id="lead"),
                 printed_shas=["abc1234"], turn=1),
        _commit("2026-04-28T10:00:05Z", "notes", title, "abc1234", 5),
    ]


def test_a_squash_commit_goes_with_its_pr_not_with_the_lead_that_printed_its_hash():
    """Week of 2026-09-07: a lead's task was estimated at 80h from 22 squash
    commits; 21 of their PRs sat on the builders' tasks, priced a second time."""
    events = _squash_fixture("Tidy the readme (#9)")
    host = {i: t.task_id for t in segment(events) for i in t.event_ids}
    assert host[2] == host[1]
    assert host[5] == host[1] != host[4]
    assert printed_artifact_credit(segment(events), events)["commits_on_printer"] == 1


def test_a_commit_that_is_no_squash_stays_with_the_session_that_printed_it():
    events = _squash_fixture("Tidy the readme")
    host = {i: t.task_id for t in segment(events) for i in t.event_ids}
    assert host[5] == host[4]


def test_a_squash_title_only_counts_beside_the_merge_it_names():
    """Another repo's #9, merged hours away, is a different pull request."""
    events = _squash_fixture("Tidy the readme (#9)", merged_at="2026-04-28T07:00:00Z")
    host = {i: t.task_id for t in segment(events) for i in t.event_ids}
    assert host[5] == host[4]


def _two_pr_fixture(one_turn: bool = False, squash: bool = False) -> list[Event]:
    """A lead session lands two builders' PRs from two worktrees. With
    `one_turn` the lead opened both from the same turn."""
    def main(ts, text, seq, turn, kind="tool_call", **md):
        e = _agent(ts, "notes", text, seq, session_id="lead")
        return dataclasses.replace(e, kind=kind, metadata={**e.metadata, "agent_lane": "main", "turn": turn, **md})
    events = [
        _uev("2026-04-28T09:00:00Z", "notes", "please land both rebuilds today", 0, session_id="lead"),
        *_builder("agent-alpha", 5, 16, 1, kind="tool_call", session_id="lead",
                  text="git -C /repo/.claude/worktrees/alpha status"),
        # Beta starts a minute later: two tool-call-only lanes starting in the
        # same second share a stable id (same project, ts and fallback title).
        *_builder("agent-beta", 6, 16, 17, kind="tool_call", session_id="lead",
                  text="git -C /repo/.claude/worktrees/beta status"),
        main("2026-04-28T09:25:00Z", "git -C /repo/.claude/worktrees/alpha push && gh pr create", 33, 1),
        main("2026-04-28T09:25:30Z", _NOTES, 34, 1, kind="tool_result"),
        _pr("2026-04-28T09:25:31Z", "notes", "Rebuild the alpha path", 9, 35, repo="arivera/notes"),
        main("2026-04-28T09:30:00Z", "git -C /repo/.claude/worktrees/beta push && gh pr create", 36,
             1 if one_turn else 2),
        main("2026-04-28T09:30:30Z", "https://github.com/arivera/notes/pull/10", 37, 1 if one_turn else 2,
             kind="tool_result"),
        _pr("2026-04-28T09:30:31Z", "notes", "Rebuild the beta path", 10, 38, repo="arivera/notes"),
    ]
    if squash:
        events.append(_commit("2026-04-28T09:25:40Z", "notes", "Rebuild the alpha path (#9)", "abc1234", 39))
    return events


def test_a_task_holding_two_merged_prs_is_split_into_one_unit_per_pr():
    """The user, 2026-09-17: one PR per prompt. The task he opened held 12 PRs
    and 10k lines across four areas of work; 22 PRs in one prompt read 80h."""
    events = _two_pr_fixture()
    tasks = segment(events)
    host = {i: t.task_id for t in tasks for i in t.event_ids}
    assert host[35] == host[34] == host[33], "PR 9 holds the turn that opened it"
    assert all(host[i] == host[35] for i in range(1, 17)), "and the lane that built in its worktree"
    assert host[38] == host[37] == host[36]
    assert all(host[i] == host[38] for i in range(17, 33))
    assert host[35] != host[38]
    assert 0 not in host, "his message is on no task"
    assert merged_pr_units(tasks, events) == {"merged_prs": 2, "on_a_task": 2, "tasks_with_two_or_more": 0}
    # The run db is written from the trace, not event_ids: a stale step put
    # all 15 PRs back on the split task on disk while the run printed 0.
    for t in tasks:
        traced = {s["event_idx"] for s in t.segmentation_trace if s.get("event_idx", -1) >= 0}
        assert traced == set(t.event_ids), t.title


def test_a_turn_that_opened_two_prs_stays_with_the_lead():
    events = _two_pr_fixture(one_turn=True)
    tasks = segment(events)
    host = {i: t for t in tasks for i in t.event_ids}
    assert host[33] is host[34] is host[36] is host[37], "a batch open is triage, not one PR's work"
    assert host[33].unit_kind == "coordination" and host[33] not in (host[35], host[38])
    assert host[35] is not host[38]
    assert host[1] is host[35] and host[17] is host[38]
    # A unit cut from the task that printed its PR is still credited there;
    # the health line encodes the rule, and the rule changed with the split.
    credit = printed_artifact_credit(segment(events), events)
    assert (credit["prs_on_printer"], credit["prs_printed"]) == (2, 2)


def test_a_squash_commit_follows_its_pr_into_the_unit():
    events = _two_pr_fixture(squash=True)
    host = {i: t.task_id for t in segment(events) for i in t.event_ids}
    assert host[39] == host[35] != host[38]


def test_merged_pr_units_counts_a_task_built_with_two_merged_prs():
    """The check the run prints every run: a task with two merged PRs is a
    prompt pricing two PRs, and must read 0."""
    events = [_pr("2026-04-28T09:00:00Z", "notes", "One", 1, 0, repo="arivera/notes"),
              _pr("2026-04-28T09:01:00Z", "notes", "Two", 2, 1, repo="arivera/notes"),
              _pr("2026-04-28T09:02:00Z", "notes", "Three", 3, 2, repo="arivera/notes")]
    pooled = Task(task_id="x", project="notes", title="both", event_ids=[0, 1])
    assert merged_pr_units([pooled], events) == {"merged_prs": 3, "on_a_task": 2, "tasks_with_two_or_more": 1}


def test_a_turn_that_opened_a_merged_pr_beside_an_unmerged_one_stays_with_the_lead():
    """Review of ab41fca: the turn test counted only this task's merged PRs,
    so a turn that opened #9 and #11 (not merged) was claimed whole by #9."""
    events = _two_pr_fixture()
    extra = [dataclasses.replace(events[33], ts="2026-04-28T09:26:00Z", seq=39,
                                 content_snippet="git -C /repo/.claude/worktrees/gamma push && gh pr create"),
             dataclasses.replace(events[34], ts="2026-04-28T09:26:30Z", seq=40,
                                 content_snippet="https://github.com/arivera/notes/pull/11")]
    host = {i: t for t in segment(events + extra) for i in t.event_ids}
    assert host[33] is host[34] is host[39] is host[40] and host[33].unit_kind == "coordination"
    assert host[35] is not host[33] and host[1] is host[35]


def test_a_squash_commit_picks_the_pr_of_its_number_merged_nearest():
    """Two repos both have a #9; a commit's repo field is the slug."""
    events = _two_pr_fixture()
    events[38] = _pr("2026-04-28T09:30:31Z", "notes", "Rebuild the beta path", 9, 38, repo="other/notes")
    events[37] = dataclasses.replace(events[37], content_snippet="https://github.com/other/notes/pull/9")
    events.append(_commit("2026-04-28T09:25:40Z", "notes", "Rebuild the alpha path (#9)", "abc1234", 39))
    host = {i: t.task_id for t in segment(events) for i in t.event_ids}
    assert host[39] == host[35] != host[38]


# --- A lead's own lane is never one group (the user, 2026-09-17) ---------------


def _lead_turns(day: str, hours: list[int], turn0: int, seq0: int, text: str = "tool", tool_name: str = "Bash",
                session_id: str = "lead", project: str = "notes", kind: str = "tool_call") -> list[Event]:
    """One main-lane turn per hour given, each of one event, with no message
    of his and no board task: what a lead does between dispatches."""
    out = []
    for k, h in enumerate(hours):
        e = _agent(f"{day}T{h:02d}:00:00Z", project, text, seq0 + k, session_id=session_id)
        out.append(dataclasses.replace(e, kind=kind, metadata={
            **e.metadata, "agent_lane": "main", "turn": turn0 + k, "tool_name": tool_name, "trigger": "agent"}))
    return out


def _local_days(events: list[Event]) -> set[str]:
    from weekly_review.segmentation import _local_day, _parse_ts
    return {_local_day(_parse_ts(e.ts)) for e in events}


def test_a_lead_session_with_no_quiet_hour_is_one_coordination_unit_per_day_not_one_task():
    """The task the user opened: one lead session from 09-10 18:21 to 09-11 23:07
    with no 6-hour gap, 5,440 events, 15 merged PRs. Turns every two hours
    across two days must land on two units, one per calendar day."""
    hours_day1 = [8, 10, 12, 14, 16, 18, 20, 22]
    events = (_lead_turns("2026-04-28", hours_day1, 1, 0)
              + _lead_turns("2026-04-29", hours_day1, 9, 8))
    days = _local_days(events)
    assert len(days) >= 2, "the fixture must cross a local midnight"
    tasks = segment(events)
    units = [t for t in tasks if t.event_ids]
    assert len(units) == len(days), [(t.title, t.event_ids) for t in tasks]
    assert {t.unit_kind for t in units} == {"coordination"}
    for t in units:
        assert len(_local_days([events[i] for i in t.event_ids])) == 1, "a coordination unit is one calendar day"
    assert sum(len(t.event_ids) for t in units) == len(events), "and nothing is dropped"


def test_two_lead_sessions_on_one_day_share_one_coordination_unit():
    """Coordination is per project per day, not per session: grouping per
    session is the rule that made a lead's day one task."""
    events = _lead_turns("2026-04-28", [9, 11], 1, 0, session_id="lead-a") + \
        _lead_turns("2026-04-28", [10, 12], 1, 2, session_id="lead-b")
    tasks = [t for t in segment(events) if t.event_ids]
    assert len(tasks) == 1 and tasks[0].unit_kind == "coordination"
    assert sorted(tasks[0].event_ids) == [0, 1, 2, 3]


def test_a_lane_with_a_seven_hour_gap_is_still_one_unit():
    """The 6-hour cut is gone: a lane is one unit however long it paused."""
    first = _builder("agent-alpha", 0, 16, 0, session_id="lead")  # 09:00 .. 09:15
    later = [dataclasses.replace(e, ts=e.ts.replace("T09:", "T17:"), seq=e.seq + 16) for e in first]
    tasks = [t for t in segment(first + later) if t.event_ids]
    assert len(tasks) == 1, [(t.title, len(t.event_ids)) for t in tasks]
    assert tasks[0].unit_kind == "lane" and len(tasks[0].event_ids) == 32


def test_a_turn_that_wrote_in_a_worktree_is_that_worktrees_unit_and_joins_its_pr():
    """A lead's own edits are task work, not coordination (199 edit or write
    calls in one lead's lane on the week of 2026-09-07). They go to the
    worktree's unit, and pass 6d puts that unit with the PR opened from it."""
    edits = _lead_turns("2026-04-28", [9, 10], 1, 0, tool_name="Edit",
                        text="{'file_path': '/repo/.claude/worktrees/alpha/src/app.py'}")
    chatter = _lead_turns("2026-04-28", [11], 3, 2, text="post_status")
    events = edits + chatter + [
        # The lead opens the PR from that worktree at 12:00, in its own turn.
        *_lead_turns("2026-04-28", [12], 4, 3, text="git -C /repo/.claude/worktrees/alpha push && gh pr create"),
        dataclasses.replace(_lead_turns("2026-04-28", [12], 4, 4, text=_NOTES, kind="tool_result")[0],
                            ts="2026-04-28T12:00:30Z"),
        _pr("2026-04-28T12:00:31Z", "notes", "Rebuild the alpha path", 9, 5, repo="arivera/notes"),
    ]
    host = {i: t for t in segment(events) for i in t.event_ids}
    assert host[0] is host[1], "both edit turns are one worktree unit"
    assert host[2] is not host[0] and host[2].unit_kind == "coordination", "the status turn is coordination"
    assert host[5] is host[0], "the PR opened from the worktree lands with the lead's edits there"
    assert host[3] is host[5], "and so does the turn that opened it"
    assert host[0].unit_kind == "lead_worktree", "the edits are the unit; the day did not absorb them"


def test_a_pr_a_lead_opened_from_its_day_goes_to_the_lane_that_built_it():
    """Before this, pass 6d merged the builder INTO the lead's coordination
    day (the day was the PR's print host): one 417-event lane sat inside a
    coordination unit on the week of 2026-09-07. The PR and its opening turn
    move to the lane instead; the day keeps its other turns."""
    lane = _builder("agent-alpha", 0, 16, 0, session_id="lead",
                    text="Edit /repo/.claude/worktrees/alpha/src/app.py")  # 09:00 .. 09:15
    lane = [dataclasses.replace(e, kind="tool_call", metadata={**e.metadata, "tool_name": "Edit"}) for e in lane]
    events = lane + _lead_turns("2026-04-28", [10], 1, 16, text="post_status") + [
        *_lead_turns("2026-04-28", [11], 2, 17, text="git -C /repo/.claude/worktrees/alpha push && gh pr create"),
        dataclasses.replace(_lead_turns("2026-04-28", [11], 2, 18, text=_NOTES, kind="tool_result")[0],
                            ts="2026-04-28T11:00:30Z"),
        # The repo is what the link join keys on; without it the opened PR is a
        # fragment nothing places now that proximity is gone.
        _pr_opened("2026-04-28T11:00:31Z", "notes", "Rebuild the alpha path", 9, 19, repo="arivera/notes"),
    ]
    host = {i: t for t in segment(events) for i in t.event_ids}
    assert host[0].unit_kind == "lane" and all(host[i] is host[0] for i in range(16))
    assert host[19] is host[0] and host[17] is host[0] and host[18] is host[0], "PR and opening turn on the lane"
    assert host[16].unit_kind == "coordination" and host[16] is not host[0]
    assert host[16].event_ids == [16]


def test_a_coordination_unit_that_holds_one_merged_pr_gives_it_up():
    """A coordination unit is priced as a lead's day; a PR diff in that
    prompt would price the day as the PR. Even one merged PR is cut out
    with its opening turn, where a conversation keeps a single PR."""
    events = _lead_turns("2026-04-28", [9, 10], 1, 0, text="post_status") + [
        *_lead_turns("2026-04-28", [11], 3, 2, text="git -C /repo push && gh pr create"),
        dataclasses.replace(_lead_turns("2026-04-28", [11], 3, 3, text=_NOTES, kind="tool_result")[0],
                            ts="2026-04-28T11:00:30Z"),
        _pr("2026-04-28T11:00:31Z", "notes", "Rebuild the alpha path", 9, 4, repo="arivera/notes"),
    ]
    host = {i: t for t in segment(events) for i in t.event_ids}
    assert host[0] is host[1] and host[0].unit_kind == "coordination"
    assert host[4] is host[2] is host[3], "the PR leaves with the turn that opened it"
    assert host[4] is not host[0] and host[4].unit_kind == "pr"


def test_a_coordination_unit_hosts_no_stray_artifact():
    """Coordination is live all day by construction, so proximity adoption
    would hand it every commit nobody printed."""
    events = _lead_turns("2026-04-28", [9, 10, 11], 1, 0, text="post_status") + [
        _commit("2026-04-28T10:30:00Z", "notes", "Unprinted commit", "abc1234", 3),
    ]
    host = {i: t for t in segment(events) for i in t.event_ids}
    assert host[3] is not host[0]
    assert host[0].unit_kind == "coordination"


def test_the_run_prints_the_lead_lane_report():
    """Call-site check: the number that would have shown the user's task exists
    only if cli.py prints it every run."""
    from pathlib import Path
    src = (Path(__file__).parent.parent / "src" / "weekly_review" / "cli.py").read_text()
    assert "lead_lane_report(" in src
    assert "coordination units per project" in src
    assert "widest lane / worktree / coordination unit" in src


def test_lead_lane_report_measures_the_widest_units():
    from weekly_review.segmentation import lead_lane_report
    events = (_lead_turns("2026-04-28", [8, 20], 1, 0)  # a lead: no message of his, turns woken by agents
              + [dataclasses.replace(e, metadata={**e.metadata, "tool_name": "Agent"}) for e in
                 _lead_turns("2026-04-28", [9], 3, 2)])
    tasks = segment(events)
    rep = lead_lane_report(tasks, events)
    assert rep["lead_sessions"] == 1 and rep["lead_lane_events"] == 3
    days = _local_days(events)
    assert rep["coordination"]["notes"]["units"] == len(days)
    assert rep["coordination"]["notes"]["share"] == 1.0
    assert rep["widest_pass5_unit"]["unit_kind"] == "coordination"
    assert rep["widest_pass5_unit"]["hours"] <= 24


def test_a_lead_turn_that_opened_two_prs_stays_on_the_day_and_each_pr_goes_to_its_own_lane():
    """Review of f87ae8a: the coordination branch of pass 6d moved the whole
    opening turn with the first PR, so the second PR's opening landed on the
    first lane and the second lane got nothing."""
    alpha = [dataclasses.replace(e, kind="tool_call", metadata={**e.metadata, "tool_name": "Edit"})
             for e in _builder("agent-alpha", 0, 16, 0, session_id="lead",
                               text="Edit /repo/.claude/worktrees/alpha/src/app.py")]
    beta = [dataclasses.replace(e, kind="tool_call", metadata={**e.metadata, "tool_name": "Edit"})
            for e in _builder("agent-beta", 0, 16, 16, session_id="lead",
                              text="Edit /repo/.claude/worktrees/beta/src/app.py")]
    both = "https://github.com/arivera/notes/pull/9 https://github.com/arivera/notes/pull/10"
    events = alpha + beta + _lead_turns("2026-04-28", [10], 1, 32, text="post_status") + [
        *_lead_turns("2026-04-28", [11], 2, 33,
                     text="git -C /repo/.claude/worktrees/alpha push && gh pr create; "
                          "git -C /repo/.claude/worktrees/beta push && gh pr create"),
        dataclasses.replace(_lead_turns("2026-04-28", [11], 2, 34, text=both, kind="tool_result")[0],
                            ts="2026-04-28T11:00:30Z"),
        _pr("2026-04-28T11:00:31Z", "notes", "Rebuild alpha", 9, 35, repo="arivera/notes"),
        _pr("2026-04-28T11:00:32Z", "notes", "Rebuild beta", 10, 36, repo="arivera/notes"),
    ]
    host = {i: t for t in segment(events) for i in t.event_ids}
    assert host[0] is not host[16], "the two builders are two units"
    assert host[35] is not host[36], "each merged PR is its own unit"
    assert host[35] is not host[16], "the beta build is not priced with PR 9"
    assert host[33] is host[34] and host[33].unit_kind == "coordination", "a turn that opened two PRs stays on the day"
    assert host[32] is host[33]
    # Known limit: one turn that opened PRs from two worktrees names both in
    # the lookback, so which PR the alpha build lands on is not decided here.


# --- Linking priced units to board tasks (method Step 5, 2026-09-18) --------

_T1, _T2, _T3 = tid("AAAAAAAAAAAA"), tid("BBBBBBBBBBBB"), tid("CCCCCCCCCCCC")


def _link(events: list[Event], links: BoardLinks | None = None) -> tuple[Task, dict]:
    """One unit holding every event, run through the link pass alone."""
    t = Task(task_id="u", project="notes", title="unit")
    for i, e in enumerate(events):
        t.touch(i, datetime.fromisoformat(e.ts.replace("Z", "+00:00")), "test")
    counts = _link_units_to_board_tasks({"notes": [t]}, events, links)
    return t, counts


def test_a_unit_links_to_the_board_task_whose_record_names_its_pr():
    pr = _pr("2026-04-28T09:25:31Z", "notes", "Rebuild the alpha path", 9, 0, repo="arivera/notes")
    links = BoardLinks(pr_tasks={("arivera/notes", "9"): frozenset({_T1})}, known=frozenset({_T1}))
    t, counts = _link([pr], links)
    assert (t.board_task, t.link_source) == (_T1, "board_pr")
    assert counts["units_linked"] == counts["linked_board_pr"] == 1
    assert _link([pr])[0].board_task is None, "no store, no link"


def test_a_unit_links_to_the_board_task_its_pr_body_or_branch_names():
    pr = _pr("2026-04-28T09:25:31Z", "notes", "Rebuild the alpha path", 9, 0, repo="arivera/notes")
    pr = dataclasses.replace(pr, metadata={**pr.metadata, "named_tasks": [_T2]})
    t, counts = _link([pr], BoardLinks(known=frozenset({_T2})))
    assert (t.board_task, t.link_source) == (_T2, "pr_ref")
    assert counts["linked_pr_ref"] == 1
    t, _ = _link([pr], BoardLinks(known=frozenset({_T1})))
    assert t.board_task is None, "an id the store does not hold is not a board task"


def test_a_unit_links_to_the_board_task_its_dispatch_prompt_names():
    prompt = _agent("2026-04-28T09:00:00Z", "notes", "build the export", 0)
    prompt = dataclasses.replace(prompt, kind="subagent_input",
                                 metadata={**prompt.metadata, "agent_lane": "agent-x", "card": _T3})
    work = _builder("agent-x", 1, 3, 1)
    t, counts = _link([prompt, *work])
    assert (t.board_task, t.link_source) == (_T3, "dispatch")
    assert counts["linked_dispatch"] == 1


def test_a_unit_links_to_the_board_task_its_worktree_is_named_after():
    work = _builder("agent-x", 1, 3, 0, kind="tool_call",
                    text=f"git -C /repo/.claude/worktrees/{_T1} status")
    t, counts = _link(work, BoardLinks(known=frozenset({_T1})))
    assert (t.board_task, t.link_source) == (_T1, "worktree")
    assert counts["linked_worktree"] == 1
    slug = _builder("agent-x", 1, 3, 0, kind="tool_call", text="git -C /repo/.claude/worktrees/t-page-bar status")
    assert _link(slug)[0].board_task is None, "a worktree named like a task is not one"


def test_a_source_naming_two_board_tasks_abstains_and_the_next_source_is_tried():
    pr = _pr("2026-04-28T09:25:31Z", "notes", "Rebuild the alpha path", 9, 0, repo="arivera/notes")
    pr = dataclasses.replace(pr, metadata={**pr.metadata, "named_tasks": [_T2]})
    links = BoardLinks(pr_tasks={("arivera/notes", "9"): frozenset({_T1, _T3})}, known=frozenset({_T1, _T2, _T3}))
    t, counts = _link([pr], links)
    assert (t.board_task, t.link_source) == (_T2, "pr_ref")
    assert counts["link_ambiguous"] == 1
    pr2 = dataclasses.replace(pr, metadata={**pr.metadata, "named_tasks": [_T1, _T2]})
    t, counts = _link([pr2], links)
    assert t.board_task is None and counts["link_ambiguous"] == 1, "two abstaining sources are one unit"


def test_segment_runs_the_link_pass_with_the_store_links_it_is_given():
    """Call-site test: `presence_hours_by_task` shipped tested and never
    called. The link pass must run inside `segment()`, not only when a test
    calls it directly."""
    events = _two_pr_fixture()
    links = BoardLinks(pr_tasks={("arivera/notes", "9"): frozenset({_T1})}, known=frozenset({_T1}))
    stats: dict = {}
    tasks = segment(events, stats=stats, board_links=links)
    host = {i: t for t in tasks for i in t.event_ids}
    assert (host[35].board_task, host[35].link_source) == (_T1, "board_pr")
    assert host[38].board_task is None
    assert stats["units_linked"] == stats["linked_board_pr"] == 1
    assert stats["units_linkable"] >= 2


def test_a_lane_that_worked_a_board_task_is_its_own_unit_linked_to_it_not_pooled_onto_it():
    """473d13e3c7 on the week of 2026-09-07: one board task held 56 lanes,
    13,259 events and 163.9h because every lane inheriting it was pooled on."""
    proj = "claude-workspaces-plugin"
    lane = [dataclasses.replace(e, session_id="lead", ts=f"2026-09-11T22:{k:02d}:00Z",
                                metadata={**e.metadata, "card": "t-AAA"})
            for k, e in enumerate(_builder("agent-x", 0, 16, 2, project=proj))]
    short = [dataclasses.replace(e, session_id="lead", ts=f"2026-09-11T23:{k:02d}:00Z",
                                 metadata={**e.metadata, "card": "t-AAA"})
             for k, e in enumerate(_builder("agent-y", 0, 3, 18, project=proj))]
    events = [
        _uev("2026-09-11T21:00:00Z", proj, "run the sprint", 0, session_id="lead"),
        _card_msg("2026-09-11T21:30:00Z", proj, "t-AAA", 1),
        *lane, *short,
    ]
    tasks = segment(events)
    host = {i: t for t in tasks for i in t.event_ids}
    assert host[2] is not host[1], "the lane is its own unit"
    assert all(host[i] is host[2] for i in range(2, 18))
    assert (host[2].board_task, host[2].link_source) == ("t-AAA", "lane_card")
    assert host[2].unit_kind == "lane"
    assert host[18] is host[1], "a lane under the floor folds onto the board task's unit"
    assert host[1].board_task == "t-AAA" and host[1].link_source == ""


def test_a_lanes_short_second_stretch_on_another_board_task_folds_onto_that_board_task():
    """A lane over the floor is reassigned for its last three events: that
    stretch is under the floor and goes with its own board task, not the
    day's coordination and not the first board task's unit."""
    proj = "claude-workspaces-plugin"
    lane = [dataclasses.replace(e, session_id="lead", ts=f"2026-09-11T22:{k:02d}:00Z",
                                metadata={**e.metadata, "card": "t-AAA" if k < 16 else "t-BBB"})
            for k, e in enumerate(_builder("agent-x", 0, 19, 2, project=proj))]
    events = [
        _uev("2026-09-11T21:00:00Z", proj, "run the sprint", 0, session_id="lead"),
        _card_msg("2026-09-11T21:30:00Z", proj, "t-AAA", 1),
        _card_msg("2026-09-11T21:31:00Z", proj, "t-BBB", 21),
        *lane,
    ]
    tasks = segment(events)
    host = {i: t for t in tasks for i in t.event_ids}  # list index: lane is 3..21
    assert all(host[i] is host[3] for i in range(3, 19)) and host[3].board_task == "t-AAA"
    assert host[19] is host[20] is host[21] is host[2], "the short stretch folds onto t-BBB's unit"
    assert host[19].board_task == "t-BBB" and host[19].unit_kind == ""
    assert host[19] is not host[1]


def test_a_pr_unit_split_from_a_board_tasks_unit_keeps_its_board_task():
    events = _two_pr_fixture()
    # The split alone, on a task that carries a board task.
    t = Task(task_id="u", project="notes", title="unit", board_task=_T1)
    for i in (33, 34, 35, 36, 37, 38):
        t.touch(i, datetime.fromisoformat(events[i].ts.replace("Z", "+00:00")), "test",
                session_id=events[i].session_id)
    by_project = {"notes": [t]}
    _one_unit_per_merged_pr(by_project, events)
    units = [u for u in by_project["notes"] if u.unit_kind == "pr"]
    assert len(units) >= 1
    assert all((u.board_task, u.link_source) == (_T1, "host") for u in units)
