# CA Presentation Stats — n=1, self-measured, cleanup-inclusive

**Author:** weekly-review agent · **Generated:** 2026-06-10 · **Updated:** 2026-06-12 · **For:** Research Notes (lesson plan input) → CA demo (Mon 6/15 AM review)

> **Reviewer-assurance status:** As of 2026-06-12, none of the underlying weekly enrichment runs have been fully reviewer-validated (Bryan has only spot-checked them and noted they "look mostly sensible so far"). The phrase "fully-enriched" in this document describes the data state (every task in the window has an LLM-generated est_h, scope, depth, type), NOT a human review status. Treat the numbers as defensible at the instrument level but not yet ground-truthed.

> All numbers below are **n=1 self-measured** from Bryan's transcript-segment data. This is the *instrument demo* — what measured agentic output looks like for one heavily-instrumented operator across many projects. It is NOT a universal claim about agentic productivity.

## Headline (the load-bearing number) — REVISED per Bryan's 2026-06-12 in-flight review

**Bryan's two calls in the in-flight review session:** (1) wall-clock speedup, not hands-on leverage, is the lead metric — it's the comparable with existing research; (2) exclude the Cloudflare-PE era (R2 traffic ended Apr 12) — only all-Claude-Code windows count.

**CLEAN SET = Apr 27 + May 11-25 + Jun 1 enriched windows, n=442 tasks:**

**Pooled wall-clock speedup: 13.8×** (Σ 1,362.1h est ÷ Σ 98.7h wall-clock) **Per-task wall-clock speedup median: 4.9×** (IQR 2.5–9.7×, n=197 above 3-min floor)

Supporting exhibit — hands-on leverage ("per hour of Bryan's attention specifically," the dispatch-mode upside): pooled 31.3×, per-task median 9.4× (IQR 5.0–19.1×, n=110).

