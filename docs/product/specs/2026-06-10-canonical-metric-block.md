# Canonical metric block — spec

**Status:** active · **Owner:** weekly-review · **Adopted:** 2026-06-10

> **Read with [METHOD.md](../../../METHOD.md) (2026-09-18).** METHOD.md is the plain-language definition of every published number and wins where the two differ. In particular: hands-on is presence blocks, not the per-message model this spec's "hands-on hours" text describes; weekly totals of hands-on are sums of per-unit unions with the week-wide union printed as a check, not the simple summation at every scope this spec describes; and the multiplier is published as a pooled figure and a per-unit median with their populations, not as one leverage number.

Every weekly-review artifact (epic worksheet, digest, client presentation, future Slack/Discord drops) MUST present the 7 effort metrics below in the order, names, and definitions specified here. The same definitions apply at every scope (per-task, per-epic, per-week, per-window) — they aggregate by simple summation; the two derived ratios recompute from the summed totals.

This spec replaces ad-hoc "leverage" / "speedup" / "active hours" labels that drifted across earlier outputs. Anything new that emits one of these numbers MUST source it from `task_metrics` (see `src/weekly_review/persist.py`) and render via `_fmt_metric_block` in `scripts/make_label_worksheet.py`.

**Renamed 2026-09-18 (method Step 7, run db schema v2).** Metric 3 below is now called **`labor_hours`** (user ∪ agent, pauses removed — the definition is unchanged) and metric 5 is **`agent_lane_hours`**. **`wall_clock_hours` is calendar time**: 168 for a week; per board task, first agent work on it (in the window) to its done transition, from the store's own transitions; per unit, first agent work to last event. **Wall-clock speedup = (`est_h` / 8) ÷ (`wall_clock_hours` / 24)**, every calendar day counted, no weekend adjustment (the user, 2026-09-17); for a week that is `est_h` / 56. The user's 2026-09-15 ruling against dividing by a summed span still holds — the new denominator is calendar time, which cannot be summed past the window. The digest's "Calendar time and speedup" section (`src/weekly_review/calendar_time.py`, `digest._calendar_section`) is the renderer for the calendar figures; `_fmt_metric_block` keeps the labor-based lines under their new names ("Σ per-task labor_hours", "median expert ÷ labor"). Run dbs written before v2 hold labor in the column called `wall_clock_hours`; `persist.metric_columns()` is the one place that reads them under the old meaning. The text below predates the rename and uses the old names where it says "wall-clock hours" and "agent labor".

**Known non-conformance (as of 2026-07-12): `src/weekly_review/digest.py`.** The digest predates this spec and renders its own effort lines (`Σ agent active hours`, `leverage_agent`) off the legacy `agent_active_hours` column instead of calling `_fmt_metric_block`. It therefore shows neither agent labor nor the canonical metric set, and its numbers will not reconcile against the worksheet for the same window. This is recorded here rather than papered over; routing the digest through the canonical renderer is the fix, and it is not done yet.

## The seven metrics, in order

1. **expert hours** (`est_h`, `est_human_hours`)
   *METR-50% expert baseline.* Hours at which an expert in the relevant domain would succeed at this work with ~50% reliability. Derived from the EFFORT_BREAKDOWN countables (see complexity-criteria rubric below). **No domain-familiarity multiplier** — the user's friction is captured downstream in the leverage ratio, not the numerator.

2. **hands-on hours** (`active_hours`)
   The user's modeled active time per user message: agent's prior output read at 300 WPM + the user's typed reply at 40 WPM, capped at 20 min/turn. Captures steering + review + cleanup. Conservative on purpose — the user skims long output, so the model overstates attention, which makes hands-on leverage a CONSERVATIVE number, not an inflated one.

