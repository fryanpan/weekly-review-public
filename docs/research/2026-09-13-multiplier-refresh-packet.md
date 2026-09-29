# Productivity multiplier refresh: review packet

## Update Sep 14: rerun with over-merged tasks split

Run `run-20260914T111428Z` covers the same window: 2,275 tasks, all of them estimated. The sections below still show the Sep 13 run. Where the two differ, use these tables.

| Measure                                    | Sep 13 run             | Sep 14 run                      |
| ------------------------------------------ | ---------------------- | ------------------------------- |
| Median multiplier                          | 3.0× (n=825)           | **3.6×** (IQR 1.4–11.0, n=823)  |
| Same, transcripts only                     | 2.8×                   | 3.0× (n=609)                    |
| Median wall-clock speedup                  | 2.0×                   | **2.3×** (IQR 1.1–5.0, n=1,722) |
| Pooled Σest ÷ Σhands-on                    | 9.7× (3,789h ÷ 389.7h) | **15.1×** (5,931h ÷ 393.3h)     |
| Apr 13 – Jun 14: median / pooled           | 2.8× / 11.1×           | 2.8× / 12.6×                    |
| Share of est in tasks spanning over 7 days | 25.5% (109 tasks)      | 0.2% (4 tasks)                  |

| Month         | Median multiplier | Wall-clock speedup | Pooled |
| ------------- | ----------------- | ------------------ | ------ |
| Apr           | 3.5× (n=126)      | 2.6×               | 13.2×  |
| May           | 2.6× (n=96)       | 2.3×               | 11.7×  |
| Jun           | 2.6× (n=93)       | 2.7×               | 12.6×  |
| Jul           | 2.7× (n=163)      | 2.0×               | 9.7×   |
| Aug           | 5.1× (n=204)      | 2.2×               | 18.2×  |
| Sep (2 weeks) | 6.6× (n=141)      | 2.2×               | 27.9×  |

| Type          | Median multiplier | Wall-clock speedup | Pooled | Share of output | Share of hands-on |
| ------------- | ----------------- | ------------------ | ------ | --------------- | ----------------- |
| Code          | 12.7× (n=246)     | 4.4×               | 29.2×  | 65%             | 33%               |
| Research      | 2.9× (n=236)      | 2.4×               | 10.2×  | 18%             | 26%               |
| Documentation | 2.4× (n=153)      | 1.9×               | 6.0×   | 8%              | 21%               |
| Ops           | 1.6× (n=170)      | 1.2×               | 5.1×   | 6%              | 18%               |

| Task size (est) | Tasks | Median multiplier | Wall-clock speedup | Share of output | Share of hands-on |
| --------------- | ----- | ----------------- | ------------------ | --------------- | ----------------- |
| Under 30m       | 717   | 1.2×              | 1.4×               | 3%              | 15%               |
| 30m–2h          | 882   | 3.0×              | 2.1×               | 14%             | 41%               |
| 2–8h            | 544   | 10.6×             | 3.5×               | 33%             | 31%               |
| 8h and up       | 128   | 45.7×             | 14.9×              | 50%             | 13%               |

- **What the fix did.** Two things let a task chain months of work onto itself. A commit could attach to a months-old conversation. And a task holding one stray commit counted as still live, so it adopted more. 111 tasks spanned over 7 days; now 4 do, holding 14h between them.
- **Medians moved a little. Pooled moved a lot.** Aug–Sep estimated output went from 1,297h to 3,149h. Those commits used to sit on a few mega-tasks, each estimated once (one held 280h). Split out, each batch gets its own estimate, and the sum comes out larger.
- **No double counting found.** In claude-live-feedback-plugin, Sep 7–13, only 5 PRs are named by a second task, and each of those tasks is under 6h.
- **Pooled depends on how commits are grouped, not only on the work done.** The top 20 Aug–Sep tasks hold 1,169h. The largest is a single message that dispatched 35 PRs, estimated at 160h. Treat pooled as unconfirmed until Bryan's estimate sample covers tasks of a day or more.
- **Lead with the median (about 3.5×) and wall-clock speedup (about 2.3×).** Neither moved by more than 0.6× across the fix.
- **August wall-clock speedup rose from 1.5× to 2.2×.** So chaining caused part of the August drop, not only board actions.
- **The est-vs-rationale audit flags 7 tasks (Sep 13: 3).** I read all 7: each rationale lists parts that add up to the estimate, so none is a real mismatch.
- **The Trend caveat still applies.** Aug–Sep medians (5.1×, 6.6×) rise alongside the new workspace instruments.

