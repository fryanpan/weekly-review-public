from weekly_review.events import Event
from weekly_review.effort import active_time_seconds_for_user_message
from weekly_review.extractors.live_feedback import _is_subject, LIVE_FEEDBACK_REVIEW_SEC
from weekly_review.identity import AnonEra, SubjectIdentity

from datetime import date
from tests.fakeids import tid

# The subject as configured for these tests: two fragments covering the four
# handle families one person is rendered under, and the one dated era.
SUBJECT = SubjectIdentity(
    name_fragments=("alex", "arivera"),
    anon_eras=(AnonEra(date(2026, 6, 29), date(2026, 8, 10), "handles unnamed"),),
)
IN_ERA = "2026-07-15T10:00:00.000Z"
AFTER_ERA = "2026-09-01T10:00:00.000Z"


def _is_bryan(author, ts=IN_ERA):
    return _is_subject(author, SUBJECT, ts)


def test_fixed_active_sec_honored():
    """A live-feedback comment event carries fixed_active_sec; the effort
    formula must use it directly (no prior-agent reading)."""
    ev = Event(ts="2026-05-01T00:00:00+00:00", actor="user", project="maps",
               session_id="live-feedback:maps-doc", source_system="live_feedback",
               source_machine="live_feedback", kind="user_message", seq=0,
               content_snippet="this looks wrong",
               metadata={"words": 3, "fixed_active_sec": 124.5})
    # 120s review + ~4.5s typing = 124.5s, regardless of prior agent message
    assert abs(active_time_seconds_for_user_message(ev, None) - 124.5) < 0.01


def test_fixed_active_sec_capped():
    ev = Event(ts="2026-05-01T00:00:00+00:00", actor="user", project="maps",
               session_id="s", source_system="live_feedback", source_machine="lf",
               kind="user_message", seq=0, content_snippet="x",
               metadata={"words": 9999, "fixed_active_sec": 99999})
    # capped at PER_MESSAGE_CAP_SEC (20 min)
    assert active_time_seconds_for_user_message(ev, None) == 20 * 60


def test_is_bryan_filters_agent():
    assert _is_bryan({"kind": "anon", "name": "Anon-dqpf6b"}) is True
    assert _is_bryan({"kind": "known", "name": "Agent"}) is False
    assert _is_bryan({"kind": "known", "name": "Alex"}) is True
    assert _is_bryan({"kind": "known", "name": "Alex Rivera"}) is True


def test_every_handle_family_the_store_renders_him_under_counts():
    """The bug: one fragment is one identity, and people have several.

    `HUMAN_NAME_FRAGMENT = "alex"` matched `known-*` ("Alex") and `anon-*`
    ("Alex Rivera") and missed `user-*` ("Arivera") entirely -- 1,054 of his
    2,938 comments, 29%, dropped by the filter written to keep machines out.
    """
    for name in ("Alex", "Alex Rivera", "Arivera", "arivera"):
        assert _is_bryan({"kind": "known", "name": name}) is True, name


def test_an_anonymous_handle_counts_only_inside_the_era():
    """The user's ruling, 2026-09-12: "July anon reads are me".

    The era is a dated exception with two hard edges, and the edge is the
    point: after 2026-08-10 the store resolves names onto browser handles, so
    one that is still anonymous is somebody else.
    """
    anon = {"kind": "anon", "name": "Anon-tnd4hs"}
    assert _is_bryan(anon, IN_ERA) is True
    assert _is_bryan(anon, AFTER_ERA) is False
    assert _is_bryan(anon, "2026-06-01T10:00:00.000Z") is False  # before it too


def test_a_named_stranger_inside_the_era_is_still_not_him():
    """The era covers GENERATED handles, never a name the store really knows.

    This is what drops the one identified collaborator without this repo ever
    holding their name -- an exclude list of real people would be both a
    maintenance burden and somebody else's data in a public repo.
    """
    assert _is_bryan({"kind": "user", "name": "Someone Else"}, IN_ERA) is False


