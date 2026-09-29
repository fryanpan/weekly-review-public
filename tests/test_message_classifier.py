"""The classifier that places his unlinked messages on board tasks (method
Step 3, rule 3). What it may see, what it may answer, where a placement lands,
and that the run calls it."""
import json
import re
from pathlib import Path

from weekly_review.events import Event
from weekly_review.message_classifier import (BoardTask, Placement, attach_placements, candidates_at,
                                              classify_messages, his_unlinked_messages, sample_for_review)
from weekly_review.segmentation import Task, _parse_ts
from tests.fakeids import tid

H = 3600 * 1000
T0 = 1_757_300_000_000  # epoch ms, an arbitrary moment in the week of 2026-09-07


def _bt(tid: str, title: str, *transitions: tuple[int, str], board: str = "Weekly Review",
        goal: str = "Segment the week", body: str = "") -> BoardTask:
    return BoardTask(id=tid, workspace_id="w-1", board=board, title=title, body=body,
                     goal_title=goal, transitions=tuple(sorted(transitions)))


def _msg(ts_ms: int, text: str, seq: int, project: str = "weekly-review", source: str = "live_feedback",
         metadata: dict | None = None) -> Event:
    from datetime import datetime, timezone
    ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return Event(ts=ts, actor="user", project=project, session_id="s1", source_system=source,
                 source_machine="mac_mini", kind="user_message", seq=seq, content_snippet=text,
                 metadata=metadata or {})


class FakeLLM:
    """Answers a fixed text and records every prompt it was shown."""
    def __init__(self, text: str):
        self.text = text
        self.prompts: list[str] = []

    def complete(self, *, system, user, model, max_tokens, temperature):
        self.prompts.append(user)
        return {"text": self.text}


# --- what the classifier may see ------------------------------------------

def test_candidates_are_tasks_in_progress_at_that_moment_or_done_in_the_last_day():
    tasks = [
        _bt(tid("inprogress0001"), "Building", (T0 - 5 * H, "todo"), (T0 - 2 * H, "in-progress")),
        _bt(tid("donerecently01"), "Just shipped", (T0 - 30 * H, "in-progress"), (T0 - 3 * H, "done")),
        _bt(tid("doneoldxxxxxx1"), "Shipped last week", (T0 - 80 * H, "in-progress"), (T0 - 40 * H, "done")),
        _bt(tid("notyetxxxxxxx1"), "Filed later", (T0 + 1 * H, "todo"), (T0 + 2 * H, "in-progress")),
        _bt(tid("todoonlyxxxxx1"), "Waiting", (T0 - 9 * H, "todo")),
        _bt(tid("finishedbefor1"), "Was in progress, then done long ago",
            (T0 - 50 * H, "in-progress"), (T0 - 26 * H, "done")),
    ]
    got = {bt.id: label for bt, label in candidates_at(tasks, T0)}
    assert got == {tid("inprogress0001"): "in progress", tid("donerecently01"): "done 3h earlier"}


def test_candidates_can_be_limited_to_the_boards_that_hold_the_work():
    tasks = [_bt(tid("mineinprogres1"), "Mine", (T0 - H, "in-progress")),
             _bt(tid("otherboardxxx1"), "Theirs", (T0 - H, "in-progress"), board="Lead Agent")]
    assert [bt.id for bt, _ in candidates_at(tasks, T0, boards={"Weekly Review"})] == [tid("mineinprogres1")]
    assert len(candidates_at(tasks, T0)) == 2


def test_a_task_reopened_after_done_reads_as_in_progress_again():
    bt = _bt(tid("reopenedxxxxx1"), "Back in progress",
             (T0 - 10 * H, "in-progress"), (T0 - 6 * H, "done"), (T0 - 1 * H, "in-progress"))
    assert bt.status_at(T0) == ("in-progress", T0 - 1 * H)
    assert bt.status_at(T0 - 3 * H) == ("done", T0 - 6 * H)


# --- which messages it runs on ---------------------------------------------

def test_only_his_messages_on_no_unit_are_unlinked():
    events = [_msg(T0, "placed", 1), _msg(T0 + 1000, "free", 2),
              Event(ts="2026-09-08T00:00:03Z", actor="agent", project="weekly-review", session_id="s1",
                    source_system="claude_transcript", source_machine="m", kind="agent_message", seq=3)]
    t = Task(task_id="a", project="weekly-review", title="x")
    t.touch(0, _parse_ts(events[0].ts), "board_task_comment")
    assert his_unlinked_messages([t], events) == [1]


