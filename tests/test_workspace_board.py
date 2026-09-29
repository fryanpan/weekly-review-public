"""The claude-workspaces board action log is a first-class source.

It was never read. That is not a bug in how events were parsed — it is a whole
store the pipeline never opened, which is why it leads the 2026-09-08 plan:
no dedup or routing fix recovers an event nobody loaded. These tests pin the
three things the task is done when: the events arrive carrying their `taskId`,
person and agent are separated, and a week's `decision.answered` count matches
what is in the store.
"""
import json

from weekly_review.config import ProjectSpec, ProjectsConfig
from weekly_review.events import dedupe_by_event_id
from weekly_review.extractors.workspace_board import (
    BOARD_EVENT_PREFIXES,
    extract_board_events,
)
from tests.fakeids import tid

PROJECTS = ProjectsConfig(projects=[
    ProjectSpec(slug="weekly-review", cwd_patterns=["/dev/weekly-review/**"]),
    ProjectSpec(slug="family-bike-map", cwd_patterns=["/dev/family-bike-map/**"]),
])

PERSON = {"id": "user-1h6o7qo0ih5ggd", "name": "Arivera", "kind": "person"}
AGENT = {"id": "agent-weekly-review", "name": "Weekly Review", "kind": "agent"}


def _board(tmp_path, ws_id, name, records, docIds=None):
    """Write one board's tasks.json + events.jsonl, as the plugin lays them out."""
    ws = tmp_path / "workspaces"
    ws.mkdir(exist_ok=True)
    (ws / f"{ws_id}.tasks.json").write_text(json.dumps({
        "workspace": {"id": ws_id, "name": name, "docIds": docIds or [], "goals": []},
        "tasks": [],
    }))
    lines = []
    for r in records:
        r.setdefault("workspaceId", ws_id)
        lines.append(json.dumps(r))
    (ws / f"{ws_id}.events.jsonl").write_text("\n".join(lines) + "\n")


def test_board_events_arrive_carrying_their_task_id(tmp_path):
    _board(tmp_path, "w-aaa", "Weekly Review", [
        {"event": "decision.answered", "taskId": "t-111", "answer": "Board-first, dir fallback",
         "headline": "How should a doc event resolve to a task?",
         "actor": PERSON, "ts": 1788918034467},
        {"event": "task.transitioned", "taskId": "t-222", "from": "todo", "to": "in-progress",
         "note": "starting", "actor": AGENT, "ts": 1788918094467},
    ])

    evs = list(extract_board_events(PROJECTS, data_dir=tmp_path))

    assert [e.metadata["task_id"] for e in evs] == ["t-111", "t-222"]
    assert {e.metadata["task_id"] for e in evs} and all(e.metadata["task_id"] for e in evs)
    assert [e.metadata["board_event"] for e in evs] == ["decision.answered", "task.transitioned"]
    assert [e.source_system for e in evs] == ["workspace_board"] * 2
    # The record's own payload survives as the snippet, so a reader of the
    # event stream can tell which decision was answered without the board.
    assert evs[0].content_snippet == "Board-first, dir fallback"


def test_a_dictation_keeps_its_length_and_not_just_its_snippet(tmp_path):
    """The snippet stops at 500 characters; the speaking window needs every word."""
    _board(tmp_path, "w-aaa", "Weekly Review", [
        {"event": "voice.request", "actor": PERSON, "ts": 1_788_918_000_000,
         "transcript": "word " * 200},
    ])
    [ev] = list(extract_board_events(PROJECTS, data_dir=tmp_path))
    assert ev.metadata["words"] == 200
    assert len(ev.content_snippet.split()) < 200


def test_person_and_agent_are_separated(tmp_path):
    _board(tmp_path, "w-aaa", "Weekly Review", [
        {"event": "decision.answered", "taskId": "t-1", "actor": PERSON, "ts": 1_788_918_000_000},
        {"event": "task.created", "taskId": "t-2", "actor": AGENT, "ts": 1_788_918_060_000},
        {"event": "task.archived", "taskId": "t-3", "actor": PERSON, "ts": 1_788_918_120_000},
    ])

    evs = list(extract_board_events(PROJECTS, data_dir=tmp_path))

    assert [e.actor for e in evs] == ["user", "agent", "user"]
    assert [e.author_kind for e in evs] == ["human", "agent", "human"]
    assert [e.author_id for e in evs] == [PERSON["id"], AGENT["id"], PERSON["id"]]


