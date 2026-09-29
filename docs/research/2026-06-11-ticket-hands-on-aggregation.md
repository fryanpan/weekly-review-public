# Per-ticket hands-on aggregation (for RN v3 rubric fit)

Generated: 2026-06-11 · Source: weekly-review v1-impl fully-enriched + structural runs (Mar 2 → Jun 10, 2026)

**Unit:** Σ Bryan-hands-on hours across all transcript segments mapped to the ticket. Hands-on = modeled reading@300 WPM + typing@40 WPM per user-message turn, capped 20 min/turn. **NOT** off-keyboard time (planning in head, calls, Slack DMs outside Claude Code, in-person discussion, walking-around thinking).

## Gap vs CA peer's whole-ticket estimates

| Ticket | My Σ hands-on (transcript-measured) | CA peer's whole-ticket estimate | Gap | Likely explanation |
|---|---:|---:|---:|---|
| CA-2433 (xkcd) | **1.49h** | 4.5h | −3.0h | Most of the xkcd work happened outside the transcripts I ingested — likely the mockup/setup phase before benchmark week. |
| CA-2435 (Forms) | **4.95h** | 26h | **−21h** | Forms involved heavy off-keyboard work: model evaluation reading, benchmark design deliberation, deciding ML Kit vs alternatives. None captured by the reading+typing model. |
| CA-2436 (Maps) | **0.28h** | 30h | **−29.7h** | Maps work was overwhelmingly dispatch + agent runs; my hands-on is microscopic because Bryan's intervention pattern was "dispatch via Slack → agents shipped → review PR." Whole-ticket time includes design/decisions that didn't show as Claude messages. |

