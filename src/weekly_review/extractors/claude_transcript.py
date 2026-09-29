"""Extract events from Claude Code transcript JSONL.

Adapted from patterns in analyze_transcript.py (ai-project-support, internal).
See ATTRIBUTIONS.md.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterator

from ..config import ProjectsConfig
from ..events import Event


SYSTEM_MESSAGE_PATTERNS = [
    re.compile(r"^Base directory for this skill:"),
    # Slash-command wrappers: <command-name>, <command-message>, <command-args>.
    re.compile(r"^<(command-name|command-message|command-args|local-command|system-reminder)"),
    re.compile(r"^<local-command-caveat>"),
    re.compile(r"^This session is being continued from a previous conversation"),
    # `claude --continue` auto-injects this exact synthetic user message at
    # session resume. Not the user typing.
    re.compile(r"^Continue from where you left off\.?\s*$", re.IGNORECASE),
    # Inter-agent channel messages (claude-hive, notion-channel, github-channel,
    # discord) are routed INTO the transcript as user-role messages but are
    # not hands-on input from the user.
    re.compile(r"^<channel\s+source=", re.IGNORECASE),
    # Scheduled loop / cron triggers produce content like "Scheduled <prompt>" or
    # auto-loop sentinels. Filter the common patterns.
    re.compile(r"^Scheduled .*check-in"),
    re.compile(r"^<<autonomous-loop", re.IGNORECASE),
    # Loop-tick synthetic prompts from the /loop skill (autonomous self-pacing).
    re.compile(r"^Loop tick\b", re.IGNORECASE),
    # Bare slash-command invocations as the WHOLE message ("/doctor", "/clear").
    # Must be the only content on the line — won't match "/doctor please run a check".
    re.compile(r"^/[a-z][a-z0-9_-]*\s*$"),
    # Caveats that the transcript viewer inserts.
    re.compile(r"^Caveat:"),
    # Background task-runner notifications posted back into the conversation.
    re.compile(r"^<task-notification>", re.IGNORECASE),
    # Tool-result payloads echoed as user messages (they carry tool_use_id refs).
    re.compile(r"^<tool_result>", re.IGNORECASE),
    # Output-exceeded-limit injections.
    re.compile(r"^Tool output exceeded token budget"),
    re.compile(r"^The user doesn't want to"),
    # rare-disease-benchmark harness synthesizes role-play prompts as user-role
    # messages — they're test cases, not the user typing.
    re.compile(r"^You are a rare disease diagnostic expert\b", re.IGNORECASE),
    re.compile(r"^You are the lead physician coordinating\b", re.IGNORECASE),
    re.compile(r"^You are helping a clinician investigate\b", re.IGNORECASE),
    # Peer relay: another Claude session's message injected into this one's
    # conversation as a user-role record. The harness wraps the payload in
    # <teammate-message>, <agent-message> or <cross-session-message>. These are
    # agent-to-agent traffic and by far the largest source of phantom "typing"
    # time — the payloads are long (JSON status blobs, summaries) and they carry
    # no promptSource marker, so only the text prefix identifies them.
    re.compile(r"^Another Claude session sent a message", re.IGNORECASE),
    # Harness markers emitted when the human hits Esc. Not typed prose.
    re.compile(r"^\[Request interrupted", re.IGNORECASE),
]

# `promptSource` values that mean "the harness injected this", not "the human
# typed this". Cron/scheduled runs and loop ticks arrive as promptSource=system
# (usually alongside isMeta). Real human input is `typed` or `queued`.
MACHINE_PROMPT_SOURCES = {"system"}

# promptSource=sdk says only that an SDK client sent the prompt, not who wrote
# it. Headless `claude -p` runs (launchd jobs, test rigs, probes) are
# entrypoint=sdk-cli, and every sdk-cli prompt in the archive through
# 2026-09-28 was machine input. But the desktop app also sends sdk, with
# entrypoint=claude-desktop, and those prompts are typed by the human.
MACHINE_SDK_ENTRYPOINTS = {"sdk-cli"}


def _is_machine_prompt_source(rec: dict) -> bool:
    src = rec.get("promptSource")
    if src in MACHINE_PROMPT_SOURCES:
        return True
    return src == "sdk" and rec.get("entrypoint") in MACHINE_SDK_ENTRYPOINTS

SNIPPET_LIMIT = 500
USER_SNIPPET_LIMIT = 2000  # user messages get more room for post-hoc validation


# A workspace comment delivered into an agent's conversation. Only the tag's
# addressing attributes are read; the comment body never leaves this function.
_CHANNEL_TAG = re.compile(r"^<channel\s+([^>]*)>")
_CHANNEL_ATTR = re.compile(r'(\w+)="([^"]*)"')
# Resolving or reopening a thread addresses it just as a reply does: on the week
# of 2026-09-07, 103 of his resolves and 6 reopens woke agents whose work then
# sat on whatever the session last typed.
_COMMENT_EVENTS = {"thread.created", "thread.replied", "thread.resolved", "thread.reopened"}


def comment_wake_key(text: str) -> str | None:
    """`<doc_id>|<thread_id>` when this record delivers a workspace comment.

    The agent turn it starts is the agent answering that comment, so the turn
    belongs to the task holding the comment, not to whatever the session's
    human last typed.
    """
    m = _CHANNEL_TAG.match(text)
    if not m:
        return None
    attrs = dict(_CHANNEL_ATTR.findall(m.group(1)))
    source = attrs.get("source", "")
    if "workspaces" not in source and "live-feedback" not in source:
        return None
    if attrs.get("event") not in _COMMENT_EVENTS or not attrs.get("doc_id") or not attrs.get("thread_id"):
        return None
    return f"{attrs['doc_id']}|{attrs['thread_id']}"


# Records that carry on the turn before them rather than start new work: a
# compaction summary resumes whatever was running, a background task reports
# back to the turn that launched it, and harness bookkeeping answers nothing.
# On the week of 2026-09-07, compaction summaries started 50h of estimated work
# that read as agent-only because the ask sat before the compaction.
_CARRIES_ON = [
    re.compile(r"^This session is being continued from a previous conversation"),
    re.compile(r"^Continue from where you left off\.?\s*$", re.IGNORECASE),
    re.compile(r"^<task-notification>", re.IGNORECASE),
    re.compile(r"^<(command-name|command-message|command-args|local-command|system-reminder)"),
    re.compile(r"^<local-command-caveat>"),
    re.compile(r"^Caveat:"),
    re.compile(r"^Base directory for this skill:"),
    re.compile(r"^\[Request interrupted", re.IGNORECASE),
    re.compile(r"^Tool output exceeded token budget"),
    re.compile(r"^<tool_result>", re.IGNORECASE),
]
# His review answer reaches the agent unsigned; agents answer a review item a
# tenth as often (42 of 613 answers in the board log), so it reads as his.
_HIS_UNSIGNED_EVENTS = {"workspace.review_answered"}
# His wakes that often name no board task: a review answer's delivery carries
# only the board, and a dictation names a task only when he spoke on its page.
# On the week of 2026-09-07, 28 answers and 28 dictations started turns naming
# no task. Segmentation finds his board event behind each one.
_BOARD_ASK_WAKES = {"workspace.review_answered", "voice.request"}


def unkeyed_board_wake(text: str, trigger: str | None, task: str | None) -> dict:
    """{"ask_wake": event, "ask_wake_ws": board} for a turn his review answer or
    dictation started without naming its board task; {} otherwise."""
    m = _CHANNEL_TAG.match(text.strip())
    if trigger != "him" or task or not m:
        return {}
    attrs = dict(_CHANNEL_ATTR.findall(m.group(1)))
    if attrs.get("event") not in _BOARD_ASK_WAKES or "workspaces" not in attrs.get("source", ""):
        return {}
    return {"ask_wake": attrs["event"], **({"ask_wake_ws": attrs["workspace_id"]} if attrs.get("workspace_id") else {})}


def turn_trigger(rec: dict, text: str, previous: tuple[str | None, str | None],
                 is_subject) -> tuple[str | None, str | None]:
    """(who started this turn, the board task its wake names).

    "him" when he typed it or a workspace event he authored woke it (a comment,
    a review answer, a dictation); "agent" when a peer, a board notice, a
    schedule or another agent did. A record that carries on the previous turn
    keeps that turn's trigger. Only the tag's addressing attributes are read.
    """
    t = text.strip()
    m = _CHANNEL_TAG.match(t)
    if m:
        attrs = dict(_CHANNEL_ATTR.findall(m.group(1)))
        task = attrs.get("task_id") if str(attrs.get("task_id", "")).startswith("t-") else None
        if "workspaces" not in attrs.get("source", "") and "live-feedback" not in attrs.get("source", ""):
            task = None
        if attrs.get("event") in _HIS_UNSIGNED_EVENTS:
            return "him", task
        author = attrs.get("author")
        return ("him" if author and is_subject(author) else "agent"), task
    if any(p.match(t) for p in _CARRIES_ON):
        return previous
    # A peer's message arrives as a meta record, and it starts the peer's ask,
    # not more of the turn before: on the week of 2026-09-07, 427 of them kept
    # the trigger of the dictation or answer that came before.
    origin = rec.get("origin")
    if _is_system_message(t) or (isinstance(origin, dict) and origin.get("kind") == "peer"):
        return "agent", None
    if rec.get("isMeta"):
        return previous
    if _is_machine_prompt_source(rec):
        return "agent", None
    return "him", None


# Board tools that act ON a card: moving it, reporting on it, asking about it,
# dispatching a builder to it. Reads (list_tasks) and triage (archive_task,
# set_task_goal) touch cards without working them, so they name no card.
_CARD_WORK_TOOLS = {
    "task_transition", "post_status", "report_done_when", "block_task",
    "add_review_item", "revise_review_item", "rewrite_task",
    "register_dispatch", "close_dispatch",
}


def card_of_tool_call(tool_name: str, tool_input) -> str | None:
    """The board card a workspace tool call works on, if it names one."""
    if "workspaces" not in tool_name and "live-feedback" not in tool_name:
        return None
    if tool_name.rsplit("__", 1)[-1] not in _CARD_WORK_TOOLS or not isinstance(tool_input, dict):
        return None
    card = tool_input.get("taskId")
    return card if isinstance(card, str) and card.startswith("t-") else None


# Exactly 12 id characters, not the start of a longer hyphenated word, with an
# uppercase letter or digit among them. "don't-<verb>-the-<noun>" once gave a
# lead's builder the board task `t-<verb>-the-`. A generated id has no
# uppercase or digit about once in 20,000.
_CARD_ID = re.compile(r"(?<![\w'-])t-(?=[\w-]{0,11}[A-Z0-9])[A-Za-z0-9_-]{12}(?!\w)")


def card_named_in_prompt(text: str) -> str | None:
    """The one board card a dispatch prompt names. A lead dispatching builders
    in one turn names a card per prompt; a prompt naming two names neither."""
    cards = set(_CARD_ID.findall(text))
    return cards.pop() if len(cards) == 1 else None


def _is_system_message(text: str) -> bool:
    return any(p.match(text) for p in SYSTEM_MESSAGE_PATTERNS)


def _is_machine_record(rec: dict, text: str) -> bool:
    """True when a user-role record was produced by the harness, not the human.

    Two independent signals, because neither covers the whole population:

    * Structural — `promptSource` names who supplied the prompt, and `isMeta`
      marks harness bookkeeping records. Preferred when present: it needs no
      knowledge of any particular user's cron prompt wording, so it generalizes
      to anyone running this tool.
    * Textual — peer-relay records carry no promptSource at all, so the
      `<teammate-message>` / `<agent-message>` wrapper prefix is the only
      available tell.

    Missing the machine records inflates hands-on time enormously: in the
    2026-08-10 week, peer relay alone was 132k words against ~12k words the
    human actually typed.
    """
    if _is_machine_prompt_source(rec):
        return True
    if rec.get("isMeta"):
        return True
    return _is_system_message(text)


def _extract_text_and_tools(content) -> tuple[str, list[tuple[str, dict]]]:
    """Return (joined_text, [(tool_name, tool_input)])."""
    if isinstance(content, str):
        return content, []
    texts: list[str] = []
    tools: list[tuple[str, dict]] = []
    for block in content or []:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            texts.append(block.get("text", ""))
        elif btype == "tool_use":
            tools.append((block.get("name", ""), block.get("input", {})))
    return "\n".join(texts), tools


# A commit's abbreviated or full hash, as `git commit`, `git push` and
# `git commit-tree` print it: hex with at least one letter and one digit, so a
# year or an English word never reads as one.
_PRINTED_SHA = re.compile(r"(?<![0-9A-Za-z])(?=[0-9a-f]*[a-f])(?=[0-9a-f]*[0-9])[0-9a-f]{7,40}(?![0-9A-Za-z])")
_PRINTED_PR = re.compile(r"github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)", re.IGNORECASE)
PRINTED_SHAS_MAX = 64


def printed_artifacts(text: str) -> dict:
    """{"printed_shas": [...], "printed_prs": [...]} for the commits and pull
    requests a tool result names, read from its whole text; {} when it names none.

    The session that commits or opens a pull request prints its identity in the
    same second, usually past the 500-character snippet. Joining on it took the
    week of 2026-09-07 from 46 estimated tasks with no wall clock to 35. Shas
    are kept as their first 7 characters (the git log extractor's `short_sha`),
    at most 64 per result."""
    shas = sorted({m[:7] for m in _PRINTED_SHA.findall(text or "")})[:PRINTED_SHAS_MAX]
    prs = sorted({f"{repo.lower()}#{n}" for repo, n in _PRINTED_PR.findall(text or "")})
    return {**({"printed_shas": shas} if shas else {}), **({"printed_prs": prs} if prs else {})}


def _extract_tool_results(content) -> list[dict]:
    """Pull tool_result blocks out of a record's content (if any).

    Tool results are produced by the harness in response to agent tool_use
    calls. The Claude Agent SDK packs them into "user"-role records (because
    structurally the OS responds to the agent), but they are NOT the user typing.
    We surface them as agent-side `tool_result` events so they don't inflate
    user_message counts or active-hours reading-time.
    """
    if not isinstance(content, list):
        return []
    out = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            inner = block.get("content")
            text = ""
            if isinstance(inner, str):
                text = inner
            elif isinstance(inner, list):
                for sub in inner:
                    if isinstance(sub, dict) and sub.get("type") == "text":
                        text += sub.get("text", "")
            out.append({
                "tool_use_id": block.get("tool_use_id"),
                "is_error": bool(block.get("is_error")),
                "text": text,
            })
    return out


def _subject_name_check():
    """is_subject(name) for channel authors, from the user's identity config."""
    from ..identity import load_subject_identity
    ident = load_subject_identity()
    return lambda name: ident.is_subject(name=name)


def extract_from_jsonl(
    path: Path,
    projects: ProjectsConfig,
    source_machine: str,
    is_subagent: bool | None = None,
    is_subject=None,
    seen_uuids: set | None = None,
    generation: str | None = None,
) -> Iterator[Event]:
    """See _extract_from_jsonl. `seen_uuids`, when given, collects the uuid of
    every record read. `generation` marks a recovered transcript generation (see
    recovered_generations): records whose uuid is already in `seen_uuids` are
    skipped, and every event's id carries the generation, because its `seq`
    restarts at 0 and would collide with the current file's."""
    skip = seen_uuids if generation else None
    collect = None if generation else seen_uuids
    for ev in _extract_from_jsonl(path, projects, source_machine, is_subagent, is_subject, collect, skip):
        if generation:
            md = ev.metadata
            md["source_event_id"] = f"{ev.session_id}:{generation}:{md.get('agent_lane') or 'main'}:{ev.seq}"
            md["transcript_generation"] = generation
            # Turns are keyed (project, session, lane, turn) and restart at 0 in
            # the current file too, so a week holding both generations would join
            # the old loop's turn 12 with the new loop's turn 12.
            for k in ("turn", "ask_turn"):
                if md.get(k) is not None:
                    md[k] += GENERATION_TURN_OFFSET
        yield ev


# Added to a recovered generation's turn numbers. No transcript has come within
# three orders of magnitude of it (the largest, de3f8893, has ~40k records).
GENERATION_TURN_OFFSET = 10_000_000


def recovered_generations(history_root: Path, current: list[Path], since_iso: str | None = None) -> list[tuple[Path, str]]:
    """Session transcripts that were overwritten, and survive only in the
    archive's history store: (the largest snapshot of each lost generation, its
    generation id).

    de3f8893, the claude-workspaces lead: on 2026-08-17 at 19:45Z a worktree
    transcript was renamed onto the parent path and replaced four months of the
    lead's main loop. The archiver's `rsync --backup` kept the old file in
    mac_mini_history, which discovery skips, so every week from April to Aug 17
    lost the lead's turns and his messages to it while its subagent lanes stayed,
    orphaned from the turns that dispatched them.

    A generation is the transcript a file begins with: its first record's uuid.
    A snapshot whose generation matches a current copy of the same session is
    an older state of that file and adds nothing. Only top-level session files
    are compared; subagent files are never overwritten this way.
    """
    from datetime import datetime

    def first_uuid(p: Path) -> str | None:
        try:
            with p.open("rb") as f:
                for i, line in enumerate(f):
                    if i >= 64:
                        break
                    try:
                        uid = json.loads(line).get("uuid")
                    except (json.JSONDecodeError, AttributeError):
                        continue
                    if uid:
                        return uid
        except OSError:
            return None
        return None

    cutoff = datetime.fromisoformat(since_iso.replace("Z", "+00:00")).timestamp() if since_iso else None
    live: dict[str, set] = {}
    for p in current:
        live.setdefault(p.stem, set()).add(first_uuid(p))
    lost: dict[tuple[str, str], Path] = {}
    if not history_root.exists():
        return []
    for p in sorted(history_root.glob("*/*/*.jsonl")):
        if p.stem not in live or (cutoff is not None and p.stat().st_mtime < cutoff):
            continue
        uid = first_uuid(p)
        if not uid or uid in live[p.stem]:
            continue
        key = (p.stem, uid)
        if key not in lost or p.stat().st_size > lost[key].stat().st_size:
            lost[key] = p
    return [(p, f"gen-{uid[:8]}") for (_, uid), p in sorted(lost.items())]


def _extract_from_jsonl(
    path: Path,
    projects: ProjectsConfig,
    source_machine: str,
    is_subagent: bool | None = None,
    is_subject=None,
    collect_uuids: set | None = None,
    skip_uuids: set | None = None,
) -> Iterator[Event]:
    """Yield events from one Claude Code transcript JSONL.

    If is_subagent is None, infer from path (contains '/subagents/').
    Subagent transcripts have "user" role records that are synthetic
    parent-agent prompts, NOT the user typing — emit those as actor=agent
    kind=subagent_input so they don't inflate active-time metrics.

    Product-engineer agent runs (cwd starts with `/workspace/`) are an
    analogous case: the agent runs autonomously inside a Docker container
    with a synthesized initial prompt and any subsequent "user"-role
    records are agent-injected (Slack tool calls, follow-up task
    assignments, etc.). Those user records get emitted as
    kind=agent_prompt so the active-hours formula doesn't credit the user
    with typing what was actually a multi-thousand-word ticket spec.
    """
    if is_subagent is None:
        is_subagent = "/subagents/" in str(path)
    if is_subject is None:
        is_subject = _subject_name_check()

    # Which agent produced these events. Every dispatched agent — Agent-tool
    # subagents, workflow agents, in-process team members — gets its own file
    # under <session>/subagents/, so the file stem is a stable per-agent lane id.
    # Parent-loop events share the "main" lane. Lanes are what let
    # agent_lane_hours_by_task credit N agents running concurrently as N× the
    # work, while agent hours stays an elapsed-time measure.
    lane = path.stem if is_subagent else "main"

    seq = 0
    # The comment whose delivery started the current agent turn, if any. Any
    # other inbound record (his typing, a loop tick, a board notice) starts a
    # turn that answers something else and clears it.
    turn_wake: str | None = None
    # Who started the current main-lane turn ("him" or "agent"), and the board
    # task a workspace wake named. See turn_trigger.
    trigger: str | None = None
    # The turn of the message he typed that started the current work, kept
    # through continuations and notifications: an overnight run he asked for
    # is his ask's work however long it has been since he typed.
    ask_turn: int | None = None
    wake_task: str | None = None
    # His review answer or dictation that started the turn without naming its
    # board task: see unkeyed_board_wake.
    board_wake: dict = {}
    # Which turn of this lane an agent event belongs to: every inbound record
    # that is not a tool result starts a new one. Segmentation groups a turn's
    # events by it to see which board card the turn worked on.
    turn = 0
    # tool_use id -> the board card that call worked on, so its result follows it.
    call_card: dict[str, str] = {}
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            uid = rec.get("uuid") if isinstance(rec, dict) else None
            if uid and collect_uuids is not None:
                collect_uuids.add(uid)
            if uid and skip_uuids and uid in skip_uuids:
                continue

            kind_in = rec.get("type")
            if kind_in not in ("user", "assistant"):
                continue

            ts = rec.get("timestamp")
            session_id = rec.get("sessionId") or rec.get("session_id") or "unknown"
            cwd = rec.get("cwd") or ""
            project = projects.slug_for(cwd) or "unknown"
            message = rec.get("message") or {}
            content = message.get("content")
            text, tools = _extract_text_and_tools(content)

            if kind_in == "user":
                # Detect tool_result records — these are produced by the harness
                # responding to agent tool_use calls, NOT the user typing. The SDK
                # tags them with role=user because the OS responds to the agent,
                # but they're agent-side from a "who did the work" standpoint.
                tool_results = _extract_tool_results(message.get("content"))
                if tool_results and not text.strip():
                    for tr in tool_results:
                        tr_words = len(tr["text"].split())
                        yield Event(
                            ts=ts,
                            actor="agent",
                            project=project,
                            session_id=session_id,
                            source_system="claude_transcript",
                            source_machine=source_machine,
                            kind="tool_result",
                            seq=seq,
                            content_snippet=tr["text"][:SNIPPET_LIMIT],
                            metadata={
                                "words": tr_words,
                                "cwd": cwd,
                                "agent_lane": lane,
                                "tool_use_id": tr.get("tool_use_id"),
                                "is_error": tr.get("is_error", False),
                                "turn": turn,
                                # A card call's result is the same board action
                                # coming back: 1,310 of 2,784 landed on another task.
                                **({"card": call_card[tr.get("tool_use_id")]}
                                   if tr.get("tool_use_id") in call_card else {}),
                                **({"wake": turn_wake} if turn_wake else {}),
                                **({"trigger": trigger} if trigger else {}),
                            **({"ask_turn": ask_turn} if ask_turn is not None else {}),
                                **({"wake_task": wake_task} if wake_task else {}),
                                **board_wake,
                                **printed_artifacts(tr["text"]),
                            },
                        )
                        seq += 1
                    continue

                turn += 1
                if not is_subagent:
                    previous = (trigger, wake_task)
                    trigger, wake_task = started = turn_trigger(rec, text, previous, is_subject)
                    # A notification or compaction carries on the work, and the
                    # comment or message that started it, rather than ending it.
                    if started is not previous:
                        turn_wake = comment_wake_key(text.strip())
                        board_wake = {**unkeyed_board_wake(text, trigger, wake_task), **({"ask_wake_at": ts} if ts else {})}
                        board_wake = board_wake if "ask_wake" in board_wake else {}
                        ask_turn = turn if trigger == "him" and not _is_machine_record(rec, text.strip()) else None
                if _is_machine_record(rec, text.strip()):
                    continue
                words = len(text.split())
                if is_subagent:
                    # Synthetic parent-agent prompt — record as agent-side metadata.
                    yield Event(
                        ts=ts,
                        actor="agent",
                        project=project,
                        session_id=session_id,
                        source_system="claude_transcript",
                        source_machine=source_machine,
                        kind="subagent_input",
                        seq=seq,
                        content_snippet=text[:USER_SNIPPET_LIMIT],
                        metadata={
                            "words": words,
                            "cwd": cwd,
                            "agent_lane": lane,
                            "subagent": True,
                            **({"card": card} if (card := card_named_in_prompt(text)) else {}),
                        },
                    )
                    seq += 1
                    continue
                # Product-engineer agent runs in /workspace/ Docker containers.
                # Their "user"-role records are agent-injected, not the user typing —
                # treat them like subagent_input so active-hours stays honest.
                if cwd.startswith("/workspace/"):
                    yield Event(
                        ts=ts,
                        actor="agent",
                        project=project,
                        session_id=session_id,
                        source_system="claude_transcript",
                        source_machine=source_machine,
                        kind="agent_prompt",
                        seq=seq,
                        content_snippet=text[:USER_SNIPPET_LIMIT],
                        metadata={
                            "words": words,
                            "cwd": cwd,
                            "agent_lane": lane,
                            "agent_run": True,
                        },
                    )
                    seq += 1
                    continue
                yield Event(
                    ts=ts,
                    actor="user",
                    project=project,
                    session_id=session_id,
                    source_system="claude_transcript",
                    source_machine=source_machine,
                    kind="user_message",
                    seq=seq,
                    content_snippet=text[:USER_SNIPPET_LIMIT],
                    # chars: full pre-truncation length; feeds the
                    # human_input_chars task metric.
                    metadata={"words": words, "chars": len(text), "cwd": cwd, "turn": turn},
                )
                seq += 1

            elif kind_in == "assistant":
                words = len(text.split())
                model = message.get("model", "")
                if text:
                    yield Event(
                        ts=ts,
                        actor="agent",
                        project=project,
                        session_id=session_id,
                        source_system="claude_transcript",
                        source_machine=source_machine,
                        kind="agent_message",
                        seq=seq,
                        content_snippet=text[:SNIPPET_LIMIT],
                        metadata={
                            "words": words,
                            "model": model,
                            "cwd": cwd,
                            "agent_lane": lane,
                            "turn": turn,
                            **({"wake": turn_wake} if turn_wake else {}),
                            **({"trigger": trigger} if trigger else {}),
                            **({"ask_turn": ask_turn} if ask_turn is not None else {}),
                            **({"wake_task": wake_task} if wake_task else {}),
                            **board_wake,
                        },
                    )
                    seq += 1
                use_ids = [b.get("id") for b in (content if isinstance(content, list) else [])
                           if isinstance(b, dict) and b.get("type") == "tool_use"]
                for n, (tool_name, tool_input) in enumerate(tools):
                    card = card_of_tool_call(tool_name, tool_input)
                    if card and n < len(use_ids) and use_ids[n]:
                        call_card[use_ids[n]] = card
                    yield Event(
                        ts=ts,
                        actor="agent",
                        project=project,
                        session_id=session_id,
                        source_system="claude_transcript",
                        source_machine=source_machine,
                        kind="tool_call",
                        seq=seq,
                        content_snippet=str(tool_input)[:SNIPPET_LIMIT],
                        metadata={
                            "tool_name": tool_name,
                            "model": model,
                            "cwd": cwd,
                            "agent_lane": lane,
                            "turn": turn,
                            **({"card": card} if card else {}),
                            **({"wake": turn_wake} if turn_wake else {}),
                            **({"trigger": trigger} if trigger else {}),
                            **({"ask_turn": ask_turn} if ask_turn is not None else {}),
                            **({"wake_task": wake_task} if wake_task else {}),
                            **board_wake,
                        },
                    )
                    seq += 1


def iter_transcript_files(
    projects_dir: Path,
    since_iso: str | None = None,
) -> Iterator[Path]:
    """Yield JSONL files under the per-cwd directories (recursively).

    Captures both top-level session jsonls AND nested subagent jsonls
    (under `.../<session-uuid>/subagents/`).

    Filters by mtime >= since_iso when supplied (cheap first-pass filter;
    the extractor itself filters per-event by timestamp).
    """
    from datetime import datetime
    cutoff = None
    if since_iso:
        cutoff = datetime.fromisoformat(since_iso.replace("Z", "+00:00")).timestamp()
    if not projects_dir.exists():
        return
    for jsonl in sorted(projects_dir.rglob("*.jsonl")):
        if cutoff is not None and jsonl.stat().st_mtime < cutoff:
            continue
        yield jsonl