## Status: on hold until the failure-modes fixes land (Sep 14); don't quote these numbers

1. **Every project now has estimates.** Bryan raised the API cap and the whole period (Apr 13 – Sep 13, 22 weeks) was re-run as one piece. 2,093 of 2,099 tasks have an estimate. The other 6 failed on malformed model responses, not on the cap.
2. **The re-run cost $8.78** for 533 new estimates. Every other estimate came from the on-disk cache.
3. **Headline (updated Sep 14):** the typical task runs about **3.6×** Bryan's hands-on time, and wall-clock speedup is **2.3×**. The pooled figure of **15.1×** depends on how commits are grouped (see the Sep 14 update).
4. **Don't publish the 9.2× in the draft.** It can't be reproduced, and under the current method it is about 3×.
5. **Don't call the Aug–Sep rise a productivity jump.** It lines up with new workspace instruments starting to count toward hands-on (see [Trend](#trend)).

## Headline numbers

These figures come from the Sep 13 run `run-20260914T063444Z` and are **superseded by the Sep 14 update above**. They cover Apr 13 – Sep 13, all projects. The per-task median counts only tasks with at least 3 minutes of hands-on.

| Measure                                         | Value                         |
| ----------------------------------------------- | ----------------------------- |
| Median multiplier, `est_h ÷ hands-on`           | **3.0×** (IQR 1.3–8.4, n=825) |
| Same, transcripts only                          | 2.8× (IQR 1.2–7.2, n=606)     |
| Median wall-clock speedup, `est_h ÷ wall_clock` | 2.0× (IQR 1.0–4.0, n=1,715)   |
| Pooled, Σest ÷ Σhands-on                        | **9.7×** (3,789h ÷ 389.7h)    |

| Type          | Median multiplier              | Wall-clock speedup | Pooled | Share of output | Share of hands-on |
| ------------- | ------------------------------ | ------------------ | ------ | --------------- | ----------------- |
| Code          | **9.9×** (IQR 3.8–20.1, n=230) | 3.1×               | 16.8×  | 57%             | 33%               |
| Research      | 2.8× (n=245)                   | 2.3×               | 7.8×   | 20%             | 25%               |
| Documentation | 2.0× (n=157)                   | 1.6×               | 5.6×   | 11%             | 18%               |
| Ops           | 1.7× (n=180)                   | 1.3×               | 4.0×   | 9%              | 22%               |
| UX design     | 5.4× (n=12, too thin)          | 3.3×               | 7.8×   | 1%              | 1%                |

| Task size (est)    | Tasks | Median multiplier | Wall-clock speedup        | Post says (speedup) |
| ------------------ | ----- | ----------------- | ------------------------- | ------------------- |
| Trivial, under 30m | 670   | 1.2× (n=229)      | 1.4× (n=403)              | 1.7×                |
| Small, 30m–2h      | 896   | 3.0× (n=363)      | 2.0× (n=808)              | 4.9×                |
| Moderate, 2–8h     | 467   | 10.7× (n=200)     | 2.6× (n=452)              | 9.7×                |
| Day+, 8h and up    | 56    | 20.2× (n=33)      | 4.9× (IQR 2.3–19.7, n=52) | "above 10×", n=15   |

### Why the post's 9.2× no longer holds

Both columns cover Apr 13 – Jun 14, use the same 3-minute floor and take the per-task median.

|                                   | June run (old method)      | Now (current method, all projects) |
| --------------------------------- | -------------------------- | ---------------------------------- |
| Median multiplier                 | 7.4× (IQR 3.4–14.9, n=262) | **2.8×** (IQR 1.2–6.3, n=284)      |
| Pooled Σest ÷ Σhands-on           | 11.4×                      | **11.1×**                          |
| Median estimate per included task | 1.50h                      | 0.75h                              |
| Median hands-on per included task | 0.15h                      | 0.26h                              |

- **I could not reproduce the post's 9.2× (n=467).** Recomputing the June run with this filter gives 7.4× (n=262), so the post must have used a looser inclusion cut.
- **Two method fixes explain the fall. Neither is a change in how Bryan works:**
  - **Hands-on now measures presence, not keystrokes** (fixed Aug 17). A typical task's hands-on went up about 1.7×.
  - **Each artifact is credited to one task only** (fixed Jul 28). A typical task's estimate halved, because a PR no longer counts in several tasks.
- **The pooled ratio barely moved.** Total output against total hands-on is stable; only the typical task changed.

