# Per-ticket hands-on aggregation v2 — full Jira sweep

Generated: 2026-06-12 · 66 tickets auto-discovered · post-R2-join data

**Unit:** Σ Bryan-hands-on hours across all transcript segments mapped to the ticket. Hands-on = reading@300 WPM + typing@40 WPM per user-message turn, capped 20 min/turn. Tier A = high confidence (epic title contains ticket ref). Tier B = medium-low confidence (task titles infer topic). 

## Headline table

| Ticket        | Tier | n_tasks | n_enriched | n*with*user_msgs | hands-on h | wall-clock h | agent-busy h | est_h sum | first      | last       | projects                       |
| ------------- | ---- | ------- | ---------- | ---------------- | ---------- | ------------ | ------------ | --------- | ---------- | ---------- | ------------------------------ |
| **CA-2435** | A    | 15      | 13         | 8                | **5.78**   | 6.9          | 1.81         | 193.0     | 2026-05-07 | 2026-06-06 | client-a                   |
| **CA-2436** | A    | 7       | 6          | 2                | **0.28**   | 0.87         | 0.52         | 34.5      | 2026-05-07 | 2026-06-06 | client-a                   |
| **BC-141**    | A    | 5       | 2          | 2                | **0.21**   | 0.51         | 0.17         | 24.0      | 2026-03-12 | 2026-03-26 | health-tool, rare-disease-benc |
| **BC-203**    | A    | 3       | 2          | 2                | **0.01**   | 0.35         | 0.32         | 10.5      | 2026-03-28 | 2026-03-28 | product-engineer               |
| **BC-68**     | B    | 3       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-03-03 | 2026-03-03 | health-tool, rare-disease-benc |
| **BC-97**     | B    | 3       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-03-04 | 2026-03-05 | health-tool, rare-disease-benc |
| **BC-120**    | B    | 3       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-03-07 | 2026-06-04 | health-tool, rare-disease-benc |
| **BC-121**    | B    | 3       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-03-07 | 2026-03-08 | health-tool, rare-disease-benc |
| **BC-123**    | B    | 3       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-03-08 | 2026-03-08 | health-tool, rare-disease-benc |
| **BC-76**     | B    | 2       | 0          | 1                | **0.01**   | 0            | 0            | 0         | 2026-03-03 | 2026-03-07 | product-engineer               |
| **BC-82**     | B    | 2       | 0          | 1                | **0.02**   | 0            | 0            | 0         | 2026-03-04 | 2026-03-31 | product-engineer               |
| **BC-173**    | B    | 2       | 0          | 1                | **0.07**   | 0            | 0            | 0         | 2026-03-18 | 2026-03-18 | product-engineer               |
| **BC-84**     | B    | 2       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-03-04 | 2026-03-07 | product-engineer               |
| **BC-88**     | B    | 2       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-03-04 | 2026-03-04 | product-engineer               |
| **BC-139**    | B    | 2       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-03-11 | 2026-03-11 | product-engineer               |
| **BC-108**    | B    | 2       | 0          | 1                | **0.01**   | 0            | 0            | 0         | 2026-03-06 | 2026-05-05 | health-tool, rare-disease-benc |
| **BC-75**     | B    | 2       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-03-04 | 2026-03-04 | health-tool, rare-disease-benc |
| **BC-83**     | B    | 2       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-03-04 | 2026-03-05 | health-tool, rare-disease-benc |
| **BC-95**     | B    | 2       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-03-04 | 2026-03-05 | health-tool, rare-disease-benc |
| **BC-96**     | B    | 2       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-03-05 | 2026-03-06 | health-tool, rare-disease-benc |
| **BC-115**    | B    | 2       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-03-07 | 2026-04-20 | health-tool, rare-disease-benc |
| **BC-126**    | B    | 2       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-03-08 | 2026-03-08 | health-tool, rare-disease-benc |
| **BC-131**    | B    | 2       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-03-08 | 2026-03-09 | health-tool, rare-disease-benc |
| **BC-242**    | B    | 2       | 0          | 1                | **0.32**   | 0            | 0            | 0         | 2026-04-01 | 2026-04-02 | family-bike-map                |
| **BC-216**    | B    | 2       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-03-31 | 2026-04-04 | family-bike-map                |
| **BC-241**    | B    | 2       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-04-01 | 2026-04-01 | family-bike-map                |
| **BC-243**    | B    | 2       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-04-01 | 2026-04-01 | family-bike-map                |
| **BC-256**    | B    | 2       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-04-02 | 2026-04-02 | family-bike-map                |
| **BC-258**    | B    | 2       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-04-03 | 2026-04-03 | family-bike-map                |
| **BC-268**    | B    | 2       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-04-03 | 2026-04-10 | family-bike-map                |
| **CA-2433** | B    | 2       | 0          | 1                | **1.49**   | 0            | 0            | 0         | 2026-05-06 | 2026-05-14 | client-a                   |
| **BC-192**    | A    | 1       | 1          | 1                | **0.09**   | 0.2          | 0.12         | 3.5       | 2026-03-25 | 2026-03-25 | product-engineer               |
| **BC-195**    | A    | 1       | 1          | 1                | **0.03**   | 0.07         | 0.04         | 1.0       | 2026-03-25 | 2026-03-25 | product-engineer               |
| **BC-998**    | A    | 1       | 1          | 1                | **0.0**    | 0.02         | 0.01         | 0.2       | 2026-03-28 | 2026-03-28 | product-engineer               |
| **BC-81**     | B    | 1       | 0          | 1                | **0.01**   | 0            | 0            | 0         | 2026-03-04 | 2026-03-04 | product-engineer               |
| **BC-90**     | B    | 1       | 0          | 1                | **0.01**   | 0            | 0            | 0         | 2026-03-04 | 2026-03-06 | product-engineer               |
| **BC-94**     | B    | 1       | 0          | 1                | **0.05**   | 0            | 0            | 0         | 2026-03-04 | 2026-03-13 | product-engineer               |
| **BC-98**     | B    | 1       | 0          | 1                | **0.37**   | 0            | 0            | 0         | 2026-03-05 | 2026-03-05 | product-engineer               |
| **BC-269**    | B    | 1       | 0          | 1                | **0.01**   | 0            | 0            | 0         | 2026-04-04 | 2026-04-04 | product-engineer               |
| **BC-91**     | B    | 1       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-03-05 | 2026-03-05 | product-engineer               |
| **BC-125**    | B    | 1       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-03-08 | 2026-03-09 | product-engineer               |
| **BC-177**    | B    | 1       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-03-19 | 2026-03-19 | product-engineer               |
| **BC-228**    | B    | 1       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-04-01 | 2026-04-01 | product-engineer               |
| **BC-93**     | B    | 1       | 0          | 1                | **0.02**   | 0            | 0            | 0         | 2026-03-05 | 2026-03-05 | health-tool                    |
| **BC-99**     | B    | 1       | 0          | 1                | **0.01**   | 0            | 0            | 0         | 2026-03-05 | 2026-03-05 | health-tool                    |
| **BC-102**    | B    | 1       | 0          | 1                | **0.04**   | 0            | 0            | 0         | 2026-03-06 | 2026-03-06 | health-tool                    |
| **BC-79**     | B    | 1       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-03-04 | 2026-04-06 | health-tool                    |
| **CV-03**     | B    | 1       | 0          | 1                | **0.08**   | 0            | 0            | 0         | 2026-04-26 | 2026-04-27 | blog-assistant                 |
| **BC-221**    | B    | 1       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-03-31 | 2026-03-31 | notes-tool                 |
| **XPS-13**    | B    | 1       | 0          | 1                | **0.04**   | 0            | 0            | 0         | 2026-03-06 | 2026-03-06 | ai-team-lead                   |
| **CVE-2026**  | B    | 1       | 0          | 1                | **0.1**    | 0            | 0            | 0         | 2026-05-09 | 2026-05-09 | ai-team-lead                   |
| **BC-245**    | B    | 1       | 0          | 1                | **0.21**   | 0            | 0            | 0         | 2026-04-02 | 2026-04-02 | unknown                        |
| **BC-270**    | B    | 1       | 0          | 1                | **0.07**   | 0            | 0            | 0         | 2026-04-04 | 2026-04-04 | family-bike-map                |
| **BC-277**    | B    | 1       | 0          | 1                | **0.01**   | 0            | 0            | 0         | 2026-04-06 | 2026-04-07 | family-bike-map                |
| **BC-222**    | B    | 1       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-03-31 | 2026-04-01 | family-bike-map                |
| **BC-230**    | B    | 1       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-04-01 | 2026-04-01 | family-bike-map                |
| **BC-231**    | B    | 1       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-04-01 | 2026-04-01 | family-bike-map                |
| **BC-239**    | B    | 1       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-04-01 | 2026-06-06 | family-bike-map                |
| **BC-240**    | B    | 1       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-04-01 | 2026-04-01 | family-bike-map                |
| **BC-250**    | B    | 1       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-04-02 | 2026-04-17 | family-bike-map                |
| **BC-253**    | B    | 1       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-04-02 | 2026-04-17 | family-bike-map                |
| **HEA-99**    | B    | 1       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-03-03 | 2026-03-06 | rare-disease-benchmark         |
| **BC-73**     | B    | 1       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-03-04 | 2026-03-05 | rare-disease-benchmark         |
| **BC-92**     | B    | 1       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-03-05 | 2026-03-05 | rare-disease-benchmark         |
| **BC-132**    | B    | 1       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-03-09 | 2026-05-07 | rare-disease-benchmark         |
| **BC-273**    | B    | 1       | 0          | 0                | **0.0**    | 0            | 0            | 0         | 2026-04-05 | 2026-04-05 | rare-disease-benchmark         |

