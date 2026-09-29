"""The digest reports how the week went; it never rules on whether it was the
right week to have had.

The user's standing instruction: priorities are his. An on-goal / off-goal split
takes the same hours the digest already reports and adds a verdict on top of
them — and a verdict is the one thing this tool is not for.

Two layers, because the defect this guards against is not a broken function:

1. `test_digest_renders_no_goal_verdict` renders a digest from a fixture whose
   task carries goal-framed prose, and asserts the OUTPUT is clean. That is the
   assertion a reader can check against a real digest.
2. `test_no_output_module_carries_a_goal_verdict` greps the renderer and the
   three other named outputs. A rendered-output test only covers the branches
   one fixture reaches, so it would pass while the judgement sat in a code path
   the fixture never enters. This is the same reason the presence-metric
   regression test asserts the CALL SITE rather than the behaviour: the failure
   mode is shipped-but-unreachable, not incorrect.

The vocabulary below matches the VERDICT, never the word "goal" on its own.
A task's `goal` field is its own purpose statement, written by the enricher and
read by a human reviewing an estimate — that is description, not judgement, and
it must keep working. What is banned is a label that sorts his hours into
deserving and undeserving.
"""
from __future__ import annotations

import re
from pathlib import Path

from weekly_review.digest import render_digest
from weekly_review.enrichment import EnrichedTask
from weekly_review.events import Event
from weekly_review.segmentation import Task

# Each pattern is a way of saying "this time was well spent / this was not."
# `off-track` is here because the mid-week off-track ping belongs to the lead agent's
# planning loop, not to a measurement digest — if it turns up in one of these
# outputs it has leaked across that line.
VERDICT_PATTERNS = [
    r"\boff[-_ ]goal\b",
    r"\bon[-_ ]goal\b",
    r"\boff[-_ ]track\b",
    r"\bon[-_ ]track\b",
    r"\boff[-_ ]plan\b",
    r"\bon[-_ ]plan\b",
    r"\bgoal[-_ ]aligned\b",
    r"\bmisaligned\b",
    r"\bunplanned work\b",
    r"\bdistraction(s)?\b",
    r"\bwasted (time|hours)\b",
    r"\bshould have (spent|worked|focused)\b",
]
VERDICT_RE = re.compile("|".join(VERDICT_PATTERNS), re.IGNORECASE)

REPO_ROOT = Path(__file__).resolve().parents[1]

# The digest, the two worksheets, the BI export, and the post-run audits: every
# artifact this pipeline hands to a human. Named explicitly rather than globbed,
# so adding an output is a deliberate decision to add it to this guard too.
OUTPUT_SOURCES = [
    REPO_ROOT / "src" / "weekly_review" / "digest.py",
    REPO_ROOT / "src" / "weekly_review" / "export.py",
    REPO_ROOT / "src" / "weekly_review" / "validate.py",
    REPO_ROOT / "scripts" / "make_label_worksheet.py",
    REPO_ROOT / "scripts" / "make_verdict_worksheet.py",
]


def _uev(ts: str, project: str, text: str, seq: int) -> Event:
    return Event(
        ts=ts, actor="user", project=project, session_id="s",
        source_system="claude_transcript", source_machine="mm",
        kind="user_message", seq=seq,
        content_snippet=text, metadata={"words": len(text.split())},
    )


def test_digest_renders_no_goal_verdict():
    """A task whose own prose is goal-framed must still render without a verdict.

    The fixture deliberately puts goal language in `goal`, `title` and
    `rationale` — the enricher writes there and the digest reads from there, so
    if a renderer ever passed that prose through as a judgement, or sorted tasks
    under an on/off heading, this is where it would surface.
    """
    events = [
        _uev("2026-04-15T10:00:00Z", "weekly-review", "restate the weekly series", 0),
        _uev("2026-04-16T11:00:00Z", "side-project", "poke at the bike map", 1),
    ]
    on = Task(task_id="t1", project="weekly-review",
              title="Restate the weekly series", event_ids=[0])
    off = Task(task_id="t2", project="side-project",
               title="Poke at the bike map", event_ids=[1])
    enriched = {
        "t1": EnrichedTask(
            task_id="t1", title="Restate the weekly series",
            goal="The published hours predate three instruments, so the series "
                 "had to be restated before anyone quotes it.",
            est_human_hours=8.0, uncertainty="low",
            bryan_domain_expertise="expert",
            dimensions={"scope": "single-feature", "depth": "careful design",
                        "type": "code"},
            rationale="squarely the week's stated goal",
        ),
        "t2": EnrichedTask(
            task_id="t2", title="Poke at the bike map",
            goal="Wanted to see whether the POI download race was still there.",
            est_human_hours=1.0, uncertainty="high",
            bryan_domain_expertise="competent",
            dimensions={"scope": "bugfix", "depth": "routine", "type": "code"},
            rationale="nothing to do with the week's stated goal",
        ),
    }
    md = render_digest(
        period_start="2026-04-13",
        period_end="2026-04-19",
        tasks=[on, off],
        enriched=enriched,
        statuses={"t1": "shipped", "t2": "open"},
        events=events,
        active_hours_by_task={"t1": 6.0, "t2": 1.5},
        agent_hours_by_task={"t1": 20.0, "t2": 2.0},
        resumptions_by_task={"t1": 0, "t2": 0},
        coverage={
            "user_message_events": 2, "assigned": 2, "unassigned": 0,
            "coverage_percent": 100.0,
            "unassigned_by_project": {},
        },
        cognitive_wall_clock_hours=9.0,
        context_switches=1,
        last_calibrated_days=None,
    )

    # Both tasks' hours are reported. That is the point: the digest says where
    # the time went, in full, and declines to say which of it counted.
    assert "Restate the weekly series" in md
    assert "Poke at the bike map" in md

    hits = VERDICT_RE.findall(md)
    assert not hits, (
        "the digest rendered a verdict on how the user spent his week: "
        f"{sorted({h if isinstance(h, str) else h[0] for h in hits})}\n"
        "Report the hours; the priorities are his."
    )


def test_no_output_module_carries_a_goal_verdict():
    """No human-facing output reintroduces the judgement under another name."""
    offenders: list[str] = []
    for path in OUTPUT_SOURCES:
        assert path.exists(), f"guarded output no longer at {path}"
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if VERDICT_RE.search(line):
                rel = path.relative_to(REPO_ROOT)
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "an output module names a verdict on how the time was spent:\n  "
        + "\n  ".join(offenders)
        + "\nThe digest measures; it does not rule on priorities."
    )