def test_the_owner_flag_counts_without_any_name_list():
    """The structural field beats the name list where the store has one.

    `actorName` and `isOwner` agree on 5,687 of 5,699 read sessions (99.8%),
    and `isOwner` covers the resolved-anon handles a fragment cannot see.
    """
    assert _is_bryan({"kind": "anon", "name": "Anon-zzz", "isOwner": True},
                     AFTER_ERA) is True


def test_an_unconfigured_identity_does_not_report_zero_hours():
    """A fresh install with no identity.yaml must not silently read as no work.

    It counts every non-agent person instead, matching github_usernames(), and
    the run warns. Reporting zero would look exactly like a quiet week.
    """
    blank = SubjectIdentity()
    assert _is_subject({"kind": "known", "name": "Anyone"}, blank, AFTER_ERA) is True
    assert _is_subject({"kind": "agent", "name": "Live Feedback"}, blank, AFTER_ERA) is False


def test_is_bryan_excludes_agents_posting_under_project_names():
    """Agents identify by their project display name, not "Agent". A denylist
    on the literal name let 53k of 61k live-feedback words through as human
    review time in the 2026-08-10 week."""
    for name, aid in [
        ("Client A board", "agent-client-a-fast-build"),
        ("Live Feedback", "agent-live-feedback"),
        ("Notes Repo", "agent-notes-repo"),
        ("Health Tool", "agent-health-tool"),
        ("Claude (fast build agent)", "qb-agent"),
    ]:
        assert _is_bryan({"kind": "known", "name": name, "id": aid}) is False, name

    # Explicit agent kind is excluded even when the name would otherwise pass.
    assert _is_bryan({"kind": "agent", "name": "Live Feedback"}) is False

    # A third party's comment is real review, but it is not the user's time.
    assert _is_bryan({"kind": "known", "name": "A Reviewer"}) is False

    # Malformed input must not be credited.
    assert _is_bryan({}) is False
    assert _is_bryan(None) is False


def test_iter_review_sessions_parses_activity_jsonl(tmp_path):
    import json
    from weekly_review.extractors.live_feedback import _iter_review_sessions
    from weekly_review.config import ProjectsConfig

    events = [
        # counted: person, interaction-bounded
        {"type": "read_session", "actor": "person", "actorId": "anon-x", "actorName": "Alex Rivera",
         "ts": "2026-06-20T10:05:00.000Z",
         "doc": {"docId": "d1", "sourceUrl": "/Users/b/dev/weekly-review/doc.md", "title": "t"},
         "payload": {"endTs": "2026-06-20T10:05:00.000Z", "durationMs": 300000,
                     "interactionBounded": True, "maxScrollDepthPct": 80}},
        # skipped: agent actor
        {"type": "read_session", "actor": "agent", "actorId": "known-agent",
         "ts": "2026-06-20T11:00:00.000Z", "doc": {"docId": "d1"},
         "payload": {"durationMs": 60000, "interactionBounded": True}},
        # skipped: not interaction-bounded (could be idle tab)
        {"type": "read_session", "actor": "person", "actorId": "anon-x", "actorName": "Alex Rivera",
         "ts": "2026-06-20T12:00:00.000Z", "doc": {"docId": "d1"},
         "payload": {"durationMs": 60000, "interactionBounded": False}},
        # skipped: comment (handled by the ydoc path)
        {"type": "comment", "actor": "person", "actorId": "anon-x", "actorName": "Alex Rivera",
         "ts": "2026-06-20T13:00:00.000Z", "doc": {"docId": "d1"},
         "payload": {"text": "hi", "wordCount": 1}},
    ]
    f = tmp_path / "activity.jsonl"
    f.write_text("\n".join(json.dumps(e) for e in events))
    from weekly_review.config import ProjectSpec
    projects = ProjectsConfig(projects=[ProjectSpec(
        slug="weekly-review",
        cwd_patterns=["/Users/*/dev/weekly-review", "/Users/*/dev/weekly-review/**"])])
    out = list(_iter_review_sessions(projects, f, "live_feedback", identity=SUBJECT))
    assert len(out) == 1
    ev = out[0]
    assert ev.kind == "review_session"
    assert ev.project == "weekly-review"
    assert ev.metadata["fixed_active_sec"] == 300.0


