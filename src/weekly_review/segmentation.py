"""Task segmentation from event stream.

V2: split-then-merge.

Split pass: keep deterministic Rules 1-5, but the proximity fallback is
session-sized (PROXIMITY_HOURS, default 4h) rather than week-sized. Each
coherent sitting becomes its own task — no more week-long clusters.

Merge pass: pairs of same-project tasks are fused when artifact-level
evidence shows they're the same topic:
  - Shared issue ref (BRY-42)
  - Shared conventional-commit scope (the "(navigate)" in "feat(navigate):")
  - Title-token Jaccard similarity above threshold

This catches the rework/cleanup case — "feat(navigate)" and "fix(navigate)"
end up in one task even when separated by days — without collapsing
unrelated work just because it's close in time.
"""
from __future__ import annotations

import hashlib
import re
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .events import Event


# Match `PREFIX-N` where PREFIX is 2-4 uppercase letters. Bare `#NNN` is
# intentionally NOT matched: hex colors (`#374151`), bare ints in code/output
# (`#0`, `#42`), and counters all collide with it. Linear tickets always carry
# the alpha prefix, so dropping `#NNN` is lossless in practice. A repo-mention
# form (`owner/repo#NNN`) can be added later if GitHub-cross-ref evidence is
# ever needed.
ISSUE_REF_RE = re.compile(r"\b([A-Z]{2,4}-\d+)\b")
# Denylist of `PREFIX-N`-shaped strings that are NOT tickets: biomedical
# vocabulary, scoring instruments, policy/development codes, and generic
# 2-letter prefixes that produce false positives across the rare-disease
# corpus. Filtered post-match so the regex stays simple and future prefixes
# are accepted automatically.
ISSUE_REF_DENYLIST = {
    # cancer / immunology / virology
    "COVID", "IL", "GLP", "CTLA", "PD", "ICD", "HPS", "HLH", "HER",
    "EGFR", "BRCA", "TNF", "CSF", "MHC", "HLA", "ACE", "ANCA",
    # antigen markers
    "CA", "HSV", "HBV", "HCV", "HIV",
    # scoring instruments / outcome scales
    "SF", "NYHA", "ECOG", "MMSE", "MOCA", "GCS", "EQ",
    # genetics / metabolism
    "MODY", "MEN",
    # development / policy
    "SDG", "WHO",
    # generic vocabulary that produces false positives
    "PR", "RT",
}
# Conventional-commit scope: "feat(navigate):" -> "navigate"
CONV_COMMIT_SCOPE_RE = re.compile(r"^\s*[a-z]+\(([a-z0-9][a-z0-9_\-/]*)\)\s*:", re.IGNORECASE)
TOKEN_RE = re.compile(r"[a-z][a-z0-9]{2,}", re.IGNORECASE)

# Generic conv-commit scopes that are too coarse to count as topical evidence.
# A shared scope from this set means very little: site:, mcp:, app:, ui: get
# applied to all sorts of unrelated work across one project. When a scope is
# in this set, fall back to title-token evidence; don't accept it alone.
GENERIC_SCOPE_STOPWORDS = {
    "site", "mcp", "app", "ui", "ux", "fix", "feat", "chore",
    "docs", "refactor", "test", "style", "perf", "build", "ci",
    "deps", "release",
}

SAME_SESSION_MAX_GAP_HOURS = 6    # a session idle longer than this is a new day's work (passes 5 and 6d)


COMMENT_WAKE_SKEW = timedelta(minutes=2)  # a comment's stored time vs the agent turn it wakes
LANE_DISPATCH_TOOLS = frozenset({"SendMessage", "Agent", "Task"})  # the lead's calls that hand a lane work
LANE_MESSAGE_DELIVERY = timedelta(seconds=60)  # the lead's send vs the lane receiving it (0.25s measured)


def board_task_of(ev) -> str | None:
    """The workspace board task an event names itself: a card tool call or its
    result (`card`), a board action or a doc event on a task card (`task_id`),
    or a voice request dictated from a task page (`context.taskId`)."""
    md = ev.metadata if isinstance(ev.metadata, dict) else {}
    ctx = md.get("context") if isinstance(md.get("context"), dict) else {}
    for v in (md.get("card"), md.get("task_id"), ctx.get("taskId")):
        if isinstance(v, str) and v.startswith("t-"):
            return v
    return None


def is_board_work_evidence(ev) -> bool:
    """True when an event shows work on its board task in this window.

    the user's ruling of 2026-09-14: every board task lines up with one measured
    task. A board task earns one only from work: a transcript event that names
    it, a read, touch or comment on its card, a review item or dictated request
    on it. Moving, archiving, regrouping or retitling a card is housekeeping,
    and a card that only had housekeeping in the window did no work in it.

    An agent's tool call naming the card (moving it, posting status on it) is
    the same housekeeping seen from the transcript and an instant: on its own
    it made 101 measured tasks of nothing but instants, with an estimate and
    zero wall clock, in the week of 2026-09-07. The turn around it is evidence
    when that turn did other work (see `segment`).

    A review answer or dictation is his: 5 of 223 answers on that week were an
    agent answering its own review item, housekeeping that made a measured task
    of nothing but instants (4c6932f3a0)."""
    if not board_task_of(ev):
        return False
    if ev.kind in ("tool_call", "tool_result"):
        return False
    if ev.source_system != "workspace_board":
        return True
    name = str((ev.metadata or {}).get("board_event") or "")
    return ev.actor == "user" and name.startswith(("decision.", "voice."))


# Words that are too generic to count as evidence of shared topic.
STOPWORDS = {
    # commit-type verbs
    "feat", "fix", "chore", "docs", "refactor", "test", "style", "perf", "build", "ci",
    # generic
    "add", "update", "remove", "delete", "change", "apply", "use", "set", "get",
    "the", "and", "for", "with", "from", "into", "onto", "when", "then", "this",
    "that", "these", "those", "not", "but", "all", "any", "new", "old",
    # noise from auto_titles
    "untitled", "image", "please", "let", "lets",
    # auto-expansion placeholders
    "null", "none", "true", "false",
}


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _issue_refs(text: str) -> set[str]:
    out = set()
    for m in ISSUE_REF_RE.finditer(text):
        ref = m.group(1)
        if not ref:
            continue
        prefix = ref.split("-", 1)[0]
        if prefix in ISSUE_REF_DENYLIST:
            continue
        out.add(ref)
    return out


def _conv_scope(text: str) -> str | None:
    """Extract the 'navigate' out of 'feat(navigate): tap-to-avoid'."""
    m = CONV_COMMIT_SCOPE_RE.match(text or "")
    return m.group(1).lower() if m else None


def _tokens(text: str) -> set[str]:
    """Lowercase alphanumeric tokens of length >= 3, minus stopwords."""
    if not text:
        return set()
    return {
        t.lower() for t in TOKEN_RE.findall(text) if t.lower() not in STOPWORDS
    }


@dataclass
class Task:
    task_id: str
    project: str
    title: str
    event_ids: list[int] = field(default_factory=list)
    issue_refs: set[str] = field(default_factory=set)
    session_ids: set[str] = field(default_factory=set)  # claude sessions touching this task
    last_event_ts: datetime | None = None
    first_event_ts: datetime | None = None
    # Agent-only work stretch reconstructed by pass 5 (no user messages —
    # fleet-delegated builds steered via channel messages). Still the user's work,
    # indirectly ("if it comes from the team lead it indirectly came from me"),
    # but hands-on is legitimately ~0. Filterable in analysis.
    delegated: bool = False
    # The workspace board task this measured task stands for, when it is one
    # (the user, 2026-09-14: one measured task per board task).
    board_task: str | None = None
    # What kind of priced unit pass 5 or 9 made this: "lane", "lead_worktree",
    # "coordination", "pr". Empty for a task built around his messages or a
    # board task. Added 2026-09-18.
    unit_kind: str = ""
    # How `board_task` was found on a unit that is not the board task's own
    # measured task: "lane_card" (pass 5, the lane's dispatch), "host" (pass
    # 9, split from a board task's unit), or a link-pass source — "board_pr",
    # "pr_ref", "dispatch", "own_card", "worktree". Empty on a board task's
    # own unit and on an unlinked unit. Method Step 5, 2026-09-17.
    link_source: str = ""
    segmentation_trace: list[dict[str, Any]] = field(default_factory=list)

    def touch(self, event_idx: int, ts: datetime, rule: str, detail: str = "", session_id: str | None = None) -> None:
        self.event_ids.append(event_idx)
        if self.first_event_ts is None or ts < self.first_event_ts:
            self.first_event_ts = ts
        # last_event_ts must only advance forward. Pass 3 attaches non-user
        # events in chronological order, but those events may be EARLIER than
        # the user messages already on this task — pulling last_event_ts
        # backward would cause downstream proximity/preceding-task checks to
        # treat this task as if it's old, attracting unrelated events.
        if self.last_event_ts is None or ts > self.last_event_ts:
            self.last_event_ts = ts
        if session_id:
            self.session_ids.add(session_id)
        self.segmentation_trace.append({"event_idx": event_idx, "rule": rule, "detail": detail})


def _stable_task_id(project: str, first_ts: str, seed_text: str, issue_refs: set[str]) -> str:
    """Deterministic id so the same underlying task keeps its id across runs.

    Inputs are the cheapest stable signals we have when the task is first created:
    project + first-event timestamp + either its issue ref (if any) or the first
    chunk of content. Small collision risk (same project + ts + seed) is acceptable
    because it means the two tasks really do look identical to the segmenter.
    """
    h = hashlib.sha1()
    h.update(project.encode("utf-8"))
    h.update(b"\x00")
    h.update(first_ts.encode("utf-8"))
    h.update(b"\x00")
    if issue_refs:
        h.update(",".join(sorted(issue_refs)).encode("utf-8"))
    else:
        h.update((seed_text or "")[:200].encode("utf-8"))
    return h.hexdigest()[:10]


def _new_task(project: str, title: str, first_ts: str, seed_text: str, issue_refs: set[str]) -> Task:
    tid = _stable_task_id(project, first_ts, seed_text, issue_refs)
    return Task(task_id=tid, project=project, title=title)


def _auto_title(text: str, max_words: int = 8) -> str:
    words = text.strip().split()[:max_words]
    return " ".join(words) if words else "untitled"


_PULL_URL = re.compile(r"github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)", re.IGNORECASE)


def _renamed_repos(events: list[Event]) -> dict[str, str]:
    """Old repo name -> the repo GitHub reports today, for repos that were renamed.

    A transcript keeps the name the repo had when it ran: the plugin's PR 6 was
    printed as `<owner>/claude-live-feedback-plugin/pull/6` on 2026-04-29 and
    the API reports it under `<owner>/claude-workspaces-plugin`, so the PR join
    keyed on (repo, number) never matched and a 1,009-line PR sat as its own
    task with an estimate and no time. GitHub redirects the old URL forever, so
    nothing in the text ever says the name changed.

    An alias is only read off a name that is NOT a repo any artifact came from,
    whose bare name is a project slug in this window, and whose project has
    exactly one repo of its own — so two repos under one project can never be
    folded into each other, and a live repo is never aliased away.
    """
    repos_of: dict[str, set[str]] = {}
    for ev in events:
        if ev.kind not in ("pr_opened", "pr_merged"):
            continue
        md = ev.metadata if isinstance(ev.metadata, dict) else {}
        repo = str(md.get("repo") or "").lower()
        if repo:
            repos_of.setdefault(ev.project, set()).add(repo)
    known = {r for rs in repos_of.values() for r in rs}
    by_slug = {proj: rs for proj, rs in repos_of.items() if len(rs) == 1}

    out: dict[str, str] = {}
    for ev in events:
        if ev.actor not in ("agent", "user"):
            continue
        md = ev.metadata if isinstance(ev.metadata, dict) else {}
        names = {r.lower() for r, _ in _PULL_URL.findall(ev.content_snippet or "")}
        names |= {p.rsplit("#", 1)[0].lower() for p in md.get("printed_prs") or () if "#" in p}
        for name in names:
            if name in known or name in out:
                continue
            slug = name.split("/")[-1]
            if slug in by_slug:
                out[name] = next(iter(by_slug[slug]))
    return out


def _task_signature(task: Task, events: list[Event]) -> dict[str, Any]:
    """Collect evidence-of-topic signals across a task's attached events.

    - issue_refs: explicit BRY-42 / #12 markers (from user text + artifact text)
    - scopes: conventional-commit scopes from attached PR/commit titles
    - artifact_tokens: informative tokens from shipped artifact titles only
    - title_tokens: informative tokens from the task's auto-title (user message)
    - has_user_msgs / has_artifact / has_turns: what pass 8 reads to tell a
      worked task from an artifact-only straggler.
    """
    issue_refs = set(task.issue_refs)
    scopes: set[str] = set()
    artifact_tokens: set[str] = set()
    has_user_msgs = False
    has_artifact = False
    has_r2_activity = False
    has_turns = False
    last_agent_ts = None
    earliest_artifact_ts = None
    artifact_source_machines: set[str] = set()
    artifact_repos: set[str] = set()
    conv_repos: set[str] = set()

    for idx in task.event_ids:
        if idx >= len(events):
            continue
        ev = events[idx]
        if ev.kind in LIVE_TURN_KINDS:
            has_turns = True
        if ev.kind in ("pr_merged", "pr_opened", "commit"):
            has_artifact = True
            snippet = (ev.content_snippet or "")
            scope = _conv_scope(snippet)
            if scope:
                scopes.add(scope)
            artifact_tokens |= _tokens(snippet.split("\n", 1)[0])
            issue_refs |= _issue_refs(snippet)
            ev_ts = _parse_ts(ev.ts)
            if earliest_artifact_ts is None or ev_ts < earliest_artifact_ts:
                earliest_artifact_ts = ev_ts
            artifact_source_machines.add(ev.source_machine or "")
            # Extract owner/repo from event metadata (github extractor stamps it).
            if isinstance(ev.metadata, dict):
                repo = ev.metadata.get("repo") or ev.metadata.get("repository")
                if repo:
                    artifact_repos.add(str(repo).lower())
        elif ev.kind == "user_message":
            has_user_msgs = True
            # Linear-task dispatch user_messages (from product_engineer DB dump)
            # carry the /workspace/<product> cwd — capture it for conv_repos.
            if isinstance(ev.metadata, dict):
                cwd = ev.metadata.get("cwd") or ""
                if isinstance(cwd, str) and cwd.startswith("/workspace/"):
                    repo_slug = cwd[len("/workspace/"):].split("/", 1)[0]
                    if repo_slug:
                        conv_repos.add(repo_slug.lower())
        elif ev.actor == "agent":
            ts = _parse_ts(ev.ts)
            if last_agent_ts is None or ts > last_agent_ts:
                last_agent_ts = ts
            # R2 PE transcripts emit user-role records reclassified as
            # `agent_prompt` (see extractors/claude_transcript.py) — they ARE
            # the dispatch content even though they don't qualify as
            # user_messages. Mark r2-activity so Rule 3 can treat them as the
            # conv side.
            if ev.source_machine == "r2_product_engineer":
                has_r2_activity = True
            # R2 PE transcripts emit a cwd like /workspace/<product>. The
            # `<product>` part identifies the repo the R2 agent was working on
            # (e.g. /workspace/bike-route-finder). Capture it as a conv-side
            # repo hint so we can match it against the artifact's repo.
            if isinstance(ev.metadata, dict):
                cwd = ev.metadata.get("cwd") or ""
                if isinstance(cwd, str) and cwd.startswith("/workspace/"):
                    repo_slug = cwd[len("/workspace/"):].split("/", 1)[0]
                    if repo_slug:
                        conv_repos.add(repo_slug.lower())

    return {
        "issue_refs": issue_refs,
        "scopes": scopes,
        "artifact_tokens": artifact_tokens,
        "title_tokens": _tokens(task.title),
        "has_user_msgs": has_user_msgs,
        "has_artifact": has_artifact,
        "has_r2_activity": has_r2_activity,
        "has_turns": has_turns,
        "last_agent_ts": last_agent_ts,
        "earliest_artifact_ts": earliest_artifact_ts,
        "artifact_source_machines": artifact_source_machines,
        "artifact_repos": artifact_repos,
        "conv_repos": conv_repos,
    }


