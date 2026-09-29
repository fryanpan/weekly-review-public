from weekly_review.events import Event
from weekly_review.segmentation import Task
from weekly_review.enrichment import enrich_task, build_enrichment_prompt


class FakeLLM:
    def __init__(self, response_text: str):
        self.text = response_text
        self.calls: list[dict] = []

    def complete(self, *, system: str, user: str, model: str, max_tokens: int = 2048, temperature: float = 0.0):
        self.calls.append({"system": system, "user": user, "model": model})
        return {"text": self.text, "usage": {}, "stop_reason": "end_turn", "model": model}


def _uev(ts, project, text, seq):
    return Event(
        ts=ts, actor="user", project=project, session_id="s",
        source_system="claude_transcript", source_machine="mm",
        kind="user_message", seq=seq,
        content_snippet=text, metadata={"words": len(text.split())},
    )


def test_enrichment_parses_llm_json():
    events = [_uev("2026-04-15T10:00:00Z", "weekly-review", "write the digest renderer", 0)]
    task = Task(task_id="t1", project="weekly-review", title="initial", event_ids=[0])
    llm = FakeLLM(
        '{"title":"Digest renderer","summary":"Goal: build renderer. Outcome: shipped v1.",'
        '"est_human_hours":3.5,"uncertainty":"medium",'
        '"bryan_domain_expertise":"expert",'
        '"dimensions":{"scope":"single-feature","depth":"careful design","type":"code"},'
        '"rationale":"..."}'
    )
    out, call = enrich_task(task, events, anchors=[], expertise_seed={}, llm=llm, model="claude-sonnet-4-6")
    assert out.title == "Digest renderer"
    assert out.summary.startswith("Goal: build renderer")
    assert out.est_human_hours == 3.5
    assert out.uncertainty == "medium"
    assert out.bryan_domain_expertise == "expert"
    assert out.dimensions["type"] == "code"
    # New fields default to empty string when LLM omits them.
    assert out.effort_breakdown == ""
    assert out.bryan_role_summary == ""
    # The call trace captures exactly what the LLM saw and produced.
    assert call.task_id == "t1"
    assert call.model == "claude-sonnet-4-6"
    assert "write the digest renderer" in call.prompt_user
    assert "Digest renderer" in call.response_raw
    assert call.response_parsed["est_human_hours"] == 3.5


def test_enrichment_captures_effort_and_role_fields():
    events = [_uev("2026-04-15T10:00:00Z", "weekly-review", "build the renderer", 0)]
    task = Task(task_id="t1", project="weekly-review", title="initial", event_ids=[0])
    llm = FakeLLM(
        '{"title":"Renderer","summary":"g. o.",'
        '"est_human_hours":4.0,"uncertainty":"medium",'
        '"bryan_domain_expertise":"expert",'
        '"dimensions":{"scope":"single-feature","depth":"careful design","type":"code"},'
        '"rationale":"r",'
        '"effort_breakdown":"Approximately 200 lines added across 4 files; 1 new component, 2 modified.",'
        '"bryan_role_summary":"the user provided the spec then reviewed two checkpoints; the agent wrote the code."}'
    )
    out, _ = enrich_task(task, events, anchors=[], expertise_seed={}, llm=llm, model="claude-sonnet-4-6")
    assert "200 lines" in out.effort_breakdown
    assert "the user provided the spec" in out.bryan_role_summary


def test_prompt_includes_anchors_and_expertise_seed():
    events = [_uev("2026-04-15T10:00:00Z", "weekly-review", "x", 0)]
    task = Task(task_id="t1", project="weekly-review", title="x", event_ids=[0])
    anchors = [{"title": "write research note", "est_human_hours": 4, "dimensions": {"type": "research"}}]
    expertise = {"backend / python": "expert"}
    system, user = build_enrichment_prompt(task, events, anchors, expertise)
    assert "write research note" in system
    assert "backend / python" in system
    assert "x" in user