def test_review_session_counts_as_hands_on_but_not_a_message():
    from weekly_review.segmentation import Task
    from weekly_review.effort import (
        active_hours_by_task, hands_on_blocks_by_task, human_input_chars_by_task,
    )
    msg = Event(ts="2026-06-20T10:00:00+00:00", actor="user", project="wr",
                session_id="s", source_system="claude_transcript", source_machine="mm",
                kind="user_message", seq=0, content_snippet="go",
                metadata={"words": 2, "chars": 2})
    rs = Event(ts="2026-06-20T12:00:00+00:00", actor="user", project="wr",
               session_id="live-feedback:d1", source_system="live_feedback",
               source_machine="lf", kind="review_session", seq=1,
               content_snippet="review session", metadata={"fixed_active_sec": 600.0})
    task = Task(task_id="t1", project="wr", title="x", event_ids=[0, 1])
    hours = active_hours_by_task([task], [msg, rs])["t1"]
    assert hours * 3600 >= 600  # the measured 10-min session is included
    # but it is NOT a message: no block, no chars
    assert hands_on_blocks_by_task([task], [msg, rs])["t1"] == 1
    assert human_input_chars_by_task([task], [msg, rs])["t1"] == 2


def test_board_routes_a_doc_whose_id_and_title_say_nothing():
    """The 2026-08-24 bug: renamed plugin, opaque doc ids, 26% into `unknown`."""
    from weekly_review.extractors.live_feedback import _route_doc
    from weekly_review.config import ProjectsConfig, ProjectSpec

    projects = ProjectsConfig(projects=[
        ProjectSpec(slug="claude-live-feedback-plugin", cwd_patterns=[],
                    doc_keywords=["live-feedback"]),
        ProjectSpec(slug="contacts-tool", cwd_patterns=[]),
    ])

    # A task card carries no path, and its id and title name no project.
    assert _route_doc(projects, "", "task:" + tid("Op7QrstU_vWx"), "Marking it done") == "unknown"
    # The board it sits on is the missing signal; its name matches a configured keyword.
    assert _route_doc(projects, "", "task:" + tid("Op7QrstU_vWx"), "Marking it done",
                      workspace_name="live-feedback") == "claude-live-feedback-plugin"
    # A board named exactly after a slug routes with no keyword entry at all.
    assert _route_doc(projects, "", "task:t-x", "", workspace_name="contacts-tool") == "contacts-tool"
    assert _route_doc(projects, "", "task:t-x", "", workspace_name="Contacts Tool") == "contacts-tool"
    # An unrecognised board stays unknown rather than being guessed at.
    assert _route_doc(projects, "", "task:t-x", "", workspace_name="Unfiled") == "unknown"


def test_load_workspace_names_and_doc_boards(tmp_path):
    import json
    from weekly_review.extractors.live_feedback import (
        load_workspace_names, load_doc_boards)

    # A missing workspaces dir is normal (no boards yet), not an error.
    assert load_workspace_names(tmp_path) == {}
    assert load_doc_boards(tmp_path) == {}

    ws_dir = tmp_path / "workspaces"
    ws_dir.mkdir()
    (ws_dir / "w-abc.tasks.json").write_text(json.dumps({
        "workspace": {"id": "w-abc", "name": "live-feedback",
                      "docIds": ["doc-1", "doc-2"]},
        "tasks": [{"id": "t-zzz", "workspaceId": "w-abc", "title": "a card"}],
    }))
    # Truncated mid-write by a crashed server: skip it, don't take the run down.
    (ws_dir / "w-broken.tasks.json").write_text('{"workspace": {"id": "w-b"')

    assert load_workspace_names(tmp_path) == {"w-abc": "live-feedback"}
    # Attached docs come from workspace.docIds; task cards come from `tasks`,
    # which is the ONLY record of their board — their own .ydoc doesn't have one.
    assert load_doc_boards(tmp_path) == {
        "doc-1": "live-feedback",
        "doc-2": "live-feedback",
        "task:t-zzz": "live-feedback",
    }


# --- activity touches (2026-09-12) ------------------------------------------