def test_an_unclassifiable_actor_is_dropped_rather_than_called_human(tmp_path):
    """Fail closed. An actor kind we don't recognise must not inflate his hours.

    The live-feedback extractor learned this the expensive way: a denylist on
    the literal name "Agent" let every agent posting under its project name
    through as human review time -- 53k of 61k words in one week.
    """
    _board(tmp_path, "w-aaa", "Weekly Review", [
        {"event": "decision.answered", "taskId": "t-1", "actor": PERSON, "ts": 1_788_918_000_000},
        {"event": "decision.answered", "taskId": "t-2", "actor": {"id": "x", "kind": "webhook"},
         "ts": 1_788_918_060_000},
        {"event": "decision.answered", "taskId": "t-3", "actor": "Arivera", "ts": 1_788_918_120_000},
        {"event": "decision.answered", "taskId": "t-4", "ts": 1_788_918_180_000},
    ])

    evs = list(extract_board_events(PROJECTS, data_dir=tmp_path))

    assert [e.metadata["task_id"] for e in evs] == ["t-1"]


def test_only_the_acting_families_are_read(tmp_path):
    """`server.tick` is 74,542 of the 97,398 records on disk. It is not work.

    `voice.` joined `task.` and `decision.` on 2026-09-12 — the one entry the
    constant's own comment promised. Everything else in the file is still server
    bookkeeping, agent liveness or review-item plumbing, and the list stays
    narrow on purpose.
    """
    _board(tmp_path, "w-aaa", "Weekly Review", [
        {"event": "server.tick", "actor": AGENT, "ts": 1_788_918_000_000},
        {"event": "agent.heartbeat", "actor": AGENT, "ts": 1_788_918_010_000},
        {"event": "review_item.added", "taskId": "t-9", "actor": AGENT, "ts": 1_788_918_020_000},
        {"event": "task.noted", "taskId": "t-1", "actor": AGENT, "ts": 1_788_918_030_000},
        {"event": "decision.info_requested", "taskId": "t-2", "actor": PERSON, "ts": 1_788_918_040_000},
        {"event": "voice.request", "taskId": "t-3", "transcript": "note that down",
         "actor": PERSON, "ts": 1_788_918_050_000},
    ])

    evs = list(extract_board_events(PROJECTS, data_dir=tmp_path))

    assert [e.metadata["board_event"] for e in evs] == [
        "task.noted", "decision.info_requested", "voice.request"]
    assert BOARD_EVENT_PREFIXES == ("task.", "decision.", "voice.")


def test_every_action_on_one_task_survives_dedup(tmp_path):
    """The defect that eats the doc stream must not be reproduced here.

    Live-feedback ids are `live_feedback:live-feedback:<docId>:0` -- the doc is
    the identity and the sequence is always 0, so every revisit collides with
    the first and dedup discards it (82% of his sessions). The board log is
    append-only, so the record's line index is a stable, distinct sequence.
    """
    _board(tmp_path, "w-aaa", "Weekly Review", [
        {"event": "task.transitioned", "taskId": "t-1", "from": "todo", "to": "in-progress",
         "actor": PERSON, "ts": 1_788_918_000_000 + i * 60_000}
        for i in range(8)
    ])

    evs = list(extract_board_events(PROJECTS, data_dir=tmp_path))

    assert len(evs) == 8
    assert len({e.event_id for e in evs}) == 8
    assert len(dedupe_by_event_id(evs)) == 8
    # Ingesting the same store twice (archive mirror, re-run) still collapses.
    assert len(dedupe_by_event_id(evs + list(extract_board_events(PROJECTS, data_dir=tmp_path)))) == 8


def test_a_board_named_after_a_project_routes_without_config(tmp_path):
    _board(tmp_path, "w-aaa", "weekly-review", [
        {"event": "decision.answered", "taskId": "t-1", "actor": PERSON, "ts": 1_788_918_000_000},
    ])
    _board(tmp_path, "w-bbb", "Family Bike Map", [
        {"event": "decision.answered", "taskId": "t-2", "actor": PERSON, "ts": 1_788_918_060_000},
    ])
    _board(tmp_path, "w-ccc", "Something Nobody Registered", [
        {"event": "decision.answered", "taskId": "t-3", "actor": PERSON, "ts": 1_788_918_120_000},
    ])

    by_task = {e.metadata["task_id"]: e.project
               for e in extract_board_events(PROJECTS, data_dir=tmp_path)}

    assert by_task["t-1"] == "weekly-review"
    assert by_task["t-2"] == "family-bike-map"   # name normalised to slug shape
    assert by_task["t-3"] == "unknown"           # reported, never guessed