# Kinds that show a task was being WORKED at a moment: somebody typing, an agent
# taking a turn, a review read, a recorded meeting. Artifacts and stamped instants
# (board actions, doc touches) are deliberately absent -- they record that a
# thing happened, not that the task was live. An allowlist, so a new source's
# kinds count as NOT live until someone adds them: the failure is an artifact
# left standalone, never a month of unrelated commits chained onto one task.
LIVE_TURN_KINDS = frozenset({
    "user_message", "agent_message", "agent_prompt", "tool_call", "tool_result",
    "subagent_input", "review_session", "meeting_segment",
})


_SQUASH_SUFFIX = re.compile(r"\s*\(#\d+\)\s*$")


def _artifact_identity(ev: Event) -> set[str]:
    """Keys naming the one piece of work an artifact is: its PR, and its title
    with any squash-merge "(#N)" suffix removed, so a PR's opened, merged and
    squash-commit events share a key while two different PRs never do."""
    keys = set()
    md = ev.metadata if isinstance(ev.metadata, dict) else {}
    if ev.kind in ("pr_opened", "pr_merged") and md.get("number") is not None:
        keys.add(f"pr:{str(md.get('repo') or '').lower()}#{md['number']}")
    title = _SQUASH_SUFFIX.sub("", (ev.content_snippet or "").split("\n", 1)[0]).strip().lower()
    if len(title.split()) >= 3:  # "wip" or "fix tests" names no one piece of work
        keys.add(f"title:{title}")
    return keys


def _two_board_tasks(a: Task, b: Task) -> bool:
    """Both tasks hold a different board task. Every board task is its own
    measured task, so no automatic merge may fuse them: orphan adoption did,
    six times on the week of 2026-08-17."""
    return bool(a.board_task and b.board_task and a.board_task != b.board_task)


def _merge_tasks(keep: Task, drop: Task, reason: str) -> None:
    """Merge `drop` into `keep` in place."""
    # keep the earlier task_id/title; just absorb the other's events
    keep.event_ids.extend(drop.event_ids)
    keep.issue_refs |= drop.issue_refs
    if drop.first_event_ts and (keep.first_event_ts is None or drop.first_event_ts < keep.first_event_ts):
        keep.first_event_ts = drop.first_event_ts
    if drop.last_event_ts and (keep.last_event_ts is None or drop.last_event_ts > keep.last_event_ts):
        keep.last_event_ts = drop.last_event_ts
    keep.segmentation_trace.append(
        {"event_idx": -1, "rule": "merged_from", "detail": f"{drop.task_id}:{reason}"}
    )
    keep.board_task = keep.board_task or drop.board_task
    keep.segmentation_trace.extend(drop.segmentation_trace)


