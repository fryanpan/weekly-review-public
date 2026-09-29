"""Extract the user's live-feedback review comments as hands-on events.

Review time (eyeballing a mockup, commenting on a doc) happens in the
live-feedback plugin, NOT in Claude Code transcripts — so it's invisible to
the rest of the pipeline (e.g. Maps/UX mockup review reads ~0 hands-on).
This extractor pulls comment threads from the live-feedback server's HTTP
API and emits one hands-on event per the user comment.

Per the user (2026-06-12): "if I made a comment, assume I was hands-on active
for 2 minutes before I made the comment." We model hands_on per comment as
LIVE_FEEDBACK_REVIEW_SEC (review allowance) + typing time of the comment
text at TYPE_WPM. The effort module honors the baked `fixed_active_sec`
metadata so this doesn't depend on a prior-agent-message (review comments
have none).

Coverage caveat: the HTTP API only exposes ACTIVE (currently-bound) docs.
Archived docs live as Yjs `.ydoc` binaries under the plugin's data/_archive
(back to ~2026-04-17, ≈ the clean-data window) and need a Yjs decoder we
don't have yet — so archived-doc review time is not captured by this v1.
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ..events import Event
from ..identity import SubjectIdentity, load_subject_identity

LIVE_FEEDBACK_REVIEW_SEC = 120  # 2-min review allowance per comment (the user's spec)
TYPE_WPM = 40
DEFAULT_BASE = "http://localhost:8787"
DEFAULT_DATA_DIR = Path.home() / "dev" / "claude-live-feedback-plugin" / "data"

# The plugin renamed itself live-feedback -> claude-workspaces and, on
# 2026-09-01, moved its runtime data out of the repo into Application Support.
# The OLD directory still exists, full of files frozen at the moment of the
# move, so `dd.exists()` stayed true and every run since read a dead store:
# review sessions and review comments both went to zero from 2026-09-02 on,
# with no error. That is 30% of one measured week silently deleted, and audit
# 22's "1 stale source" line was the only trace.
#
# So resolve by CURRENCY, not existence — newest activity.jsonl wins. A path
# that exists proves nothing about whether anything is still writing to it.
DATA_DIR_CANDIDATES = (
    Path.home() / "Library" / "Application Support" / "claude-workspaces" / "data",
    DEFAULT_DATA_DIR,
)


def resolve_data_dir(candidates=DATA_DIR_CANDIDATES) -> Path:
    """Pick the live claude-workspaces data dir among the known locations.

    Ranked by the mtime of each candidate's `activity.jsonl`, so a future
    relocation is picked up the day it happens rather than the day someone
    notices the hands-on numbers sagging.
    """
    present = [p for p in candidates if p.is_dir()]
    if not present:
        return DEFAULT_DATA_DIR

    def freshness(p: Path) -> float:
        act = p / "activity.jsonl"
        try:
            return act.stat().st_mtime
        except OSError:
            return -1.0

    return max(present, key=freshness)


def _get(url: str, timeout: float = 6.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# Keyword → project routing for docs whose sourceUrl doesn't resolve to a file
# path (mockup-preview HTTP URLs, scratch docs) comes from each project's
# `doc_keywords` list in projects.yaml — see `_keyword_routes`. It used to be
# a table in this module; the words are one user's project vocabulary and do
# not belong in source.


def _keyword_routes(projects) -> list[tuple[str, tuple[str, ...]]]:
    """(slug, keywords) for every configured project that lists `doc_keywords`,
    in config order. Lowercased, so a caller matches against a lowercased blob."""
    routes = []
    for p in getattr(projects, "projects", []) or []:
        kws = tuple(k.lower() for k in (getattr(p, "doc_keywords", None) or []))
        if kws:
            routes.append((p.slug, kws))
    return routes


def _norm(name: str) -> str:
    """Lowercase a workspace name into slug shape so it can be compared to one."""
    return "-".join("".join(c if c.isalnum() else " " for c in name.lower()).split())


def board_files(data_dir: Path, suffix: str) -> list[Path]:
    """Every board's `<wsId><suffix>` file, deleted boards included, sorted.

    Deleting a board moves its files to `_archive/<batch>/`. The week of
    2026-08-17 ran on two boards deleted on Aug 19: 2,375 board events,
    among them his review answers, and the creators of the board tasks its
    agents worked, were read by nothing, so their tasks read as his ask on
    one task and the work on another. A board both live and archived is read
    once, from its live file.
    """
    live = sorted((data_dir / "workspaces").glob(f"*{suffix}")) if (data_dir / "workspaces").is_dir() else []
    seen = {p.name for p in live}
    archived = []
    for p in sorted((data_dir / "_archive").glob(f"*/w-*{suffix}")) if (data_dir / "_archive").is_dir() else []:
        if p.name not in seen:
            seen.add(p.name)
            archived.append(p)
    return live + archived


def load_workspace_names(data_dir: Path) -> dict[str, str]:
    """workspaceId -> human name, read from the plugin's own on-disk board files.

    Boards are the missing half of doc routing. A doc's `meta.workspaceId` says
    which board it belongs to, but the id is opaque (`w-<12 chars>`); the name
    that identifies the work ("live-feedback", "client-a-fast-build") lives in
    `workspaces/<id>.tasks.json`. Reading it here keeps the extractor local-first
    — no running server, same as the .ydoc path.
    """
    out: dict[str, str] = {}
    for path in board_files(data_dir, ".tasks.json"):
        try:
            ws = json.loads(path.read_text()).get("workspace") or {}
        except (OSError, ValueError):
            continue
        wid, name = ws.get("id"), ws.get("name")
        if wid and name:
            out[wid] = name
    return out


def load_doc_roots(data_dir: Path) -> dict[str, str]:
    """docId -> the working directory that produced the doc.

    Each doc has an `<docId>.index.json` beside its .ydoc whose `meta` carries
    `owner` (the cwd of the agent that created it) and `workspaceRoot` (the repo
    root it was opened against). Both are ordinary absolute paths, so they route
    through `projects.slug_for()` exactly like a transcript cwd — no new config.

    This is what rescues diff reviews. `create_diff_review` called without a
    board writes `meta.workspaceId` equal to the *setId*, not a `w-…` board, and
    a diff legitimately has no sourceUrl — so board routing and path routing
    both miss and the docs land in `unknown`. In the week of 2026-08-31 that was
    1.6h (14% of hands-on), all of it fast-build review, on the goal the number
    was being computed for. `owner` names that worktree on every one of them.

    Prefer `owner` over `workspaceRoot`: a doc created from a scratchpad carries
    a /private/tmp workspaceRoot that no project pattern should ever match.
    """
    out: dict[str, str] = {}
    if not data_dir.is_dir():
        return out
    files = list(data_dir.glob("*.index.json")) + list((data_dir / "_archive").glob("*.index.json"))
    for path in files:
        try:
            meta = (json.loads(path.read_text()) or {}).get("meta") or {}
        except (OSError, ValueError):
            continue
        root = meta.get("owner") or meta.get("workspaceRoot")
        if root:
            out[meta.get("docId") or path.name.split(".index.json")[0]] = root
    return out


# A task card IS a doc, keyed `task:<taskId>`, so the only part of the
# doc-side stream that can name its task without further instrumentation is the
# card itself. Everything else -- an attached review doc, a diff review, a
# standalone note -- carries no task anywhere in the store, and guessing one
# from proximity is how a doc-side hour ends up billed against work it had
# nothing to do with. The share depends entirely on the window, so state the
# denominator with it: on 2026-08-31..09-06 it is 44.2% of doc events (662 of
# 1,499), which reproduces the ticket's "45%" almost exactly — but over the
# WHOLE store it is 16.8% of person read sessions (9.0% of their hours) and
# 16.2% of the user's comments, because task cards are recent and the archive is
# mostly older standalone docs. 39%/37% of the rest sit on a board but on a doc
# with no task; the remainder are on no board at all. A run prints the current
# share on stderr so the two are never confused again.
_TASK_DOC_PREFIX = "task:"


def workspace_task_id(doc_id: str) -> str:
    """The workspace taskId this doc IS, or "" when the doc is not a task card.

    Deliberately not a guess. A doc that merely sits on a board whose tasks are
    known still returns "" -- the board tells you the project, never the task.
    """
    if not doc_id or not doc_id.startswith(_TASK_DOC_PREFIX):
        return ""
    return doc_id[len(_TASK_DOC_PREFIX):]


# The resolution is always stamped, never merely absent. An absent field cannot
# be told apart from an extractor that forgot to look, which is how a coverage
# number ends up counting a bug as a clean miss.
NO_TASK = "no-task"


def _task_stamp(doc_id: str, doc_links: dict[str, frozenset[str]] | None = None) -> dict[str, Any]:
    """Always-present task resolution for a doc event.

    `task-card`: the doc IS the task. `task-link`: exactly one board task
    links the doc (the user's 2026-09-17 rule: no judgment needed). `multi-task`:
    several do, and `task_candidates` names them for the classifier. Else
    NO_TASK.
    """
    task_id = workspace_task_id(doc_id)
    if task_id:
        return {"task_id": task_id, "task_resolution": "task-card"}
    linked = (doc_links or {}).get(doc_id) or ()
    if len(linked) == 1:
        return {"task_id": next(iter(linked)), "task_resolution": "task-link"}
    if len(linked) > 1:
        return {"task_resolution": "multi-task", "task_candidates": sorted(linked)}
    return {"task_resolution": NO_TASK}


def load_board_projects(projects, doc_boards: dict[str, str],
                        doc_roots: dict[str, str]) -> dict[str, str]:
    """board name -> project slug, derived from where the board's docs were made.

    The user's 2026-09-08 ruling puts the board ahead of the file path, but a board
    name is not a slug: `Team Lead` is `ai-team-lead`, `live-feedback` is
    `claude-live-feedback-plugin`, `Finance B` is `finance-tool`.
    Matching the name exactly -- what the code did until now -- fires on 4 of 14
    boards, and on those four the path already agreed, so the ruling was a
    measured no-op. Hand-writing the aliases would work and would need somebody
    to maintain it; the working directories already say it.

    A board is only allowed to name a project when ALL of its rooted docs came
    from one. That is the container test, and it is a measurement rather than a
    list: 12 of 14 boards are 100% single-slug, and the two that are not are
    exactly the two places that hold other projects' work -- `Unfiled` (128
    docs, 8 slugs) and `Team Lead` (31 docs, 4). Those two are also where all 7
    docs live that a board-first rule would route away from a working directory
    that is right: 5 fast-build docs, `reconnect-merged-2026-08-18`
    (contacts-tool) and `weekly-review-methodology`, all filed on Team Lead
    because that is who was reading them, not what they were about.

    So a container abstains and the path answers. Measured 2026-09-12.
    """
    by_board: dict[str, set[str]] = {}
    for doc_id, root in doc_roots.items():
        board = doc_boards.get(doc_id, "")
        if not board:
            continue
        slug = projects.slug_for(root or "")
        if slug and slug != "unknown":
            by_board.setdefault(board, set()).add(slug)
    return {board: next(iter(slugs)) for board, slugs in by_board.items()
            if len(slugs) == 1}


def _route_doc(projects, source_url: str, doc_id: str, title: str, workspace_name: str = "",
               doc_root: str = "", board_projects: dict[str, str] | None = None) -> str:
    """Resolve a project for a live-feedback doc.

    Board first, per the user's 2026-09-08 ruling. A `sourceUrl` and a working
    directory are both just paths, and a path says which checkout a file sat in,
    not which project the work belonged to — so the board, which is where a
    human deliberately filed the thing, outranks both. Keyword hints over the
    text come last, and they are a guess.

    The board only gets to answer when it names one project: `board_projects`
    is derived from the working directories of that board's own docs and omits
    any board that holds more than one (see load_board_projects). Exact
    name-to-slug matching stays as a second board rule, for a board whose docs
    carry no working directory at all.

    Board routing was added 2026-08-24 after the plugin renamed itself from
    live-feedback to claude-workspaces: its docs became opaque `task:t-*` ids
    with an empty sourceUrl, so every keyword missed and 26% of one week's
    hands-on time landed in `unknown` — while coverage still read 100%.
    """
    if workspace_name:
        slug = (board_projects or {}).get(workspace_name, "")
        if slug:
            return slug
        # A board named exactly after a registered project routes without config.
        normalized = _norm(workspace_name)
        for p in projects.projects:
            if p.slug == normalized:
                return p.slug
    slug = projects.slug_for(source_url or "")
    if slug and slug != "unknown":
        return slug
    # The directory the doc was produced in. The only signal a boardless diff
    # review has, and the answer for anything on a container board.
    if doc_root:
        slug = projects.slug_for(doc_root)
        if slug and slug != "unknown":
            return slug
    blob = f"{source_url} {doc_id} {title} {workspace_name}".lower()
    for proj, kws in _keyword_routes(projects):
        if any(k in blob for k in kws):
            return proj
    return "unknown"


def _actor_kind(record: dict) -> str:
    """The actor family an activity record belongs to, from its `actorId` prefix.

    `actor == "person"` does NOT mean a person. The activity stream files
    "Claude", "Live Feedback" and "Chat Audit" under it too, with `agent-`,
    `live-` and `chat-` ids. The prefix is the structural tell, and passing it
    to the identity rule is what stops a machine being judged on its name.
    """
    return str(record.get("actorId") or "").split("-", 1)[0].strip().lower()


def _is_subject(author: dict, identity: SubjectIdentity, ts=None) -> bool:
    """True only for the data subject's own comments.

    Allowlist, not denylist. Agents post under their PROJECT display name —
    "Client A board", "Live Feedback", "Notes Repo", "Health Tool" —
    so the old rule (exclude name == "Agent") let nearly every agent comment
    through as human review time. In the 2026-08-10 week that was 53k of the
    61k live-feedback words, and it landed in hands-on hours.

    Identifying the subject positively also does the right thing with a third
    party: a client reviewer's comment is somebody's time, but it is not *his*,
    so it must not count toward his hands-on total either.

    The rule itself lives in `identity.py` and is shared with read sessions and
    activity touches. It used to live here as `HUMAN_NAME_FRAGMENT = "<first name>"`,
    a single fragment that never matched his own primary handle —
    1,054 of his 2,938 comments, 29%, uncounted by the filter written to keep
    machines out. Meanwhile the read-session path had no allowlist at all. Two
    filters that can drift apart WILL drift apart; there is one now.
    """
    if not isinstance(author, dict):
        return False
    return identity.is_subject(
        name=author.get("name", ""),
        kind=author.get("kind", ""),
        is_owner=bool(author.get("isOwner")),
        ts=ts,
    )


def _comment_event(projects, source_url, doc_id, thread_id, cm, source_machine, title="",
                   workspace_name="", doc_root="", board_projects=None, identity=None,
                   doc_links=None):
    ts_ms = cm.get("ts")
    if not ts_ms:
        return None
    # Identity is judged against the comment's OWN timestamp, because an anon
    # era is a dated exception: the same handle is him in July and is not him
    # in September.
    if not _is_subject(cm.get("author", {}), identity or load_subject_identity(), ts_ms):
        return None
    ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()
    text = cm.get("text", "") or ""
    words = len(text.split())
    fixed = LIVE_FEEDBACK_REVIEW_SEC + words / TYPE_WPM * 60
    project = _route_doc(projects, source_url, doc_id, title, workspace_name, doc_root,
                         board_projects)
    # Comment ids are unique within a thread but NOT globally: 25 of 2,535
    # distinct ids repeat across the store, so the key has to carry the doc and
    # the thread too. Without any of it, dedup keeps one comment per document.
    comment_id = cm.get("id") or f"ts{ts_ms}"
    return Event(
        ts=ts, actor="user", project=project,
        session_id=f"live-feedback:{doc_id}",
        source_system="live_feedback", source_machine=source_machine,
        kind="user_message", seq=0, content_snippet=text[:500],
        metadata={
            "words": words, "fixed_active_sec": round(fixed, 1),
            "doc_id": doc_id, "source_url": source_url,
            "thread_id": thread_id, "via": "live-feedback-review",
            "source_event_id": f"comment:{doc_id}:{thread_id}:{comment_id}",
            **_task_stamp(doc_id, doc_links),
        },
        author_id=cm.get("author", {}).get("id", ""), author_kind="human",
    )


def load_doc_boards(data_dir: Path) -> dict[str, str]:
    """docId -> board name, from each board file's `docIds` list AND its tasks.

    Two kinds of doc live on a board and they are recorded in two different
    places. Attached review docs are listed by id in `workspace.docIds`. Task
    cards are not: each is its own doc keyed `task:<taskId>`, and the only thing
    tying it to a board is the matching entry in the same file's `tasks` array.
    A task card's own .ydoc carries title/docId and NOTHING about its board, so
    reading the cards from here is the only way to route them — which is why the
    whole `task:t-*` population sat in `unknown`.
    """
    out: dict[str, str] = {}
    for path in board_files(data_dir, ".tasks.json"):
        try:
            blob = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        name = ((blob.get("workspace") or {}).get("name")) if isinstance(blob, dict) else None
        if not name:
            continue
        for doc_id in (blob.get("workspace") or {}).get("docIds") or []:
            out[doc_id] = name
        for task in blob.get("tasks") or []:
            tid = task.get("id") if isinstance(task, dict) else None
            if tid:
                out[f"task:{tid}"] = name
    return out


def _iter_from_ydoc_files(projects, data_dir: Path, source_machine: str,
                          doc_boards: dict[str, str] | None = None,
                          doc_roots: dict[str, str] | None = None,
                          board_projects: dict[str, str] | None = None,
                          identity: SubjectIdentity | None = None,
                          doc_links: dict[str, frozenset[str]] | None = None) -> Iterator[Event]:
    """Parse .ydoc Yjs binaries directly (covers ACTIVE + _archive docs — full
    history back to ~2026-04-17 — and needs no running server)."""
    try:
        from pycrdt import Doc, Map
    except ImportError:
        return
    identity = identity or load_subject_identity()
    files = list(data_dir.glob("*.ydoc")) + list((data_dir / "_archive").glob("*.ydoc"))
    workspace_names = load_workspace_names(data_dir)
    seen_docs = set()
    for f in files:
        try:
            doc = Doc()
            meta = doc.get("meta", type=Map)
            threads = doc.get("threads", type=Map)
            doc.apply_update(f.read_bytes())
            mkeys = set(meta.keys())
            doc_id = meta["docId"] if "docId" in mkeys else f.stem
            if doc_id in seen_docs:  # active copy wins over _archive duplicate
                continue
            seen_docs.add(doc_id)
            source_url = meta["sourceUrl"] if "sourceUrl" in mkeys else ""
            title = meta["title"] if "title" in mkeys else ""
            # The board files are the reliable source (see load_doc_boards);
            # `meta.workspaceId` is absent from every task card on disk today,
            # so it is only a fallback for docs the board files don't list.
            ws_name = (doc_boards or {}).get(doc_id, "")
            if not ws_name and "workspaceId" in mkeys:
                ws_name = workspace_names.get(meta["workspaceId"], "")
                if doc_boards is not None and ws_name:
                    doc_boards[doc_id] = ws_name
            for tid in threads.keys():
                th = threads[tid].to_py()
                for cm in th.get("comments", []) or []:
                    ev = _comment_event(projects, source_url, doc_id, tid, cm, source_machine, title,
                                        ws_name, (doc_roots or {}).get(doc_id, ""),
                                        board_projects, identity, doc_links=doc_links)
                    if ev:
                        yield ev
        except Exception:
            continue  # skip corrupt/unreadable docs


def _iter_from_api(projects, base_url: str, source_machine: str,
                   identity: SubjectIdentity | None = None) -> Iterator[Event]:
    """Fallback: pull from the running server's HTTP API (active docs only)."""
    identity = identity or load_subject_identity()
    try:
        docs = _get(f"{base_url}/api/docs").get("docs", [])
    except Exception:
        return
    for doc in docs:
        doc_id = doc.get("docId")
        source_url = doc.get("sourceUrl") or ""
        try:
            threads = _get(f"{base_url}/api/docs/{doc_id}/threads").get("threads", [])
        except Exception:
            continue
        for th in threads:
            for cm in th.get("comments", []):
                ev = _comment_event(projects, source_url, doc_id, th.get("id"), cm,
                                    source_machine, identity=identity)
                if ev:
                    yield ev