### Typical task about 3×, total output about 10×: where the gap comes from

These tables cover the 2,089 tasks that have estimates and hands-on figures: 3,789h of estimated output against 389.7h of hands-on.

| Tasks, by their own multiplier  | Tasks | Share of output | Share of hands-on | Pooled     |
| ------------------------------- | ----- | --------------- | ----------------- | ---------- |
| Delegated: under 3 min hands-on | 1,264 | **55%**         | 2%                | about 220× |
| Under 2×                        | 302   | 5%              | **52%**           | 0.9×       |
| 2–5×                            | 233   | 9%              | 26%               | 3.3×       |
| 5–20×                           | 202   | 15%             | 16%               | 9.4×       |
| 20× and up                      | 88    | 16%             | 4%                | 40×        |

| Task size (est) | Tasks | Share of output | Share of hands-on | Pooled |
| --------------- | ----- | --------------- | ----------------- | ------ |
| Under 30m       | 670   | 4%              | 17%               | 2.3×   |
| 30m–2h          | 896   | 22%             | **45%**           | 4.7×   |
| 2–8h            | 467   | 43%             | 29%               | 14×    |
| 8h and up       | 56    | 31%             | 8%                | 36×    |

- **Most of Bryan's hands-on time goes to tasks that barely multiply.** 52% of it is on tasks under 2×: steering, reviewing, conversation and small fixes.
- **Most output comes from tasks he barely touches.** 55% of estimated output is on tasks with under 3 minutes of hands-on. The median leaves those out by design.
- **Bigger tasks multiply more.** Tasks of 2h and up hold 74% of output for 37% of hands-on.
- **So both numbers are true.** The median describes a typical hands-on task, and the pooled ratio describes the whole period.
- **Caveat:** the delegated rows lean hardest on LLM estimates nobody has hand-checked. They also include artifact-only tasks, which the audits flag as the likeliest place for an over-estimate.

## Trend

| Month      | Median multiplier | Transcripts only | Pooled | Wall-clock speedup | Hands-on on tasks | Hands-on from transcripts |
| ---------- | ----------------- | ---------------- | ------ | ------------------ | ----------------- | ------------------------- |
| Apr        | 3.7× (n=134)      | 3.6×             | 11.3×  | 2.7×               | 59.3h             | 98%                       |
| May        | 2.4× (n=95)       | 2.3×             | 12.4×  | 2.2×               | 58.8h             | 99%                       |
| Jun        | 2.5× (n=96)       | 2.3×             | 7.6×   | 2.7×               | 41.0h             | 98%                       |
| Jul        | 2.4× (n=159)      | 2.6×             | 9.2×   | 2.0×               | 85.3h             | 95%                       |
| Aug        | 3.4× (n=201)      | 2.5× (n=110)     | 8.4×   | 1.5×               | 99.5h             | 51%                       |
| Sep (2 wk) | 4.3× (n=140)      | 4.6× (n=25)      | 10.0×  | 1.6×               | 45.8h             | 13%                       |

| Week of | Median multiplier | Transcripts only | Wall-clock speedup | Hands-on from transcripts |
| ------- | ----------------- | ---------------- | ------------------ | ------------------------- |
| Apr 13  | 4.4× (n=52)       | 4.4×             | 3.0×               | 96%                       |
| Apr 20  | 4.4× (n=43)       | 4.4×             | 2.7×               | 100%                      |
| Apr 27  | 2.5× (n=48)       | 2.4×             | 2.4×               | 99%                       |
| May 4   | 3.0× (n=22)       | 3.0×             | 2.3×               | 96%                       |
| May 11  | 2.0× (n=18)       | 2.0×             | 1.9×               | 98%                       |
| May 18  | 2.0× (n=17)       | 2.0×             | 2.6×               | 100%                      |
| May 25  | 2.5× (n=27)       | 2.5×             | 2.1×               | 100%                      |
| Jun 1   | 1.8× (n=28)       | 1.8×             | 2.6×               | 99%                       |
| Jun 8   | 3.2× (n=27)       | 3.2×             | 3.2×               | 100%                      |
| Jun 15  | 1.8× (n=17)       | 1.8×             | 2.7×               | 100%                      |
| Jun 22  | 3.9× (n=14)       | 3.4×             | 3.7×               | 75%                       |
| Jun 29  | 1.6× (n=22)       | 1.9×             | 1.9×               | 92%                       |
| Jul 6   | 2.7× (n=49)       | 2.7×             | 2.0×               | 96%                       |
| Jul 13  | 2.6× (n=42)       | 2.6×             | 2.2×               | 98%                       |
| Jul 20  | 3.6× (n=33)       | 3.6×             | 2.1×               | 100%                      |
| Jul 27  | 1.8× (n=25)       | 1.8×             | 2.1×               | 89%                       |
| Aug 3   | 1.4× (n=35)       | 1.7×             | 1.9×               | 89%                       |
| Aug 10  | 2.8× (n=39)       | 2.0× (n=22)      | 1.6×               | 51%                       |
| Aug 17  | 4.0× (n=50)       | 3.7× (n=22)      | 1.2×               | 37%                       |
| Aug 24  | 4.8× (n=63)       | 3.5× (n=32)      | 1.6×               | 45%                       |
| Aug 31  | 5.3× (n=78)       | 4.7× (n=13)      | 1.3×               | 11%                       |
| Sep 7   | 3.9× (n=74)       | 3.7× (n=13)      | 1.8×               | 14%                       |