def segment(events: list[Event], manual_merges: list[dict] | None = None,
            dropped: list[Task] | None = None,
            stamp_only: list[Task] | None = None,
            stats: dict[str, int] | None = None,
            board_links: "BoardLinks | None" = None) -> list[Task]:
    """Place events onto measured tasks.

    Pass 1 places his messages: a comment on a board task goes to that board
    task's measured task. That is the only rule for a message (the user,
    2026-09-17). A message with no board task is placed by nothing here; it
    stays hands-on for its project, and the classifier (Step 3) may attach
    it later. The old rules — new-topic phrases (fired 0 of 848 on the week
    of 2026-09-07), issue numbers (2 events), same-session 6-hour extension,
    30-minute cross-session join, message-starts-a-task, PR-opened-seals-task —
    are gone with their tests.

    Pass 3 places agent events by their board task, the comment that woke
    them, or the typed message that asked for them. Artifacts go to the task
    already holding the same artifact, else stand alone until an identity join
    (passes 6, 6a, 6b, 6d, 9) places them; shared title words placed 99 PRs
    wrongly on that week and are gone. Pass 5 places every other agent event:
    each lane is a unit, and a session's own turns go by worktree or to one
    coordination unit per project per day.

    After pass 9 every unit with no board task gets one from the first source
    that names exactly one (`_link_units_to_board_tasks`); `board_links`
    carries what the board store knows (which board tasks link each PR, and
    which ids exist).
    """
    sorted_pairs = sorted(enumerate(events), key=lambda pair: pair[1].ts)
    tasks_by_project: dict[str, list[Task]] = {}

    # One measured task per board task, across sessions and projects (the user,
    # 2026-09-14): "every board task should line up with a weekly review task."
    evidenced_cards = {board_task_of(e) for e in events if is_board_work_evidence(e)}
    card_tasks: dict[str, Task] = {}

    def board_task(card: str, ev: Event, title: str | None = None) -> Task:
        task = card_tasks.get(card)
        if task is None:
            task = _new_task(ev.project, title or f"board task {card}", ev.ts, f"card:{card}", set())
            task.board_task = card
            card_tasks[card] = task
            tasks_by_project.setdefault(ev.project, []).append(task)
        return task
    for idx, ev in sorted_pairs:
        if ev.kind != "user_message":
            continue
        # A comment on a board task belongs to that task. The board task is
        # the unit of work, so idle gaps and topic changes do not split it,
        # and no other message joins it by proximity: one board task's 24
        # comments were once cut into 6 tasks, and a second board task's first
        # comment a minute later landed on the first one's task.
        card = board_task_of(ev) if ev.source_system == "live_feedback" else None
        if card:
            board_task(card, ev, _auto_title(ev.content_snippet or "")).touch(
                idx, _parse_ts(ev.ts), rule="board_card", detail=card, session_id=ev.session_id)

    # Pass 3: place agent events and artifacts.
    #
    # An artifact (pr_opened, pr_merged, commit) joins the task already holding
    # the same artifact, else stands alone as its own task until an identity
    # join places it. Agent events go to the board task their turn worked on,
    # the comment that woke them, or the typed message that asked for them;
    # what is left goes to pass 5.
    TOPICAL_ARTIFACT_KINDS = {"pr_opened", "pr_merged", "commit"}

    # The task holding each typed message, by its turn: agent work carries the
    # turn of the message that asked for it (`ask_turn`). Only transcript
    # messages carry a turn, and since 2026-09-18 nothing places a transcript
    # message until the classifier does, so this rule fires on no current
    # run (0 in the Sep 7 week's rule table). It is the placement the
    # classifier will hand work to; do not delete it as dead.
    ask_host: dict[tuple[str, str, int], Task] = {}
    for project, project_tasks in tasks_by_project.items():
        for t in project_tasks:
            for i in t.event_ids:
                e = events[i]
                if e.kind == "user_message" and isinstance(e.metadata, dict) and e.metadata.get("turn") is not None:
                    ask_host[(project, e.session_id, e.metadata["turn"])] = t

    # The task holding each workspace comment, by `<doc_id>|<thread_id>`. An
    # agent turn the extractor stamped with a comment is the agent answering
    # it, and belongs there rather than with its session's last typed message:
    # a lead's replies to a mockup comment otherwise joined a 6h, 35-lane
    # stretch under an unrelated question (e3838bffd1).
    comment_hosts: dict[str, list[tuple[datetime, Task]]] = {}
    for held in tasks_by_project.values():
        for t in held:
            for i in t.event_ids:
                e = events[i]
                md = e.metadata if isinstance(e.metadata, dict) else {}
                if e.kind == "user_message" and md.get("thread_id") and md.get("doc_id"):
                    comment_hosts.setdefault(f"{md['doc_id']}|{md['thread_id']}", []).append((_parse_ts(e.ts), t))
    for lst in comment_hosts.values():
        lst.sort(key=lambda x: x[0])
    comment_host_ts = {k: [ts for ts, _ in v] for k, v in comment_hosts.items()}
    # His resolve or reopen of a thread he never commented on is still his hand
    # on it. Those touches attach below, before the turn they wake, so their
    # task is looked up when a wake first needs it.
    thread_acts: dict[str, list[int]] = {}
    for i, e in enumerate(events):
        md = e.metadata if isinstance(e.metadata, dict) else {}
        if (e.actor == "user" and md.get("activity_type") in ("resolve", "reopen")
                and md.get("doc_id") and md.get("thread_id")):
            thread_acts.setdefault(f"{md['doc_id']}|{md['thread_id']}", []).append(i)
    thread_acts_tried: set[str] = set()

    def adopt_thread_acts(key: str) -> None:
        thread_acts_tried.add(key)
        for i in thread_acts[key]:
            host = next((t for t in tasks_by_project.get(events[i].project, ())
                         if i in t.event_ids), None)
            if host is not None:
                comment_hosts.setdefault(key, []).append((_parse_ts(events[i].ts), host))
        if key in comment_hosts:
            comment_hosts[key].sort(key=lambda x: x[0])
            comment_host_ts[key] = [ts for ts, _ in comment_hosts[key]]

    # The one board card each agent turn worked on. A turn that moves a card,
    # reports on it or dispatches a builder to it is work on that card, and
    # belongs with his messages on it rather than with whatever the session
    # last typed: one lead session's week of sprint turns across 92 cards
    # became a single task (9e669d1bdc, 8,533 events), while one card's work
    # sat on 23 tasks. A turn naming two cards is triage, and names none.
    turn_card: dict[tuple, str | None] = {}
    turn_cards: dict[tuple, set[str]] = {}
    # A subagent lane's card: the one card its own tool calls name, else the
    # one its dispatch prompt names. Lanes are where a lead's builders work.
    own_lane_card: dict[tuple[str, str, str], str | None] = {}
    # A teammate lane lives across several assignments: each message to it that
    # names one board task starts a stretch of work on that task, until the next
    # one does. Week of 2026-08-17: a lane told "Next task: ... t-X" kept billing
    # its first card, and a lane told "You are implementing board task t-X" gave
    # all 288 events to a card it touched once.
    prompt_stretches: dict[tuple[str, str, str], list[tuple[datetime, str]]] = {}
    for _, e in sorted_pairs:
        md = e.metadata if isinstance(e.metadata, dict) else {}
        card = md.get("card")
        if e.actor != "agent" or not card:
            continue
        lane = md.get("agent_lane") or ""
        if e.kind == "subagent_input":
            prompt_stretches.setdefault((e.project, e.session_id, lane), []).append((_parse_ts(e.ts), card))
            continue
        if md.get("turn") is None:
            continue
        key = (e.project, e.session_id, lane, md["turn"])
        turn_card[key] = card if turn_card.get(key, card) == card else None
        turn_cards.setdefault(key, set()).add(card)
        if lane != "main":
            lk = (e.project, e.session_id, lane)
            own_lane_card[lk] = card if own_lane_card.get(lk, card) == card else None
    # Turns that did something besides calls naming a card: only those make
    # their card's work evidence. Inside a turn naming several cards, work
    # between two calls on the SAME card is that card's: a lead reading and
    # revising one review item between two calls on it. Work between calls on
    # different cards stays with the session; which card it served is a guess.
    # Measured on the week of 2026-09-07: 2,037 bracketed events, 139 cards.
    # Doing something means a tool call naming no card, as for a wake below: a
    # turn that moved a card and said so, or read back the call's own result,
    # made measured tasks of instants on that week (7b4dd5cc2e, 9458d4dff0).
    turn_work = set()
    # His review answer or dictation whose delivery named no board task names
    # it on the board: his answer or request stored a moment before the wake.
    # He also answers a review item by replying on its thread: on 2026-09-08 his
    # reply on a task card came 3s before the answer's delivery, and the board
    # recorded no decision. A comment carries no board; its task's board events do.
    board_asks: dict[tuple[str, str], list[tuple[datetime, int]]] = {}
    task_board: dict[str, str] = {}
    for i, e in sorted_pairs:
        md = e.metadata if isinstance(e.metadata, dict) else {}
        name = str(md.get("board_event") or "")
        if e.source_system == "workspace_board" and md.get("workspace_id"):
            if isinstance(md.get("task_id"), str) and md["task_id"].startswith("t-"):
                task_board.setdefault(md["task_id"], md["workspace_id"])
            wake_name = ("workspace.review_answered" if name in ("decision.answered", "decision.info_requested")
                         else name if name == "voice.request" else None)
            if wake_name and e.actor == "user":
                board_asks.setdefault((md["workspace_id"], wake_name), []).append((_parse_ts(e.ts), i))
    for i, e in sorted_pairs:
        md = e.metadata if isinstance(e.metadata, dict) else {}
        if (e.actor == "user" and e.kind == "user_message" and e.source_system == "live_feedback"
                and md.get("task_id") in task_board):
            board_asks.setdefault((task_board[md["task_id"]], "workspace.review_answered"), []).append((_parse_ts(e.ts), i))
    for asks in board_asks.values():
        asks.sort(key=lambda x: x[0])
    board_ask_ts = {k: [ts for ts, _ in v] for k, v in board_asks.items()}

    def board_ask(md: dict) -> int | None:
        """The index of his board event behind a turn his unkeyed wake started."""
        key = (md.get("ask_wake_ws"), md.get("ask_wake"))
        if key not in board_asks or not md.get("ask_wake_at"):
            return None
        at = _parse_ts(md["ask_wake_at"])
        pos = bisect_right(board_ask_ts[key], at + COMMENT_WAKE_SKEW)
        if not pos:
            return None
        ts, i = board_asks[key][pos - 1]
        return i if at - ts <= COMMENT_WAKE_SKEW else None

    # A dictation naming no task is his ask for the work it started, the way a
    # typed message is: the work stays where it lands and his dictation joins it
    # (see _join_dictations_to_their_work).
    dictation_work: dict[int, list[int]] = {}
    # A turn naming no board task works on the one its wake named: his review
    # answer on t-X wakes the agent to act on t-X. Only a wake turn that used a
    # tool is evidence for it: a one-line reply to a stalled notice is bookkeeping,
    # and on the week of 2026-09-07 made 40 zero-wall tasks of its own. In a
    # turn naming several cards, the wake says which one the unbracketed work
    # served, when it is one of them: his review answers on such turns left
    # 956 events to be rebuilt as delegated work on the week of 2026-09-07.
    # When the wake is his and names none of them, the turn is still his
    # answer being acted on (filing the cards it asked for): 255 events on
    # that week sat on a lead's session task, splitting 4 tasks, 30h of
    # estimate, from the answer. A board notice naming none of them breaks no tie.
    wake_card: dict[tuple, str] = {}
    wake_work: set[tuple] = set()
    turn_events: dict[tuple, list[Event]] = {}
    for idx, e in sorted_pairs:
        md = e.metadata if isinstance(e.metadata, dict) else {}
        if e.actor != "agent" or e.kind == "subagent_input" or md.get("turn") is None:
            continue
        key = (e.project, e.session_id, md.get("agent_lane") or "", md["turn"])
        if e.kind == "tool_call" and not md.get("card"):
            turn_work.add(key)
        wake = md.get("wake_task")
        asked = board_ask(md) if md.get("ask_wake") and not wake else None
        if asked is not None:
            wake = board_task_of(events[asked])
            if wake is None and md.get("ask_wake") == "voice.request":
                dictation_work.setdefault(asked, []).append(idx)
        if wake and (key not in turn_card or (turn_card[key] is None and (
                wake in turn_cards[key] or md.get("trigger") == "him"))):
            wake_card[key] = wake
            if e.kind == "tool_call" and not md.get("card"):
                wake_work.add(key)
        if turn_card.get(key) is None and key in turn_card:
            turn_events.setdefault(key, []).append(e)
    bracket_card: dict[int, str] = {}
    # Bracketed work is evidence under the same test as a turn's: a tool call.
    # Another call's result landing between a call and its own result is not
    # (bd01c34a42, week of 2026-09-07).
    bracket_work: set[str] = set()
    for evs in turn_events.values():
        prev_card = None
        pending: list[Event] = []
        for e in evs:
            card = e.metadata.get("card")
            if not card:
                pending.append(e)
                continue
            if card == prev_card:
                bracket_card.update((id(p), card) for p in pending)
                if any(p.kind == "tool_call" for p in pending):
                    bracket_work.add(card)
            prev_card, pending = card, []

    # A subagent dispatched while the lead answers a comment is part of that
    # answer, but its own transcript never saw the comment. It inherits the
    # comment, and the card, of the main-lane turn running when it started.
    # It also inherits the typed message that asked for the turn, and the board
    # task a wake named.
    # A teammate lives across assignments, so each message the lead sends it
    # starts a new stretch that inherits from the turn that sent it. Week of
    # 2026-09-07: his comment's turn messaged a teammate started the day before,
    # and its 260 events of work inherited that day's turn instead (155f6b4728).
    # A message counts when the lead's SendMessage or Agent call came just before
    # its delivery, so a message from another teammate starts nothing.
    lane_inherit: dict[tuple[str, str, str], list[tuple[datetime, dict]]] = {}
    main_turns: dict[tuple[str, str], list[tuple[datetime, dict]]] = {}
    main_sends: dict[tuple[str, str], list[tuple[datetime, dict]]] = {}
    lane_starts: dict[tuple[str, str, str], list[tuple[datetime, bool]]] = {}
    for _, e in sorted_pairs:
        md = e.metadata if isinstance(e.metadata, dict) else {}
        lane = md.get("agent_lane")
        if e.actor != "agent" or not lane:
            continue
        if lane == "main":
            main_turns.setdefault((e.project, e.session_id), []).append((_parse_ts(e.ts), md))
            if e.kind == "tool_call" and md.get("tool_name") in LANE_DISPATCH_TOOLS:
                main_sends.setdefault((e.project, e.session_id), []).append((_parse_ts(e.ts), md))
        else:
            lk = (e.project, e.session_id, lane)
            if lk not in lane_starts:
                lane_starts[lk] = [(_parse_ts(e.ts), False)]
            elif e.kind == "subagent_input":
                lane_starts[lk].append((_parse_ts(e.ts), True))
    main_turn_ts = {k: [ts for ts, _ in v] for k, v in main_turns.items()}
    main_send_ts = {k: [ts for ts, _ in v] for k, v in main_sends.items()}

    def inherited(parent_md: dict, proj: str, sess: str) -> dict:
        turn = parent_md.get("turn")
        parent = (proj, sess, "main", turn)
        card = None
        if turn is not None and turn_card.get(parent):
            card = turn_card[parent]
        elif turn is not None and parent in wake_work:
            card = wake_card[parent]
        return {"wake": parent_md.get("wake"), "ask": parent_md.get("ask_turn"), "card": card}

    for (proj, sess, lane), starts in lane_starts.items():
        for start, is_message in starts:
            if is_message:
                sends = main_send_ts.get((proj, sess)) or []
                pos = bisect_right(sends, start)
                if not pos or start - sends[pos - 1] > LANE_MESSAGE_DELIVERY:
                    continue
                pmd = main_sends[(proj, sess)][pos - 1][1]
            else:
                tss = main_turn_ts.get((proj, sess))
                pos = bisect_right(tss, start) if tss else 0
                if not pos:
                    continue
                pmd = main_turns[(proj, sess)][pos - 1][1]
            lane_inherit.setdefault((proj, sess, lane), []).append((start, inherited(pmd, proj, sess)))
    lane_inherit_ts = {lk: [ts for ts, _ in v] for lk, v in lane_inherit.items()}

    def lane_parent(ev: Event) -> dict:
        """What this lane event inherits from the lead's turn that assigned its stretch."""
        md = ev.metadata if isinstance(ev.metadata, dict) else {}
        lk = (ev.project, ev.session_id, md.get("agent_lane") or "")
        pos = bisect_right(lane_inherit_ts[lk], _parse_ts(ev.ts)) if lk in lane_inherit_ts else 0
        return lane_inherit[lk][pos - 1][1] if pos else {}

    lane_card_values = {i["card"] for v in lane_inherit.values() for _, i in v if i["card"]}
    lane_card_values |= {c for c in own_lane_card.values() if c}
    stretch_ts = {lk: [ts for ts, _ in v] for lk, v in prompt_stretches.items()}

    def stretch_card(ev: Event, lane: str) -> str | None:
        lk = (ev.project, ev.session_id, lane)
        pos = bisect_right(stretch_ts[lk], _parse_ts(ev.ts)) if lk in stretch_ts else 0
        return prompt_stretches[lk][pos - 1][1] if pos else None

    # Work the lanes and turns did on a board task is evidence for it too. A
    # message assigning a lane a board task already is.
    evidenced_cards |= ({c for k, c in turn_card.items() if c and k in turn_work} | lane_card_values
                        | bracket_work |{c for k, c in wake_card.items() if k in wake_work})

    def event_card(ev: Event) -> str | None:
        md = ev.metadata if isinstance(ev.metadata, dict) else {}
        # An event that names its board task works on it, even inside a triage
        # turn that names several: a lead session's 735 card calls in one week
        # sat on its broad session tasks instead of their cards'. A dispatch
        # prompt names the builder's card, which its lane resolves below.
        own = board_task_of(ev)
        if own and ev.kind != "subagent_input":
            return own
        if ev.actor != "agent":
            return None
        if id(ev) in bracket_card:
            return bracket_card[id(ev)]
        lane = md.get("agent_lane") or ""
        key = (ev.project, ev.session_id, lane, md.get("turn"))
        card = (turn_card.get(key) or wake_card.get(key)) if md.get("turn") is not None else None
        if card or lane == "main" or not lane:
            return card or stretch_card(ev, lane)
        # lane_parent is {} off a teammate lane: only teammate lanes inherit.
        return (stretch_card(ev, lane) or own_lane_card.get((ev.project, ev.session_id, lane))
                or lane_parent(ev).get("card"))

    def card_turn_host(ev: Event, ts: datetime) -> Task | None:
        """The measured task of the board task this event worked on. A card
        with only housekeeping in the window (moved, archived) has none."""
        card = event_card(ev)
        if not card or card not in evidenced_cards:
            return None
        return board_task(card, ev)

    def comment_turn_host(ev: Event, ts: datetime) -> Task | None:
        """The task holding the comment that started this event's agent turn."""
        md = ev.metadata if isinstance(ev.metadata, dict) else {}
        stamped = md.get("wake")
        key = stamped or lane_parent(ev).get("wake")
        if key and key not in comment_hosts and key in thread_acts and key not in thread_acts_tried:
            adopt_thread_acts(key)
        if not key or key not in comment_hosts:
            return None
        # The comment is stored a moment before its delivery wakes the agent;
        # allow for the two clocks disagreeing by a little.
        pos = bisect_right(comment_host_ts[key], ts + COMMENT_WAKE_SKEW)
        if not pos:
            return None
        comment_ts, host = comment_hosts[key][pos - 1]
        # Work his own comment started is its answer however long it ran. A wake
        # somebody else sent on the thread, or one a lane inherited, only finds
        # his comment from nearby.
        his_wake = stamped and md.get("trigger") == "him"
        if not his_wake and ts - comment_ts > timedelta(hours=SAME_SESSION_MAX_GAP_HOURS):
            return None
        return host
    attached_before_pass3 = {i for ts_ in tasks_by_project.values() for t in ts_ for i in t.event_ids}

    # Each task's artifact identities (a PR by repo and number, a commit by
    # sha), extended incrementally: event_ids only grow by appending.
    identity_cache: dict[int, tuple[int, set[str]]] = {}

    def identity_keys(t: Task) -> set[str]:
        n, keys = identity_cache.get(id(t), (0, set()))
        for i in t.event_ids[n:]:
            if i < len(events) and events[i].kind in ("pr_opened", "pr_merged", "commit"):
                keys |= _artifact_identity(events[i])
        identity_cache[id(t)] = (len(t.event_ids), keys)
        return keys

    # event index -> the board task a dispatched lane's event worked on.
    lane_event_card: dict[int, str] = {}
    for idx, ev in sorted_pairs:
        if ev.kind == "user_message":
            continue
        project_tasks = tasks_by_project.setdefault(ev.project, [])
        ts = _parse_ts(ev.ts)
        # Pass 3 visits each event once, so "already attached" can only mean
        # attached before this pass began. A set lookup, not a scan of every
        # task's event list (160s of a 476s slice before).
        if idx in attached_before_pass3:
            continue

        if ev.kind in TOPICAL_ARTIFACT_KINDS:
            snippet = ev.content_snippet or ""
            first_line = snippet.split("\n", 1)[0]
            art_refs = _issue_refs(snippet)
            art_identity = _artifact_identity(ev)
            # The same PR opened then merged, or a PR and its squash commit,
            # is one artifact: the later record joins the earlier one's task.
            # Nothing else places an artifact here. Shared title words did
            # until 2026-09-17, and put 99 merged PRs on tasks that never
            # mention them (two words such as "its, page" within 72h; each
            # attached PR added its words to the task, and three tasks ended
            # the week holding 41, 26 and 20 PRs).
            same_artifact_host: Task | None = None
            for t in project_tasks:
                if t.last_event_ts is None or t.last_event_ts > ts:
                    continue
                if art_identity & identity_keys(t):
                    same_artifact_host = t
            if same_artifact_host is not None:
                same_artifact_host.touch(idx, ts, rule="same_artifact", detail=ev.kind)
                continue

            title = first_line[:80] if first_line else ev.kind
            pr_number = ev.metadata.get("number") if isinstance(ev.metadata, dict) else None
            seed = f"pr:{pr_number}" if pr_number is not None else snippet
            new_t = _new_task(ev.project, title or "shipped artifact", ev.ts, seed, art_refs)
            new_t.issue_refs |= art_refs
            new_t.touch(idx, ts, rule="artifact_own_task", detail=ev.kind)
            project_tasks.append(new_t)
            continue

        # An agent event: the board task first, since it is the unit of work
        # (the user, 2026-09-14) and a comment on a board task already lives on
        # its measured task; then the comment that woke the turn; then the
        # typed message that asked for it, however long the work ran. What
        # none of these places goes to pass 5 (a lane unit, a worktree unit,
        # or the day's coordination). Nothing is placed by time alone: the
        # same-session 6-hour window and the nearest-preceding task by
        # timestamp were removed on 2026-09-17.
        emd = ev.metadata if isinstance(ev.metadata, dict) else {}
        lane = emd.get("agent_lane") or ""
        if lane and lane != "main":
            # A dispatched lane is its own priced unit (method Step 4,
            # 2026-09-17), linked to the board task that dispatched it rather
            # than pooled into it: one board task on the week of 2026-09-07
            # held 56 lanes, 13,259 events and 163.9h. Pass 5 makes the unit
            # and folds a lane under the floor back onto the board task; the
            # link pass records the card on the unit.
            card = event_card(ev)
            if card and card in evidenced_cards:
                lane_event_card[idx] = card
                continue
        card_host = card_turn_host(ev, ts)
        if card_host is not None:
            card_host.touch(idx, ts, rule="card_turn", detail=ev.kind)
            continue
        wake_host = comment_turn_host(ev, ts)
        if wake_host is not None:
            wake_host.touch(idx, ts, rule="comment_turn", detail=ev.kind)
            continue
        ask = emd.get("ask_turn")
        if ask is None:
            ask = lane_parent(ev).get("ask")
        ask_task = ask_host.get((ev.project, ev.session_id, ask)) if ask is not None else None
        if ask_task is not None:
            ask_task.touch(idx, ts, rule="ask_turn", detail=ev.kind)

    # Pass 5: place the agent events no pass above attached. Agent sessions
    # steered via channel messages (filtered as system-injected) have no user
    # messages at all, so ~85% of Client A's agent events were orphaned and the
    # maps/xkcd/forms plugin builds never became tasks (2026-07-09 gap
    # analysis). Per the user: "It's still a task if it comes from the team lead
    # — that means it indirectly came from me." Each lane is a unit; the
    # session's own turns go by worktree or to a coordination unit per project
    # per day. Never grouped per session, never cut by time (the user, 2026-09-17).
    _reconstruct_delegated_tasks(tasks_by_project, events, stats, lane_event_card=lane_event_card,
                                 board_task_unit=board_task)

    # Pass 6: a PR-only task joins the task whose transcript linked that PR
    # around the time it was opened or merged. The session that runs
    # `gh pr create` prints the URL; nothing else is as direct. On Apr 13-Sep 13
    # 86 PR-only tasks (Σest 117h, no measured time) had a timed task linking
    # their PR, 67 of them in another project (health-tool PRs ingested under
    # a second slug). Runs after pass 5 so delegated builds can be hosts too.
    _join_prs_to_linking_tasks(tasks_by_project, events)
    # Pass 6a: a squash commit titled "... (#N)" IS pull request N landing on
    # main, so it goes wherever PR N went, ahead of whoever printed its hash.
    _join_squash_commits_to_their_pr(tasks_by_project, events)
    # Pass 6b: a commit-only task joins the task that printed its hash as it
    # was made, the same identity join for commits.
    _join_commits_to_printing_tasks(tasks_by_project, events)
    # Pass 6d: a builder lane joins the task holding the PR that was opened
    # from the lane's worktree. A lead opens its builders' PRs itself, so the
    # PR lands on the lead's turn (often a board task) and the build on a lane
    # task with no PR: the same work would be priced twice.
    _join_lanes_to_prs_from_their_worktree(tasks_by_project, events)

    # Stray-artifact adoption (a commit or PR with no transcript that printed
    # it, glued onto whichever task was live nearby, in this project or
    # another) ran here until 2026-09-17. An artifact nothing printed now
    # stays its own task, priced from its diff, and the run prints how many.

    # Pass 4d: human-declared merges. When the user reviews the segmentation and
    # says "this is rework on the previous task, merge it", we honor that here.
    # Keyed by (project, first_ts prefix) rather than task_id — task_ids are not
    # stable across re-segmentation, but the absorbed task's first-event
    # timestamp is. It runs after every artifact pass, as it always ran after
    # 4c: a directive with no `into_first_ts` merges into the preceding task by
    # start time, and before adoption that neighbour can be a bare commit.
    _apply_manual_merges(tasks_by_project, manual_merges)

    # Pass 7: a dictation naming no task joins the work it started. Runs last
    # so the work has its final home, including a delegated rebuild.
    _join_dictations_to_their_work(tasks_by_project, events, dictation_work)

    # Pass 8: a rollout's straggler follows the siblings it was opened with.
    # Runs on what every earlier pass left, so nothing it does can outrank a
    # printed hash, a link, or a task's own proximity.
    _join_rollout_stragglers(tasks_by_project, events)

    # Pass 9: one unit per merged PR (the user, 2026-09-17: the estimator sees
    # one PR per prompt). Runs after every join so it undoes only pooling: a
    # task that ends up holding two merged PRs is split, each PR taking its
    # opening turn, its builder lanes and its commits, and the rest of the
    # task stays under its own id.
    pr_units = _one_unit_per_merged_pr(tasks_by_project, events)
    if stats is not None:
        stats.update(pr_units)

    # Link each unit to a board task so the page rolls up by goal (method
    # Step 5). Runs after every split so a link lands on the final unit.
    linked = _link_units_to_board_tasks(tasks_by_project, events, board_links)
    if stats is not None:
        stats.update(linked)

    # the user's ruling of 2026-09-15: a commit with no session anywhere in the
    # data is not his work. Three on the week of 2026-09-07 were a scheduled
    # refresh job and two cloud routines whose transcripts are not on this
    # machine; each read as an estimate with no wall clock. They are dropped
    # here and counted, because the limitation is that this tool cannot see
    # what a machine did off-transcript, not that the work took no time.
    #
    # Which sessions count as the witness is the whole question, and reading
    # the fleet answers it wrong. Twenty sessions run at once here, so
    # something unrelated is nearly always inside half an hour: the two that
    # survived on the week of 2026-09-07 had 502 and 1,773 events in their
    # windows and not one of them in the project that was committed to. The
    # same daily digest job ran six days on the week of 2026-04-27 and five
    # were dropped, because on the sixth another project happened to be busy.
    # A session in client-b cannot have written a notes-repo digest, so
    # the witness has to be the project's own.
    session_ts, session_ts_by_project = _session_times(events)
    witnessed = _projects_whose_commits_sit_beside_their_sessions(events, session_ts_by_project)
    for project, project_tasks in tasks_by_project.items():
        # ...except where the project has no session to witness with. On the
        # week of 2026-08-17 assistant-b is ten commits and nothing
        # else, which is a routing hole rather than a scheduled job, and there
        # the fleet-wide window is all there is to go on. So the narrow rule
        # applies only to a project seen putting a commit beside its own work.
        witness = session_ts_by_project.get(project, ()) if project in witnessed else session_ts
        for t in list(project_tasks):
            if _is_sessionless_commit_task(t, events, witness):
                project_tasks.remove(t)
                if dropped is not None:
                    dropped.append(t)

    # A task made of nothing but stamps is a card somebody moved, not work.
    #
    # `is_board_work_evidence` already refuses to raise a card task on
    # housekeeping, and its docstring records what happens when that slips:
    # "101 measured tasks of nothing but instants, with an estimate and zero
    # wall clock". Four things feed `evidenced_cards`, though, and only three
    # are gated on the lane having done something — a card a lane was merely
    # assigned counts, so a dispatch whose work fell outside the window still
    # raises a task, and 28 such tasks survived on the weeks of 2026-08-17 and
    # 09-07 (16 and 12), carrying 4.6h of estimated output between them and
    # nothing else: no message, no artifact, not one non-instant event.
    #
    # Rather than gate the fourth term and wait for a fifth route, this is the
    # post-condition, asked of the finished task: a task with no evidence in it
    # is not a task. Reading a card is evidence and survives — 16 tasks on the
    # week of 09-07 are his review sessions and touches, which are small but
    # real. Their events are orphaned, which is the honest record: the work
    # those cards stand for did not happen in this window.
    from .effort import is_instant  # local: effort imports Task from here
    for project_tasks in tasks_by_project.values():
        for t in list(project_tasks):
            if t.event_ids and all(is_instant(events[i]) for i in t.event_ids
                                   if i < len(events)):
                project_tasks.remove(t)
                if stamp_only is not None:
                    stamp_only.append(t)

    all_tasks: list[Task] = []
    for project_tasks in tasks_by_project.values():
        all_tasks.extend(project_tasks)
    return all_tasks