def test_one_board_never_splits_across_two_projects(tmp_path):
    """The board is the whole routing signal; a task title must not override it.

    Measured on the real store before this was pinned: 2 of 27 person events on
    the "Week of Aug 10 — Lead Agent" board went to writing-tool because
    their titles said "blog", and 1 of 90 on "Lead Agent" went to client-a.
    A board belongs to one project or to none. Per-event drift inside one board
    is a guess wearing a measurement's clothes.
    """
    _board(tmp_path, "w-aaa", "Week of Aug 10 — Lead Agent", [
        {"event": "task.created", "taskId": "t-1", "actor": PERSON, "ts": 1_788_918_000_000,
         "task": {"id": "t-1", "title": "Draft the blog post on substack migration"}},
        {"event": "task.created", "taskId": "t-2", "actor": PERSON, "ts": 1_788_918_060_000,
         "title": "Chase the CA client-a-app pmtiles regression"},
        {"event": "task.archived", "taskId": "t-3", "actor": PERSON, "ts": 1_788_918_120_000,
         "title": "Bike route rendering"},
    ])

    projects = {e.project for e in extract_board_events(PROJECTS, data_dir=tmp_path)}

    assert len(projects) == 1, f"one board routed to {projects}"


def test_an_answer_names_the_review_item_it_answers(tmp_path):
    _board(tmp_path, "w-aaa", "weekly-review", [
        {"event": "decision.answered", "taskId": "t-1", "reviewItemId": "r-9", "actor": PERSON,
         "ts": 1_788_918_000_000},
        {"event": "decision.answered", "taskId": "t-1", "actor": PERSON, "ts": 1_788_918_060_000},
    ])
    evs = list(extract_board_events(PROJECTS, data_dir=tmp_path))
    assert [e.metadata.get("review_item_id") for e in evs] == ["r-9", None]


def test_board_actions_add_no_hands_on_hours_yet(tmp_path):
    """Ingest first, attribute second -- so this change can only ADD events.

    Turning an instant into an interval is the 60-second contiguous-block rule,
    which is a separate task that depends on this one. Until it lands a board
    action must contribute zero time, or the week's number moves for a reason
    nobody reviewed.
    """
    _board(tmp_path, "w-aaa", "weekly-review", [
        {"event": "decision.answered", "taskId": "t-1", "actor": PERSON,
         "ts": 1_788_918_000_000 + i * 60_000}
        for i in range(20)
    ])

    evs = list(extract_board_events(PROJECTS, data_dir=tmp_path))

    assert all("fixed_active_sec" not in e.metadata for e in evs)
    assert all(e.kind == "board_action" for e in evs)


def test_the_pipeline_actually_calls_the_extractor():
    """Assert the CALL SITE, not the behaviour.

    `presence_hours_by_task` shipped correct, tested and documented as THE
    hands-on metric, and `cli.py` went on calling the old function for two days
    while every published number came from the model the user had rejected.
    Coverage measures whether a function works, never whether anything reaches
    it — and a source that is never read is the exact defect this task exists
    to close, so shipping it unreachable would be the same bug twice.
    """
    import inspect
    from weekly_review import cli

    src = inspect.getsource(cli)
    assert "extract_board_events(" in src, "cli.py must call the board-log extractor"
    assert "no_board_log" in src, "the board log needs an off switch, like every other source"


def test_no_board_directory_is_empty_not_an_error(tmp_path):
    assert list(extract_board_events(PROJECTS, data_dir=tmp_path)) == []


def test_a_corrupt_line_does_not_take_the_file_down(tmp_path):
    ws = tmp_path / "workspaces"
    ws.mkdir()
    (ws / "w-aaa.tasks.json").write_text(json.dumps(
        {"workspace": {"id": "w-aaa", "name": "weekly-review"}, "tasks": []}))
    (ws / "w-aaa.events.jsonl").write_text(
        json.dumps({"event": "decision.answered", "taskId": "t-1", "workspaceId": "w-aaa",
                    "actor": PERSON, "ts": 1_788_918_000_000}) + "\n"
        + "{not json at all\n"
        + "\n"
        + json.dumps({"event": "task.archived", "taskId": "t-2", "workspaceId": "w-aaa",
                      "actor": PERSON, "ts": 1_788_918_060_000}) + "\n"
    )

    evs = list(extract_board_events(PROJECTS, data_dir=tmp_path))

    assert [e.metadata["task_id"] for e in evs] == ["t-1", "t-2"]


def _ev(ts, kind, actor="agent", session="workspace-board:w-aaa", lane=None):
    from weekly_review.events import Event
    md = {"agent_lane": lane} if lane else {}
    return Event(ts=ts, actor=actor, project="weekly-review", session_id=session,
                 source_system="workspace_board" if kind == "board_action" else "claude_transcript",
                 source_machine="mac_mini", kind=kind, seq=0, metadata=md)


