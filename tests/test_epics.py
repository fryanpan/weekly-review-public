"""Tests for the epic-rollup layer."""
from __future__ import annotations

import logging

from weekly_review.epics import (
    EPIC_MAX_MEMBERS,
    Epic,
    roll_up_epics,
)
from weekly_review.events import Event
from weekly_review.segmentation import Task, _parse_ts


# --- helpers ---


def _task(
    task_id: str,
    project: str,
    title: str,
    *,
    issue_refs: set[str] | None = None,
    first: str = "2026-05-11T10:00:00Z",
    last: str | None = None,
    n_events: int = 1,
    n_user: int = 1,
) -> tuple[Task, list[Event]]:
    """Build an atomic Task plus its backing user_message events.

    Returns (task, events). The task's event_ids index into the events list
    the caller assembles; here each task gets its own freshly-numbered events
    starting at 0, and the caller stitches them together via _assemble().
    """
    t = Task(task_id=task_id, project=project, title=title)
    if issue_refs:
        t.issue_refs = set(issue_refs)
    t.first_event_ts = _parse_ts(first)
    t.last_event_ts = _parse_ts(last or first)
    evs: list[Event] = []
    for k in range(n_events):
        kind = "user_message" if k < n_user else "agent_message"
        actor = "user" if kind == "user_message" else "agent"
        evs.append(
            Event(
                ts=first, actor=actor, project=project, session_id="s",
                source_system="claude_transcript", source_machine="mm",
                kind=kind, seq=k, content_snippet=title,
            )
        )
    return t, evs


def _assemble(*pairs: tuple[Task, list[Event]]) -> tuple[list[Task], list[Event]]:
    """Stitch task/event pairs: assign global event indices into a shared list."""
    tasks: list[Task] = []
    events: list[Event] = []
    for task, evs in pairs:
        start = len(events)
        task.event_ids = list(range(start, start + len(evs)))
        events.extend(evs)
        tasks.append(task)
    return tasks, events


def _epic_of(epics: list[Epic], task_id: str) -> Epic:
    for e in epics:
        if task_id in e.member_task_ids:
            return e
    raise AssertionError(f"no epic contains {task_id}")


# --- shared issue ref clusters (Signal 1) ---


def test_two_tasks_sharing_issue_ref_cluster():
    tasks, events = _assemble(
        _task("a", "forms", "scan a paper form", issue_refs={"CLA-2435"}),
        _task("b", "forms", "improve form field detection", issue_refs={"CLA-2435"},
              first="2026-05-14T10:00:00Z"),
    )
    epics = roll_up_epics(tasks, events)
    assert len(epics) == 1
    assert set(epics[0].member_task_ids) == {"a", "b"}
    assert "CLA-2435" in epics[0].issue_refs
    assert epics[0].member_count == 2


def test_issue_ref_clusters_across_projects():
    """A ticket is specific — sharing it links tasks even in different projects."""
    tasks, events = _assemble(
        _task("a", "forms", "backend for the form", issue_refs={"CLA-2440"}),
        _task("b", "forms-ui", "frontend for the form", issue_refs={"CLA-2440"}),
    )
    epics = roll_up_epics(tasks, events)
    assert len(epics) == 1
    assert epics[0].projects == {"forms", "forms-ui"}


def test_different_issue_refs_do_not_cluster():
    tasks, events = _assemble(
        _task("a", "forms", "scan a paper form", issue_refs={"CLA-2435"}),
        _task("b", "forms", "draw the route map", issue_refs={"CLA-2436"}),
    )
    epics = roll_up_epics(tasks, events)
    assert len(epics) == 2


# --- same-project topical clusters (Signal 2) ---


def test_same_project_high_token_overlap_clusters():
    tasks, events = _assemble(
        _task("a", "maps", "render the route polyline overlay"),
        _task("b", "maps", "fix the route polyline overlay color",
              first="2026-05-13T10:00:00Z"),
    )
    epics = roll_up_epics(tasks, events)
    assert len(epics) == 1
    assert set(epics[0].member_task_ids) == {"a", "b"}


def test_different_projects_token_overlap_no_shared_ref_do_not_cluster():
    """Topical overlap must NOT bridge projects — only issue refs do."""
    tasks, events = _assemble(
        _task("a", "maps", "render the route polyline overlay"),
        _task("b", "atlas", "render the route polyline overlay"),
    )
    epics = roll_up_epics(tasks, events)
    assert len(epics) == 2