NO_SESSION_WINDOW = timedelta(minutes=30)


def _session_times(events: list[Event]) -> tuple[list[datetime], dict[str, list[datetime]]]:
    """When somebody was working, over the whole window and per project."""
    everywhere: list[datetime] = []
    by_project: dict[str, list[datetime]] = {}
    for e in events:
        if e.kind in _ARTIFACT_ONLY_KINDS:
            continue
        at = _parse_ts(e.ts)
        everywhere.append(at)
        by_project.setdefault(e.project, []).append(at)
    everywhere.sort()
    for v in by_project.values():
        v.sort()
    return everywhere, by_project


def _projects_whose_commits_sit_beside_their_sessions(
    events: list[Event], session_ts_by_project: dict[str, list[datetime]]
) -> set[str]:
    """Projects that have been seen committing next to their own sessions.

    A project whose commits never land near its own transcripts is not
    instrumented — its sessions are routed to some other slug, or to none —
    and its commits say nothing about whether a person was there. A project
    that manages it even once does, so a commit of its that stands alone is
    standing alone for a reason. Asking the data rather than a threshold is
    what keeps this from being a knob: it is one co-occurrence or none.
    """
    out: set[str] = set()
    for e in events:
        if e.kind != "commit" or e.project in out:
            continue
        if _within_window(session_ts_by_project.get(e.project, ()), _parse_ts(e.ts)):
            out.add(e.project)
    return out


def _within_window(session_ts, made: datetime) -> bool:
    """Was anybody working within NO_SESSION_WINDOW either side of `made`?"""
    pos = bisect_left(session_ts, made - NO_SESSION_WINDOW)
    return pos < len(session_ts) and session_ts[pos] <= made + NO_SESSION_WINDOW


def _is_sessionless_commit_task(t: Task, events: list[Event], session_ts) -> bool:
    """A task of commits alone with nothing anybody did within half an hour.

    A scheduled job that commits its own output, or a routine whose machine is
    not this one, leaves a commit and no other trace: the nearest transcript,
    board or review event on the week of 2026-09-07 was more than an hour away
    for all three. Anything else the task holds makes it work with a witness.

    `session_ts` is the witness the caller chose — the project's own sessions
    where the project has any, the whole fleet's where it has none.
    """
    kinds = {events[i].kind for i in t.event_ids if i < len(events)}
    if kinds != {"commit"}:
        return False
    for i in t.event_ids:
        if i >= len(events):
            continue
        if _within_window(session_ts, _parse_ts(events[i].ts)):
            return False
    return True


def _join_rollout_stragglers(tasks_by_project: dict[str, list[Task]],
                             events: list[Event]) -> int:
    """Give a PR to the task already holding the burst it was opened in.

    On 2026-04-28 one ai-team-lead session opened ten identically-titled PRs
    across ten repos in 65 seconds — a fleet-wide rule rollout, one act. Six of
    them printed their URLs into that session's transcript and joined it by the
    link; three were claimed by their own project's session; the tenth,
    writing-tool #10, printed nothing and had no session of its own awake
    at 09:33, so 54 lines sat as their own measured task with an estimate and
    no wall clock. Nothing distinguishes it from its nine siblings except which
    URLs happened to survive into the text.

    The token gate cannot reach this: all ten share a title, and against a
    whole day's session the best jaccard any of them scores is 0.005 against a
    threshold of 0.4. So the evidence used here is the burst itself. A PR whose
    title is one a task is already holding, timestamped inside the span that
    task holds that title across, was opened by the same run of the same
    script.

    Inside the span rather than within some window of it, because that needs
    no constant to tune and it is what makes a single PR harmless: one PR is a
    zero-width span and nothing but another PR of the same title in the same
    second can be inside it. The price is that a rollout's own first or last PR
    is out of reach if it is also the unprinted one.
    """
    def pr_title(i: int) -> str | None:
        ev = events[i]
        if ev.kind != "pr_opened":
            return None
        t = (ev.content_snippet or "").strip().lower()
        return t or None

    # What each task already holds, by PR title.
    burst: dict[int, dict[str, list[datetime]]] = {}
    for project_tasks in tasks_by_project.values():
        for t in project_tasks:
            titles: dict[str, list[datetime]] = {}
            for i in t.event_ids:
                if i >= len(events):
                    continue
                title = pr_title(i)
                if title:
                    titles.setdefault(title, []).append(_parse_ts(events[i].ts))
            if titles:
                burst[id(t)] = {k: sorted(v) for k, v in titles.items()}

    joined = 0
    for oproject, project_tasks in tasks_by_project.items():
        for orphan in list(project_tasks):
            sig = _task_signature(orphan, events)
            if sig["has_user_msgs"] or sig["has_r2_activity"] or sig["has_turns"]:
                continue
            titles = {pr_title(i) for i in orphan.event_ids if i < len(events)}
            titles.discard(None)
            if len(titles) != 1:
                continue  # one act, one title
            title = titles.pop()
            at = sorted(_parse_ts(events[i].ts) for i in orphan.event_ids if i < len(events))
            host = None
            for other_tasks in tasks_by_project.values():
                for h in other_tasks:
                    if h is orphan:
                        continue
                    span = burst.get(id(h), {}).get(title)
                    if span and span[0] <= at[0] and at[-1] <= span[-1]:
                        host = h
                        break
                if host is not None:
                    break
            if host is None:
                continue
            _merge_tasks(host, orphan, f"rollout_sibling_join:{oproject}")
            project_tasks.remove(orphan)
            joined += 1
    return joined


def _join_dictations_to_their_work(tasks_by_project: dict[str, list[Task]], events: list[Event],
                                   dictation_work: dict[int, list[int]]) -> int:
    """Move each dictation that named no task onto the task holding most of the
    work it started. Returns how many moved.

    bf56f86e52, week of 2026-09-07: his dictation to a board, with no task
    page open, woke a lead for 5h of estimated work that was rebuilt as a
    delegated task with no hands-on, while the dictation sat wherever proximity
    put it. A typed ask and its work share a session; a dictation and its work
    share only the wake, so this pass joins them the way the session would.

    **The move has to be a move, from every task at once (2026-09-15).** This
    read the holders into `dict[int, Task]`, which keeps ONE task per event, so
    a dictation already sitting on two tasks was removed from whichever the
    comprehension happened to write last and copied onto the host: still two
    tasks, and now both of them billed for it. That is exactly what audit 1
    caught on the week of 2026-08-17 -- a 28-word hub dictation naming no task,
    held by a card/comment/ask placement and by this pass at once.
    The upstream duplicate did not reproduce from the run's persisted events, so
    what is fixed here is this pass's half: a multimap cannot silently leave one
    behind, so whatever puts an event on two tasks, the dictation join takes it
    off both.
    """
    holders: dict[int, list[Task]] = {}
    for ts_ in tasks_by_project.values():
        for t in ts_:
            for i in t.event_ids:
                holders.setdefault(i, []).append(t)
    moved = 0
    for asked, work in dictation_work.items():
        votes: dict[int, int] = {}
        hosts: dict[int, Task] = {}
        for i in work:
            for t in holders.get(i, ()):
                votes[id(t)] = votes.get(id(t), 0) + 1
                hosts[id(t)] = t
        if not votes:
            continue
        host = hosts[max(votes, key=lambda k: votes[k])]
        current = holders.get(asked, [])
        if current == [host]:
            continue
        for t in current:
            if t is host:
                continue
            while asked in t.event_ids:
                t.event_ids.remove(asked)
            if not t.event_ids:
                tasks_by_project[t.project].remove(t)
        if host not in current:
            host.touch(asked, _parse_ts(events[asked].ts), rule="dictation_work",
                       detail="voice.request")
        holders[asked] = [host]
        moved += 1
    return moved