def test_board_actions_add_no_agent_hours_either():
    """The instant must not become an interval on the AGENT side either.

    `_agent_intervals_for_task` filtered on actor alone with no kind filter, so
    3,155 agent board actions on a real week stitched themselves into agent
    bursts and, carrying no `agent_lane`, opened a brand-new labor lane per
    board (+30.3h, 6.5%, on 2026-08-31..09-06). Hands-on was provably
    untouched, but agent hours would have moved for a reason nobody decided --
    the same unmeasured-change-to-a-published-number failure that the presence
    swap taught.
    """
    from weekly_review.effort import (
        agent_active_hours_by_task,
        agent_lane_hours_by_task,
        labor_and_agent_hours_by_task,
    )
    from weekly_review.segmentation import Task

    # A minute of real transcript work, then board actions spread over 3h.
    events = [
        _ev("2026-09-01T10:00:00Z", "agent_message", lane="main"),
        _ev("2026-09-01T10:01:00Z", "agent_message", lane="main"),
    ]
    work_only = Task(task_id="t-1", project="weekly-review", title="t",
                     event_ids=[0, 1])

    events_with_board = list(events) + [
        _ev("2026-09-01T10:30:00Z", "board_action"),
        _ev("2026-09-01T11:30:00Z", "board_action"),
        _ev("2026-09-01T13:00:00Z", "board_action"),
    ]
    with_board = Task(task_id="t-1", project="weekly-review", title="t",
                      event_ids=[0, 1, 2, 3, 4])

    _, agent_before, _ = labor_and_agent_hours_by_task([work_only], events)
    _, agent_after, _ = labor_and_agent_hours_by_task(
        [with_board], events_with_board)
    assert agent_after["t-1"] == agent_before["t-1"]

    assert (agent_active_hours_by_task([with_board], events_with_board)["t-1"]
            == agent_active_hours_by_task([work_only], events)["t-1"])

    # The lane check is the one that moved by 30h on the real week: board
    # events carry no agent_lane, so each board session opened its own "main".
    assert (agent_lane_hours_by_task([with_board], events_with_board)["t-1"]
            == agent_lane_hours_by_task([work_only], events)["t-1"])


def test_an_agents_tool_call_on_a_board_card_adds_no_agent_hours():
    """The lead's status posts on a card, 20 minutes apart, followed the card to
    its task and stitched 40 minutes of bursts there: +71h on one week."""
    from dataclasses import replace
    from weekly_review.effort import (
        agent_lane_hours_by_task,
        labor_and_agent_hours_by_task,
    )
    from weekly_review.segmentation import Task

    def card_call(ts):
        e = _ev(ts, "tool_call", session="lead", lane="main")
        return replace(e, metadata={**e.metadata, "card": "t-AAA", "tool_name": "post_status"})

    events = [card_call("2026-09-01T10:00:00Z"), card_call("2026-09-01T10:20:00Z"),
              card_call("2026-09-01T10:40:00Z")]
    task = Task(task_id="t-1", project="weekly-review", title="t", event_ids=[0, 1, 2])
    wall, agent, _ = labor_and_agent_hours_by_task([task], events)
    assert agent["t-1"] == 0 and wall["t-1"] == 0
    assert agent_lane_hours_by_task([task], events)["t-1"] == 0

    # The control: the same calls without the card marker still create time.
    # Ten minutes apart, not twenty — since 2026-09-15 a quiet gap bridges 15
    # minutes, so a 20-minute spacing would read as zero for that reason and the
    # control would pass on the wrong mechanism.
    plain = [replace(card_call(ts), metadata={"agent_lane": "main"})
             for ts in ("2026-09-01T10:00:00Z", "2026-09-01T10:10:00Z",
                        "2026-09-01T10:20:00Z")]
    assert labor_and_agent_hours_by_task([task], plain)[1]["t-1"] > 0, "other tool calls still count"


# --- voice requests (board task and review doc, 2026-09-12) ------------------------

VOICE = {
    "event": "voice.request",
    "taskId": "t-777",
    "transcript": "file a task to re-run the whole history",
    "route": "task",
    "ack": "spoken",
    "queueId": "q-1",
    "context": {"surface": "board"},
    "actor": PERSON,
    "ts": 1788918034467,
}


def test_a_voice_request_is_ingested_and_counted_as_his(tmp_path):
    """The task looked for these in `activity.jsonl`. They are in the board log.

    88 of them, 2026-08-18 to 2026-09-11, in a store the pipeline already reads
    — dropped only because `BOARD_EVENT_PREFIXES` did not list `voice.`. A
    dictated request is the densest possible evidence that he was at the desk
    and not at the keyboard, which is the whole point of the task that asked
    for it.
    """
    _board(tmp_path, "w-aaa", "Weekly Review", [dict(VOICE)])

    ev, = list(extract_board_events(PROJECTS, data_dir=tmp_path))

    assert ev.metadata["board_event"] == "voice.request"
    assert ev.metadata["task_id"] == "t-777"
    assert ev.actor == "user" and ev.author_kind == "human"
    # The spoken text is kept because it is HIS, dictated to his own board —
    # the same act as typing to it, and his typed messages have always been
    # kept. Meeting turns are other people talking, and keep word counts only.
    assert ev.content_snippet == "file a task to re-run the whole history"