def _iter_review_sessions(projects, activity_path: Path, source_machine: str,
                          doc_boards: dict[str, str] | None = None,
                          doc_roots: dict[str, str] | None = None,
                          board_projects: dict[str, str] | None = None,
                          identity: SubjectIdentity | None = None,
                          doc_links: dict[str, frozenset[str]] | None = None) -> Iterator[Event]:
    """Yield kind='review_session' events from the plugin's activity.jsonl.

    These are the interaction-bounded read/edit sessions the activity stream
    records per person (durationMs measured from actual scroll/typing
    interactions — an idle open tab records nothing). They capture the review
    time that comment events miss: reading a doc and typing inline edits
    without ever posting a comment. Only exists from the stream's go-live
    (~2026-06-15); comment history is separately backfilled to ~2026-04-17.

    Event ts = session end; effort counts the [end - duration, end] window as
    measured (not modeled) hands-on. Overlap with comment windows unions away.

    `actor == "person"` is not the data subject. It includes anonymous readers
    of docs he shared -- 1,031 sessions / 22.6h over all history sat in his
    hands-on until this filter existed, and the dedup fix that stopped
    discarding most of them is what made it urgent. The same
    `SubjectIdentity.is_subject` the comment path uses decides it here.
    """
    identity = identity or load_subject_identity()
    for line in activity_path.read_text(errors="replace").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") != "read_session" or d.get("actor") != "person":
            continue
        if not identity.is_subject(name=d.get("actorName", ""),
                                   kind=_actor_kind(d), is_owner=bool(d.get("isOwner")),
                                   ts=d.get("ts")):
            continue
        p = d.get("payload") or {}
        if not p.get("interactionBounded"):
            continue  # pre-bounding sessions could include idle tab time
        dur = float(p.get("durationMs") or 0) / 1000
        if dur <= 0:
            continue
        doc = d.get("doc") or {}
        doc_id = doc.get("docId") or ""
        source_url = doc.get("sourceUrl") or ""
        # `producedBy.cwd` rides along on the activity record itself, so a
        # session on a doc with no index.json still routes.
        root = (doc_roots or {}).get(doc_id, "") or ((doc.get("producedBy") or {}).get("cwd") or "")
        project = _route_doc(projects, source_url, doc_id, doc.get("title") or "",
                             (doc_boards or {}).get(doc_id, ""), root, board_projects)
        end_ts = p.get("endTs") or d.get("ts")
        # `durationMs` is the interaction time INSIDE the session and is shorter
        # than first-interaction -> last-interaction: 21.07h against 23.01h over
        # 1,293 sessions in the week of 2026-08-31, differing on 1,240 of them.
        # The modelled-window metric wants the former (it is charging measured
        # work); the contiguous-block rule wants the latter, because a bounded
        # read session IS a block of interactions and the rule's span is first
        # event to last. Carry both rather than making one of them re-derive.
        start_ts = p.get("startTs") or ""
        # The activity stream stamps every record with its own `eventId`, and
        # all 5,699 person read sessions carry a distinct one. `payload.sessionId`
        # is the fallback for any record written before that field existed.
        source_event_id = d.get("eventId") or p.get("sessionId") or ""
        yield Event(
            ts=end_ts, actor="user", project=project,
            session_id=f"live-feedback:{doc_id}",
            source_system="live_feedback", source_machine=source_machine,
            kind="review_session", seq=0,
            content_snippet=f"review session {dur:.0f}s on {doc_id}",
            metadata={
                "fixed_active_sec": round(dur, 1),
                "doc_id": doc_id, "source_url": source_url,
                **({"start_ts": start_ts} if start_ts else {}),
                "scroll_pct": p.get("maxScrollDepthPct"),
                "via": "live-feedback-activity",
                **({"source_event_id": f"read:{source_event_id}"}
                   if source_event_id else {}),
                **_task_stamp(doc_id, doc_links),
            },
            author_id=d.get("actorId", ""), author_kind="human",
        )