def test_enrichment_tolerates_fenced_json():
    events = [_uev("2026-04-15T10:00:00Z", "weekly-review", "x", 0)]
    task = Task(task_id="t1", project="weekly-review", title="x", event_ids=[0])
    llm = FakeLLM(
        '```json\n{"title":"Y","summary":"g. o.","est_human_hours":1.0,"uncertainty":"low",'
        '"bryan_domain_expertise":"competent","dimensions":{"scope":"tiny","depth":"surface","type":"code"},'
        '"rationale":"r"}\n```'
    )
    out, _call = enrich_task(task, events, anchors=[], expertise_seed={}, llm=llm, model="claude-sonnet-4-6")
    assert out.title == "Y"
    assert out.est_human_hours == 1.0


def test_task_summary_includes_agent_log_for_delegated():
    from weekly_review.enrichment import _task_summary
    from weekly_review.segmentation import Task
    from weekly_review.events import Event
    from datetime import datetime, timezone
    events = [Event(ts=f"2026-06-10T0{i}:00:00Z", actor="agent", project="p", session_id="s",
                    source_system="claude_transcript", source_machine="mm",
                    kind="agent_message", seq=i, content_snippet=f"built module {i}",
                    metadata={"words": 3}) for i in range(4)]
    t = Task(task_id="t1", project="p", title="delegated build", delegated=True,
             event_ids=[0, 1, 2, 3],
             first_event_ts=datetime(2026, 6, 10, tzinfo=timezone.utc),
             last_event_ts=datetime(2026, 6, 10, 3, tzinfo=timezone.utc))
    s = _task_summary(t, events)
    assert "delegated task" in s.lower()
    assert "built module 0" in s and "built module 3" in s
    assert "agent_message×4" in s


def _ev(kind, actor, seq, snippet="", tool=None, extra=None):
    from weekly_review.events import Event
    md = {"words": 3}
    if tool:
        md["tool_name"] = tool
    if extra:
        md.update(extra)
    return Event(ts=f"2026-06-10T0{seq % 10}:00:00Z", actor=actor, project="p",
                 session_id="s", source_system="claude_transcript",
                 source_machine="mm", kind=kind, seq=seq,
                 content_snippet=snippet, metadata=md)


def test_estimate_evidence_shows_agent_work_even_when_user_messages_exist():
    """The bug this fixes: estimates were built from what the user ASKED, because
    only user_message events reached the model. Agent output must be there too."""
    from weekly_review.enrichment import _task_summary
    from weekly_review.segmentation import Task
    events = [
        _ev("user_message", "user", 0, "add a dark mode"),
        _ev("agent_message", "agent", 1, "implemented the theme provider"),
        _ev("tool_call", "agent", 2, "{'file_path': '/r/theme.ts', 'old_string': 'a'}", tool="Edit"),
        _ev("tool_call", "agent", 3, "{'file_path': '/r/toggle.tsx'}", tool="Write"),
        _ev("tool_call", "agent", 4, "{'file_path': '/r/theme.ts'}", tool="Edit"),   # dup path
        _ev("tool_call", "agent", 5, "{'file_path': '/r/README.md'}", tool="Read"),  # read != touched
    ]
    t = Task(task_id="t1", project="p", title="dark mode", event_ids=list(range(6)))
    s = _task_summary(t, events)

    assert "WHAT GOT DONE" in s
    # Agent output is present alongside the ask.
    assert "implemented the theme provider" in s
    # Files EDITED are counted, de-duplicated, and Read-only files excluded.
    assert "FILES THE AGENTS EDITED: 2 distinct" in s
    assert "/r/theme.ts" in s and "/r/toggle.tsx" in s
    assert "/r/README.md" not in s
    # The ask is still present, but explicitly demoted.
    assert "add a dark mode" in s
    assert "NOT the basis of the estimate" in s


