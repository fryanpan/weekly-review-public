# Product Decisions

## 2026-09-18 — `wall_clock_hours` is calendar time; the old figure is `labor_hours`
- Method Step 7 (board task, 2026-09-18). The column called `wall_clock_hours` held his time and agent time unioned with pauses removed; that is labor, and it is now `labor_hours`. `agent_labor_hours` (per-lane agent time) is now `agent_lane_hours`. `wall_clock_hours` is calendar time: 168h for a week; per board task, first agent work on it inside the window to its last `done` transition at or before the window's end. Speedup = (`est_h` / 8) ÷ (`wall_clock_hours` / 24), every calendar day counted (the user, 2026-09-17: no weekend adjustment), so the week's speedup is `est_h` / 56. A board task not done by the window's end, with no agent work in the window, or done before its agent work here has no span and the digest says which, rather than a zero.
- Run dbs carry `run_meta.schema_version = "v2"`; `persist.metric_columns` is the one place a v1 db is read under the old names, and every reader (export, validate, verdict cases, billing, the worksheet scripts) goes through it. A test fails if any source or script line reads `m.wall_clock_hours` or `agent_labor_hours` elsewhere.
- Measured on the 2026-09-07 week (run-20260918T101247Z, 801 units, Σest 1,202.2h): week speedup **21.5x** (1,202.2h / 56h); `labor_hours` summed per unit 420.9h, `agent_lane_hours` 387.5h, hands-on 33.1h. 338 board tasks had units this week; **240 have a calendar span, median speedup 4.5x (n=240)**; 55 not done by the window's end, 37 with no agent work in the window, 6 done before their agent work here. Span across the 240: median 1.5 calendar hours, p25 0.5h, p75 8.2h, 206 under a day, 7 over three days, longest 96.7h.
- The earlier measurement in the method doc (median 4.7 calendar hours, speedup 1.2x, n=237) started each board task at its first agent work over its whole life; this one starts at the window's first agent work, so a task begun in an earlier week reads shorter here and faster. The digest line says so. Measuring from the task's whole life needs events from outside the window and is a separate change.
- The Σ of per-unit `wall_clock_hours` (7,395.7h over 801 units) is not printed and must not be: units overlap, so the sum counts a clock hour once per live unit. The week's calendar time is 168h by definition.
- Reversible: rename plus one new module (`calendar_time.py`); the formula is one function.

