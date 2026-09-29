from pathlib import Path
from weekly_review.config import ProjectSpec, ProjectsConfig
from weekly_review.extractors.claude_transcript import extract_from_jsonl
from tests.fakeids import tid, wid


PROJECTS = ProjectsConfig(projects=[
    ProjectSpec(slug="weekly-review", cwd_patterns=[
        "/Users/*/dev/weekly-review",
        "/Users/*/dev/weekly-review/**",
    ]),
])


def test_extract_skips_system_reminders():
    fixture = Path(__file__).parent / "fixtures" / "sample_transcript.jsonl"
    events = list(extract_from_jsonl(fixture, projects=PROJECTS, source_machine="mac_mini"))
    user_events = [e for e in events if e.kind == "user_message"]
    assert len(user_events) == 2
    assert "system-reminder" not in user_events[0].content_snippet
    assert all(e.project == "weekly-review" for e in events)


def test_extract_produces_tool_call_events():
    fixture = Path(__file__).parent / "fixtures" / "sample_transcript.jsonl"
    events = list(extract_from_jsonl(fixture, projects=PROJECTS, source_machine="mac_mini"))
    tool_calls = [e for e in events if e.kind == "tool_call"]
    assert len(tool_calls) == 1
    assert tool_calls[0].metadata["tool_name"] == "Write"


def test_extract_preserves_session_id_and_seq():
    fixture = Path(__file__).parent / "fixtures" / "sample_transcript.jsonl"
    events = list(extract_from_jsonl(fixture, projects=PROJECTS, source_machine="mac_mini"))
    assert all(e.session_id == "sess-a" for e in events)
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)


def test_extract_word_counts_in_metadata():
    fixture = Path(__file__).parent / "fixtures" / "sample_transcript.jsonl"
    events = list(extract_from_jsonl(fixture, projects=PROJECTS, source_machine="mac_mini"))
    for e in events:
        if e.kind in ("user_message", "agent_message"):
            assert "words" in e.metadata
            assert isinstance(e.metadata["words"], int)


def test_tool_result_records_emit_as_agent_not_user():
    """Regression: harness tool-results are recorded as role=user in Claude's
    transcript JSONL but are NOT the user typing — they're the OS responding to
    agent tool_use calls. Counting them as user_message inflated W14's
    user_msg_count by 87% (1219 of 1405) and added phantom reading-time."""
    fixture = Path(__file__).parent / "fixtures" / "sample_transcript.jsonl"
    events = list(extract_from_jsonl(fixture, projects=PROJECTS, source_machine="mac_mini"))
    user_msgs = [e for e in events if e.kind == "user_message"]
    tool_results = [e for e in events if e.kind == "tool_result"]
    assert len(user_msgs) == 2, f"expected 2 real user messages, got {len(user_msgs)}"
    assert len(tool_results) == 1
    tr = tool_results[0]
    assert tr.actor == "agent"
    assert "tests/test_x.py" in tr.content_snippet
    assert tr.metadata["tool_use_id"] == "t1"


def test_workspace_cwd_flagged_as_agent_prompt_not_user_message():
    """Regression: R2 product-engineer transcripts have cwd=/workspace/<project>
    and their "user"-role records are agent-injected (synthesized agent prompt
    on first message, Slack/Linear-routed follow-ups after). Treating these as
    user_message inflates active_hours from a few keyboard-minutes per week
    to tens of hours (the user would have to be typing 1500-word ticket specs).
    Mark them as kind=agent_prompt, actor=agent."""
    fixture = Path(__file__).parent / "fixtures" / "sample_workspace_transcript.jsonl"
    pe_projects = ProjectsConfig(projects=[
        ProjectSpec(slug="notes-repo", cwd_patterns=["/workspace/notes-repo", "/workspace/notes-repo/**"]),
    ])
    events = list(extract_from_jsonl(fixture, projects=pe_projects, source_machine="r2_product_engineer"))

    # No "user_message" — both user-role records become agent_prompt.
    user_msgs = [e for e in events if e.kind == "user_message"]
    assert user_msgs == []

    agent_prompts = [e for e in events if e.kind == "agent_prompt"]
    assert len(agent_prompts) == 2
    assert all(e.actor == "agent" for e in agent_prompts)
    assert all(e.metadata.get("agent_run") is True for e in agent_prompts)
    assert all(e.project == "notes-repo" for e in agent_prompts)

    # Agent-side text still flows as agent_message normally.
    agent_msgs = [e for e in events if e.kind == "agent_message"]
    assert len(agent_msgs) == 1