def test_estimate_evidence_uses_measured_commit_diffstat():
    from weekly_review.enrichment import _task_summary
    from weekly_review.segmentation import Task
    events = [
        _ev("user_message", "user", 0, "ship it"),
        _ev("commit", "artifact", 1, "feat: add exporter",
            extra={"short_sha": "abc1234", "files_changed": 8,
                   "insertions": 421, "deletions": 31}),
    ]
    t = Task(task_id="t1", project="p", title="exporter", event_ids=[0, 1])
    s = _task_summary(t, events)
    assert "+421/-31 across 8 files" in s
    assert "ARTIFACT TOTALS: 1 commit(s)" in s
    assert "MEASURED" in s


def test_estimate_evidence_marks_excluded_checked_in_data():
    # The estimator sees the authored diffstat, and is told in the same line
    # what was set aside and that it is not to be priced.
    from weekly_review.enrichment import _task_summary
    from weekly_review.segmentation import Task
    events = [
        _ev("user_message", "user", 0, "rebuild the front page"),
        _ev("commit", "artifact", 1, "Front page rebuilt",
            extra={"short_sha": "c8f069f", "files_changed": 5,
                   "insertions": 663, "deletions": 90,
                   "excluded_files": 275, "excluded_insertions": 118000}),
    ]
    t = Task(task_id="t1", project="p", title="front page", event_ids=[0, 1])
    s = _task_summary(t, events)
    assert "+663/-90 across 5 files" in s
    assert "275 files / +118000 lines of checked-in data, EXCLUDED" in s
    assert "ARTIFACT TOTALS: 1 commit(s), 5 file-changes" in s


def test_estimate_evidence_reports_subagent_fanout():
    from weekly_review.enrichment import _task_summary
    from weekly_review.segmentation import Task
    events = [
        _ev("agent_message", "agent", 0, "main loop", extra={"agent_lane": "main"}),
        _ev("agent_message", "agent", 1, "child a", extra={"agent_lane": "agent-a"}),
        _ev("agent_message", "agent", 2, "child b", extra={"agent_lane": "agent-b"}),
    ]
    t = Task(task_id="t1", project="p", title="fanout", event_ids=[0, 1, 2])
    s = _task_summary(t, events)
    assert "AGENT FAN-OUT: 2 sub-agent(s)" in s


def test_estimate_evidence_is_honest_when_no_artifact_shipped():
    from weekly_review.enrichment import _task_summary
    from weekly_review.segmentation import Task
    events = [_ev("user_message", "user", 0, "research options")]
    t = Task(task_id="t1", project="p", title="research", event_ids=[0])
    s = _task_summary(t, events)
    # Must say so rather than let the model invent countables.
    assert "ARTIFACTS SHIPPED: none recorded" in s


def test_extract_json_salvages_unescaped_control_chars():
    """A stray newline inside one prose field must not discard the whole task."""
    from weekly_review.enrichment import _extract_json
    raw = '{"title":"X","rationale":"line one\nline two","est_human_hours":3.5}'
    obj = _extract_json(raw)
    assert obj["est_human_hours"] == 3.5
    assert obj["title"] == "X"


def test_extract_json_salvages_unescaped_quote():
    from weekly_review.enrichment import _extract_json
    raw = ('{"title":"the "big" refactor","summary":"did things",'
           '"est_human_hours":6.0,"uncertainty":"medium"}')
    obj = _extract_json(raw)
    assert obj["est_human_hours"] == 6.0
    assert obj["uncertainty"] == "medium"


def test_extract_json_salvages_a_reply_truncated_after_the_estimate():
    """A reply cut off at max_tokens has no closing brace of its own, so the last
    `}` in the text belongs to a nested object that closes BEFORE the estimate.
    Salvaging only up to that brace threw away an estimate that was sitting in the
    text -- 9 of the 10 unparseable cached replies on 2026-09-14 were this shape."""
    from weekly_review.enrichment import _extract_json
    raw = ('{"title":"Benchmark gap","dimensions":{"scope":"multi-feature","type":"research"},'
           '"est_human_hours":48,"uncertainty":"medium",'
           '"rationale":"the pipeline was measuring nothing due to a tel')
    obj = _extract_json(raw)
    assert obj["est_human_hours"] == 48