## 2026-09-18 — His unlinked messages go to a classifier that may answer "none"
- Method Step 3, rule 3 (board task, 2026-09-18). A comment on a board task, or on a doc exactly one board task links, is placed by structure; the rest — comments on docs several tasks link or none, and terminal messages — go to a classifier. For each it sees the goal, title and description of every board task in progress at the moment he wrote (from the store's own transition list) plus those done in the previous 24h, and answers one id, "none", or "none" as a status request, which goes to the project's coordination unit for that local day. An id it was not shown is refused. Which boards it sees is decided per message: a comment sees its doc's board, a terminal message the boards that hold its project's work, and a message with neither sees every board. One call per (project, local day, ≤25 messages). A placement joins the conversation unit on that board task, else the largest unit on it, else a new unit named after it; it never moves an attached event, and is recorded on the trace as rule `classifier`.
- Measured on the 2026-09-07 week (run-20260918T091119Z, 799 units, Σest 1,200.9h): 848 messages of his; 261 placed by structure; of the 587 unlinked, **413 placed (70%)** and **174 "none" (30%)** — 14 of those status requests to coordination, 25 with no task in progress on their boards at the time. 50 calls, 0 unparsed, 0 invented ids. Messages on a unit with a board task went 261 → 677 of 848 (31% → 80%). 412 of the 427 placements landed on an existing conversation unit, 14 on coordination, 1 on a lane; 12 new units were made for board tasks with no unit yet, 1 new coordination unit. Placements spread over 98 units, at most 37 on one.
- Effect on estimates is small: Σest 1,186.7h → 1,200.9h (+1.2%); the 85 units that gained messages and had a prior estimate moved +5.5h between them, the largest single move −2.0h. The classifier changes which goal his words sit under, not how much output is priced.
- The 12-row random sample for the user's check is on the board (a review doc; his words, so it stays in gitignored `docs/data/` and beside the run db). Reading it before he does: at least one of the 12 looks wrong (a one-line "spin up X agent" placed on an unrelated approval task from 116 candidates), and one is a judgement call. His verdict sets whether 70% placed is a share to trust or a share to tighten.
- Reversible: `message_classifier.py` (new), one block in `cli.py`; the prompt is one string.

## 2026-09-18 — Coordination is priced as a lead's day in counts; a unit with no artifact says so
- Method Step 6 (board task, 2026-09-17). The user, 2026-09-17: coordination is work that takes time, shown as its own line per project. A coordination unit's prompt is now "a human lead handled N dispatches, N PR reviews, N merges, N PRs opened, N board actions, N messages to other sessions over D day(s)" plus lane and message counts, and nothing else: no diffstat, no artifact line, no work-log text. The system prompt says to price it as that lead's time, not by event count, and that the builds it handled are priced in their own units. A unit with no commit or PR joined to it is priced from its work log and told to say "no artifact" in its rationale. A test fails if a coordination prompt carries a diff, a diffstat, or a dispatch brief.
- Measured on the enriched 2026-09-07 week (run-20260918T081110Z, 786 units, Σest 1,186.7h): 786 of 786 units have an estimate. 55 coordination units, Σest 47.1h (4% of output), mean 0.86h, range 0.02–4.5h; 53 of the 55 rationales reason from a lead's time. Per project the coordination line ranges from 2% (claude-live-feedback-plugin, 19.6h of 906.7h, 8 units) to 12% (ai-team-lead, 5.1h of 41.8h, 8 units) among projects with real output; the busiest single day priced at 3.5h for 133 briefs, 79 reviews and 13 merges across 9 lanes. 104 rationales say "no artifact". The run prints the per-project line and the no-estimate count every time.
- One caveat in that 47.1h: the largest coordination unit (6eb59851ed, client-b, 4.5h, 62 events) is not the estimator's number. The user agreed with a 4.5h estimate on 2026-09-16 when that id was a 40-event conversation; the id now sits on a coordination day whose own rationale says 0.3h, and the carry-forward guard (3× on event count) lets it through at 62 events. Audit 14 flags it. A verdict should not carry forward onto a unit whose kind changed since the review; filed as a follow-up rather than changed here, since it is the user's verdict.
- Reversible: `enrichment.py` (one branch keyed on `unit_kind`), two prompt paragraphs, three lines in `cli.py`.

## 2026-09-18 — Each priced unit links to a board task; a lane on a board task is a unit of its own
- Method Step 5 (board task, 2026-09-18). A dispatched lane that worked on a board task is its own priced unit carrying that board task (`link_source="lane_card"`), one stretch per (lane, board task); it is no longer pooled onto the board task's measured task. On the week of 2026-09-07 the widest task (473d13e3c7, one board task) held 56 lanes, 13,259 events and 163.9h; it is now one conversation unit of 5,587 events plus 34 lane units, all linked to the same board task. A stretch under the 15-event / 10-minute floor folds onto the board task's unit, not the day's coordination (18 stretches there, each a whole agent run under 10 minutes).
- After pass 9, a unit with no board task takes the first source that names exactly one: the board store's own PR links (`board_pr`), the PR body or branch (`pr_ref`, carried as `named_tasks` on the PR event; the body text is never stored), the dispatch prompt (`dispatch`), any event naming a board task (`own_card`), or a worktree named after a board task id (`worktree`). A source naming two abstains and the next is tried. Ids are validated against the store, since a worktree can be called `t-page-bar`. A PR unit split from a board task's unit keeps the board task (`host`). `own_card` can put a whole coordination day under one goal on the strength of one event naming a board task (3 of the 8 `own_card` links on the measured week are coordination units); accepted for now, since the alternative is a day under "no goal", and the source is printed so it can be re-examined.
- Measured on the stored 2026-09-07 week: 31 of 237 unlinked units gain a board task (23 by the store's PR links, 8 by an event naming one; 13 abstentions). `pr_ref` and `worktree` are 0 because the archived run predates `named_tasks` and the worktree-naming rule is not deployed yet. Every unit with a board task (583) has a goal. The run prints per project: units with a board task / units, and estimated hours in "no goal".
- Reversible: `segmentation.py`, one column (`tasks.board_task_source`), two extractor helpers.

## 2026-09-18 — Only a clear link places a message; the eight grouping rules are gone
- The user, reviewing the 2026-09-07 week: "The first task I looked at has twelve PRs or something and 10k lines on four loosely related areas of work all tied together and with no board task links." He approved the method doc that replaces grouping-by-timing-or-wording with grouping-by-link.
- Removed from `segment()`: 6-hour same-session extension, 30-minute cross-session proximity, new-topic phrases, issue-number match, shared-title-word attachment (topical A/B/C and the evidence merge), stray-artifact adoption (in-project and cross-project), message-starts-a-task, and PR-opened-seals-the-task. Their functions, constants, the `sealed` field and their tests are deleted, not disabled.
- What places a message now: a comment on a board task goes to that board task (`board_card`). A message with no link starts nothing and is left unplaced until the classifier (Method Step 3) attaches it. Agent turns place by `card_turn`, `comment_turn`, `ask_turn`, else pass 5 makes lane, worktree and coordination units. Artifacts place by `same_artifact` or start their own unit, then the PR and commit joins run as before.
- Measured on the week of 2026-09-07 (stored events, no enrichment), before → after: tasks 676 → 624; lane 98 → 114, lead_worktree 34 → 47, coordination 43 → 55 (9,680 → 22,979 events, because turns that a 6-hour rule used to glue onto his last message now go to the day's coordination), PR units 89 → 93. Merged PRs 264 of 264 on one unit each, both runs. His messages on a measured task: 261 of 848 (31%), all by board-task link; the other 587 wait for the classifier. Unattached agent events 485 of 171,823. Printed commits on the task that printed them 350 → 354 of 354, because pass 6d now moves a squash commit with the PR it lands when it moves that PR off a coordination day. The run prints a rule-count table every time; none of the eight names can appear in it.
- The 95% coverage target (2026-04-19) is withdrawn. Coverage measured whether the grouping rules placed everything, and they placed everything by construction; the printed share is now a fact about how much of the week ran through the board, with no target.
- Reversible: one file plus its tests.

## 2026-09-17 — A lead's own lane is never one group: per turn, to a board task, a worktree, or the day's coordination
- The user, on the 2026-09-07 week: one lead session from 09-10 18:21 to 09-11 23:07 became one measured task (5,440 events, 15 PRs, 32h). The per-session leftover group with a 6-hour cut can only split a session at a quiet hour, and a lead has none.
- Now (pass 5): each turn of a lead's main lane is placed on its own. A turn whose edit, write or commit calls name a worktree goes to that worktree's unit for the session (`lead_worktree`), which pass 6d joins to the PR opened from there. A turn that wrote in the main checkout is the session's own-edits unit for that day. Every other turn goes to one coordination unit per project per calendar day. A builder lane is one unit however long it paused (no 6-hour cut); a lane under 15 events or 10 minutes folds into that day's coordination. Turns about one board task were already placed by pass 3 and are untouched.
- A calendar day is the machine's local clock. Transcripts are stamped UTC, whose day turns over mid-afternoon here.
- Worktree and coordination units have no floor: a three-event turn that committed is work with an artifact, and folded into coordination its commit would price the lead's day.
- A coordination unit never hosts a stray artifact (pass 6c) and gives up even a single merged PR (pass 9), because it is priced as a lead's day and never with a PR diff. When a lead opens a PR from its coordination turn, pass 6d moves the PR and that turn to the lane that built it; before, it merged the lane into the day (one 417-event lane sat inside a coordination unit).
- Measured on the week of 2026-09-07 (stored events, no enrichment): 676 tasks; 98 lane, 43 coordination, 34 lead_worktree, 89 PR units. 43 coordination units hold 9,680 events; the widest is 23.0h (one day). Lead-lane events in coordination by project: 2% to 59%, 15% on the busiest. Widest lane unit 25.4h (539 events, no cut by design). Merged PRs 264 of 264 on a task, none on two. The run prints these every time.
- Reversible; one file plus the run's printout.

## 2026-09-17 — A squash commit goes with the pull request it lands
- A lead merges its builders' PRs and prints the squash hash, so the print join put the squash commit on the lead while the PR sat with the builder. Artifact credit treats the two as one identity and gives it to the task with the most non-artifact events, which was the lead: one lead task priced 22 PRs at 80h while each builder was told its PR was credited elsewhere. 164 identities were shared between two tasks.
- Corrected 2026-09-17: this entry first said those PRs were priced twice. They were priced once, on the wrong task.
- Now (pass 6a): a commit-only task whose commits are all titled "... (#N)", each with PR N merged within 10 minutes, joins the task holding PR N. It runs before the hash-print join. Matched on number plus time because a commit's repo field is the project slug.
- Measured on the week of 2026-09-07 (stored events): squash commits on their PR's task 84 of 224 before, 223 after. `printed_artifact_credit` counts a squash commit as credited on its PR's task: 326 of 326 commits, 263 of 266 PRs.
- Enriched re-run (run-20260917T192554Z): 1 shared identity, the lead task 80h → 0.75h, Σest 942.1h → 1094.6h over 545 tasks. +151h is on the 189 tasks whose artifacts moved (32h of it a task whose enrichment failed in the earlier run); the other 356 tasks moved +1.3h. Builders pricing their own PRs sum to more than the lead's pooled 80h.
- Reversible; one file.

## 2026-09-17 — A builder a lead dispatched is its own task, and joins the PR opened from its worktree
- The user, on the 2026-09-07 week: big tasks tie to many PRs and to no board task, or to one that does not cover the work. His one message of 2026-09-08 22:41 was answered by 22:57, and its task (`b689a63a57`, est 28h) held 12 builders and 6 PRs through 05:00.
- Cause: a session was cut only at his messages. A lane with no ask, comment or board task of its own joined the session's last message, and a session with no message became one delegated task per session (`d41afbd69b`: 49 lanes, 12,717 events, 27 merged PRs).
- Now: a dispatched lane with 15 or more agent events over 10 minutes or more (the existing delegated thresholds) is its own task, titled from the lead's dispatch summary. Shorter subagent lanes stay with the message that caused them.
- A lead opens its builders' PRs itself, so the PR sat on the lead's turn and the build on a lane with no PR. The lane now joins the task that first printed a PR opened from the lane's worktree (same session, lane active in the 6h before). 22 lane tasks joined on that week.
- A PR-only task goes to the task that linked it FIRST, not most: the opener prints the URL once, the reviewing lead links it many times. 39 of 265 opened PRs sat off their first linker; now 0.
- Measured on that week, re-segmented from stored events: 448 tasks become 545; most merged PRs on one task 15 (the lead's own main-lane day); printed credit unchanged at 263 of 266 PRs and 323 of 326 commits.
- Not done: PRs carry no head branch, so a lane whose PR was opened more than 3 minutes after the last worktree command stays apart. Reversible; one file.

## 2026-09-17 — A PR or commit goes to the session that printed it, before any rule based on time or shared words
- The user, reviewing the 40 biggest tasks of the 2026-09-07 week: big tasks held many unrelated PRs, and he stopped trusting the data.
- Measured on that week: 51 of 264 merged PRs sat on a task whose transcript mentions them. After the change it is 263. Printed commits on the session that printed them: 92 of 326 before, 323 after.
- Two causes. Proximity adoption (pass 4c) ran before the identity joins (passes 6, 6b), and the joins only move a task made of nothing but artifacts, so they arrived to find 373 of 396 fragments already glued to a nearby conversation. And pass 3 attached a PR to any task sharing two title words within 72h.
- Now: an artifact a transcript linked or printed forms its own task in pass 3, the identity joins run next, and proximity adoption takes what is left. Adoption only takes a task with no turns of its own, because it now runs after the delegated builds exist.
- Manual merges (pass 4d) moved to after every artifact pass. They always ran after 4c; a directive with no target merges into the preceding task, which before adoption can be a bare commit.
- Not changed: the 2-word rule still applies to an artifact nobody linked. Reversible; one file.

## 2026-09-16 — Tasks with no conversation keep their estimated hours

- The user's ruling, on a review item, 2026-09-16: **"Keep them."** A task whose events are commits and pull requests with no agent conversation at all is still priced from its diffstat, and its hours stay in every pooled figure.
- **Size of the population it rules on.** Over 2026-04-13 to 2026-09-13 (run `run-20260914T210858Z`, 2,258 tasks), 656 such tasks hold 1,054.8h, 16.3% of all estimated hours. They are priced in one task each: of their 288 PRs, 264 appear in no other task and the 24 that do are credited to the task holding the conversation.
- **What the ruling moves.** Pooled monthly multiplier as published, Jul / Aug / Sep: 9.8x / 20.7x / 22.5x. With these tasks removed: 9.4x / 18.0x / 18.9x. The per-task median (tasks with 3 minutes or more of hands-on) is unaffected either way: 7.1, 6.0, 5.7, 5.3, 5.8, 5.5 for Apr through Sep.
- **Why keep.** Work the user kicked off from a board task or a planning doc and never discussed in a transcript is still his output; dropping it would delete the delegated work the board exists to create (his thread on the August doc, 2026-09-16: "Many tasks are going to take less than 3 minutes to kick off. Even bigger tasks."). The cost, which stays: these estimates rest on line counts alone, and line counts can be checked-in data. `diffstat_exclude` (PR #102) is the mitigation, per project, for paths that are never authored work.
- No code change: this is the pipeline's current behaviour. The alternative (drop them from pooled totals, keep them listed) is not built.

## 2026-09-15 — `AGENT_QUIET_STITCH_SEC` stays at 15 minutes, and its known error is accepted

- The user's ruling, on a review item, 2026-09-16: *"Keep 15 and document briefly your research on how it goes wrong. Keep that doc in our records. And document decision that for now, the few percent this messes up aggregate metrics doesn't matter."*
- The research is [2026-09-15-fifteen-minute-gaps.md](../research/2026-09-15-fifteen-minute-gaps.md), kept in the repo for that reason. It lists both directions with named examples.
- **What the rule gets wrong, measured on the three era weeks.** It still bridges idle for **1.4h / 4.7h / 6.4h — about 2% of agent hours in every week**, and it cuts genuine work for **0h / 0.3h / 1.4h**, all of which is a machine running while the agent watches. Both figures are floors: each comes from a pattern list, so anything shaped differently is missing from both.
- **So the errors do not cancel, and the residue points the safe way.** We bill about 2% too much, inside the ±2% run-to-run variance this pipeline already has, and in the conservative direction the user asked for on an earlier review item — a multiplier that reads low is a better input to a client promise than one that reads high.
- **Accepted as immaterial for now.** No correction, no flag, no adjustment to any published aggregate. A tolerance is a modelling choice inside a range, never a measurement, and this one is stated rather than hidden.
- **Moving the constant would not help.** 10 minutes catches more idle and cuts more real work; 30 minutes does the reverse and starts billing a blocked orchestrator, which was removed on purpose. The two defects the research actually found — a lead idle while its dispatched work runs elsewhere, and an agent parked on a board posting "no change" on a timer — want their own rules, not a different number. The second of those now has one (audit B8f, 2026-09-15).

## 2026-04-19 — Repo goes private (reverses earlier public+MIT decision)
- After the task-leverage design took shape, we decided the digest and the raw event stream contain enough personal/project-level detail that re-sharing is a deliberate future choice, not a default.
- Open-source re-release remains an option once the framework stabilizes and a redaction pass is built.
- Executed: `gh repo edit fryanpan/weekly-review --visibility private` run 2026-04-19.
- Branch protection on main; all changes via PR.

## 2026-04-19 — Public repo, MIT-licensed (SUPERSEDED)
- Superseded by the private-repo decision above on the same day.
- Original rationale: other developers should be able to install this skill and configure their own data sources.

## 2026-04-19 — Skill, not standalone app
- The skill (`/weekly-review`) is the entry point. Helper scripts in `scripts/` are independently runnable but the skill is what users invoke.
- Reason: matches the conductor's `analyze_transcript.py` pattern (skill + helper scripts), and keeps the user experience inside Claude Code.

## 2026-04-19 — Local-first, per-user config at `~/.config/weekly-review/`
- All data pulls and synthesis happen on the user's machine.
- No shared service, no telemetry.
- Each user supplies their own Timely API key, Calendar OAuth, GitHub username, etc.
- Config files: `projects.yaml`, `anchors.yaml`, `overrides.yaml`, `effort.yaml`, `exclusions.yaml`, `timely.token`.

## 2026-04-19 — Helper scripts in Python
- Matches `analyze_transcript.py` pattern from ai-project-support.
- Pure stdlib where possible; explicit deps documented.
- Each script is independently runnable with `--help` and emits JSON to stdout.

## 2026-04-19 — Explicitly NOT measuring (the bad proxies)
- Total Claude hours (mixes user time with agent time, misleading).
- LOC, cyclomatic complexity (refactors delete code; design work has zero LOC).
- Commits/week (a 2026 commit isn't comparable to a 2024 commit from the same person).
- Single-number productivity score (gameable; SPACE framework guidance).
- These are deliberately excluded from the headline. Don't add them later "for completeness" — they're misleading by design.

## 2026-04-19 — Decisions and capabilities sourced from project docs
- Each project's `docs/product/decisions.md` is the canonical record of "what got decided this week."
- Each project's `docs/process/retrospective.md` and PR descriptions are the canonical record of "what shipped."
- If a project doesn't write these files, the digest can't see those signals.

## 2026-04-19 — Primary unit = task, not week-project grid
- Tasks are topic-bounded and can span sessions, dormancy gaps, and projects.
- The event stream aggregates into tasks first; weeks are aggregates of tasks.
- See spec: `docs/product/specs/2026-04-19-task-leverage-model-design.md` §2.

## 2026-04-19 — Primary metric = leverage (est_human_hours / bryan_active_hours)
- `est_human_hours` estimates how long a competent domain-expert human would take to produce the same output, doing it as simply as reasonable. METR time-horizons framing ([arXiv:2503.14499](https://arxiv.org/abs/2503.14499)).
- `bryan_active_hours` is derived from transcript user-messages via a WPM-based formula; we label this "active time," not "effort," because it underestimates total cognitive effort.
- Secondary: `leverage_agent = est_human_hours / agent_active_hours`.
- Components (numerator, denominator, rework, switches) are always shown alongside the ratio in the digest. No single-number dashboards.

## 2026-04-19 — Rework absorbed into the same task
- Iteration, bug-fixing, and polish on the same topic stay in the same task.
- This lowers the task's leverage naturally — no separate rework KPI.
- Rework is surfaced as a counter-signal in the digest for awareness, not as a headline metric.

## 2026-04-19 — Attribution and licensing practice
- Every external code or idea we build on is tracked in `ATTRIBUTIONS.md` at repo root.
- License checked before any code copy; when in doubt, reimplement from a description instead.
- Applies to internal code borrowed from the user's other repos too.

## 2026-04-19 — V1 uses YAML overrides, not an HTML review tool (supersedes earlier "HTML in V1" decision)
- Review and calibration for the first pass happen through `~/.config/weekly-review/{overrides,anchors,expertise}.yaml` plus `weekly-review --recalibrate` and `--inspect` CLI flows.
- HTML comes later if the YAML workflow proves too painful in practice.
- Reason: scope for this pass must fit in 1-2 days. YAML + CLI is sufficient to test whether the metrics are trustworthy before we invest in a UI.

## 2026-04-19 — Recalibration is biweekly, with an initial training pass (supersedes earlier "quarterly" decision)
- Before the first trusted digest: the user labels 5-10 recent shipped tasks. Those seed the anchor set and the calibration curve.
- Every 2 weeks (or more often, on demand via `weekly-review --recalibrate`): review a random sample of newly-shipped tasks, adjust estimates, refit the bias correction.
- Reason: the user asked for faster cadence than quarterly so the rubric stays calibrated as workflows evolve rapidly.

## 2026-04-19 — No taxes or multipliers on primary metrics
- Context switches are counted, not penalized. Secondary goal: reduce per-day switches without hurting leverage.
- Resumed tasks (>24h gap) are flagged as a count, not as a time adjustment.
- Reason: N=1 personal analysis shouldn't bake population-study averages into the primary number. Traceability of raw numbers beats a "corrected" headline.

## 2026-04-19 — Events are regenerable, not immutable
- We re-extract from source (transcripts, git, GitHub) whenever the schema or segmenter changes.
- Runs are versioned (`run_id`, `schema_version`) and kept under `runs/<run_id>/`.
- `weekly-review --diff-runs <old> <new>` shows what changed — used to validate that a new extractor is actually better before making it the default.

## 2026-04-19 — `bryan_domain_expertise` tag per task
- Each task tagged `expert | competent | learning` via LLM inference against the user's history + a seed list in `~/.config/weekly-review/expertise.yaml`.
- Reason: leverage in domains where the user is *not* an expert is the strongest signal that the agent team is expanding what's possible.

## 2026-04-19 — Coverage target ≥ 95% (withdrawn 2026-09-18, see above)
- At least 95% of user-message events in any period must be assigned to a task. Below that, the leverage number isn't trustworthy and the digest flags it.
- Reason: unassigned events inflate the denominator through the back door ("active time on what?"). Coverage is the simplest check that segmentation is working.

## 2026-04-19 — Human-effort rubric anchored to METR time-horizons
- The LLM estimator is primed with 5-10 anchored examples from the user's own recent history, not a generic rubric.
- Rubric dimensions (scope / depth / type / novelty) are secondary calibration knobs.
- See spec §8.

## Open decisions

- Discord delivery model: post to channel, DM, or both?
- "Today" view vs. "week" view — separate skill invocations or flags?
- Capabilities-shipped: human-written or LLM-extracted from PR descriptions?
- Backfill strategy for "vs. last year" comparison.
- Whether to invest in the optional shareability path (spec §14) in V2 or defer indefinitely.
