# Every week restated, 2026-04-13 → present

Twenty-two ISO weeks re-measured under the current method on 2026-09-12, so the
trend can be read without a step in it that is a method change rather than a
change in Bryan's work.

Every figure below was produced by running the shipped CLI once per week —
`weekly-review --since <mon> --until <sun> --no-enrich` — and reading the
numbers it printed. None of it is re-derived from an earlier run's output. That
matters here: five attempts to reproduce one published figure by guessing its
method got as close as 23.75h against 25.06h, and only recovering the original
script reproduced it exactly.

`--until` is **inclusive**, so each window is Mon..Sun and consecutive weeks do
not overlap on the boundary day. Every run printed its own resolved window and
day count; all 21 whole weeks measured 7 days, and the current week is marked
partial.

## The series

<!-- GENERATED: do not edit by hand -->

| week of | d | published | transcripts | workspace | era | his msgs | ws doc events | board events | unread |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-04-13 | 7 | **18.21h** | 18.19h | 0.01h | keyboard only | 494 | 20 | 0 | 9% |
| 2026-04-20 | 7 | **15.79h** | 15.74h | 0.05h | keyboard only | 413 | 60 | 0 | 12% |
| 2026-04-27 | 7 | **15.99h** | 15.91h | 0.08h | keyboard only | 439 | 49 | 0 | 2% |
| 2026-05-04 | 7 | **8.73h** | 8.71h | 0.02h | keyboard only | 237 | 144 | 0 | 10% |
| 2026-05-11 | 7 | **13.14h** | 12.70h | 0.46h | keyboard only | 371 | 334 | 0 | 5% |
| 2026-05-18 | 7 | **9.57h** | 9.57h | 0.00h | keyboard only | 228 | 179 | 0 | — |
| 2026-05-25 | 7 | **19.88h** | 19.88h | 0.00h | keyboard only | 496 | 100 | 0 | 0% |
| 2026-06-01 | 7 | **18.14h** | 18.02h | 0.11h | keyboard only | 458 | 203 | 0 | 1% |
| 2026-06-08 | 7 | **5.87h** | 5.87h | 0.00h | keyboard only | 136 | 197 | 0 | — |
| 2026-06-15 | 7 | **5.14h** | 5.14h | 0.00h | keyboard only | 108 | 59 | 0 | — |
| 2026-06-22 | 7 | **2.66h** | 2.03h | 0.66h | keyboard only | 69 | 87 | 0 | — |
| 2026-06-29 | 7 | **11.15h** | 9.43h | 1.87h | keyboard only | 264 | 90 | 0 | — |
| 2026-07-06 | 7 | **24.51h** | 18.10h | 7.49h | keyboard only | 448 | 378 | 0 | 0% |
| 2026-07-13 | 7 | **23.41h** | 21.22h | 3.43h | keyboard only | 498 | 407 | 0 | 1% |
| 2026-07-20 | 7 | **18.33h** | 16.01h | 2.68h | keyboard only | 402 | 296 | 0 | — |
| 2026-07-27 | 7 | **22.06h** | 15.65h | 7.52h | keyboard only | 410 | 742 | 0 | 2% |
| 2026-08-03 | 7 | **26.16h** | 14.83h | 13.78h | keyboard only | 463 | 1,292 | 0 | 14% |
| 2026-08-10 | 7 | **24.70h** | 10.99h | 15.09h | + board | 651 | 2,241 | 262 | 10% |
| 2026-08-17 | 7 | **26.31h** | 14.89h | 12.64h | + voice | 896 | 2,525 | 1,320 | 1% |
| 2026-08-24 | 7 | **20.63h** | 10.24h | 11.74h | all four | 780 | 2,423 | 1,527 | — |
| 2026-08-31 | 7 | **29.60h** | 4.61h | 25.60h | all four | 862 | 4,254 | 3,796 | — |
| 2026-09-07* | 6 | **22.72h** | 2.93h | 20.23h | all four | 719 | 3,446 | 2,752 | — |

21 whole weeks total 360.0h; partial weeks excluded from the total.

## What the series says

**21 whole weeks total 360.0h**, from 2.66h in the week of 2026-06-22 to 29.60h
in the week of 2026-08-31. The current week is partial (6 days) and excluded
from that total.

Two things in the table are worth reading before the trend:

**The instrument mix inverts completely.** In April, transcripts carry
essentially all of it (18.19h of 18.21h, workspace 0.01h). By the week of
2026-08-31 that has flipped — transcripts 4.61h, workspace 25.60h. That is not
Bryan moving off the keyboard by 78%; it is the workspace instrument arriving
and then becoming where the work is discussed. **A week before 2026-07 and a
week after are measured by substantially different instruments, and most of the
apparent rise across the series is that.**

**One independent check passed exactly.** The week of 2026-08-31 restates to
**29.60h**, and the spec's separately-produced published figure for that week
(`run-20260912T133716Z`) is **29.60h**. Same number to the digit from a run this
sweep did not touch — which is the only evidence here that the sweep reproduces
the pipeline rather than merely being self-consistent.

## The four eras

The task this came from assumed two eras — "tasks are inferred before
2026-08-13, goals throughout". Measured against the stores, there are four, and
the first boundary is the reason the series starts where it does.

| instrument | first record | first week | what it adds |
| --- | --- | --- | --- |
| Claude Code transcripts | throughout | W16 | what he typed, and the gaps around it |
| workspace doc activity | **2026-04-17** | W16 | reading and commenting, off the keyboard |
| board actions, and the first `taskId` anywhere | 2026-08-13 | W33 | which task a moment belongs to |
| `voice.request` | 2026-08-18 | W34 | dictation to his own board |
| meetings | 2026-08-28 | W35 | recorded presence while talking |

**2026-04-17 is not an arbitrary start date.** It is the day the workspace
instrument began recording, which is why the task named it. Before it, the only
witness is a transcript, and a transcript can only see a keyboard.

So the honest shape of the series is: **W16–W32 had one instrument, W33 gained
task identity, W34 and W35 each gained a source, and only from W35 does the
measurement see time spent away from the keyboard.** A rise at W33 is partly
the instrument arriving. A rise at W35 is partly the recorder being switched
on. Neither is him working more.

### Weeks where an instrument started mid-week

- **2026-04-13** — doc activity from 2026-04-17
- **2026-08-10** — board + taskId from 2026-08-13
- **2026-08-17** — voice.request from 2026-08-18
- **2026-08-24** — meetings from 2026-08-28

### What the era caveat cannot be built from

The task body quotes weekly workspace volumes of 17–145 events for W16–W27,
330–1,183 for W28–W32, and 1,370–2,955 for W33 on. Recounted off the raw store,
where no dedup has ever applied, those are **20–334**, **296–1,292** and
**2,503–8,050**. The groupings are right and the boundary at W33 is real; the
volumes are not, because they were counted while the event-id defect was
discarding four of every five read sessions and four of every five comments.

A caveat about how much evidence a week had is only as good as the count behind
it, and that count moved by up to 2.7x the week the dedup was fixed.

## What each week supersedes, compared like with like

<!-- GENERATED: do not edit by hand -->

Each old figure is set against the SAME construction in the restated run,
never against the published union — a union compared to a sum flatters
whichever side was summed.

| week of | per-message: was → now | presence sum: was → now | published union (new) |
| --- | --- | --- | --- |
| 2026-04-13 | 15.2 → 13.9h | 18.9 → 22.4h | **18.21h** |
| 2026-04-20 | 13.8 → 12.0h | 16.6 → 17.9h | **15.79h** |
| 2026-04-27 | 11.7 → 11.2h | 16.8 → 19.5h | **15.99h** |
| 2026-05-04 | 8.1 → 5.7h | 9.9 → 9.5h | **8.73h** |
| 2026-05-11 | 11.6 → 8.3h | 13.4 → 14.8h | **13.14h** |
| 2026-05-18 | 8.1 → 6.5h | 8.5 → 10.1h | **9.57h** |
| 2026-05-25 | 12.8 → 11.1h | 20.9 → 23.1h | **19.88h** |
| 2026-06-01 | 15.0 → 12.1h | 19.1 → 20.4h | **18.14h** |
| 2026-06-08 | 4.2 → 3.9h | 5.7 → 6.2h | **5.87h** |
| 2026-06-15 | 5.3 → 3.7h | 3.5 → 5.5h | **5.14h** |
| 2026-06-22 | 2.5 → 2.3h | 2.0 → 3.6h | **2.66h** |
| 2026-06-29 | 6.4 → 7.1h | 9.4 → 12.3h | **11.15h** |
| 2026-07-06 | 9.4 → 14.5h | 19.5 → 23.2h | **24.51h** |
| 2026-07-13 | 14.3 → 14.4h | 21.3 → 25.6h | **23.41h** |
| 2026-07-20 | 10.7 → 10.3h | 16.7 → 18.3h | **18.33h** |
| 2026-07-27 | 12.3 → 12.4h | 16.2 → 19.8h | **22.06h** |
| 2026-08-03 | 11.5 → 13.8h | 20.1 → 23.0h | **26.16h** |
| 2026-08-10 | 10.8 → 20.4h | 18.8 → 31.3h | **24.70h** |
| 2026-08-17 | — | — | **26.31h** |
| 2026-08-24 | — | — | **20.63h** |
| 2026-08-31 | — | — | **29.60h** |
| 2026-09-07 | — | — | **22.72h** |

