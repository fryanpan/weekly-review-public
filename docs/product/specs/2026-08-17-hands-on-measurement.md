# How "hands-on hours" is measured

**Status:** current as of 2026-08-17. Supersedes the per-message-only model.

> **Summary in [METHOD.md](../../../METHOD.md) (2026-09-18).** METHOD.md states the current definition in plain words and is the one to link; this document is the detailed record. Two rulings after this document's date apply: the transcript tolerance is 10 minutes and the workspace tolerance is 60 seconds (2026-09-08), and unowned anonymous workspace reads count only between 2026-06-29 and 2026-08-10 (2026-09-12).

## Provenance

**Written by:** Weekly Review — the agent that owns the `weekly-review` pipeline and produces the "Where last week actually went" section of the weekly plan.

| where it lives |                                                              |
| -------------- | ------------------------------------------------------------ |
| Repo           | [github.com/fryanpan/weekly-review](https://github.com/fryanpan/weekly-review)  |
| Branch         | `feat/rework-detection-clean`                                |
| The metric     | `presence_hours_by_task` in [src/weekly_review/effort.py](https://github.com/fryanpan/weekly-review/blob/feat/rework-detection-clean/src/weekly_review/effort.py) |
| Its tests      | [tests/test_effort.py](https://github.com/fryanpan/weekly-review/blob/feat/rework-detection-clean/tests/test_effort.py) — gap-spanning, long-break, isolated-message floor, tolerance sweep |
| This doc       | [docs/product/specs/2026-08-17-hands-on-measurement.md](https://github.com/fryanpan/weekly-review/blob/feat/rework-detection-clean/docs/product/specs/2026-08-17-hands-on-measurement.md) |
| Run artifacts  | `~/.local/share/weekly-review/runs/<run-id>/run.sqlite` — every event, task and metric, queryable |
| Commits        | [d6ddb87](https://github.com/fryanpan/weekly-review/commit/d6ddb87) the presence model · [52d8e87](https://github.com/fryanpan/weekly-review/commit/52d8e87) machine-traffic filtering · [fc3429b](https://github.com/fryanpan/weekly-review/commit/fc3429b) this doc + learnings |

> **Pushed 2026-08-17 — every link above now resolves to the code that produced the numbers**, verified against the remote rather than assumed. The 84 unpushed commits were regrouped into 9 before pushing, so the three commit hashes cited in an earlier version of this table no longer exist; the ones above are the real ones. 246 tests pass on the pushed tree. The repo is **private** — publishing it is a separate, manual decision that has not been made.

This is the doc to point at when someone asks "where does that number come from?" It covers what is counted, what is provably not counted, and what the number cannot see at all.

## The two models

|                                          | what it measures                                             | Aug 10–16 |
| ---------------------------------------- | ------------------------------------------------------------ | --------- |
| **Per-message** (`active_hours_by_task`) | modeled reading + typing window around each message          | 10.4h     |
| **Presence** (`presence_hours_by_task`)  | continuous stretches of activity, gaps under the tolerance included | ~20h      |

**Presence is the reported number.** The per-message model is retained because it is a genuine floor and because it is what every pre-2026-08-17 figure used.

### Why the per-message model was wrong

It charges time only in a window attached to a message: the agent's prior message read at 300wpm, plus the human's own words typed at 40wpm, capped at 20 minutes. Everything *between* messages is worth zero — watching a build, reading a diff, driving a device by hand, thinking about the answer.

The user rejected the 10.4h figure for the Aug 10–16 week as obviously too low. He was right. Sweeping the stitch tolerance shows how much the choice matters:

| a pause still counts as working if under… | hours                                         |
| ----------------------------------------- | --------------------------------------------- |
| 5 min                                     | 9.9h                                          |
| **10 min (current default)**              | **18.3h**                                     |
| 15 min                                    | 25.3h                                         |
| 30 min                                    | 37.4h — exceeds stated capacity, so ruled out |

The per-message model lands near the 5-minute column: it behaves as if any pause over ~8 minutes meant work had stopped.

### How presence works

A run of the subject's own events (typed messages and live-feedback review sessions) with gaps under `PRESENCE_STITCH_SEC` becomes one interval spanning first→last. The per-message windows are unioned in as well, so an **isolated** message keeps its modeled typing time instead of collapsing to a zero-length span. Presence is therefore never below the per-message number for the same task.

`PRESENCE_STITCH_SEC` defaults to **10 minutes**. It is a calibration knob, not a constant of nature.

**It is confirmed by evidence that was never fitted to it.** The user had *planned* 18h for the Aug 10–16 week, before any of this was measured, and read the result as "pretty close." 10min gives **18.8h**; 15min would give 25.7h, which his own plan contradicts. An independent prior estimate beats any capacity-ceiling argument, so the looser setting is ruled out on better grounds than the ones originally used.

Re-check it whenever the subject's own account and the reported number diverge.

## What is counted as the human's own time

Two independent filters are required, because neither covers the whole population (both added 2026-08-17 after they were found inflating every prior number):

- **Machine traffic in transcripts.** `promptSource` = `system` and `isMeta` mark

  harness injection. Peer relay — one session's message injected into another — carries *no* `promptSource`, so it is matched textually on the `Another Claude session sent a message` wrapper. In the Aug 10–16 week peer relay alone was 132k of 155k apparent "typed" words.
- **Workspace authorship.** An allowlist on the data subject's own handles, now

  shared by comments, read sessions and activity touches alike (`identity.py`; see "Whose time it is" below). Agents sign as their project ("Client A board", "Live Feedback", "Notes Repo"), not as "Agent", so the denylist this replaced let nearly all of them through — 53k of 61k comment words. An allowlist also correctly excludes third-party reviewers: real review time, but not the subject's.

## Sources read

All analysis runs off the archive at `~/dev/weekly-review-transcripts/<machine>`, refreshed at the start of every run. **Check archive freshness against the requested window before trusting any run** — the hourly refresh job has died twice to macOS revoking its disk access, and the pipeline reads the archive by design, so a stale archive reports an empty week rather than an error.

| source                                                    | what it contributes                          |
| --------------------------------------------------------- | -------------------------------------------- |
| Claude Code transcripts (incl. `subagents/agent-*.jsonl`) | typed messages, agent activity               |
| live-feedback `.ydoc` + `activity.jsonl`                  | review comments, measured read/edit sessions |
| workspace board `workspaces/*.events.jsonl`               | `task.*`, `decision.*`, `voice.*` — instants carrying a `taskId` |
| workspace `meetings/*/meetings.jsonl`                     | recorded meetings, as measured intervals     |
| git / GitHub                                              | commits, PRs opened + merged                 |
| Google Calendar                                           | meetings, off-keyboard reachouts             |
| Notion Personal CRM                                       | contact targets and intent                   |

## What the number still cannot see

Off-keyboard work leaves no trace in transcripts: phone calls, in-person conversations, email, reading on a device, thinking away from the machine. Calendar closes part of this gap (it is how an Aug 13 lunch with a contact was recovered after the transcript-only pass reported zero reachouts), but only for work that was scheduled.

**Recorded meetings close a second part of it, from 2026-09-12** — but only back to 2026-08-28, when the workspace recorder started writing, and only for conversations he chose to record. A meeting nobody recorded is still invisible, and so is every phone call. See "Meetings and voice requests" below.

**A zero on a goal that lives outside the tooling means "no trace", not "did not happen."** Report it that way, always.

### Calendar caveats

- Only events the subject **accepted** count against capacity. `needsAction` and

  `declined` do not; organizer counts as accepted.
- That rule systematically undercounts recurring invites the subject attends but

  never RSVPs to — the weekly client demo is the known live instance.
- **A gap between meetings does not say whose move it is.** A prospective client read as ten

  days of silence after 8/07; the user supplied the missing half — they owe him a proposal. Absence of activity is not evidence of inaction, and the pipeline cannot tell the two apart. Report the gap, never the blame.

### CRM caveats

`Last Contacted` lags reality badly (a contact read three months stale the day after a lunch with them). **Use the calendar for what happened and the CRM for what was intended**; any "overdue" count computed from CRM dates alone is inflated.

## Provenance for the Aug 10–16 figures

- run `run-20260817T195219Z`, generated 2026-08-17T19:54Z, window 2026-08-10 → 2026-08-16
- 78,962 events across 286 sessions → 98 tasks, task coverage 403/403 (100%)
- code: `src/weekly_review/effort.py`, tests in `tests/test_effort.py`

## Historical backfill (18 weeks, 2026-04-13 → 2026-08-16)

Re-run 2026-08-17 on the fixed pipeline so past weeks are comparable to present ones. Every week reached 100% task coverage. Run un-enriched throughout, including the current week, so the series is internally consistent — enrichment slightly re-cuts task boundaries (~3% on hands-on), so enriched and un-enriched figures must not be mixed in one trend.

| week of | events | 5min | 10min | 15min | 30min | per-message |
| ------- | ------ | ---- | ----- | ----- | ----- | ----------- |
| 04-13   | 537    | 11.6 | 18.9  | 25.0  | 33.1  | 14.8        |
| 04-20   | 453    | 8.5  | 16.6  | 21.9  | 35.4  | 12.5        |
| 04-27   | 431    | 7.7  | 16.8  | 22.6  | 34.3  | 10.8        |
| 05-04   | 264    | 5.5  | 9.9   | 12.3  | 18.5  | 6.6         |
| 05-11   | 346    | 6.2  | 13.4  | 19.1  | 29.4  | 8.5         |
| 05-18   | 239    | 4.3  | 8.5   | 13.2  | 18.9  | 7.1         |
| 05-25   | 506    | 10.5 | 20.9  | 25.5  | 39.7  | 11.5        |
| 06-01   | 448    | 10.4 | 19.1  | 24.7  | 33.2  | 12.3        |
| 06-08   | 148    | 1.8  | 5.7   | 9.1   | 15.5  | 4.5         |
| 06-15   | 113    | 1.7  | 3.5   | 4.7   | 6.9   | 3.9         |
| 06-22   | 71     | 1.2  | 2.0   | 2.6   | 3.9   | 1.9         |
| 06-29   | 268    | 5.6  | 9.4   | 12.2  | 19.3  | 6.4         |
| 07-06   | 482    | 10.6 | 19.5  | 25.2  | 34.9  | 13.9        |
| 07-13   | 548    | 11.6 | 21.3  | 28.1  | 42.4  | 14.3        |
| 07-20   | 460    | 8.7  | 16.7  | 22.0  | 34.6  | 10.7        |
| 07-27   | 438    | 9.6  | 16.2  | 22.6  | 36.1  | 12.3        |
| 08-03   | 593    | 10.5 | 20.1  | 28.0  | 42.4  | 11.5        |
| 08-10   | 621    | 10.0 | 18.8  | 25.7  | 38.1  | 10.8        |

### What the series shows

- **The per-message model ran at a median 67% of presence** across all 18 weeks. Every hands-on and leverage figure published before 2026-08-17 is low by roughly a third, on top of the separate machine-traffic corrections.
- **The 30-minute tolerance exceeds ~30h capacity in 11 of 18 weeks**, so excluding it generalizes rather than being fitted to a single week.
- **Peak observed week is 21.3h at 10min, 28.1h at 15min.** That a peak never approaches a full week looked like an argument for a looser setting — until the user's own prior plan settled it (see below).
- **Mid-June collapses to 2.0–5.7h** across three consecutive weeks, consistent with time away. A useful check that the measure does register absence rather than manufacturing hours.
- **The reporting week was not an outlier.** 18.8h ranks 12th of 18 (upper half) against a last-6-week median of 19.1h. Shortfalls that week were allocation and over-booking, not lost capacity.

## Workspace attribution — approved 2026-09-08, PARTLY IMPLEMENTED

Implementation state, updated as each piece lands. Everything else in this section is still the scratchpad analysis described in "How it was measured" — **do not cite a figure below as a pipeline output unless its row says shipped.**

| Piece | Task | State |
| --- | --- | --- |
| Board action log read as a source | board task, 2026-09-12 | **Shipped 2026-09-12** — `extractors/workspace_board.py`, on by default, `--no-board-log` to skip |
| Doc-keyed id collapse | board task, 2026-09-12 | **Shipped 2026-09-12** — `metadata["source_event_id"]` in `events.py`, set by `live_feedback.py` for both comments and read sessions |
| Board-first doc→task resolution | board task, 2026-09-12 | **Shipped 2026-09-12** — `_route_doc` in `live_feedback.py`; the row said "not started" for a day after the section below said shipped |
| 60s contiguous blocks, unioned with CC presence | board task, 2026-09-12 | **Shipped 2026-09-12** — `workspace_blocks()` / `hands_on_hours_by_task()` in `effort.py`, wired at `cli.py` and printed on every run |
| One identity rule for all workspace sources | board task, 2026-09-12 | **Shipped 2026-09-12** — `identity.py`, read from `identity.yaml`; the run prints the fragments and eras in force |
| Meeting turns and `voice.request` | board task, 2026-09-12 | **Shipped 2026-09-12** — `extractors/meetings.py` (on by default, `--no-meetings` to skip); `voice.` added to `BOARD_EVENT_PREFIXES`. The coverage caveat prints every run |
| Restatement back to 2026-04-17 | board task, 2026-09-12 | Not started |

**Ingesting is not attributing.** The board log's events are instants and carry no `fixed_active_sec`, so on their own they contribute **zero hands-on hours**. What turns an instant into an interval is the contiguous-block rule below — shipped 2026-09-12, so board actions now buy time only by joining a block that a measured read session or another touch already anchors.

### The document was the identity, and 4 of every 5 visits were discarded

Every live-feedback event was keyed `live_feedback:live-feedback:<docId>:0` — the doc id, and a `seq` that was always zero — so `dedupe_by_event_id` kept **one event per document** and threw the rest away. Measured on the store: 5,699 person read sessions collapsed to 1,180 documents, and 2,560 of the user's comments to 475. One doc he visited 58 times became one event. This is the same defect as the subagent-lane collision that destroyed 80% of a fan-out session, and it has the same shape of fix: the id has to carry what actually distinguishes two records.

It did not need a new scheme, because the store already assigns one. `Event.event_id` now honours `metadata["source_event_id"]` ahead of everything else, and the extractor passes through the activity stream's own `eventId` (present and distinct on 5,699 of 5,699 person read sessions; `payload.sessionId` is the fallback for older records) and `comment:<docId>:<threadId>:<commentId>` for comments. The comment key is compound because bare comment ids are **not** globally unique — 25 of 2,535 distinct ids repeat across the store. Transcript ids are untouched byte-for-byte, so nothing already persisted shifts.

**This moves a published number a long way, and it is the recovered time, not an inflation.** On 2026-08-31..09-06 the store holds **1,293** person read sessions with 1,293 distinct ids, of which dedup was keeping **279**. Hands-on for that week goes **10.6h → 24.3h** (two runs, both 24.3h), against 21.1h of measured read-session duration in the store that was previously discarded. The presence model unions intervals, so the recovered sessions overlap existing typing windows rather than stacking on them — which is why the rise is less than the raw 21.1h. Every hands-on figure published before 2026-09-12 is low by roughly this factor and needs the restatement run (the board task above) before it is quoted again.

**Recovering the events exposed a leak that the collapse had been masking.** Read sessions are filtered on `actor == "person"` with no allowlist, so they are not restricted to the data subject. Over all history that is **1,031 sessions / 22.6h of other people's reading** (`isOwner: false`, generated `Anon-*` names) sitting in the user's hands-on; on the week above it is only 0.39h of 21.07h because the readers that week were almost all him. Dedup had been discarding most of it by accident, and this fix stops discarding it. Two further findings from the same measurement: the activity stream carries both `actorName` and a structural `isOwner` flag, which agree on 5,687 of 5,699 sessions (99.8%); and the comment allowlist's single name fragment (his first name) does not match his own primary handle, so **1,054 of his 2,938 comments (29%) have never counted**. Filed as its own task rather than folded in here — it changes who counts as the data subject, which is a definition change and his call, not a dedup fix.

**An instant on the human side was an interval on the agent side, and that was a bug.** `_agent_intervals_for_task` filtered on `actor == "agent"` with no kind filter, so the 3,155 agent board actions in the 2026-08-31..09-06 week stitched themselves into agent bursts; carrying no `agent_lane`, each board's session also opened a fresh "main" labor lane. Measured before the fix: `agent_labor_hours` **465.0h → 495.3h, +6.5%** — a published metric moving for a reason nobody decided, from a second instrument billing work the transcript stream had already billed. `effort.py` now carries `INSTANT_AGENT_KINDS` and `is_agent_work()`, and the three agent-duration paths (`_agent_intervals_for_task`, `agent_active_hours_by_task`, `agent_labor_hours_by_task`) all filter through it. After the fix `agent_labor_hours` is 465.1h, +0.0% against the same week with `--no-board-log`. Board actions still extend a task's wall-clock span, because a stamped moment is a true fact about when the task was alive; they just never create duration.

What the board log actually costs the published numbers, on that week across five runs: hands-on lands in **10.5–10.8h** whether the board log is read or not (±1.5% around ~10.65h, inside the documented run-to-run variance, and driven by segmentation boundaries moving rather than by board events being charged as time). `agent_busy_hours`, `agent_active_hours` and `wall_clock_hours` all move under 1%.

### Two instruments, two tolerances

The user's ruling, 2026-09-08, verbatim: *"Keep 10min for old data. We already picked 1min for new data in workspaces."*

- **Claude Code transcripts — 10 minutes.** Unchanged. `PRESENCE_STITCH_SEC` stays as published. Transcripts record messages, so a modelled window between them is unavoidable. This closes the 15-minute alternative that had been open since 2026-08-28; see `docs/research/2026-08-28-delivered-hands-on-aug-3-16.md`. **No re-run of published transcript figures is needed** — that was contingent on a ruling for 15 min.
- **Workspace event streams — 60 seconds**, under the contiguous-block rule below. The workspace records interaction directly, so it needs almost no bridging.

### The contiguous-block rule

The user's rule, replacing the modelled window on the workspace side: *"look instead for contiguous blocks on a single taskId that don't have more than a certain gap, and then consider that contiguous block as the time span. Don't extend it before or after."*

Concretely: same `taskId`, consecutive events no more than G apart, span is first event to last event in the block, **nothing added at either end**. A block ends at its last event, not at the start of the next block — the latter reading extends a task forward across every idle gap and is the padding he rejected.

`G` is still a tolerance, but it points **inward**: it bridges every sub-G silence as work, so it can join two things he actually did and can never invent time outside them. That is why it is a better assumption than symmetric padding — but it is not assumption-free, and it must not be described as "no tolerance."

### Why G = 60s

| G | Union hours | Blocks | Zero-length |
| --- | --- | --- | --- |
| 0s | 22.61h | 2,951 | 1,672 |
| 30s | 23.79h | 1,831 | 711 |
| **60s** | **25.06h** | **1,726** | **654** |
| 120s | 27.31h | 1,625 | 621 |
| 300s | 30.34h | 1,561 | 608 |
| 900s | 33.81h | 1,537 | 599 |

Week of 2026-08-31 → 09-06 inclusive, workspace only.

From 0 to 60s the total rises 2.45h while zero-length blocks fall by 1,018 — that stretch buys real joins. From 60s to 120s it rises another 2.25h while only 33 further blocks stop being zero, so the second stretch almost entirely lengthens blocks that already existed. `G = 0` is the method's floor at 22.61h, within 1.5h of 21.07h of raw session time.

The zero-length column barely moves past 60s because those events are stranded in the other store — a decision keyed on a task, a read keyed on a doc, no link. **No value of G reaches them.** Blocking with the key ignored entirely, the ceiling on what any join can achieve, takes 654 down to 89 at the same G.

### Union, never sum

`read_session`, the board action log and Claude Code transcripts describe the same hours from different angles. Intervals are **unioned**, never added. Measured cross-task overlap inside the workspace is 0.5h on that week (2%, union 25.06h vs sum 25.55h).

### The combined weekly figure

Shipped 2026-09-12. The two instruments are unioned at last, and the run prints the union beside the sum so the "never added" claim is checkable rather than asserted.

| Week of 2026-08-31 → 09-06 inclusive | Hours |
| --- | --- |
| Claude Code transcripts, 10-min presence | 4.61h |
| Workspace, 60s contiguous blocks | 25.60h |
| Their **sum** (what we do NOT publish) | 30.21h |
| **Union — the published weekly figure** | **29.60h** |
| What it replaces: presence-only on both instruments, week-wide | 49.44h |

*(Revised twice on 2026-09-12. The identity rule took it 29.06h → 28.63h; meetings and voice requests then took it 28.63h → 29.60h. Run `run-20260912T133716Z`. See "Whose time it is" and "Meetings and voice requests" below for the per-week movement of each.)*

**It replaces the presence-only model and it is 19.85h (40%) smaller.** Both figures are week-wide unions over the same events in the same window, which is the only way the comparison means anything — the old per-task **sum** for this week reads 28.5h and happens to sit near 29.06h by coincidence of two different errors, so do not read that as agreement.

The 0.62h the union removes from the sum is the time recorded on both sides at once: driving an agent in Claude Code while its doc is open in the workspace. That is the double-count the union exists to stop, and it is small here because most of the workspace time is reading and commenting he does *instead* of typing, not alongside it.

**19.87h of the 25.60h (78%) lands on a weekly-review task**, and the 5.73h that does not sits on workspace events no task claims — a workspace task with no sibling evidence in the window (1,047 events orphaned this run). The run prints this every time.

This was **43% (10.78h of 25.06h, 1,984 orphaned)** the day before, and the identity rule is what closed it: recovering 1,073 of his own comments (below) gave the orphaned workspace events the sibling evidence they needed to attach. That also restores the normal relationship — the per-task **sum** is 30.0h, now *larger* than the 25.60h week-wide union rather than smaller. A per-task sum below the union is a coverage hole, never a broken union, and the printed attached share is what tells the two apart. Closing the rest still needs `docId` on board events, which is an instrumentation ask this repo cannot derive; see the orphan discussion in `learnings.md`.

**Per-task figures still double-count across tasks**, exactly as presence did: a block two tasks both have an event in is claimed by both. The week-wide union bounds it, and the run prints the size (0.45h, 2%).

### Whose time it is

The user's ruling, 2026-09-12, on a board task. Shipped the same day in `src/weekly_review/identity.py`, which is the **only** place the question is decided — comments, read sessions and activity touches all call `SubjectIdentity.is_subject()`, and a test asserts each call site, because the defect being fixed was never in a function.

**Two filters had drifted apart.** The comment path carried `HUMAN_NAME_FRAGMENT` set to one fragment of his first name, which never matched his own primary handle. The read-session path carried no allowlist at all, so it counted anyone who opened a doc he shared. The store files one person four ways at once:

| `actorId` prefix | `actorName` | records |
| --- | --- | --- |
| `known-*` | first name | 5,205 |
| `user-*` | handle | 3,938 |
| `anon-*` | full name | 1,905 |
| `anon-*` | generated `Anon-xxxxxx`, no owner flag | 2,510 |

**The rule, in order.** A machine is never the subject (`agent-*`, `live-*`, `chat-*`, `test-*`, `bot-*` — all filed under `actor: "person"`, told apart only by the id prefix). A configured name fragment always counts, whatever the date. The store's own `isOwner` flag counts. A *named* non-subject never counts — which is how an identified third-party reviewer is excluded without this repo holding their name. A **blank** name counts never: every person record in the store carries one, so a blank is malformed input, and crediting it would make a parse failure read as his time. What is left is a generated handle, and that counts only inside a dated era.

**The era is 2026-06-29 → 2026-08-10 inclusive**, when the workspace did not resolve browser handles, so his own reading arrived unowned and name-shaped. Both edges are hard and tested a day either side. An era that can be nudged outward to recover hours is a dial, not evidence.

**None of it is in source.** `~/.config/weekly-review/identity.yaml` carries `workspace_names`, `anon_eras` and `anon_name_pattern`; `examples/identity.yaml` documents them. Unconfigured, the rule counts **everyone** who is not a machine and the run says so loudly on stderr — zero hours is indistinguishable from a quiet week, so the failure mode has to be visibly wrong rather than invisibly wrong. Every run prints the fragments and eras it used.

#### What it moves, per week

Measured over the whole store before anything quoted it, as the week-wide workspace block union under the shipped rule against the code that produced the published figures. Weeks under 0.05h either side are omitted.

| week of | before | after | change | | week of | before | after | change |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 04-20 | 0.10 | 0.05 | −0.05 | | 07-06 | 7.55 | 7.55 | — |
| 05-04 | 0.33 | 0.02 | −0.31 | | 07-13 | 3.92 | 3.92 | — |
| 05-11 | 0.78 | 0.46 | −0.32 | | 07-20 | 2.68 | 2.68 | — |
| 05-18 | 0.28 | 0.00 | −0.28 | | 07-27 | 7.52 | 7.52 | — |
| 05-25 | 0.33 | 0.00 | −0.33 | | 08-03 | 13.93 | 13.92 | −0.00 |
| 06-01 | 0.62 | 0.11 | −0.51 | | 08-10 | 15.24 | 15.22 | −0.02 |
| 06-08 | 0.49 | 0.00 | −0.49 | | 08-17 | 12.60 | 12.58 | −0.02 |
| 06-15 | 0.47 | 0.00 | −0.47 | | 08-24 | 11.52 | 11.48 | −0.04 |
| 06-22 | 0.90 | 0.66 | −0.24 | | **08-31** | **25.47** | **25.00** | **−0.48** |
| 06-29 | 1.87 | 1.87 | — | | 09-07 | 18.93 | 18.93 | — |
| | | | | | **total** | **125.62** | **122.07** | **−3.55 (−2.8%)** |

**Inside the era the change is exactly zero**, which is the point: those weeks already counted the anonymous reads and the ruling keeps them. The losses are all in the weeks either side — other people's reading, and handles nobody can resolve, in weeks where that is not a known condition of the store.

**And the ruling costs far less than the option it was weighed against.** The review item priced *named-only* at 22.6h of read-session time. Against the code that actually produced the published figures the ruling moves −3.55h over all history; the era is worth **+21.82h against named-only**. Measure a ruling's delta against what shipped, not against the alternative you rejected.

**The comment recovery is the larger effect and it runs the other way.** 1,073 of his own comments return, and they are almost entirely in the two most recent weeks (**+487** on 08-31, **+586** on 09-07) because the `user-*`/handle family is recent — earlier weeks he commented under his first name, which the one fragment did match. Comments are instants under the block rule, so they barely move the union; under the presence model they each carry a modelled typing window, which is why the published week's **presence-only comparison rose 46.95h → 49.44h** while its block figure fell. One identity change, two instruments, opposite directions.

### Meetings and voice requests

Shipped 2026-09-12 on a board task, in `src/weekly_review/extractors/meetings.py` and one entry in `BOARD_EVENT_PREFIXES`. Until this, every source measured a keyboard: an hour spent talking left no trace in a transcript or in the workspace, so off-keyboard time was silently zero.

**Two of the task's own premises did not survive a census of the store, so the implementation follows the measured shape.**

| The task said | The store says |
| --- | --- |
| Coverage is "how many meeting directories carried a `meeting.json`, out of how many exist" — 13 of 31 | `meeting.json` is a **rollup** and no duration is read from it. The ledger is `meetings.jsonl`, and **42 of 42** directories have one. The caveat prints both, so the substitution is visible |
| A turn missing either end must be counted as coverage lost | Agreed, and **no segment is missing an end** — 66 of 66 start records have a matching `endedAt`. The guard ships and is tested anyway |
| Meeting total ≈ 1.71h | **5.24h** of ledger time across 66 segments |
| `voice.request` lives in `activity.jsonl` | It lives in `workspaces/*.events.jsonl`. `activity.jsonl`'s eight types are `read_session`, `reply`, `comment`, `doc_open`, `resolve`, `archive`, `reopen`, `unarchive` |

**A segment is worth its ledger span, not its turns.** Across the 54 turn files carrying timestamps the spoken span totals 4.22h against the ledger's 5.24h — **0.98h (19%) is recorded and not spoken**. A pause, a document being read, somebody else talking off-mic: the recorder was running, and that is presence. Every turn's own timestamp rides in `metadata["turn_ts"]`, with `max_silence_sec` beside it, so a reader can tell an hour of talking from a recorder somebody forgot to stop.

**`resumedAt` opens a second run rather than extending the first.** Collapsing a paused meeting to first-start → last-end would bill the pause as presence. The two real pauses are 7s and 1.3s, so this changes today's union by nothing; it changes what happens the first time somebody pauses a recording to take a call.

**Mode, `participant` and speaker labels are not identity signals and none is used as one.** `participant` is absent on 32 of 66 segments and is his own handle on the other 34; the auto-notes split one solo speaker into "Speaker A" and "Speaker B". It is his recorder on his machine, and the measurement is his presence, not who spoke.

**Nothing spoken enters an event.** Turn text carries third parties' names and their personal and medical business, and the ledger's `speakers` records carry real names outright. The extractor reads turn files for timestamps and word counts and reads nothing else, and never opens a `speakers` record. The one spoken field the pipeline does keep is a `voice.request`'s `transcript` — because it is *his*, dictated to his own board, which is the same act as typing to it, and his typed messages have always been kept.

#### What they move

Measured before anything quoted them, as the week-wide workspace block union with and without each addition. The transcript instrument is untouched.

| week of | shipped | + voice | + meetings | + both | change |
| --- | --- | --- | --- | --- | --- |
| 08-24 | 11.61 | 11.64 | 11.82 | 11.85 | **+0.24 (+2.1%)** |
| 08-31 | 25.09 | 25.08 | 27.41 | 27.41 | **+2.33 (+9.3%)** |
| 09-07 | 19.19 | 19.21 | 19.75 | 19.77 | **+0.59 (+3.1%)** |
| **all history** | **122.70** | **122.75** | **125.80** | **125.85** | **+3.15 (+2.6%)** |

Every week before 2026-08-24 moves by exactly zero: the recorder started writing on 2026-08-28 and the first `voice.request` is 2026-08-18, so there is nothing earlier to recover. **This source cannot backfill.**

**Meetings are worth +3.10h against 5.24h of ledger time.** The 2.14h shortfall is meeting time that *overlaps* workspace time already counted — he records while reading and commenting — which is the union doing exactly what it exists to do.

**Voice requests are worth +0.06h, and the first measurement said +0.00h, which was a drop rather than a result.** All 88 carry no `taskId`, and `workspace_blocks` skips a keyless event entirely, so they contributed nothing *and* bridged nothing while looking ingested. `workspace_attribution_key` now falls back to the board, which is the narrowest true statement the store supports. Two further notes on that number: 7 of the 88 name their task under `context.taskId`, which reading only the top level would have dropped silently; and the per-week movement runs in **both** directions (−0.01h on the week of 08-31) because a keyed instant landing between two same-key events correctly **cuts** their block — he is on record doing something else in that gap.

### Routing: board-first, directory fallback

The user's ruling, 2026-09-08. `_route_doc` in `extractors/live_feedback.py` has consulted the board since 2026-08-24, but its precedence is still file-path `sourceUrl` → board → directory → keywords. A file-path `sourceUrl` is itself directory-derived, so the ruling demotes it below the board.

**Shipped 2026-09-12, and the blast radius above was measured against the wrong field.** That paragraph said "470 docs carry both a file-path source and a board." Re-measured over the whole store: `sourceUrl` is present on **161 of 7,587 docs (2.1%)**, and **none of those 161 also carries a board** — so the branch the ruling literally names almost never competes with anything. The signal that actually contests the board is `doc_root`, the working directory from each doc's `.index.json`: **532 docs carry both a root and a board.** `doc_root` is directory-derived in exactly the sense the ruling demotes, so it moves below the board too.

**Matching a board name to a slug exactly is why the ruling was a no-op.** A board name is not a slug: `Lead Agent` is `ai-team-lead`, `live-feedback` is `claude-live-feedback-plugin`, `Finance B` is `finance-tool`, `client-a-board` is `client-a`. Exact normalised matching fires on 4 of 14 boards, and on those four the path already agreed — so reordering alone changed the routing of **zero** docs. The mapping is not hand-written: a board's own docs' working directories already state it.

**A board only names a project when all its docs came from one.** That is the container test, and it is a measurement rather than a maintained list — **12 of 14 boards are 100% single-project**, and the two that are not are exactly the two places that hold other people's work:

| board | docs | project slugs | purity |
|---|---|---|---|
| `Unfiled` | 128 | 8 | 53.9% |
| `Lead Agent` | 31 | 4 | 77.4% |

A container abstains and the working directory answers. Those two boards are also where **every** doc lives that board-first would route away from a directory that is right — 7 of them, named individually rather than left as "about 8":

| doc | working directory says | board would say |
|---|---|---|
| `ca-analysis-2026-08-19` | `client-a` | `ai-team-lead` |
| `ca-announcement-draft` | `client-a` | `ai-team-lead` |
| `ca-benchmark-review-2026-08-19` | `client-a` | `ai-team-lead` |
| `ca-data-validity-2026-08-19` | `client-a` | `ai-team-lead` |
| `ca-report-2026-08-19` | `client-a` | `ai-team-lead` |
| `reconnect-merged-2026-08-18` | `contacts-tool` | `ai-team-lead` |
| `weekly-review-methodology` | `weekly-review` | `ai-team-lead` |

All seven sit on `Lead Agent` because that is who was reading them, not what they were about — which is the whole argument for the container test. The two the earlier paragraph named by category (a `weekly-review` doc on the lead agent's board, a `contacts-tool` one) are the last two rows, so the estimate was right about the shape and wrong about the mechanism.

**On the board side the ruling is absolute, because there is nothing to rank it against.** A board event has no `sourceUrl` and no working directory, so `extractors/workspace_board.py` routes on the board name alone: a board whose normalised name matches a registered slug takes it, and a board that matches nothing lands in `unknown`. The task title is deliberately withheld from the keyword fallback — passing it split one board across two projects (2 of 27 person events on "Week of Aug 10 — Lead Agent" went to `writing-tool` because their titles said "blog"; 1 of 90 on "Lead Agent" went to `client-a`). A board belongs to one project or to none; per-event drift inside a single board is a guess wearing a measurement's clothes. Cost of the stricter rule, measured 2026-09-12: 45 of 1,323 person board events (3.4%) route to `unknown`, all of them on three boards with no `projects.yaml` entry at all — `client-d-lore`, `research`, `share-link-test`. That is a registry gap the coverage report should surface, not something routing should paper over.

### What blocks full attribution

The two stores share only `workspaceId`. The doc stream carries `docId` and `threadId` and **no `taskId`**; the board log carries `taskId` on 100% of task events and **`docId` on 0% of all 32 event types**. The only bridge in the data is `review_item.revised` carrying a `threadId` — 11 of 384 review items, 3%.

Re-measured 2026-09-12 through the shipped extractor, across all ten boards and all of history rather than one week: **9,356 of 9,356** `task.*` / `decision.*` records carry a `taskId`, and **9,356 of 9,356** carry an `actor` with a `kind` — 1,323 `person`, 8,033 `agent`. So the board side of the join is not partially instrumented; it is complete. Every remaining gap is on the doc side.

So 45% of person events sit on task cards and resolve directly; **55% have no recorded path to a task**, and `tasks.json`'s `links` and `docIds` resolve zero of them. Instrumentation asks are with the Workspaces lead; `docId` on board events is the single highest-value one, because board events already carry `taskId`.

**That 45% is a property of the window, not of the data, and the difference is large.** On 2026-08-31..09-06 the shipped pipeline reports **44.2% (662 of 1,499 doc events)** — the ticket's figure, reproduced. Over the **whole store** the same rule resolves **16.8% of person read sessions (9.0% of their hours) and 16.2% of comments**: task cards are a recent shape and the archive is mostly older standalone docs, so the historical share is a third of the current one. Both numbers are right and quoting either without its denominator is not. Every run now prints the current share on stderr, so the restatement back to 2026-04-17 will show this moving per era rather than reading as a regression.

### What the join recovers, and what it deliberately does not

`attach_by_workspace_task` (segmentation.py, shipped 2026-09-12) attaches a workspace event to the weekly-review task that already holds a sibling carrying the same workspace `taskId`. That is an identity, not a proximity guess. Measured on 2026-08-31..09-06:

| | events |
|---|---|
| carry a workspace `taskId` | 4,433 |
| already attached before the join | 1,229 |
| **attached by the join** (had an anchor) | **1,420** |
| left orphaned (no sibling in the window) | 1,779 |

**Hands-on for the week goes 24.3h → 28.5h (+17%).** The movement is the 236 `review_session` events among the joined set, at ~64s mean measured duration: 236 x 64s ≈ 4.2h against a measured +4.18h. `agent_labor_hours` moves 465.19h → 465.28h (+0.02%) in the same run, which is the independent confirmation that the 1,189 joined `board_action` events attached without creating duration — an instant stays an instant.

Three things the pass refuses to do, each because doing them would move a published number for an unmeasured reason:

- **It never moves an already-attached event.** 48 of 222 workspace tasks (21.6%) are split across more than one weekly-review task, one of them eight ways. Merging those is a re-segmentation; it is counted and reported, not performed.
- **It leaves the 1,779 no-anchor events orphaned**, across 511 workspace tasks. A workspace task with no other evidence in the window is work that happened outside it; manufacturing a task to hold it would report a week that did not happen. Closing that gap needs `docId` on board events — the instrumentation ask above.
- **It does not guess a task from a board.** A doc that merely sits on a board whose tasks are known still resolves to nothing. The board tells you the project; it never tells you the task.

### How it was measured

Scratchpad analysis, not pipeline code: block sweep and union over `activity.jsonl` (person events, `interactionBounded` read sessions contributing their own measured interval) merged with `workspaces/*.events.jsonl` (person events carrying a `taskId`). Approved plan and evidence: a workspace doc on the project board, and the eight tasks under one goal.