def test_enrich_task_retries_once_with_more_room_when_a_reply_is_truncated():
    """A reply stopped by max_tokens before any JSON is a budget problem, not a
    model problem: ask again with a larger budget rather than dropping the task."""
    events = [_uev("2026-04-15T10:00:00Z", "weekly-review", "build the renderer", 0)]
    task = Task(task_id="t1", project="weekly-review", title="initial", event_ids=[0])

    class TruncatingLLM:
        def __init__(self):
            self.max_tokens: list[int] = []

        def complete(self, *, system, user, model, max_tokens=2048, temperature=0.0):
            self.max_tokens.append(max_tokens)
            if len(self.max_tokens) == 1:
                return {"text": "Looking at the evidence, this task shipped three PRs",
                        "usage": {"output_tokens": max_tokens}, "stop_reason": "max_tokens", "model": model}
            return {"text": '{"title":"Renderer","est_human_hours":5.0}', "usage": {},
                    "stop_reason": "end_turn", "model": model}

    llm = TruncatingLLM()
    out, call = enrich_task(task, events, anchors=[], expertise_seed={}, llm=llm)
    assert out.est_human_hours == 5.0
    assert len(llm.max_tokens) == 2 and llm.max_tokens[1] > llm.max_tokens[0]
    assert "Renderer" in call.response_raw


def test_extract_json_takes_the_real_object_after_a_placeholder_sketch():
    """The model sometimes sketches the shape before answering. First brace to last
    brace spans both, and the salvage stitched sketch fields into the answer: the
    task lost its type and its goal became a code fence (run-20260914T202524Z)."""
    from weekly_review.enrichment import _extract_json
    raw = ('I will answer in this shape:\n```json\n{\n  "goal": "...",\n  ...\n}\n```\n'
           'Here is the estimate:\n```json\n'
           '{"goal":"Ship the plugin","dimensions":{"type":"code"},"est_human_hours":120,'
           '"rationale":"20 merged PRs"}\n```')
    obj = _extract_json(raw)
    assert obj["goal"] == "Ship the plugin"
    assert obj["dimensions"]["type"] == "code"
    assert obj["est_human_hours"] == 120


def test_extract_json_takes_the_last_estimate_when_the_model_revises_itself():
    from weekly_review.enrichment import _extract_json
    raw = ('First pass: {"goal":"Draft","est_human_hours":2}\n'
           'On reflection the PR count is higher:\n{"goal":"Final","est_human_hours":9}')
    assert _extract_json(raw)["est_human_hours"] == 9


def test_extract_json_prefers_an_object_with_an_estimate_over_a_later_one_without():
    from weekly_review.enrichment import _extract_json
    raw = ('{"goal":"Real","est_human_hours":4}\n'
           'Example of a dimension block: {"scope":"single-feature"}')
    obj = _extract_json(raw)
    assert obj["goal"] == "Real" and obj["est_human_hours"] == 4


def test_enrich_task_retries_a_truncated_reply_even_when_the_estimate_salvages():
    """A reply cut off after the hours still parses, but everything after the cut
    (type, rationale) is gone. It is retried with more room like any truncation."""
    events = [_uev("2026-04-15T10:00:00Z", "weekly-review", "build the renderer", 0)]
    task = Task(task_id="t1", project="weekly-review", title="initial", event_ids=[0])

    class CutAfterHoursLLM:
        def __init__(self):
            self.max_tokens: list[int] = []

        def complete(self, *, system, user, model, max_tokens=2048, temperature=0.0):
            self.max_tokens.append(max_tokens)
            if len(self.max_tokens) == 1:
                return {"text": '{"title":"Renderer","est_human_hours":5.0,"rationale":"three PRs and a te',
                        "usage": {}, "stop_reason": "max_tokens", "model": model}
            return {"text": '{"title":"Renderer","est_human_hours":6.0,"dimensions":{"type":"code"}}',
                    "usage": {}, "stop_reason": "end_turn", "model": model}

    llm = CutAfterHoursLLM()
    out, call = enrich_task(task, events, anchors=[], expertise_seed={}, llm=llm)
    assert len(llm.max_tokens) == 2
    assert out.est_human_hours == 6.0
    assert '"type":"code"' in call.response_raw