## What this replaces, and where those numbers went

Four prior publications carry whole-week hands-on figures. All four are
superseded by the table above; the spec's current published week (29.60h for
2026-08-31, `run-20260912T133716Z`) is **not** — it was produced by the same
code as this series.

| where | what it published | method | status |
| --- | --- | --- | --- |
| `task_facts.csv` (BI export) | 13 weeks, 124.05h total, `run-20260710T073100Z` | per-message typing model, per-task sum | superseded; the file is still frozen at Jul 14 by `ExportWouldTruncate` and has **not** been rewritten by this work |
| `docs/product/specs/2026-08-17-hands-on-measurement.md` | 18-week backfill, the 10min column | transcript presence @10min, per-task sum, **8-day windows** | superseded |
| `docs/data/2026-05-25-last-2w-digest.md` | 22.91h over 2 weeks | per-message typing model | superseded |
| `docs/research/2026-08-28-delivered-hands-on-aug-3-16.md` | 19.1h / 14.9h / 34.0h | transcript presence @10min, 7-day windows | superseded; **this is the one that went to Team Lead** |

**Two of these publish the same two weeks and disagree with each other.** The
2026-08-17 backfill has 2026-08-03 at 20.1h and 2026-08-10 at 18.8h; the
2026-08-28 doc has 19.1h and 14.9h. The gap is not new data — it is the
inclusive `--until`, which made every row of the backfill an 8-day week. That
error is already retracted in the 2026-08-28 doc, but the backfill table it
came from is still the largest published series this repo has, which is most of
why a restatement was needed at all.

The BI export is the one superseded artifact this doc does not fix. It needs a
deliberate full-history run to lift the truncation guard, and doing that from
inside a 22-week sweep would overwrite 13 weeks of a downstream table as a side
effect of a research task. Left as its own piece of work.

## Figures given to Team Lead

<!-- GENERATED: do not edit by hand -->

These two weeks are the token-efficiency denominator, so they get the full
comparison. Note that this repo has published each of them TWICE already,
and the two disagree: the 2026-08-17 backfill measured 8-day windows
(`--until` is inclusive), which is the error the 2026-08-28 doc retracted.
The restated figure is set against the 2026-08-28 one, which is the 7-day
measurement and the number actually delivered.

| week of | 8-day backfill (withdrawn) | delivered 2026-08-28 | restated, transcripts only (same construction) | typing floor: was → now | published union (all sources) |
| --- | --- | --- | --- | --- | --- |
| 2026-08-03 | 20.1h | **19.1h** | 16.2h (-2.9h) | 10.7 → 13.8h | **26.16h** |
| 2026-08-10 | 18.8h | **14.9h** | 12.0h (-2.9h) | 8.9 → 20.4h | **24.70h** |

- **2026-08-03**: 73 of his messages (14%) are unread because of the reused-session-id defect, so the restated figure is a FLOOR.
- **2026-08-10**: 73 of his messages (10%) are unread because of the reused-session-id defect, so the restated figure is a FLOOR.

### Reconciling the two weeks that went to Team Lead

Measured on the construction those figures actually used — transcripts only, no
workspace sources at all — both weeks come back **lower by exactly 2.9h**:
19.1h → 16.2h, and 14.9h → 12.0h.

Both weeks are also missing **exactly 73 of his messages** to the coverage
defect below. Two weeks moving by the same amount, each missing the same number
of messages, is consistent with the defect accounting for most or all of the
movement rather than the method having changed underneath the delivered figure.

That is a reconciliation, not a measurement: I have not measured how many hours
those 146 messages would carry, and I am not going to infer it from a message
count, because hours per message is exactly the per-message model this project
rejected. **What can be said without modelling anything: the restated
transcript-only figures are floors, the delivered figures sit above them, and
nothing here shows the delivered numbers were wrong.**

