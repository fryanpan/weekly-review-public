"""Concurrent-slice (narrative-twin) detection and prompt disclosure."""
from datetime import datetime, timedelta, timezone

from weekly_review.enrichment import _task_summary
from weekly_review.events import Event
from weekly_review.segmentation import Task
from weekly_review.workstream import find_workstreams, sibling_block

T0 = datetime(2026, 7, 13, 9, 0, tzinfo=timezone.utc)


def _task(tid, project="p", start_min=0, dur_min=120, events=50, title=None):
    t = Task(task_id=tid, project=project, title=title or f"task {tid}",
             event_ids=list(range(events)))
    t.first_event_ts = T0 + timedelta(minutes=start_min)
    t.last_event_ts = t.first_event_ts + timedelta(minutes=dur_min)
    return t


def test_sequential_tasks_are_not_siblings():
    a = _task("a", start_min=0, dur_min=60)
    b = _task("b", start_min=60, dur_min=60)  # starts exactly when a ends
    ws = find_workstreams([a, b])
    assert ws.siblings == {}
    assert ws.n_groups == 0


def test_shard_inside_a_long_task_is_a_sibling():
    """The mega-task case: a short shard fully concurrent with a long task."""
    big = _task("big", start_min=0, dur_min=3 * 24 * 60, events=1000)
    shard = _task("shard", start_min=600, dur_min=90, events=80)
    ws = find_workstreams([big, shard])
    assert ws.siblings == {"big": ["shard"], "shard": ["big"]}
    assert ws.n_groups == 1


def test_split_session_pair_is_detected():
    """One evening cut into two tasks — the health-tool case."""
    a = _task("a", start_min=0, dur_min=180)
    b = _task("b", start_min=30, dur_min=150)
    ws = find_workstreams([a, b])
    assert sorted(ws.siblings) == ["a", "b"]


def test_different_projects_never_pair():
    a = _task("a", project="alpha", start_min=0, dur_min=180)
    b = _task("b", project="beta", start_min=0, dur_min=180)
    assert find_workstreams([a, b]).siblings == {}


def test_slight_overlap_below_threshold_is_ignored():
    # b starts 100 of its 120 minutes after a's start: overlap 20min = 17%.
    a = _task("a", start_min=0, dur_min=120)
    b = _task("b", start_min=100, dur_min=120)
    assert find_workstreams([a, b]).siblings == {}


def test_instantaneous_tasks_are_ignored():
    a = _task("a", start_min=0, dur_min=0)
    b = _task("b", start_min=0, dur_min=0)
    assert find_workstreams([a, b]).siblings == {}


def test_tasks_without_timestamps_are_skipped():
    a = Task(task_id="a", project="p", title="no ts", event_ids=[0])
    b = _task("b")
    assert find_workstreams([a, b]).siblings == {}


def test_sibling_block_ranks_by_size_and_caps():
    main = _task("main", start_min=0, dur_min=600)
    sibs = [_task(f"s{i}", start_min=10, dur_min=300, events=i * 10, title=f"sibling {i}")
            for i in range(1, 9)]
    ws = find_workstreams([main] + sibs)
    by_id = {t.task_id: t for t in [main] + sibs}
    lines = sibling_block(main, by_id, ws.siblings["main"])
    listed = [l for l in lines if l.startswith('- "')]
    assert len(listed) == 6                      # capped
    assert 'sibling 8' in listed[0]              # largest first
    assert 'sibling 1' not in "\n".join(listed)  # smallest dropped


def test_sibling_block_empty_without_siblings():
    assert sibling_block(_task("a"), {"a": _task("a")}, []) == []


def test_prompt_carries_the_do_not_reprice_instruction():
    ev = [Event(ts="2026-07-13T09:00:00Z", actor="user", project="p", session_id="s",
                source_system="x", source_machine="mm", kind="user_message", seq=0,
                content_snippet="merge it", metadata={"words": 2})]
    main = _task("main", start_min=0, dur_min=600, title="merge it")
    main.event_ids = [0]
    sib = _task("sib", start_min=10, dur_min=300, events=400, title="built the pipeline")
    ws = find_workstreams([main, sib])
    lines = sibling_block(main, {"main": main, "sib": sib}, ws.siblings["main"])
    s = _task_summary(main, ev, sibling_lines=lines)

    assert "CONCURRENT SLICES OF THE SAME WORKSTREAM" in s
    assert "built the pipeline" in s
    assert "Price ONLY the increment" in s
    assert "well under 1h" in s
    # Deferring must not zero out a task's own demonstrated effort.
    assert "FLOOR:" in s and "never price it below" in s.lower()
    # Sibling hours are deliberately NOT disclosed (anchoring risk).
    import re
    assert not re.search(r"\d+(\.\d+)?\s*h\b", s.split("CONCURRENT SLICES")[1].split("If THIS task")[0])


# --- owner invariant + repo scoping + zero-span pairing (2026-07-29) --------

def _art(seq, project="p", repo_name="r", number=None, sha=None, minute=0):
    md = {"words": 3, "repo_name": repo_name}
    if number is not None:
        md.update({"number": number, "additions": 900, "deletions": 20, "changedFiles": 12})
    if sha:
        md.update({"short_sha": sha, "files_changed": 12, "insertions": 900, "deletions": 20})
    return Event(ts=(T0 + timedelta(minutes=minute)).isoformat().replace("+00:00", "Z"),
                 actor="artifact", project=project, session_id="s", source_system="x",
                 source_machine="mm", kind="pr_merged" if number else "commit",
                 seq=seq, content_snippet="feat: thing", metadata=md)


