# Review Data Model & Decisions Supported — DRAFT for co-editing

**Status:** draft · **Owner:** the user + weekly-review agent · **Created:** 2026-06-13 (lifted from the flight-review doc per the user's request, with agent additions marked ➕)

> **Superseded in part (2026-09-18).** The `hands_on_h` row below describes the per-message reading/typing model, replaced on 2026-08-17 by presence blocks. Current definitions: [METHOD.md](../../../METHOD.md).

Purpose: understand how work is going and trends over time. Organize at the **task** level, roll up to **epic** level. Review correctness at the epic level; drill down to task → chat logs when needed.

---

## Per-task metrics (how the task went)

| Field                   | Definition                                                   | Status                                                       |
| ----------------------- | ------------------------------------------------------------ | ------------------------------------------------------------ |
| `estimated_effort_h`    | Hours an expert would take to do the task (METR-50% baseline, no familiarity multiplier) | ✅ have (`est_human_hours`)                                   |
| `wall_clock_h`          | Total active time, agent + human, overlap-resolved (sweep-line union) | ✅ have (`wall_clock_hours`)                                  |
| `wall_clock_speedup`    | estimated_effort_h ÷ wall_clock_h                            | ✅ derived                                                    |
| `hands_on_h`            | Human active time = read-last-prompt @300wpm + type-reply @40wpm, cap 20min/turn, no floor | ✅ have (`active_hours`)                                      |
| `hands_on_leverage`     | estimated_effort_h ÷ hands_on_h                              | ✅ derived                                                    |
| `agent_cost_usd`        | Sum of token costs in the session transcript (tokens × model price; Anthropic gives tokens not $) | ⚠️ have at week level; need per-task attribution             |
| `human_cost_usd`        | hands_on_h × $100                                            | ✅ derived                                                    |
| `cost_per_output_hour`  | (agent_cost + human_cost) ÷ estimated_effort_h — see note ⚠️ below | ➕ proposed (formula corrected)                               |
| `task_succeeded`        | yes / no / paused (failed-to-reach-goal = no)                | ➕ NEW — not currently captured; needs enrichment field or your verdict |
| `nudge_count`           | Times you had to say "keep going" to a stalled agent (steering-friction signal) | ✅ have (computed in the worksheet generator)                 |
| `rework_hands_on_h`     | Hands-on time spent on problems/redo loops                   | ⚠️ partial (`rework.py` computes cluster-level; need per-task) |
| `rework_agent_cost_usd` | Agent cost attributable to redo loops                        | ➕ NEW                                                        |
| `rework_human_cost_usd` | rework_hands_on_h × $100                                     | ➕ derived once rework_hands_on exists                        |
| `rework_summary`        | Text: what went wrong and how it was redone                  | ✅ have (`problems_encountered`)                              |

## Explanatory factors

| Field          | Values                                                      | Status                                                       |
| -------------- | ----------------------------------------------------------- | ------------------------------------------------------------ |
| `task_type`    | UX / Code / Research / Writing / Operations                 | ⚠️ have, but `Writing` not yet in enrichment vocab (blog work mis-bucketed as documentation) — known gap |
| `spec_clarity` | 4-tier: trivial / routine / substantial / open-ended        | ✅ derived from uncertainty + est_h + depth (overlay)         |
| `models_used`  | which model(s) ran the task                                 | ➕ NEW — token scan has per-message model; need to attach to task. Enables your "are some models better/worse" analysis |
| `skills_used`  | goal / workflow / subagent-teams / etc. (may increase cost) | ➕ NEW — detectable from transcript (skill-invocation events); not yet extracted |

## Canonical definitions (this doc is the source of truth)

Code and prompts should reference this section, not re-derive.

### `uncertainty` (low / med / high) — in detail

`uncertainty` is **how confident we are in the ****`estimated_effort_h`**** number** — how wide the error bars are on that estimate. It is NOT about how hard or how big the task is. A 40-hour task with a clear spec can be low-uncertainty; a 30-minute task with a vague ask can be high-uncertainty.

**What "observable" means.** The hours estimate is built bottom-up from countables — new/modified components, new integrations, lines of code, test files, pages read/written. A countable is **observable** when the evidence directly contains it, so you read it off rather than guess:

- A merged PR carries metadata — additions, deletions, files changed, test files. The estimator sees "2,057 lines across 17 files, 3 new components" directly → observable.
- A commit diff shows what changed → observable.
- A doc with a word count → pages written is observable (1 page ≈ 500 words).

A countable is **inferred / guessed** when the evidence doesn't contain it:

- A conversation-only task with no PR/commit — the estimator infers "probably ~3 components touched" from discussion → inferred.
- Work shipped off-transcript (R2 agents, benchmark runs) where only the outcome is visible, not the size → guessed.
- Novel work with no precedent — even an expert can't predict the hours tightly.

**The three levels** (spread of plausible est_h):

| Level      | When                                                         | est_h spread |
| ---------- | ------------------------------------------------------------ | ------------ |
| **low**    | Countables directly observable (PR metadata present, file/line/test counts visible) AND a known repeatable pattern. | < ±25%       |
| **medium** | Some countables observable, others inferred; OR a known pattern with a novel piece. | ±25–60%      |
| **high**   | Countables mostly guessed (no PR metadata, unclear scope, off-transcript) OR genuinely novel work (no precedent). | > ±60%       |

**Worked examples:**

- "Merged PR #161: pluggable map engine, 2,057 additions / 17 files, +3 backends" → **low** (all observable, conventional refactor).
- "Scoped six parallel workstreams: forms/maps context, camera fix, QA…" → **high** (conversation-only, no artifact metadata, scope spans many half-defined threads).
- "Drafted Forms Benchmark v4 report (~5 pages)" → **medium** (page count observable, but synthesis-vs-transcription is inferred).

**Status:** NOT yet enforced in the enrichment prompt — the LLM currently emits low/med/high by judgment without this rubric. Wiring it into the prompt is a one-shot change pending the user's ok; it matters because `uncertainty` feeds the `spec_clarity` tier below.

### `spec_clarity` (4-tier: trivial / routine / substantial / open-ended)

Anthropic's complexity tiering (recursive-self-improvement page), operationalized on the spec-clarity axis. The page explicitly defines only **open-ended** ("problems with no clear specification, where the engineer isn't sure what the answer looks like"); the other three are inferred.

- **trivial** — `uncertainty`=low AND `estimated_effort_h` < 1h AND depth=surface (known solution, near-zero design judgment).
- **routine** — `uncertainty`=low AND est ≤ 8h, OR `uncertainty`=medium AND est ≤ 4h (known shape, standard pattern).
- **substantial** — `uncertainty`=medium AND est 4–40h AND depth ∈ {careful design, novel} (known goal, plannable, real design work).
- **open-ended** — `uncertainty`=high (any size), OR depth=novel AND est > 8h, OR est > 40h (no clear spec, solution shape unclear).

## Context

| Field                                                        | Status                      |
| ------------------------------------------------------------ | --------------------------- |
| goal (self-contained why) + brief_description (goal-framed title) + summary (what got done) | ✅ have                      |
| `component_breakdown` (the countables behind the estimate)   | ✅ have (`effort_breakdown`) |
| **project** (client-a / health-tool / contacts-tool / …)     | ✅ have                      |

## ➕ Agent-proposed additions (for trends + data quality)

- `week` / `date` — ISO week of completion (you need this for "how has speedup changed over time")
- `uncertainty` (low/med/high) — the estimate-confidence field; also feeds the `spec_clarity` tier (rubric being defined)
- `bryan_domain_expertise` (expert/competent/learning) — confounder annotation; lets you separate "slow because learning" from "slow because hard"
- `resumptions` / `context_switches` — already computed; useful for "fragmented week" detection
- `n_user_messages` / `n_agent_events` — volume, for sanity-checking attribution
- `review_status` — unreviewed / spot-checked / validated (per your rule: validated only when you've gone item-by-item) + your verdict (OK/HIGH/LOW) + `corrected_hours` + note
- `run_id` / provenance — which pipeline run produced this row (for reproducibility + diffing runs over time)

## Decisions this data should support

1. **Trends over time** — wall_clock_speedup and hands_on_leverage by week; are they rising?
2. **Where agents help most** — ranges by task_type and by spec_clarity tier (the delegation-policy view)
3. **Model economics** — speedup/leverage/cost by model; is Opus worth it vs Sonnet for routine work?
4. **Skill ROI** — do goal/workflow/subagent-team skills increase cost faster than output?
5. **Rework drag** — how much hands-on + cost goes to redo loops, by project/type
6. **Public sharing** — sanitized: strip project names, share speedup/leverage trends + by-type/by-tier/by-model ranges

## Tooling recommendation (reviewed with Blog Assistant 2026-06-13)

The data is small (hundreds–low-thousands of task-rows). Split by purpose:

1. **Denormalize to one flat fact table.** Add a pipeline step that writes `task_facts.parquet` (one row per task, all columns above) + `epic_facts.parquet` (rolled up). This is the substrate everything reads. ~1hr to build.
2. **Internal exploration (fast):** **DuckDB** — query the parquet directly with SQL, zero setup. Or **Metabase** (self-host, point at the parquet/sqlite) for click-to-chart without SQL. Both beat Tableau for speed-to-first-chart at this size, no license friction.
3. **Public charts — use the user's existing blog stack, NOT a second tool** (Blog Assistant's consistency call): the blog already standardized (2026-05-25) on **Observable Plot** (simple charts, via `Chart.astro`) + **Vega-Lite** (faceted/small-multiples, via `SmallMultiples.astro`), build-time SVG, authored in `.mdx`. If the sanitized public charts land in the weekly-review blog post (Blog Assistant's task #13), author them with these wrappers so they match the site and reuse the honest-axis infra. **Standing up Evidence.dev for the public cut would fork the site into a second charting stack with a different look — steer against it for the blog.** Evidence.dev is only worth it if the user wants a *separate standalone interactive data-explorer site* (a second public surface to maintain — explicit tradeoff, not a default).

### Charting conventions to honor (from the blog's decisions)

- **Length, not color, for quantitative comparison** (Bertin) — by-type/tier/model → bars, not a heatmap.
- **Honest axis.** Speedup (×) spans an order of magnitude (1.2×→13×) — use a log axis, or linear with the **1× baseline explicitly marked**, so trivial-tier doesn't look like zero.
- **Show the spread, not just the point** — plot **median + IQR** (interval mark) with **n per category** annotated. Collapsing "IQR 2.5–9.7 → 4.9×" is exactly the overclaim the post argues against. (Blog Assistant is adding an interval/errorbar mark to the wrappers for the blog charts; if our public cut targets the blog, that work lands once and we inherit it.)
- **Accessibility** — text labels baked into the SVG; never color-alone encoding (pair with position/label).

**Drill-down to chat logs:** every task row keeps its `task_id`; the pipeline already stores per-task events in `run.sqlite` (`task_events` → `events`). A "view chat log" link in the review UX is a join away — we can wire it when we build the review UX you described.

---

*Co-edit zone — for the user: mark anything wrong/missing and we'll iterate.*