## A coverage defect found while doing this, still open

The restated weeks before 2026-08-17 read **less** transcript than the same
weeks read on 2026-08-17, and the cause is not a filter.

Claude Code reused session id `de3f8893-10e7-45b6-a00f-6368d816a705` for a new
conversation on 2026-08-17T19:44. The archiver runs `rsync --update --backup`,
so the mirror file was replaced by the new conversation and the original was
filed into a dated `mac_mini_history/` snapshot. `cli.py` puts those snapshot
directories in `SKIP_DIRS`, on the stated assumption that the mirror is "a
monotonically-growing union … no `--delete`". That assumption holds exactly as
long as a session id is used once.

Measured two ways that agree to the message — inferring the loss from what the
2026-08-17 runs read, and running the shipped extractor directly over the lost
file:

- **one session id** of the 13,410 in the archive is affected
- its lost era runs **2026-04-17 → 2026-08-17**
- it holds **32,766 events and 342 of his messages**
- weighted onto **2026-08-03 (73) and 2026-08-10 (73)** — the two weeks whose
  hours went to Team Lead as the token-efficiency denominator — and
  **2026-04-13 (51), 2026-04-20 (57)**; eight weeks lose nothing at all

It is not recoverable by pointing `--extra-transcripts` at the snapshot. Both
conversations carry the same session id *and* the same filename, and the event
id derives its lane from the filename precisely so that one file ingested from
two mirrors dedupes to one event. The two eras collide and one is dropped.
Fixing it means putting the file's era into the identity, which shifts
persisted ids — review verdicts and task-id carry-forward are keyed on them —
so it is filed separately rather than bolted onto this restatement.

**Every week from 2026-08-24 is unaffected.** The `unread` column in the series
table is what the defect keeps out of each week, as a share of the messages
that week did read.

## Why an old week moves at all

The past is fixed, so every movement in this series is the pipeline reading it
differently. For the weeks before the board existed, the whole movement is on
the transcript side, and it decomposes cleanly. Taking W16 against its own
2026-08-17 run, whose database is still on disk:

| | 2026-08-17 run | restated run |
| --- | --- | --- |
| transcript sessions read | 1,460 | 1,460 |
| his transcript messages | 533 | 482 |
| his doc events | 4 | **28** |
| presence @10min, per-task sum | 18.9h | 22.4h |
| published union | — | **18.21h** |

Identical session coverage, fewer of his messages, and **7x his doc events** —
and presence rises by 3.5h. That is the dedup fix returning 24 recovered
instants into a model where each one can bridge up to ten minutes. Under the
60s block rule the same 24 events buy 0.01h.

So the two-tolerance ruling is visible in the history and not only in the
current week: **the recovered evidence is worth 3.5h under a modelled window
and one minute under a rule that only joins things he actually did.** The
published union ends up slightly *below* the figure it replaces, which is the
block rule removing modelled padding, not the week shrinking.

## What it still cannot see

Everything in the spec's "What the number still cannot see" applies unchanged.
Two things are specific to reading this series as a trend:

- **No source here backfills.** Meetings and voice requests contribute exactly
  zero before 2026-08-24, and board actions exactly zero before 2026-08-13.
  Weeks either side of those boundaries are measured by different instruments
  and a difference across one is not a difference in his week.
- **The run-to-run variance is ~±2%** — task-id carry-forward reads every prior
  run, so task boundaries shift slightly each time. Quote a week to 0.1h, and
  do not treat a small delta between two runs as a finding.

## Provenance

- 22 runs on 2026-09-12, `weekly-review --since <mon> --until <sun> --no-enrich`
- run in two batches (W16–W25, then W26–W37 after a session restart killed the
  first sweep mid-W26). Same command, same commit, same machine; the second
  batch skipped the already-finished weeks rather than re-measuring them, so no
  week in the series was measured twice and none was averaged
- tolerances: transcripts 10-minute presence, workspace 60-second blocks
  (Bryan's ruling, 2026-09-08)
- code as of `fc6cab5` on `feat/meeting-turns`, with a clean `src/` and
  `tests/` working tree — three commits past the `62104b3` merge, which is what
  gives the series the meetings and voice sources. The earlier merge commit
  would be the wrong citation: it predates two of the four instruments
- the per-week coverage loss was measured by
  `extract_from_jsonl` over the superseded archive copy, so it uses the same
  classification the hours are built from