def attach_by_workspace_task(
    tasks: list[Task], events: list[Event]
) -> dict[str, int]:
    """Attach workspace events to the task that already holds their sibling.

    The workspace stamps a `taskId` on every board action, and a task card IS a
    doc, so a read session or comment on one carries the same id. That is a hard
    identity -- two events naming the same workspace task are the same work, no
    proximity guess required. The segmenter never used it: board events live in
    a `workspace-board:<wsId>` session that holds no user messages, so nothing
    clustered them and they fell out as orphans.

    Measured on 2026-08-31..09-06 before this pass: 4,433 events carried a
    workspace `task_id` and only 1,229 were attached to anything. Of the rest,
    1,425 (236 of them `review_session` events carrying measured hands-on time)
    had an ANCHOR -- a sibling that did attach -- and 1,779 across 511 workspace
    tasks had none at all.

    So this pass is deliberately partial. It attaches the 1,425 and leaves the
    1,779 orphaned rather than inventing a task to hold them: a workspace task
    with no other evidence in the window is a task whose work happened outside
    it, and manufacturing a container would report a week that did not happen.
    Closing that gap needs `docId` on board events, which is an instrumentation
    ask, not something this repo can derive.

    Events already attached are NEVER moved. 48 of 222 workspace tasks (21.6%)
    are split across more than one weekly-review task today, one of them eight
    ways; merging those is a re-segmentation that would move published numbers
    for a reason nobody has measured, so it is reported and left alone.

    Returns {"attached": n, "orphans_without_anchor": n, "split": n}.
    """
    ws_of: dict[int, str] = {}
    for idx, ev in enumerate(events):
        md = ev.metadata if isinstance(ev.metadata, dict) else {}
        ws = md.get("task_id")
        if ws:
            ws_of[idx] = str(ws)
    if not ws_of:
        return {"attached": 0, "orphans_without_anchor": 0, "split": 0}

    attached_idx = {i for t in tasks for i in t.event_ids}

    # Vote: the task holding the most of a workspace task's events hosts it.
    # Ties break on the earliest task, then on task_id, so a re-run of the same
    # data lands the same way -- an unstable host would churn task boundaries
    # and with them every task id that carry-forward tries to match.
    votes: dict[str, dict[str, int]] = {}
    by_id: dict[str, Task] = {}
    for task in tasks:
        by_id[task.task_id] = task
        for i in task.event_ids:
            ws = ws_of.get(i)
            if ws:
                votes.setdefault(ws, {})[task.task_id] = (
                    votes.setdefault(ws, {}).get(task.task_id, 0) + 1)

    split = sum(1 for v in votes.values() if len(v) > 1)

    def _rank(item: tuple[str, int]) -> tuple[int, str, str]:
        tid, n = item
        t = by_id[tid]
        return (-n, (t.first_event_ts or datetime.max.replace(tzinfo=timezone.utc)).isoformat(), tid)

    host = {ws: min(v.items(), key=_rank)[0] for ws, v in votes.items()}

    n_attached = 0
    orphans = 0
    for idx, ws in sorted(ws_of.items()):
        if idx in attached_idx:
            continue
        tid = host.get(ws)
        if not tid:
            orphans += 1
            continue
        by_id[tid].touch(
            idx, _parse_ts(events[idx].ts),
            rule="workspace_task_join", detail=ws,
            session_id=events[idx].session_id,
        )
        n_attached += 1

    return {"attached": n_attached, "orphans_without_anchor": orphans, "split": split}


# Delegated-work reconstruction thresholds: a cluster must look like a real
# work stretch, not a dribble of stray telemetry.
DELEGATED_GAP_SEC = SAME_SESSION_MAX_GAP_HOURS * 3600  # same sitting-rhythm as the split pass
DELEGATED_MIN_EVENTS = 15
DELEGATED_MIN_SPAN_SEC = 10 * 60
_DELEGATED_KINDS = {"agent_message", "tool_call", "tool_result", "subagent_input", "agent_prompt"}


_DISPATCH_SUMMARY = re.compile(r'<teammate-message\b[^>]*\bsummary="([^"]+)"')


def _own_work_lanes(events: list[Event]) -> set[tuple[str, str, str]]:
    """(project, session, lane) of each dispatched lane that did enough work to
    be a task by the delegated thresholds. The main lane is never one."""
    n: dict[tuple[str, str, str], int] = {}
    span: dict[tuple[str, str, str], tuple[datetime, datetime]] = {}
    for ev in events:
        if ev.kind not in _DELEGATED_KINDS:
            continue
        md = ev.metadata if isinstance(ev.metadata, dict) else {}
        lane = md.get("agent_lane") or ""
        if not lane or lane == "main":
            continue
        lk = (ev.project, ev.session_id, lane)
        ts = _parse_ts(ev.ts)
        n[lk] = n.get(lk, 0) + 1
        lo, hi = span.get(lk, (ts, ts))
        span[lk] = (min(lo, ts), max(hi, ts))
    return {lk for lk, c in n.items() if c >= DELEGATED_MIN_EVENTS
            and (span[lk][1] - span[lk][0]).total_seconds() >= DELEGATED_MIN_SPAN_SEC}


_EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
_GIT_COMMIT = re.compile(r"\bgit\b[^\n;|&]*\bcommit\b")


def _local_day(ts: datetime) -> str:
    """The calendar day on this machine's clock. Transcripts are stamped in
    UTC, whose day turns over in the afternoon here."""
    return ts.astimezone().date().isoformat()


MAIN_CHECKOUT = "main checkout"  # a turn's edit site when it wrote or committed outside any worktree


def _turn_edit_site(idxs: list[int], events: list[Event]) -> str | None:
    """Where a turn wrote: the worktree its edit, write and commit calls name
    most; MAIN_CHECKOUT when they name none; None when the turn wrote nothing.
    A turn that only reads, pushes or lists a worktree is not building in it."""
    named: dict[str, int] = {}
    wrote = False
    for i in idxs:
        ev = events[i]
        if ev.kind != "tool_call":
            continue
        md = ev.metadata if isinstance(ev.metadata, dict) else {}
        snip = ev.content_snippet or ""
        if not (md.get("tool_name") in _EDIT_TOOLS or (md.get("tool_name") == "Bash" and _GIT_COMMIT.search(snip))):
            continue
        wrote = True
        for w in _WORKTREE.findall(snip):
            named[w] = named.get(w, 0) + 1
    if not wrote:
        return None
    return max(named.items(), key=lambda kv: kv[1])[0] if named else MAIN_CHECKOUT


def _turn_worktree(idxs: list[int], events: list[Event]) -> str | None:
    """The worktree a turn wrote in, or None when it wrote nowhere or only in
    the main checkout."""
    site = _turn_edit_site(idxs, events)
    return site if site and site != MAIN_CHECKOUT else None


def _lead_sessions(events: list[Event]) -> set[tuple[str, str]]:
    """(project, session) of each session run as a lead: its main lane
    dispatched a lane, or more of its turns were woken by agents than by him."""
    dispatch: dict[tuple[str, str], int] = {}
    his: dict[tuple[str, str], set] = {}
    other: dict[tuple[str, str], set] = {}
    for ev in events:
        md = ev.metadata if isinstance(ev.metadata, dict) else {}
        if ev.actor != "agent" or ev.kind not in _DELEGATED_KINDS or (md.get("agent_lane") or "") != "main":
            continue
        key = (ev.project, ev.session_id)
        if ev.kind == "tool_call" and md.get("tool_name") in LANE_DISPATCH_TOOLS:
            dispatch[key] = dispatch.get(key, 0) + 1
        woken_by_him = md.get("ask_turn") is not None or md.get("trigger") == "him"
        (his if woken_by_him else other).setdefault(key, set()).add(md.get("turn"))
    keys = set(dispatch) | set(his) | set(other)
    return {k for k in keys if dispatch.get(k, 0) > 0 or len(other.get(k, ())) > len(his.get(k, ()))}


def _reconstruct_delegated_tasks(tasks_by_project: dict[str, list[Task]], events: list[Event],
                                 stats: dict[str, int] | None = None,
                                 lane_event_card: dict[int, str] | None = None,
                                 board_task_unit=None) -> int:
    """Place the agent events no earlier pass attached. Three kinds of unit,
    none cut by time:

    - a lane that did work of its own is one unit (rule `delegated_reconstruction`);
    - a turn of the session's own lane that wrote or committed in a worktree
      joins that worktree's unit for the session (`lead_worktree_unit`), which
      pass 6d then joins to the PR opened from it; a turn that wrote in the
      main checkout joins the session's own-edits unit for that day;
    - every other turn, and every lane too short to be a unit, goes to one
      coordination unit per project per calendar day (`coordination_unit`).

    A lead's own lane is never one group (the user, 2026-09-17). Grouped per
    session and cut at 6h gaps, a lead that runs builders all day and never
    goes quiet made one task of its day: 5,440 events, 15 merged PRs, 32h on
    2026-09-10/11. A lane under the DELEGATED_MIN_EVENTS / DELEGATED_MIN_SPAN_SEC
    floor folds into coordination rather than being dropped; coordination
    itself has no floor, since it is the catch-all.

    `lane_event_card` says which board task each lane event worked on (pass 3
    records it instead of pooling the lane onto the board task). A lane is
    cut at each board task it is assigned: one stretch per (lane, board
    task), so a teammate told "next task: t-X" starts a new unit. A stretch
    under the floor folds onto its board task's unit (`board_task_unit(card,
    event)` makes or finds it), not into coordination; a stretch over the
    floor is its own unit with `board_task` set (`link_source="lane_card"`).
    """
    lane_event_card = lane_event_card or {}
    attached: set[int] = set()
    for tasks in tasks_by_project.values():
        for t in tasks:
            attached.update(t.event_ids)

    own_work = _own_work_lanes(events)
    lanes: dict[tuple[str, str, str, str], list[int]] = {}
    carded_rest: dict[tuple[str, str, str, str], list[int]] = {}
    turns: dict[tuple[str, str, Any], list[int]] = {}
    coordination: dict[tuple[str, str], list[int]] = {}
    for idx, ev in enumerate(events):
        if idx in attached or ev.kind not in _DELEGATED_KINDS:
            continue
        md = ev.metadata if isinstance(ev.metadata, dict) else {}
        lane = md.get("agent_lane") or ""
        card = lane_event_card.get(idx, "")
        lk = (ev.project, ev.session_id, lane, card)
        if lk[:3] in own_work:
            lanes.setdefault(lk, []).append(idx)
        elif (not lane or lane == "main") and md.get("turn") is not None:
            turns.setdefault((ev.project, ev.session_id, md.get("turn")), []).append(idx)
        elif lane and card:
            # A lane that worked on a board task but did no work of its own
            # (reads, searches) goes with the board task, not the day.
            carded_rest.setdefault(lk, []).append(idx)
        else:
            coordination.setdefault((ev.project, _local_day(_parse_ts(ev.ts))), []).append(idx)

    # A turn that wrote in a worktree joins the session's unit for that
    # worktree, which is one piece of work however many days it took (pass 6d
    # joins it to the PR opened from there). A turn that wrote in the main
    # checkout is the session's own work for that day. A turn crossing
    # midnight goes with the day it started.
    worktrees: dict[tuple[str, str, str], list[int]] = {}
    for (project, session_id, _turn), idxs in turns.items():
        idxs.sort(key=lambda i: _parse_ts(events[i].ts))
        day = _local_day(_parse_ts(events[idxs[0]].ts))
        site = _turn_edit_site(idxs, events)
        if site is None:
            coordination.setdefault((project, day), []).extend(idxs)
        elif site == MAIN_CHECKOUT:
            worktrees.setdefault((project, session_id, f"{MAIN_CHECKOUT} {day}"), []).extend(idxs)
        else:
            worktrees.setdefault((project, session_id, site), []).extend(idxs)

    def under_floor(idxs: list[int]) -> bool:
        if len(idxs) < DELEGATED_MIN_EVENTS:
            return True
        first, last = _parse_ts(events[idxs[0]].ts), _parse_ts(events[idxs[-1]].ts)
        return (last - first).total_seconds() < DELEGATED_MIN_SPAN_SEC

    def fold(project: str, idxs: list[int]) -> None:
        for i in idxs:
            coordination.setdefault((project, _local_day(_parse_ts(events[i].ts))), []).append(i)

    created = 0

    def make(project: str, idxs: list[int], title: str, seed: str, rule: str, detail: str,
             unit_kind: str, refs: set[str], card: str | None = None) -> None:
        nonlocal created
        task = Task(task_id=_stable_task_id(project, events[idxs[0]].ts, seed, refs),
                    project=project, title=title, delegated=True, unit_kind=unit_kind)
        task.issue_refs = refs
        if card:
            task.board_task, task.link_source = card, "lane_card"
        for i in idxs:
            task.touch(i, _parse_ts(events[i].ts), rule, detail=detail, session_id=events[i].session_id)
        tasks_by_project.setdefault(project, []).append(task)
        created += 1

    def lane_card(lk: tuple[str, str, str, str]) -> str | None:
        """The board task this stretch of the lane worked on, if any."""
        return lk[3] or None

    for lk, idxs in sorted(carded_rest.items()):
        card = lane_card(lk)
        if card and board_task_unit is not None:
            host = board_task_unit(card, events[idxs[0]])
            for i in sorted(idxs, key=lambda i: _parse_ts(events[i].ts)):
                host.touch(i, _parse_ts(events[i].ts), "card_lane_fold", detail=card,
                           session_id=events[i].session_id)
        else:
            fold(lk[0], idxs)

    for lk, idxs in sorted(lanes.items()):
        project = lk[0]
        idxs.sort(key=lambda i: _parse_ts(events[i].ts))
        if under_floor(idxs):
            card = lane_card(lk)
            if card and board_task_unit is not None:
                host = board_task_unit(card, events[idxs[0]])
                for i in idxs:
                    host.touch(i, _parse_ts(events[i].ts), "card_lane_fold", detail=card,
                               session_id=events[i].session_id)
            else:
                fold(project, idxs)
            continue
        # title: what the lead's dispatch called the work, else the first
        # substantive agent message. A builder opens with "I'll start by
        # reading the brief", which names nothing.
        title = ""
        for i in idxs:
            if events[i].kind == "subagent_input":
                m = _DISPATCH_SUMMARY.search(events[i].content_snippet or "")
                if m:
                    title = m.group(1).strip()[:80]
                    break
        for i in idxs if not title else ():
            if events[i].kind == "agent_message" and events[i].content_snippet.strip():
                title = events[i].content_snippet.strip().split("\n")[0][:80]
                break
        title = title or f"delegated agent work ({len(idxs)} events)"
        refs: set[str] = set()
        for i in idxs:
            refs |= _issue_refs(events[i].content_snippet or "")
        make(project, idxs, title, f"delegated:{title}", "delegated_reconstruction", "", "lane", refs,
             card=lane_card(lk))

    # No floor here: a turn that wrote a file or committed is work with an
    # artifact, however few events it took, and folded into coordination its
    # commit would price the lead's day.
    for (project, _session_id, w), idxs in sorted(worktrees.items()):
        idxs.sort(key=lambda i: _parse_ts(events[i].ts))
        title = f"own edits in {w}" if w.startswith(MAIN_CHECKOUT) else f"own edits in worktree {w}"
        make(project, idxs, title[:80], f"lead_worktree:{w}", "lead_worktree_unit", w, "lead_worktree", set())

    coordination_units = 0
    coordination_events = 0
    for (project, day), idxs in sorted(coordination.items()):
        idxs.sort(key=lambda i: _parse_ts(events[i].ts))
        make(project, idxs, f"coordination {day}", f"coordination:{day}", "coordination_unit", day,
             "coordination", set())
        coordination_units += 1
        coordination_events += len(idxs)
    if stats is not None:
        stats["coordination_units"] = coordination_units
        stats["coordination_events"] = coordination_events
    return created


