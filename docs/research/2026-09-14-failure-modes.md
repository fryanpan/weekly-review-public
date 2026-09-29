# Failure modes in the effort data, and the check for each

Every error found so far in the Apr 13 – Sep 13 data, from your worksheet review and from the audits. It lists the automated check that catches each one, and whether that check passes now. Each check is marked existing (it ran before your review) or new (added since, with its PR). Existing checks already covered modes 13 and 14, and mode 3 for estimates of 1h or more. Every existing check is listed below the table.

Numbers are from `run-20260914T210858Z`, the latest full run.

## Your verdicts, re-checked

Your 24 worksheet verdicts now run as a check on every run. Each verdict is tied to the events of the task you reviewed, so it still applies after task boundaries move.

| Metric     | Run you reviewed | Latest run   |
| ---------- | ---------------- | ------------ |
| Hands-on   | 4 of 11 hold     | **10 of 11** |
| Wall clock | 0 of 5           | 1 of 5       |
| Project    | 0 of 2           | 0 of 2       |
| Estimate   | 22 of 23         | **5 of 23**  |

On the run you reviewed, the estimate row is close to automatic: it compares each estimate with itself, allowing a factor of 2. The drop on the latest run is real. Fixes #33–#35 moved task boundaries, so work you judged as one task is now split or merged into bigger tasks.

## The list