def test_is_system_message_filters_synthetic_prompts():
    """The extractor's _is_system_message must catch slash-command artifacts,
    autonomous-loop ticks, session-resume cues, and rare-disease harness prompts."""
    from weekly_review.extractors.claude_transcript import _is_system_message

    # Already covered (regression-check).
    assert _is_system_message("<system-reminder>")
    assert _is_system_message("<command-name>/loop</command-name>")
    assert _is_system_message("<task-notification>")
    assert _is_system_message('<channel source="claude-hive">')

    # New patterns.
    assert _is_system_message("<command-message>loop</command-message>")
    assert _is_system_message("<command-args>--verbose</command-args>")
    assert _is_system_message("Continue from where you left off.")
    assert _is_system_message("Continue from where you left off")  # no period
    assert _is_system_message("Loop tick (the user's on a bike ride; keep going)")
    assert _is_system_message("/doctor")
    assert _is_system_message("/clear")
    assert _is_system_message("/health-tool")  # bare slash skill invocation
    assert _is_system_message("You are a rare disease diagnostic expert with...")
    assert _is_system_message("You are the lead physician coordinating a rare disease...")
    assert _is_system_message("You are helping a clinician investigate a case.")

    # Real user messages must NOT be filtered.
    assert not _is_system_message("Continue with the next task")
    assert not _is_system_message("/doctor please run a health check on the cluster")
    assert not _is_system_message("Loop the test suite until it passes")
    assert not _is_system_message("You are amazing")
    assert not _is_system_message("Can you fix the bug?")


def test_is_system_message_filters_peer_relay():
    """Peer-relay records are another session's traffic injected as user-role
    records. They carry no promptSource, so the text prefix is the only tell —
    and they were 132k words (vs ~12k real) in the 2026-08-10 week."""
    from weekly_review.extractors.claude_transcript import _is_system_message

    assert _is_system_message(
        'Another Claude session sent a message:\n<teammate-message teammate_id="qa-driver-1">'
    )
    assert _is_system_message(
        'Another Claude session sent a message: <agent-message from="bench-a06">'
    )
    assert _is_system_message(
        "Another Claude session sent a message: <cross-session-message>"
    )
    assert _is_system_message("[Request interrupted by user]")
    assert _is_system_message("[Request interrupted by user for tool use]")

    # The user talking *about* the peers is still the user.
    assert not _is_system_message("Another approach: send the message to the peer")
    assert not _is_system_message("Did another Claude session send a message?")


def test_is_machine_record_uses_prompt_source():
    """Structural markers catch harness-injected prompts without needing to know
    any particular user's cron wording — `promptSource` names who supplied it."""
    from weekly_review.extractors.claude_transcript import _is_machine_record

    # Scheduled/cron runs: arbitrary prose, but flagged structurally.
    assert _is_machine_record(
        {"promptSource": "system", "isMeta": True},
        "Twice-daily assistant inbox check. Search Gmail for NEW notes",
    )
    assert _is_machine_record(
        {"promptSource": "system"}, "Hourly fast-build watch. Check on the peer"
    )
    # Headless `claude -p` runs (launchd jobs) mark their prompt `sdk`. Two daily
    # ones read as hands-on at 7:30 and 9:00 every day until this was added.
    assert _is_machine_record(
        {"promptSource": "sdk", "entrypoint": "sdk-cli"},
        "You are the daily chat-vs-workspace audit for the board.",
    )
    # isMeta alone is harness bookkeeping.
    assert _is_machine_record({"isMeta": True}, "some meta record")

    # The desktop app also sends sdk, and its prompts are typed by the human.
    assert not _is_machine_record(
        {"promptSource": "sdk", "entrypoint": "claude-desktop"},
        "hey, can you add some notes to the README?",
    )
    # Real human input is `typed` or `queued` — must survive.
    assert not _is_machine_record(
        {"promptSource": "queued"}, "please rebase the PR onto main"
    )
    assert not _is_machine_record({"promptSource": "typed"}, "how's it going?")
    assert not _is_machine_record({}, "what are the 16 green ones?")

    # Text patterns still apply when no structural marker is present.
    assert _is_machine_record(
        {"promptSource": "queued"},
        'Another Claude session sent a message: <teammate-message teammate_id="x">',
    )


