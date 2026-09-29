"""Extract events from a product-engineer DB dump (full_db_dump.json).

These are Linear tickets the user dispatched via Slack to the product-engineer
agent fleet. Each task carries a user-typed title (the dispatch intent),
a Linear ticket id, slack thread metadata, and a created/updated timestamp.

For weekly-review purposes, each task becomes ONE user_message event:
  - ts = created_at
  - actor = "user" (the user typed this in Slack)
  - kind = "user_message" (drives active_hours via WPM formula)
  - source_system = "linear_task"
  - session_id = task_uuid
  - content_snippet = title

Project routing: each task has a `product` field (e.g. "notes-repo")
which we feed through projects.slug_for() via a synthetic /workspace/<product>
cwd, since /workspace/<slug> patterns are already in projects.yaml.

This is the cheapest way to recover the user's W14-W15 input signal, which was
otherwise lost — those weeks ran via Slack→agent dispatch, not Claude Code.
A future enrichment pass could fetch the actual Slack thread bodies via the
slack_thread_ts/slack_channel fields for richer signal, but the title alone
is enough to surface the dispatch action.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from ..config import ProjectsConfig
from ..events import Event


SOURCE_SYSTEM = "linear_task"


def _normalize_ts(s: str) -> str:
    """Convert "2026-03-29 15:00:34" → "2026-03-29T15:00:34Z" (the DB uses UTC space-separated)."""
    if not s:
        return s
    if "T" in s:
        return s if s.endswith("Z") or "+" in s else s + "Z"
    return s.replace(" ", "T", 1) + "Z"


def extract_from_pe_db_dump(
    path: Path,
    projects: ProjectsConfig,
    source_machine: str = "product_engineer",
) -> Iterator[Event]:
    """Yield Events from a product-engineer full_db_dump.json file.

    Skips tasks with empty title (webhook-only / scheduled tasks). Emits
    one user_message event per task at created_at.
    """
    data = json.loads(path.read_text())
    tasks = data.get("tasks", []) or []
    for i, t in enumerate(tasks):
        title = (t.get("title") or "").strip()
        ts = t.get("created_at")
        product = t.get("product")
        if not title or not ts or not product:
            continue
        # Use a synthetic /workspace/<product> cwd so existing projects.yaml
        # patterns route this correctly. /workspace/* patterns were added when
        # we wired R2 transcripts; reusing them keeps routing rules in one place.
        cwd = f"/workspace/{product}"
        slug = projects.slug_for(cwd) or "unknown"
        # session_id: prefer the Linear task_uuid; fall back to task_id (BC-NNN)
        session_id = t.get("task_uuid") or t.get("task_id") or f"linear:{i}"
        words = len(title.split())
        yield Event(
            ts=_normalize_ts(ts),
            actor="user",
            project=slug,
            session_id=session_id,
            source_system=SOURCE_SYSTEM,
            source_machine=source_machine,
            kind="user_message",
            seq=0,  # one event per task; seq is per-session and we have one
            content_snippet=title[:2000],
            metadata={
                "words": words,
                "cwd": cwd,
                "linear_task_id": t.get("task_id"),
                "task_uuid": t.get("task_uuid"),
                "slack_channel": t.get("slack_channel"),
                "slack_thread_ts": t.get("slack_thread_ts"),
                "transcript_r2_key": t.get("transcript_r2_key"),
                "status": t.get("status"),
                "pr_url": t.get("pr_url"),
                "via": "product-engineer",
            },
        )