**Read this as the v3 fit signal RN asked for:** the rubric should account for the systematic gap between modeled-keyboard-hands-on (mine) and lived-hands-on-end-to-end (peer's estimates). My number measures **transcript-attributed attention**, not **end-to-end ownership effort**. Both are real; the gap is the v3 calibration target.

## Headline table

| Ticket | n_tasks | n_enriched | n_with_user_msgs | hands-on h | wall-clock h | agent-busy h | est_h sum | first activity | last activity | projects touched |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| **CA-2433** (xkcd plugin) | 2 | 0 | 1 | **1.49** | 0 | 0 | 0 | 2026-05-06 | 2026-05-14 | client-a |
| **CA-2435** (Forms plugin (OCR/benchmark/ship)) | 9 | 8 | 6 | **4.95** | 4.73 | 0.7 | 163.0 | 2026-05-07 | 2026-05-20 | client-a |
| **CA-2436** (Maps / GIS plugin) | 6 | 6 | 2 | **0.28** | 0.87 | 0.52 | 34.5 | 2026-05-14 | 2026-05-22 | client-a |

## Per-ticket detail

### CA-2433 — xkcd plugin

- **Σ hands-on:** 1.49h (2 segments, 0 enriched, 1 with Claude user messages)
- **Σ wall-clock:** 0h (Bryan ∪ agent intervals)
- **Σ agent busy:** 0h (tool-latency-inclusive)
- **Σ est_h:** 0h (expert-baseline, enriched subset only)
- **Span:** 2026-05-06 → 2026-05-14
- **Projects touched:** client-a
- **Match sources:** {'issue_refs+topic': 1, 'issue_refs': 1}

**Top 8 segments by est_h:**

| segment task_id | project | title | est_h | hands-on | wall-clock | match |
|---|---|---|---:|---:|---:|---|
| `636bb158d2` | client-a | http://localhost:8090/CA-2433-xkcd-mockup.html | — | 1.49 | 0.00 | issue_refs+topic |
| `ab4bb1343d` | client-a | CA-2433/2435/2436: design plans + interactive mockups + plugin refer | — | 0.00 | 0.00 | issue_refs |

### CA-2435 — Forms plugin (OCR/benchmark/ship)

- **Σ hands-on:** 4.95h (9 segments, 8 enriched, 6 with Claude user messages)
- **Σ wall-clock:** 4.73h (Bryan ∪ agent intervals)
- **Σ agent busy:** 0.7h (tool-latency-inclusive)
- **Σ est_h:** 163.0h (expert-baseline, enriched subset only)
- **Span:** 2026-05-07 → 2026-05-20
- **Projects touched:** client-a
- **Match sources:** {'issue_refs+topic': 3, 'epic': 6}

**Top 8 segments by est_h:**

| segment task_id | project | title | est_h | hands-on | wall-clock | match |
|---|---|---|---:|---:|---:|---|
| `d74f8cbb88` | client-a | Shipped Forms v1 plugin + production decision after 15-approach v4 ben | 120.0 | 0.00 | 0.00 | epic:Ship Forms Plugin with OCR and Benchmark |
| `3921c8bb25` | client-a | Debugged Forms Plugin Flows and Ran Multi-Model Benchmark Loop | 18.0 | 1.41 | 1.91 | epic:Ship Forms Plugin with OCR and Benchmark |
| `fa9c702be1` | client-a | Drafted Forms Benchmark v4 Final Report with Speedup Framing | 6.0 | 0.09 | 0.49 | epic:Ship Forms Plugin with OCR and Benchmark |
| `b03ac168e5` | client-a | Scoped CA-2435 fix and evaluated Kaspresso adoption for client-a-app | 5.0 | 1.00 | 1.18 | issue_refs+topic |
| `60bb951b63` | client-a | Pushed branch to main, researched vision models, drafted benchmark rep | 4.5 | 0.40 | 0.92 | epic:Ship Forms Plugin with OCR and Benchmark |
| `18f6b07fa5` | client-a | Iterated step-2 UX, button styles, and drafted plugin-crash Slack mess | 3.5 | 0.17 | 0.23 | epic:Ship Forms Plugin with OCR and Benchmark |
| `ca6f70d098` | client-a | Merged CA-2435 docs: forms architecture decision + CV-image-to-xml d | 3.0 | 0.00 | 0.00 | issue_refs+topic |
| `ab6dbb8af7` | client-a | Shipped v4 benchmark with TFLite exports, augmented data, and gitignor | 3.0 | 0.00 | 0.00 | epic:Ship Forms Plugin with OCR and Benchmark |

### CA-2436 — Maps / GIS plugin

- **Σ hands-on:** 0.28h (6 segments, 6 enriched, 2 with Claude user messages)
- **Σ wall-clock:** 0.87h (Bryan ∪ agent intervals)
- **Σ agent busy:** 0.52h (tool-latency-inclusive)
- **Σ est_h:** 34.5h (expert-baseline, enriched subset only)
- **Span:** 2026-05-14 → 2026-05-22
- **Projects touched:** client-a
- **Match sources:** {'epic': 5, 'issue_refs+topic': 1}

**Top 8 segments by est_h:**

| segment task_id | project | title | est_h | hands-on | wall-clock | match |
|---|---|---|---:|---:|---:|---|
| `44e56d2731` | client-a | Overhauled client-a-app map region-selection UX with GPS defaults and r | 10.0 | 0.19 | 0.22 | epic:Ship Maps Plugin with Region Selection a |
| `c6c5d518c4` | client-a | Fixed maps plugin: auto-region activation, size estimates, map centeri | 6.0 | 0.09 | 0.65 | epic:Ship Maps Plugin with Region Selection a |
| `30221b733c` | client-a | Shipped Maps planning docs, PMTiles design pivot, and B15–B18 bug log | 6.0 | 0.00 | 0.00 | epic:Ship Maps Plugin with Region Selection a |
| `8ec05094bf` | client-a | Shipped Maps v2/v3 mockups with decision docs and Android template | 5.0 | 0.00 | 0.00 | epic:Ship Maps Plugin with Region Selection a |
| `9ef0bf5ec7` | client-a | Shipped maps app mockups: read-only POIs and photo annotation flows | 4.0 | 0.00 | 0.00 | epic:Ship Maps Plugin with Region Selection a |
| `c8d202b6b9` | client-a | Researched IIAB map APIs and documented native.code permission blocker | 3.5 | 0.00 | 0.00 | issue_refs+topic |


_Detailed JSON dump: `/tmp/ticket-aggregation.json`_

## Mapping confidence (per ticket)

- **CA-2433 (xkcd)** — *LOW confidence*. Only 2 transcript segments matched the ticket key; 0 were LLM-enriched. The 1.49h hands-on is essentially one mockup-iteration session in early May; earlier xkcd-plugin scaffolding work probably happened on R2 / Slack-dispatched paths that never produced a Claude Code transcript joinable to CA-2433. Treat my number as a lower bound.
- **CA-2435 (Forms)** — *MEDIUM-HIGH confidence on attribution, LOW confidence on coverage*. 9 segments, 8 enriched; topic-disambiguated to drop Maps-only tasks that shared the cross-cutting epic ref. The 4.95h captures the in-Claude-Code work Bryan did during the v4 benchmark loop and the v1 ship. Whole-ticket end-to-end time will be larger because: (a) ML Kit vs alternatives deliberation, (b) sketch review, (c) external benchmark reading. None of those show as user-message reads/types.
- **CA-2436 (Maps)** — *MEDIUM-HIGH confidence on attribution, LOW confidence on coverage*. 6 segments, all enriched. Hands-on is microscopic (0.28h) because the Maps workstream was dominated by "dispatch a multi-day agent run and review the PR" sessions where Bryan's user-message volume was tiny. Almost all of the est_h (34.5h shipped) ran through agents with sub-minute human-touch per session. CA peer's 30h estimate is therefore measuring something fundamentally different — likely the end-to-end calendar ownership including design + review + decision time off-keyboard.

## Methodology

- **Run sources walked:** 4 enriched weekly runs (Mar 23, Apr 27, May 11, Jun 1) + 1 wide structural Mar 2 → Jun 10 run. Tasks deduped by content-hashed `task_id` (stable across reruns over identical event sets); for duplicates seen in both wide and enriched runs, the enriched values (est_h, scope, etc.) are preferred.
- **Attribution rules:**
  1. **Primary — explicit issue_refs match.** A task whose `issue_refs_json` contains the target ticket key, AND whose title/summary topic-aligns (so a Maps-only task that happens to mention CA-2435 in its ref list doesn't count as Forms).
  2. **Secondary — LLM-clustered epic membership.** A task in an epic whose title BOTH (a) contains the target ticket key or has it in `issue_refs`, AND (b) is topic-aligned to the target. The (b) clause is the bug-fix: prior versions of this aggregation double-counted because the Forms epic and Maps epic both had CA-2435 in their refs.
- **Topic disambiguation patterns** (declared in code, reviewed for each ticket):
  - CA-2433: `xkcd`
  - CA-2435 (Forms): `forms?|ocr|benchmark|kaspresso|paper(\s*form)?` — anti-pattern `map(s|tiler)?|pmtiles|region-select|gps|gis`
  - CA-2436 (Maps): `map(s|tiler)?|pmtiles|region-?select|maplibre|gis` — anti-pattern `forms?|ocr|kaspresso|paper-?form`
- **Hands-on calc** (same as elsewhere in weekly-review): per user_message, sum reading@300 WPM of prior agent output + typing@40 WPM of user content, capped 20 min/turn. Summed across all matched segments.
- **Wall-clock + agent-busy** (new 2026-06-10 metrics): present only for the 4 enriched runs; show as 0 for tasks attributed solely via the wide structural run (which predates the schema change). Numbers therefore understate wall-clock / agent for tickets like CA-2433 where most matches came from structural-only data.

## Caveats (must accompany any external citation of these numbers)

1. **My hands-on is a transcript-attribution measure, not an ownership-time measure.** Any work Bryan did off-keyboard (reading a paper, talking with someone, walking around the block thinking) registers as zero. The CA peer's whole-ticket estimates likely incorporate ownership time; ours don't.
2. **R2 product-engineer transcripts are NOT joined to PRs** at the segmentation layer (see `docs/process/learnings.md` and the May 11–25 worksheet methodology footer). Tasks anchored to PRs/commits without a Claude Code conversation joined show user_msg_count = 0 and contribute 0 hands-on. For CA-2435 specifically, the v1 ship task `d74f8cbb88` (120h est) has 0 hands-on because the work happened on the R2 agent path. This understates the true total.
3. **Confidence on the topic-disambiguation regex** is moderate: it correctly separated Forms from Maps in the May 11–25 epic data, but novel work on these tickets going forward may use vocabulary that slips past the patterns. Re-validate the regex against any new epic title before reusing.
4. **The 300-WPM reading model is conservative** (Bryan skims long output). Hands-on numbers are therefore lower bounds, which means the gap vs CA peer estimates is, if anything, an upper bound on the v3 calibration delta.

## Files on disk
- This report: `docs/research/2026-06-11-ticket-hands-on-aggregation.md`
- Per-task detail JSON: `/tmp/ticket-aggregation.json`
- Source script: `/tmp/ticket_aggregation.py`
