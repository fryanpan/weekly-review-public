# How the numbers are made

This tool measures one person's week of software work with AI coding agents: how many hours an experienced engineer would have needed to produce what the week produced, and how much of the person's own attention that took. The ratio is the multiplier. This page defines every number the tool prints, in pipeline order, for a reader who has not seen the code.

The tool is built for one user (the author) and reads only local data: Claude Code transcripts, git history, GitHub PR records, an optional team board, and calendar and meeting timestamps. Nothing leaves the machine except the text sent to the estimator model in step 6. It is published so the method can be read, not because anyone else can run it yet.

## Summary

| Number | Definition | Population it is computed over |
| --- | --- | --- |
| Hands-on hours | Time the person was demonstrably active: blocks of their own events (step 2). A union per unit; project and week totals sum the unit figures, with the true week-wide union printed beside the sum as a check | All of their events in the window |
| Output hours | Sum of the estimator's `est_h` over every priced unit (step 6) | Every priced unit in the window |
| Multiplier (pooled) | Output hours ÷ hands-on hours | The whole window |
| Multiplier (per-task median) | Median over units of `est_h` ÷ that unit's hands-on, on units with at least 3 minutes of hands-on. Computed by a script over the run database, not by the run itself | Units the person touched; delegated builds mostly excluded by the floor |
| Coordination share | `est_h` of coordination units ÷ output hours | The whole window |
| Calendar time (`wall_clock_hours`) | Elapsed clock time. 168 for a week. Per board task: first agent work on it to its done transition | One board task, or the window |
| Speedup | (`est_h` ÷ 8) ÷ (`wall_clock_hours` ÷ 24). For a week: output hours ÷ 56 | One board task (median reported with n), or the window |
| Labor hours | Agent time and hands-on time unioned, pauses removed | The window |

The pooled multiplier and the per-task median are different quantities, never reconciled to one number. Pooled is output per hour of the person's attention across everything that ran, and delegated work with large estimates and near-zero hands-on dominates it. The median is the typical ratio on a unit the person actually worked.

## Step 1. Every record becomes an event with a project

An event is one timestamped record from one source:

- A transcript record: a message the person typed, an agent message, a tool call, or a sub-agent dispatch. Each carries its session and, if a sub-agent produced it, its lane.
- A git commit, a PR opened, a PR merged.
- A board record: a task claimed or transitioned, a status, a comment, a decision answered, a read session on a document, a touch.
- A meeting or dictation record, kept as timestamps and word counts only. Spoken text never enters the pipeline.

Each event gets a project, from its working directory or its board, before anything else. Both headline numbers are reported per project, and a wrong project is the one error a ten-minute review cannot see.