3. **wall-clock hours** (`wall_clock_hours`)
   Sweep-line union of the user and agent intervals, overlap collapsed.
   - User interval: the modeled reading+typing window ending at each `user_message` timestamp.
   - Agent interval: agent-event burst, stitched with ≤30-minute gaps while a tool call is running and ≤15-minute gaps otherwise (see below), hard-capped at 4 hours per burst (so a runaway overnight tool doesn't blow up the number). **A burst never bridges a gap its own worker spent on another task** — added 2026-09-15, after the stitch was found billing a lane that interleaves twenty tasks for the time it spent on the other nineteen. Same rule the hands-on block partition already applies: a key switch cuts the block. The worker is `(session, agent_lane)`, and only that lane's own events can cut it; a sibling agent's work does not, because this metric merges every lane into one timeline by design.

     **The 30 minutes is two tolerances, and only one of them has evidence behind it** (split 2026-09-15, `AGENT_BURST_STITCH_SEC` / `AGENT_QUIET_STITCH_SEC`). A gap that opens at a `tool_call` and closes at its `tool_result` is a tool running — the worker is provably busy, and that is the case the generous value was written for. Every other gap is quiet: the model emitting, which takes seconds, or the session waiting to be woken, which takes as long as it takes. Bridging quiet asserts work with no evidence of work, and the shape that makes it concrete is a scheduled loop that wakes every 20–45 minutes, says "Waiting.", and sleeps — it emits, so neither the lane cut nor any silence test can see it. On the week of 2026-09-07, 267 stretches holding no tool call at all are worth 6.0h of agent time, one task being 2.81h of its 3.19h; on 2026-08-17, 3.5h; on 2026-04-27, 0.2h.

     **The quiet number is a modelling choice, not a measurement, and the data has no knee to read it off.** Of 144,795 pending-tool-call gaps across the three era weeks, 11 exceed 15 minutes; quiet gaps decay smoothly from seconds to hours with no break anywhere. Sweeping it alone (agent hours, 2026-04-27 / 08-17 / 09-07): 15min −19.6% / −10.0% / −8.9%, 10min −30.4% / −14.7% / −12.3%, 5min −43.0% / −20.3% / −16.3%. It is **not** a level knob — a sparse week moves several times more than a dense one, so lowering it tilts the Apr–Sep trend the opposite way from the task-switch cut above. Both tolerances print on every run.

     **The user set the quiet tolerance to 15 minutes on 2026-09-15**, the gentlest setting that still removes the polling loops. Every agent-hours, wall-clock and agent-labor figure published before that date is therefore hot: on the three era weeks agent hours fall 63.8h → 51.4h, 252.8h → 227.5h and 328.4h → 299.3h. No task reaches zero and **no task rises**, which is the invariant worth checking — a tolerance that can only shorten a burst must never lengthen one. It did at first: the 4h cap is per burst, so a split burst was collecting a second allowance and 12 fan-out tasks gained 24.7h. The cap envelope is now stitched at the tool-call tolerance with no cuts applied, and both cuts intersect into it, so each is a subset of the old answer by construction. That also means the sweep table above was re-measured — the figures the ruling was made against read −5.8% / −3.6% on the two dense weeks and were understated by the same artifact.
   - Union: time during which SOMETHING was happening on this work.

4. **agent hours** (`agent_busy_hours`)
   Sum of agent intervals (same definition as in metric 3 — tool_call→tool_result latency INCLUDED). Reads as "time the agent was actively driving on my behalf." This is the measure that captures the user's lived "agents ran for hours while I was away" experience; the older `agent_active_hours` column (5-min stitching, emission-only) is preserved for backward compatibility but should NOT be used in new output.

5. **agent labor hours** (`agent_labor_hours`) — *added 2026-07-12*
   Same burst definition as metric 4, but computed **per agent lane and then summed**, where metric 4 merges every agent's events into one timeline. A lane is one transcript: the main loop plus every dispatched agent — Agent-tool subagents, Workflow agents, in-process team members — each of which writes its own `<session>/subagents/agent-*.jsonl`.

   The distinction only shows up under fan-out. Sixteen workflow agents running concurrently for 10 minutes are ~10 agent-minutes of **agent hours** (elapsed: one thing was happening for 10 minutes) and ~160 agent-minutes of **agent labor hours** (labor: sixteen agents did ten minutes of work each). Measured on real July data: 2.0× in the heaviest fan-out projects, 1.0× where work was sequential.

   **A blocked orchestrator is not a worker.** The dispatching parent is itself a lane, and it sits idle while its children run. Its labor therefore excludes any window a child of the same session was covering — otherwise 16 fanned-out agents would report as 17, and the inflation would compound with nesting. Blocked time is detected from actual child-lane activity, not from tool names, so it covers Task, Workflow and team dispatches identically. (the user's call, 2026-07-12.)

   Render it with the **parallelism factor** (`agent labor ÷ agent hours`) attached, because the ratio is the informative part — the raw hours are hard to interpret alone. Read it as *average concurrent agents over the busy window*. It can sit slightly below 1.0× when the elapsed window contains time nothing was working; that's honest, not a bug. Agent hours remains the elapsed-time metric; agent labor never replaces it.

6. **wall-clock speedup** = `expert hours ÷ wall-clock hours`, **per task only**
   How much faster the work happened than an expert doing it solo.

   **Never compute this at epic or week scope by dividing expert hours by a summed wall clock.** Tasks genuinely run at the same time, so adding their spans counts one clock minute once per live task: on two of the three era weeks the sum came to **246.18h and 329.47h inside 168-hour weeks**, which falsifies the metric with no model and no threshold. At those scopes report the **median of the member tasks' own speedups** instead (`_task_speedups` + `_median` in `scripts/make_label_worksheet.py`), which is sound because each task's own wall clock is a correct union. Tasks with no measured wall clock are dropped rather than scored infinite, and when none remain the line is omitted rather than printed as a placeholder.

   The user's ruling on a review item, 2026-09-15: *"Stop printing aggregate speedup for weeks and projects. We can consider median speedup across tasks for a week. Or across tasks by project."* He chose this over computing the aggregate as a union, which would have been arithmetically correct but would have roughly doubled a lead metric (whole-week 3.63x → 5.72x, 3.77x → 9.21x, 2.62x → 7.07x) — the direction he has said he would rather not err in.

   The summed hours are still printed, renamed **Σ per-task wall clock**, because they are a real quantity: wall-clock *labor*, the same distinction this spec already draws between `agent_labor_hours` (per-lane, summed) and `agent_hours` (merged, elapsed). Only the ratio built on them is gone.

7. **hands-on leverage** = `expert hours ÷ hands-on hours`
   How much expert output the user got per hour of his keyboard attention.

**Neither ratio has agent hours or agent labor in it, and that is deliberate.** Fan-out already pays off in the numerator — more agents produce more expert-hours of output without touching the user's keyboard time — so crediting it again in a denominator would double-count it. Agent labor is a descriptive companion: it says *how* the work got done, not how much it was worth.

## Rendering rules

| Scope | What to show |
|---|---|
| **Per-task** (in epic detail rows) | 3 metrics inline: `expert / hands-on / wall-clock`. Use `Xh expert / Y.YYh hands-on / Z.ZZh wall-clock`. Do NOT compute per-task leverage here — sums-of-tiny-denominators explode meaninglessly. |
| **Per-epic** (epic header) | Full metric block via `_fmt_metric_block`. Each on its own bulleted line, in the order above. |
| **Per-week / per-window** (top of doc) | Full metric block via `_fmt_metric_block`. Same format as per-epic. |

Use `—` for the speedup/leverage ratios when the denominator is 0; use `∞` when est_h > 0 and denominator is 0 (means: agent shipped without measurable human time, and we don't yet have wall-clock attribution either — typically a Slack-dispatched product-engineer PR whose transcripts aren't joined).

## Methodology footer (required)

Every published artifact MUST include a "Methodology — what each metric means" footer that quotes the definitions above verbatim. Readers should never have to guess what a column means. The worksheet template in `make_label_worksheet.py` emits this footer automatically; copy-paste it into any new output.

## Complexity-criteria rubric (the source of expert hours)

The expert-hours numerator must be derived from countables, not guessed. The EFFORT_BREAKDOWN field in the enrichment output uses the labels below; same labels appear in any future estimation prompt or human override flow.

### Code work
- `[new components: N]` — net-new modules / services / plugins
- `[modified components: N]` — touched-but-not-net-new components
- `[new integrations/APIs/packages: N]` — newly adopted third-party or cross-system contracts
- `[LoC added: N]` / `[LoC removed: N]` — from PR metadata when available
- `[test files added: N]` / `[test lines added: N]` — coverage delta as a proxy for test investment

### Research work
- `[sources reviewed: N]` — papers, blog posts, websites read
- `[pages read: N]` — 1 page ≈ 500 words
- `[pages written: N]` — output, same word convention

### UX design
- `[new pages/components designed: N]`
- `[modified pages/components: N]`
- `[variants explored per screen: N]`

### Documentation
- `[pages written: N]`
- `[files touched: N]`

### Ops
- `[distinct config/infra changes: N]`
- `[files touched: N]`

After the countables, a short clause naming the SHIPPED ARTIFACT or DECISION. "~N" allowed when uncertain; never fabricate. See `src/weekly_review/enrichment.py` step 3 for the exact prompt.

## Known limits (state these whenever the numbers are presented externally)

- **Hands-on = 0 with high est_h** is usually a task anchored to a PR/commit whose conversation lived in an R2 product-engineer transcript that isn't joined to the PR event. Don't read this as "agent shipped 80h of work with zero human input" — read it as "the conversation that produced this is not in this dataset."
- **Wall-clock and agent hours clip at 4h per burst.** A genuinely 8-hour overnight build registers as 4h. Rare but real. Agent labor clips per lane, so a fan-out of long-running agents clips once per agent. The cap is per burst, so the task-switch cut above is applied *inside* the capped stitch rather than instead of it — otherwise splitting one burst into three would hand out three 4h allowances and the cut would manufacture hours (measured: +14.5h on 2026-08-17 and +26.7h on 2026-09-07 before this was fixed, one task reading 11.64h where the cap says 4h).
- **Agent labor is 0 on runs predating 2026-07-12.** Those runs have no `agent_lane` on their events and no `agent_labor_hours` column. `_fmt_metric_block` OMITS the line rather than printing 0, precisely so a missing measurement never reads as "this week had no fan-out." If you see a 0 in a re-derived table, it means not-measured.
- **A commit with no session to witness it is not counted at all** (the user's ruling, 2026-09-15). A scheduled job that commits its own output, and a routine run on a machine whose transcripts this tool cannot read, both leave a commit and nothing else: no turn, no board action, no review, within half an hour either side. There is no evidence of anyone working, so the commit becomes no measured task rather than a zero-wall-clock one — three of them on the week of 2026-09-07, including a 27-file archive refresh authored at 08:00 on a Sunday. **The limitation this states is that work done outside the harness is invisible here, not absent**: the diff is real, and nothing in this dataset can size it. Every run prints the count and the projects it dropped (`commits with no session within 30 min, not counted as work:`) — read it before reading a project's totals, because a project whose work lives in a scheduled job will read light.
  - **Which sessions count as the witness depends on the project, and that is the part to understand before quoting the count.** Reading the whole fleet makes the test almost vacuous: about twenty sessions run at once here, so something unrelated is nearly always inside the half hour. The same daily digest job ran six days on the week of 2026-04-27 and five were dropped — the sixth survived only because another project happened to be busy that morning. So where a project has been seen committing beside its *own* sessions, its own sessions are the witness, and a session in some other project no longer rescues its cron commits.
  - **Where a project has never been seen doing that, the fleet-wide window decides and the commit stays.** `assistant-b` on the week of 2026-08-17 is ten commits and no other event at all — real work whose sessions route to another slug, not a scheduled job. A project that cannot be observed committing next to its own work cannot be read for silence either, so it keeps the benefit of the doubt. **A project appearing in the dropped-commits line with a large share of its commits is more likely a routing hole than a cron job** — check `projects.yaml` before believing the week was quiet.
- **A task that spans weeks counts whole in the week it started** (the user's ruling, 2026-09-15). Measured over Apr 13 – Sep 13 (2,259 tasks, Σest 6,472.3h): 148 tasks (6.6%) start in one week and end in another, and they carry 1,680.2h — **26.0% of all estimated hours**. A pooled figure over the whole range is immune, because a task landing in the wrong week inside one bucket moves nothing. A **weekly or monthly series is not**: splitting each spanning task across the weeks it touches, by share of elapsed span, would move 870.7h (13.5% of all est) out of its first week. The reason this is a documented limit rather than a fix is concentration — **75.8% of those hours are `claude-live-feedback-plugin`** (62 tasks, 1,274.0h), which ships in multi-week pushes, and all twelve of the largest spanning tasks are its. So: state it alongside any weekly or monthly series, and expect that project's rows to be lumpy. A distortion living in one project is a caveat; the same distortion spread evenly would have been a defect, which is why the per-project split is the number that decided this and the 26% alone is not.
- **300-WPM reading model** overstates skim-heavy reading. Hands-on leverage is therefore conservative.
