"""Board-first routing, and the container test that keeps it honest.

The user's 2026-09-08 ruling: the board outranks the file path. The whole risk in
that ruling is a board that is a PLACE rather than a project -- a lead's board,
a holding pen -- because it would drag other projects' work onto itself. The
container test is what stops that, so most of these tests are about a board
declining to answer.
"""
from __future__ import annotations

import types

from weekly_review.extractors.live_feedback import (
    NO_TASK,
    _route_doc,
    _task_stamp,
    load_board_projects,
    workspace_task_id,
)
from tests.fakeids import did


class _Projects:
    """Minimal stand-in: a slug is whatever directory segment the path holds."""

    def __init__(self, slugs):
        self.projects = [types.SimpleNamespace(slug=s) for s in slugs]

    def slug_for(self, path: str) -> str:
        for p in self.projects:
            if f"/{p.slug}" in (path or ""):
                return p.slug
        return "unknown"


PROJECTS = _Projects(["client-a", "ai-team-lead", "weekly-review",
                      "claude-live-feedback-plugin"])


def test_a_single_project_board_names_its_project():
    boards = {"d1": "live-feedback", "d2": "live-feedback"}
    roots = {"d1": "/dev/claude-live-feedback-plugin",
             "d2": "/dev/claude-live-feedback-plugin/worktrees/x"}

    assert load_board_projects(PROJECTS, boards, roots) == {
        "live-feedback": "claude-live-feedback-plugin"}


def test_a_board_holding_two_projects_names_none():
    """`Lead Agent` holds ai-team-lead, client-a, contacts-tool and
    weekly-review work. A board like that must abstain, not pick its majority --
    a majority vote here is how the 7 known-bad docs would move."""
    boards = {"d1": "Lead Agent", "d2": "Lead Agent"}
    roots = {"d1": "/dev/ai-team-lead", "d2": "/dev/client-a"}

    assert load_board_projects(PROJECTS, boards, roots) == {}


def test_the_board_beats_the_working_directory():
    board_projects = {"client-a-fast-build": "client-a"}

    assert _route_doc(
        PROJECTS, source_url="", doc_id="d1", title="",
        workspace_name="client-a-fast-build",
        doc_root="/dev/ai-team-lead", board_projects=board_projects,
    ) == "client-a"


def test_the_board_beats_a_file_path_source_url():
    """The ruling's literal wording: board before the file-path sourceUrl."""
    board_projects = {"client-a-fast-build": "client-a"}

    assert _route_doc(
        PROJECTS, source_url="/dev/ai-team-lead/docs/plan.md", doc_id="d1",
        title="", workspace_name="client-a-fast-build", doc_root="",
        board_projects=board_projects,
    ) == "client-a"


def test_a_container_board_lets_the_working_directory_answer():
    """`weekly-review-methodology` sits on the lead agent and was written in the
    weekly-review worktree. Board-first must not take it to ai-team-lead."""
    assert _route_doc(
        PROJECTS, source_url="", doc_id="weekly-review-methodology", title="",
        workspace_name="Lead Agent",
        doc_root="/dev/weekly-review/.claude/worktrees/v1-impl",
        board_projects={},
    ) == "weekly-review"


def test_a_board_named_exactly_after_a_slug_still_routes_with_no_roots():
    """A board whose docs carry no working directory has no derived mapping;
    the exact-name rule is what covers it."""
    assert _route_doc(
        PROJECTS, source_url="", doc_id="d1", title="",
        workspace_name="weekly-review", doc_root="", board_projects={},
    ) == "weekly-review"


def test_a_doc_on_no_board_still_routes_by_path():
    assert _route_doc(
        PROJECTS, source_url="", doc_id="d1", title="", workspace_name="",
        doc_root="/dev/client-a", board_projects={},
    ) == "client-a"


def test_nothing_anywhere_is_unknown_not_a_guess():
    assert _route_doc(
        PROJECTS, source_url="", doc_id="d1", title="", workspace_name="",
        doc_root="", board_projects={},
    ) == "unknown"


def test_a_task_card_stamps_its_task_id():
    assert workspace_task_id("task:t-abc") == "t-abc"
    assert _task_stamp("task:t-abc") == {"task_id": "t-abc",
                                         "task_resolution": "task-card"}


def test_a_non_task_doc_stamps_an_explicit_no_task_marker():
    """Never merely absent. An absent field cannot be told apart from an
    extractor that forgot to look."""
    stamp = _task_stamp(did("Qr4StUvWxYz5"))

    assert stamp == {"task_resolution": NO_TASK}
    assert "task_id" not in stamp


def test_a_board_is_never_guessed_from_a_docs_mere_presence_on_it():
    """The board tells you the project, never the task."""
    assert workspace_task_id(did("Qr4StUvWxYz5")) == ""
    assert workspace_task_id("") == ""


def test_a_doc_one_task_links_is_that_tasks_and_a_doc_many_link_is_nobodys():
    """The user, 2026-09-17: a comment on a doc or mockup linked to exactly one
    board task goes to that task. Linked by several, it is a candidate list
    for the classifier, never a guess. The task card still wins."""
    links = {"d-one": frozenset({"t-1"}), "d-many": frozenset({"t-1", "t-2"})}

    assert _task_stamp("d-one", links) == {"task_id": "t-1",
                                           "task_resolution": "task-link"}
    assert _task_stamp("d-many", links) == {"task_resolution": "multi-task",
                                            "task_candidates": ["t-1", "t-2"]}
    assert _task_stamp("d-none", links) == {"task_resolution": NO_TASK}
    assert _task_stamp("task:t-9", links)["task_resolution"] == "task-card"
    assert _task_stamp("d-one") == {"task_resolution": NO_TASK}


def test_doc_task_links_and_goals_come_from_every_boards_task_store(tmp_path):
    import json
    from weekly_review.extractors.workspace_board import (
        board_task_goals, doc_task_links,
    )
    (tmp_path / "workspaces").mkdir()
    (tmp_path / "workspaces" / "w-a.tasks.json").write_text(json.dumps({
        "workspace": {"id": "w-a", "name": "A"},
        "tasks": [
            {"id": "t-1", "goal": "g-1", "links": [
                {"kind": "doc", "docId": "d-one"},
                {"kind": "doc", "docId": "d-many"},
                {"kind": "url", "url": "https://example.test"}]},
            {"id": "t-2", "goal": "g-1", "links": [
                {"kind": "thread", "docId": "d-many", "threadId": "x"}]},
            {"id": "t-3", "goal": "", "links": []},
        ]}))
    gone = tmp_path / "_archive" / "b1"
    gone.mkdir(parents=True)
    (gone / "w-gone.tasks.json").write_text(json.dumps({
        "workspace": {"id": "w-gone", "name": "Gone"},
        "tasks": [{"id": "t-4", "goal": "g-9",
                   "links": [{"kind": "doc", "docId": "d-arch"}]}]}))

    assert doc_task_links(tmp_path) == {"d-one": frozenset({"t-1"}),
                                        "d-many": frozenset({"t-1", "t-2"}),
                                        "d-arch": frozenset({"t-4"})}
    assert board_task_goals(tmp_path) == {"t-1": "g-1", "t-2": "g-1",
                                          "t-4": "g-9"}