# The activity stream records five more person actions besides `read_session`,
# and none of them was ever ingested: `doc_open`, `comment`, `reply`, `resolve`,
# `reopen` — 1,662 of the user's own touches in the week of 2026-08-31 alone. They
# carry no duration, so under the modelled-window metric they were worth nothing
# and their absence cost nothing. Under the contiguous-block rule they are worth
# a great deal: a block is bridged by the touches inside it, so a doc_open
# between two reads is what makes those reads one block instead of two.
ACTIVITY_TOUCH_TYPES = frozenset({"doc_open", "comment", "reply", "resolve", "reopen"})


def _iter_activity_touches(projects, activity_path: Path, source_machine: str,
                           doc_boards: dict[str, str] | None = None,
                           doc_roots: dict[str, str] | None = None,
                           board_projects: dict[str, str] | None = None,
                           identity: SubjectIdentity | None = None,
                           doc_links: dict[str, frozenset[str]] | None = None) -> Iterator[Event]:
    """Yield kind='workspace_touch' instants from the plugin's activity.jsonl.

    These are stamped moments, not intervals: the store records that a person
    opened a doc, posted, replied, resolved or reopened a thread at an instant,
    and records no duration for any of them. They are the human counterpart of
    `board_action` and are treated the same way — they attach to tasks, they
    extend a task's wall-clock span, and they never create duration on their
    own. What they DO is bridge, under the 60s contiguous-block rule.

    A `comment` record here is the same act as the `user_message` the .ydoc pass
    emits for the same comment. That is deliberate duplication with a purpose:
    the .ydoc event carries the modelled typing window that the transcript-side
    metric wants, this one carries the bare instant that the workspace-side
    block rule wants, and because the block rule treats it as a point the two
    can never add up to more than the moment they describe.

    Identity is filtered by the same `SubjectIdentity.is_subject` as comments
    and read sessions (ruled 2026-09-12). `actorId`,
    `actorName` and the structural `isOwner` flag still ride along in metadata
    so a later ruling can be re-applied without re-deriving who was who.

    A touch bridges, so letting somebody else's touch through is worse here
    than in a duration-bearing source: it costs no hours of its own and can
    still join two of his blocks into one.
    """
    identity = identity or load_subject_identity()
    for line in activity_path.read_text(errors="replace").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("actor") != "person":
            continue
        act_type = d.get("type") or ""
        if act_type not in ACTIVITY_TOUCH_TYPES:
            continue
        ts = d.get("ts")
        if not ts:
            continue
        if not identity.is_subject(name=d.get("actorName", ""),
                                   kind=_actor_kind(d), is_owner=bool(d.get("isOwner")),
                                   ts=ts):
            continue
        doc = d.get("doc") or {}
        doc_id = doc.get("docId") or ""
        source_url = doc.get("sourceUrl") or ""
        root = (doc_roots or {}).get(doc_id, "") or ((doc.get("producedBy") or {}).get("cwd") or "")
        project = _route_doc(projects, source_url, doc_id, doc.get("title") or "",
                             (doc_boards or {}).get(doc_id, ""), root, board_projects)
        source_event_id = d.get("eventId") or ""
        payload = d.get("payload") or {}
        text = str(payload.get("text") or "")
        yield Event(
            ts=ts, actor="user", project=project,
            session_id=f"live-feedback:{doc_id}",
            source_system="live_feedback", source_machine=source_machine,
            kind="workspace_touch", seq=0,
            content_snippet=(text or act_type)[:500],
            metadata={
                "activity_type": act_type,
                "doc_id": doc_id, "source_url": source_url,
                "thread_id": d.get("threadId") or "",
                "actor_name": d.get("actorName") or "",
                "is_owner": bool(d.get("isOwner")),
                "via": "live-feedback-activity-touch",
                **({"source_event_id": f"touch:{source_event_id}"}
                   if source_event_id else {}),
                **_task_stamp(doc_id, doc_links),
            },
            author_id=d.get("actorId", ""), author_kind="human",
        )


