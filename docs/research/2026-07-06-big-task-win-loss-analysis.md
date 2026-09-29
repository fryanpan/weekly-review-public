# Big-task win/loss analysis — what speeds us up, what slows us down

**Question:** among the harder/bigger tasks (est ≥ 2h), what separates the ones that went well (high productivity multiplier) from the ones that didn't — and what are the few top blockers and enablers?

**Data:** run-20260707T040227Z (Apr 13 – Jul 5, 2026), Family-assistant excluded, Bryan's 118 review verdicts applied. 143 big tasks with wall-clock data. Ranked by **wall-clock speedup** (est ÷ calendar time — chosen over hands-on leverage because it doesn't inherit the ±50% hands-on measurement band). Themes mined from the per-task `what_went_well` / `problems_encountered` enrichment fields (LLM-written from transcripts; spot-checked, not validated).

## The gap

|                          | median speedup | median est | dominant types               | dominant projects                                |
| ------------------------ | -------------- | ---------- | ---------------------------- | ------------------------------------------------ |
| **Top quartile (35)**    | **43×**        | 4.5h       | code 20, research 10         | live-feedback 7, notes-tool 7, health-tool 5 |
| **Bottom quartile (35)** | **3.5×**       | 3.0h       | code 12, docs 10, research 8 | **client-a 11**, ai-team-lead 5              |

A **12× spread** between the best and worst quartiles of comparable-size work. The wins aren't smaller tasks (median est is *higher* in the top quartile) — they're differently-shaped tasks.

## Top 3 blockers (by frequency in the bottom 35)

### 1. Wrong-direction runs needing human catch-and-redirect — 19/35 (54%)

The single biggest drag. The agent commits to a wrong interpretation and burns a round (or several) before Bryan catches it: the Gradle rabbit hole instead of the simple settings.gradle edit; truncating Bryan's nested PR notes to three bullets (content loss); site copy that missed the audience twice; a hallucinated Reddit community; bundling map tiles instead of using the configured host; leaking private client references into public commits. **The common thread: the agent had the wrong context or ignored existing rules, and nothing caught it before Bryan did.** (The CA agent ignoring release-only-testing/white-box rules is the archetype — Bryan had to diagnose the failure himself.)

### 2. Autonomy stalls & approval gaps — 9/35 (26%)

The inverse failure: the agent *stops* when it shouldn't. Paused mid-goal "waiting for approval" on an overnight run; the stop-hook firing before work was actually done or blocked; Bryan having to re-authorize the Sentry track mid-session; an autonomous loop that kept firing after Bryan said stop; a 6.5h silent stall; an API-key gap that halted a benchmark; the Mac Mini crashing overnight and losing state. These waste the cheapest resource we have (overnight/parallel agent time) and burn the most expensive one (Bryan noticing).

### 3. Non-converging visual/UI fix loops — 5/35 (plus env friction 6/35)

UI fixes verified without eyes: the dashboard still visually broken at session end after multiple rounds; Mermaid dark-mode taking three commits of progressively-more-specific CSS; the GPS button obscured by the nav bar; a chart "verified" via logcat with no screenshot. **When the agent can't see the result, iteration doesn't converge.** (Env/tooling friction — install failures, malformed tool calls, unreliable Gmail labels — is real but diffuse; no single fix.)

## Top 3 enablers (by frequency in the top 35)

### 1. Autonomous runs with minimal steering — 21/35 (60%)

The signature of nearly every win: the agent runs a long way without Bryan in the loop. "One 'keep going' message was enough to get from zero to a merged PR"; two check-ins across a half-day of research; terse directives ("m3 style everything") trusted and interpreted well.

### 2. One well-specified dispatch message — 7/35

The biggest wins started as a single consolidated message: the 60h-est multi-doc plugin from one well-specified message; the 28h watchOS logger from one well-scoped ask; the 50h MVP from a **two-sentence prompt**; the tile-selector fix where "Bryan consolidated all the UX and functional requirements into a single message." Upfront consolidation is what *enables* blocker #1's absence — the agent can't run in the wrong direction if the direction is fully specified.

### 3. Clean decomposition into reviewable PRs — 5/35 (+ fast concrete verification 2/35)

Wins decompose: commit-per-topic research, discrete PRs per step, root-cause fixes over symptom patches. And the fastest fix loops had **concrete verification**: live iPhone testing via Tailscale catching a regression immediately; benchmark-driven diagnosis of a routing regression. This is the mirror image of blocker #3.

## Where the losses concentrate

- **client-a is 31% of the bottom quartile** (11/35) vs 3 in the top. It combines all three blockers: an unfamiliar codebase with institutional rules the agents keep missing (#1), overnight/delegated runs that stall (#2), and Android UI work that's hard to verify remotely (#3). It's the highest-leverage place to apply fixes.
- **Writing/docs tasks sink to the bottom** (10/35 bottom vs 2/35 top) — audience-fit and voice need Bryan in the loop, so low multiplier is partly *intrinsic*, not failure. (Supports the pending `writing` type split, #86.)
- **ai-team-lead (5 bottom)** is mostly blocker #2 — it's the delegation infrastructure itself stalling.

## What I'd do about it (ranked by expected value)

1. **Attack wrong-direction runs with pre-flight context checks** — the CA rules-ignored failure suggests agents need institutional rules *surfaced in-context at dispatch*, not discoverable in repo docs. A dispatch template that pins constraints ("release-only testing, CoGo is white-box, fetch tiles from IIAB…") would have prevented ~4-5 of the bottom-35 outcomes.
2. **Fix the approval-gap stalls** — audit stop-hook conditions and autonomy grants for overnight runs; every stall wastes a whole overnight window. (Several bottom-35 tasks lost most of their calendar time to this.)
3. **Mandate screenshot-verified UI loops** — the `/ux-review` + `verify` tooling exists; the losses happened when it wasn't used. "No UI fix is done without an on-screen capture" would have converged the dashboard/Mermaid/GPS loops rounds earlier.

## Caveats

- Speedup = est ÷ wall-clock, so slow-calendar tasks (stalls, overnight gaps) score low even when eventual output was fine — that's intentional (calendar time is real), but it means blocker #2 partly *defines* rather than merely explains low scores.
- `what_went_well` / `problems_encountered` are LLM-written from transcripts; themes were keyword-tagged and hand-read (top+bottom 35 only, spot-checked).
- Est values on the biggest tasks are the least-reviewed (the 60h/50h builds carry `medium/high` uncertainty), so top-quartile magnitudes are softer than their rank order.