def test_enrich_task_keeps_the_salvaged_estimate_when_the_retry_is_unparseable():
    events = [_uev("2026-04-15T10:00:00Z", "weekly-review", "build the renderer", 0)]
    task = Task(task_id="t1", project="weekly-review", title="initial", event_ids=[0])

    class BothTruncatedLLM:
        calls = 0

        def complete(self, *, system, user, model, max_tokens=2048, temperature=0.0):
            BothTruncatedLLM.calls += 1
            text = ('{"title":"Renderer","est_human_hours":5.0,"rationale":"three PRs and a te'
                    if BothTruncatedLLM.calls == 1 else "Looking at the evidence again")
            return {"text": text, "usage": {}, "stop_reason": "max_tokens", "model": model}

    out, _ = enrich_task(task, events, anchors=[], expertise_seed={}, llm=BothTruncatedLLM())
    assert BothTruncatedLLM.calls == 2
    assert out.est_human_hours == 5.0


def test_enrich_task_does_not_retry_a_complete_reply_that_fails_to_parse():
    """Only a truncated reply earns a retry; a finished reply with no estimate
    still fails loudly, so a model-side problem is not hidden behind a re-ask."""
    import pytest

    events = [_uev("2026-04-15T10:00:00Z", "weekly-review", "build the renderer", 0)]
    task = Task(task_id="t1", project="weekly-review", title="initial", event_ids=[0])
    llm = FakeLLM('{"title":"the "big" thing","summary":"no hours here"}')
    with pytest.raises(ValueError):
        enrich_task(task, events, anchors=[], expertise_seed={}, llm=llm)
    assert len(llm.calls) == 1


def test_extract_json_raises_when_hours_unrecoverable():
    from weekly_review.enrichment import _extract_json
    import pytest
    with pytest.raises(ValueError):
        _extract_json('{"title":"the "big" thing","summary":"no hours here"}')


def test_the_prompt_asks_for_everything_that_shipped_and_excludes_rework():
    """The user's ruling on a review item, 2026-09-15, verbatim:

        "The estimator should look not just into the last thing that shipped --
        it should consider everything that shipped. But be sensitive to rework
        -- if agents went down rabbit holes and shipped wasted work and then
        revised it after, then the wasted work shouldn't count in estimated
        hours."

    The measured case: task `aee3e1cdab` holds 63% of a project's week and is
    estimated at 4h, because its rationale prices "roughly 8 pages of triage and
    fix documentation" -- the last thing that shipped inside it, not the week of
    work it holds. The prompt asked for ONE shipped artifact clause, so that is
    what it got.

    The prompt is the only place this rule can live, so the test reads the
    prompt. A prompt instruction that quietly goes missing produces estimates
    that still parse, still pass every schema check, and are wrong in exactly
    the way this ruling was issued about.
    """
    events = [_uev("2026-04-15T10:00:00Z", "weekly-review", "x", 0)]
    task = Task(task_id="t1", project="weekly-review", title="x", event_ids=[0])
    system, _ = build_enrichment_prompt(task, events, [], {})

    assert "name EVERY SHIPPED ARTIFACT or DECISION" in system
    assert "not just the last one or the biggest one" in system
    assert "EXCLUDE REWORK from the countables" in system
    assert "count only what survived" in system
    assert "It must cover EVERYTHING the task shipped" in system
    assert "It must account for every front you priced in step 5" in system