def _write_jsonl(path, records):
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    base = {"sessionId": "lead", "cwd": "/Users/me/dev/weekly-review"}
    path.write_text("\n".join(json.dumps({**base, **r}) for r in records) + "\n")
    return path


def _wake(ts, event="thread.created", doc="d-X", thread="th1"):
    return {"type": "user", "timestamp": ts, "promptSource": "system", "isMeta": True,
            "message": {"role": "user", "content":
                f'<channel source="plugin:claude-workspaces:claude-workspaces" doc_id="{doc}" '
                f'thread_id="{thread}" event="{event}" author="Someone">\n[created] a comment body\n</channel>'}}


def _assistant(ts, text=None, tool_id=None):
    part = ({"type": "tool_use", "id": tool_id, "name": "Read", "input": {}} if tool_id
            else {"type": "text", "text": text})
    return {"type": "assistant", "timestamp": ts,
            "message": {"role": "assistant", "model": "m", "content": [part]}}


def _tool_result(ts, tool_id):
    return {"type": "user", "timestamp": ts, "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_id, "content": "ok", "is_error": False}]}}


def test_printed_artifacts_reads_hashes_and_pr_urls_not_years_or_words():
    from weekly_review.extractors.claude_transcript import PRINTED_SHAS_MAX, printed_artifacts
    text = ("[fix/x 9f1c2ab] Join commits\n pushed 0123456789abcdef0123456789abcdef01234567\n"
            "in 2026 we added deadbeef and 12345678 and facade1\n"
            + "x" * 600 + " https://github.com/FryanPan/Weekly-Review/pull/53 ")
    got = printed_artifacts(text)
    assert got["printed_shas"] == ["0123456", "9f1c2ab", "facade1"]
    assert got["printed_prs"] == ["fryanpan/weekly-review#53"]
    assert printed_artifacts("nothing here, 2026-09-07") == {}
    many = " ".join(f"a{n:06d}" for n in range(200))
    assert len(printed_artifacts(many)["printed_shas"]) == PRINTED_SHAS_MAX


def test_a_tool_result_carries_the_artifacts_it_printed(tmp_path):
    path = _write_jsonl(tmp_path / "lead.jsonl", [
        _assistant("2026-09-12T00:15:40Z", tool_id="t1"),
        {"type": "user", "timestamp": "2026-09-12T00:15:41Z", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "is_error": False,
             "content": "y" * 900 + " [main 9f1c2ab] done"}]}},
    ])
    events = list(extract_from_jsonl(path, projects=PROJECTS, source_machine="mm"))
    tr = next(e for e in events if e.kind == "tool_result")
    assert tr.metadata["printed_shas"] == ["9f1c2ab"]


