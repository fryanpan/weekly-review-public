# Flight review — headline 10× cut

**For Bryan, in flight window 2026-06-12 evening.** Single-page reaction doc. Comment inline on anything that looks off; I'll iterate.

**Reviewer-assurance status:** all numbers below labeled by data state. **None of the underlying weekly runs are fully reviewer-validated yet** — you've only spot-checked them. RN's demo will use the verbatim phrase "spot-checked, awaiting full review" alongside every number.

---

## 1. Headline — two metrics, two comparators

**Data set.** Clean = Apr 27 + May 11-25 + Jun 1 windows (all-Claude-Code era; R2 traffic ended Apr 12, so March is excluded per your call). n = 442 enriched tasks.

> **Use per-task MEDIANS, not pooled.** Pooled (Σest ÷ Σdenominator) is inflated by an attribution bug: 68% of est*h sits in PR-ship tasks with ~0 denominator (their conversation is attributed to a sibling task), so the big est*h lands in the numerator with ~0 in the denominator. Restricting to tasks where both numbers are real, pooled and median converge — so the median is the honest figure. Pooled is dropped until the ship-task↔conversation join is fixed.

**Metric A — wall-clock speedup** = expert-effort-h ÷ wall-clock-h (human ∪ agent active time, overlap-resolved).

- Per-task median **4.9×** (IQR 2.5–9.7, n=197). Above-floor pooled agrees at 4.5×.
- Comparator: single-task-performance research (METR, Anthropic per-session)

**Metric B — hands-on leverage** = expert-effort-h ÷ hands-on-h (your keyboard time).

- Per-task median **9.4×** (IQR 5.0–19.1, n=110)
- Comparator: throughput-multiplier research (PRs/week, LoC/week)

**Where each lands vs the literature:**

| Comparator                                 | Their number   | Bryan (n=1, measured, median)   |
| ------------------------------------------ | -------------- | ------------------------------- |
| Single-task speedup (METR / Anthropic)     | 1.2–2×         | **~5×** (4.9× median, Metric A) |
| Throughput multiplier (DX / daily-agentic) | +7.76% to +46% | **~9×** (9.4× median, Metric B) |

---

## 2. Cost framing (the David / ED hook)

- **Total API-equivalent cost (Anthropic list prices, 14 weeks Mar 2–Jun 10): $51,121**
- **Cost incl. Bryan@$100/hr: $61,084**
- **At Max subscription (~$200–700 across 14 weeks): marginal cost ≈ $0**
  - Using default model (Opus 4.8 1M) with no attempt to optimize costs
  - Could probably cut costs significantly (e.g. in 1/4 or less) if I spent more time optimizing

→ React: $51k is the headline cost-leverage number for non-technical audience. Land?

---

## 3. Per-type leverage (the "give AI the right tasks" slide — Hal / CTO hook)

Give some intuition into what task types are faster...

n=572 enriched tasks across 4 spot-checked weeks (Mar 23, Apr 27, May 11-25, Jun 1).

**Hands-on leverage by task type:**

| type          | n   | median    | IQR       |
| ------------- | --- | --------- | --------- |
| code          | 26  | **19.5×** | 5.8–47.9× |
| research      | 52  | 9.8×      | 6.1–22.9× |
| ops           | 36  | 7.8×      | 4.5–15.5× |
| documentation | 13  | 5.5×      | 3.1–10.4× |

**Wall-clock speedup by task type:**

| type          | n   | median | IQR       |
| ------------- | --- | ------ | --------- |
| ux design     | 8   | 14.2×  | 9.2–25.9× |
| code          | 59  | 9.4×   | 3.8–19.0× |
| research      | 83  | 6.0×   | 3.1–13.7× |
| documentation | 28  | 4.3×   | 2.6–9.7×  |
| ops           | 59  | 2.7×   | 1.1–5.7×  |

→ React: ux-design appearing at top of speedup is a thin n=8. Want me to drop it or footnote?

---

## 4. Anthropic 4-tier overlay (delegation-policy slide)

Same 572 tasks, tiered by spec clarity + size + design depth (operationalization in the full doc).

| Tier        | n         | hands-on leverage      | wall-clock speedup | delegation policy                                            |
| ----------- | --------- | ---------------------- | ------------------ | ------------------------------------------------------------ |
| trivial     | 123       | 3.6× (IQR 1.0–5.0×)    | **1.5×**           | **Just do them inline or batch** (dispatch overhead > work)  |
| routine     | 244 (43%) | 10.0× (IQR 6.1–22.2×)  | 6.9×               | **Volume play** — everyday agentic work                      |
| substantial | 66        | 16.1× (IQR 11.2–69.2×) | **19.1×**          | **Surprise winner when well-planned** — plan it, then dispatch |
| open-ended  | 139       | 13.4× (IQR 5.0–29.5×)  | 4.4×               | **Dispatch in background, don't wait** — slower per expert-hour |

Trivial-tier note (the "pong 10× slower" line is RETRACTED — see correction below):

> "We measured asking an agent to say 'pong': **10× SLOWER than typing it yourself.** est_h 3 min, wall-clock 30 min."