def test_low_token_overlap_does_not_cluster():
    tasks, events = _assemble(
        _task("a", "maps", "render the route polyline overlay"),
        _task("b", "maps", "set up the deployment pipeline credentials"),
    )
    epics = roll_up_epics(tasks, events)
    assert len(epics) == 2


def test_topical_overlap_outside_window_does_not_cluster():
    tasks, events = _assemble(
        _task("a", "maps", "render the route polyline overlay",
              first="2026-05-01T10:00:00Z", last="2026-05-01T11:00:00Z"),
        _task("b", "maps", "render the route polyline overlay",
              first="2026-05-25T10:00:00Z", last="2026-05-25T11:00:00Z"),
    )
    epics = roll_up_epics(tasks, events)
    # 24 days apart > 14-day window
    assert len(epics) == 2


def test_unknown_project_not_topically_clustered():
    """The `unknown` sink is too heterogeneous for token clustering."""
    tasks, events = _assemble(
        _task("a", "unknown", "render the route polyline overlay"),
        _task("b", "unknown", "render the route polyline overlay"),
    )
    epics = roll_up_epics(tasks, events)
    assert len(epics) == 2


def test_unknown_project_issue_ref_still_clusters():
    """Issue-ref clustering within unknown IS allowed."""
    tasks, events = _assemble(
        _task("a", "unknown", "something", issue_refs={"CLA-2500"}),
        _task("b", "unknown", "other thing", issue_refs={"CLA-2500"}),
    )
    epics = roll_up_epics(tasks, events)
    assert len(epics) == 1


# --- shared scope (Signal 3) ---


def test_shared_nongeneric_scope_clusters():
    """PRs sharing a non-generic conv-commit scope link their tasks."""
    t_a = Task(task_id="a", project="maps", title="work on navigate")
    t_a.first_event_ts = _parse_ts("2026-05-11T10:00:00Z")
    t_a.last_event_ts = _parse_ts("2026-05-11T12:00:00Z")
    t_b = Task(task_id="b", project="maps", title="more navigate work")
    t_b.first_event_ts = _parse_ts("2026-05-13T10:00:00Z")
    t_b.last_event_ts = _parse_ts("2026-05-13T12:00:00Z")
    events = [
        Event(ts="2026-05-11T11:00:00Z", actor="user", project="maps", session_id="s",
              source_system="claude_transcript", source_machine="mm",
              kind="user_message", seq=0, content_snippet="work on navigate"),
        Event(ts="2026-05-11T11:30:00Z", actor="artifact", project="maps", session_id="gh",
              source_system="github", source_machine="mm",
              kind="pr_merged", seq=1, content_snippet="feat(navigate): tap to avoid",
              metadata={"number": 1}),
        Event(ts="2026-05-13T11:00:00Z", actor="user", project="maps", session_id="s2",
              source_system="claude_transcript", source_machine="mm",
              kind="user_message", seq=2, content_snippet="more navigate work"),
        Event(ts="2026-05-13T11:30:00Z", actor="artifact", project="maps", session_id="gh",
              source_system="github", source_machine="mm",
              kind="pr_merged", seq=3, content_snippet="fix(navigate): reroute glitch",
              metadata={"number": 2}),
    ]
    t_a.event_ids = [0, 1]
    t_b.event_ids = [2, 3]
    epics = roll_up_epics([t_a, t_b], events)
    assert len(epics) == 1
    assert "navigate" in epics[0].scopes


def test_shared_generic_scope_does_not_cluster():
    """Generic scopes (ui:, app:, ...) are too coarse to link."""
    t_a = Task(task_id="a", project="maps", title="alpha thing")
    t_a.first_event_ts = _parse_ts("2026-05-11T10:00:00Z")
    t_a.last_event_ts = _parse_ts("2026-05-11T12:00:00Z")
    t_b = Task(task_id="b", project="maps", title="beta thing")
    t_b.first_event_ts = _parse_ts("2026-05-13T10:00:00Z")
    t_b.last_event_ts = _parse_ts("2026-05-13T12:00:00Z")
    events = [
        Event(ts="2026-05-11T11:00:00Z", actor="user", project="maps", session_id="s",
              source_system="claude_transcript", source_machine="mm",
              kind="user_message", seq=0, content_snippet="alpha thing"),
        Event(ts="2026-05-11T11:30:00Z", actor="artifact", project="maps", session_id="gh",
              source_system="github", source_machine="mm",
              kind="pr_merged", seq=1, content_snippet="feat(ui): alpha", metadata={"number": 1}),
        Event(ts="2026-05-13T11:00:00Z", actor="user", project="maps", session_id="s2",
              source_system="claude_transcript", source_machine="mm",
              kind="user_message", seq=2, content_snippet="beta thing"),
        Event(ts="2026-05-13T11:30:00Z", actor="artifact", project="maps", session_id="gh",
              source_system="github", source_machine="mm",
              kind="pr_merged", seq=3, content_snippet="fix(ui): beta", metadata={"number": 2}),
    ]
    t_a.event_ids = [0, 1]
    t_b.event_ids = [2, 3]
    epics = roll_up_epics([t_a, t_b], events)
    assert len(epics) == 2