def test_a_voice_request_is_an_instant_and_buys_no_hours_by_itself(tmp_path):
    """Every board record is a stamped moment. Widening the prefix list must not
    quietly turn one into a duration."""
    from weekly_review.effort import _workspace_span, workspace_attribution_key

    _board(tmp_path, "w-aaa", "Weekly Review", [dict(VOICE)])
    ev, = list(extract_board_events(PROJECTS, data_dir=tmp_path))

    start, end = _workspace_span(ev)
    assert start == end
    # It still bridges, which is what it is worth: it shares the task key with
    # every other action on t-777.
    assert workspace_attribution_key(ev) == "t-777"


def test_a_voice_request_takes_its_task_from_context_when_it_has_no_top_level_one(tmp_path):
    """The store's own id, in the place this store happens to put it.

    A `voice.request` carries no top-level `taskId` — not one of the 88 in the
    store does. 7 were dictated from a task page and name it under `context`
    instead. Reading only the top level drops all 7 silently, which is the same
    defect as `(session, seq)` and as the doc-keyed live-feedback ids: an id the
    store already had, unread.
    """
    _board(tmp_path, "w-aaa", "Weekly Review", [
        dict(VOICE, taskId=None, context={"surface": "task", "taskId": "t-ctx"}),
    ])

    ev, = list(extract_board_events(PROJECTS, data_dir=tmp_path))

    assert ev.metadata["task_id"] == "t-ctx"
    assert ev.metadata["surface"] == "task"


def test_a_keyless_voice_request_falls_back_to_its_board_rather_than_vanishing(tmp_path):
    """An empty key is not "blocks alone" — the block rule cannot see it at all.

    81 of the 88 were dictated from the hub or the board and name no task. Left
    keyless they contribute nothing and bridge nothing, and the +0.00h they
    produce is indistinguishable from never having been ingested. Measured
    before shipping: the board fallback is worth +0.08h over all history.
    """
    from weekly_review.effort import workspace_attribution_key, workspace_blocks

    _board(tmp_path, "w-aaa", "Weekly Review", [
        dict(VOICE, taskId=None, context={"surface": "hub"}, ts=1788918034467),
        dict(VOICE, taskId=None, context={"surface": "hub"}, ts=1788918064467),
    ])

    evs = list(extract_board_events(PROJECTS, data_dir=tmp_path))

    assert [workspace_attribution_key(e) for e in evs] == ["board:w-aaa", "board:w-aaa"]
    # Two requests 30s apart on one board are one block, not two invisible ones.
    (start, end, members), = workspace_blocks(evs)
    assert (end - start).total_seconds() == 30.0
    assert members == [0, 1]


def test_the_board_fallback_never_outranks_a_task_or_a_doc(tmp_path):
    """It is the last resort, not a new default.

    If it preceded the task id, every action on one board would bridge every
    other, which is precisely the padding the contiguous-block rule exists to
    remove.
    """
    from weekly_review.effort import workspace_attribution_key
    from weekly_review.events import Event

    _board(tmp_path, "w-aaa", "Weekly Review", [dict(VOICE)])
    ev, = list(extract_board_events(PROJECTS, data_dir=tmp_path))
    assert workspace_attribution_key(ev) == "t-777"

    doc_ev = Event(ts=ev.ts, actor="user", project="weekly-review", session_id="s",
                   source_system="live_feedback", source_machine="m",
                   kind="review_session", seq=0,
                   metadata={"doc_id": "d-1", "workspace_id": "w-aaa"})
    assert workspace_attribution_key(doc_ev) == "doc:d-1"


def test_a_voice_request_from_an_agent_is_not_his_hands_on(tmp_path):
    """The board is the only store that records who acted, so use it.

    An agent can route a request through the same queue. It arrives as an agent
    event, not a person one, and the same separation that keeps agent board
    actions out of his hands-on keeps this out too.
    """
    _board(tmp_path, "w-aaa", "Weekly Review", [dict(VOICE, actor=AGENT)])

    ev, = list(extract_board_events(PROJECTS, data_dir=tmp_path))

    assert ev.actor == "agent" and ev.author_kind == "agent"


