# Measurement refresh — the instrument is pointed at the wrong 10%

**Date:** 2026-09-08 · **Status:** draft for the user, rewritten after his meeting notes · **Window for every number:** 2026-08-31 → 2026-09-06 (7 days, inclusive)

> **Superseded 2026-09-08.** The approved plan is a workspace doc on the project board,
> with eight tasks under one goal. This file is kept as the evidence writeup that led to it.
> The attribution method the user approved is in `docs/product/specs/2026-08-17-hands-on-measurement.md`.

## Your claim was right, and it is worse than you said

Your notes say ~90% of your time is now workspace interactions, switching task every ~30 seconds, with attention shifts as fast as 15 seconds. I tried to check that and found the store agrees with you in detail:

- **2,955 of your own workspace events** in that week — 422 a day.
- **1,293 interaction-bounded read sessions, median 20 seconds**, 63% of them under 30s. The 15-second attention window in your notes is not aspirational; it is already recorded.
- **21.07h of measured session time** in that one signal alone.

The pipeline reported **10.9h of hands-on across all sources** for the same week. It kept **234 of those 1,293 sessions and discarded 1,059 — 82%.**

### Why

Every live-feedback event is identified as `live_feedback:live-feedback:<docId>:0`. The document is the identity and the sequence number is always `0`, so **every visit after the first to the same doc has the same id as the first, and dedup throws it away.** One doc that week was visited 58 times and became one event. Comments collide with sessions on the same doc the same way — 705 comments and replies became 61 events.

This is not a new class of bug. It is the one already in `learnings.md`: *"Any id built from (session, seq) on transcript data is wrong unless it also carries the lane."* That fix went into the transcript extractor and was never applied here.

**So all three of this week's defects are the same mistake:** something that was true when the code was written — one directory, one board per doc, one event per doc — quietly stopped being true, and nothing errored.

| #   | Defect                                               | Cost                               | State                                     |
| --- | ---------------------------------------------------- | ---------------------------------- | ----------------------------------------- |
| 1   | Data dir moved 09-01; extractor read the frozen copy | 1.6h, 5 of 7 days at 0.00h         | Fixed, committed                          |
| 2   | Boardless diff reviews routed to `unknown`           | 1.6h, 14% of the week              | Fixed, committed                          |
| 3   | **Doc-keyed event ids collapse every revisit**       | **82% of your workspace sessions** | **Found, not fixed — awaiting your word** |

## What your notes ask for, and whether it is reachable

> *"Count each moment of work once only, attributed to one project and one task."*

Today that is impossible by construction. Hands-on is presence: it stitches gaps up to **10 minutes** into one interval and charges it to **one task**. At a 20-second median attention span, a single stitched block can span dozens of task touches and credit all of them to whichever one it started on. Tuning the tolerance from 10 to 15 minutes — the decision already on your queue — makes this *worse*, not better.

But it becomes reachable, because the data now exists. Workspaces confirms:

- **`workspaces/<wsId>.events.jsonl`** — a per-action board log, one record per action, each with a millisecond `ts`, an `actor` with `kind` of person or agent, **and a `taskId`**.
- **`decision.answered`** `{taskId, reviewItemId, headline, answer, actor, ts}` — 154 on one board alone. Your review-item answers, the actual unit of your work, are already timestamped against a task.
- `task.transitioned`, `task.body_edited`, `review_item.added/revised/withdrawn`, `status`, `turn` — all carry `taskId` and `workspaceId`.
- **`voice.request`** `{transcript, route, ack, surface, actor, ts}` — 53 on one board. Voice planning currently measures as zero.
- **Meetings** live under `data/meetings/<docId>/` with per-turn timestamps, so time in a recorded meeting is measurable.
- Known gap: suggestion accept/reject is not logged anywhere.

With a task id on the record, attribution stops being inference. Each moment gets assigned to the task that owns it, once — which is exactly the sentence you closed the notes with.

## Plan

**Day 1 — stop losing data.**

1. Fix the id collapse so each event is its own event. Re-run the affected history. I expect hands-on to rise substantially; I will publish the new figure with its error bar and say plainly which old numbers it replaces.
2. Ingest the board event log as a first-class source, keyed on `taskId`.

**Day 2 — attribute by task, not by guess.**

1. Route on `taskId` where it exists, keep directory routing as the fallback. This settles the repo-vs-board decision with evidence instead of a ruling.
2. Pick up meeting turn timestamps and `voice.request`, so planning-by-voice stops reading as zero.

**Not in this plan:** rebuilding attribution board-native from scratch. It breaks comparability with everything measured since April, and the incremental version gets the same answer.

## Two risks I want you to see

- **Double-counting is now the real danger, not undercounting.** `read_session` and the board log describe the *same* moments from two angles. They must be unioned, never summed. Your "count each moment once" line is the correct spec and I will hold the code to it.
- **Every published number moves.** Recovering 82% of workspace events changes the record back to ~2026-06-15, when that stream went live. Anything quoted from before then is transcript-only and stays comparable; anything after is currently too low.

## What is still yours to decide

The tolerance question on your queue is now much smaller — for workspace work we will measure directly rather than model a gap. **The question that has not changed is whether work an agent drafts under your identity counts as your hands-on time.** Last week that was 13,761 words posted by your GitHub account against 4,795 you typed, and it decides whether the number means *what you did* or *what went out under your name*.

## One thing I would still ask

Your notes are about tracking more finely. The measured week says five of seven goals moved less than an hour each, and the goal that got the most of your time shipped nothing because it was waiting on you. Finer tracking will describe that more precisely; it will not change it. If what you want to know is *"why did the week not deliver,"* that is a different instrument than *"where did the hours go"* — worth naming which one this is before we build it.