def test_agent_turn_woken_by_a_doc_comment_carries_the_comment_key(tmp_path):
    """A lead answering a doc comment is working on the comment's task, so
    the turn has to say which comment woke it. The body is never kept."""
    path = _write_jsonl(tmp_path / "lead.jsonl", [
        _wake("2026-09-12T00:15:33Z"),
        _assistant("2026-09-12T00:15:40Z", tool_id="t1"),
        _tool_result("2026-09-12T00:15:41Z", "t1"),
        _assistant("2026-09-12T00:16:00Z", text="replied on the thread"),
        {"type": "user", "timestamp": "2026-09-12T00:20:00Z", "promptSource": "typed",
         "message": {"role": "user", "content": "now look at the export"}},
        _assistant("2026-09-12T00:20:10Z", text="looking at the export"),
    ])
    events = list(extract_from_jsonl(path, projects=PROJECTS, source_machine="mm"))
    woken = [e for e in events if e.metadata.get("wake")]
    assert [e.kind for e in woken] == ["tool_call", "tool_result", "agent_message"]
    assert {e.metadata["wake"] for e in woken} == {"d-X|th1"}
    after = [e for e in events if e.ts >= "2026-09-12T00:20:00Z"]
    assert after and not any(e.metadata.get("wake") for e in after)
    assert not any("comment body" in (e.content_snippet or "") for e in events)


def test_resolving_or_reopening_a_thread_wakes_on_that_thread(tmp_path):
    for event in ("thread.resolved", "thread.reopened"):
        path = _write_jsonl(tmp_path / f"{event}.jsonl", [
            _wake("2026-09-12T00:15:33Z", event=event),
            _assistant("2026-09-12T00:15:40Z", text="closing out the thread"),
        ])
        events = list(extract_from_jsonl(path, projects=PROJECTS, source_machine="mm"))
        assert [e.metadata.get("wake") for e in events if e.actor == "agent"] == ["d-X|th1"], event
    path = _write_jsonl(tmp_path / "archived.jsonl", [
        _wake("2026-09-12T00:15:33Z", event="thread.archived"),
        _assistant("2026-09-12T00:15:40Z", text="noted"),
    ])
    assert not any(e.metadata.get("wake") for e in extract_from_jsonl(path, projects=PROJECTS, source_machine="mm"))


def test_a_notification_carries_on_the_comment_turn_it_reports_to(tmp_path):
    path = _write_jsonl(tmp_path / "lead.jsonl", [
        _wake("2026-09-12T00:15:33Z"),
        _assistant("2026-09-12T00:15:40Z", text="starting the build"),
        {"type": "user", "timestamp": "2026-09-12T08:00:00Z",
         "message": {"role": "user", "content": "<task-notification>\n<task-id>b1</task-id>"}},
        _assistant("2026-09-12T08:00:10Z", text="build done, replying"),
    ])
    events = list(extract_from_jsonl(path, projects=PROJECTS, source_machine="mm"))
    assert {e.content_snippet: e.metadata.get("wake") for e in events if e.kind == "agent_message"} == {
        "starting the build": "d-X|th1", "build done, replying": "d-X|th1"}


def test_other_board_notices_end_a_comment_turn(tmp_path):
    path = _write_jsonl(tmp_path / "lead.jsonl", [
        _wake("2026-09-12T00:15:33Z"),
        _assistant("2026-09-12T00:15:40Z", text="on it"),
        _wake("2026-09-12T00:30:00Z", event="workspace.ready_idle"),
        _assistant("2026-09-12T00:30:05Z", text="picking the next task"),
    ])
    events = list(extract_from_jsonl(path, projects=PROJECTS, source_machine="mm"))
    by_text = {e.content_snippet: e.metadata.get("wake") for e in events if e.kind == "agent_message"}
    assert by_text["on it"] == "d-X|th1"
    assert by_text["picking the next task"] is None


def _board_call(ts, tool, tool_id, **tool_input):
    return {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "model": "m", "content": [
        {"type": "tool_use", "id": tool_id, "name": f"mcp__plugin_claude-workspaces_claude-workspaces__{tool}",
         "input": tool_input}]}}


