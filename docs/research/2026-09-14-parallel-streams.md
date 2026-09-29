# How many teams and parallel streams, Apr 13 – Sep 13 2026

For the age-of-agents post. Checks Team Lead's 2026-09-14 fleet figures ("6–18 projects a week, median 9; streams median 1 bursting to 4–7; overnight 59 of 154 nights") and restates them with the parse defects fixed and machine traffic filtered out.

Script: `scripts/parallel_streams.py` (tests: `tests/test_parallel_streams.py`). Counts only; no project names.

## Answer

| Claim in the post | Holds? | What the data says (filtered) |
| --- | --- | --- |
| About 9 active projects a week | **Yes** | Median 9 |
| 6–18 projects a week | **No** | 7–13. The 18 came from cron loops and duplicated transcripts |
| Several projects at once | Partly | 2 projects in a typical half hour, 3 at p90, 13 at most |
| Each project runs 2–4 parallel streams | **No, usually 1** | Median 1 stream per active project per half hour, bursting to 4 at p90 |
| Bursts to 4–7 streams | Lower end only | p90 is 4 per half hour and 5 at a project's busiest point in a day |
| Sometimes 24/7 | **Yes** | Agents worked 01:00–05:00 PT on 67 of 154 nights (≥200 turns), 103 nights with any turn |

A defensible sentence: *"In a typical week about 9 projects had agents working on them (7–13). Most of the time each ran one stream, bursting to four or five, and on 67 of 154 nights agents were still working between 1 and 5am."*

## Definitions

Team Lead's definitions are kept so the figures compare:

- **Project**: the transcript's project dir, worktree suffix stripped.
- **Turn**: one assistant record.
- **Active**: a project with ≥50 turns in a Monday-start PT week.
- **Stream**: one transcript file, i.e. a session's main lane or one subagent lane.
- **Window**: a 30-minute PT bucket.
- **Night**: 01:00–05:00 PT.

## From Team Lead's figure to the filtered one

| Step | Active projects/week, median (range) | Peak streams per project-day, p90 | Nights ≥200 turns |
| --- | --- | --- | --- |
| Team Lead's method, live store | 9 (6–18) | 7 | 59 |
| Parse fixed, live store | 8 (5–16) | 8 | 60 |
| + archive (full window) | 11 (7–16) | 9 | 74 |
| + drop turns answering scheduled prompts | 9 (7–13) | 10 | 70 |
| + drop streams shorter than 5 min | **9 (7–13)** | **5** | **67** |
| streams ≥10 min instead | 9 (7–12) | 3 | 64 |
| streams ≥30 min instead | 9 (6–12) | 2 | 56 |

`--team-lead-method` reproduces the first row exactly. The laptop archive has no transcripts in this window, and adding it changes nothing.

## What moved the numbers

1. **Duplicated transcripts counted as projects.** Team Lead's own 300MB session had been copied into three scratchpad-cwd project dirs. Each copy was an "active project" in every week the session ran. The script keeps the largest copy of each session (median −2, top of range −3).
2. **Long turns dropped.** The original read timestamps only from a line's first 3,000 bytes, but the top-level timestamp follows the message body, so every long turn was lost (median +1, top +1).
3. **The live store only keeps about 16 weeks.** The three weeks from Apr 13 read 6 projects a week from the live store and 12–13 from the archive. Team Lead's low end of 6 is a retention artifact.
4. **Cron loops.** Scheduled prompts (`promptSource: system`) are injected into long-running fleet sessions. They made 5 extra projects "active" in the week of Sep 7 (16 → 11), and they drive most of the late-summer rise. The filter works per turn: a turn inherits the source of the last prompt before it.
5. **Short subagents.** 9,046 of 12,295 streams ran for less than 5 minutes: fan-out lookups, not teams. Removing them leaves the project counts unchanged and cuts burst sizes: peak streams per project-day go from p90 10 to 5, and the maximum from 258 to 51.

## Limits

- **A project is not a team.** ≥50 turns is a low bar; one short session clears it. The automated benchmark harness is one of the active projects in 7 of the 8 weeks from Apr 13 to Jun 1, and a scratch dir is one in two weeks. Every other active project is a named repo.
- **Subagents dispatched by a cron prompt are not filtered.** Only the main lane's own turns are. So the cron share is a floor.
- **The Conductor and R2 stores are not scanned.** R2 traffic ended Apr 12, before the window.
