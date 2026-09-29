# Delivered hands-on hours — weeks of 2026-08-03 and 2026-08-10

Prepared 2026-08-28 for the fleet token-efficiency target (tokens per delivered hour, ≤390M). Weekly Review is the source of record for the denominator.

**This doc was corrected twice on the day it was written. The numbers below are the corrected ones; the correction history is at the bottom, kept rather than tidied away.**

## The numbers

| Week (Mon–Sun, UTC)    | Delivered hands-on | Typing-only floor |
| ---------------------- | ------------------ | ----------------- |
| **2026-08-03 → 08-09** | **19.1 h**         | 10.7 h            |
| **2026-08-10 → 08-16** | **14.9 h**         | 8.9 h             |
| Combined               | **34.0 h**         | 19.6 h            |

Both windows verified as exactly 7 distinct dates, events spanning `2026-08-03T01:37 .. 08-09T23:53` and `2026-08-10T00:03 .. 08-16T23:58`. Coverage 100% in both (371/371 and 330/330).

If the target must not be able to *pass* on measurement error, use the typing-only floor — it is a genuine lower bound, not a rival model.

## What "hands-on" means here

Time Bryan was **present on the work**, not time he was typing. A run of his own events with gaps under a 10-minute tolerance counts as one continuous interval; the modeled reading+typing window around each message is unioned in, so an isolated message still keeps its own estimate rather than collapsing to zero.

The obvious alternative is wrong in a known direction: the per-message keystroke model scores watching a build, reading a diff, and thinking at zero. Bryan rejected its output on sight in August, and he was right — it understated him by roughly half.

Both figures include review-comment time and exclude machine traffic in the human lane (peer relay, agent-authored review comments), filtered structurally via `promptSource` and by an author allowlist.

## The tolerance sweep — settled 2026-09-08

| Gap tolerance          | Aug 3–9    | Aug 10–16  |
| ---------------------- | ---------- | ---------- |
| 5 min                  | 12.9 h     | 10.4 h     |
| **10 min (published)** | **19.1 h** | **14.9 h** |
| 15 min                 | 22.4 h     | 19.8 h     |
| 30 min                 | 39.6 h     | 35.4 h     |

**Bryan ruled 2026-09-08: keep 10 min for transcript-derived data, and use 60 s for workspace data.**
Two instruments, two tolerances — the workspace records interaction directly and needs almost no bridging;
transcripts record messages and need a modelled window between them. So the 15-minute alternative below is
**closed**, and the published constant for every transcript-derived figure stays 10 min. The reasoning that
raised the question is kept because it is the record of why 10 min is a choice and not a measurement.

The tolerance was chosen on 2026-08-17 against an independent anchor: Bryan had *planned* 18 h for the Aug 10–16 week before any of this was measured, and when the model returned ~18.8 h he said it read "pretty close." That is the strongest kind of calibration signal — a prior estimate never fitted to the model.

But the recorded 2026-08-17 sweep (9.9 / 18.3 / 25.3 / 37.4 h) matches today's **8-day** rerun (12.8 / 17.3 / 23.2 / 41.1 h) far better than the correct 7-day one (10.4 / 14.9 / 19.8 / 35.4 h). So that calibration almost certainly compared an **8-day measurement against a 7-day plan**.

Re-run against the same anchor on a correct 7-day window:

- 10 min → **14.9 h**, i.e. 17% *under* his 18 h plan
- 15 min → **19.8 h**, i.e. 10% *over*

By that anchor alone 15 min looked the better fit, and 30 min stays ruled out on capacity. I did **not** change the published constant: it is a methodology parameter that Bryan's own judgement set, one anchor on one week is thin, and re-tuning it silently while correcting a different error is exactly how a model gets fitted to whatever is convenient. It went to Bryan as an open question, and his ruling is above.

Everything downstream treats the headline as **10 min**. The 15-minute alternative, which would have raised both
weeks by roughly a third, is not taken.

## Confidence, itemised — and what itemising missed

Measured today, not assumed:

