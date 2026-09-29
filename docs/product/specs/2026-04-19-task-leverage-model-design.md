# Task-Leverage Model — Design Spec

**Status:** draft • **Date:** 2026-04-19 • **Author:** The user (with AI assistance)
**Supersedes:** the informal "weekly digest" scope in `docs/product/vision.md` and `.claude/skills/weekly-review/SKILL.md`. Vision remains the north-star; this spec tightens the data model and the primary metric.

> **Superseded in part (2026-09-18).** The current definitions are in [METHOD.md](../../../METHOD.md). Where this spec differs it is out of date: hands-on is measured as presence blocks (10 min / 60 s tolerances), not reading-and-typing word counts; work is split into priced units by artifact and lane, not by topic-bounded conversation segments; coverage has no 95% target; `wall_clock_hours` is calendar time. The goal section and the list of what is not measured still stand.

## 1. Goal

Build a personal, recurring feedback loop that measures how much useful output a solo developer + AI agent team produces per unit of the developer's hands-on time, so the developer can change how they work with agents **this week** based on what happened **last week or last month**.

This is also meant to help measure output over time in a consistent manner, and understand if it's getting better.  And to try to combat perception bias from the developer themselves (a known, significant issue)

Not an academic study. Not an organizational productivity tool. A personal instrument whose audience is the data subject.

## 2. Primary metric

```
leverage_user(period) = Σ est_human_hours(task active in period) / Σ bryan_active_hours(period)
leverage_agent(period) = Σ est_human_hours(task active in period) / Σ agent_active_hours(period)
```