PR_LINK_JOIN_WINDOW = timedelta(hours=24)
_ARTIFACT_ONLY_KINDS = {"pr_opened", "pr_merged", "commit"}


def _join_prs_to_linking_tasks(tasks_by_project: dict[str, list[Task]], events: list[Event]) -> int:
    """Merge each PR-only task into the task that linked its PR.

    A vote is an agent or user event on a non-artifact task that links the PR
    within PR_LINK_JOIN_WINDOW of one of the PR's own events. A later review
    conversation that cites an old PR does not vote. The task that linked it
    first wins; ties go to the most links. Crosses projects: the host keeps
    the work.
    """
    aliases = _renamed_repos(events)
    link_keys: dict[int, list[tuple[str, str]]] = {}
    for i, ev in enumerate(events):
        if ev.actor not in ("agent", "user"):
            continue
        keys = {(aliases.get(r.lower(), r.lower()), n) for r, n in _PULL_URL.findall(ev.content_snippet or "")}
        # A tool result's whole text, past the snippet: a batch that opens
        # several PRs prints most of their URLs after the first 500 characters.
        printed = ev.metadata.get("printed_prs") if isinstance(ev.metadata, dict) else None
        for link in printed or ():
            if "#" in link:
                name, num = link.rsplit("#", 1)
                keys.add((aliases.get(name.lower(), name.lower()), num))
        if keys:
            link_keys[i] = sorted(keys)

    orphans: list[tuple[str, Task]] = []
    votes_by_pr: dict[tuple[str, str], list[tuple[datetime, Task]]] = {}
    for project, project_tasks in tasks_by_project.items():
        for t in project_tasks:
            kinds = {events[i].kind for i in t.event_ids if i < len(events)}
            if kinds and kinds <= _ARTIFACT_ONLY_KINDS:
                if kinds & {"pr_opened", "pr_merged"}:
                    orphans.append((project, t))
                continue
            for i in t.event_ids:
                for key in link_keys.get(i, ()):
                    votes_by_pr.setdefault(key, []).append((_parse_ts(events[i].ts), t))
    if not orphans or not votes_by_pr:
        return 0

    joined = 0
    for oproject, orphan in orphans:
        votes: dict[int, list] = {}  # id(host) -> [host, count, nearest gap]
        for i in orphan.event_ids:
            ev = events[i]
            if ev.kind not in ("pr_opened", "pr_merged"):
                continue
            md = ev.metadata if isinstance(ev.metadata, dict) else {}
            key = (str(md.get("repo") or "").lower(), str(md.get("number")))
            pr_ts = _parse_ts(ev.ts)
            for link_ts, host in votes_by_pr.get(key, ()):
                if _two_board_tasks(host, orphan):
                    continue
                gap = abs(link_ts - pr_ts)
                if gap > PR_LINK_JOIN_WINDOW:
                    continue
                v = votes.setdefault(id(host), [host, 0, gap, link_ts])
                v[1] += 1
                v[2] = min(v[2], gap)
                v[3] = min(v[3], link_ts)
        if not votes:
            continue
        # The first link is `gh pr create` printing the URL: nobody can cite a
        # PR before it exists. Most links went to the lead who reviewed and
        # merged it instead: 39 of 265 opened PRs on the week of 2026-09-07 sat
        # on a task other than the one that first linked them.
        host = min(votes.values(), key=lambda v: (v[3], -v[1]))[0]
        _merge_tasks(host, orphan, f"pr_link_join:{oproject}")
        tasks_by_project[oproject].remove(orphan)
        joined += 1
    return joined


_WORKTREE = re.compile(r"(?:\.claude/worktrees/|--claude-worktrees-)([A-Za-z0-9._-]+)")
LANE_WORKTREE_MIN_SHARE = 0.6
PR_WORKTREE_LOOKBACK = timedelta(minutes=3)


def _join_lanes_to_prs_from_their_worktree(tasks_by_project: dict[str, list[Task]], events: list[Event]) -> int:
    """Merge a delegated lane task into the task that first printed a PR, when
    the PR was opened from the worktree the lane worked in.

    The lane's worktree is the one named by at least LANE_WORKTREE_MIN_SHARE of
    its tool calls that name any. The PR's worktree is the one named most by
    the printing lane's tool calls in the PR_WORKTREE_LOOKBACK before the first
    print (`git -C <worktree> push` then `gh pr create`). Same session only,
    and the lane must have been active in the DELEGATED_GAP_SEC before the
    print: worktree names are reused. On the week of 2026-09-07, 40 of 152 lane
    tasks (10,054 of 43,131 lane events) sat apart from the PR they built.
    """
    aliases = _renamed_repos(events)
    first_print: dict[tuple[str, str], int] = {}
    calls: dict[tuple[str, str], list[int]] = {}
    for i, ev in enumerate(events):
        if ev.actor != "agent":
            continue
        md = ev.metadata if isinstance(ev.metadata, dict) else {}
        if ev.kind == "tool_call":
            calls.setdefault((ev.session_id, md.get("agent_lane") or ""), []).append(i)
        keys = {(aliases.get(r.lower(), r.lower()), n) for r, n in _PULL_URL.findall(ev.content_snippet or "")}
        for link in md.get("printed_prs") or ():
            if "#" in link:
                name, num = link.rsplit("#", 1)
                keys.add((aliases.get(name.lower(), name.lower()), num))
        for key in keys:
            if key not in first_print or _parse_ts(ev.ts) < _parse_ts(events[first_print[key]].ts):
                first_print[key] = i

    task_of: dict[int, Task] = {}
    lanes_by_worktree: dict[tuple[str, str], list[tuple[str, Task]]] = {}
    for project, project_tasks in tasks_by_project.items():
        for t in project_tasks:
            for i in t.event_ids:
                task_of[i] = t
            if not t.delegated or not t.event_ids:
                continue
            named: dict[str, int] = {}
            for i in t.event_ids:
                if events[i].kind == "tool_call":
                    for w in _WORKTREE.findall(events[i].content_snippet or ""):
                        named[w] = named.get(w, 0) + 1
            if not named:
                continue
            w, n = max(named.items(), key=lambda kv: kv[1])
            if n >= LANE_WORKTREE_MIN_SHARE * sum(named.values()):
                lanes_by_worktree.setdefault((events[t.event_ids[0]].session_id, w), []).append((project, t))

    moved: dict[int, Task] = {}
    joined = 0
    for key, i in sorted(first_print.items(), key=lambda kv: events[kv[1]].ts):
        ev = events[i]
        printed_at = _parse_ts(ev.ts)
        lane = (ev.metadata or {}).get("agent_lane") or ""
        named = {}
        for j in calls.get((ev.session_id, lane), ()):
            if timedelta(0) <= printed_at - _parse_ts(events[j].ts) <= PR_WORKTREE_LOOKBACK:
                for w in _WORKTREE.findall(events[j].content_snippet or ""):
                    named[w] = named.get(w, 0) + 1
        host = task_of.get(i)
        if not named or host is None:
            continue
        while id(host) in moved:
            host = moved[id(host)]
        w = max(named.items(), key=lambda kv: kv[1])[0]
        # Worktree names are reused within a session: of the lanes that
        # worked in this one, the lane active nearest the print is the one
        # that built the PR.
        candidates = []
        for project, t in lanes_by_worktree.get((ev.session_id, w), ()):
            if t is host or id(t) in moved or _two_board_tasks(host, t) or t.unit_kind == "coordination":
                continue
            first, last = t.first_event_ts, t.last_event_ts
            if first is None or last is None or first > printed_at or (printed_at - last).total_seconds() > DELEGATED_GAP_SEC:
                continue
            candidates.append((printed_at - last, project, t))
        candidates.sort(key=lambda c: c[0])
        for _gap, project, t in candidates:
            if host.unit_kind == "coordination":
                # A lead opened the PR from its coordination day. The day is
                # priced without a PR diff, so the PR and the turn that opened
                # it go to the lane that built it, not the lane to the day.
                # The turn goes only when this PR is all it printed; a batch
                # that opened several stays as triage and the PR goes alone.
                turn = _turn_key(ev) if (ev.metadata or {}).get("turn") is not None else None
                turn_idxs = [j for j in host.event_ids if turn is not None and j < len(events)
                             and events[j].actor == "agent" and (events[j].metadata or {}).get("turn") is not None
                             and _turn_key(events[j]) == turn]
                printed = set()
                for j in turn_idxs:
                    printed |= _printed_pr_keys(events[j], aliases)
                gone = {j for j in host.event_ids if j < len(events) and _pr_key(events[j], aliases) == key}
                # The squash commit pass 6a put beside this PR goes with it.
                pr_times = [_parse_ts(events[j].ts) for j in gone]
                for j in host.event_ids:
                    if j >= len(events) or events[j].kind != "commit":
                        continue
                    m = _SQUASH_TITLE.search((events[j].content_snippet or "").split("\n", 1)[0])
                    if m and m.group(1) == key[1] and any(
                            abs(_parse_ts(events[j].ts) - pt) <= SQUASH_COMMIT_WINDOW for pt in pr_times):
                        gone.add(j)
                if printed == {key}:
                    gone |= set(turn_idxs)
                if not gone:
                    continue
                _drop_events(host, gone, events)
                for j in sorted(gone):
                    # "<worktree> from <task>" so the credit metric can read
                    # the origin, the same shape pass 9 records.
                    t.touch(j, _parse_ts(events[j].ts), "pr_to_its_lane", detail=f"{w} from {host.task_id}",
                            session_id=events[j].session_id)
                    task_of[j] = t
                if not host.event_ids:
                    tasks_by_project[project].remove(host)
                joined += 1
                break
            _merge_tasks(host, t, f"lane_worktree_join:{w}")
            tasks_by_project[project].remove(t)
            moved[id(t)] = host
            joined += 1
    return joined


def _pr_key(ev: Event, aliases: dict[str, str]) -> tuple[str, str] | None:
    md = ev.metadata if isinstance(ev.metadata, dict) else {}
    if ev.kind not in ("pr_opened", "pr_merged") or md.get("number") is None:
        return None
    repo = str(md.get("repo") or "").lower()
    return (aliases.get(repo, repo), str(md["number"]))


def _printed_pr_keys(ev: Event, aliases: dict[str, str]) -> set[tuple[str, str]]:
    """The PRs a transcript event links, in its snippet or the full tool result."""
    md = ev.metadata if isinstance(ev.metadata, dict) else {}
    keys = {(aliases.get(r.lower(), r.lower()), n) for r, n in _PULL_URL.findall(ev.content_snippet or "")}
    for link in md.get("printed_prs") or ():
        if "#" in link:
            name, num = link.rsplit("#", 1)
            keys.add((aliases.get(name.lower(), name.lower()), num))
    return keys


def _lane_worktree(idxs: list[int], events: list[Event]) -> str | None:
    """The worktree at least LANE_WORKTREE_MIN_SHARE of these tool calls name."""
    named: dict[str, int] = {}
    for i in idxs:
        if events[i].kind == "tool_call":
            for w in _WORKTREE.findall(events[i].content_snippet or ""):
                named[w] = named.get(w, 0) + 1
    if not named:
        return None
    w, n = max(named.items(), key=lambda kv: kv[1])
    return w if n >= LANE_WORKTREE_MIN_SHARE * sum(named.values()) else None


def _drop_events(t: Task, gone: set[int], events: list[Event]) -> None:
    """Remove `gone` from a task in place and recompute its span. The trace
    is pruned too: `persist.write_tasks` writes a task's events from its
    trace, not its `event_ids`, so a stale step re-attaches the event on disk."""
    t.event_ids = [i for i in t.event_ids if i not in gone]
    t.segmentation_trace = [s for s in t.segmentation_trace if s.get("event_idx") not in gone]
    t.first_event_ts = t.last_event_ts = None
    left: set[str] = set()
    for i in t.event_ids:
        if i >= len(events):
            continue
        ts = _parse_ts(events[i].ts)
        if t.first_event_ts is None or ts < t.first_event_ts:
            t.first_event_ts = ts
        if t.last_event_ts is None or ts > t.last_event_ts:
            t.last_event_ts = ts
        left.add(events[i].session_id)
    t.session_ids &= left


@dataclass(frozen=True)
class BoardLinks:
    """What the board store knows, for linking units to board tasks.

    `pr_tasks`: (repo lower-cased, PR number as a string) -> the board tasks
    whose record links or names that PR. `known`: every board task id the
    store holds; a name that is not in it is ignored, since a worktree or a
    branch can be named `t-something` without being a board task. An empty
    `known` means no store: any id of the right shape is accepted."""
    pr_tasks: dict[tuple[str, str], frozenset[str]] = field(default_factory=dict)
    known: frozenset[str] = frozenset()

    def valid(self, ids) -> set[str]:
        return {i for i in ids if _BOARD_ID.fullmatch(i) and (not self.known or i in self.known)}


_BOARD_ID = re.compile(r"t-[A-Za-z0-9_-]{12}")
LINK_SOURCES = ("board_pr", "pr_ref", "dispatch", "own_card", "worktree")


def _link_units_to_board_tasks(tasks_by_project: dict[str, list[Task]], events: list[Event],
                               board_links: BoardLinks | None) -> dict[str, int]:
    """Give each unit with no board task the one its evidence names.

    Sources, in order; the first that names exactly one board task links the
    unit and is recorded in `link_source`:

    - `board_pr`: the board store's own record of a task links or names a PR
      the unit holds (opened or merged).
    - `pr_ref`: the PR's body or branch names a board task id (carried as
      `named_tasks` on the PR event; the body text itself is never stored).
    - `dispatch`: the prompt that dispatched the lane names a board task
      (`card` on a `subagent_input` event).
    - `own_card`: any event of the unit names a board task itself.
    - `worktree`: the unit's tool calls name a worktree whose name is a board
      task id.

    A source naming two or more board tasks abstains, and the next source
    is tried; a unit where any source abstained counts once in
    `link_ambiguous`, whether or not a later source linked it. On the week of
    2026-09-07 the store's PR links and the dispatch prompts named the same
    task on every unit both covered (4 units, 0 disagreements), so the order
    only matters for what gets recorded.

    Returns counts: `units_linkable` (units with no board task before the
    pass), `units_linked`, one `linked_<source>` per source, and
    `link_ambiguous`."""
    links = board_links or BoardLinks()
    aliases = _renamed_repos(events)
    counts: dict[str, int] = {"units_linkable": 0, "units_linked": 0, "link_ambiguous": 0}
    counts.update({f"linked_{s}": 0 for s in LINK_SOURCES})
    for project_tasks in tasks_by_project.values():
        for t in project_tasks:
            if t.board_task or not t.event_ids:
                continue
            counts["units_linkable"] += 1
            found: dict[str, set[str]] = {s: set() for s in LINK_SOURCES}
            worktree_names: Counter = Counter()
            for i in t.event_ids:
                if i >= len(events):
                    continue
                ev = events[i]
                md = ev.metadata if isinstance(ev.metadata, dict) else {}
                if ev.kind in ("pr_opened", "pr_merged"):
                    repo = str(md.get("repo") or "").lower()
                    key = (aliases.get(repo, repo), str(md.get("number") or ""))
                    found["board_pr"].update(links.pr_tasks.get(key, ()))
                    found["pr_ref"].update(links.valid(md.get("named_tasks") or ()))
                if ev.kind == "subagent_input" and isinstance(md.get("card"), str):
                    found["dispatch"].update(links.valid([md["card"]]))
                own = board_task_of(ev)
                if own:
                    found["own_card"].update(links.valid([own]))
                if ev.kind == "tool_call":
                    for m in _WORKTREE.finditer(ev.content_snippet or ""):
                        worktree_names[m.group(1)] += 1
            found["worktree"] = links.valid(worktree_names)
            ambiguous = False
            for source in LINK_SOURCES:
                ids = found[source]
                if len(ids) == 1:
                    t.board_task, t.link_source = next(iter(ids)), source
                    counts["units_linked"] += 1
                    counts[f"linked_{source}"] += 1
                    break
                if len(ids) > 1:
                    ambiguous = True
            # Counted per unit, not per source: the run reads it as units.
            if ambiguous:
                counts["link_ambiguous"] += 1
    return counts