CORRECTION (2026-06-12): this example was RETRACTED — it's a segmentation artifact (trivial ask merged with unrelated voice-tests 27 min later; burst-stitching bridged the gap). There is NO 3-min hands-on floor — hands-on is pure typing time (pong measured 18 sec). Honest trivial-tier speedup ≈1.2× ("no gain, just do it inline"). The only real effect: agent dispatch overhead doesn't scale down to micro-tasks. Micro-task-merging fix filed for post-demo.

→ React: 4-tier story reads as 4 distinct delegation policies in one table. Lock?

---

## 5. The two stories for the blog (NOT in demo per RN)

These go to Writing Assistant for blog draft #4, not Hal/David's deck:

**Per-ticket gap story (the v3 fit signal):** CA-2435 (Forms) shows 5.78h keyboard hands-on (mine) vs 26h ownership time (CA peer) = **0.22 ratio**. Same shape for Maps (0.28h vs 30h). Confirms the construct split is real: my instrument measures keyboard attention; ownership-time includes off-keyboard cognition.

**R2-transcript-join instrument-fixing story:** Mar 23-30 had 73 product-engineer tasks with 0h hands-on because R2 PE transcripts weren't joined to their PRs. Pipeline fix shipped (commit `fa43b45`) — now 6 zero-hands-on tasks remain (92% recovery). Honest report of "we found an instrument bug; here's the before/after."

---

## 6. What's still SOFT (flagging for the Sunday lock)

- Mechanical validity checks
  - Check that tasks were segmented reasonablyNo two tasks cover the same work
  - Record task time spansCheck that within a single Claude Code sessionNot more than two tasks overlap any moment in time.(some minimal overlap is okay, especially when I'm multitasking within one session, but a lot needs review -- maybe it's okay, but I want it flagged)
  - Check that hours of estimated hands-on time for each week sum to roughly what I said i might be available for.  If it's off by more than 25%, then there might be an issue in the hands-on time
- 4 spot-checked weeks not item-by-item validated (will carry "spot-checked, awaiting full review" verbatim per RN)
- ~60 BC-* tickets in Tier B of the sweep-v2 doc lack epic-title disambiguation. To lift them to Tier A, RN recommended overnight wide-window enrichment (~5h LLM cost). **YOUR CALL** — want me to kick off tonight so it's ready by Sunday?
- `writing` type isn't in the enrichment vocabulary; blog/essay tasks get bucketed as "documentation." Known gap, not fixing before lock.
- Anthropic 4-tier thresholds are derived from the spec-clarity axis Anthropic implies — only "open-ended" is explicitly defined on their page. My operationalization is in the full doc; could shift if Anthropic publishes calibration data.

## Review Data Format and Decisions Supported

---

Part of this is to help with understanding how the work is going, and trends over time.

Metrics about how the task went

- Estimated effort of output = hours it would take an expert to do the task
- Wall clock time = total estimated active time for agent + human (resolving overlaps)
- Wall clock speedup = estimated effort of output / wall clock time
- Hands-on time = estimated active time for human(based on reading last prompt and then typing out next reply)
- Hands-on leverage = estimated effort of output / hands-on leverage
- Agent cost = summing up costs in the session transcript
- Human cost = hands-on time in hours * $100
- Cost per hour of output = estimated effort of output / (agent cost + human cost)
- Task succeeded = yes / no / paused(if we failed to get to the goal we wanted, success = no)
- For problems -- i.e. when there was a problem with the work and it had to be redone or improved
  - Hands-on time for problems
  - Agent cost for problems
  - Human cost for problems
  - Summary of problems -- text summary of what happened

Explanatory factors for these metrics include

- Task type (UX, Code, Research, Writing, Operations)
- Spec clarity (4-tier)
- Model(s) used
- Skills used (e.g. goal, workflow, subagent teams, etc. might increase costs)

Context information

- Brief task description
- Detailed by component breakdown for output effort estimate
- Project (e.g. CA, Health Coach, CRM, etc.)

Please organize this information at a task level and group into epic level -- and we should generally be reviewing correctness at an epic level.  But should be possible to dive into task level.

- I'd like to be able to run exploratory analyses on this data in Tableau (or other tool -- what would you recommend as the fastest, best tool to do exploratory analyses and then make them shareable.
- I would like to have a better UX for review that's based on this dataShould eventually be able to drill down from the summary epic / task metrics down to chat logs when needed
- I would like to share sanitized numbers publicly
  - Remove any private information like projects
  - Share things like how has my wall clock speedup and hands on leverage changed over time?
  - What ranges am I seeing by task type? or spec clarity?
  - How does it look by model -- are some models better or worse than others?

## Full doc + supporting reports

- [**Full CA presentation stats**](http://<your-review-host>:8787/review/ca-presentation-stats-2026-06-12)
- [**66-ticket sweep**](http://<your-review-host>:8787/review/ticket-sweep-v2-2026-06-12) 
- [**This headline cut (current)**](http://<your-review-host>:8787/review/flight-review-2026-06-12) 

Comment inline on anything; I'll iterate in-flight.