- **Data foundation sound.** All three archive audits pass — transcript mirror lag 0.0 h, live store fully archived, zero settled transcripts short. Both windows genuinely covered (309 and 535 archived files with in-window mtimes; `rsync -a` preserves mtimes, so the check isn't vacuous).
- **Summing a per-task metric is safe to ≤4%.** Presence is per-task and was never designed to be summed, so the cross-task double-count was bounded directly: sum-of-parts ≥ their union, and the week-wide stitched union is a lower bound on the true union. Ceiling 0.84 h (4%) and 0.47 h (3%) on the 8-day runs; the same construction applies here.
- **Run-to-run variance ~±2%.** Task-id carry-forward reads every prior run, so task boundaries shift slightly between runs. Hence 0.1 h precision, not two decimals.
- **~0.9% undercount** from events attaching to no task.

**The honest caveat about that list: it was present, correct, and complete in its own terms in the first version of this doc — and the actual error was ~23% and ~9%, from measuring 8-day windows.** Itemising the known uncertainties made the number feel audited and made me stop looking. Read the list as "these specific things were checked," never as a bound on total error.

### Still flagged, not fixed

1. **Week boundaries are UTC.** Aug 3 00:00 UTC is Aug 2 17:00 Pacific, so some Sunday-evening Pacific work lands in the prior week. No timezone configuration exists. This shifts hours *between* weeks more than it changes the sum, so the combined 34.0 h is the sturdier figure.
2. **6.6% of Aug 10–16 (1.0 h) routes to project `unknown`**, against 1.9% the prior week. An *attribution* defect, not a total defect — unrouted work is still counted. It would matter for per-project denominators.
3. **No `est_h` in these runs** (`--no-enrich`), so nothing here speaks to output delivered, only to time present.

## Where the time went

| Aug 3–9        |              | Aug 10–16                   |              |
| -------------- | ------------ | --------------------------- | ------------ |
| client-a   | 6.62 h (35%) | client-a                | 8.82 h (59%) |
| client-b       | 5.69 h (30%) | ai-team-lead                | 2.15 h (14%) |
| health-tool    | 2.74 h (14%) | health-tool                 | 1.41 h (10%) |
| ai-team-lead   | 1.54 h (8%)  | *unknown*                   | 0.99 h (7%)  |
| notes-tool | 1.53 h (8%)  | notes-tool              | 0.63 h (4%)  |
| crm-tool   | 0.41 h (2%)  | claude-live-feedback-plugin | 0.51 h (3%)  |

Client-b reads 5.69 h in the first week and zero in the second. That is the consulting slug with no `projects.yaml` entry until 2026-08-24, so these runs route it correctly where a contemporaneous run would not have. The second-week zero is plausible engagement timing, not independently confirmed against a non-transcript source.

## Correction history

### Correction 3 (the one that moved the numbers): 8-day windows

`--until` is **inclusive** of its end date — `cli.py` maps the date to 23:59:59. I passed `--until 2026-08-10` for a week ending Aug 9, so **both original runs measured 8 days**, the two weeks overlapped on Aug 10, and 22.9% / 8.5% of Bryan's events in each fell on the surplus day.

Withdrawn: 21.4 h, 17.3 h, combined 38.7 h. Correct: 19.1 h, 14.9 h, 34.0 h.

**How it surfaced matters more than the bug.** All 24 internal audits passed on an 8-day week — nothing in the pipeline knew how long the window was meant to be, so no check could fire. It surfaced only because Team Lead's independently-built session count disagreed with mine (their 22/17 against my 24/29) and we chased the difference instead of splitting it. Subtracting the sessions that first appear on the surplus day reconciles us exactly: 29 − 13 = 16 vs their 17; 24 − 3 = 21 vs their 22. Their instrument was right.

Fixed in `1b92f71`: both flags document INCLUSIVE, every run now prints the resolved window and its day count flagging anything that isn't a whole number of weeks, and a test locks the semantics against the help text.

### Correction 2: the audits broke after Jul 28, not "since Jul 10"

Dated from artifacts, not git — this branch was squashed, so commit dates no longer order the change. An `audits.json` beside a run proves the audits ran for it: they ran from Jul 9 through **Jul 28**, then stopped, with 27 runs in Jul 29–Aug 17 producing none. Exposure is about a month, not seven weeks.

### Correction 1: the BI export is not broken and I mis-attributed it

I told Team Lead the ordering bug caused the "Tableau export stuck at Jul 10" item. Wrong, and an inference rather than a finding. The export was last written **Jul 14 12:14**, and the audits still ran Jul 14 and Jul 28, so the ordering bug post-dates the freeze. Every run since has been a narrow `--since` window, and `ExportWouldTruncate` refuses to shrink the existing 13-week table — the guard added after a two-week run destroyed that same export on 2026-07-12.

**The export is correctly protected, not broken.** It needs a deliberate full-history run.

## The genuine defect found along the way

The post-run **audits and the BI export both read `run.sqlite` and both were called before the pipeline wrote it.** Every run printed `no such table: run_meta` and `run.sqlite missing`, and each block's own best-effort `except` downgraded a dead call site to a warning line nobody read. `try/except` is for calls that *can* fail; a call that always fails is unreachable code wearing a resilience costume.

Fixed in `ee27122`, 254 tests pass. The regression test asserts *ordering*, since the defect was never in the functions.

## On Team Lead's "entirely turn growth" finding

Their decomposition and mine were cross-checked. Growth ratios, Aug 3–9 → Aug 10–16:

| Term               | Team Lead | This pipeline | Verdict        |
| ------------------ | --------- | ------------- | -------------- |
| Subagent lanes     | 2.12×     | 1.57×         | same direction |
| **Turns per lane** | **2.07×** | **1.90×**     | **replicates** |

**Conceded:** lanes got materially chattier and fan-out does not explain that. My claim that flat tokens-per-turn pointed at more agents rather than a longer loop was wrong — it is both, roughly evenly.

The parent-session term we disputed is now resolved in their favour: my higher session counts were the 8-day window, not a predicate difference. Their hypothesis (sessions with no billed turn) was independently ruled out — zero sessions in either week lack an assistant turn.

**Suggested companion metric, not an adjustment.** Tokens per *agent lane-hour*, or per unit of delivered output, separates "more agents working" from "each agent working less efficiently." Those have opposite remedies and the current single ratio cannot tell them apart.

## Reproducing

```
weekly-review --since 2026-08-03 --until 2026-08-09 --no-enrich
weekly-review --since 2026-08-10 --until 2026-08-16 --no-enrich
```

Note the **inclusive** end dates. `--no-enrich` is deliberate: presence needs no LLM enrichment, so these runs cost nothing in tokens. Sweep with `--presence-stitch-min`.
