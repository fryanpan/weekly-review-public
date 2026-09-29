"""The worksheet's hands-on and wall-clock accounts: counts, zero cases, caps."""
from datetime import datetime, timedelta, timezone

from weekly_review.effort import _agent_intervals_for_task
from weekly_review.events import Event
from weekly_review.explain import (
    MAX_WORDS,
    RunIndex,
    _cap,
    explain_hands_on,
    explain_wall_clock,
    fmt_dur,
    hands_on_counts,
)
from weekly_review.segmentation import Task

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def ev(minute, actor, kind, session="s1", source="claude_transcript", snippet="", **md):
    ts = (T0 + timedelta(minutes=minute)).isoformat().replace("+00:00", "Z")
    return Event(ts=ts, actor=actor, project="p", session_id=session, source_system=source,
                 source_machine="m", kind=kind, seq=int(minute * 100), content_snippet=snippet,
                 metadata=md)


def task(tid, ids, title="t", delegated=False):
    return Task(task_id=tid, project="p", title=title, event_ids=list(ids), delegated=delegated)


def test_counts_messages_comments_files_review_items():
    events = [
        ev(0, "user", "user_message", snippet="go"),
        ev(1, "user", "workspace_touch", source="live_feedback", activity_type="comment"),
        ev(2, "user", "workspace_touch", source="live_feedback", activity_type="reply"),
        ev(3, "user", "user_message", source="live_feedback"),  # same comment, other store
        ev(4, "user", "review_session", source="live_feedback", doc_id="d1", source_url="/a/plan.md"),
        ev(5, "user", "workspace_touch", source="live_feedback", activity_type="doc_open",
           doc_id="task:t-1"),
        ev(6, "user", "workspace_touch", source="live_feedback", activity_type="doc_open", doc_id="d1"),
        ev(7, "user", "board_action", source="workspace_board", board_event="decision.answered"),
        ev(8, "user", "board_action", source="workspace_board", board_event="task.transitioned"),
        ev(9, "agent", "board_action", source="workspace_board", board_event="decision.answered"),
    ]
    c = hands_on_counts(task("a", range(len(events))), events)
    assert c["messages"] == 1
    assert c["comments"] == 2
    assert c["files"] == 2
    assert c["file_types"] == {"md": 1, "task card": 1}
    assert c["review_items"] == 1
    assert c["board_actions"] == 1


def test_comments_take_the_larger_store_because_the_activity_stream_started_later():
    events = [ev(0, "user", "user_message", source="live_feedback", snippet="one two three"),
              ev(1, "user", "user_message", source="live_feedback", snippet="four five"),
              ev(1, "user", "workspace_touch", source="live_feedback", activity_type="comment")]
    c = hands_on_counts(task("a", [0, 1, 2]), events)
    assert c["comments"] == 2
    assert c["comment_words"] == 5


def test_lone_clicks_say_they_are_instants_but_comments_do_not():
    clicks = [ev(0, "user", "workspace_touch", source="live_feedback", activity_type="doc_open"),
              ev(1, "agent", "agent_message")]
    tasks = {"a": task("a", [0, 1])}
    assert "Clicks count as instants" in explain_hands_on(
        tasks["a"], RunIndex(clicks, tasks), 0.0, [], [])
    comments = [ev(0, "user", "user_message", source="live_feedback", snippet="a b c d"),
                ev(1, "agent", "agent_message")]
    text = explain_hands_on(tasks["a"], RunIndex(comments, tasks), 0.05, [], [(T0, T0 + timedelta(minutes=3))])
    assert "instants" not in text
    assert "each comment's typing time" in text
    assert "1 min of reading before it" in text


def test_review_answers_name_their_reading_time_instead_of_calling_clicks_instants():
    """The user, 2026-09-17: "in the hands-on time summary in the task, I'm
    expecting to see that I reviewed a review item". A task whose only human
    events are review answers showed minutes and said clicks add no time."""
    events = [
        ev(0, "agent", "agent_message"),
        # On another task's page until 30 s before the first answer: cuts its window to 30 s.
        ev(9.5, "user", "workspace_touch", source="live_feedback", activity_type="doc_open",
           doc_id="task:t-other", task_id="t-other"),
        ev(10, "user", "board_action", source="workspace_board", board_event="decision.answered",
           task_id="t-1", review_item_id="r-1"),
        # Nothing else of his in the 2 min before this one: the full window.
        ev(30, "user", "board_action", source="workspace_board", board_event="decision.answered",
           task_id="t-1", review_item_id="r-2"),
    ]
    tasks = {"a": task("a", [0, 2, 3]), "b": task("b", [1])}
    idx = RunIndex(events, tasks)
    windows = [idx.answer_windows[i] for i in (2, 3)]
    text = explain_hands_on(tasks["a"], idx, 2.5 / 60, [], windows)
    assert "2 review items handled" in text
    assert "Review answers: 2 min of reading before 2 answers (up to 2 min each" in text
    assert "instants" not in text
    assert "comment's typing time" not in text   # no comments on the task, so no comment rule
    assert text.startswith("**Hands-on — 2 min.**")
    # The other task holds no answer, so it says nothing about review answers.
    assert "Review answers" not in explain_hands_on(tasks["b"], idx, 0.0, [], [])


def test_fmt_dur_shows_seconds_under_a_minute():
    # A 4-second reading window printed as "0 min" reads as no time at all.
    assert fmt_dur(4) == "4 s"
    assert fmt_dur(59.6) == "60 s"
    assert fmt_dur(60) == "1 min"