def _one_unit_per_merged_pr(tasks_by_project: dict[str, list[Task]], events: list[Event]) -> dict[str, int]:
    """Split every task holding two or more merged PRs so each merged PR is its
    own unit. Runs after every join, so it undoes pooling and nothing else.

    A unit is the PR's own events, the turn that first printed its URL (the
    `gh pr create` turn, when that turn printed only this PR), the lanes that
    worked in the worktree the PR was opened from, their commits, and the
    squash commit "(#N)" that landed it. What the task held besides stays on
    the task under its own id: his messages, the lead's other turns, PRs only
    opened. A unit with no message of his is delegated, as a lane task is.

    Why: one prompt per PR. The estimator given 22 PRs in one prompt returned
    80h; priced one at a time with their builders they read 0.75h + the
    builders' own estimates (#112). On the week of 2026-09-07 three tasks
    held 41, 26 and 20 merged PRs before the identity joins, and a lead's
    session task still held 15 after them. This pass split 32 tasks into 96
    units on that week: 58 hold the opening turn, 24 found a builder lane by
    worktree (4 more hold a lane because the lane opened the PR itself), 17
    the PR alone (its turn opened several PRs, or lies outside the task).
    """
    aliases = _renamed_repos(events)
    first_print: dict[tuple[str, str], int] = {}
    calls: dict[tuple[str, str], list[int]] = {}
    for i, ev in enumerate(events):
        if ev.actor != "agent":
            continue
        md = ev.metadata if isinstance(ev.metadata, dict) else {}
        if ev.kind == "tool_call":
            calls.setdefault((ev.session_id, md.get("agent_lane") or ""), []).append(i)
        for key in _printed_pr_keys(ev, aliases):
            if key not in first_print or _parse_ts(ev.ts) < _parse_ts(events[first_print[key]].ts):
                first_print[key] = i

    def worktree_of_print(i: int) -> str | None:
        ev = events[i]
        md = ev.metadata if isinstance(ev.metadata, dict) else {}
        printed_at = _parse_ts(ev.ts)
        named: dict[str, int] = {}
        for j in calls.get((ev.session_id, md.get("agent_lane") or ""), ()):
            if timedelta(0) <= printed_at - _parse_ts(events[j].ts) <= PR_WORKTREE_LOOKBACK:
                for w in _WORKTREE.findall(events[j].content_snippet or ""):
                    named[w] = named.get(w, 0) + 1
        return max(named.items(), key=lambda kv: kv[1])[0] if named else None

    stats = {"tasks_split": 0, "units_made": 0, "units_with_a_lane": 0}
    for project, project_tasks in tasks_by_project.items():
        for t in list(project_tasks):
            held = set(t.event_ids)
            merged: dict[tuple[str, str], list[int]] = {}
            for i in t.event_ids:
                if i < len(events) and events[i].kind == "pr_merged":
                    key = _pr_key(events[i], aliases)
                    if key is not None:
                        merged.setdefault(key, []).append(i)
            # A coordination unit is priced as a lead's day, never with a PR
            # diff, so even one merged PR leaves it.
            if len(merged) < (1 if t.unit_kind == "coordination" else 2):
                continue

            claim: dict[int, tuple[str, str]] = {}
            for key, idxs in merged.items():
                for i in idxs:
                    claim[i] = key
            for i in t.event_ids:
                key = _pr_key(events[i], aliases) if i < len(events) else None
                if key in merged and i not in claim:
                    claim[i] = key

            # The opening turn, when this PR is the only one it printed. A
            # lead's batch that opened several is triage and stays, and so
            # does a turn that opened this PR beside one merged elsewhere.
            turn_printed: dict[tuple, set[tuple[str, str]]] = {}
            for i in t.event_ids:
                if i >= len(events) or events[i].actor != "agent":
                    continue
                md = events[i].metadata if isinstance(events[i].metadata, dict) else {}
                if md.get("turn") is not None:
                    keys = _printed_pr_keys(events[i], aliases)
                    if keys:
                        turn_printed.setdefault(_turn_key(events[i]), set()).update(keys)
            turn_owner: dict[tuple, tuple[str, str]] = {}
            for key in merged:
                i = first_print.get(key)
                if i is not None and i in held and turn_printed.get(_turn_key(events[i])) == {key}:
                    turn_owner[_turn_key(events[i])] = key
            for i in t.event_ids:
                if i in claim or i >= len(events) or events[i].actor != "agent":
                    continue
                md = events[i].metadata if isinstance(events[i].metadata, dict) else {}
                if md.get("turn") is not None and _turn_key(events[i]) in turn_owner:
                    claim[i] = turn_owner[_turn_key(events[i])]

            # The lanes that built in the PR's worktree. A lane's events go to
            # the next PR opened from its worktree, or to the last one if the
            # lane kept going within the delegated gap (review fixes).
            prints_by_wt: dict[tuple[str, str], list[tuple[datetime, tuple[str, str]]]] = {}
            for key in merged:
                i = first_print.get(key)
                if i is None:
                    continue
                w = worktree_of_print(i)
                if w:
                    prints_by_wt.setdefault((events[i].session_id, w), []).append((_parse_ts(events[i].ts), key))
            for lst in prints_by_wt.values():
                lst.sort(key=lambda x: x[0])
            # The session's own turns that wrote in the PR's worktree go the
            # same way (a lead's own edits, pass 5's lead_worktree units).
            lanes: dict[tuple, list[int]] = {}
            for i in t.event_ids:
                if i in claim or i >= len(events) or events[i].actor != "agent":
                    continue
                md = events[i].metadata if isinstance(events[i].metadata, dict) else {}
                lane = md.get("agent_lane") or ""
                if lane and lane != "main":
                    lanes.setdefault((events[i].session_id, lane), []).append(i)
                elif md.get("turn") is not None:
                    lanes.setdefault(("turn",) + _turn_key(events[i]), []).append(i)
            lane_units = set()
            for group, idxs in lanes.items():
                sess = idxs and events[idxs[0]].session_id
                w = _turn_worktree(idxs, events) if group[0] == "turn" else _lane_worktree(idxs, events)
                prints = prints_by_wt.get((sess, w)) if w else None
                if not prints:
                    continue
                print_ts = [ts for ts, _ in prints]
                for i in idxs:
                    ts = _parse_ts(events[i].ts)
                    pos = bisect_left(print_ts, ts)
                    if pos < len(prints):
                        claim[i] = prints[pos][1]
                    elif (ts - print_ts[-1]).total_seconds() <= DELEGATED_GAP_SEC:
                        claim[i] = prints[-1][1]
                    else:
                        continue
                    if group[0] != "turn":
                        lane_units.add(claim[i])

            # Commits: the squash that landed the PR, and any commit whose hash
            # a claimed event printed as it was made.
            # A commit's repo field is the project slug, so a squash "(#N)"
            # is matched on the number and the nearest merge within the
            # window: two repos can both have a #9.
            merges_by_number: dict[str, list[tuple[datetime, tuple[str, str]]]] = {}
            for key, idxs in merged.items():
                for i in idxs:
                    merges_by_number.setdefault(key[1], []).append((_parse_ts(events[i].ts), key))
            printed_by_unit: dict[str, tuple[str, str]] = {}
            for i, key in claim.items():
                md = events[i].metadata if isinstance(events[i].metadata, dict) else {}
                for sha in md.get("printed_shas") or ():
                    printed_by_unit.setdefault(sha, key)
            for i in t.event_ids:
                if i in claim or i >= len(events) or events[i].kind != "commit":
                    continue
                md = events[i].metadata if isinstance(events[i].metadata, dict) else {}
                made = _parse_ts(events[i].ts)
                m = _SQUASH_TITLE.search((events[i].content_snippet or "").split("\n", 1)[0])
                near = [(abs(at - made), key) for at, key in merges_by_number.get(m.group(1), ())
                        if abs(at - made) <= SQUASH_COMMIT_WINDOW] if m else []
                if near:
                    claim[i] = min(near, key=lambda x: x[0])[1]
                    continue
                sha = str(md.get("short_sha") or "")[:7]
                if sha and sha in printed_by_unit:
                    claim[i] = printed_by_unit[sha]

            units: dict[tuple[str, str], Task] = {}
            for key, idxs in merged.items():
                first = events[min((i for i in claim if claim[i] == key), key=lambda i: _parse_ts(events[i].ts))].ts
                title = (events[idxs[0]].content_snippet or "").strip().split("\n", 1)[0][:80] or f"PR #{key[1]}"
                unit = Task(task_id=_stable_task_id(project, first, f"pr:{key[0]}#{key[1]}", set()),
                            project=project, title=title, delegated=True, unit_kind="pr")
                if t.board_task:
                    # Split from a board task's own unit: the PR was opened
                    # in that board task's turn, so it keeps the goal.
                    unit.board_task, unit.link_source = t.board_task, "host"
                unit.segmentation_trace.append({"event_idx": -1, "rule": "merged_pr_unit",
                                                "detail": f"{key[0]}#{key[1]} from {t.task_id}"})
                units[key] = unit
            for i in sorted(claim, key=lambda i: _parse_ts(events[i].ts)):
                units[claim[i]].touch(i, _parse_ts(events[i].ts), "merged_pr_unit",
                                      detail=f"{claim[i][0]}#{claim[i][1]}", session_id=events[i].session_id)
            _drop_events(t, set(claim), events)
            t.segmentation_trace.append({"event_idx": -1, "rule": "split_to_pr_units",
                                         "detail": ",".join(u.task_id for u in units.values())})
            if not t.event_ids:
                project_tasks.remove(t)
            project_tasks.extend(units.values())
            stats["tasks_split"] += 1
            stats["units_made"] += len(units)
            stats["units_with_a_lane"] += len(lane_units)
    return stats


def merged_pr_units(tasks: list[Task], events: list[Event]) -> dict[str, int]:
    """How the week's merged PRs sit on tasks: how many there are, how many
    are on a task, and how many tasks hold more than one. Printed every run
    with its expected value (the last must be 0), the way the printed-artifact
    credit is, so a pooled prompt is a number and not a claim."""
    aliases = _renamed_repos(events)
    task_of = {i: id(t) for t in tasks for i in t.event_ids}
    keys_by_task: dict[int, set[tuple[str, str]]] = {}
    all_keys: set[tuple[str, str]] = set()
    placed: set[tuple[str, str]] = set()
    for i, ev in enumerate(events):
        if ev.kind != "pr_merged":
            continue
        key = _pr_key(ev, aliases)
        if key is None:
            continue
        all_keys.add(key)
        if i in task_of:
            placed.add(key)
            keys_by_task.setdefault(task_of[i], set()).add(key)
    return {"merged_prs": len(all_keys), "on_a_task": len(placed),
            "tasks_with_two_or_more": sum(1 for ks in keys_by_task.values() if len(ks) >= 2)}


def lead_lane_report(tasks: list[Task], events: list[Event]) -> dict[str, Any]:
    """Where a lead's own-lane events sit. A lead session is one that
    dispatched a lane or whose turns were mostly woken by agents. Printed
    every run so a lead's day cannot quietly become one unit again:

    - `lead_lane_events`: main-lane agent events of lead sessions;
    - `coordination`: per project, the units (one per day), their events, and
      the share of the project's lead-lane events in them;
    - `widest_unit`: the unit holding the widest span of lead-lane events. A
      board task's unit spans the board task's life by design;
    - `widest_pass5_unit`: the widest of the units pass 5 makes (lane,
      lead_worktree, coordination), over all their events. A coordination
      unit is one calendar day by construction; a worktree unit is one
      piece of work however long; a lane is one lane. None is cut by time,
      and the lane and worktree kinds can legitimately pass a day.
    """
    lead = _lead_sessions(events)
    task_of: dict[int, Task] = {i: t for t in tasks for i in t.event_ids}
    per_project: dict[str, dict[str, Any]] = {}
    span: dict[int, tuple[datetime, datetime, Task]] = {}
    total = 0
    for i, ev in enumerate(events):
        md = ev.metadata if isinstance(ev.metadata, dict) else {}
        if (ev.actor != "agent" or ev.kind not in _DELEGATED_KINDS
                or (md.get("agent_lane") or "") != "main" or (ev.project, ev.session_id) not in lead):
            continue
        total += 1
        row = per_project.setdefault(ev.project, {"lead_lane_events": 0, "coordination_events": 0, "units": set()})
        row["lead_lane_events"] += 1
        t = task_of.get(i)
        if t is None:
            continue
        if t.unit_kind == "coordination":
            row["coordination_events"] += 1
            row["units"].add(t.task_id)
        ts = _parse_ts(ev.ts)
        lo, hi, _ = span.get(id(t), (ts, ts, t))
        span[id(t)] = (min(lo, ts), max(hi, ts), t)

    def row(t: Task, lo: datetime, hi: datetime) -> dict[str, Any]:
        return {"task_id": t.task_id, "unit_kind": t.unit_kind or "conversation",
                "hours": round((hi - lo).total_seconds() / 3600, 1)}

    widest = max((row(t, lo, hi) for lo, hi, t in span.values()), key=lambda r: r["hours"], default=None)
    pass5 = None
    for t in tasks:
        if t.unit_kind not in ("lane", "lead_worktree", "coordination") or not t.event_ids:
            continue
        tss = [_parse_ts(events[i].ts) for i in t.event_ids if i < len(events)]
        r = row(t, min(tss), max(tss))
        if pass5 is None or r["hours"] > pass5["hours"]:
            pass5 = r

    coordination = {
        p: {"units": len(r["units"]), "events": r["coordination_events"],
            "share": (r["coordination_events"] / r["lead_lane_events"]) if r["lead_lane_events"] else 0.0}
        for p, r in sorted(per_project.items())
    }
    return {"lead_sessions": len(lead), "lead_lane_events": total, "coordination": coordination,
            "widest_unit": widest, "widest_pass5_unit": pass5}