| #   | Failure mode                                                 | Example you flagged        | Check                                                        | Latest                   |
| --- | ------------------------------------------------------------ | -------------------------- | ------------------------------------------------------------ | ------------------------ |
| 1   | **Hands-on 0 when you did interact.** Your ask landed on a neighbouring task. | 21988d08da                 | new (#31): no split tasks; no task holding your messages at 0h | 248 tasks, 449h          |
| 2   | **Comments counted as instants**, so hands-on ran low        | f497b05f64                 | new (#40): your verdicts (hands-on)                          | fixed: 10 of 11          |
| 3   | **Wall clock 0: commits with no recorded conversation.** They may come from a machine or store we don't read. | 360a9f47ea                 | Existing (but used to be only for `est_h > 1h` tasks).  Widened.Any task that a human or agent worked on should have `wall_clock` > 0 | 643 tasks, 687h          |
| 4   | **Wall clock held open by polling.** Pings 20 min apart are counted as continuous work. | a539856708                 | new (#41): wall clock is mostly minutes with activity        | 197 tasks                |
| 5   | **One task swallows a board lead's sprint**                  | 45d47f240b                 | new (#41): no task over 2 days or 100 agent lanes; new (#31): wall ≥4h and >3× est | 26 tasks, 217h; 16 tasks |
| 6   | **One piece of work split across many tasks**                | f497b05f64 (23 tasks)      | new (#40): your verdicts (split guard)                       | failing                  |
| 7   | **A small task merged into a much bigger one**               | 3614aa47bd → a 120h task   | new (#40): your verdicts                                     | failing                  |
| 8   | **Wrong project**: unregistered folder, or a doc name that implies the project | 64f06b9a0b, 3c754d0aba     | new (#40): your verdicts (project); existing: unknown bucket | 93 tasks, 67h            |
| 9   | **The same work estimated differently between runs**         | 04f1d68f7d 0.25h → 1h      | new (#41): Estimates stable within 2× between estimates      | 1 task                   |
| 10  | **Estimate far from 1 day per 1,000 lines**                  | b70873945c                 | new (#31): `est_h` for coding should be > `LoC added` / 4000 unless it's a trivial search and replace style change | 98 of 267                |
| 11  | **PR history capped at 200 per repo**                        | 45d47f240b (15 of 262 PRs) | new (#33): PR fetch is complete -- no PRs missedLinked PRs ingested | fixed                    |
| 12  | **Reply parsing lost the work type**                         | top live-feedback tasks    | new (#41): Every estimate carries a type                     | 15 → 1                   |
| 13  | **The same artifact credited to two tasks**                  | —                          | existing: est double count; orphaned PRs                     | passing                  |
| 14  | **Excluded projects leaking in**                             | —                          | existing: excluded projects leak no events                   | 72 events                |

### How each check works

Every check reads the run's own database after the run finishes. None of them calls a model.

| #   | Method                                                       |
| --- | ------------------------------------------------------------ |
| 1   | Takes each estimated task under the hands-on floor. It flags the task if an agent session on it holds your message on a different task, or if the task holds your messages and still scores 0.00h. |
| 2   | Today only your verdicts: each hands-on verdict must hold. **Proposed automatic check:** a task's hands-on must be at least the review time its own comments carry. |
| 3   | Flags any task with an estimate above 0 and a wall clock of 0. |
| 4   | For each task with a wall clock of 1h or more, counts the minutes that hold an agent or user event. Board actions don't count. It flags the task when those minutes cover under half the wall clock. |
| 5   | Counts the days with agent work and the distinct agent lanes on each task, and flags more than 2 days or more than 100 lanes. A second check flags a wall clock of 4h or more that is over 3× the estimate. |
| 6   | Today only your verdicts: the events of the task you reviewed must mostly sit on one task. **Proposed automatic check:** flag any board task card, doc thread or PR whose events land on more than 2 tasks. |
| 7   | Today only your verdicts: the task holding your reviewed events must match your hours. **Proposed automatic check:** flag a task that holds 3 or more board task cards, or doc threads from unrelated docs. |
| 8   | Your verdicts, plus the size of the unknown-project bucket. **Proposed automatic check:** flag a task whose project differs from the one its working folder or its board names. |
| 9   | Finds the previous run over the same week. It flags a task with the same id and an event count within 10% whose estimate moved more than 2×. |
| 10  | Adds up lines added on a code task's PRs, counting each PR once. It skips diffs that are tiny, huge, generated-looking, or sweeps where deletions nearly match additions. It flags an estimate outside 4× of one day per 1,000 lines. |
| 11  | The fetch log records any repo whose PR fetch failed or hit the result cap. Separately, PR links pasted in conversations for a fetched repo must mostly have been ingested. |
| 12  | Flags any estimated task with no work type.                  |
| 13  | Within each epic, flags when the tasks holding your conversation and the artifact-only tasks both carry over 5h of estimate, which is the same work estimated twice. It also flags merged PRs that sit on no task. |
| 14  | Counts events from projects marked excluded.                 |

Your verdicts are checked like this: each one is pinned to the events of the task you reviewed. Each run finds the task now holding most of those events and compares its hands-on, wall clock, project and estimate with your numbers. An estimate passes within 2×.

## Checks that already existed

These ran on every run before your review. Results are from the latest full run. "Review" means the check found something to look at.

| Check                                                        | What it catches                                        | Latest             |
| ------------------------------------------------------------ | ------------------------------------------------------ | ------------------ |
| each event on at most one task                               | the same work counted twice                            | pass               |
| at most 2 tasks at once per session                          | one conversation cut into overlapping tasks            | review: 9 sessions |
| at most 3 tasks at once per project                          | the same, across a project                             | review: 9 projects |
| no duplicate-work task pairs                                 | two tasks for one piece of work (shared refs or title) | review: 65 pairs   |
| over 95% of events on a task                                 | work dropped from every task                           | pass: 99.1%        |
| wall clock ≥ hands-on                                        | a task shorter than your time on it                    | review: 44 tasks   |
| wall clock ≥ agent-busy time                                 | a task shorter than its agents' time on it             | pass               |
| task first ≤ last timestamp                                  | broken time ordering                                   | pass               |
| delegated tasks carry none of your messages                  | your ask mislabelled as agent-only                     | pass               |
| estimate share on tasks without your messages under 60%      | delegated work swamping the totals                     | pass: 18%          |
| no task estimated below its hands-on                         | estimates under your measured time                     | review: 50 tasks   |
| every substantive task has an estimate                       | enrichment failures                                    | review: 1 task     |
| estimate agrees with its own rationale (3×, and 2–3× drift)  | the model's number contradicting its own reasoning     | review: 5 and 2    |
| no reviewed override 3× above the rationale; no override echo | a stale verdict overwriting a fresh estimate           | pass               |
| no estimate double count across views; no orphaned merged PRs | mode 13                                                | pass               |
| excluded projects leak no events                             | mode 14                                                | review: 72 events  |
| no task estimated ≥1h with zero wall clock                   | mode 3, for larger tasks only                          | widened in #31     |
| hands-on vs stated availability                              | a week total that doesn't fit your hours               | informational      |
| archives fresh; no unarchived backlog; archived copies current | a week read from a stale or incomplete transcript copy | pass               |

## Fix order

The loop now runs on single weeks, one from each data era: pre-workspace (Apr 27 – May 3), workspace task cards (Aug 17 – 23), and meetings and board leads (Sep 7 – 13). First fix, #42: a lead session's hours-long sprint no longer merges into the next message's task. 3614aa47bd now holds. Each fix must keep every verdict in that week holding and must not break any audit that passes.

1. Boundaries: modes 6, 7 and 5. These cause most of the estimate regression.
2. Wall clock: modes 4 and 3.
3. Project routing: mode 8.
4. Estimates: modes 9 and 10, re-checked once boundaries are stable.

### Fix plan, with a task per fix

Every open mode has a task on the board. Each fix is done only when its check passes on all three era weeks (Apr 27, Aug 17, Sep 7).

| Modes                       | Task                                                         | How we'll check going forward                                | How I'm fixing                                               |
| --------------------------- | ------------------------------------------------------------ | ------------------------------------------------------------ | ------------------------------------------------------------ |
| 5, 6, 7 (task boundaries)   | [One piece of work is one task](/workspaces/w-id?task=t-id) | New audit: no board card or doc thread lands on more than 2 tasks. The existing oversized-task audit (over 2 days or 100 lanes) must pass. | #42 capped lead-ins. #43 (merged): agent turns answering a doc comment join the comment's task. In test now: agent work stamped with a board card joins that card's task. |
| 1 (ask split from its work) | [Hands-on on the right task](/workspaces/w-id?task=t-id) | Existing split-task audit, under the impact limit.           | I'll trace a sample from the era weeks, then attach agent work to the task holding the ask it answers. Much of this shares a cause with the boundary fix. |
| 3 (zero wall clock)         | [Nonzero wall clock](/workspaces/w-id?task=t-id) | Existing zero-wall-clock audit, under the impact limit.      | I'll separate commit fragments cut off from their conversation from work done elsewhere. The first kind get attached by branch, repo and time; the second get the span of their commits. |
| 4 (polling wall clock)      | [Wall clock counts real activity](/workspaces/w-id?task=t-id) | Existing polling-wall audit under the impact limit, and your wall-clock verdicts hold. | Wait-and-poll turns will no longer bridge gaps when the wall-clock span is built. |
| 8, 14 (wrong project)       | [Right project](/workspaces/w-id?task=t-id) | New audit: a task's project must match the project its working folder or board names. Worktrees resolve to their parent repo, and agent-wrapper projects resolve to the project they do work for (such as CA). Unknown stays under 2%. | I'll register the missing folders and route docs by their board. |
| 9, 10, 12 (estimates)       | [Stable, size-fitting estimates](/workspaces/w-id?task=t-id) | Existing LoC (4×) and estimate-drift audits, under the impact limit. | This starts once boundaries are stable, since boundary moves cause most of the drift. Then the estimator sees the task's diffstat. |
| 15–19 (older checks)        | [Older checks over the limit](/workspaces/w-id?task=t-id) | Each existing check, under the impact limit.                 | I'll measure the hours each touches and trace a sample of each, after the boundary fix lands, since several likely share its cause. |
| All (checks nobody acts on) | [Impact limit stops the run](/workspaces/w-id?task=t-id) | A test proves a run exits non-zero when any check is over the impact limit. | Every check reports the tasks and hours it touches, and the run fails when either is over the limit. Nothing is accepted as a baseline. I'll also add automatic checks for modes 2, 6, 7 and 8, so your verdicts test the checks instead of standing in for them. |

**The impact limit.** Any issue that touches more than 5 tasks or 100h across Apr–Sep must be fixed, since that can move the final results by a few percentage points. On a one-week check that is more than 2 tasks or 5h. There are no accepted counts.
