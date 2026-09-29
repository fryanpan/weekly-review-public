"""Event schema and JSONL IO for weekly-review."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator


@dataclass(frozen=True)
class Event:
    ts: str                      # ISO8601 UTC
    actor: str                   # "user" | "agent" | "artifact"
    project: str                 # registry slug
    session_id: str              # source-specific session identifier
    source_system: str           # "claude_transcript" | "git" | "github" | ...
    source_machine: str          # "mac_mini" | "laptop" | ...
    kind: str                    # "user_message" | "agent_message" | "tool_call" | "pr_merged" | ...
    seq: int                     # monotonic within session for dedup
    content_snippet: str = ""    # truncated; see extractors for per-kind caps
    metadata: dict[str, Any] = field(default_factory=dict)
    author_id: str = ""          # platform-native author handle (gh login, slack user id, notion email, etc.)
    author_kind: str = ""        # "human" | "bot" | "webhook" | "automation" | "" (unknown / inherited from actor)

    @property
    def event_id(self) -> str:
        """Stable identity, used by dedupe_by_event_id.

        Subagent transcripts carry the PARENT's sessionId, and `seq` restarts at
        0 in every file — so a bare source:session:seq collides across the main
        loop and every dispatched agent in the same session, and dedupe silently
        discards all but the first. (Measured before this was fixed: 1,624 lanes
        of one research session collapsed to 1, destroying 80% of its events.)
        The agent lane is therefore part of the identity.

        Main-loop events keep the original 3-part id byte-for-byte, so ids that
        were already persisted don't shift. Lane is derived from the transcript
        filename, which is identical across archive mirrors, so the same file
        ingested from two mirrors still dedupes to one event.

        `metadata["source_event_id"]` overrides everything else. Some stores
        already assign each record a unique id, and when they do, inventing one
        from (session, seq) is strictly worse: live-feedback events were keyed
        `live_feedback:live-feedback:<docId>:0`, so the DOCUMENT was the
        identity and every revisit collided with the first. One doc the user
        visited 58 times became one event; 5,699 read sessions collapsed to
        1,180 and 2,560 of his comments to 475. The extractor passes the
        store's own id through instead of guessing at one.
        """
        source_event_id = (self.metadata or {}).get("source_event_id")
        if source_event_id:
            return f"{self.source_system}:{source_event_id}"
        lane = (self.metadata or {}).get("agent_lane")
        if lane and lane != "main":
            return f"{self.source_system}:{self.session_id}:{lane}:{self.seq}"
        return f"{self.source_system}:{self.session_id}:{self.seq}"

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, line: str) -> "Event":
        data = json.loads(line)
        return cls(**data)


def write_events(path: Path, events: Iterable[Event]) -> int:
    count = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(ev.to_json())
            f.write("\n")
            count += 1
    return count


def read_events(path: Path) -> Iterator[Event]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield Event.from_json(line)


def dedupe_by_event_id(events: Iterable[Event]) -> list[Event]:
    """Remove duplicate Events by event_id, keeping the first occurrence.

    The same logical event can be extracted from multiple sources (e.g. a
    Claude transcript scanned both at ~/.claude/projects/ and at a mirror
    under ~/dev/weekly-review-transcripts/laptop/), yielding two Event
    objects with identical event_ids but different list indices. Without
    dedup, segmentation can attach the same event to two different tasks,
    inflating PR counts and effort estimates by 3-5x.
    """
    seen: dict[str, Event] = {}
    for ev in events:
        if ev.event_id not in seen:
            seen[ev.event_id] = ev
    return list(seen.values())