def _coordination_events():
    diff = "diff --git a/src/app.py b/src/app.py\n+++ b/src/app.py\n@@ -1,3 +1,9 @@\n+def secret_new_code():"
    return [
        _ev("subagent_input", "agent", 0, "Build the export page in worktree alpha", extra={"agent_lane": "agent-a"}),
        _ev("tool_call", "agent", 1, "{'prompt': 'build beta'}", tool="Agent", extra={"agent_lane": "main"}),
        _ev("tool_call", "agent", 2, "gh pr view 9 --json files", tool="Bash", extra={"agent_lane": "main"}),
        _ev("tool_result", "agent", 3, diff, extra={"agent_lane": "main"}),
        _ev("tool_call", "agent", 4, "gh pr diff 10", tool="Bash", extra={"agent_lane": "main"}),
        _ev("tool_call", "agent", 5, "gh api -X PUT repos/o/r/pulls/9/merge -f merge_method=squash", tool="Bash",
            extra={"agent_lane": "main"}),
        _ev("tool_call", "agent", 6, "git -C wt push && gh pr create --title x", tool="Bash", extra={"agent_lane": "main"}),
        _ev("tool_call", "agent", 7, "{'taskId': 't-AAA'}", tool="mcp__plugin_claude-workspaces_claude-workspaces__post_status",
            extra={"agent_lane": "main", "card": "t-AAA"}),
        _ev("tool_call", "agent", 8, "{'to': 'peer'}", tool="mcp__claude-hive__send_message", extra={"agent_lane": "main"}),
        _ev("agent_message", "agent", 9, "Merged PR 9; the diff added secret_new_code", extra={"agent_lane": "main"}),
        _ev("pr_merged", "artifact", 10, "Rebuild alpha", extra={"number": 9, "repo": "o/r", "additions": 1200,
                                                                    "deletions": 300, "changedFiles": 14}),
        # A reply to a running teammate is a message, not a brief.
        _ev("tool_call", "agent", 11, "{'to': 'builder-a', 'message': 'looks good'}", tool="SendMessage",
            extra={"agent_lane": "main"}),
        # A lead reviews a builder's branch with git as often as with gh.
        _ev("tool_call", "agent", 12, "git -C /wt/alpha diff main...HEAD --stat", tool="Bash",
            extra={"agent_lane": "main"}),
    ]


def test_a_coordination_prompt_is_counts_of_a_leads_day_and_carries_no_diff():
    """Board task, 2026-09-17: coordination is priced as a lead's time for that many
    dispatches, reviews and merges. The builds it handled are priced in their
    own units, so no diff, diffstat or work-log text may reach this prompt.
    The Agent call and the folded lane's subagent_input are ONE dispatch;
    SendMessage is a message; a git diff is a review."""
    from weekly_review.enrichment import _task_summary
    from weekly_review.segmentation import Task
    events = _coordination_events()
    t = Task(task_id="c1", project="p", title="coordination 2026-06-10", event_ids=list(range(len(events))),
             unit_kind="coordination", delegated=True)
    s = _task_summary(t, events, sibling_lines=["=== SIBLINGS ===", "price the artifacts listed above HERE"])
    assert "COORDINATION UNIT" in s
    assert "1 dispatches" in s and "3 PR reviews" in s and "1 merges" in s and "1 PRs opened" in s
    assert "1 board actions" in s and "2 messages to other sessions" in s
    assert "priced in their OWN units" in s
    for leak in ("diff --git", "secret_new_code", "+1200", "1200", "changedFiles", "ARTIFACTS SHIPPED",
                 "AGENT WORK LOG", "WHAT GOT DONE", "Build the export page", "SIBLINGS", "listed above"):
        assert leak not in s, leak


