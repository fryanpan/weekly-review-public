"""Record the user's verdicts into `estimate_reviews` from wherever he wrote them.

Two sources, one parser:

* a verdict worksheet (`.md`) whose `**VERDICT [<id>]:**` lines he edited
  in place — the text after the colon is the verdict;
* a thread dump (`.json`, the output of `list_threads` on the served mockup),
  where each thread's anchor names `VERDICT [<id>]` and the first comment is
  the verdict. The comment's own `ts` becomes `reviewed_at`, so when the user
  submits twice on one task (each Submit opens a new thread) the newer submit
  wins in every reader regardless of the order `list_threads` returns them.

A pre-filled line (`✅ reviewed:` / `NOT carried forward`) is never re-read
as a fresh verdict: re-recording it with a newer timestamp would let the
stale value win over a later correction (see learnings, "Verdict ingestion").

A thread comment that names no verdict ("give me more room for the content",
"why is there no board task?") is a comment, not a verdict, and is skipped:
only the worksheet's edited VERDICT line makes free prose a verdict, because
there he wrote it in the verdict's own slot. From threads, a verdict is the
Submit form or a line starting ok / high / low.

Verdict grammar, case-insensitive, anything else kept as the note:
  ok / OK …                          -> confidence ok, hours = LLM estimate
                                        (a number in the note is NOT read as
                                        hours — "ok, not much more than 0.18h
                                        hands-on" must keep the estimate)
  HIGH … Nh | HIGH … N hours          -> confidence high, hours = N
  LOW  … Nh | LOW … "full day"        -> confidence low,  hours = N (day = 8)
  estimate: X · hands-on: Y — note    -> the page's Submit button; X drives
                                        confidence/hours as above, Y is kept
                                        in the note (and `hands_on` in the row)

Usage:
  uv run python3 scripts/harvest_verdicts.py --run-id <run> --worksheet docs/data/<week>.md
  uv run python3 scripts/harvest_verdicts.py --run-id <run> --threads threads.json
Add --dry-run to print what would be recorded and write nothing.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA_DIR = Path.home() / ".local/share/weekly-review"
sys.path.insert(0, str(REPO / "src"))

VERDICT_RE = re.compile(r"VERDICT \[([0-9a-f]{6,})\]")
PREFILLED = ("✅", "reviewed:", "NOT carried forward", "(comment here)")
HOURS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:h\b|hr|hour)", re.I)
DAY_RE = re.compile(r"(?:a\s+|one\s+|full\s+)?day", re.I)
# The page's Submit button composes "estimate: high · hands-on: ok — note".
STRUCTURED_RE = re.compile(r"^(?:estimate:\s*(ok|high|low)|hands-on:\s*(?:ok|high|low))", re.I)
HANDS_RE = re.compile(r"hands-on:\s*(ok|high|low)", re.I)


def parse_verdict(text: str, llm_h: float | None) -> dict | None:
    """Turn one verdict string into {confidence, bryan_h, note}; None when it
    is a pre-filled line or says nothing a reviewer wrote."""
    t = text.strip().strip("*_ ").strip()
    if not t or any(m in t for m in PREFILLED):
        return None
    low = t.lower()
    hours = HOURS_RE.search(t)
    form = STRUCTURED_RE.match(t)
    if form:
        # The page's Submit button writes "estimate: high · hands-on: ok — note".
        # The estimate verdict drives the recorded hours; the hands-on verdict
        # is a judgement on a measured figure and rides along in the note.
        est = (form.group(1) or "").lower()
        conf = est or "note"
        hands = HANDS_RE.search(t)
        if est in ("high", "low"):
            bh = float(hours.group(1)) if hours else (8.0 if est == "low" and DAY_RE.search(t) else llm_h)
            # A figure on the wrong side of the estimate is not his correction.
            # "estimate: low — 3.6k lines per 8h workday" on an 18h estimate
            # read as a correction DOWN to 8h, against the verdict it sat in.
            if llm_h is not None and ((est == "low" and bh < llm_h) or (est == "high" and bh > llm_h)):
                bh = llm_h
        else:
            # ok, or hands-on only: the estimate stands. A number in the note
            # is about something else (usually the hands-on figure he is
            # discussing) and must not replace the estimate.
            bh = llm_h
        if bh is None:
            return None
        out = {"confidence": conf, "bryan_h": bh, "note": t}
        if hands:
            out["hands_on"] = hands.group(1).lower()
        return out
    if low.startswith("high"):
        conf = "high"
        bh = float(hours.group(1)) if hours else llm_h
    elif low.startswith("low"):
        conf = "low"
        bh = float(hours.group(1)) if hours else (8.0 if DAY_RE.search(t) else llm_h)
    elif low.startswith("ok"):
        conf = "ok"
        bh = llm_h
    else:
        conf = "note"
        bh = float(hours.group(1)) if hours else llm_h
    if bh is None:
        return None
    return {"confidence": conf, "bryan_h": bh, "note": t}


def from_worksheet(md: str) -> list[tuple[str, str]]:
    """(short task id, verdict text) for every VERDICT line a person edited."""
    out = []
    for line in md.splitlines():
        m = re.search(r"\*\*VERDICT \[([0-9a-f]{6,})\]:\*\*\s*(.*)$", line)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def from_threads(threads: object) -> list[tuple[str, str, str | None]]:
    """(short task id, verdict text, reviewed_at) per thread. The id is read
    from the thread's anchor (whatever the server calls it — the whole thread
    is searched for `VERDICT [id]`), the verdict from its first comment body,
    and reviewed_at from that comment's `ts` (epoch ms → ISO UTC; None when
    the dump carries no timestamp, in which case harvest time is used)."""
    out = []
    items = threads.get("threads") if isinstance(threads, dict) else threads
    for th in items or []:
        tid = _anchor_task_id(th)
        if not tid:
            continue
        body = _first_comment(th)
        if body:
            out.append((tid, body, _first_comment_ts(th)))
    return out


def _anchor_task_id(th: dict) -> str | None:
    """The mockup stamps `data-task-id` on every task row and detail field, so
    a comment anchored anywhere on a task carries its id in the anchor's
    `dataAttrs`; a comment on the VERDICT box also carries it in text."""
    blob = json.dumps(th, ensure_ascii=False)
    m = re.search(r'"(?:data-task-id|taskId)":\s*"([0-9a-f]{6,})"', blob)
    if m:
        return m.group(1)
    m = VERDICT_RE.search(blob)
    return m.group(1) if m else None


def _first_comment_record(th: dict) -> dict | str | None:
    for key in ("comments", "replies", "messages"):
        seq = th.get(key)
        if isinstance(seq, list) and seq:
            return seq[0]
    return None


def _first_comment(th: dict) -> str:
    first = _first_comment_record(th)
    if isinstance(first, dict):
        return str(first.get("text") or first.get("body") or first.get("content") or "")
    if first is not None:
        return str(first)
    return str(th.get("text") or th.get("body") or "")


def _first_comment_ts(th: dict) -> str | None:
    """The submit's own time as ISO UTC. The server stamps comments with epoch
    ms in `ts`; an ISO string is passed through unchanged."""
    first = _first_comment_record(th)
    raw = first.get("ts") if isinstance(first, dict) else None
    if raw is None:
        raw = th.get("createdAt")
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw / 1000, tz=timezone.utc).isoformat()
    if isinstance(raw, str) and raw:
        return raw
    return None


def resolve_ids(conn: sqlite3.Connection, shorts: list[str]) -> dict[str, tuple[str, float | None]]:
    """short id -> (full task id, llm est) for ids this run knows."""
    out = {}
    for tid, est in conn.execute("SELECT task_id, est_human_hours FROM enriched"):
        for s in shorts:
            if tid.startswith(s):
                out[s] = (tid, est)
    return out


def harvest(run_dir: Path, pairs: list[tuple], dry_run: bool = False) -> list[dict]:
    """`pairs` are (short id, text) or (short id, text, reviewed_at). A missing
    or None reviewed_at records the harvest time."""
    from weekly_review.persist import record_review
    conn = sqlite3.connect(str(run_dir / "run.sqlite"))
    ids = resolve_ids(conn, [pr[0] for pr in pairs])
    recorded = []
    for pr in pairs:
        short, text = pr[0], pr[1]
        reviewed_at = pr[2] if len(pr) > 2 else None
        if short not in ids:
            print(f"skip {short}: not an enriched task in {run_dir.name}")
            continue
        tid, llm_h = ids[short]
        v = parse_verdict(text, llm_h)
        if not v:
            continue
        from_thread = len(pr) > 2
        if from_thread and v["confidence"] == "note" and not STRUCTURED_RE.match(text.strip().strip("*_ ")):
            print(f"skip {tid[:10]}: comment, not a verdict — {text[:60]!r}")
            continue
        row = {"task_id": tid, "llm_h": llm_h, "reviewed_at": reviewed_at, **v}
        recorded.append(row)
        print(f"{'would record' if dry_run else 'record'} {tid[:10]} {v['confidence']:5} "
              f"{llm_h}h -> {v['bryan_h']}h  {v['note'][:60]}")
        if not dry_run:
            record_review(conn, task_id=tid, run_id=run_dir.name,
                          bryan_est_hours=v["bryan_h"], llm_est_hours=llm_h,
                          confidence=v["confidence"], note=v["note"],
                          reviewed_at=reviewed_at)
    if not dry_run:
        conn.commit()
    conn.close()
    return recorded


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--run-id", required=True)
    p.add_argument("--worksheet", help="verdict worksheet .md with edited VERDICT lines")
    p.add_argument("--threads", help="JSON dump of list_threads on the served mockup")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    pairs: list[tuple] = []
    if args.worksheet:
        pairs += from_worksheet(Path(args.worksheet).read_text())
    if args.threads:
        pairs += from_threads(json.loads(Path(args.threads).read_text()))
    if not pairs:
        print("nothing to harvest")
        return 1
    rows = harvest(DATA_DIR / "runs" / args.run_id, pairs, dry_run=args.dry_run)
    print(f"{len(rows)} verdicts {'found' if args.dry_run else 'recorded'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
