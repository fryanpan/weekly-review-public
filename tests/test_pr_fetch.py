"""The PR fetch must return every PR in the window, and a run must say when it did not.

`gh pr list --limit 200` returned a repo's newest 200 PRs, so the Sep 1-7 run
held 15 of the 262 PRs claude-workspaces-plugin merged that week and every
audit passed.
"""
import json
import re
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from weekly_review.extractors import github_pr
from weekly_review.extractors.github_pr import extract_github_prs, fetch_prs
from weekly_review.persist import init_schema
from weekly_review.validate import pr_fetch_problems, pr_mentions_missing
from tests.fakeids import tid

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _pr(n, updated, created=None, merged=None):
    iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ") if d else None
    return {"number": n, "title": f"PR {n}", "createdAt": iso(created or updated),
            "mergedAt": iso(merged), "updatedAt": iso(updated), "state": "MERGED",
            "author": {"login": "arivera"}, "additions": 1, "deletions": 0,
            "changedFiles": 1, "url": f"https://github.com/o/r/pull/{n}"}


def _fake_gh(monkeypatch, prs, calls=None):
    """A gh that honours `updated:A..B` and returns at most --limit results, newest first."""
    def run(cmd, *a, **k):
        if calls is not None:
            calls.append(cmd)
        lo, hi = re.search(r"updated:(\S+)\.\.(\S+)", cmd[cmd.index("--search") + 1]).groups()
        limit = int(cmd[cmd.index("--limit") + 1])
        hit = [p for p in prs if lo <= p["updatedAt"] <= hi]
        hit.sort(key=lambda p: p["updatedAt"], reverse=True)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(hit[:limit]), stderr="")
    monkeypatch.setattr(subprocess, "run", run)


def test_a_window_larger_than_one_page_is_fetched_whole(monkeypatch):
    prs = [_pr(n, T0 + timedelta(minutes=7 * n)) for n in range(1, 251)]
    _fake_gh(monkeypatch, prs)
    stats = {}
    got = fetch_prs("o/r", "2026-09-01T00:00:00Z", until=T0 + timedelta(days=3), limit=40, stats=stats)
    assert [p["number"] for p in got] == list(range(1, 251))
    assert stats["truncated"] == [] and stats["failed"] is False
    assert stats["queries"] > 1


def test_a_pr_opened_before_the_window_and_merged_in_it_is_kept(monkeypatch):
    old = T0 - timedelta(days=30)
    prs = [_pr(7, updated=T0 + timedelta(days=1), created=old, merged=T0 + timedelta(hours=2))]
    _fake_gh(monkeypatch, prs)
    events = list(extract_github_prs("o/r", "p", "2026-09-01T00:00:00Z", "mm"))
    assert [e.kind for e in events] == ["pr_merged"]


def test_a_range_still_full_at_minimum_width_is_reported_truncated(monkeypatch, capsys):
    same = T0 + timedelta(hours=1)
    _fake_gh(monkeypatch, [_pr(n, same) for n in range(1, 31)])
    stats = {}
    fetch_prs("o/r", "2026-09-01T00:00:00Z", until=T0 + timedelta(hours=2), limit=10, stats=stats)
    assert stats["truncated"], "a cut page must not read as a complete fetch"
    assert "WARNING" in capsys.readouterr().err