def test_a_tool_call_working_a_board_card_names_the_card_and_every_event_its_turn(tmp_path):
    path = _write_jsonl(tmp_path / "lead.jsonl", [
        {"type": "user", "timestamp": "2026-09-12T00:00:00Z", "promptSource": "typed",
         "message": {"role": "user", "content": "ship the export"}},
        _board_call("2026-09-12T00:00:10Z", "task_transition", "c1", workspaceId="w-1", taskId="t-AAA", to="in-progress"),
        _tool_result("2026-09-12T00:00:11Z", "c1"),
        _board_call("2026-09-12T00:00:20Z", "list_tasks", "c2", workspaceId="w-1", taskId="t-BBB"),
        _board_call("2026-09-12T00:00:30Z", "task_transition", "c3", workspaceId="w-1", taskId=wid("not-a-card")),
        _wake("2026-09-12T00:30:00Z", event="workspace.ready_idle"),
        _assistant("2026-09-12T00:30:05Z", text="next"),
    ])
    events = list(extract_from_jsonl(path, projects=PROJECTS, source_machine="mm"))
    calls = [e for e in events if e.kind == "tool_call"]
    assert [c.metadata.get("card") for c in calls] == ["t-AAA", None, None], "reads and non-card ids name no card"
    before = {e.metadata["turn"] for e in events if e.actor == "agent" and e.ts < "2026-09-12T00:30:00Z"}
    after = [e.metadata["turn"] for e in events if e.kind == "agent_message"]
    assert len(before) == 1, "a tool call and its result are one turn"
    assert after[0] > max(before), "any inbound record, even a board notice, starts a new turn"


def test_a_dispatch_prompt_naming_one_card_names_it_and_two_names_neither(tmp_path):
    def prompt(ts, text):
        return {"type": "user", "timestamp": ts, "message": {"role": "user", "content": text}}

    one = _write_jsonl(tmp_path / "lead" / "subagents" / "agent-one.jsonl", [
        prompt("2026-09-12T00:00:00Z", "You are building " + tid("Ab-cDefgh1jK") + ". Read " + tid("Ab-cDefgh1jK") + " first.")])
    two = _write_jsonl(tmp_path / "lead" / "subagents" / "agent-two.jsonl", [
        prompt("2026-09-12T00:00:00Z", "Compare " + tid("Ab-cDefgh1jK") + " with " + tid("Lm3nOpqRsTu4") + ".")])
    [e1] = extract_from_jsonl(one, projects=PROJECTS, source_machine="mm")
    [e2] = extract_from_jsonl(two, projects=PROJECTS, source_machine="mm")
    assert e1.kind == "subagent_input" and e1.metadata.get("card") == tid("Ab-cDefgh1jK")
    assert "card" not in e2.metadata


def test_a_hyphenated_word_is_not_a_board_task_id():
    """Week of 2026-08-17: the tail of a hyphenated phrase ("don't restart the server", hyphenated) was read as a board task.

    The hyphenated words are spelled in two pieces so no tracked file holds a
    string shaped like a board id (see tests/fakeids.py).
    """
    from weekly_review.extractors.claude_transcript import card_named_in_prompt
    dont = "don't-" + "restart-the-server"
    card = tid("-vwXyz01AbCd")           # a real shape: the twelve characters may start with a hyphen
    assert card_named_in_prompt(f"Please {dont}, then build {card}.") == card
    assert card_named_in_prompt(f"{dont} first") is None
    assert card_named_in_prompt("at-restart-the- step") is None
    assert card_named_in_prompt("build " + tid("abcdefghijkl") + " now") is None
    assert card_named_in_prompt("build " + tid("Ab-cDefgh1jKx") + " now") is None     # thirteen characters
    assert card_named_in_prompt("(" + tid("Ab-cDefgh1jK") + ")") == tid("Ab-cDefgh1jK")
    assert card_named_in_prompt("x-" + tid("Ab-cDefgh1jK")) is None
    assert card_named_in_prompt("see " + tid("Ab-cDefgh1jK") + "-notes.md") == tid("Ab-cDefgh1jK")
    assert card_named_in_prompt(tid("RESTART-THE-SERVER")) is None