# --- what it may answer -----------------------------------------------------

def _two_tasks():
    return [_bt(tid("parserxxxxxxx1"), "Agent can parse the ledger", (T0 - H, "in-progress"),
                body="Read meetings.jsonl and price each turn."),
            _bt(tid("digestxxxxxxx1"), "the user can read the digest on the board", (T0 - H, "in-progress"))]


def test_a_placement_a_none_and_a_status_request_are_all_counted():
    events = [_msg(T0, "the ledger parse drops closed turns", 1),
              _msg(T0 + 60_000, "where are we overall this week?", 2),
              _msg(T0 + 120_000, "unrelated: fix my bike map tiles", 3)]
    llm = FakeLLM(json.dumps([{"m": 1, "task": tid("parserxxxxxxx1"), "status_request": False},
                              {"m": 2, "task": "none", "status_request": True},
                              {"m": 3, "task": "none", "status_request": False}]))
    placements, stats = classify_messages(events, [0, 1, 2], _two_tasks(), llm=llm, model="m")
    assert [p.board_task for p in placements] == [tid("parserxxxxxxx1"), None, None]
    assert [p.status_request for p in placements] == [False, True, False]
    assert stats["calls"] == 1 and stats["messages"] == 3
    assert stats["placed"] == 1 and stats["none"] == 2 and stats["status_request"] == 1
    assert placements[0].candidates == 2


def test_the_prompt_shows_goal_title_description_and_status_and_his_words():
    events = [_msg(T0, "the ledger parse drops closed turns", 1)]
    llm = FakeLLM("[]")
    classify_messages(events, [0], _two_tasks(), llm=llm, model="m")
    prompt = llm.prompts[0]
    assert tid("parserxxxxxxx1") + " | in progress | goal: Segment the week | Agent can parse the ledger | Read meetings.jsonl" in prompt
    assert "1. [" in prompt and "the ledger parse drops closed turns" in prompt


def test_an_id_it_was_not_shown_is_refused_and_counted():
    events = [_msg(T0, "something", 1)]
    llm = FakeLLM(json.dumps([{"m": 1, "task": tid("invented0001"), "status_request": False}]))
    placements, stats = classify_messages(events, [0], _two_tasks(), llm=llm, model="m")
    assert placements[0].board_task is None
    assert stats["invalid_id"] == 1 and stats["none"] == 1 and stats["placed"] == 0


def test_an_answer_that_does_not_parse_is_none_not_a_crash():
    events = [_msg(T0, "something", 1), _msg(T0 + 1000, "else", 2)]
    llm = FakeLLM("Sure! Message 1 is about the parser.")
    placements, stats = classify_messages(events, [0, 1], _two_tasks(), llm=llm, model="m")
    assert all(p.board_task is None for p in placements)
    assert stats["unparsed"] == 2 and stats["none"] == 2


def test_a_fenced_answer_still_parses():
    events = [_msg(T0, "ledger", 1)]
    llm = FakeLLM('```json\n[{"m": 1, "task": "' + tid("parserxxxxxxx1") + '", "status_request": false}]\n```')
    placements, _ = classify_messages(events, [0], _two_tasks(), llm=llm, model="m")
    assert placements[0].board_task == tid("parserxxxxxxx1")


def test_no_task_in_progress_means_none_and_no_call():
    events = [_msg(T0, "anything", 1)]
    idle = [_bt(tid("todoonlyxxxxx1"), "Waiting", (T0 - H, "todo"))]
    llm = FakeLLM("[]")
    placements, stats = classify_messages(events, [0], idle, llm=llm, model="m")
    assert placements[0].board_task is None and placements[0].candidates == 0
    assert stats["calls"] == 0 and stats["no_candidates"] == 1 and stats["none"] == 1