# --- transitive clustering ---


def test_transitive_topical_chain_clusters():
    """A~B and B~C via topical overlap in same project → A,B,C one epic."""
    tasks, events = _assemble(
        _task("a", "maps", "render route polyline overlay"),
        _task("b", "maps", "render route polyline tooltip", first="2026-05-12T10:00:00Z"),
        _task("c", "maps", "route polyline tooltip color fix", first="2026-05-13T10:00:00Z"),
    )
    epics = roll_up_epics(tasks, events)
    assert len(epics) == 1
    assert set(epics[0].member_task_ids) == {"a", "b", "c"}


def test_multi_ref_task_does_not_bridge_distinct_families():
    """A task citing two tickets joins its DOMINANT ticket family — it must NOT
    transitively fuse two otherwise-disjoint features into one mega-epic.

    CLA-1 tags 3 tasks (a, b, bridge); CLA-2 tags 2 (bridge, c). The bridge
    task carries both. Dominance: CLA-1 (3) > CLA-2 (2), so bridge joins the
    CLA-1 family, leaving c as its own CLA-2 epic.
    """
    tasks, events = _assemble(
        _task("a", "forms", "alpha", issue_refs={"CLA-1"}),
        _task("b", "forms", "beta", issue_refs={"CLA-1"}, first="2026-05-12T10:00:00Z"),
        _task("bridge", "forms", "planning note", issue_refs={"CLA-1", "CLA-2"},
              first="2026-05-13T10:00:00Z"),
        _task("c", "maps", "map work", issue_refs={"CLA-2"}, first="2026-05-14T10:00:00Z"),
    )
    epics = roll_up_epics(tasks, events)
    fam1 = _epic_of(epics, "a")
    assert set(fam1.member_task_ids) == {"a", "b", "bridge"}
    fam2 = _epic_of(epics, "c")
    assert fam2.member_task_ids == ["c"]
    assert fam1 is not fam2


# --- guardrails ---


def test_size_cap_guardrail_trips(caplog):
    """An over-large component (> EPIC_MAX_MEMBERS sharing one ref) is demoted
    to singleton epics, with a warning."""
    n = EPIC_MAX_MEMBERS + 5
    pairs = [
        _task(f"t{i}", "forms", f"task {i}", issue_refs={"CLA-9999"},
              first=f"2026-05-{11 + (i % 10):02d}T10:00:00Z")
        for i in range(n)
    ]
    tasks, events = _assemble(*pairs)
    with caplog.at_level(logging.WARNING):
        epics = roll_up_epics(tasks, events)
    # Demoted: every member is its own epic, none merged.
    assert len(epics) == n
    assert all(e.member_count == 1 for e in epics)
    assert any("guardrail tripped" in r.message for r in caplog.records)


def test_window_spanning_many_projects_guardrail(caplog):
    """A component spanning the whole window across >5 projects via shared ref
    is demoted (pathological cross-project sprawl)."""
    # 6 projects, all sharing one ref, spanning the full window.
    pairs = []
    days = ["2026-05-11T10:00:00Z", "2026-05-25T10:00:00Z"]
    for i in range(6):
        pairs.append(
            _task(f"p{i}a", f"proj{i}", f"alpha {i}", issue_refs={"CLA-8888"},
                  first=days[0], last=days[0])
        )
    # one late task to force whole-window span
    pairs.append(
        _task("late", "proj0", "late work", issue_refs={"CLA-8888"},
              first=days[1], last=days[1])
    )
    tasks, events = _assemble(*pairs)
    with caplog.at_level(logging.WARNING):
        epics = roll_up_epics(tasks, events)
    # All demoted to singletons.
    assert len(epics) == len(pairs)
    assert all(e.member_count == 1 for e in epics)
    assert any("guardrail tripped" in r.message for r in caplog.records)


# --- shape / singletons ---