def _touch_projects():
    from weekly_review.config import ProjectSpec, ProjectsConfig

    return ProjectsConfig(projects=[ProjectSpec(
        slug="weekly-review",
        cwd_patterns=["/Users/*/dev/weekly-review", "/Users/*/dev/weekly-review/**"])])


def _write_activity(tmp_path, records):
    import json

    f = tmp_path / "activity.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in records))
    return f


def test_activity_touches_are_ingested_as_instants(tmp_path):
    """The five person actions the store records and we never read.

    doc_open / comment / reply / resolve / reopen carry no duration, so under
    the modelled-window metric their absence cost nothing. Under the block rule
    they are what bridges two reads into one block.
    """
    from weekly_review.extractors.live_feedback import _iter_activity_touches

    doc = {"docId": "d1", "sourceUrl": "/Users/b/dev/weekly-review/doc.md", "title": "t"}
    f = _write_activity(tmp_path, [
        {"type": "doc_open", "actor": "person", "actorId": "known-alex",
         "actorName": "Alex", "isOwner": True, "eventId": "e1",
         "ts": "2026-06-20T10:00:00.000Z", "doc": doc, "payload": {}},
        {"type": "resolve", "actor": "person", "actorId": "known-alex",
         "actorName": "Alex", "isOwner": True,
         "eventId": "e2", "ts": "2026-06-20T10:00:30.000Z", "threadId": "th1",
         "doc": doc, "payload": {}},
        # not a touch: the read session has its own path and its own duration
        {"type": "read_session", "actor": "person", "actorId": "known-alex",
         "actorName": "Alex", "isOwner": True,
         "ts": "2026-06-20T10:01:00.000Z", "doc": doc,
         "payload": {"durationMs": 5000, "interactionBounded": True}},
        # not his: an agent's activity is not his hands-on
        {"type": "doc_open", "actor": "agent", "actorId": "known-agent",
         "ts": "2026-06-20T10:02:00.000Z", "doc": doc, "payload": {}},
    ])
    out = list(_iter_activity_touches(_touch_projects(), f, "live_feedback",
                                     identity=SUBJECT))
    assert [e.metadata["activity_type"] for e in out] == ["doc_open", "resolve"]
    assert all(e.kind == "workspace_touch" for e in out)
    assert all(e.actor == "user" for e in out)
    # An instant: nothing here may create duration on its own.
    assert all("fixed_active_sec" not in e.metadata for e in out)
    assert out[0].project == "weekly-review"


def _anon_touch(ts):
    return {"type": "comment", "actor": "person", "actorId": "anon-zz",
            "actorName": "Anon-zz", "isOwner": False, "eventId": f"e{ts[:10]}",
            "ts": ts,
            "doc": {"docId": "d1", "sourceUrl": "/Users/b/dev/weekly-review/doc.md"},
            "payload": {"text": "hi", "wordCount": 1}}


def test_a_touch_is_filtered_by_the_identity_rule_and_still_carries_the_fields(tmp_path):
    """The ruling landed, so this now filters — and still records who it was.

    Written 2026-09-12 as "carries the identity fields RATHER THAN filtering on
    them": the dedup fix that recovered these events deliberately left the
    definition of the data subject alone, because who counts as the user is his
    call and not a side effect of a measurement change. He ruled the next day
    (a board task, 2026-09-12: "July anon reads are me"), so the filter is correct now
    and the discipline is unchanged — the ruling arrived before the filter did.

    The identity fields stay in metadata either way. A later ruling has to be
    re-appliable without re-deriving who was who from an event stream that no
    longer says.
    """
    from weekly_review.extractors.live_feedback import _iter_activity_touches

    f = _write_activity(tmp_path, [
        _anon_touch("2026-06-20T10:00:00.000Z"),  # before the era: not his
        _anon_touch("2026-07-15T10:00:00.000Z"),  # inside it: his
        _anon_touch("2026-09-01T10:00:00.000Z"),  # after it: not his
    ])
    out = list(_iter_activity_touches(_touch_projects(), f, "live_feedback",
                                      identity=SUBJECT))
    assert [e.ts for e in out] == ["2026-07-15T10:00:00.000Z"]
    ev, = out
    assert ev.metadata["actor_name"] == "Anon-zz"
    assert ev.metadata["is_owner"] is False
    assert ev.author_id == "anon-zz"