def test_a_doc_linked_to_several_tasks_adds_them_to_the_candidates():
    events = [_msg(T0, "tighten this", 1, metadata={"doc_id": "d-1", "task_candidates": [tid("oldbutlinked01")]})]
    tasks = _two_tasks() + [_bt(tid("oldbutlinked01"), "Old, but this doc links it",
                                (T0 - 90 * H, "in-progress"), (T0 - 80 * H, "done"))]
    llm = FakeLLM(json.dumps([{"m": 1, "task": tid("oldbutlinked01"), "status_request": False}]))
    placements, stats = classify_messages(events, [0], tasks, llm=llm, model="m")
    assert placements[0].board_task == tid("oldbutlinked01")
    assert tid("oldbutlinked01") + " | linked from this doc" in llm.prompts[0]


def test_messages_are_batched_per_project_and_day_and_the_boards_of_the_message():
    events = [_msg(T0, "a", 1, project="p1"), _msg(T0 + 1000, "b", 2, project="p2"),
              _msg(T0 + 2000, "c", 3, project="p1")]
    tasks = [_bt(tid("p1boardxxxxxx1"), "P1 work", (T0 - H, "in-progress"), board="B1"),
             _bt(tid("p2boardxxxxxx1"), "P2 work", (T0 - H, "in-progress"), board="B2")]
    llm = FakeLLM("[]")
    _, stats = classify_messages(events, [0, 1, 2], tasks, llm=llm, model="m",
                                 boards_of_project={"p1": {"B1"}, "p2": {"B2"}})
    assert stats["calls"] == 2
    assert any(tid("p1boardxxxxxx1") in p and tid("p2boardxxxxxx1") not in p for p in llm.prompts)
    assert any(tid("p2boardxxxxxx1") in p and tid("p1boardxxxxxx1") not in p for p in llm.prompts)


# --- where a placement lands ------------------------------------------------

def test_a_placed_message_joins_the_conversation_unit_on_that_board_task():
    events = [_msg(T0, "free", 1)]
    conv = Task(task_id="conv", project="weekly-review", title="conversation")
    conv.board_task = tid("parserxxxxxxx1")
    lane = Task(task_id="lane", project="weekly-review", title="lane")
    lane.board_task = tid("parserxxxxxxx1")
    lane.unit_kind = "lane"
    for i in range(5):
        lane.touch(10 + i, _parse_ts("2026-09-08T00:00:00Z"), "lane")
    tasks = [conv, lane]
    stats = attach_placements(tasks, events, [Placement(event_idx=0, board_task=tid("parserxxxxxxx1"))])
    assert 0 in conv.event_ids and 0 not in lane.event_ids
    assert conv.segmentation_trace[-1]["rule"] == "classifier"
    assert conv.segmentation_trace[-1]["detail"] == tid("parserxxxxxxx1")
    assert stats == {"to_board_task": 1, "new_units": 0, "to_coordination": 0, "new_coordination": 0}


def test_a_board_task_with_no_unit_yet_gets_one_named_after_it():
    events = [_msg(T0, "free", 1)]
    tasks: list[Task] = []
    stats = attach_placements(tasks, events, [Placement(event_idx=0, board_task=tid("parserxxxxxxx1"))],
                              titles={tid("parserxxxxxxx1"): "Agent can parse the ledger"})
    assert len(tasks) == 1 and tasks[0].board_task == tid("parserxxxxxxx1")
    assert tasks[0].title == "Agent can parse the ledger" and tasks[0].link_source == ""
    assert tasks[0].event_ids == [0] and stats["new_units"] == 1


def test_a_status_request_goes_to_that_days_coordination_unit_made_if_absent():
    events = [_msg(T0, "where are we?", 1), _msg(T0 + 5000, "and now?", 2)]
    tasks: list[Task] = []
    stats = attach_placements(tasks, events, [Placement(event_idx=0, status_request=True),
                                              Placement(event_idx=1, status_request=True)])
    assert len(tasks) == 1 and tasks[0].unit_kind == "coordination" and not tasks[0].delegated
    assert re.fullmatch(r"coordination \d{4}-\d{2}-\d{2}", tasks[0].title)
    assert tasks[0].event_ids == [0, 1]
    assert stats["to_coordination"] == 2 and stats["new_coordination"] == 1