def test_singleton_task_is_its_own_epic():
    tasks, events = _assemble(_task("solo", "maps", "a one-off fix"))
    epics = roll_up_epics(tasks, events)
    assert len(epics) == 1
    assert epics[0].member_count == 1
    assert epics[0].member_task_ids == ["solo"]


def test_empty_input():
    assert roll_up_epics([], []) == []


def test_rollup_preserves_all_tasks():
    """Every atomic task lands in exactly one epic."""
    tasks, events = _assemble(
        _task("a", "forms", "scan form", issue_refs={"CLA-2435"}),
        _task("b", "forms", "form again", issue_refs={"CLA-2435"}, first="2026-05-12T10:00:00Z"),
        _task("c", "maps", "route overlay"),
        _task("d", "unknown", "misc"),
    )
    epics = roll_up_epics(tasks, events)
    seen = [tid for e in epics for tid in e.member_task_ids]
    assert sorted(seen) == ["a", "b", "c", "d"]
    assert len(seen) == len(set(seen))  # no task in two epics


def test_epic_rollup_totals():
    """Rolled-up totals sum member events / user messages."""
    tasks, events = _assemble(
        _task("a", "forms", "scan form", issue_refs={"CLA-2435"}, n_events=3, n_user=2),
        _task("b", "forms", "form again", issue_refs={"CLA-2435"},
              first="2026-05-12T10:00:00Z", n_events=2, n_user=1),
    )
    epics = roll_up_epics(tasks, events)
    assert len(epics) == 1
    assert epics[0].total_events == 5
    assert epics[0].total_user_messages == 3


def test_title_derives_from_dominant_issue_ref():
    tasks, events = _assemble(
        _task("a", "forms", "small task", issue_refs={"CLA-2435"}, n_events=1),
        _task("b", "forms", "the big detection feature", issue_refs={"CLA-2435"},
              first="2026-05-12T10:00:00Z", n_events=10),
    )
    epics = roll_up_epics(tasks, events)
    assert epics[0].title.startswith("CLA-2435")
    # tail comes from the busiest member
    assert "detection" in epics[0].title


def test_sorted_by_total_events_desc():
    tasks, events = _assemble(
        _task("small", "maps", "tiny", n_events=1),
        _task("a", "forms", "scan form", issue_refs={"CLA-2435"}, n_events=5),
        _task("b", "forms", "form again", issue_refs={"CLA-2435"},
              first="2026-05-12T10:00:00Z", n_events=5),
    )
    epics = roll_up_epics(tasks, events)
    assert epics[0].total_events >= epics[-1].total_events
    assert epics[0].member_count == 2  # the CLA epic (10 events) leads


def test_roll_up_epics_llm_groups_by_returned_assignments():
    """LLM clustering groups atomic tasks per the model's epic assignments and
    backfills any dropped task as its own singleton."""
    from types import SimpleNamespace
    from weekly_review.epics import roll_up_epics_llm

    tasks, events = _assemble(
        _task("a", "client-a", "forms work"),
        _task("b", "client-a", "more forms", first="2026-05-13T10:00:00Z"),
        _task("c", "client-a", "maps work", first="2026-05-12T10:00:00Z"),
    )
    enriched = {t.task_id: SimpleNamespace(title=t.title, est_human_hours=2.0) for t in tasks}

    class FakeLLM:
        def complete(self, **kw):
            # group sessions 0+1 into one epic, leave 2 to be backfilled
            return {"text": '{"epics": [{"title": "Forms epic", "members": [0, 1]}]}'}

    epics = roll_up_epics_llm(tasks, enriched, events, FakeLLM(), model="x")
    # Every task lands in exactly one epic.
    all_members = [m for e in epics for m in e.member_task_ids]
    assert len(all_members) == len(tasks)
    assert len(set(all_members)) == len(tasks)
    # The 2-member epic exists and is titled by the LLM.
    multi = [e for e in epics if e.member_count == 2]
    assert len(multi) == 1
    assert multi[0].title == "Forms epic"


def test_roll_up_epics_llm_falls_back_on_llm_error():
    from types import SimpleNamespace
    from weekly_review.epics import roll_up_epics_llm

    tasks, events = _assemble(_task("a", "client-a", "forms work"))
    enriched = {t.task_id: SimpleNamespace(title=t.title, est_human_hours=2.0) for t in tasks}

    class BoomLLM:
        def complete(self, **kw):
            raise RuntimeError("api down")

    # Falls back to mechanical roll_up_epics — still returns one epic per task.
    epics = roll_up_epics_llm(tasks, enriched, events, BoomLLM(), model="x")
    assert len(epics) == len(tasks)