def printed_artifact_credit(tasks: list[Task], events: list[Event]) -> dict[str, int]:
    """How many PRs and commits a transcript event printed as they were made,
    and how many of those sit on a task holding a printing event.

    The same windows the joins use: a PR link within PR_LINK_JOIN_WINDOW of one
    of the PR's events, a hash within COMMIT_PRINT_BEFORE / COMMIT_PRINT_AFTER
    of the commit. A printed artifact on a task that printed none of it is
    credited to somebody else's work. A squash commit is the exception: it
    belongs with the PR it lands, and counts as credited there. So is a PR
    unit cut from the task that printed it: the opening turn stayed with
    the lead because it opened several PRs, and the unit is still that
    session's work. Printed every run so the join's reach is a number, not
    a claim."""
    task_of = {i: id(t) for t in tasks for i in t.event_ids}
    by_task_id = {t.task_id: id(t) for t in tasks}
    # A unit's origin: the task it was split from (pass 9) or the coordination
    # day its PR was moved off (pass 6d), when that task survives.
    origin: dict[int, int] = {}
    for t in tasks:
        for step in t.segmentation_trace:
            if step.get("rule") in ("merged_pr_unit", "pr_to_its_lane") and " from " in str(step.get("detail", "")):
                src = by_task_id.get(step["detail"].rsplit(" from ", 1)[1])
                if src is not None:
                    origin[id(t)] = src
                break

    def same_work(a: int | None, b: int | None) -> bool:
        return a is not None and (a == b or origin.get(a) == b)
    merges: dict[str, list[tuple[datetime, int]]] = {}
    for i, ev in enumerate(events):
        if ev.kind == "pr_merged" and isinstance(ev.metadata, dict):
            merges.setdefault(str(ev.metadata.get("number")), []).append((_parse_ts(ev.ts), i))
    aliases = _renamed_repos(events)
    pr_prints: dict[tuple[str, str], list[tuple[datetime, int]]] = {}
    sha_prints: dict[str, list[tuple[datetime, int]]] = {}
    for i, ev in enumerate(events):
        if ev.actor not in ("agent", "user"):
            continue
        md = ev.metadata if isinstance(ev.metadata, dict) else {}
        keys = {(aliases.get(r.lower(), r.lower()), n) for r, n in _PULL_URL.findall(ev.content_snippet or "")}
        for link in md.get("printed_prs") or ():
            if "#" in link:
                name, num = link.rsplit("#", 1)
                keys.add((aliases.get(name.lower(), name.lower()), num))
        for key in keys:
            pr_prints.setdefault(key, []).append((_parse_ts(ev.ts), i))
        for sha in md.get("printed_shas") or ():
            sha_prints.setdefault(sha, []).append((_parse_ts(ev.ts), i))

    out = {"prs_printed": 0, "prs_on_printer": 0, "commits_printed": 0, "commits_on_printer": 0}
    seen_prs: dict[tuple[str, str], bool] = {}
    for i, ev in enumerate(events):
        md = ev.metadata if isinstance(ev.metadata, dict) else {}
        at = _parse_ts(ev.ts)
        if ev.kind in ("pr_opened", "pr_merged"):
            key = (str(md.get("repo") or "").lower(), str(md.get("number")))
            near = [j for ts, j in pr_prints.get(key, ()) if abs(ts - at) <= PR_LINK_JOIN_WINDOW]
            if near:
                # Opened and merged both count: a PR is credited only when
                # each of its printed events sits with a task that printed it.
                on = any(same_work(task_of.get(i), task_of.get(j)) for j in near)
                seen_prs[key] = seen_prs.get(key, True) and on
        elif ev.kind == "commit" and md.get("short_sha"):
            near = [j for ts, j in sha_prints.get(str(md["short_sha"])[:7], ())
                    if at - COMMIT_PRINT_BEFORE <= ts <= at + COMMIT_PRINT_AFTER]
            if near:
                out["commits_printed"] += 1
                m = _SQUASH_TITLE.search((ev.content_snippet or "").split("\n", 1)[0])
                landed = [j for ts, j in merges.get(m.group(1), ())
                          if abs(ts - at) <= SQUASH_COMMIT_WINDOW] if m else []
                out["commits_on_printer"] += any(same_work(task_of.get(i), task_of.get(j))
                                                 for j in (landed or near))
    out["prs_printed"] = len(seen_prs)
    out["prs_on_printer"] = sum(seen_prs.values())
    return out


COMMIT_PRINT_BEFORE = timedelta(minutes=2)   # a result stamped just before the commit's own time
COMMIT_PRINT_AFTER = timedelta(minutes=15)   # `git commit`, then a push or a log, a few minutes on
# A commit rebased or rebuilt before landing keeps its author time and loses the
# hash its session printed. A tool result in the same project within this of the
# author time is the command that made it (fc493d0, week of 2026-09-07: 0s).
COMMIT_SAME_SECOND = timedelta(seconds=5)


def _turn_key(ev: Event) -> tuple:
    md = ev.metadata if isinstance(ev.metadata, dict) else {}
    return (ev.project, ev.session_id, md.get("agent_lane") or "", md.get("turn"))


_SQUASH_TITLE = re.compile(r"\(#(\d+)\)\s*$")
SQUASH_COMMIT_WINDOW = timedelta(minutes=10)


def _join_squash_commits_to_their_pr(tasks_by_project: dict[str, list[Task]], events: list[Event]) -> int:
    """Merge a commit-only task into the task holding the PR it is the squash of.

    GitHub titles a squash merge "<PR title> (#N)" and stamps it at the merge.
    The lead that merges a builder's PR prints that hash, so the print join
    put the squash on the lead while the PR sat with its builder, and one
    change was priced twice: week of 2026-09-07, a lead's task was estimated
    at 80h from 22 squash commits, 21 of whose PRs sat on other tasks.
    Matched on the number plus a merge within SQUASH_COMMIT_WINDOW of the
    commit, because a commit's repo field is the project slug, not the repo.
    Every squash commit in the task must resolve to one host.
    """
    merged_at: dict[str, list[tuple[datetime, Task]]] = {}
    orphans: list[tuple[str, Task]] = []
    for project, project_tasks in tasks_by_project.items():
        for t in project_tasks:
            kinds = {events[i].kind for i in t.event_ids if i < len(events)}
            if kinds == {"commit"}:
                orphans.append((project, t))
            for i in t.event_ids:
                if i < len(events) and events[i].kind == "pr_merged":
                    md = events[i].metadata if isinstance(events[i].metadata, dict) else {}
                    merged_at.setdefault(str(md.get("number")), []).append((_parse_ts(events[i].ts), t))
    joined = 0
    for oproject, orphan in orphans:
        hosts: dict[int, Task] = {}
        resolved = True
        for i in orphan.event_ids:
            m = _SQUASH_TITLE.search((events[i].content_snippet or "").split("\n", 1)[0])
            made = _parse_ts(events[i].ts)
            near = [(abs(at - made), id(h), h) for at, h in merged_at.get(m.group(1), ())
                    if abs(at - made) <= SQUASH_COMMIT_WINDOW] if m else []
            if not near:
                resolved = False
                break
            h = min(near, key=lambda x: x[:2])[2]
            hosts[id(h)] = h
        if not resolved or len(hosts) != 1:
            continue
        host = next(iter(hosts.values()))
        if host is orphan or _two_board_tasks(host, orphan):
            continue
        _merge_tasks(host, orphan, f"squash_commit_join:{oproject}")
        tasks_by_project[oproject].remove(orphan)
        joined += 1
    return joined


def _join_commits_to_printing_tasks(tasks_by_project: dict[str, list[Task]], events: list[Event]) -> int:
    """Merge each commit-only task into the task whose tool result printed its
    hash when it was made.

    `git commit` prints the new hash in the same second, so a result naming it
    within COMMIT_PRINT_BEFORE / COMMIT_PRINT_AFTER of the commit is the session
    that made it. A later `git log` that lists it is outside the window and does
    not vote. Hosts need no message of his: an agent-woken session that commits
    is still the work. Crosses projects: the host keeps the work. Most printing
    events win; ties go to the nearest in time.

    When the session that printed it is on no task (an agent-woken turn nothing
    else claimed), the commit's task adopts that turn's unattached events
    instead: week of 2026-09-07, two commits had no wall clock beside the
    turn that made them (00b49fbc55, 75414308e7).

    With neither, a task in the commit's project with a tool result within
    COMMIT_SAME_SECOND of its author time takes it: the hash was rebuilt after
    the session printed its own.
    """
    printed: dict[str, list[tuple[datetime, Task]]] = {}
    results: dict[str, list[tuple[datetime, Task]]] = {}  # project -> tool results on a task
    orphans: list[tuple[str, Task]] = []
    attached: set[int] = set()
    for project, project_tasks in tasks_by_project.items():
        for t in project_tasks:
            attached.update(t.event_ids)
            kinds = {events[i].kind for i in t.event_ids if i < len(events)}
            if kinds and kinds <= _ARTIFACT_ONLY_KINDS:
                if "commit" in kinds:
                    orphans.append((project, t))
                continue
            for i in t.event_ids:
                if i >= len(events):
                    continue
                md = events[i].metadata if isinstance(events[i].metadata, dict) else {}
                for sha in md.get("printed_shas") or ():
                    printed.setdefault(sha, []).append((_parse_ts(events[i].ts), t))
                if events[i].kind == "tool_result":
                    results.setdefault(events[i].project, []).append((_parse_ts(events[i].ts), t))
    for lst in results.values():
        lst.sort(key=lambda x: x[0])
    results_ts = {p: [ts for ts, _ in v] for p, v in results.items()}
    loose_printed: dict[str, list[tuple[datetime, int]]] = {}
    loose_turns: dict[tuple, list[int]] = {}
    for i, ev in enumerate(events):
        md = ev.metadata if isinstance(ev.metadata, dict) else {}
        if i in attached:
            continue
        for sha in md.get("printed_shas") or ():
            loose_printed.setdefault(sha, []).append((_parse_ts(ev.ts), i))
        if md.get("turn") is not None:
            loose_turns.setdefault(_turn_key(ev), []).append(i)
    if not orphans or not (printed or loose_printed or results):
        return 0

    joined = 0
    for oproject, orphan in orphans:
        votes: dict[int, list] = {}  # id(host) -> [host, count, nearest gap]
        loose: list[tuple[timedelta, int]] = []
        same_second: dict[int, list] = {}
        for i in orphan.event_ids:
            ev = events[i]
            md = ev.metadata if isinstance(ev.metadata, dict) else {}
            if ev.kind != "commit" or not md.get("short_sha"):
                continue
            made = _parse_ts(ev.ts)
            tss = results_ts.get(ev.project, [])
            for at, host in results.get(ev.project, [])[bisect_left(tss, made - COMMIT_SAME_SECOND):
                                                         bisect_right(tss, made + COMMIT_SAME_SECOND)]:
                if not _two_board_tasks(host, orphan):
                    v = same_second.setdefault(id(host), [host, 0, abs(at - made)])
                    v[2] = min(v[2], abs(at - made))
            sha = str(md["short_sha"])[:7]
            for at, host in printed.get(sha, ()):
                if not (made - COMMIT_PRINT_BEFORE <= at <= made + COMMIT_PRINT_AFTER) or _two_board_tasks(host, orphan):
                    continue
                v = votes.setdefault(id(host), [host, 0, abs(at - made)])
                v[1] += 1
                v[2] = min(v[2], abs(at - made))
            loose.extend((abs(at - made), j) for at, j in loose_printed.get(sha, ())
                         if made - COMMIT_PRINT_BEFORE <= at <= made + COMMIT_PRINT_AFTER and j not in attached)
        if not votes and loose:
            j = min(loose)[1]
            md = events[j].metadata if isinstance(events[j].metadata, dict) else {}
            turn = loose_turns.get(_turn_key(events[j]), [j]) if md.get("turn") is not None else [j]
            for k in turn:
                if k not in attached:
                    orphan.touch(k, _parse_ts(events[k].ts), rule="commit_print_turn",
                                 detail=events[k].kind, session_id=events[k].session_id)
                    attached.add(k)
                    for sha in (events[k].metadata or {}).get("printed_shas") or ():
                        printed.setdefault(sha, []).append((_parse_ts(events[k].ts), orphan))
            continue
        if not votes and same_second:
            votes = same_second
        if not votes:
            continue
        host = max(votes.values(), key=lambda v: (v[1], -v[2].total_seconds()))[0]
        _merge_tasks(host, orphan, f"commit_print_join:{oproject}")
        tasks_by_project[oproject].remove(orphan)
        joined += 1
    return joined


def _apply_manual_merges(tasks_by_project: dict[str, list[Task]],
                         merges: list[dict] | None) -> int:
    """Absorb human-declared tasks into a same-project neighbor.

    Each directive: {project, absorb_first_ts, [into_first_ts], [reason]}.
    `absorb_first_ts` / `into_first_ts` are ISO-timestamp *prefixes* matched
    against each task's first-event timestamp (stable across re-segmentation).
    If `into_first_ts` is omitted, the task is merged into its immediate
    same-project predecessor by start time (the user's "merge into the preceding
    task"). Idempotent: a directive whose absorb task is already gone is a no-op.
    """
    applied = 0
    for m in merges or []:
        project = m.get("project")
        absorb_prefix = m.get("absorb_first_ts")
        if not project or not absorb_prefix:
            continue
        tasks = tasks_by_project.get(project)
        if not tasks:
            continue

        def _ts(t: Task) -> str:
            return t.first_event_ts.isoformat() if t.first_event_ts else ""

        absorb = next((t for t in tasks if _ts(t).startswith(absorb_prefix)), None)
        if absorb is None:
            continue
        into_prefix = m.get("into_first_ts")
        if into_prefix:
            target = next((t for t in tasks if _ts(t).startswith(into_prefix)), None)
        else:
            earlier = [t for t in tasks
                       if t is not absorb and t.first_event_ts and absorb.first_event_ts
                       and t.first_event_ts < absorb.first_event_ts]
            target = max(earlier, key=lambda t: t.first_event_ts) if earlier else None
        if target is None or target is absorb:
            continue
        _merge_tasks(target, absorb, m.get("reason", "manual merge (the user review)"))
        tasks.remove(absorb)
        applied += 1
    return applied