def test_a_status_request_joins_an_existing_coordination_unit_for_that_day():
    events = [_msg(T0, "where are we?", 1)]
    from weekly_review.segmentation import _local_day
    coord = Task(task_id="c", project="weekly-review", title=f"coordination {_local_day(_parse_ts(events[0].ts))}")
    coord.unit_kind = "coordination"
    coord.delegated = True
    coord.touch(7, _parse_ts(events[0].ts), "lead_day")
    tasks = [coord]
    stats = attach_placements(tasks, events, [Placement(event_idx=0, status_request=True)])
    assert len(tasks) == 1 and coord.event_ids == [7, 0] and stats["new_coordination"] == 0
    assert not coord.delegated


def test_a_none_places_nothing():
    events = [_msg(T0, "unrelated", 1)]
    tasks: list[Task] = []
    stats = attach_placements(tasks, events, [Placement(event_idx=0)])
    assert tasks == [] and sum(stats.values()) == 0


# --- the sample for the user -----------------------------------------------------

def test_the_review_sample_is_drawn_from_placed_messages_only_and_carries_his_words():
    events = [_msg(T0 + i * 1000, f"message {i}", i) for i in range(20)]
    placements = [Placement(event_idx=i, board_task=tid("parserxxxxxxx1") if i % 2 == 0 else None, candidates=3)
                  for i in range(20)]
    rows = sample_for_review(placements, events, {tid("parserxxxxxxx1"): "Parse"}, n=5)
    assert len(rows) == 5
    assert all(r["board_task"] == tid("parserxxxxxxx1") and r["title"] == "Parse" for r in rows)
    assert all(int(r["message"].split()[-1]) % 2 == 0 for r in rows)
    assert rows == sorted(rows, key=lambda r: r["ts"])


# --- the run calls it ---------------------------------------------------------

def test_the_digest_run_classifies_unlinked_messages_and_prints_both_counts():
    """A correct, tested and never-called function has shipped here before.
    The run must call the classifier after the workspace join and before
    task ids are carried forward, and print the placed count and the share
    it answered "none" every run."""
    src = (Path(__file__).resolve().parents[1] / "src" / "weekly_review" / "cli.py").read_text()
    i_join = src.index("attach_by_workspace_task(tasks, all_events)")
    i_cls = src.index("classify_messages(\n")
    i_att = src.index("attach_placements(tasks, all_events, _placements")
    i_carry = src.index("carry_forward_task_ids(")
    assert i_join < i_cls < i_att < i_carry
    printed = src[i_att:i_carry]
    assert re.search(r"classifier: .*unlinked messages placed", printed)
    assert re.search(r"none.*:.*%", printed)
    assert "classifier: skipped" in printed  # a skipped pass says so; silence is not a value


# --- gaps a review found (2026-09-18) --------------------------------------

def test_the_status_window_edges_are_inclusive_of_done_and_exclusive_of_the_future():
    at_done = _bt(tid("doneat24hxxxx1"), "Done exactly 24h ago", (T0 - 30 * H, "in-progress"), (T0 - 24 * H, "done"))
    just_over = _bt(tid("doneat24h1xxx1"), "Done 24h and 1ms ago", (T0 - 30 * H, "in-progress"), (T0 - 24 * H - 1, "done"))
    starts_now = _bt(tid("startsnowxxxx1"), "In progress from this ms", (T0, "in-progress"))
    starts_next = _bt(tid("startsnextxxx1"), "In progress from the next ms", (T0 + 1, "in-progress"))
    got = {bt.id for bt, _ in candidates_at([at_done, just_over, starts_now, starts_next], T0)}
    assert got == {tid("doneat24hxxxx1"), tid("startsnowxxxx1")}


def test_a_batch_is_at_most_25_messages_and_the_second_batch_is_numbered_from_one():
    events = [_msg(T0 + i * 1000, f"msg{i}", i) for i in range(30)]
    llm = FakeLLM(json.dumps([{"m": 1, "task": tid("parserxxxxxxx1"), "status_request": False}]))
    placements, stats = classify_messages(events, list(range(30)), _two_tasks(), llm=llm, model="m")
    assert stats["calls"] == 2
    assert "25. [" in llm.prompts[0] and "26. [" not in llm.prompts[0]
    assert "1. [" in llm.prompts[1] and "msg25" in llm.prompts[1] and "6. [" not in llm.prompts[1]
    # "m": 1 in each batch is that batch's first message
    assert [p.event_idx for p in placements if p.board_task] == [0, 25]