## Caveats

1. **Tier B confidence is medium-low.** No epic-title disambiguation — only explicit ref matches on task title/summary or `issue_refs`. May miss segments the LLM clusterer pulled into an epic without mentioning the ref.
2. **R2-join only fires in March** (where R2 PE traffic was concentrated). May tickets like CA-2435 (Forms) / CA-2436 (Maps) shouldn't shift much from yesterday's 2026-06-11 report.
3. **Hands-on remains transcript-attributed keyboard attention.** Off-keyboard ownership time (design deliberation, reading, in-person discussion) is not counted. See [[project*hands*on*two*constructs]] — Bryan picked this construct as canonical 2026-06-12.
4. **Wide structural run lacks wall*clock*hours / agent*busy*hours** (predates 2026-06-10 schema). For tickets active outside the 4 weekly enriched windows, wall-clock and agent columns under-report. These tickets are flagged in the table by wall-clock = 0 with non-zero hands-on.
5. **Topic keywords used per ticket** are saved in the JSON dump for audit. If a ticket's number looks wrong, check `/tmp/ticket-aggregation-v2.json` for the keywords inferred and the source titles.

## Files

- This report: `docs/research/2026-06-12-ticket-sweep-v2.md`
- JSON dump (full detail, all 66 tickets): `/tmp/ticket-aggregation-v2.json`
- Source script: `/tmp/ticket_sweep_v2.py`
