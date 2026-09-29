"""One artifact identity → one task's est credit (the est_h double-count fix)."""
from weekly_review.artifact_credit import artifact_identity, assign_artifact_credit
from weekly_review.enrichment import _task_summary
from weekly_review.events import Event
from weekly_review.segmentation import Task


def _ev(kind, actor, seq, snippet="", project="p", meta=None):
    md = {"words": 3}
    if meta:
        md.update(meta)
    return Event(ts=f"2026-06-10T{seq:02d}:00:00Z", actor=actor, project=project,
                 session_id="s", source_system="x", source_machine="mm",
                 kind=kind, seq=seq, content_snippet=snippet, metadata=md)


def _pr(kind, seq, number, repo="arivera/family-bike-map", project="p", add=500, dele=100, files=12):
    return _ev(kind, "artifact", seq, f"feat: big change #{number}", project=project,
               meta={"number": number, "repo": repo, "additions": add,
                     "deletions": dele, "changedFiles": files})


def _commit(seq, sha, project="p", add=100, dele=10, files=3):
    return _ev("commit", "artifact", seq, "feat: change", project=project,
               meta={"short_sha": sha, "repo": project, "files_changed": files,
                     "insertions": add, "deletions": dele})


def test_identity_pr_opened_and_merged_share_one_identity():
    assert artifact_identity(_pr("pr_opened", 0, 59)) == artifact_identity(_pr("pr_merged", 1, 59))
    assert artifact_identity(_pr("pr_opened", 0, 59)) != artifact_identity(_pr("pr_opened", 0, 60))


def test_identity_matches_owner_slash_name_against_bare_slug():
    a = _pr("pr_merged", 0, 7, repo="arivera/health-tool")
    b = _pr("pr_merged", 1, 7, repo="health-tool")
    assert artifact_identity(a) == artifact_identity(b)


def test_identity_commit_keys_on_sha_across_project_slugs():
    # git extractor stamps metadata.repo with the PROJECT slug, so the same
    # repo registered under two slugs emits different repo strings — sha is
    # the only cross-slug-stable key.
    a = _commit(0, "abc1234", project="health-tool")
    b = _commit(1, "abc1234", project="rare-disease-benchmark")
    assert artifact_identity(a) == artifact_identity(b)


def test_ship_shard_loses_credit_to_build_task():
    """A 'merge it' shard holding pr_merged must not keep the build's diffstat."""
    events = [
        # Build task: real conversation + the pr_opened.
        _ev("user_message", "user", 0, "build the enriched tiles pipeline"),
        _ev("agent_message", "agent", 1, "built it"),
        _ev("agent_message", "agent", 2, "tests pass"),
        _pr("pr_opened", 3, 214),
        # Ship shard: one message + the pr_merged of the SAME PR.
        _ev("user_message", "user", 4, "merge it"),
        _pr("pr_merged", 5, 214),
    ]
    build = Task(task_id="build", project="p", title="build", event_ids=[0, 1, 2, 3])
    shard = Task(task_id="shard", project="p", title="merge it", event_ids=[4, 5])
    credit = assign_artifact_credit([build, shard], events)

    assert credit.owners[artifact_identity(events[3])] == "build"
    assert credit.duplicates_by_task == {"shard": {5}}
    assert credit.n_duplicate_placements == 1


def test_cross_slug_commit_echo_demoted_once():
    events = [
        _ev("user_message", "user", 0, "fix the portal download", project="health-tool"),
        _commit(1, "abc1234", project="health-tool"),
        _commit(2, "abc1234", project="rare-disease-benchmark"),
    ]
    conv = Task(task_id="conv", project="health-tool", title="fix", event_ids=[0, 1])
    echo = Task(task_id="echo", project="rare-disease-benchmark", title="echo", event_ids=[2])
    credit = assign_artifact_credit([conv, echo], events)
    assert credit.owners[artifact_identity(events[1])] == "conv"
    assert credit.duplicates_by_task == {"echo": {2}}


def test_single_holder_has_no_duplicates():
    events = [
        _ev("user_message", "user", 0, "do the thing"),
        _pr("pr_opened", 1, 9),
        _pr("pr_merged", 2, 9),
    ]
    t = Task(task_id="t", project="p", title="t", event_ids=[0, 1, 2])
    credit = assign_artifact_credit([t], events)
    assert credit.duplicates_by_task == {}
    assert credit.n_duplicate_placements == 0
    assert credit.owners[artifact_identity(events[1])] == "t"