def test_a_message_number_answered_as_a_string_still_counts():
    events = [_msg(T0, "ledger", 1)]
    llm = FakeLLM(json.dumps([{"m": "1", "task": tid("parserxxxxxxx1"), "status_request": False}]))
    placements, stats = classify_messages(events, [0], _two_tasks(), llm=llm, model="m")
    assert placements[0].board_task == tid("parserxxxxxxx1") and stats["unparsed"] == 0


def test_a_placed_message_is_not_also_a_status_request():
    events = [_msg(T0, "ledger status?", 1)]
    llm = FakeLLM(json.dumps([{"m": 1, "task": tid("parserxxxxxxx1"), "status_request": True}]))
    placements, stats = classify_messages(events, [0], _two_tasks(), llm=llm, model="m")
    assert placements[0].board_task == tid("parserxxxxxxx1") and not placements[0].status_request
    assert stats["status_request"] == 0


def test_an_agents_user_role_record_is_not_his_message():
    events = [_msg(T0, "his", 1),
              Event(ts="2026-09-08T00:00:03Z", actor="agent", project="weekly-review", session_id="s1",
                    source_system="claude_transcript", source_machine="m", kind="user_message", seq=3,
                    content_snippet="a dispatch prompt")]
    assert his_unlinked_messages([], events) == [0]


def test_a_comment_on_another_boards_doc_does_not_hide_the_projects_own_board_from_terminal_messages():
    """A batch is one project and day; a comment on a doc that sits on a
    board abstaining from the project must not narrow what the terminal
    messages in the same batch may see."""
    events = [_msg(T0, "on the methodology doc", 1, metadata={"doc_id": "d-tl"}),
              _msg(T0 + 1000, "terminal: the parser drops turns", 2, source="claude_transcript")]
    tasks = [_bt(tid("p1boardxxxxxx1"), "Weekly-review work", (T0 - H, "in-progress"), board="Weekly Review"),
             _bt(tid("tlboardxxxxxx1"), "the lead agent work", (T0 - H, "in-progress"), board="Lead Agent"),
             _bt(tid("elsewherexxxx1"), "Other board", (T0 - H, "in-progress"), board="Bike Map")]
    llm = FakeLLM("[]")
    classify_messages(events, [0, 1], tasks, llm=llm, model="m",
                      board_of_event=lambda e: {"d-tl": "Lead Agent"}.get((e.metadata or {}).get("doc_id", "")),
                      boards_of_project={"weekly-review": {"Weekly Review"}})
    prompt = llm.prompts[0]
    assert tid("p1boardxxxxxx1") in prompt and tid("tlboardxxxxxx1") in prompt
    assert tid("elsewherexxxx1") not in prompt


def test_a_message_whose_board_is_unknown_opens_the_batch_to_every_board():
    events = [_msg(T0, "somewhere", 1, project="unrouted")]
    tasks = [_bt(tid("anyboardxxxxx1"), "Anything", (T0 - H, "in-progress"), board="Bike Map")]
    llm = FakeLLM("[]")
    classify_messages(events, [0], tasks, llm=llm, model="m",
                      board_of_event=lambda e: None, boards_of_project={"weekly-review": {"Weekly Review"}})
    assert tid("anyboardxxxxx1") in llm.prompts[0]


def test_a_coordination_unit_pulled_onto_the_day_before_still_hosts_its_own_days_status_request():
    """A PR join can move a coordination unit's first_event_ts to the previous
    day; the unit is still the day segmentation named it."""
    from datetime import timedelta
    from weekly_review.segmentation import _local_day
    events = [_msg(T0, "where are we?", 1)]
    day = _local_day(_parse_ts(events[0].ts))
    coord = Task(task_id="c", project="weekly-review", title=f"coordination {day}")
    coord.unit_kind = "coordination"
    coord.touch(3, _parse_ts(events[0].ts), "coordination_unit", day)
    coord.first_event_ts = _parse_ts(events[0].ts) - timedelta(days=1)
    tasks = [coord]
    stats = attach_placements(tasks, events, [Placement(event_idx=0, status_request=True)])
    assert len(tasks) == 1 and coord.event_ids == [3, 0] and stats["new_coordination"] == 0