def test_owner_is_the_artifact_holder_not_just_the_biggest():
    """The task holding the shipped diffstat must own the shared work — a
    deferral chain that leaves an artifact unowned is how PR #214 (+8,370
    lines) ended up priced by nobody."""
    events = [_art(0, number=243, minute=30)]
    holder = _task("holder", start_min=0, dur_min=600, events=1)
    holder.event_ids = [0]
    chatty = _task("chatty", start_min=10, dur_min=300, events=900)
    chatty.event_ids = list(range(1, 901))   # holds no artifact
    ws = find_workstreams([holder, chatty], events)
    assert ws.owners == {"holder"}


def test_owner_falls_back_to_event_count_without_artifacts():
    big = _task("big", start_min=0, dur_min=600, events=900)
    small = _task("small", start_min=10, dur_min=300, events=20)
    ws = find_workstreams([big, small], [])
    assert ws.owners == {"big"}


def test_one_owner_per_connected_group():
    a = _task("a", start_min=0, dur_min=600, events=500)
    b = _task("b", start_min=10, dur_min=300, events=300)
    c = _task("c", start_min=20, dur_min=200, events=100)
    ws = find_workstreams([a, b, c], [])
    assert len(ws.owners) == 1 and ws.owners == {"a"}


def test_owner_prompt_says_own_it_and_non_owner_says_defer():
    ev = [Event(ts="2026-07-13T09:00:00Z", actor="user", project="p", session_id="s",
                source_system="x", source_machine="mm", kind="user_message", seq=0,
                content_snippet="go", metadata={"words": 1})]
    owner = _task("owner", start_min=0, dur_min=600, events=900, title="built the pipeline")
    owner.event_ids = [0]
    shard = _task("shard", start_min=10, dur_min=300, events=5, title="merge it")
    shard.event_ids = [0]
    by_id = {"owner": owner, "shard": shard}
    ws = find_workstreams([owner, shard], [])

    o = _task_summary(owner, ev, sibling_lines=sibling_block(
        owner, by_id, ws.siblings["owner"], is_owner=True))
    assert "THIS TASK HOLDS THE ARTIFACTS" in o
    assert "counted nowhere" in o
    assert "not licence to size the whole" in o
    assert "Price ONLY the increment" not in o

    s = _task_summary(shard, ev, sibling_lines=sibling_block(
        shard, by_id, ws.siblings["shard"], is_owner=False))
    assert "Price ONLY the increment" in s
    assert "THIS TASK HOLDS THE ARTIFACTS" not in s


def test_repo_scope_pairs_tasks_across_project_slugs():
    """health-tool conversation ↔ rare-disease-benchmark PR shard: same repo,
    different project slugs, so project scoping is blind to the pair."""
    events = [_art(0, project="rare-disease-benchmark", repo_name="health-tool",
                   number=243, minute=30)]
    conv = _task("conv", project="health-tool", start_min=0, dur_min=600, events=400)
    shard = _task("shard", project="rare-disease-benchmark", start_min=20, dur_min=120, events=1)
    shard.event_ids = [0]
    ws = find_workstreams([conv, shard], events)
    assert sorted(ws.siblings) == ["conv", "shard"]


def test_zero_span_artifact_shard_pair_is_caught_by_proximity():
    """A commit shard and its PR shard land seconds apart with ~zero span, so
    an overlap FRACTION can never fire on them (the daily-digest case)."""
    events = [_art(0, sha="aaa1", minute=0), _art(1, number=7, minute=0)]
    a = _task("a", start_min=0, dur_min=0, events=1)
    a.event_ids = [0]
    b = _task("b", start_min=0, dur_min=0, events=1)
    b.event_ids = [1]
    b.first_event_ts = T0 + timedelta(seconds=30)
    b.last_event_ts = b.first_event_ts
    ws = find_workstreams([a, b], events)
    assert sorted(ws.siblings) == ["a", "b"]


def test_distant_zero_span_shards_do_not_pair():
    events = [_art(0, sha="aaa1", minute=0), _art(1, number=7, minute=120)]
    a = _task("a", start_min=0, dur_min=0, events=1); a.event_ids=[0]
    b = _task("b", start_min=120, dur_min=0, events=1); b.event_ids=[1]
    assert find_workstreams([a, b], events).siblings == {}


def test_zero_span_pairing_requires_artifacts_on_both_sides():
    """Two brief unrelated tasks must NOT pair just because they are close."""
    a = _task("a", start_min=0, dur_min=0, events=6)
    b = _task("b", start_min=0, dur_min=0, events=5)
    b.first_event_ts = T0 + timedelta(seconds=30)
    b.last_event_ts = b.first_event_ts
    assert find_workstreams([a, b], []).siblings == {}


def test_pairs_are_not_double_counted_across_scopes():
    """A task indexed under both its repo scope and its project scope must not
    produce the same sibling twice."""
    events = [_art(0, project="p", repo_name="r", number=1, minute=30)]
    a = _task("a", start_min=0, dur_min=600, events=1)
    a.event_ids = [0]
    b = _task("b", start_min=10, dur_min=300, events=50)
    ws = find_workstreams([a, b], events)
    assert ws.siblings["a"] == ["b"] and ws.siblings["b"] == ["a"]
    assert ws.n_groups == 1