def test_prompt_demotes_duplicate_artifacts_and_excludes_from_totals():
    events = [
        _ev("user_message", "user", 0, "merge it"),
        _pr("pr_merged", 1, 214, add=4800, dele=900, files=40),
        _commit(2, "ddd7777", add=20, dele=2, files=1),
    ]
    t = Task(task_id="shard", project="p", title="merge it", event_ids=[0, 1, 2])
    s = _task_summary(t, events, duplicate_artifact_ids={1})

    assert "CREDITED TO ANOTHER TASK" in s
    # The demoted PR is listed as context but its diffstat is out of the totals.
    assert "PR #214" in s
    assert "ARTIFACT TOTALS: 1 commit(s), 1 file-changes, +20/-2 lines" in s
    assert "+4800" not in s.split("CREDITED TO ANOTHER TASK")[0].split("ARTIFACT TOTALS")[1]


def test_totals_count_a_pr_once_even_with_opened_and_merged_events():
    events = [
        _ev("user_message", "user", 0, "build it"),
        _pr("pr_opened", 1, 9, add=300, dele=50, files=6),
        _pr("pr_merged", 2, 9, add=300, dele=50, files=6),
    ]
    t = Task(task_id="t", project="p", title="t", event_ids=[0, 1, 2])
    s = _task_summary(t, events)
    # Both lifecycle events listed, but the totals count the PR once.
    assert "[opened]" in s and "[merged]" in s
    assert "1 unique PR(s), 6 file-changes, +300/-50 lines" in s


def test_totals_warn_when_commits_overlap_a_pr():
    events = [
        _ev("user_message", "user", 0, "build it"),
        _pr("pr_merged", 1, 9, add=300, dele=50, files=6),
        _commit(2, "aaa1111", add=280, dele=40, files=5),
    ]
    t = Task(task_id="t", project="p", title="t", event_ids=[0, 1, 2])
    s = _task_summary(t, events)
    assert "SAME work stated twice" in s
    assert "NOT their sum" in s


# --- repo-keyed identity + commit↔PR unification (2026-07-29) ---------------

def test_squash_commit_unifies_with_its_pr_across_projects():
    """rare-disease-benchmark split out of health-tool: the PR lands under one
    project slug and the squash commit under another. Same artifact."""
    pr = _ev("pr_merged", "artifact", 0, "feat: add portal download",
             project="rare-disease-benchmark",
             meta={"number": 243, "repo": "arivera/health-tool",
                   "repo_name": "health-tool", "additions": 900,
                   "deletions": 20, "changedFiles": 12})
    commit = _ev("commit", "artifact", 1, "feat: add portal download (#243)",
                 project="health-tool",
                 meta={"short_sha": "abc9999", "repo": "health-tool",
                       "repo_name": "health-tool", "files_changed": 12,
                       "insertions": 900, "deletions": 20})
    assert artifact_identity(pr) == artifact_identity(commit) == "pr:health-tool:243"


def test_repo_name_beats_project_slug_for_identity():
    a = _ev("commit", "artifact", 0, "fix: thing", project="slug-one",
            meta={"short_sha": "aaa1", "repo": "slug-one", "repo_name": "real-repo"})
    from weekly_review.artifact_credit import event_repo
    assert event_repo(a) == "real-repo"


def test_event_repo_falls_back_for_legacy_runs():
    from weekly_review.artifact_credit import event_repo
    legacy = _ev("pr_merged", "artifact", 0, "x", project="p",
                 meta={"number": 1, "repo": "arivera/family-bike-map"})
    assert event_repo(legacy) == "family-bike-map"
    bare = _ev("commit", "artifact", 0, "x", project="proj", meta={"short_sha": "s"})
    assert event_repo(bare) == "proj"


def test_commit_without_pr_ref_still_keys_on_sha():
    c = _ev("commit", "artifact", 0, "chore: tidy up", project="p",
            meta={"short_sha": "def4444", "repo_name": "r"})
    assert artifact_identity(c) == "commit:def4444"


def test_cross_project_pr_and_commit_dedupe_to_one_owner():
    events = [
        _ev("user_message", "user", 0, "add the portal download", project="health-tool"),
        _ev("agent_message", "agent", 1, "built it", project="health-tool"),
        _ev("commit", "artifact", 2, "feat: portal download (#243)", project="health-tool",
            meta={"short_sha": "abc9999", "repo_name": "health-tool",
                  "files_changed": 12, "insertions": 900, "deletions": 20}),
        _ev("pr_merged", "artifact", 3, "feat: portal download",
            project="rare-disease-benchmark",
            meta={"number": 243, "repo_name": "health-tool", "additions": 900,
                  "deletions": 20, "changedFiles": 12}),
    ]
    conv = Task(task_id="conv", project="health-tool", title="build", event_ids=[0, 1, 2])
    shard = Task(task_id="shard", project="rare-disease-benchmark", title="pr", event_ids=[3])
    credit = assign_artifact_credit([conv, shard], events)
    assert credit.owners["pr:health-tool:243"] == "conv"
    assert credit.duplicates_by_task == {"shard": {3}}
