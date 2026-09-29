# Vision

## The Goal

Help me know how I'm really doing on my work each week — what I shipped, where my time went, what I'd change next week — without me having to remember any of it.

The naive version of this question gets answered with bad metrics: total hours, lines of code, commits per week, "Claude usage." Those describe activity, not output. They don't help me decide what to do differently.

The honest version of the question is much harder: how much of my hands-on time produced something that matters? Where did I waste effort on rework? What did I get done that I couldn't have done last year? When was I blocked on myself vs. someone else? Was I working on the right things?

## What Becomes Possible

- **Friday afternoon:** open a digest of my week. See: hours I was hands-on (not Claude's hours, mine), the projects I touched, the decisions I made, the capabilities that exist now that didn't last week, the rework loops I should have avoided, the things I was blocked on.
- **Quarterly:** see whether the "X times more output" feeling is real or perception bias. Compare against a baseline of the same metrics from previous periods.
- **Daily:** see today's "where am I blocking the team" — what's waiting on me right now that I could unblock with 5 minutes of attention.
- **Per project:** know which projects are flowing vs. stuck. Catch projects that have absorbed a lot of hands-on time without producing capabilities.

## What This Is NOT

- Not a Claude usage dashboard. That's a separate thing (and the conductor's `analyze_transcript.py` already does it).
- Not LOC / commits / PR-count vanity metrics. Those describe shipping volume, not what changed in the world.
- Not a time tracker. Timely already tracks time; this synthesizes across many sources.
- Not a productivity-shaming tool. The point is to help decide what to do, not to feel bad about a slow week.

## What We're Measuring

| Signal | Why it matters | Source |
|---|---|---|
| **My hands-on time per project per week** | The expensive resource. Agent hours are cheap; mine aren't. | Timely (going forward); Claude transcript user-message gaps (past) |
| **Decisions made** | Better complexity proxy than LOC. Each project's `docs/product/decisions.md` is the canonical record. | Project decisions.md files, parsed by week |
| **Capabilities shipped** | Better impact proxy than PR count. What exists now that didn't last week. | Project retrospective.md, blog drafts, release notes |
| **Rework loops** | Inverse productivity signal. Time spent re-doing things I thought were done. | PRs revised within 7 days of merge; rework patterns in transcripts |
| **Blocked-on-me time** | What I should delegate or auto-handle. | Conductor's "waiting on the user" check-ins; PR review backlog |
| **Time per project, breadth per week** | Context-switch cost; what I was actually working on | Timely + Calendar |
| **Meeting hours** | Carve-out from focus time | Calendar |
| **Active project count** | Breadth signal | Distinct repos touched |
| **Agent cost** | Cost-of-goods (not productivity) | analyze_transcript.py --scan |

What we explicitly DON'T track in the headline:
- Total Claude hours (mixes my time with the agent's, misleading)
- LOC, cyclomatic complexity (refactors delete code; design work has zero LOC)
- Commit count (a 2026 commit isn't comparable to a 2024 commit from the same person)

## Decisions That Shape This

- **The user is the data subject.** This skill is configured per-user. Each user provides their own Timely API key, Calendar OAuth, GitHub username, etc. No shared service.
- **Pull, don't push.** The skill runs on demand (or on a cron); doesn't sit between you and your tools.
- **Source files are canonical.** `decisions.md` and `retrospective.md` are the source of truth for "what got decided" and "what shipped." If you don't write them, the digest can't see them.
- **Local-first.** All data pulls and synthesis happen on the user's machine. Nothing uploaded.
- **Shareable.** MIT-licensed. Other developers should be able to install this skill, configure their own data sources, and get value.

## Open Design Questions

- Do we want a separate "today" view (blocked-on-me right now) vs. a "week" view (synthesis at week end)?
- How do we represent "decisions made" — counts, summaries, or just listed?
- Should "capabilities shipped" be agent-extracted from PR descriptions and retros, or human-written?
- For the "vs. last year" comparison: do we have any data from 2-3 years ago, or do we only have the slope of the last few weeks?
- Discord delivery: does the digest just post to a channel, or get DMed?

## Origin

The conductor session was being asked to do this analysis as a side errand. It got in the way of coordination work, and the analysis deserved deeper focus than a side errand allows. So this becomes its own project, its own agent, its own retro and learnings.

The first iteration of the data pipeline (transcript parsing, GitHub PR pull, weekly aggregation) was built in `ai-project-support/docs/process/work-review/` by two peer agents. That work is the seed; this project takes over from there.
