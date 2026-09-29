# weekly-review

Understand your own productivity when you work with AI coding agents: by project, by task, and as a trend over time. The point is to decide where to put your time, with measured evidence instead of memory.

The tool reads your Claude Code transcripts, git history, GitHub PRs and an optional team board; splits each week into units of work; and reports, per project and per task, three numbers: output (hours an experienced engineer would have needed to produce what shipped), hands-on (hours you were demonstrably at work), and their ratio. Run it every week and the same numbers become a series. [METHOD.md](METHOD.md) defines every number.

## Goal

Know how each week really went — what shipped, where your attention went, how that compares with earlier weeks — without having to remember any of it, and without the usual vanity metrics (LOC, commit count, total agent hours). The output is a page you can check in ten minutes; the decision it serves is what to focus on next.

## Getting started

1. Clone: `git clone https://github.com/fryanpan/weekly-review-public.git`
2. Start Claude Code in the repo: `claude`
3. Ask for setup: "please help me do setup"

The `/setup` skill checks dependencies, creates your config in `~/.config/weekly-review/`, starts the transcript archive and does a first run on last week. It needs Python 3.12+, `uv`, a signed-in `gh` CLI and, for estimated runs, an `ANTHROPIC_API_KEY`.

To do it by hand instead:

```bash
uv sync
mkdir -p ~/.config/weekly-review
cp examples/identity.yaml examples/projects.yaml examples/expertise.yaml ~/.config/weekly-review/
# edit all three, then put ANTHROPIC_API_KEY=... in ~/.config/weekly-review/env (chmod 600)

# --until is inclusive: pass the Sunday
uv run weekly-review --since 2026-09-21 --until 2026-09-27 --no-enrich   # no API calls
uv run weekly-review --since 2026-09-21 --until 2026-09-27               # with estimates
```

Contributors: `git config core.hooksPath .githooks` turns on the pre-push check that blocks private hostnames, home paths and names (see `scripts/scrub-check.py`).

## Configuration

Everything per-user lives in `~/.config/weekly-review/`; the repo carries no names, paths or ids of its own.

| File | What it holds |
|---|---|
| `projects.yaml` | One stanza per project: working-directory patterns, repos, and `doc_keywords` (lowercase substrings that route a review doc with no board to this project). See `examples/projects.yaml`. |
| `identity.yaml` | Who the data subject is: your GitHub logins and every display name the workspace renders you under. Hours are "time this person spent", so a missing handle silently drops your own comments. See `examples/identity.yaml`. |
| `expertise.yaml` | Your self-rated expertise per domain, given to the estimator as a confounder annotation. |
| `slack_channels.yaml` | Optional. Slack channel id → project slug, for the Slack extractor. See `examples/slack_channels.yaml.example`. |
| `env` | `ANTHROPIC_API_KEY=...`, `chmod 600`. |
| `prompt_overrides.json` | Optional. `system_prompt_template` and `asked_header` replace the estimator prompt text byte for byte. The reply cache keys on the exact prompt, so a user who priced tasks under an earlier wording keeps this file and keeps the cache; without it the defaults in `src/weekly_review/enrichment.py` render. |

## What it measures

Full definitions, populations and the checks that run are in [METHOD.md](METHOD.md).

| Metric | What it is | Why |
|---|---|---|
| `est_h` per unit | Estimator's hours a median expert would need to produce the same result, METR time-horizons framing; rework excluded; your review verdicts override it | Output measure |
| Output hours | Σ `est_h` over the window's priced units | Headline numerator |
| Hands-on hours | Union of blocks of your own events (10 min tolerance for transcripts, 60 s for board events); never summed across projects | Headline denominator |
| Multiplier | Output ÷ hands-on, pooled; plus the per-unit median over units with ≥ 3 min hands-on | Two different quantities, both published with n |
| Coordination share | `est_h` of coordination units ÷ output | How much of the output is dispatch, review and merge |
| `wall_clock_hours` | Calendar time: 168 per week; per board task, first agent work to done | Speedup denominator |
| Speedup | (`est_h` ÷ 8) ÷ (`wall_clock_hours` ÷ 24) | Per board task median and per week |
| `labor_hours` | Agent ∪ hands-on timeline, pauses removed | Secondary cost |
| Expertise tag per unit | expert / competent / learning | Where the agents expand what's possible; does not enter the hours |

## What it does NOT measure

- Total Claude hours, LOC, commits/week — vanity metrics, excluded by design.
- "Context-switch tax" or any opaque adjustment to primary numbers — counter-signals surface separately.
- Anything not derivable from local data — no telemetry.

## Where to look

- [METHOD.md](METHOD.md) — how every number is made, written to stand alone.
- [docs/product/vision.md](docs/product/vision.md) — north-star goals.
- [docs/product/specs/2026-04-19-task-leverage-model-design.md](docs/product/specs/2026-04-19-task-leverage-model-design.md) — original design spec (2026-04); where it differs from METHOD.md, METHOD.md is current.
- [docs/product/decisions.md](docs/product/decisions.md) — product decisions + reversals.
- [ATTRIBUTIONS.md](ATTRIBUTIONS.md) — prior art cited and any code borrowed.
- [docs/product/plans/v1-plan.md](docs/product/plans/v1-plan.md) — the V1 implementation plan executed to produce this codebase.

## Status

In weekly use by one person since April 2026 on Claude Code transcripts, git, GitHub and a claude-workspaces board. It has been tested only on macOS with a single user's data, so expect to adjust `projects.yaml` and `identity.yaml` for your setup. Issues and pull requests are welcome.

## License

MIT. See [LICENSE](LICENSE). Prior art is credited in [ATTRIBUTIONS.md](ATTRIBUTIONS.md).
