"""Extract events from a local Slack history archive.

Layout (produced by scripts/sync-slack-history.sh):
  <slack_root>/
    _users.json                  # users.list response (caches U... → display name)
    _channels.json               # conversations.list response (channel id → name)
    <channel_id>/history.jsonl   # one message per line from conversations.history

Per-message classification (the discipline boundary the user asked about):
  - human-typed (user present, no bot_id, no automation subtype)
      → actor=user, kind=user_message, author_kind=human, author_id=<U...>
  - bot/webhook/system event
      → actor=agent, kind=agent_message OR system_notification, author_kind=bot
        (drops thread joins, channel topic edits, etc. via SYSTEM_SUBTYPES filter)

Project routing: Slack channels don't map cleanly to project slugs. We use
a per-channel mapping in `slack_channel_map.yaml` (config dir). If a channel
isn't in the map, events route to "unknown" — visible in coverage so you
can add the mapping.

Thread replies are pulled into <channel_id>/threads/<thread_ts>.jsonl by the
sync script and folded into the same per-channel event stream — that's where
most of the dispatch back-and-forth lives. The reply parent (the original
top-level message) is NOT re-emitted from the thread file (already covered
by history.jsonl) — we drop replies whose ts == thread_ts.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import yaml

from ..config import CONFIG_DIR, ProjectsConfig
from ..events import Event


SOURCE_SYSTEM = "slack"

# Slack subtypes that are NOT real messages (channel joins, topic changes, etc.).
# Anything in here gets dropped entirely.
DROP_SUBTYPES = {
    "channel_join", "channel_leave", "channel_topic", "channel_purpose",
    "channel_name", "channel_archive", "channel_unarchive",
    "group_join", "group_leave", "pinned_item", "unpinned_item",
    "tombstone",
}

# Subtypes that ARE messages but were posted by automation/integration.
# These become agent_message, not user_message.
BOT_SUBTYPES = {"bot_message", "slackbot_response", "thread_broadcast"}


def _load_channel_map() -> dict[str, str]:
    """Load <channel_id> → <project_slug> mapping from config.

    Format: a YAML mapping at ~/.config/weekly-review/slack_channels.yaml:
      C0EXAMPLE1: product-engineer
      C0EXAMPLE2: example-project
    """
    path = CONFIG_DIR / "slack_channels.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    return {str(k): str(v) for k, v in data.items()}


def _ts_to_iso(ts: str) -> str:
    """Slack ts is "<seconds>.<microseconds>". Convert to ISO8601 UTC."""
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, TypeError):
        return ""


def extract_from_slack_root(
    root: Path,
    projects: ProjectsConfig,
    source_machine: str = "slack",
) -> Iterator[Event]:
    """Yield Events from a slack/ archive directory."""
    if not root.is_dir():
        return

    channel_map = _load_channel_map()

    # Optional channel-name lookup for nicer metadata (not load-bearing for routing)
    channels_meta_path = root / "_channels.json"
    channel_names: dict[str, str] = {}
    if channels_meta_path.exists():
        try:
            data = json.loads(channels_meta_path.read_text())
            for ch in data.get("channels", []) or []:
                if ch.get("id"):
                    channel_names[ch["id"]] = ch.get("name") or ch.get("user") or ""
        except json.JSONDecodeError:
            pass

    for channel_dir in sorted(root.iterdir()):
        if not channel_dir.is_dir():
            continue
        cid = channel_dir.name
        if cid.startswith("_"):
            continue
        history = channel_dir / "history.jsonl"
        if not history.exists():
            continue
        slug = channel_map.get(cid, "unknown")
        cname = channel_names.get(cid, "")

        # Walk both history.jsonl (top-level) AND threads/*.jsonl (replies).
        files: list[Path] = [history]
        threads_dir = channel_dir / "threads"
        if threads_dir.is_dir():
            files.extend(sorted(threads_dir.glob("*.jsonl")))

        seq = 0
        for f_path in files:
            is_thread_file = f_path.parent.name == "threads"
            thread_parent_ts = f_path.stem if is_thread_file else None
            with f_path.open("r", encoding="utf-8") as f:
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    if msg.get("type") != "message":
                        continue
                    subtype = msg.get("subtype")
                    if subtype in DROP_SUBTYPES:
                        continue

                    msg_ts = msg.get("ts")
                    ts_iso = _ts_to_iso(msg_ts)
                    if not ts_iso:
                        continue

                    user_id = msg.get("user") or ""
                    bot_id = msg.get("bot_id")
                    text = msg.get("text") or ""
                    thread_ts = msg.get("thread_ts")

                    if is_thread_file:
                        # Thread file holds the parent (ts == thread_parent_ts) plus replies.
                        # Drop the parent — already emitted by history.jsonl walk.
                        if msg_ts == thread_parent_ts:
                            continue
                    else:
                        # In history.jsonl, drop reply records (thread_ts != ts).
                        # Top-level messages have either no thread_ts, or thread_ts == ts.
                        if thread_ts and thread_ts != msg_ts:
                            continue

                    is_bot = bool(bot_id) or subtype in BOT_SUBTYPES or not user_id
                    actor = "agent" if is_bot else "user"
                    kind = "agent_message" if is_bot else "user_message"
                    author_kind = "bot" if is_bot else "human"
                    author_id = bot_id if is_bot else user_id

                    words = len(text.split())
                    yield Event(
                        ts=ts_iso,
                        actor=actor,
                        project=slug,
                        session_id=f"slack:{cid}:{thread_ts}" if thread_ts else f"slack:{cid}",
                        source_system=SOURCE_SYSTEM,
                        source_machine=source_machine,
                        kind=kind,
                        seq=seq,
                        content_snippet=text[:2000],
                        metadata={
                            "words": words,
                            "channel_id": cid,
                            "channel_name": cname,
                            "thread_ts": thread_ts,
                            "subtype": subtype,
                            "is_thread_reply": is_thread_file,
                        },
                        author_id=author_id or "",
                        author_kind=author_kind,
                    )
                    seq += 1