def _split_fixture():
    events = [
        ev(0, "user", "user_message", snippet="please build the thing"),
        ev(1, "agent", "agent_message"),
        ev(5, "agent", "tool_call"),
        ev(6, "user", "user_message", snippet="also this"),
        ev(9, "agent", "tool_result"),
    ]
    tasks = {"ask": task("ask", [0, 3], title="Build the thing"),
             "exec": task("exec", [1, 2, 4], title="exec", delegated=True)}
    return events, tasks


def test_zero_hands_on_names_the_split_task_and_always_shows_counts():
    events, tasks = _split_fixture()
    idx = RunIndex(events, tasks)
    text = explain_hands_on(tasks["exec"], idx, 0.0, [], [])
    assert "0 messages, 0 comments, 0 files reviewed, 0 review items handled" in text
    assert "1 message in the same session" in text
    assert '"Build the thing"' in text
    assert "split in two" in text
    assert "delegated" in text


def test_zero_hands_on_reports_trigger_message_when_it_came_just_before():
    events = [ev(0, "user", "user_message", snippet="kick it off"),
              ev(2, "agent", "agent_message"), ev(3, "agent", "tool_call")]
    tasks = {"ask": task("ask", [0]), "exec": task("exec", [1, 2])}
    text = explain_hands_on(tasks["exec"], RunIndex(events, tasks), 0.0, [], [])
    assert 'It started 2 min after your message "kick it off"' in text
    assert "another task" in text


def test_zero_hands_on_without_a_recent_message_says_something_else_started_it():
    events = [ev(0, "user", "user_message", snippet="old"),
              ev(120, "agent", "agent_message"), ev(121, "agent", "tool_call")]
    tasks = {"ask": task("ask", [0]), "exec": task("exec", [1, 2])}
    text = explain_hands_on(tasks["exec"], RunIndex(events, tasks), 0.0, [], [])
    assert "2h earlier" in text
    assert "something else started it" in text


def test_artifact_only_task_says_no_conversation_was_matched_without_claiming_where_it_is():
    """Nothing verifies the producing conversation landed on another task, so the
    text must not assert it; zero hands-on can equally mean it was never recorded."""
    events = [ev(0, "artifact", "commit", source="git")]
    tasks = {"c": task("c", [0])}
    text = explain_hands_on(tasks["c"], RunIndex(events, tasks), 0.0, [], [])
    assert "none was matched" in text
    assert "not recorded" in text
    assert "is counted on another task" not in text


def test_hands_on_breaks_out_both_instruments():
    events, tasks = _split_fixture()
    iv = [(T0, T0 + timedelta(minutes=30))]
    text = explain_hands_on(tasks["ask"], RunIndex(events, tasks), 0.6, iv,
                            [(T0, T0 + timedelta(minutes=6))])
    assert "Messages: 30 min" in text
    assert "Workspace: 6 min" in text
    assert "Overlap counted once." in text
    assert "split" not in text  # not near zero, no trigger story


def test_wall_clock_counts_bursts_cap_and_quiet_and_recomputes():
    # Two bursts: 0-10 min, and 60 min to 60 min + 5h of events every 10 min
    # (capped at 4h). Ten, because a quiet gap bridges 15 minutes since
    # 2026-09-15 — at twenty the second burst would be 16 zero-length ones.
    events = [ev(0, "agent", "tool_call"), ev(10, "agent", "tool_result")]
    events += [ev(60 + m, "agent", "tool_call") for m in range(0, 301, 10)]
    t = task("w", range(len(events)))
    tasks = {"w": t}
    idx = RunIndex(events, tasks)
    bursts = _agent_intervals_for_task(t, events)
    stored_h = sum((e - s).total_seconds() for s, e in bursts) / 3600
    text = explain_wall_clock(t, idx, stored_h, lambda *_: None)
    assert f"**Wall clock — {stored_h:.2f}h.**" in text
    assert "2 bursts" in text
    assert "1 hit the cap" in text
    assert "totalling 4h 10m" in text
    assert "spans 6h" in text
    assert "Recomputed" not in text


def test_wall_clock_flags_a_stored_figure_it_cannot_reproduce():
    events = [ev(0, "agent", "tool_call"), ev(10, "agent", "tool_result")]
    tasks = {"w": task("w", [0, 1])}
    text = explain_wall_clock(tasks["w"], RunIndex(events, tasks), 3.0, lambda *_: None)
    assert "Recomputed 0.17h" in text


def test_board_actions_alone_create_no_agent_time():
    events = [ev(0, "agent", "board_action", source="workspace_board"),
              ev(5, "agent", "board_action", source="workspace_board")]
    tasks = {"b": task("b", [0, 1])}
    text = explain_wall_clock(tasks["b"], RunIndex(events, tasks), 0.0, lambda *_: None)
    assert "No agent working time" in text


def test_cap_holds_every_account_to_the_word_limit():
    long = " ".join(["word"] * (MAX_WORDS + 40))
    capped = _cap(long)
    assert len(capped.split()) == MAX_WORDS
    assert capped.endswith("…")
    assert _cap("short text") == "short text"


def test_fmt_dur_scales_units():
    assert fmt_dur(90) == "2 min"
    assert fmt_dur(3600 * 4 + 600) == "4h 10m"
    assert fmt_dur(86400 * 3) == "3.0 days"