def test_a_machine_filed_under_actor_person_is_not_hands_on(tmp_path):
    """`actor == "person"` does not mean a person.

    The store files "Claude", "Live Feedback" and "Chat Audit" under it, told
    apart only by the actorId prefix. A touch costs no hours of its own, so
    this one is easy to miss — and it still bridges two of his blocks into one,
    which buys time he did not spend.
    """
    from weekly_review.extractors.live_feedback import _iter_activity_touches

    f = _write_activity(tmp_path, [
        {"type": "doc_open", "actor": "person", "actorId": "agent-claude",
         "actorName": "Claude", "eventId": "m1", "ts": "2026-07-15T10:00:00.000Z",
         "doc": {"docId": "d1", "sourceUrl": "/Users/b/dev/weekly-review/doc.md"},
         "payload": {}},
        {"type": "doc_open", "actor": "person", "actorId": "chat-audit",
         "actorName": "Chat Audit", "eventId": "m2", "ts": "2026-07-15T10:00:30.000Z",
         "doc": {"docId": "d1", "sourceUrl": "/Users/b/dev/weekly-review/doc.md"},
         "payload": {}},
    ])
    assert list(_iter_activity_touches(_touch_projects(), f, "live_feedback",
                                       identity=SUBJECT)) == []


def test_a_touch_keeps_the_stores_own_event_id(tmp_path):
    """Never invent an id a store already has -- the third time that defect shipped."""
    from weekly_review.extractors.live_feedback import _iter_activity_touches

    f = _write_activity(tmp_path, [
        {"type": "doc_open", "actor": "person", "actorId": "known-alex",
         "actorName": "Alex", "isOwner": True,
         "eventId": "abc123", "ts": "2026-06-20T10:00:00.000Z",
         "doc": {"docId": "d1", "sourceUrl": "/Users/b/dev/weekly-review/doc.md"},
         "payload": {}},
        {"type": "doc_open", "actor": "person", "actorId": "known-alex",
         "actorName": "Alex", "isOwner": True,
         "eventId": "def456", "ts": "2026-06-20T10:00:01.000Z",
         "doc": {"docId": "d1", "sourceUrl": "/Users/b/dev/weekly-review/doc.md"},
         "payload": {}},
    ])
    out = list(_iter_activity_touches(_touch_projects(), f, "live_feedback",
                                     identity=SUBJECT))
    assert [e.metadata["source_event_id"] for e in out] == ["touch:abc123", "touch:def456"]
    assert len({e.event_id for e in out}) == 2


def test_a_read_session_carries_its_start_as_well_as_its_duration(tmp_path):
    """Two numbers, two consumers, and they differ by 1.9h over a real week.

    durationMs is the interaction time INSIDE the session; startTs->endTs is
    first interaction to last. The modelled-window metric wants the former, the
    block rule wants the latter, so the extractor carries both.
    """
    from weekly_review.extractors.live_feedback import _iter_review_sessions

    f = _write_activity(tmp_path, [
        {"type": "read_session", "actor": "person", "actorId": "known-alex",
         "actorName": "Alex", "isOwner": True,
         "ts": "2026-06-20T10:05:00.000Z",
         "doc": {"docId": "d1", "sourceUrl": "/Users/b/dev/weekly-review/doc.md"},
         "payload": {"startTs": "2026-06-20T10:00:00.000Z",
                     "endTs": "2026-06-20T10:05:00.000Z",
                     "durationMs": 120000, "interactionBounded": True}},
    ])
    ev, = list(_iter_review_sessions(_touch_projects(), f, "live_feedback",
                                    identity=SUBJECT))
    assert ev.metadata["fixed_active_sec"] == 120.0
    assert ev.metadata["start_ts"] == "2026-06-20T10:00:00.000Z"


def test_the_pipeline_actually_reads_the_activity_touches():
    """Assert the CALL SITE. A source nothing reaches is the defect this closes."""
    import inspect

    from weekly_review.extractors import live_feedback

    src = inspect.getsource(live_feedback.extract_live_feedback)
    assert "_iter_activity_touches(" in src