def test_board_task_facts_record_creator_and_his_requests_up_to_the_window_end(tmp_path):
    """The split check's exemption (the user, 2026-09-14) needs who created each
    board task, often before the window, and whether he ever asked for a look."""
    from weekly_review.extractors.workspace_board import board_task_facts
    _board(tmp_path, "w-aaa", "Weekly Review", [
        {"event": "task.created", "taskId": "t-agent", "actor": AGENT, "ts": 1000},
        {"event": "task.archived", "taskId": "t-agent", "actor": PERSON, "ts": 2000},
        {"event": "decision.answered", "taskId": "t-agent", "actor": PERSON, "ts": 9000},
        {"event": "task.created", "taskId": "t-his", "actor": PERSON, "ts": 1000},
        {"event": "task.created", "taskId": "t-rev", "actor": AGENT, "ts": 1000},
        {"event": "decision.info_requested", "taskId": "t-rev", "actor": PERSON, "ts": 3000},
        {"event": "decision.answered", "taskId": "t-rev", "actor": PERSON, "ts": 4000},
        {"event": "decision.answered", "taskId": "t-rev", "actor": PERSON, "ts": 3500},
        {"event": "decision.created", "taskId": "t-bot", "actor": AGENT, "ts": 3000},
        {"event": "voice.request", "context": {"surface": "task", "taskId": "t-voice"}, "actor": PERSON, "ts": 3000},
    ])
    f = board_task_facts(tmp_path, until_ms=5000)
    assert f["t-agent"] == {"creator_kind": "agent", "his_review": False, "his_dictation": False,
                            "his_last_ask": None}, \
        "housekeeping is not a request, and an answer after the window does not count"
    assert f["t-his"]["creator_kind"] == "person"
    assert f["t-rev"]["his_review"] is True
    assert f["t-bot"]["his_review"] is False, "an agent's review item is not his"
    assert f["t-voice"]["his_dictation"] is True
    assert board_task_facts(tmp_path)["t-agent"]["his_review"] is True
    assert (f["t-his"]["his_last_ask"], f["t-rev"]["his_last_ask"], f["t-voice"]["his_last_ask"],
            f["t-bot"]["his_last_ask"]) == (1000, 4000, 3000, None), "filing, answering and dictating are his asks"
    assert board_task_facts(tmp_path)["t-agent"]["his_last_ask"] == 9000


def test_his_last_ask_is_stored_as_a_time_the_check_can_compare():
    import sqlite3
    from weekly_review import persist
    c = sqlite3.connect(":memory:")
    persist.init_schema(c)
    persist.write_board_tasks(c, {"t-1": {"creator_kind": "agent", "his_review": True, "his_last_ask": 1_788_918_000_000},
                                  "t-2": {"creator_kind": "agent"}})
    assert dict(c.execute("SELECT board_task, his_last_ask FROM board_tasks")) == {
        "t-1": "2026-09-09T01:40:00+00:00", "t-2": None}


def test_a_board_tasks_goal_reaches_the_run_db_and_an_old_db_gains_the_column(tmp_path):
    """The goal is the summary item (the user, 2026-09-17). It comes from the
    task store, so a task the log never mentions still carries one."""
    import sqlite3
    from weekly_review import persist
    from weekly_review.extractors.workspace_board import board_task_facts
    _board(tmp_path, "w-aaa", "Weekly Review", [
        {"event": "task.created", "taskId": "t-1", "actor": AGENT, "ts": 1000},
    ])
    store = tmp_path / "workspaces" / "w-aaa.tasks.json"
    blob = json.loads(store.read_text())
    blob["tasks"] = [{"id": "t-1", "goal": "g-1", "links": []},
                     {"id": "t-quiet", "goal": "g-2", "links": []}]
    store.write_text(json.dumps(blob))

    f = board_task_facts(tmp_path)
    assert f["t-1"]["goal"] == "g-1" and f["t-1"]["creator_kind"] == "agent"
    assert f["t-quiet"]["goal"] == "g-2"

    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE board_tasks (board_task TEXT PRIMARY KEY, creator_kind TEXT, "
              "his_review INTEGER NOT NULL DEFAULT 0, his_dictation INTEGER NOT NULL DEFAULT 0, his_last_ask TEXT)")
    persist.init_schema(c)
    persist._migrate(c)
    persist.write_board_tasks(c, f)
    assert dict(c.execute("SELECT board_task, goal FROM board_tasks")) == {"t-1": "g-1", "t-quiet": "g-2"}


def test_a_task_he_files_keeps_the_length_of_what_he_typed(tmp_path):
    _board(tmp_path, "w-aaa", "Weekly Review", [
        {"event": "task.created", "taskId": "t-1", "actor": PERSON, "ts": 1_788_918_000_000,
         "task": {"title": "fix the export", "body": "it drops a week"}},
        {"event": "task.created", "taskId": "t-2", "actor": PERSON, "ts": 1_788_918_000_000,
         "task": {"title": "fix the export"}},
    ])
    evs = list(extract_board_events(PROJECTS, data_dir=tmp_path))
    assert [e.metadata["words"] for e in evs] == [7, 3]