def test_turn_trigger_says_who_started_a_turn():
    from weekly_review.extractors.claude_transcript import turn_trigger

    def trig(text, previous=("him", "t-OLD"), **rec):
        return turn_trigger(rec, text, previous, lambda name: name == "Alex")

    ws = '<channel source="plugin:claude-workspaces:claude-workspaces" '
    assert trig("ship the export", promptSource="typed") == ("him", None)
    assert trig("Another Claude session sent a message: <teammate-message>") == ("agent", None)
    assert trig("run the weekly sweep", promptSource="system") == ("agent", None)
    assert trig(ws + 'event="workspace.stalled" task_id="t-AAA">\nstalled\n</channel>') == ("agent", "t-AAA")
    assert trig(ws + 'event="workspace.review_answered" task_id="t-AAA">\n</channel>') == ("him", "t-AAA")
    assert trig(ws + 'event="thread.replied" author="Alex" task_id="t-AAA">\n</channel>') == ("him", "t-AAA")
    assert trig(ws + 'event="thread.replied" author="Lead Agent" task_id="t-AAA">\n</channel>') == ("agent", "t-AAA")
    assert trig('<channel source="claude-hive" task_id="t-AAA">\nhi\n</channel>') == ("agent", None), \
        "only a workspace names a board task"
    assert trig(ws + 'event="thread.created" task_id="w-1">\n</channel>') == ("agent", None)
    carry = ("agent", "t-AAA")
    assert trig("<task-notification>\n<task-id>x</task-id>", previous=carry) == carry
    assert trig("This session is being continued from a previous conversation", previous=carry) == carry
    assert trig("anything", previous=carry, isMeta=True) == carry
    # A peer's message is a meta record too, and starts the peer's ask.
    his = ("him", "t-AAA")
    assert trig("Another Claude session sent a message: <cross-session-message>", previous=his,
                isMeta=True, promptSource="system") == ("agent", None)
    assert trig("status from the builder", previous=his, isMeta=True, promptSource="system",
                origin={"kind": "peer"}) == ("agent", None)
    assert trig("anything", previous=his, isMeta=True, origin={"kind": "channel"}) == his


def test_every_agent_event_of_a_turn_carries_its_trigger_and_wake_task(tmp_path):
    ws = '<channel source="plugin:claude-workspaces:claude-workspaces" '
    path = _write_jsonl(tmp_path / "lead.jsonl", [
        {"type": "user", "timestamp": "2026-09-12T00:00:00Z", "promptSource": "system", "isMeta": True,
         "message": {"role": "user", "content": ws + 'event="workspace.stalled" task_id="t-AAA">\nstalled\n</channel>'}},
        _assistant("2026-09-12T00:00:10Z", tool_id="t1"),
        _tool_result("2026-09-12T00:00:11Z", "t1"),
        {"type": "user", "timestamp": "2026-09-12T00:05:00Z",
         "message": {"role": "user", "content": "<task-notification>\n<task-id>b1</task-id>"}},
        _assistant("2026-09-12T00:05:10Z", text="build finished"),
        {"type": "user", "timestamp": "2026-09-12T00:20:00Z", "promptSource": "typed",
         "message": {"role": "user", "content": "now look at the export"}},
        _assistant("2026-09-12T00:20:10Z", text="looking at the export"),
    ])
    events = [e for e in extract_from_jsonl(path, projects=PROJECTS, source_machine="mm", is_subject=lambda n: False)
              if e.actor == "agent"]
    got = [(e.kind, e.metadata.get("trigger"), e.metadata.get("wake_task")) for e in events]
    assert got == [("tool_call", "agent", "t-AAA"), ("tool_result", "agent", "t-AAA"),
                   ("agent_message", "agent", "t-AAA"), ("agent_message", "him", None)]