- **Numerator — \****`est_human_hours`** — for each task, an estimate of how long a competent domain-expert human would take to produce the same output, doing the task as simply as reasonable. Methodology from METR time-horizons ([arXiv:2503.14499](https://arxiv.org/abs/2503.14499)). Estimated once per task; re-estimated if the task scope materially changes.
- Each task carries a `bryan_domain_expertise ∈ {expert, competent, learning}` tag, inferred by the estimator from (a) prior tasks in the same domain in the user's history, and (b) a small seed list of the user's self-declared expertise domains in `~/.config/weekly-review/expertise.yaml`. This lets the digest answer "what kinds of tasks can the user pursue?" — tasks where the ratio stays high in domains where the user is *not* a domain expert are the strongest signal that the agent team is expanding what's possible.
- **Denominator — \****`bryan_active_hours`** — estimated user time at keyboard, derived from user messages: `words_of_prior_agent_output / 300 wpm + words_user_typed / 40 wpm`, capped at ~20 min per message to exclude pauses. Calibrated against Timely where available. This is NOT total cognitive effort — it is "active-in-conversation time." A companion "cognitive wall-clock" metric (§7) keeps us honest about how much of each hour is actually going to thinking.
- **`agent_active_hours`** — wall-clock sum of tool-call + generation spans within a session, excluding idle gaps > 5 min.

### Task definition

A **task** is one topic within scope of the original request, including all iteration (bug fixes, polish, rework) until the topic is resolved or abandoned. Tasks can span days, weeks, and sessions. Multiple tasks can run interleaved.

Split boundaries are low-stakes: if a task gets incorrectly split into two, `est_human_hours` splits proportionally and the numerator sum for the period is roughly preserved. The segmentation errors we care about are *merges* (two real topics collapsed into one) because they inflate `est_human_hours` on a single "task" and distort the per-task leverage view. The review tool is designed around making merge/split easy.

### Why a ratio and not a single input

We keep **numerator, denominator, and ratio visible side-by-side** in the digest. Single-ratio dashboards get gamed and trick the user into local-optima decisions (SPACE framework, [Forsgren et al. 2021](https://queue.acm.org/detail.cfm?id=3454124); quantified-self retrospectives, [Personal Informatics](https://personalinformatics.org/)). At N=1 with the user as the only consumer, gaming is self-defeating, but Goodhart drift still happens. Always show the components.

For quality of analysis, we should try and make sure that tasks cover at least 95% of the work done with agents in each given time period of analysis (e.g. a week, a month, etc.)

## 3. What's different from prior art

See `ATTRIBUTIONS.md` for sources. Key differences from the commercial and academic landscape:

1. **N=1 with the data subject as the audience.** Commercial tools (DX, Jellyfish, Swarmia, LinearB) deliberately avoid effort-ratio metrics because at team scale, the denominator gets gamed. At N=1, gaming is self-defeating, unlocking a metric category the industry walked away from for org-politics reasons, not methodology reasons.
2. **Recurring personal feedback loop, not a one-off study.** METR and Microsoft/MIT run RCTs; DX runs quarterly surveys. We run on-demand and weekly, and the output changes how the user works *next week*.
3. **Retroactive reconstruction from existing Claude Code JSONL.** ~4 months of transcripts on the user's machines already contain most of the evidence. We don't need forward-deployed instrumentation. ccusage / CodeBurn read transcripts for different purposes (cost / activity classification); neither is in a process-improvement loop.
4. **Task = topic-bounded across sessions and projects.** Every surveyed tool uses PR, session, ticket, sprint, or time window as the implicit segmentation. None segment by how work is actually experienced: topics that span dormancy gaps and interleave with 2-3 other topics at once.
5. **Agent as teammate, not as assist to an individual.** We measure the joint user + agents system and track two distinct resource prices (user time scarce, agent time cheap-but-not-free). Commercial tools model the agent as an assist *to* an individual; the teammate framing doesn't exist in their schemas.
6. **Calibration loop is the product.** Review-tool corrections become few-shot examples for the next run's segmenter and estimator. The instrument learns the user's taste over months. This is closer to Good Judgment Project calibration ([Tetlock](https://www.goodjudgment.com/)) than to productivity SaaS.

## 4. Event model

One normalized event stream per project per week, stored as JSONL. Events are **regenerable** from source (transcripts, git, GitHub), not immutable — when we improve extraction or change the schema, we re-run and overwrite. Each run is tagged with a `run_id` and `schema_version`; historical runs are kept under `runs/<run_id>/` so we can diff whether a new extractor actually produces a better stream.

Fields:

```
{
  "ts": "ISO8601",                    # event timestamp
  "actor": "user" | "agent" | "artifact",
  "project": "<registry-slug>",       # derived from cwd via projects.yaml
  "session_id": "<source-specific>",  # Claude transcript session, or equivalent
  "source_system": "claude_transcript" | "github" | "linear" | "git" | "notion" | "timely",
  "source_machine": "mac_mini" | "laptop" | "server" | ...,
  "kind": "user_message" | "agent_message" | "tool_call" | "pr_opened" | "pr_merged" | "commit" | "ticket_moved" | "notion_published" | ...,
  "content_snippet": "<first 500 chars, redacted as needed>",
  "metadata": { ... kind-specific ... },
  "seq": <int within session>         # for dedup + ordering
}
```

Dedup key within a run: `(source_system, session_id, seq)`. User corrections (merges/splits/relabels) live in `~/.config/weekly-review/overrides.yaml` and apply on top of any regenerated stream — they survive re-extraction.

**Run comparison.** `weekly-review --diff-runs <old> <new>` reports what changed between runs (tasks appeared/disappeared, `est_human_hours` shifted, coverage % changed). This is how we decide whether a new segmenter or estimator version is actually better than the old one before making it the default.

Artifact events (`pr_merged`, `notion_published`, etc.) are first-class — they don't define task boundaries, but they're strong "task reached a milestone" signals the segmenter and estimator both consume.

## 5. Ingestion

### Transcript geography

The user's Claude Code transcripts are scattered across machines and tools because the workflow has evolved:

- **Recent 2 weeks (early-mid April 2026):** remote Claude setup; almost all activity captured on the Mac Mini in `~/.claude/projects/`.
- **March:** Mac Mini arrived mid-March, heavy use from ~1 week later. Significant product-engineer agent infrastructure work with transcripts stored in **Cloudflare R2** and backed up on the Mac Mini.
- **February:** Claude Code via Conductor.build on the MacBook (which saves standard Claude Code JSONL).
- **January:** Claude Code CLI directly on the MacBook.

We start narrow and only widen if we need more data:

1. **Start: Mac Mini, last 2 weeks.** Enough to sanity-check the pipeline and iterate on segmentation with real data.
2. **Extend to Cloudflare R2** for the March product-engineer work.
3. **Extend to the MacBook** for January-February.

Each extension is a config change (a new extractor source), not a new version of the tool.

### Sources

- **Claude Code transcripts (primary).** Parse `~/.claude/projects/*/*.jsonl` on the Mac Mini; pull from Cloudflare R2 and the MacBook's equivalent path as we extend scope. Derive project via a `projects.yaml` registry that maps cwd patterns to project slugs (worktrees normalized to the root repo).
- **Git log** per registered repo. Contributes `commit` and `doc_committed` events.
- **GitHub** via `gh` CLI. Contributes `pr_opened` / `pr_merged` / `review_requested` events.

**Enrichers that plug in as they prove useful** (not required to ship V1): Linear MCP, Asana MCP, Notion MCP (`notion_published` and comment events), Google Calendar, Timely. Each is a plug-in extractor emitting the unified schema. Missing extractors degrade gracefully — the metric computes over whatever events exist.

### Cross-machine mechanics

Each event tags `source_machine`. Dedup is machine-agnostic via `(source_system, session_id, seq)`. When we extend to the MacBook:

```
rsync -a laptop:~/.claude/projects/ ~/.local/share/weekly-review/transcripts/laptop/
```

Cloudflare R2 extraction uses `rclone` or the `aws s3` CLI against the R2 bucket; credentials via existing config.

### Relationship to ccusage and our own existing scripts

The existing `analyze_transcript.py` in `ai-project-support/.claude/skills/retro/scripts/` and `work-review.ts` in `ai-project-support/docs/process/work-review/` already parse Claude Code JSONL and produce per-session + per-week aggregates that we've validated on the user's setup. We'll **port those into this repo** rather than borrow code from ccusage. ccusage remains a reference point for community-landed conventions (session boundary detection, cost-per-project rollups), and we cite it in `ATTRIBUTIONS.md`, but copying from it isn't worthwhile when our internal scripts already cover the same ground and we know they work.

## 6. Task segmentation

The hard problem. Two-layer approach, inspectable at every step.

### Layer 1 — deterministic pre-segmentation

Per project, walk events in chronological order. For each **user message**, classify against currently-open tasks in that project using rules:

1. **Explicit new-task cues in the message text:** `"new topic:"`, `"different thing:"`, `"actually, let's (instead|switch|do)"`, `"pivot to"`, bare issue/PR number prefix (`#123`, `BRY-45`).
2. **Issue/PR/ticket reference match:** if the message references an identifier already attached to an open task, extend that task. If it references a new identifier, start a new task anchored to it.
3. **Artifact reference:** if the message references a PR, commit, or Notion page whose event is attached to an existing task, extend that task.
4. **Gap threshold:** if no events on this project for > 14 days AND no identifier match, candidate new task. (Parameter, tunable.)

Each assignment records `confidence ∈ {high, medium, low}` and `rationale`. Low-confidence assignments escalate to Layer 2. Tool-use patterns within a task window (extending CodeBurn's approach, MIT-licensed — see `ATTRIBUTIONS.md`) label sub-activities (`coding`, `research`, `debugging`, `review`, `writing`, etc.) as metadata on the task, not as separate tasks.

### Layer 2 — LLM at ambiguity

For user messages where Layer 1 is low-confidence, a short LLM call gets:
- Titles + 1-line summaries of currently open tasks in the project (max ~8)
- The user message in full
- Last 2 events of each candidate task

Returns: `{extend: task_id | "new", confidence, rationale, suggested_title_if_new}`. Model: Haiku 4.5 (cheap, high volume).

### Layer 3 — overrides

`~/.config/weekly-review/overrides.yaml` is authoritative. Any merge / split / relabel from the review tool (§10) writes here. Subsequent runs honor overrides as ground truth and use them as few-shot anchors for Layer 2 prompts.

### Task lifecycle

- `open` — events arriving, no milestone reached
- `shipped` — an artifact event of the right kind attached (merged PR for code tasks; published Notion page or committed doc for research/docs tasks; ticket `done` transition for ops tasks)
- `abandoned` — no events for > 30 days AND not shipped. Abandoned tasks still contribute to the period they were active in.
- Iteration after `shipped` reopens the task (returns to `open`). This is how rework is absorbed into the same task per the goal framing.

## 7. Effort attribution

For each user message in a task:

```
reading_time = words_of_preceding_agent_output / 300      # words per minute
typing_time  = words_in_user_message / 40
active_time  = min(reading_time + typing_time, 20 min)
```

Default WPM constants are tunable per-user via `~/.config/weekly-review/effort.yaml`. Where Timely overlaps the same time window, auto-calibrate the constants to minimize disagreement (linear regression in a config-refresh step, not at query time).

### What this is and isn't

**`bryan_active_hours`**** measures active-in-conversation time.** It does NOT capture:
- Planning, reading output after the agent runs, verification, or mental flow-state tax
- Work done outside the conversation (solo coding, reading, meetings)

Labels in the digest call it **"active time"**, not "effort," to keep the distinction honest. No taxes, no multipliers, no dormancy adjustments — the number stays traceable back to specific user messages.

### Cognitive wall-clock time (companion metric)

`bryan_active_hours` is a lower bound on cognitive effort. A useful upper bound: when the user is actively working, total cognitive time ≈ total wall-clock time in the active span. He can't think faster than time moves. And he's disciplined about turning off when not working — so if there's been no LLM activity for 15 minutes, assume he's with family / on a bike ride / otherwise off.

Operationally: `cognitive_wall_clock(period)` = sum of durations of "active spans," where an active span is a continuous stretch of LLM activity (user messages or agent actions) where no gap exceeds 15 minutes. The `active_hours / cognitive_wall_clock` ratio is surfaced in the digest as a sanity check. If it's way below 1.0 over time, either the WPM constants need tuning or the active-time formula is missing real effort we should fold in.

### Context-switch count (no tax, no adjustment)

We count context switches — user messages that change the active project from the previous user message on the same day — and report the count per day and per week. That's it. No penalty is applied to `bryan_active_hours`.

Secondary goal (see §1): reduce context switches per day while maintaining or improving leverage. The workflow lever we already started pulling: ask agents to do as much independent work as possible, batch feedback requests to the latest responsible moment, then unblock everything at once.

Research context for why we care ([Parnin & Rugaber 2011](https://link.springer.com/article/10.1007/s11219-010-9104-9), [Mark et al. 2008](https://ics.uci.edu/~gmark/chi08-mark.pdf)) is cited in `ATTRIBUTIONS.md`. We don't encode their averages into our numbers — N=1 warrants tracking the raw count and letting the user decide what to do with it.

### Resumed tasks (flag, not adjustment)

Tasks with gaps > 24 hours between events get a `resumption_events_count` field — how many times the task was paused and resumed. Surfaced in task detail and the digest for awareness ("this task had 4 resumptions"), not used as a time adjustment. Subjective and task-dependent; population averages don't belong in N=1 analysis.

### Agent-blocked time

Time between the agent's last output and the user's next message, capped at 8h to exclude overnight, stored as `agent_blocked_ms` on the user-message event. Not part of the primary metric; surfaced in the "blocked-on-you" section of the digest so the user can see where he's the bottleneck for the agent team.

## 8. Output estimation (`est_human_hours`)

Per task, produced when the task reaches `shipped` status (or periodically re-estimated for long-running `open` tasks).

### Rubric (first draft — will evolve)

A task's human-hours estimate is produced by an LLM pass with structured input:

- **Task title** (from Layer 1/2 segmentation or override)
- **Summarized user messages** on the task (compressed via a separate cheaper pass)
- **Artifact list** with metadata: PR additions/deletions/files; Notion page word count; committed doc word count; external sources cited; number of distinct systems touched
- **Rubric dimensions**:
  - *Scope:* single-file change / single-feature / cross-repo / multi-source research
  - *Depth:* surface edit / careful design / novel problem
  - *Type:* code / research / documentation / UX design / ops
  - *Novelty for user:* routine / learning-required / genuinely new
- **5-10 anchored examples** from the user's own recent history (`~/.config/weekly-review/anchors.yaml`), with phrases like "this research note shipped last month took a human ~4h"
- **Calibration target:** the METR time-horizons framing — "hours for a competent domain expert to succeed at this with ~50% reliability, doing it as simply as reasonable" — used as a *shape* for estimates, not as a ground-truth anchor. Known limitations we accept:
  - METR's time-horizon methodology measures well-defined technical/reasoning tasks, not messy real-world work ([METR "Clarifying Limitations," Jan 2026](https://metr.org/blog/2026-1-29-time-horizon-1-1/)).
  - Benchmark velocity is not production velocity ([METR "Many SWE-bench-Passing PRs Would Not Be Merged," Mar 2026](https://metr.org/notes/2026-03-10-many-swe-bench-passing-prs-would-not-be-merged-into-main/)).
  - Estimates are sensitive to the tooling generation the estimator implicitly assumes; the recalibration cadence (below) exists so the LLM's prior doesn't drift away from what your current agents can actually do.

Output: `{est_human_hours: float, rationale: str, dimensions: {...}, uncertainty: "low" | "medium" | "high"}`. Model: Sonnet 4.6 (judgment matters more than volume).

### What our leverage ratio is *not*

Our `leverage_user = est_human_hours / bryan_active_hours` can be comfortably in the 5-20× range at N=1 without implying team throughput has 5-20×'d. Macro throughput data ([Reock Mar 2026: 9.97% PR throughput gain across 400 companies over 15 months](https://newsletter.getdx.com/p/ai-productivity-gains-are-10-not)) suggests aggregate team output has risen single-digit percent. Our metric explodes because the *denominator* (the user's active typing time) can shrink 5-10× while the numerator (expert-equivalent output of the joint human+agent system) shrinks much less. High leverage here means "the agent team lets me produce the same shipped output using much less of my keyboard time," not "I'm shipping 10× more software." Don't conflate the two. Reock's 10% is the right answer to "how much more does the team produce"; leverage is the right answer to "how much of the user's time did it take to produce it."

### Re-estimation and idempotency

Estimates are cached per `(task_id, anchor_set_version)`. Re-running without override changes reuses the cache. Adding an anchor or a manual override invalidates the cache for affected tasks. Full refresh is a CLI command (`--refresh-estimates`).

### Recalibration cadence

**Initial training pass** (required before the first "real" digest is trusted): The user labels ~5-10 recent shipped tasks with his own human-hours estimate. Those become the starting anchor set and seed a calibration curve (systematic bias correction) that's applied to the LLM's raw outputs.

**Biweekly (or more often) recalibration:** every 2 weeks, the user is prompted to review a random sample of newly-shipped tasks and adjust the human-hours estimate. Adjustments extend the anchor set and refit the calibration curve. This is how the rubric stays honest as work, stack, and agent skill evolve.

Recalibration is a first-class feature, not future work. `weekly-review --recalibrate` runs the flow on demand; the digest surfaces a "last calibrated: N days ago" line to nudge the user when it's due.

### Jørgensen's warning

Expert judgment beats formal models when experts are domain-familiar ([Jørgensen 2004](https://www.simula.no/publications/review-studies-expert-estimation-software-development-effort)). Implication: the anchored examples from the user's own history are the primary accuracy lever; the rubric dimensions are secondary. Design the review tool to make it trivial to promote a task into the anchor set.

## 9. Counter-signals (so leverage isn't the only number that matters)

Presented alongside leverage in the digest, without being collapsed into a single score:

- **Rework cost per task** — post-`shipped` events on a task. Inspired by [GitClear's churn analysis](https://www.gitclear.com/ai_assistant_code_quality_2025_research); high post-ship activity on a task lowers its leverage naturally, but surfacing it separately lets the user see "this task looked good but I spent 4 more hours on it after merge."
- **Context-switch density** — switches per day, per week. Already in the v0 prototype (`work-review.ts`); keep.
- **Time blocked on the user** — sum of `agent_blocked_ms` attributed to the user's response latency, per week.
- **Low-confidence estimates** — count of tasks whose `est_human_hours` has `uncertainty: "high"`. A digest with 40% high-uncertainty estimates means the leverage number is squishy.

## 10. Review & calibration (this pass: YAML; HTML later)

For this pass, review and calibration happen through YAML files + the CLI, not a web UI. An HTML view is a later upgrade if YAML-editing proves too painful in practice.

### Inputs the user edits

- **`~/.config/weekly-review/overrides.yaml`** — merge / split / relabel / ignore. Applied on every run. Example:
```yaml
  - merge: [task_abc123, task_def456]
    into_title: "Weekly-review spec + plan"
  - split: task_ghi789
    at_event_id: evt_2026-04-18T15:22
    new_title: "Rate-limit retries"
  - override_hours: task_jkl012
    est_human_hours: 6.5
    promote_to_anchor: true
```
- **`~/.config/weekly-review/anchors.yaml`** — labeled human-effort examples. Seeded manually at initial training; extended by the `--recalibrate` flow.
- **`~/.config/weekly-review/expertise.yaml`** — the user's self-declared expertise domains; feeds the `bryan_domain_expertise` classifier.

### CLI flows

- `weekly-review --inspect <period>` — prints a task-timeline view of events, segmentation rationale, estimator output, anchors used. Read-only inspection; no editing.
- `weekly-review --recalibrate` — picks a random sample of shipped tasks, walks the user through labeling them, writes to `anchors.yaml`.
- `weekly-review --diff-runs <old> <new>` — what changed between two extraction runs (§4).

### What the HTML upgrade would add (future)

Drag-drop merges, inline timeline editing, visual anchor browsing. Included in §13 (optional future), not blocking this pass.

## 11. Output artifacts

### Digest (markdown)

```
# Week of YYYY-MM-DD

## Leverage
- Tasks active this week: N
- Σ est human hours (output): X
- Σ your active hours: Y
- Σ cognitive wall-clock hours: C    (active/cognitive ratio: Y/C — sanity check)
- leverage_user: X/Y
- Σ agent active hours: A, leverage_agent: X/A
- By domain expertise: leverage on expert / competent / learning tasks
- Context: last 4 weeks averaged L_user, L_agent

## Coverage
- % of user-message events assigned to a task: P% (target ≥95%)
- Unassigned events: N (by project)
- If P < 95%: flag and suggest --inspect to review

## Top tasks by est human hours (what got done)
...

## Top tasks by your active hours (where your time went)
...

## Low-leverage tasks (candidates to review)
- Tasks whose L < 0.5 × weekly average — either poor estimates, real inefficiency, or deep-rework cycles
...

## Rework
- Tasks with significant post-ship activity
...

## Blocked on you
- PRs awaiting review (from GitHub)
- Agent-blocked hours attributed to your response latency

## Context switches
- Switches this week / avg per day / max in a day
- Most-interleaved task pair
- Trend vs. last 4 weeks (secondary goal: reduce without hurting leverage)

## Resumed tasks
- Tasks with ≥1 gap > 24h between events (flag only, no time adjustment)

## Open + low-confidence estimates
- Tasks whose est_human_hours has high uncertainty (flag for calibration review)
- Last calibrated: N days ago
```

### Raw JSON

Full machine-readable dump of the above for automation (conductor consumption, charting, future pooling).

### CLI

```
weekly-review                           # this week
weekly-review last-week
weekly-review --since 2026-04-05        # arbitrary range
weekly-review --inspect                 # opens review tool
weekly-review --refresh-estimates       # invalidate estimate cache
weekly-review --recalibrate             # run calibration flow
weekly-review --diff-runs <old> <new>   # compare two extraction runs
weekly-review --write path/to/file.md   # save digest
```

Skill entry point (`/weekly-review`) wraps the CLI with defaults and project-registry discovery.

## 12. Scope for this pass (1-2 days of work)

One target scope, shippable in 1-2 days. No V2/V3 plan. When this ships and the user has used it, we decide what to build next.

### Must ship

1. **Transcript ingestion — Mac Mini, last 2 weeks.** Port `analyze_transcript.py` and the relevant parts of `work-review.ts` into `scripts/parse_transcripts.py`. Emit events in the §4 schema into `runs/<run_id>/events/<project>/<iso-week>.jsonl`.
2. **Projects registry** at `~/.config/weekly-review/projects.yaml`: cwd-pattern → project-slug, worktree normalization.
3. **Git log + \****`gh pr list`** extractors for the registered projects, same event schema.
4. **Segmentation — Layer 1 rules + project-proximity fallback.** Layer 1 from §6.1 runs first. For messages Layer 1 can't place, fall back to "attach to most recent open task in same project within 7 days"; if no such task exists, start a new one. This achieves the ≥95% coverage requirement without needing an LLM classifier yet.
5. **Active-time formula** with default WPM constants. No Timely calibration yet.
6. **Cognitive wall-clock** computed from 15-min-gap rule (§7). Surfaced as sanity-check ratio.
7. **Context-switch count** per day and per week. No tax.
8. **`est_human_hours`**** — real LLM estimator.** Needed because the "combat perception bias" goal requires a defensible numerator. Sonnet 4.6 pass with the §8 rubric + an initial anchor set the user labels by hand (5-10 shipped tasks from the 2-week window). Uncertainty bucketed low/medium/high.
9. **`bryan_domain_expertise`**** tagging** per task, LLM-inferred with a seed list in `~/.config/weekly-review/expertise.yaml`.
10. **Coverage metric** in the digest. If <95%, flag and list unassigned-event counts per project.
11. **Markdown digest** over any date range, per §11. Include leverage, coverage, switches, cognitive ratio, domain-expertise breakdown, resumed-tasks flag.
12. **Overrides YAML** at `~/.config/weekly-review/overrides.yaml` — the user edits by hand for V1 (merge/split via YAML is painful but usable). Applied on every run.
13. **Recalibration flow** — `weekly-review --recalibrate` picks a random sample of shipped tasks and walks the user through labeling them. Feeds into anchors.

### Explicitly NOT in this pass (cut to fit 1-2 days)

- HTML review tool — YAML overrides are the V1 UX. HTML comes after we know the digest is trustworthy.
- Layer 2 LLM segmenter — the proximity fallback covers the coverage requirement; LLM segmentation is an upgrade only if we see misassignment in practice.
- Cloudflare R2 + MacBook transcript sources — added once the Mac-Mini-2-weeks pipeline is trusted.
- Linear / Asana / Notion / Calendar / Timely enrichers.
- Timely-based WPM auto-calibration.
- Conductor integration / Discord delivery.
- Optional shareability scaffolding (schema versioning is already in §4).

### Success criteria for this pass

- `weekly-review --since 2026-04-05` produces a digest the user reads and finds accurate enough to change how he works next week.
- Coverage ≥ 95% on the 2-week window.
- `active/cognitive` ratio is within a plausible range (0.3–1.0); if way off, we know what to fix.
- Initial anchor set (~5-10 tasks) labeled; initial calibration curve fit.
- The user has a clear list of "next-pass" improvements based on what the first digest got wrong.

## 13. Optional shareability (future)

The pooling-across-users idea: if multiple practitioners adopt the same framework, aggregate data gets statistical power that no single N=1 study produces. Not in scope for this pass, but these design choices preserve the option:

- **Versioned event schema** — `schema_version` field on every event; estimation rubric versioned.
- **Anonymization-friendly** — every metric can be computed from metadata alone, so a user can export derived data (tasks + estimates + hours, without content) if they choose to share.
- **Standardized rubric dimensions** — §8's dimensions are fixed; per-user anchors are additive.
- **Explicit opt-in, no default-on telemetry** — nothing leaves the machine without the user explicitly running an export command.

## 14. What this project is NOT

- Not a performance-review tool, even at N=1. The digest is for the user's own workflow decisions.
- Not a Claude-usage dashboard — that's ccusage's job, and our "agent cost" line is informational only, not productivity.
- Not a single-number scoreboard. Leverage is one signal among several; rework + switches + uncertainty are shown alongside it.
- Not a live timer or focus-mode tool (ActivityWatch / RescueTime territory).
- **Not claiming team-scale throughput gains.** See §8 — `leverage_user` measures output-per-active-hour at N=1, not throughput gain at team scale. [Reock Mar 2026](https://newsletter.getdx.com/p/ai-productivity-gains-are-10-not) is the right answer for the latter (~10% aggregate).

### N=1 gaming-resistance caveat

Our argument against the SPACE/Goodhart concerns — "gaming is self-defeating at N=1 with no external audience" — is logically coherent but **formal N=1 gaming-resistance research is scarce**. The closest published analogue is Tetlock's Good Judgment Project work on individual forecaster calibration (feedback loops improve single-person judgment). We rely on:

- Logical argument (self-measurement + no external stakeholder = no extrinsic incentive to inflate)
- The "no taxes or multipliers on primary metrics" rule (raw numbers stay traceable to specific events)
- Biweekly recalibration (drift detection)
- Review-tool overrides (correct bad estimates when spotted)

Real N=1 failure modes we don't formally guard against, flagged by the personal-informatics literature ([Li et al.](https://personalinformatics.org/)): identity-protective measurement bias (under-weighting weeks I remember as productive), motivation-driven overclaim (aspirational estimates creeping up), calibration drift during mood-state shifts. Recalibration cadence is the first line of defense; if these show up in practice, add structured drift-detection in a later pass.

## 15. Open questions

- **Worktree → project normalization.** Agree to collapse worktrees onto the root repo for project-attribution. Exceptions need to be called out in `projects.yaml`.
- **Notion page deletions.** If a published page is deleted, does its contribution to `est_human_hours` get removed from the period? Lean: yes, but retain a log entry.
- **Multi-user tasks.** If the user works on a task alongside another human (rare but happens), do we attribute the human-equivalent-hours differently? This pass: attribute all to the user; flag for future.
- **Meetings and calendar events.** Not in primary metric (not conversation events), but relevant to "where did time go." Possible future enricher.

## 16. Decisions to log

The following are captured in `docs/product/decisions.md`. Summarized here for spec-reader convenience.

1. **Repo private** (reverses earlier MIT-public decision). Already executed.
2. **Primary unit = task**, not week-project grid. Event stream aggregates into tasks first.
3. **Primary metric = leverage (****`est_human_hours / bryan_active_hours`****).** Numerator, denominator, and ratio always shown together. No funny adjustments to the raw numbers.
4. **Rework absorbed into same task** — no separate rework KPI.
5. **Context switches are counted, not taxed.** Secondary goal: reduce per-day count without hurting leverage.
6. **Resumed tasks are flagged, not penalized** — report the count of tasks with >24h gaps, no time adjustment.
7. **Estimate calibration is biweekly (or more often)**, with an initial training pass before the first trusted digest.
8. **Events are regenerable**, not immutable. Runs are versioned and diff-able.
9. **`bryan_domain_expertise`**** tag on each task** — expert / competent / learning — inferred from history + a seed config.
10. **Attribution practice.** All external code/ideas tracked in `ATTRIBUTIONS.md`; license checked before any copy.
11. **Human-effort rubric anchored to METR time-horizons** and primed with user-labeled anchors over a generic rubric.
12. **V1 uses YAML overrides, not an HTML review tool.** HTML comes later if YAML-editing proves too painful.

## 17. Risks

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| `est_human_hours` is too noisy per-task to be useful | Medium-High | Only aggregate numbers are headlined; per-task numbers flagged with uncertainty; biweekly recalibration. |
| Segmentation is wrong often enough to distort metrics | Medium | Coverage % check; YAML overrides let the user fix it; Layer 1 + proximity fallback is deterministic and auditable. |
| Active-time underestimates so badly the leverage ratio is meaningless | Medium | Cognitive wall-clock companion metric catches this; label is "active time" not "effort"; trends are valid even if absolute ratio isn't. |
| User stops doing the calibration step | Medium-High | Digest nudges "last calibrated N days ago"; minimal friction via YAML + `--recalibrate` CLI flow. |
| Coverage target <95% on real data | Medium | Project-proximity fallback explicitly designed to reach 95%; if it doesn't, next pass adds Layer 2 LLM classifier. |
| Privacy leak if repo re-opened publicly | Low with repo-private now | `ATTRIBUTIONS.md` tracks compliance; `content_snippet` is stored locally only; before any public push, a redaction pass. |

## 18. Prior art summary

Full details in `ATTRIBUTIONS.md`. Key sources:

- **METR time horizons** ([arXiv:2503.14499](https://arxiv.org/abs/2503.14499)) and the follow-ups that qualify the methodology: [Time Horizon 1.1 + "Clarifying Limitations," Jan 2026](https://metr.org/blog/2026-1-29-time-horizon-1-1/); ["Changing Our Developer Productivity Experiment Design," Feb 2026](https://metr.org/blog/2026-02-24-uplift-update/); ["Many SWE-bench-Passing PRs Would Not Be Merged," Mar 2026](https://metr.org/notes/2026-03-10-many-swe-bench-passing-prs-would-not-be-merged-into-main/); and the [19% slowdown RCT](https://arxiv.org/abs/2507.09089).
- **Reock, "AI productivity gains are 10%, not 10x"** ([DX newsletter, Mar 2026](https://newsletter.getdx.com/p/ai-productivity-gains-are-10-not)) — 400 companies, 15-month longitudinal, 9.97% PR throughput gain. The best macro-scale counterweight to vendor claims, and the sanity check for what team-scale AI gains actually look like in 2026.
- **DX AI Measurement Framework** ([getdx.com](https://getdx.com/research/measuring-ai-code-assistants-and-agents/)) — closest commercial cousin; survey + telemetry triangulation.
- **Toby Ord, "Are the Costs of AI Agents Rising Exponentially?"** ([tobyord.com](https://www.tobyord.com/writing/hourly-costs-for-ai-agents))
- **SPACE framework** ([ACM Queue 2021](https://queue.acm.org/detail.cfm?id=3454124))
- **Parnin & Rugaber 2011; Mark et al. 2008; Monsell 2003** (context-switch cost)
- **Jørgensen 2004** (expert estimation)
- **GitClear 2025** AI code quality research
- **ccusage (MIT)** and **CodeBurn (MIT)** — transcript parsing and tool-use-pattern classification
- **Internal:** `analyze_transcript.py` and `work-review.ts` from `ai-project-support`

See also `../../notes-repo/topics/measuring-ai-agent-impact.md` (a private notes repo) for an external-research compendium with a full DX publication timeline, DX vs METR comparison table, methodology critique, and independent-reproduction analysis — that file links back here for the user's own measurement stance.