def extract_live_feedback(
    projects,
    base_url: str = DEFAULT_BASE,
    data_dir: Path | None = None,
    source_machine: str = "live_feedback",
) -> Iterator[Event]:
    """Yield hands-on events from live-feedback review activity.

    Comments: parsed from the .ydoc files directly (complete: active +
    archived docs, no server needed); HTTP API fallback if the data dir is
    absent. Read/edit sessions: from the plugin's activity.jsonl when present
    (interaction-bounded, measured). The doc's sourceUrl routes via
    projects.slug_for() so review time lands on the right project.
    """
    dd = data_dir or resolve_data_dir()
    # docId -> board name. Seeded from each board's attached-doc list, then
    # extended by the .ydoc pass with the task cards (whose board is on the doc).
    # The review-session pass runs afterwards and reads the finished map.
    doc_boards = load_doc_boards(dd)
    # docId -> producing working directory, for the docs no board claims.
    doc_roots = load_doc_roots(dd)
    board_projects = load_board_projects(projects, doc_boards, doc_roots)
    # Read the identity rule ONCE per run and hand the same object to every
    # path. Three sources loading their own copy is how the two filters this
    # replaces drifted apart in the first place.
    identity = load_subject_identity()
    # docId -> board tasks linking it; a doc one task links is that task's
    # (the user, 2026-09-17). Read once and handed to every path, like identity.
    from .workspace_board import doc_task_links
    doc_links = doc_task_links(dd) if dd.exists() else {}
    if dd.exists():
        yield from _iter_from_ydoc_files(projects, dd, source_machine, doc_boards, doc_roots,
                                         board_projects, identity, doc_links=doc_links)
    else:
        yield from _iter_from_api(projects, base_url, source_machine, identity)
    activity = (dd if dd.exists() else resolve_data_dir()) / "activity.jsonl"
    if activity.exists():
        yield from _iter_review_sessions(projects, activity, source_machine, doc_boards, doc_roots,
                                         board_projects, identity, doc_links=doc_links)
        yield from _iter_activity_touches(projects, activity, source_machine, doc_boards,
                                          doc_roots, board_projects, identity, doc_links=doc_links)