def test_a_failed_query_is_reported(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, *a, **k: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom"))
    stats = {}
    assert fetch_prs("o/r", "2026-09-01T00:00:00Z", stats=stats) == []
    assert stats["failed"] is True


def test_a_pr_keeps_its_id_when_other_prs_appear(monkeypatch):
    a = _pr(5, T0 + timedelta(hours=1), merged=T0 + timedelta(hours=1))
    _fake_gh(monkeypatch, [a])
    first = {e.kind: e.event_id for e in extract_github_prs("o/r", "p", "2026-09-01T00:00:00Z", "mm")}
    _fake_gh(monkeypatch, [_pr(n, T0 + timedelta(hours=n)) for n in (2, 3)] + [a])
    second = {e.kind: e.event_id for e in extract_github_prs("o/r", "p", "2026-09-01T00:00:00Z", "mm")
              if e.metadata["number"] == 5}
    assert first == second
    assert first["pr_merged"] != first["pr_opened"]


def test_same_pr_under_two_slugs_is_one_identity(monkeypatch):
    _fake_gh(monkeypatch, [_pr(5, T0 + timedelta(hours=1))])
    a = next(extract_github_prs("O/R", "slug-a", "2026-09-01T00:00:00Z", "mm")).event_id
    b = next(extract_github_prs("o/r", "slug-b", "2026-09-01T00:00:00Z", "mm")).event_id
    assert a == b


def test_cli_records_the_fetch_for_the_audits():
    src = (Path(github_pr.__file__).parents[1] / "cli.py").read_text()
    assert "stats=fetch_stats" in src
    assert '"pr_fetch.json"' in src


# --- audits -----------------------------------------------------------------

def _db():
    c = sqlite3.connect(":memory:")
    init_schema(c)
    return c


_n = iter(range(100_000))


def _ev(c, actor, kind, session, snippet="", **md):
    n = next(_n)
    c.execute("""INSERT INTO events (event_id, ts, actor, project, session_id, source_system, source_machine,
                 kind, seq, content_snippet, metadata_json) VALUES (?, '2026-09-01T12:00:00Z', ?, 'p', ?,
                 'x', 'm', ?, ?, ?, ?)""", (f"e{n}", actor, session, kind, n, snippet, json.dumps(md)))


def _links(c, repo, numbers):
    for n in numbers:
        _ev(c, "agent", "agent_message", "s", f"merged https://github.com/{repo}/pull/{n} ok")


def test_mention_audit_flags_a_repo_missing_most_linked_prs():
    c = _db()
    for n in range(1, 11):
        _ev(c, "artifact", "pr_merged", "github:o/big", number=n)
    _links(c, "o/big", range(1, 80))
    for n in range(1, 31):
        _ev(c, "artifact", "pr_merged", "github:o/fine", number=n)
    _links(c, "o/fine", range(1, 61))   # exactly half missing: not most
    _ev(c, "artifact", "pr_merged", "github:o/small", number=1)
    _links(c, "o/small", range(1, 40))  # too few links to speak for
    _links(c, "x/unfetched", range(1, 90))
    assert pr_mentions_missing(c) == [("o/big", 79, 69)]


def test_mention_audit_sees_a_fetched_repo_that_ingested_nothing():
    c = _db()
    _links(c, "o/empty", range(1, 60))
    assert pr_mentions_missing(c) == []
    assert pr_mentions_missing(c, ["O/Empty"]) == [("o/empty", 59, 59)]


def test_fetch_record_audit(tmp_path):
    assert pr_fetch_problems(tmp_path) is None
    (tmp_path / "pr_fetch.json").write_text(json.dumps([
        {"repo": "o/ok", "truncated": [], "failed": False},
        {"repo": "o/cut", "truncated": ["a..b"], "failed": False},
        {"repo": "o/down", "truncated": [], "failed": True},
    ]))
    assert pr_fetch_problems(tmp_path) == ["o/cut: 1 range(s) at the result cap", "o/down: fetch failed"]


def test_a_pr_carries_the_board_task_ids_its_body_and_branch_name_and_never_the_body(monkeypatch):
    """Method Step 5: a PR whose body or branch names a `t-` id links its unit
    to that board task. The body is never stored (it can quote anything)."""
    pr = _pr(5, T0 + timedelta(hours=1), merged=T0 + timedelta(hours=1))
    pr["body"] = "Closes " + tid("89YzAbCdE012") + ". Secret plan text.\nSee also t-page-bar (a slug, not an id)."
    pr["headRefName"] = tid("FgH3iJkLmNoP-goal-links")
    _fake_gh(monkeypatch, [pr])
    evs = list(extract_github_prs("o/r", "p", "2026-09-01T00:00:00Z", "mm"))
    md = evs[0].metadata
    assert md["named_tasks"] == [tid("89YzAbCdE012"), tid("FgH3iJkLmNoP")]
    assert "head_ref" not in md, "the branch name is read for ids and not stored"
    assert "Secret" not in json.dumps([e.metadata for e in evs]) + "".join(e.content_snippet for e in evs)
    assert github_pr.named_board_tasks({"body": None, "headRefName": None}) == []