def test_the_digest_run_writes_board_task_facts_into_its_run_db():
    """A correct function nothing calls is a defect this repo has shipped; the
    split check silently exempts nothing if the table stays empty."""
    from pathlib import Path
    cli = (Path(__file__).parents[1] / "src/weekly_review/cli.py").read_text()
    persist_block = cli.index("with persist.connect(db_path) as conn:\n        persist.write_events")
    assert cli.index("persist.write_board_tasks(conn, ") > persist_block
    assert "board_task_facts(until_ms=until_ms)" in cli


def test_a_deleted_boards_log_and_name_are_read_from_the_archive_once(tmp_path):
    """Two boards deleted on 2026-08-19 held the week of Aug 17's review answers
    and the creators of the board tasks its agents worked."""
    import shutil
    from weekly_review.extractors.live_feedback import load_doc_boards, load_workspace_names
    from weekly_review.extractors.workspace_board import board_task_facts
    _board(tmp_path, "w-live", "Live Board", [
        {"event": "task.created", "taskId": "t-live", "actor": AGENT, "ts": 1000}])
    _board(tmp_path, "w-gone", "Gone Board", [
        {"event": "task.created", "taskId": "t-gone", "actor": AGENT, "ts": 1000},
        {"event": "decision.answered", "taskId": "t-gone", "actor": PERSON, "ts": 2000}])
    _board(tmp_path, "w-both", "Restored Board", [
        {"event": "task.created", "taskId": "t-both", "actor": PERSON, "ts": 1000}])
    blob = json.loads((tmp_path / "workspaces" / "w-gone.tasks.json").read_text())
    blob["tasks"] = [{"id": "t-gone"}]
    (tmp_path / "workspaces" / "w-gone.tasks.json").write_text(json.dumps(blob))
    arch = tmp_path / "_archive" / "deleted-boards-20260819"
    arch.mkdir(parents=True)
    for suffix in (".tasks.json", ".events.jsonl"):
        shutil.move(str(tmp_path / "workspaces" / f"w-gone{suffix}"), arch / f"w-gone{suffix}")
        shutil.copy(tmp_path / "workspaces" / f"w-both{suffix}", arch / f"w-both{suffix}")
    (arch / "notes.events.jsonl").write_text(json.dumps(  # not a board's log
        {"event": "task.created", "taskId": "t-junk", "workspaceId": "w-x", "actor": AGENT, "ts": 1000}) + "\n")

    evs = list(extract_board_events(PROJECTS, data_dir=tmp_path))
    assert sorted(e.metadata["task_id"] for e in evs) == ["t-both", "t-gone", "t-gone", "t-live"]
    assert len(dedupe_by_event_id(evs)) == 4
    facts = board_task_facts(tmp_path)
    assert facts["t-gone"]["creator_kind"] == "agent" and facts["t-gone"]["his_review"] is True
    assert load_workspace_names(tmp_path)["w-gone"] == "Gone Board"
    assert load_doc_boards(tmp_path)["task:t-gone"] == "Gone Board"


def test_held_board_tasks_come_from_task_lists_logs_and_deleted_boards(tmp_path):
    import shutil
    from weekly_review.extractors.workspace_board import held_board_tasks
    _board(tmp_path, "w-live", "Live Board", [
        {"event": "task.created", "taskId": "t-logged", "actor": AGENT, "ts": 1000},
        {"event": "voice.request", "context": {"taskId": "t-dictated"}, "actor": PERSON, "ts": 2000},
        {"event": "server.tick", "ts": 3000}])
    blob = json.loads((tmp_path / "workspaces" / "w-live.tasks.json").read_text())
    blob["tasks"] = [{"id": "t-listed"}, {"title": "no id"}]
    (tmp_path / "workspaces" / "w-live.tasks.json").write_text(json.dumps(blob))
    _board(tmp_path, "w-gone", "Gone Board", [
        {"event": "task.created", "taskId": "t-deleted", "actor": AGENT, "ts": 1000}])
    blob = json.loads((tmp_path / "workspaces" / "w-gone.tasks.json").read_text())
    blob["tasks"] = [{"id": tid("deleted-listed")}]  # created before its log began
    (tmp_path / "workspaces" / "w-gone.tasks.json").write_text(json.dumps(blob))
    arch = tmp_path / "_archive" / "deleted-boards-20260819"
    arch.mkdir(parents=True)
    for suffix in (".tasks.json", ".events.jsonl"):
        shutil.move(str(tmp_path / "workspaces" / f"w-gone{suffix}"), arch / f"w-gone{suffix}")
    assert held_board_tasks(tmp_path) == {"t-logged", "t-dictated", "t-listed", "t-deleted", tid("deleted-listed")}