def test_a_turn_his_unkeyed_answer_or_dictation_started_says_so(tmp_path):
    ws = '<channel source="plugin:claude-workspaces:claude-workspaces" '
    path = _write_jsonl(tmp_path / "lead.jsonl", [
        {"type": "user", "timestamp": "2026-09-12T00:00:00Z", "promptSource": "system", "isMeta": True,
         "message": {"role": "user", "content": ws + 'event="workspace.review_answered" workspace_id="w-1">\n</channel>'}},
        _assistant("2026-09-12T00:00:10Z", text="acting on the answer"),
        {"type": "user", "timestamp": "2026-09-12T00:05:00Z",
         "message": {"role": "user", "content": "This session is being continued from a previous conversation"}},
        _assistant("2026-09-12T00:05:10Z", text="still acting on it"),
        {"type": "user", "timestamp": "2026-09-12T00:10:00Z", "promptSource": "system", "isMeta": True,
         "message": {"role": "user", "content": ws + 'event="voice.request" author="Alex" workspace_id="w-1">\n</channel>'}},
        _assistant("2026-09-12T00:10:10Z", text="doing what he said"),
        {"type": "user", "timestamp": "2026-09-12T00:20:00Z", "promptSource": "system", "isMeta": True,
         "message": {"role": "user", "content": ws + 'event="workspace.review_answered" task_id="' + tid("AAAAAAAAAAAA") + '" workspace_id="w-1">\n</channel>'}},
        _assistant("2026-09-12T00:20:10Z", text="keyed answer"),
        {"type": "user", "timestamp": "2026-09-12T00:30:00Z", "promptSource": "system", "isMeta": True,
         "message": {"role": "user", "content": ws + 'event="voice.request" author="Lead Agent" workspace_id="w-1">\n</channel>'}},
        _assistant("2026-09-12T00:30:10Z", text="peer dictation"),
    ])
    events = [e for e in extract_from_jsonl(path, projects=PROJECTS, source_machine="mm", is_subject=lambda n: n == "Alex")
              if e.kind == "agent_message"]
    got = {e.content_snippet: (e.metadata.get("ask_wake"), e.metadata.get("ask_wake_ws"), e.metadata.get("ask_wake_at"))
           for e in events}
    assert got == {
        "acting on the answer": ("workspace.review_answered", "w-1", "2026-09-12T00:00:00Z"),
        "still acting on it": ("workspace.review_answered", "w-1", "2026-09-12T00:00:00Z"),
        "doing what he said": ("voice.request", "w-1", "2026-09-12T00:10:00Z"),
        "keyed answer": (None, None, None),
        "peer dictation": (None, None, None),
    }


def test_agent_work_names_the_turn_he_typed_until_something_else_starts_work(tmp_path):
    path = _write_jsonl(tmp_path / "lead.jsonl", [
        {"type": "user", "timestamp": "2026-09-12T00:00:00Z", "promptSource": "typed",
         "message": {"role": "user", "content": "run the full sweep overnight"}},
        _assistant("2026-09-12T00:00:10Z", text="starting the sweep"),
        {"type": "user", "timestamp": "2026-09-12T09:00:00Z",
         "message": {"role": "user", "content": "<task-notification>\n<task-id>b1</task-id>"}},
        _assistant("2026-09-12T09:00:10Z", text="sweep finished"),
        {"type": "user", "timestamp": "2026-09-12T09:30:00Z",
         "message": {"role": "user", "content": "Another Claude session sent a message: <teammate-message>"}},
        _assistant("2026-09-12T09:30:10Z", text="answering the peer"),
    ])
    events = list(extract_from_jsonl(path, projects=PROJECTS, source_machine="mm", is_subject=lambda n: False))
    [msg] = [e for e in events if e.kind == "user_message"]
    replies = {e.content_snippet: e.metadata.get("ask_turn") for e in events if e.kind == "agent_message"}
    assert replies == {"starting the sweep": msg.metadata["turn"], "sweep finished": msg.metadata["turn"],
                       "answering the peer": None}


def test_subagent_turns_are_not_stamped_with_a_comment(tmp_path):
    """A subagent's user-role records are its dispatch prompt; its lane is
    credited through the parent session, not through a wake of its own."""
    path = _write_jsonl(tmp_path / "lead" / "subagents" / "agent-abc.jsonl", [
        _wake("2026-09-12T00:15:33Z"),
        _assistant("2026-09-12T00:15:40Z", text="subagent work"),
    ])
    events = list(extract_from_jsonl(path, projects=PROJECTS, source_machine="mm"))
    assert events and not any(e.metadata.get("wake") for e in events)
