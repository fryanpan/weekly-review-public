import subprocess
from weekly_review.extractors.git_log import extract_git_log
from weekly_review.extractors.github_pr import extract_github_prs


M = "\x01commit\x01"  # header sentinel used by extract_git_log --numstat parsing


def test_git_log_parses_commits_with_diffstat(monkeypatch):
    # `git log --numstat` interleaves a header line with per-file `add\tdel\tpath`
    # rows. The diffstat is the ground truth the estimator's countables derive
    # from, so it has to survive parsing.
    sample = (
        f"{M}abc1234|2026-04-15T10:00:00-07:00|Alex Rivera|Initial commit\n"
        "10\t2\tsrc/a.py\n"
        "5\t0\tsrc/b.py\n"
        f"{M}def5678|2026-04-16T11:20:00-07:00|Alex Rivera|Add config loader\n"
        "40\t3\tsrc/config.py\n"
        "-\t-\tassets/logo.png\n"  # binary: counts as a file, no lines
    )
    def fake_run(cmd, *args, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=sample, stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    events = list(extract_git_log(
        repo_path="/tmp/fake", project="weekly-review",
        since="2026-04-01", source_machine="mac_mini"
    ))
    assert len(events) == 2
    assert events[0].kind == "commit"
    assert events[0].metadata["short_sha"] == "abc1234"
    assert events[0].metadata["files_changed"] == 2
    assert events[0].metadata["insertions"] == 15
    assert events[0].metadata["deletions"] == 2

    assert events[1].content_snippet == "Add config loader"
    assert events[1].metadata["files_changed"] == 2      # binary file still counts
    assert events[1].metadata["insertions"] == 40        # but adds no lines
    assert events[1].metadata["deletions"] == 3


def test_git_log_diffstat_exclude_drops_checked_in_data_but_reports_it(monkeypatch):
    # One commit of 275 scraped records under data/ plus a two-line page edit
    # must read as the two-line edit. The excluded bulk rides along so the
    # prompt can say what was set aside — and a commit that touches no
    # excluded path carries no excluded_* keys at all (metadata unchanged).
    sample = (
        f"{M}c8f069f|2026-09-07T08:40:47-07:00|Alex Rivera|Front page rebuilt\n"
        "118000\t0\tdata/legistar/2026-09/records.json\n"
        "600\t90\tdata/legistar/index.json\n"
        "-\t-\tdata/legistar/blob.bin\n"
        "2\t1\ttopics/sf-permitting.md\n"
        f"{M}16008e5|2026-09-05T12:29:13-07:00|Alex Rivera|Notes\n"
        "40\t3\tnotes/sf-permits.md\n"
    )
    def fake_run(cmd, *args, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=sample, stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    events = list(extract_git_log(
        repo_path="/tmp/fake", project="client-b",
        since="2026-09-01", source_machine="mac_mini",
        diffstat_exclude=["data/*"],
    ))
    big, small = events
    assert big.metadata["files_changed"] == 1
    assert big.metadata["insertions"] == 2
    assert big.metadata["deletions"] == 1
    assert big.metadata["excluded_files"] == 3
    assert big.metadata["excluded_insertions"] == 118600
    assert "excluded_files" not in small.metadata
    assert small.metadata["insertions"] == 40


def test_git_log_without_exclude_is_unchanged(monkeypatch):
    # No patterns: same numbers as before the field existed, no new keys.
    sample = (
        f"{M}abc1234|2026-04-15T10:00:00-07:00|Alex Rivera|x\n"
        "10\t2\tdata/a.json\n"
    )
    def fake_run(cmd, *args, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=sample, stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    (ev,) = extract_git_log(repo_path="/tmp/fake", project="p",
                            since="2026-04-01", source_machine="m")
    assert ev.metadata["insertions"] == 10 and "excluded_files" not in ev.metadata


def test_git_log_commit_with_no_file_changes(monkeypatch):
    # An empty/merge commit yields a header with no numstat rows — must not crash
    # and must report a zero stat rather than inheriting the previous commit's.
    sample = (
        f"{M}abc1234|2026-04-15T10:00:00-07:00|Alex Rivera|Real commit\n"
        "10\t2\tsrc/a.py\n"
        f"{M}def5678|2026-04-16T11:20:00-07:00|Alex Rivera|Merge branch 'x'\n"
    )
    def fake_run(cmd, *args, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=sample, stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    events = list(extract_git_log(
        repo_path="/tmp/fake", project="weekly-review",
        since="2026-04-01", source_machine="mac_mini"
    ))
    assert len(events) == 2
    assert events[1].metadata["files_changed"] == 0
    assert events[1].metadata["insertions"] == 0


def test_github_pr_parses_gh_output(monkeypatch):
    gh_json = (
        '[{"number":42,"title":"Add X","createdAt":"2026-04-15T10:00:00Z",'
        '"mergedAt":"2026-04-16T12:00:00Z","state":"MERGED","author":{"login":"arivera"},'
        '"additions":120,"deletions":30,"changedFiles":5,"url":"https://github.com/fryanpan/weekly-review/pull/42"}]'
    )
    def fake_run(cmd, *args, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=gh_json, stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    events = list(extract_github_prs(
        repo="fryanpan/weekly-review", project="weekly-review",
        since="2026-04-01T00:00:00Z", source_machine="mac_mini",
    ))
    kinds = [e.kind for e in events]
    assert "pr_opened" in kinds
    assert "pr_merged" in kinds
    merged = next(e for e in events if e.kind == "pr_merged")
    assert merged.metadata["additions"] == 120
    assert merged.metadata["url"].endswith("/pull/42")


def test_github_pr_filters_by_author(monkeypatch):
    # Shared repo: the user's PR plus two teammates'. Only the user's is theirs.
    gh_json = (
        '[{"number":42,"title":"Mine","createdAt":"2026-04-15T10:00:00Z",'
        '"mergedAt":"2026-04-16T12:00:00Z","state":"MERGED","author":{"login":"arivera"},'
        '"additions":10,"deletions":0,"changedFiles":1,"url":"https://x/pull/42"},'
        '{"number":43,"title":"Theirs","createdAt":"2026-04-15T10:00:00Z",'
        '"mergedAt":"2026-04-16T12:00:00Z","state":"MERGED","author":{"login":"reviewer-one-ca"},'
        '"additions":9999,"deletions":0,"changedFiles":108,"url":"https://x/pull/43"},'
        '{"number":44,"title":"AlsoTheirs","createdAt":"2026-04-15T10:00:00Z",'
        '"mergedAt":"2026-04-16T12:00:00Z","state":"MERGED","author":{"login":"reviewer-two-ca"},'
        '"additions":8421,"deletions":0,"changedFiles":108,"url":"https://x/pull/44"}]'
    )
    def fake_run(cmd, *args, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=gh_json, stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)

    # Filtered to the user: only PR #42 survives, in either state event.
    events = list(extract_github_prs(
        repo="org/shared", project="p", since="2026-04-01T00:00:00Z",
        source_machine="mm", authors=["arivera"],
    ))
    nums = {e.metadata["number"] for e in events}
    assert nums == {42}

    # Case-insensitive match on the login.
    events_ci = list(extract_github_prs(
        repo="org/shared", project="p", since="2026-04-01T00:00:00Z",
        source_machine="mm", authors=["AriVera"],
    ))
    assert {e.metadata["number"] for e in events_ci} == {42}

    # No filter (back-compat) keeps everyone — the pre-fix behavior.
    all_events = list(extract_github_prs(
        repo="org/shared", project="p", since="2026-04-01T00:00:00Z",
        source_machine="mm",
    ))
    assert {e.metadata["number"] for e in all_events} == {42, 43, 44}


def test_github_pr_filters_by_since(monkeypatch):
    gh_json = (
        '[{"number":1,"title":"Old","createdAt":"2025-01-01T00:00:00Z",'
        '"mergedAt":"2025-01-02T00:00:00Z","state":"MERGED","author":{"login":"x"},'
        '"additions":1,"deletions":0,"changedFiles":1,"url":"https://github.com/x/y/pull/1"}]'
    )
    def fake_run(cmd, *args, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=gh_json, stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    events = list(extract_github_prs(
        repo="x/y", project="p", since="2026-04-01T00:00:00Z", source_machine="mm"
    ))
    assert events == []