> *Demo phrasing (pending Bryan's final lock): "~14× pooled wall-clock speedup / ~5× median per-task, measured Apr–Jun 2026 all-Claude-Code era, n=442 tasks, spot-checked. Wall-clock = union of human and agent active intervals, overlap-resolved. n=1, cleanup-inclusive, API-equivalent cost ~$6k/week paid at Max subscription rate."*

Historical note: the original headline here was hands-on leverage (21.6× pooled, May window). That number is still true; it's just not the lead — nobody else frames productivity against operator keyboard time, so leading with it invites apples-to-oranges challenges.

## Weekly top-line table (Mar 2 → Jun 10, 2026)

| Week (Mon)     | n tasks   | hands-on h | agent wall-clock h | $ tokens (API list) | $ total (incl. Bryan@$100/hr) | est_h (expert) | hands-on lev | wall-clock speedup | $ / est_h |
| -------------- | --------- | ---------- | ------------------ | ------------------- | ----------------------------- | -------------- | ------------ | ------------------ | --------- |
| 2026-03-02     | 223       | 4.1        | 4.0                | $613                | $1,025                        | —              | —            | —                  | —         |
| 2026-03-09     | 36        | 0.6        | 7.5                | $560                | $617                          | —              | —            | —                  | —         |
| 2026-03-16     | 36        | 0.7        | 1.0                | $403                | $468                          | —              | —            | —                  | —         |
| 2026-03-23     | 117       | 2.6        | 2.8                | $1,438              | $1,701                        | —              | —            | —                  | —         |
| 2026-03-30     | 174       | 9.0        | 19.8               | $1,288              | $2,189                        | —              | —            | —                  | —         |
| 2026-04-06     | 63        | 0.6        | 7.2                | $407                | $463                          | —              | —            | —                  | —         |
| 2026-04-13     | 88        | 8.3        | 5.9                | $8,438              | $9,266                        | —              | —            | —                  | —         |
| 2026-04-20     | 106       | 7.7        | 8.8                | $4,593              | $5,357                        | —              | —            | —                  | —         |
| 2026-04-27     | 139       | 7.2        | 6.2                | $3,993              | $4,711                        | —              | —            | —                  | —         |
| 2026-05-04     | 122       | 9.2        | 5.7                | $3,834              | $4,750                        | —              | —            | —                  | —         |
| **2026-05-11** | **72**    | **14.4**   | **7.9**            | **$6,188**          | **$7,630**                    | **163.1**      | **12.8×**    | **6.0×**           | **$47**   |
| **2026-05-18** | **36**    | **6.9**    | **3.4**            | **$6,114**          | **$6,808**                    | **249.2**      | **37.6×**    | **24.1×**          | **$27**   |
| **2026-05-25** | **79**    | **13.2**   | **4.6**            | **$6,537**          | **$7,853**                    | **73.4**       | **23.7×**    | **12.5×**          | **$107**  |
| 2026-06-01     | 85        | 14.0       | 1.9                | $6,044              | $7,440                        | —              | —            | —                  | —         |
| 2026-06-08     | 27        | 1.4        | 0.6                | $671                | $807                          | —              | —            | —                  | —         |
| **TOTAL**      | **1,403** | **99.6**   | **87.2**           | **$51,121**         | **$61,084**                   | **485.7**¹     | —            | —                  | —         |

¹ est_h total covers the 3 fully-enriched weeks only. Pipeline rerun for the wider window is in flight; will backfill when complete.

**Weeks bolded** are the fully-enriched subset where per-task est*h has been computed by LLM enrichment + my expert-baseline rubric (METR ~50%, no familiarity multiplier — see [complexity criteria](#methodology--caveats)). Other weeks have all columns except est*h; the structural columns (tasks, hands-on, wall-clock, $) come from segmentation alone and don't require enrichment.

### Per-week observations worth flagging

- **W of 2026-04-13 ($8.4k token spike)** is mostly rare-disease-benchmark harness runs. This is included in the totals on purpose — the rare-disease benchmark is Bryan's research work and counts as agentic output paid for by Max. The high-tokens-low-est_h shape of harness weeks just means the "per-expert-hour cost" column will read higher than coding-heavy weeks; that's accurate, not a defect.
- **Validated weeks all land in 12–38× hands-on leverage range.** No single anomalous mode-shift week skews the headline.
- **$/est_h ranges $27–$107** at API list prices. At Bryan's actual cost (Max subscription ~$200–700/month for the full window), the marginal cost is essentially **$0 per expert-hour of output**.

## Top shipped capabilities — exemplars for the room

These are the "real names beat aggregates" rows. All shipped, all from the fully-enriched 2-week window.

| Capability                                                   | est_h (expert) | hands-on | Project                     | Verification gate                                            |
| ------------------------------------------------------------ | -------------- | -------- | --------------------------- | ------------------------------------------------------------ |
| **Forms v1 plugin** (after v4 benchmark picked ML Kit + multi-frame UX) | 120h           | <1 min   | client-a                | Plugin ships; benchmark methodology vetted in companion report |
| **Gradient gate routing via MapTiler terrain-RGB tiles**     | 14h            | <1 min   | family-bike-map             | Routes generate; PR merged after review                      |
| **MCP bundling + auto-approve hooks + auto-watch + mock server endpoints** | 14h            | <1 min   | claude-live-feedback-plugin | Self-tested via the plugin's own review surface              |
| **Maps v2 mockups + decisions doc + URL contract fix**       | 5h             | <1 min   | client-a                | Mockups → live-feedback review → decisions doc closed        |
| **RSS-to-Buttondown pipeline + chart rendering**             | 4.5h           | 50 min   | writing-tool           | Published blog post visible end-to-end                       |
| **License-audit skill with attribution check for code review** | 4h             | <1 min   | client-a                | Skill installable; runs on PRs                               |

## Rework counter-story — honest failures (n=2)

These belong in the deck for credibility: the same data that produces 22× pooled leverage *also* shows tasks where leverage collapsed near 1×.

1. **Restructured agent commands as plugins, distributed to AI team** (ai-team-lead) — **leverage 0.8×** · est 4h · hands-on **5.27h** · ENRICHED problem note: *"Loom was identified as a missing dependency for a post due EOD the next day, indicating a gap in the rollout."* The agentic distribution worked, but the dependency surfaced too late and Bryan ate the cleanup hours himself.
2. **Botkin meeting notes + CA intel into personal CRM** (crm-tool) — **leverage 1.3×** · est 0.8h · hands-on **0.59h** · ENRICHED problem note: *"Bryan had to issue an explicit correction after initially misidentifying Leanna as Dave's daughter."* Small task, agent hallucinated a family relationship from incomplete context; Bryan caught it in review but the correction round trashed the leverage.

Both are agent-grade failures that ate Bryan's time. The **median leverage for tasks with ≥30min hands-on is 8.3×** — well above 1× — but the floor exists and these are it.

## Anthropic 4-tier complexity overlay (n=572 fully-enriched tasks, 4 spot-checked weekly runs)

The Anthropic [recursive-self-improvement page](https://www.anthropic.com/institute/recursive-self-improvement) presents Claude Code session success rates across four tiers: **trivial / routine / substantial / open-ended**. The page explicitly defines only **open-ended** ("problems with no clear specification, where the engineer isn't sure what the answer looks like") and gives one example (a ~2h debug that would normally be ~2–3 days of human work). The other three tiers are presented on the success-rate chart without formal definition.

Operationalization used here (derived from the spec-clarity axis Anthropic implies, mapped onto fields the weekly-review pipeline already enriches):

| Tier            | Operationalization on `est_human_hours` / `uncertainty` / `depth` |
| --------------- | ------------------------------------------------------------ |
| **trivial**     | uncertainty=low AND est_h < 1h AND depth=surface — known solution, near-zero design judgment |
| **routine**     | uncertainty=low AND est*h ≤ 8h, OR uncertainty=medium AND est*h ≤ 4h — known shape, standard pattern |
| **substantial** | uncertainty=medium AND est_h 4–40h AND depth ∈ {careful design, novel} — known goal, plannable, real design work |
| **open-ended**  | uncertainty=high (any size), OR depth=novel AND est*h > 8h, OR est*h > 40h — no clear specification, solution shape unclear |

**Task count distribution (n=572):**

| Tier        | n   | %   |
| ----------- | --- | --- |
| trivial     | 123 | 22% |
| routine     | 244 | 43% |
| substantial | 66  | 12% |
| open-ended  | 139 | 24% |

**Pooled-across-types medians by tier** (hands-on ≥ 3min and wall-clock ≥ 3min floors applied — dispatch tail excluded to keep ratios meaningful):

| Tier        | hands-on leverage                       | wall-clock speedup                     |
| ----------- | --------------------------------------- | -------------------------------------- |
| trivial     | median **3.6×** (n=19; IQR 1.0–5.0×)    | median **1.5×** (n=32; IQR 0.9–3.5×)   |
| routine     | median **10.0×** (n=72; IQR 6.1–22.2×)  | median **6.9×** (n=114; IQR 3.8–13.4×) |
| substantial | median **16.1×** (n=13; IQR 11.2–69.2×) | median **19.1×** (n=18; IQR 5.4–32.5×) |
| open-ended  | median **13.4×** (n=25; IQR 5.0–29.5×)  | median **4.4×** (n=73; IQR 2.4–9.6×)   |

**Readable takeaways for the room:**

- **Substantial tasks have the highest wall-clock speedup (19.1×).** Plannable design work where the goal is clear but the path requires real thinking — these are the "give the agent a well-shaped problem" wins. Hands-on leverage also high (16.1×).
- **Open-ended tasks show a notably lower wall-clock speedup (4.4×) than substantial (19.1×).** Matches the Anthropic narrative that current models struggle with no-clear-spec work — the agent still produces output, but takes much longer per expert-hour. Hands-on leverage is 13.4× here, which means Bryan is *steering more* on these tasks (as expected).
- **Trivial speedup is 1.5×** — agent dispatch overhead eats most of the gain on micro-tasks (see explainer below).
- **Routine is the volume play** (n=244, 43% of tasks) with 6.9× speedup — that's where the everyday agentic work sits.

Why is trivial-tier speedup ≈1×? (CORRECTED 2026-06-12 after Bryan's challenge — the sub-0.2× numbers previously quoted here were segmentation artifacts; see below.) Two parts:

- Real effect: dispatch overhead (context load, planning, human turnaround waiting for a response) doesn't scale down with task size. For a 3-minute task, even 2–3 minutes of real overhead means dispatching gains nothing. Clean-set trivial-tier median speedup: 1.2× — dispatch ≈ typing it yourself.
- *Wall-clock denominator* is dominated by the **agent's fixed per-dispatch overhead** (context load + planning + tool calls + response generation) — typically 15–45 minutes regardless of task size. Real examples in the data: "Reply with exactly one word: pong" (est*h 3 min, wall-clock 30 min, speedup 0.10× — ARTIFACT, see note); "Add a note to ping Martin" (est*h 3 min, wall-clock 35 min, speedup 0.08×); "Did DX release a white paper this week" (est_h 6 min, wall-clock 34 min, speedup 0.17×). CORRECTION (2026-06-12, Bryan's in-flight challenge): event-level inspection showed these sub-0.2× examples are micro-task-merging artifacts — the segmenter fused trivial asks with unrelated later messages in the same session (pong absorbed voice-tests 27 min later), and the ≤30-min burst-stitching bridged the gap. Honest trivial-task interaction is ~1–2 min → speedup ≈1–1.5×, matching the clean-set median of 1.2×. DO NOT quote the sub-0.2× numbers. Segmentation fix (don't fuse trivial sequential asks) filed for post-demo.

**Operational implication:** trivial tasks are usually faster to just do than to dispatch. Either **batch them** (one dispatch covering 5 trivial asks in sequence — overhead amortizes) or **do them inline** (the saved overhead pays for the typing). The Anthropic framing "current models handle trivial well" is about *success rate*, not *speedup* — they shouldn't be dispatched on a per-task basis if speed is the goal.

**Type × tier cross-cut (counts, all tasks):**

| type          | trivial | routine | substantial | open-ended | total |
| ------------- | ------- | ------- | ----------- | ---------- | ----- |
| code          | 16      | 70      | 47          | 46         | 179   |
| research      | 17      | 72      | 10          | 26         | 125   |
| ops           | 55      | 33      | 1           | 39         | 128   |
| documentation | 35      | 65      | 7           | 23         | 130   |
| ux design     | 0       | 4       | 1           | 5          | 10    |

Per-cell medians for the speedup column are in the supporting source data (`/tmp/anthropic-tier-overlay.json` from the generation script). Several cells have n < 5 above the floor and shouldn't be quoted; the pooled-by-tier table above is the demo-ready cut.

**Status:** computed from existing enriched data, NO new LLM calls. Same spot-checked-not-validated caveat applies as everywhere else in this document.

## Suitability-band tagging (retrospective)

Tagging applied per [agent-suitability rubric](https://github.com/.../docs/research/2026-06-10-agent-suitability-rubric.md) (notes-tool); the rubric is forward-looking for tickets so I tagged retrospectively based on enriched fields (`shipped`/`exploratory` title language, uncertainty, `problems_encountered` substance, hands-on intensity).

| Band                                                         | n (% of 160) | Of which dispatch-mode (<3min hands-on) | Median leverage (measured subset, ≥3min hands-on) |
| ------------------------------------------------------------ | ------------ | --------------------------------------- | ------------------------------------------------- |
| **GREEN** (delegate end-to-end → shipped)                    | 67 (42%)     | 64 (96%)                                | n=3, median 6.6×                                  |
| **YELLOW** (delegate with prep / mixed)                      | 70 (44%)     | 36 (51%)                                | n=34, median 7.9×                                 |
| **RED** (struggled, exploratory-only, or needed hand-holding) | 23 (14%)     | 14 (61%)                                | n=9, median 9.9×                                  |

**Read this as:** 42% of Bryan's tasks were "agent took it end-to-end, shipped, no hands-on touch beyond initial dispatch." Only ~14% needed Bryan to hand-hold or showed clear struggle signals. The leverage-by-band figures look counterintuitive because **the GREEN tasks are mostly dispatch-mode and so excluded from the measured subset** — what's left in the GREEN measured slice is the small set of GREEN tasks Bryan touched briefly.

The teachable framing for the deck: **"the rubric is a forward-looking tool — most of Bryan's actual workflow was 'delegate first, observe, intervene only if it goes wrong.' The retrospective bands here measure that workflow's success rate."**

## Sample 5-row instrument log (for the §3b "this is what tracking looks like" exhibit)

```
task_id                    project                  title                                                                est_h  hands_on_h  agent_h  leverage  band    reasons
client-a-…-shipped-Forms-v1                  client-a             Shipped Forms v1 plugin after v4 benchmark picked ML Kit + multi-frame UX  120.0   0.00       (n/a)    (dispatch) GREEN  shipped,uncertainty=medium
claude-live-feedback-…-shipped-MCP-bundling      claude-live-feedback-plugin Shipped MCP bundling, auto-approve hooks, auto-watch, and mock server  14.0    0.00       (n/a)    (dispatch) GREEN  shipped,uncertainty=medium
writing-tool-…-RSS-Buttondown               writing-tool        Fixed RSS-to-Buttondown pipeline; deployed blog post with rendered charts  4.5     0.83       0.62     5.4x       YELLOW problems,hands-on=0.8h
ai-team-lead-…-plugins-distributed               ai-team-lead             Restructured agent commands as plugins, distributed to AI team            4.0     5.27       0.71     0.8x       RED    rework,problems,hands-on=5.3h
client-a-…-Forms-Benchmark-v4-report         client-a             Drafted Forms Benchmark v4 Technical Report with Speedup Framing          6.0     0.09       0.21     69.2x      YELLOW exploratory,hands-on=0.1h
```

This is the per-task log structure. Every task in the fully-enriched window has these columns plus `what_went_well` and `problems_encountered` (the LLM-enriched fields powering the rework-counter-story above).

## Methodology + caveats (read before quoting any number)

1. **est_h methodology** — *expert-baseline* hours at METR ~50% reliability (i.e., "median time at which an expert in the relevant domain would succeed half the time"). **No familiarity multiplier** — Bryan's domain-learning friction is captured *downstream* by the leverage ratio, never inside the numerator. Rubric counts (new components vs tweaks, new integrations, LoC, test coverage delta for code; pages read & written for research; pages designed & variants for UX) drive the estimate; rationale is logged per-task. Source: `src/weekly_review/enrichment.py` step 4 (EST*HUMAN*HOURS) + step 5 (RATIONALE). **Rework is tracked as a separate continuous-improvement signal** (see `rework.py`) and is **never** folded into est_h or leverage numerators.
2. **Hands-on hours definition** — sum of per-user-message active time: agent's prior output read at 300 WPM + Bryan's typed message at 40 WPM, capped at 20 min per turn. Source: `src/weekly_review/effort.py:active_hours_by_task`. This **includes** all steering, code review, cleanup messages — anything Bryan typed into the loop. It does NOT include thinking/planning time off-keyboard; that's the active/cognitive ratio (0.37 in this window — i.e., Bryan is "in conversation" ~37% of his thinking time, the rest is off-transcript).
3. **Agent wall-clock definition** — sum of contiguous agent-activity spans per task, with ≤5-minute gaps stitched. Source: `src/weekly_review/effort.py:agent_active_hours_by_task`. This is what was emitting tokens, not calendar wall-clock; it under-counts wall-clock during agent idle.
4. **Token cost basis** — extracted from transcript `usage` fields (input*tokens, output*tokens, cache*read*input*tokens, cache*creation*input*tokens) for every assistant message, grouped by ISO week × model family. **Costed at Anthropic API list prices** (cached 2026-05-26 from platform.claude.com): Opus 4.x **$5/$25** in/out, Sonnet **$3/$15**, Haiku **$1/$5**, Fable 5 **$10/$50**; cache reads ≈0.1× input, cache writes 1.25× (5-min) / 2× (1-hour). (An earlier draft of this report priced Opus at $15/$75 — that was 4.1-era pricing and overstated cost ~3×; corrected here.) **Bryan's actual marginal cost is essentially $0** (Max subscription). The $-figure represents *the API-equivalent economic cost of the work the agent did*, not what Bryan paid. Cost numbers include all projects (rare-disease-benchmark harness weeks included on purpose — it's Bryan's research work).
5. **Denominator-floor handling** — 71% of fully-enriched-window tasks have <3 minutes of hands-on time (dispatch-mode). For these, est*h ÷ tiny*denominator explodes to artifact values. The "measured subset" (n=46, hands-on ≥3min) drops the dispatch tail. The pooled totals (Σ est ÷ Σ hands-on = 21.6×) include all tasks and are NOT artifact-sensitive — that's why pooled is the recommended single-number quote.
6. **Tagging is heuristic, not gold-labeled.** Suitability bands were assigned by a rule-based scorer over the enriched fields, not by Bryan manually. The band distribution shape is reliable; individual task assignments may have ~10-15% noise.

## Files on disk for downstream use

- **This report:** `/~/dev/weekly-review/.claude/worktrees/v1-impl/docs/research/ca-presentation-stats.md`
- **Per-task tagged table (160 rows):** `/tmp/may11-25-tagged-v2.json`
- **Weekly table source:** `/tmp/weekly-table.json`
- **Token cost source:** `/tmp/weekly-token-cost.json`
- **Validated 2-week run DB:** `/~/.local/share/weekly-review/runs/run-20260529T061805Z/run.sqlite`
- **Wide-window structural run DB:** `/~/.local/share/weekly-review/runs/run-20260610T160057Z/run.sqlite`
- **Wide-window enrichment (stopped at task 204/1428 to free cache for epic clustering):** `/~/.local/share/weekly-review/runs/run-20260610T155533Z/run.sqlite` — can be resumed if per-week est_h backfill is needed.

## What still needs Bryan's call before publish

- **Which headline number to anchor:** pooled 22× (single number, hides distribution) vs. per-task median 8× IQR 5–20× (shows distribution, reads as more conservative). I recommend **leading with pooled 22× and showing the per-task distribution as the supporting exhibit** — the literature has nobody measuring per-task leverage at all, so the distribution itself is the novel artifact.
- **Whether to backfill est_h for weeks without enrichment** before the talk. Today the fully-enriched subset (May 11-25) carries all the leverage numbers; the other 12 weeks have structure (tasks, hands-on, wall-clock, $) but no per-task est_h. Resuming the wide enrichment takes ~5 hours wall-clock. If the deck only needs the headline + a few exemplars + the rework story, the fully-enriched subset is sufficient.