Records that are not the person leave the human lane: harness-injected prompts (marked by the transcript's own `promptSource` and `isMeta` fields), messages relayed from other agent sessions, and board activity by agents. Board authorship is an allowlist of the person's own handles, not a denylist of agent names, because agents sign with project names.

## Step 2. Hands-on hours

Only the person's own events count: typed messages, board comments, read sessions, touches, meetings and dictations. Sorted globally, consecutive events join one block when the gap between them is at most 10 minutes for transcript events or 60 seconds for board events. A block runs from its first event to its last. Board blocks add nothing at either end; transcript blocks also union in a modelled reading-and-typing window around each message, so an isolated message is not a zero-length block. A unit's hands-on is the union of its blocks, so within a unit a minute counts once.

Both tolerances are modelling choices, not measurements, and every run prints them. The 10-minute value came from a sweep of one August week (5 min 9.9h, 10 min 18.3h, 15 min 25.3h, 30 min 37.4h) against the person's own plan of 18h for that week, made before the measurement existed. The 60-second value came from a separate sweep of one September week's board events alone (0 s 22.6h, 60 s 25.1h, 300 s 30.3h). Even the zero-gap figure rests on the block rule, which reads every recorded span as work, so no column is free of assumptions.

An earlier model charged only the reading-and-typing window around each message and scored the time between messages at zero. It reported about half the hours the person could account for and was replaced on 2026-08-17. Figures published before that date use the old model and are not comparable.

## Step 3. The person's messages are attached to board tasks

The per-goal summary and per-task ratios need this; the headline numbers do not. First rule that fits:

1. A comment on a board task, or a review reply on one, is work on that task.
2. A comment on a document that exactly one board task links to is work on that task.
3. Otherwise a classifier decides. It sees the goal, title and description of every board task in progress at the time of the message, plus any done in the previous 24 hours, and may answer "none".
4. A message the classifier reads as a general status request goes to the project's coordination unit. Anything else it cannot place starts nothing and stays hands-on for its project.

Every run prints the classifier's "none" share, and a random sample of its placements is reviewed by hand.

## Step 4. Agent events are placed onto priced units

A priced unit is what the estimator sees in one prompt. No unit boundary depends on a time threshold. First rule that fits:

| Rule | Unit | Boundary |
| --- | --- | --- |
| 4a | A merged PR | The PR, its commits, its squash commit, and the sub-agent lane whose worktree the PR was opened from |
| 4b | A board task's own work | Turns in a lead session's own lane naming exactly one board task: a wake by it, its transition, its status, the merge of its PR |
| 4c | Coordination | Turns in a lead session's own lane naming no single board task and editing no files. One unit per project per calendar day, never cut by time. Turns editing the lead's own checkout directly go instead to one "own edits" unit per project per day |
| 4d | A document produced | One document attached to the board or committed, with the turns that wrote it (not yet implemented) |
| 4e | Other agent work | A sub-agent lane with no PR and no document, or what is left in a session per day. Flagged as having no artifact |

A lead session that dispatches builders is never one unit; each of its turns is placed on its own. Before this rule, one lead session of 5,440 events became a single unit holding 15 merged PRs, priced as one 32-hour job.

A sub-agent lane with fewer than 15 events or under 10 minutes is not its own unit. It folds into the board task its dispatch named, or else into that day's coordination unit, and is priced inside its host.

Every artifact is credited once. A PR or commit that appears in two units stays with the unit holding the work evidence, and the other unit's prompt says it is credited elsewhere. Before this fix the same PR was often priced in two or three units and weekly output ran 25 to 60 percent high.

## Step 5. Each priced unit is linked to a board task, and so to a goal

A unit is linked by the first of these sources that names exactly one board task: the board's own record links or names a PR the unit holds; the PR body or branch names a task id (the id is kept, the body text is not); the dispatch prompt that started the lane names one; any event of the unit names one; the unit's tool calls name a worktree named for a task id. A source naming two tasks abstains and the next is tried. An unlinked unit goes into one "no goal" item for its project, whose size is printed so the link's reach is a number, not a claim.

## Step 6. Each unit is priced

The estimator (a Claude model) sees one unit per prompt: its diff or document, what the lane did, and which artifacts are credited elsewhere. It returns `est_h`, the hours a median experienced engineer in the domain would need to produce the same result, and a one-sentence rationale that must account for everything the unit shipped. The framing is METR's time horizons: hours at which an expert would succeed with about 50 percent reliability. The person's own familiarity with the domain is recorded as a tag and does not enter the hours.

Rework (work thrown away or rewritten because the first version was wrong) is not priced: the estimator is told to count only what survived. Separately, the pipeline detects rework loops from reverts, revert branches and the person's own frustration markers, and reports the hands-on and agent hours spent in them on their own line.

The person's review verdicts override the estimator: "ok" keeps the estimate and a corrected number replaces it. A verdict carries forward to later runs by unit id and is dropped when the unit has changed too much since: its event count moved by more than 3x, or its kind changed (a conversation became a coordination day, a lane became a PR unit). Each drop is printed so the verdict can be re-recorded.

Coordination is priced as what a human lead would spend on that many dispatches, reviews and merges, not by event count.

Given 22 merged PRs in one prompt the estimator returned 80h; priced one at a time with their builders, the same work came to about 150h more, and each price could be checked against one diff.

## Step 7. The numbers and the page

Two details beyond the summary table:

- Speedup counts every calendar day, with no weekend adjustment. Per board task it runs from the first agent work, not from the ask, because the wait between them depends on what else is on the board.
- Labor hours is the agent-and-human timeline with pauses removed. Runs before 2026-09-18 called it `wall_clock_hours`; that column now holds calendar time.

The page a reviewer reads shows hands-on by project at the top, then per project at most ten items on a timeline, each a goal with its output hours, plus one "no goal" item. An item opens to its priced units. The design target is that the person can say in ten minutes whether the hands-on split by project feels right and whether the output per item looks plausible.

## Checks that run

- Archive freshness: a window with no transcript files in the archive is an error, not an empty week.
- The unrouted project bucket, printed every run whether or not it is large.
- The share of merged PRs that sit on a unit mentioning them, with the expected range beside it.
- The share of the person's messages placed on a unit, and the classifier's "none" share.
- Per-unit hands-on summed against the week-wide union of the same events, so the overcount in the page's totals is a printed number (about 4% on the weeks measured).
- The resolved window and its day count.

## What the numbers cannot see

- Thinking, reading and conversations that leave no record on any source. A silent source reports "no trace", never "did not happen".
- Delegated work with no board link and no artifact, which is priced but sits under "no goal".
- The estimator's own noise: repeated runs on identical input vary by about 11 percent in total output. A change smaller than that cannot be validated by re-running.
- Anything before mid-April 2026, when a second agent harness ran in parallel and the data is not comparable.
