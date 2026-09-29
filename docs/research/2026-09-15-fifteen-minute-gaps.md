# What the 15-minute rule gets wrong, in both directions

You asked for two sets of examples at a 15-minute threshold: cases where the agent
was really working but went quiet for more than 15 minutes, so we undercount; and
cases where 15 minutes is still bridging polling or idling, so we overcount.

Both exist. Both are small — and after your question on row 1 of the A-table, the undercount side is smaller than I first wrote. An artifact landing inside a silence is not evidence the agent produced it, and once that is tested properly, none of the ten artifact-witnessed gaps shows this agent working. The "cutting real work" column below is the corrected one; the correction is written up under the A-table.

| Week       | Agent hours | Still bridging idle | Cutting real work |
| ---------- | ----------- | ------------------- | ----------------- |
| 2026-04-27 | 51.4h       | 1.4h (2.7%)         | 0h                |
| 2026-08-17 | 227.5h      | 4.7h (2.1%)         | 0.3h (0.1%)       |
| 2026-09-07 | 299.3h      | 6.4h (2.1%)         | 1.4h (0.5%)       |

Both columns are **floors, not totals** — each comes from a list of patterns I can
name, so anything shaped differently is missing from both. Read them as "at least
this much, in each direction", and note that they nearly cancel.

## How to read a gap

A gap is the silence between two consecutive agent events on one task. A gap
**wider than the tolerance cuts the burst**, and the silence is not billed. A gap
**inside the tolerance is bridged**, and every second of it is billed as agent time.

Two things already get special treatment, so they are not in either list below:

- A gap where **a tool call was still running** (a call, then its result) gets the
  full 30 minutes, not 15. Long builds and long test runs are already covered.
- A gap where **the same worker served a different task** is cut regardless of
  width, because it was demonstrably somewhere else.

So everything below is a gap where nothing was on the wire and the worker was not
provably elsewhere.

## A. More than 15 minutes, and work was still happening

In the 15–90 minute band, **84 / 134 / 164 gaps** get cut across the three weeks.
Most of them should be. The commonest shape by far is *end of a turn → start of the
next turn* — 46, 72 and 56 of them — which is the agent finishing and waiting for
you. Cutting that is the whole point.

The ones below are the exceptions: an artifact — a commit or a pull request — is
timestamped **inside** the silence, or the agent came back with a call that only
makes sense if something had been running the whole time.

| #   | Week  | Gap      | What the silence contains                                    | Verdict                                                      |
| --- | ----- | -------- | ------------------------------------------------------------ | ------------------------------------------------------------ |
| 1   | 08-17 | 62.7 min | notes-tool: agent finishes a writeup, then a commit lands 48 min in (`Add AI/ML digest for 2026-08-22`), then it resumes with `Bash` in that repo | **Not work — I was wrong.** Nothing ran. The commit is a 33-line scheduled digest; the session resumed 9 seconds after you sent it a new message. |
| 2   | 09-07 | 37.3 min | client-d: a dispatched researcher reports done, a commit lands inside (`Impact note: restrict to 2026-data studies…`), the lead resumes reading that exact file | Partly, same shape as #4. The lead's lane was silent; 76 events ran in this session's other lanes, all on a different task. |
| 3   | 04-27 | 48.3 min | crm-tool: a PR opens 31 min into the silence, then the agent resumes with `Bash` | Unwitnessed. No agent event anywhere in that session inside the silence — the PR is the only thing in it, and it is a different strand from the message before. |
| 4   | 08-17 | 22.3 min | two PRs open 4 and 20 min into the silence; the agent comes back talking about the agent that opened them | **Partly.** The lead was idle; its builders were not. Billing the lead for it is the wrong answer to a real problem. |
| 5   | 08-17 | 22.0 min | a PR opens 16 min in; the agent resumes chasing an agent that had gone quiet | **Partly**, same shape as #4.                                |
| 6   | 09-07 | 15.6 min | a builder says its task was already shipped, a PR opens 15 min later, a nudge arrives | **Partly**, same shape.                                      |
| 7   | 08-17 | 18.9 min | a subagent returns structured output, a commit and a merge land inside, the lead resumes by messaging another agent | **Weak.** The merge is the queue, not the agent.             |
| 8   | 08-17 | 40.4 min | a subagent returns, a PR merges 19 min in, the lead schedules its own next wake | **Weak.** Merge-queue latency, not working time.             |
| 9   | 09-07 | 46.9 min | a mock builder reports done; a PR opens at +1 min and merges at +6; the lead reads a thread 47 min later | **Weak.** The artifacts are at the start; the rest is genuine idle. |
| 10  | 09-07 | 21.8 min | a builder posts its PR link, the PR merges 30 seconds later, the lead reads a thread 22 min on | **Weak.** Same.                                              |
| 11  | 08-17 | 17.2 min | CA: "Standing by for the arm to complete", then it resumes with `Monitor` tailing the run's log | **Machine yes, agent no.** The run was advancing; the agent was watching. |
| 12  | 09-07 | 53.2 min | a builder's last `Bash`, then the lead calls `TaskStop` on that background task 53 min later | **Machine yes, agent no.**                                   |
| 13  | 09-07 | 33.4 min | a builder reports its PR open and unmerged, then `TaskStop` 33 min later | **Machine yes, agent no.**                                   |