def test_a_card_on_a_test_servers_board_is_cleared_and_kept_for_audit():
    """Week of 2026-08-17: the live-feedback lead's builders tested the app on
    their own servers, and 5 measured tasks, 9h estimated, were cut onto the
    test boards' tasks, away from the work the user asked for."""
    from weekly_review.extractors.workspace_board import drop_unheld_cards
    real = _ev("2026-08-19T09:00:00Z", "subagent_input", lane="agent-a")
    real.metadata["card"] = tid("realRealReal")
    test = _ev("2026-08-19T09:01:00Z", "tool_call", lane="agent-a")
    test.metadata["card"] = tid("testTestTest")
    plain = _ev("2026-08-19T09:02:00Z", "tool_result", lane="agent-a")
    assert drop_unheld_cards([real, test, plain], {tid("realRealReal")}) == (1, {tid("testTestTest")})
    assert real.metadata["card"] == tid("realRealReal")
    assert "card" not in test.metadata and test.metadata["card_unheld"] == tid("testTestTest")
    assert "card" not in plain.metadata and "card_unheld" not in plain.metadata


def test_with_no_board_store_every_card_is_kept():
    from weekly_review.extractors.workspace_board import drop_unheld_cards
    ev = _ev("2026-08-19T09:00:00Z", "subagent_input", lane="agent-a")
    ev.metadata["card"] = tid("realRealReal")
    assert drop_unheld_cards([ev], set()) == (0, set())
    assert ev.metadata["card"] == tid("realRealReal")


def test_the_pipeline_clears_unheld_cards_before_it_stores_or_segments():
    import inspect
    from weekly_review import cli
    src = inspect.getsource(cli)
    call = src.find("drop_unheld_cards(all_events, held_board_tasks())")
    assert call > 0
    assert call < src.find("_persist_events(run, all_events)") < src.find("segment(all_events")


def test_a_task_he_only_answered_a_review_item_on_has_wall_clock_at_least_his_hands_on():
    """Board tasks whose only work was his reading and answering a review item
    had two minutes of hands-on and no wall clock."""
    from datetime import datetime, timezone
    from weekly_review.effort import labor_and_agent_hours_by_task
    from weekly_review.segmentation import Task

    events = [_ev("2026-09-08T05:27:02Z", "board_action", actor="user")]
    task = Task(task_id="t-1", project="weekly-review", title="t", event_ids=[0])
    at = datetime(2026, 9, 8, 5, 27, 2, tzinfo=timezone.utc)
    iv = {"t-1": ([], [(at.replace(minute=25), at)])}
    assert labor_and_agent_hours_by_task([task], events)[0]["t-1"] == 0
    wall, _, _ = labor_and_agent_hours_by_task([task], events, hands_on_intervals=iv)
    assert abs(wall["t-1"] - 120 / 3600) < 1e-9
    # Transcript and workspace intervals that overlap are unioned, not added.
    both = {"t-1": ([(at.replace(minute=25), at)], [(at.replace(minute=26), at)])}
    wall, _, _ = labor_and_agent_hours_by_task([task], events, hands_on_intervals=both)
    assert abs(wall["t-1"] - 120 / 3600) < 1e-9


def test_the_run_puts_his_hands_on_intervals_into_the_wall_clock():
    import inspect
    from weekly_review import cli
    src = inspect.getsource(cli)
    call = src.find("tasks, all_events, hands_on_intervals=_hands_on_iv)")
    assert src.find("_hands_on_iv = hands_on_intervals_by_task(") < call
    assert src.rfind("labor_and_agent_hours_by_task(", 0, call) > src.find("_hands_on_iv = hands_on_intervals_by_task(")


def test_board_task_pr_links_map_each_pr_to_every_task_whose_record_names_it(tmp_path):
    """Method Step 5: a unit holding a PR that exactly one board task names
    links to it; the map keeps every naming task so the caller can abstain."""
    from weekly_review.extractors.workspace_board import board_task_pr_links
    _board(tmp_path, "w-aaa", "Weekly Review", [])
    store = tmp_path / "workspaces" / "w-aaa.tasks.json"
    blob = json.loads(store.read_text())
    blob["tasks"] = [
        {"id": "t-1", "goal": "g-1", "links": [{"kind": "url", "url": "https://github.com/FryanPan/weekly-review/pull/114"}]},
        {"id": "t-2", "goal": "g-1", "body": "rolled up in https://github.com/fryanpan/weekly-review/pull/114 and /pull/115",
         "notes": [{"text": "merged https://github.com/fryanpan/weekly-review/pull/115"}]},
        {"id": "t-3", "goal": "g-2", "links": []},
    ]
    store.write_text(json.dumps(blob))
    links = board_task_pr_links(tmp_path)
    assert links[("fryanpan/weekly-review", "114")] == {"t-1", "t-2"}, "repo case-folded; both namers kept"
    assert links[("fryanpan/weekly-review", "115")] == {"t-2"}
    assert len(links) == 2
