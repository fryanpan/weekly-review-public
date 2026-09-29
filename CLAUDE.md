# Project: weekly-review

## The Goal

Help me know how I'm really doing on my work each week — what I shipped, where my time went, what I'd change next week — without me having to remember any of it.

Read [docs/product/vision.md](docs/product/vision.md) before non-trivial work. The vision lists what we measure, what we explicitly DON'T measure, and the open design questions.

For any history-spanning analysis (>2 weeks), read [docs/architecture/data-sources.md](docs/architecture/data-sources.md) — the user's transcripts may live in several stores with different cwd shapes (Mac Mini direct, Conductor.build SQLite, R2 product-engineer agents). Don't assume a single source covers their history.

**Before publishing any effort number**, read [docs/product/specs/2026-06-10-canonical-metric-block.md](docs/product/specs/2026-06-10-canonical-metric-block.md). It defines the 6 metrics (expert hours, hands-on hours, wall-clock hours, agent hours, wall-clock speedup, hands-on leverage), how they aggregate at each scope (per-task, per-epic, per-week), and the methodology footer that must accompany them. The worksheet generator (`scripts/make_label_worksheet.py:_fmt_metric_block`) is the canonical renderer — use it, don't reinvent.

## What This Project Is Building

A Claude Code skill (`/weekly-review`) that synthesizes a weekly digest from multiple data sources:
- **Timely** — actual time tracked per project
- **Google Calendar** — meeting hours vs. focus hours
- **GitHub** — PRs merged, reviewed, commented (across many repos)
- **Linear** — tickets created and completed per team
- **Asana** — daily-life tasks (optional)
- **Claude Code transcripts** — for past data before Timely was wired up
- **Project source files** — `decisions.md` and `retrospective.md` as canonical "what got decided" and "what shipped"

Output: a markdown digest. Eventually delivered to Discord.

## Stack

- **Skill scaffolding:** `.claude/skills/weekly-review/SKILL.md` is the entry point
- **Helper scripts:** Python (matches `analyze_transcript.py` pattern in ai-project-support; pure stdlib where possible)
- **Data sources:** REST APIs / MCP servers / file scans depending on the source

## Conventions

- Lead with goals, not implementation. Top-level docs answer "what becomes possible" before "how it works."
- Public repo, MIT-licensed. Other devs should be able to install and configure for themselves.
- Local-first. All data pulls and synthesis happen on the user's machine.
- The user is the data subject. Each user configures their own Timely API key, Calendar OAuth, GitHub username, etc.

## Linear

- Team: <your team>
- Team ID: <your-team-id>

## Origin

Started 2026-04-19 from a work-review prototype in a private project-support repo (two peer agents built the first transcript parser and PR pull). This project takes over.

@docs/process/learnings.md
