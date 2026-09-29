"""The data dir must be resolved by currency, not by existence.

Regression for 2026-09-01: claude-workspaces moved its runtime data out of
`~/dev/claude-live-feedback-plugin/data` into Application Support. The old
directory was left behind, full of files frozen at the moment of the move, so
`DEFAULT_DATA_DIR.exists()` stayed true and every run afterwards read a dead
store. Review sessions and review comments both went to zero from 2026-09-02
onward with no error raised and no number missing from the output -- the week
of 2026-08-31 measured 9.7h hands-on with five of its seven days carrying no
live-feedback time at all.

A path that exists proves nothing about whether anything still writes to it.
"""

import os
import time

from weekly_review.extractors.live_feedback import resolve_data_dir


def _dir_with_activity(root, name, mtime):
    d = root / name
    d.mkdir(parents=True)
    act = d / "activity.jsonl"
    act.write_text("{}\n")
    os.utime(act, (mtime, mtime))
    return d


def test_the_newer_activity_stream_wins(tmp_path):
    now = time.time()
    old = _dir_with_activity(tmp_path, "repo-data", now - 30 * 86400)
    new = _dir_with_activity(tmp_path, "app-support-data", now - 60)

    # Order must not decide it: the stale one is listed first, as the real
    # candidate list lists the historical location alongside the current one.
    assert resolve_data_dir((old, new)) == new
    assert resolve_data_dir((new, old)) == new


def test_a_directory_that_exists_but_has_no_stream_loses(tmp_path):
    now = time.time()
    live = _dir_with_activity(tmp_path, "live", now - 60)
    empty = tmp_path / "empty"
    empty.mkdir()

    assert resolve_data_dir((empty, live)) == live


def test_missing_candidates_fall_back_rather_than_raise(tmp_path):
    gone = tmp_path / "nope"
    from weekly_review.extractors.live_feedback import DEFAULT_DATA_DIR

    assert resolve_data_dir((gone,)) == DEFAULT_DATA_DIR


def test_the_audit_asks_the_extractor_where_the_data_is(tmp_path):
    """Audit 22 must not hardcode its own copy of the path.

    It did, and so it was checking the freshness of a directory nothing wrote
    to -- the check that existed to catch this failure was reading the frozen
    copy. Assert the call site, since the defect was never in the function.
    """
    import inspect

    from weekly_review import validate

    src = inspect.getsource(validate)
    assert "resolve_data_dir()" in src, "audit must resolve the live data dir"
    assert "claude-live-feedback-plugin" not in src, (
        "audit must not hardcode the pre-2026-09-01 live-feedback data path"
    )