- **April to early August moves between 1.4× and 4.4× with no trend.** Monthly medians stay between 2.4× and 3.7×. Weekly medians rest on 14–52 tasks, so a single week can move by ±1× on noise alone.
- **Weeks from Aug 17 read 4–5×, but that step is measurement.** Board task links arrive Aug 13, voice requests Aug 18 and meetings Aug 28. In the same weeks the transcript share of hands-on drops from about 95% to 11–45%.
- **The transcripts-only series rises too (3.5–4.7×), but only on 13–32 tasks a week.** That is too thin to call a workflow change on its own.
- **The August dip in wall-clock speedup was mostly segmentation, not instruments.** See [Why wall-clock speedup fell in Aug–Sep](#why-wall-clock-speedup-fell-in-augsep) below.
- **A safe chart line:** April to July flat at about 2.5–3.5×, with August onward shaded as "new instruments". Put no workflow label inside the shaded band.

### Why wall-clock speedup fell in Aug–Sep

Monthly median of `est_h ÷ wall_clock` over tasks with at least 3 minutes of wall-clock. Each row after the second takes the Sep 14 rerun and removes one candidate cause. Medians here take the lower middle value, so they can read 0.1× below the tables above.

| Variant                                    | Apr  | May  | Jun  | Jul  | Aug      | Sep      |
| ------------------------------------------ | ---- | ---- | ---- | ---- | -------- | -------- |
| Sep 13 run                                 | 2.7× | 2.2× | 2.7× | 2.0× | **1.5×** | **1.6×** |
| Sep 14 rerun (chaining fixed)              | 2.6× | 2.3× | 2.7× | 2.0× | **2.1×** | **2.1×** |
| Rerun, workspace review sessions removed   | 2.6× | 2.3× | 2.7× | 2.1× | 2.2×     | 2.2×     |
| Rerun, subagent and workflow lanes removed | 2.7× | 2.3× | 2.6× | 2.1× | 2.2×     | 2.2×     |
| Rerun, type mix held at Apr–Jul shares     | 2.5× | 2.3× | 2.8× | 2.0× | 2.1×     | 2.0×     |

- **Segmentation explains most of the dip.** August sat about 0.9× below the Apr–Jul months. The chaining fix recovers 0.6× of that, about two-thirds. The cause: August conversations had lost their commits to months-old tasks, so they kept their wall-clock but not the matching estimate. Median August est rose from 1.0h to 1.5h, while median wall-clock barely moved (0.68h to 0.62h).
- **New workspace sources explain about 0.1×.** Review sessions add Bryan intervals from Aug 13. Meetings and board actions don't touch wall-clock at all.
- **Agent fan-out explains about 0.1×.** This is a lower bound: the dispatching lane still waits while its children run.
- **Task mix explains nothing.** Holding type shares at Apr–Jul moves no month by more than 0.1×.
- **After the fix, Aug–Sep (2.1×) sits inside the Apr–Jul range (2.0–2.7×).** What remains is a mild drift from about 2.5× (Apr–Jun) to about 2.1× (Jul–Sep). It starts in July, before any new instrument arrived, and median wall-clock per task grows from 0.35h to 0.5–0.6h. None of the four candidates explains that drift.
- **For the post:** quote one wall-clock speedup of about 2.3× for the whole period. Don't describe a trend.

Method: `wallclock_candidates.py` rebuilds wall-clock from `run.sqlite` with the pipeline's own interval functions. It matches the stored values to 0 seconds across 2,275 tasks.

## Self-validation

| Check                                                        | Result                                                       |
| ------------------------------------------------------------ | ------------------------------------------------------------ |
| Transcript/workspace split reproduces persisted per-task hands-on | Max difference 0 s over 2,093 tasks                          |
| Hands-on on estimated tasks vs the 22 published weeks        | 389.7h vs 382.7h (+1.8%), inside run-to-run variance         |
| Artifacts placed in more than one task                       | 358, each credited to one owner; 0 owners also listed as duplicates |
| Excluded by the 3-minute hands-on floor                      | 1,268 tasks, holding 55% of estimated hours                  |
| Estimate vs its own rationale, 3× or more apart              | 10 tasks, e.g. one at 28h vs 3.5h and one at 0.3h vs 7.5h. Not corrected; they don't move a median |
| Estimated below its own hands-on                             | 148 tasks                                                    |
| Bryan's reviewed estimates                                   | 52 applied, 6 skipped as stale, 32 quarantined as pre-fix    |
| Estimator failures                                           | 6 malformed responses; 0 API-cap refusals                    |
| Other audits needing review                                  | 16 possible duplicate task pairs; 72 events from an excluded project; 23 tasks with 1h+ est and no wall-clock |

## Spot-check: 10 random included tasks (seed 20260913)

| Task       | Kind                                     | Est   | Hands-on | Wall  | Multiplier | Read                                                         |
| ---------- | ---------------------------------------- | ----- | -------- | ----- | ---------- | ------------------------------------------------------------ |
| 420c420450 | client app project: schedule note        | 0.2h  | 0.16h    | 0.05h | 1.3×       | OK                                                           |
| bb518781dc | aborted session, nothing shipped         | 0.1h  | 0.19h    | 0.17h | 0.5×       | OK. No project assigned                                      |
| c871bb3060 | search-tool advisory chat                 | 0.5h  | 0.28h    | 0.25h | 1.8×       | OK                                                           |
| 17b836aace | draft rewrite plus comparison memo       | 1.5h  | 0.54h    | 1.64h | 2.8×       | OK                                                           |
| d11502eefe | ticket review and triage                 | 0.75h | 0.12h    | 0.34h | 6.1×       | OK. No project assigned                                      |
| 8980597198 | plan plus 4 tickets                      | 0.75h | 0.07h    | 0.56h | 10.3×      | Plausible: delegated, 2 messages                             |
| d1843669bc | client app project: on-device UX reviews | 3.0h  | 1.46h    | 1.72h | 2.1×       | OK                                                           |
| acf2f96993 | workspaces agent-policy fixes, PR merged | 6.0h  | 0.12h    | 4.28h | 51×        | **Suspect:** spans 14 days, 4,987 events, 9 messages; title doesn't match. Likely an over-merged task |
| 66d5f05eb7 | disk-space audit                         | 0.5h  | 0.78h    | 0.71h | 0.6×       | OK                                                           |
| adac1e1da9 | memo condensing pass                     | 0.75h | 0.20h    | 0.29h | 3.7×       | OK                                                           |

**Summary:** 9 of 10 look right and 1 looks over-merged. 2 of the OK tasks have no project assigned. This is **spot-checked, not validated**, and nothing was corrected.

## Caveats

- **Estimates are LLM figures.** Only 52 carry Bryan's own review.
- **Hands-on is presence:** 10-minute transcript stitching unioned with 60-second workspace blocks. That is a modelling choice inside a range, not a stopwatch.
- **Workspace reading is recorded from Apr 17 but rarely tied to a task before Aug 13.** Until then, all-instruments and transcripts-only are nearly the same line.
- **This trend comes from one whole-period run, not the weekly runs.** Weekly runs cut conversations off from their PRs at week boundaries.
- **Tasks are bucketed by the week of their first event.** A task that spans weeks counts entirely in its first.
- **Run-to-run variance is about ±2%.** Quote to one decimal place.

## Measurement changes vs workflow changes

- **Source arrivals** (a step here is measurement):
  - Workspace doc reading: Apr 17.
  - Board actions and taskId joins: Aug 13.
  - Voice requests: Aug 18.
  - Recorded meetings: Aug 28.
- **Method fixes** (re-applied to all weeks, so no step): presence (Aug 17) and single artifact credit (Jul 28).
- **Workflow markers in the same week as a source arrival:** Apr 17, Aug 13 and Aug 27 are unsafe; Aug 17 and Aug 20 are safe in practice.

## Decision (resolved)

Bryan chose **A: raise the API spending cap**. The re-run cost $8.78, inside the $8–10 estimate.