def test_a_coordination_units_days_are_local_days_like_the_cut_that_made_it():
    """Two events at 15:00 and 18:00 local straddle a UTC midnight; the
    segmenter cut the unit per local day, so the prompt must say 1 day."""
    from weekly_review.enrichment import _coordination_day
    from weekly_review.events import Event
    from weekly_review.segmentation import Task, _local_day, _parse_ts

    def at(ts, seq):
        return Event(ts=ts, actor="agent", project="p", session_id="s", source_system="claude_transcript",
                     source_machine="mm", kind="agent_message", seq=seq, content_snippet="x",
                     metadata={"agent_lane": "main"})
    a, b = "2026-06-10T22:00:00Z", "2026-06-11T01:00:00Z"
    events = [at(a, 0), at(b, 1)]
    want = len({_local_day(_parse_ts(a)), _local_day(_parse_ts(b))})  # 1 west of UTC, 2 on a UTC machine
    t = Task(task_id="c2", project="p", title="c", event_ids=[0, 1], unit_kind="coordination")
    out = "\n".join(_coordination_day(t, events))
    assert f"over {want} day(s)" in out
    if want == 1:
        assert "over 2 day(s)" not in out  # the UTC-date count the old code produced


def test_the_same_events_on_a_build_unit_still_show_the_diffstat():
    """The coordination shape is keyed on unit_kind, not on the events."""
    from weekly_review.enrichment import _task_summary
    from weekly_review.segmentation import Task
    events = _coordination_events()
    t = Task(task_id="b1", project="p", title="build", event_ids=list(range(len(events))), unit_kind="lane")
    s = _task_summary(t, events)
    assert "+1200/-300 across 14 files" in s and "COORDINATION UNIT" not in s


def test_the_system_prompt_prices_coordination_as_lead_time_and_names_no_artifact_units():
    system, _ = build_enrichment_prompt(
        Task(task_id="x", project="p", title="t"), [], anchors=[], expertise_seed={})
    assert "COORDINATION UNIT" in system and "NOT by event count" in system
    assert "NO ARTIFACT" in system and '"no artifact" in the RATIONALE' in system


def test_prompt_overrides_replace_the_template_and_header_bytes(tmp_path, monkeypatch):
    """The reply cache keys on the exact prompt bytes, so a user priced under an
    earlier wording keeps it in prompt_overrides.json and keeps the cache; the
    source carries generic text. No file: the defaults render."""
    import json
    from weekly_review import config as cfg, enrichment as enr
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
    events = [_uev("2026-04-15T10:00:00Z", "weekly-review", "x", 0)]
    task = Task(task_id="t1", project="weekly-review", title="x", event_ids=[0])
    anchors = [{"title": "a", "est_human_hours": 4, "dimensions": {"type": "research"}}]
    system0, user0 = build_enrichment_prompt(task, events, anchors, {})
    assert enr.ASKED_HEADER in user0
    assert "OVERRIDE-TEMPLATE" not in system0

    (tmp_path / "prompt_overrides.json").write_text(json.dumps({
        "system_prompt_template": "OVERRIDE-TEMPLATE {expertise_seed} {anchors_block}",
        "asked_header": "=== OVERRIDE HEADER ===",
    }))
    system1, user1 = build_enrichment_prompt(task, events, anchors, {})
    assert system1.startswith("OVERRIDE-TEMPLATE")
    assert "a" in system1  # anchors still rendered into the override
    assert "=== OVERRIDE HEADER ===" in user1
    assert enr.ASKED_HEADER not in user1

    (tmp_path / "prompt_overrides.json").write_text("not json")
    system2, user2 = build_enrichment_prompt(task, events, anchors, {})
    assert (system2, user2) == (system0, user0)

    # Valid JSON whose template was copied from a RENDERED prompt (single
    # braces around the JSON schema) cannot be formatted; the built-in
    # template renders instead of the run dying on KeyError.
    (tmp_path / "prompt_overrides.json").write_text(json.dumps({
        "system_prompt_template": 'Return JSON {"goal": str} {anchors_block}',
    }))
    system3, _ = build_enrichment_prompt(task, events, anchors, {})
    assert system3 == system0
