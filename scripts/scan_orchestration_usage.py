"""Inventory every goal / loop / workflow activation in the transcript archive.

Detection is by MECHANISM SIGNATURE, not input phrasing — the user invokes these
as slash commands, mid-message slashes, or plain natural language ("make it a
goal to ..."), and Claude also self-initiates workflows. Phrasing-based greps
undercount badly (found 2 goals when the true count was dozens). Every real
activation leaves a machine trace regardless of phrasing:

  goal      -> a session-scoped Stop hook armed with a condition
               ("Stop hook is now active with condition"), plus Stop-hook
               feedback re-injections while the goal drives the session
  loop      -> /loop re-injection banners (<command-name>/loop) and
               ScheduleWakeup tool calls (dynamic pacing)
  workflow  -> Workflow tool_use calls (user-asked OR Claude-initiated)

Output: one JSON line per activation (skill, mechanism, ts, cwd, session,
detail) deduped across live-store/archive copies and across re-injections
(loop firings collapse to one setup per session+args; goal Stop-hook feedback
collapses into its arming event).

Usage:
  uv run python3 scripts/scan_orchestration_usage.py [--since 2026-04-13] [--out FILE]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOTS = [
    Path.home() / "dev" / "weekly-review-transcripts" / "mac_mini",  # archive first (superset)
    Path.home() / ".claude" / "projects",
]

# grep pre-filter markers -> files worth parsing
GREP_MARKERS = [
    "Stop hook is now active with condition",
    "Stop hook feedback",
    '"name":"Workflow"',
    '"name": "Workflow"',
    '"name":"ScheduleWakeup"',
    '"name": "ScheduleWakeup"',
    "command-name>/goal",
    "command-name>/loop",
    "command-name>/workflow",
    "/goal",
    "/workflow",
    "a goal",
]

GOAL_ARM = re.compile(r"Stop hook is now active with condition:?\s*[\"“]?(.{0,120})")
BANNER = re.compile(r"<command-name>/?(goal|loop|workflow)</command-name>")
ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)
# system-injected user lines that merely QUOTE earlier activations
ECHO_PREFIXES = (
    "This session is being continued from a previous conversation",
    "<task-notification>",
    '<channel source=',
)

# the user's goal/workflow ASKS in plain language or mid-message slashes. These
# are INTENT, not activation — /goal is a UI command the agent cannot invoke
# (Skill(goal) errors with "goal is a UI command"), so a mid-message "make it
# a /goal" ask arms nothing unless the user also types /goal at the prompt.
# Tracked separately so intent-vs-armed gaps are visible.
GOAL_INTENT = re.compile(r"(?:make|set|making|setting)\s+(?:it\s+|that\s+)?an?\s+/?goal\b|(?<![\w/])/goal\b", re.I)
WORKFLOW_INTENT = re.compile(r"(?:use|run|with|start)\s+an?\s+workflow\b|(?<![\w/])/workflow\b", re.I)
GOAL_REJECTED = "goal is a UI command, not a skill"


def _text_of(msg: dict) -> str:
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
    return ""


def candidate_files() -> list[str]:
    cand: set[str] = set()
    for root in ROOTS:
        if not root.exists():
            continue
        for m in GREP_MARKERS:
            try:
                out = subprocess.run(
                    ["grep", "-rl", m, str(root), "--include=*.jsonl"],
                    capture_output=True, text=True, timeout=900,
                )
                cand.update(out.stdout.split())
            except (subprocess.SubprocessError, OSError):
                continue
    return sorted(cand)


def scan(since: str) -> list[dict]:
    hits: list[dict] = []
    seen_lines: set[tuple[str, str, str]] = set()  # (session, ts, mech) — live/archive dedupe
    for f in candidate_files():
        try:
            fh = open(f, encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in fh:
            low = line.lower()
            if ("stop hook" not in low and "workflow" not in low
                    and "schedulewakeup" not in low and "command-name" not in low
                    and "goal" not in low):
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            ts = d.get("timestamp", "")
            if not ts or ts < since:
                continue
            sess = d.get("sessionId", "")
            cwd = d.get("cwd", "")
            typ = d.get("type")
            msg = d.get("message") or {}

            def emit(skill: str, mech: str, detail: str) -> None:
                key = (sess, ts, mech)
                if key in seen_lines:
                    return
                seen_lines.add(key)
                hits.append(dict(skill=skill, mechanism=mech, ts=ts, session=sess,
                                 cwd=cwd, detail=detail[:160].replace("\n", " ")))

            if typ == "assistant":
                for c in (msg.get("content") or []):
                    if not isinstance(c, dict) or c.get("type") != "tool_use":
                        continue
                    name = c.get("name", "")
                    inp = c.get("input") or {}
                    if name == "Workflow":
                        script = inp.get("script", "") or ""
                        m = re.search(r"name:\s*'([^']+)'", script)
                        emit("workflow", "workflow-tool",
                             m.group(1) if m else (inp.get("name") or inp.get("scriptPath", "")[:60] or "inline"))
                    elif name == "ScheduleWakeup":
                        emit("loop", "schedule-wakeup", str(inp.get("reason", ""))[:120])
                    elif name == "Skill" and (inp.get("skill") or "").split(":")[-1] in ("goal", "loop", "workflow"):
                        emit(inp["skill"].split(":")[-1], "skill-tool", str(inp.get("args", ""))[:120])
            elif typ == "user":
                text = _text_of(msg)
                if not text:
                    continue
                is_echo = text.lstrip().startswith(ECHO_PREFIXES) or bool(d.get("isSidechain"))
                bm = BANNER.search(text)
                if bm and not is_echo:
                    am = ARGS.search(text)
                    emit(bm.group(1), "slash-banner", am.group(1).strip() if am else "")
                    continue
                gm = GOAL_ARM.search(text)
                if gm and not is_echo:
                    emit("goal", "stop-hook-armed", gm.group(1))
                elif text.lstrip().startswith("Stop hook feedback:") and not is_echo:
                    emit("goal", "stop-hook-feedback", text.lstrip()[len("Stop hook feedback:"):][:120])
                elif not is_echo:
                    if GOAL_INTENT.search(text):
                        emit("goal", "intent-ask", text[:140])
                    if WORKFLOW_INTENT.search(text):
                        emit("workflow", "intent-ask", text[:140])
            # tool_result carrying the Skill(goal) rejection = an ask that
            # definitively did NOT arm
            if typ == "user" and GOAL_REJECTED in line:
                emit("goal", "skill-rejected", "agent tried Skill(goal); UI-only, not armed")
        fh.close()
    hits.sort(key=lambda h: h["ts"])
    return hits


def collapse_setups(hits: list[dict]) -> list[dict]:
    """Re-injections -> one setup. Loop banners/wakeups collapse per
    (session, skill, args-prefix); goal stop-hook-feedback collapses into the
    session's arming event (or the first feedback if arming wasn't captured)."""
    setups: dict[tuple, dict] = {}
    for h in hits:
        if h["mechanism"] in ("slash-banner", "schedule-wakeup") and h["skill"] == "loop":
            key = (h["session"], "loop", h["detail"][:50])
        elif h["mechanism"] == "stop-hook-feedback":
            key = (h["session"], "goal", "feedback")
        elif h["mechanism"] == "stop-hook-armed":
            key = (h["session"], "goal", h["detail"][:50])
        else:
            key = (h["session"], h["skill"], h["mechanism"], h["ts"])
        if key in setups:
            setups[key]["firings"] += 1
        else:
            setups[key] = dict(**h, firings=1)
    # a goal-feedback setup in a session that also has an armed setup is the same goal
    armed_sessions = {k[0] for k in setups if k[1] == "goal" and k[2] != "feedback"}
    out = [s for k, s in setups.items()
           if not (k[1] == "goal" and k[2] == "feedback" and k[0] in armed_sessions)]
    out.sort(key=lambda s: s["ts"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-04-13")
    ap.add_argument("--out")
    args = ap.parse_args()
    hits = scan(args.since)
    setups = collapse_setups(hits)
    from collections import Counter
    print(f"raw activation events: {len(hits)}  ->  distinct setups: {len(setups)}", file=sys.stderr)
    print(Counter(s["skill"] for s in setups), file=sys.stderr)
    payload = "\n".join(json.dumps(s) for s in setups)
    if args.out:
        Path(args.out).write_text(payload)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
