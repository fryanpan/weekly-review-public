"""A doc with no board and no sourceUrl still knows which directory made it.

Regression for the week of 2026-08-31: 1.6h (14% of hands-on) landed in
`unknown`, all of it fast build diff review on the one goal the number was
being computed for. `create_diff_review` had been called without a board, so
`meta.workspaceId` held the *setId* rather than a `w-…` board and board routing
missed; a diff legitimately has no sourceUrl, so path routing missed too.

Every one of those docs names its producing worktree in `meta.owner`.
"""

import json

from weekly_review.extractors.live_feedback import _route_doc, load_doc_roots


def _write_index(root, doc_id, meta):
    (root / f"{doc_id}.index.json").write_text(json.dumps({"v": 1, "meta": meta}))


def test_owner_beats_workspace_root(tmp_path):
    """A doc made from a scratchpad must not route on the scratchpad path."""
    _write_index(tmp_path, "d-scratch", {
        "docId": "d-scratch",
        "owner": "/Users/x/dev/client-a",
        "workspaceRoot": "/private/tmp/claude-501/some-session/scratchpad/cut2",
    })

    assert load_doc_roots(tmp_path)["d-scratch"] == "/Users/x/dev/client-a"


def test_archived_docs_are_included(tmp_path):
    (tmp_path / "_archive").mkdir()
    _write_index(tmp_path, "d-live", {"docId": "d-live", "owner": "/Users/x/dev/a"})
    _write_index(tmp_path / "_archive", "d-old", {"docId": "d-old", "owner": "/Users/x/dev/b"})

    roots = load_doc_roots(tmp_path)
    assert roots == {"d-live": "/Users/x/dev/a", "d-old": "/Users/x/dev/b"}


def test_a_missing_data_dir_is_empty_not_an_error(tmp_path):
    assert load_doc_roots(tmp_path / "nope") == {}


class _Projects:
    """Minimal stand-in: only the worktree path resolves, as in the real bug."""

    projects = ()

    def slug_for(self, path):
        if path.startswith("/Users/x/dev/wrapper-project"):
            return "client-a"
        return "unknown"


def test_a_boardless_diff_routes_on_its_producing_directory():
    # What the diff docs actually look like: no sourceUrl, no board, and a
    # doc_id whose only recognisable token is the set name.
    project = _route_doc(
        _Projects(), source_url="", doc_id="bench-pr2-harness-0901:harness~run_record.py",
        title="harness/run_record.py", workspace_name="",
        doc_root="/Users/x/dev/wrapper-project/worktrees/client-a-app/client-a-fast-build",
    )

    assert project == "client-a"


def test_without_the_root_the_same_doc_is_unroutable():
    """Guards the claim above: the fix is the root, not something else in the id."""
    assert _route_doc(
        _Projects(), source_url="", doc_id="bench-pr2-harness-0901:harness~run_record.py",
        title="harness/run_record.py", workspace_name="", doc_root="",
    ) == "unknown"