Correction, after your question on row 1. I had read "an artifact is timestamped inside the silence" as proof the agent produced it. It is not: the artifact is attached to the task, and attachment is not authorship. Re-tested with a witness that asks whether any agent event ran inside the silence, row by row: in 8 of the 10 the only events belong to other lanes of the same session, every one of them attached to a different task — so bridging the gap would bill this task for time another task is already billed for. In the other 2 (rows 1 and 3) no agent event ran anywhere in the session. Zero of the ten show this task's own lane working. Your row 1 is the clearest: the session went quiet at 03:54, the 33-line commit at 04:42 is an unattended daily digest with no transcript behind it, and the agent resumed 9 seconds after your message at 04:57. The 15-minute rule cut it correctly. What is left of the undercount column is rows #11–#13 only — 0h / 0.3h / 1.4h — and those are a machine running while the agent watches, not the agent working.

The pattern worth your attention is #2 and #4–#13 — ten of the thirteen — and it is not really about the threshold. It is a lead sitting idle while the work it dispatched runs somewhere else. Billing the lead's silence would be wrong; the honest fix is that the dispatched work is already measured on its own task, and the lead's idle time should stay unbilled. A wider tolerance would "fix" these by billing a blocked orchestrator, which is the thing we removed on purpose.

## B. Fifteen minutes or less, and it is not work

These are bridged today and billed in full. I looked for two shapes: the gap ends
with a call whose only purpose is to ask whether anything has happened yet
(`check_messages`, `next_tasks`, `heartbeat`, `TaskOutput`, `Monitor`,
`list_watched_docs`, `get_workspace`), or the agent says in its own words that it
is doing nothing.

| # | Week | Gap | On either side |
|---|---|---:|---|
| 1 | 09-07 | 14.7 min | "Board event. Not Sentry. Holding." → "Board event. Not Sentry. Holding." — the identical line, twice |
| 2 | 09-07 | 12.5 min | "Still listening — nothing new since the last event." → "No change — idle and connected." |
| 3 | 08-17 | 12.6 min | "(Holding.)" → "(Holding — nothing new.)" |
| 4 | 09-07 | 14.7 min | a review writeup ends → `next_tasks` asks the board for more work |
| 5 | 08-17 | 14.5 min | a report on another agent's transcript → `next_tasks` |
| 6 | 09-07 | 12.5 min | a GitHub review request → `heartbeat` |
| 7 | 08-17 | 10.3 min | "spawn confirmed alive… Standing by." → `next_tasks` |
| 8 | 04-27 | 10.0 min | "6 weekly runs in flight. Will report" → `TaskOutput` poll (non-blocking, 5s timeout) |
| 9 | 04-27 | 11.5 min | "Both edits live." → `list_watched_docs` with no arguments |
| 10 | 08-17 | 13.4 min | "Run healthy at 47m20s. My fallback watchers keep getting…" → "Still on that leg, watchers re-armed. Standing by." |
| 11 | 08-17 | 13.1 min | "Standing by on attempt 2." → "the run keeps advancing through its recovery" |
| 12 | 04-27 | 4.7 min | "Still enriching. Will report numbers next cycle." → `TaskOutput` poll |

Numbers 10, 11 and 12 are the same thing as A-#11: a long job is genuinely running,
and the agent is checking on it every few minutes. The machine is busy; the agent is
not. Whether that counts is a definition question, not a threshold question — and it
is the one place where the two lists are actually the same phenomenon seen from
either side of 15 minutes.

Numbers 1, 2 and 3 are the clearest waste in the whole set: an agent parked on a
board, posting the same "nothing happened" line on a timer, with every gap between
the posts billed as agent hours.

## What I take from this

1. **15 minutes is defensible.** Idle-still-bridged is ~2% of agent hours in every

   week; cut-but-working, once the artifact witness is tested rather than trusted, is 0% — nothing in the band shows the agent itself working through a silence over 15 minutes. So the two errors no longer cancel: the only measured error is that we bill about 2% too much, which is the conservative direction and inside the ±2% run-to-run variance this pipeline already has.
2. **Neither error shrinks by moving the number.** Tightening to 10 minutes would
   catch more of list B and cut more of list A. Loosening to 30 would do the
   reverse — and it would bill the blocked-orchestrator case we removed on purpose.
3. **The two real defects are not about the gap width at all**, and both are cheap

   to state and expensive to guess at:
   - a lead idle while its dispatched work runs elsewhere (A-#2, #4–13);
   - an agent parked on a board posting "no change" on a timer (B-#1–3).

If you want either of those addressed I can file it, but they want their own rule,
not a different constant.

## Limitations

- Both lists are pattern-matched, so both are floors. The idle detector is a phrase
  list and a tool-name list; the work detector needs an artifact timestamped inside
  the silence or a wait-shaped resume. Work that left no artifact and said nothing
  is invisible to both.
- "An artifact inside the gap" is a noisy witness: four of the ten A-cases turned out

  to be a merge queue merging a PR the agent had already pushed — and on re-test all ten fail, because a task can hold an artifact it did not produce (a scheduled commit, a sibling lane's PR). The witness that works is the transcript: did any agent event run inside the silence, and in which lane. First pass got even that wrong by counting Bryan's own message as agent activity.
- Measured on the three era weeks only (2026-04-27, 2026-08-17, 2026-09-07), on the
  runs of 2026-09-15, with the current effort code.
