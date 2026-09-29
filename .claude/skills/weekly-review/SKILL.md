---
name: weekly-review
description: Synthesize how this week actually went — expert hours shipped, hands-on time, wall-clock, agent hours, speedup, leverage — pulled from Claude Code transcripts + git + GitHub. Use when you want to honestly assess your week, not look at vanity metrics.
user-invocable: true
---

# /weekly-review

A task-leverage digest for someone who works across many projects with AI coding agents. Answers "how did this week actually go" with signals that matter, not vanity metrics.

Every effort number in the output uses the **6 canonical metrics**: expert hours, hands-on hours, wall-clock hours, agent hours, wall-clock speedup, hands-on leverage. Same definitions at per-task, per-epic, and per-week scopes. **Read [docs/product/specs/2026-06-10-canonical-metric-block.md](../../../docs/product/specs/2026-06-10-canonical-metric-block.md) before publishing or interpreting any number** — it specifies the definitions, rendering rules (per-task = 3 metrics, per-epic + per-week = 6 metrics), and the methodology footer required on every artifact.

See `docs/product/specs/2026-04-19-task-leverage-model-design.md` for the underlying model, `ATTRIBUTIONS.md` for prior-art citations, and `docs/product/decisions.md` for the decisions that shape this tool.

## When to use

- On-demand whenever you suspect perception bias is warping your sense of output.
- Weekly as a Friday retro.
- Before recalibrating how you work with your agent team — run to see what the last 2 weeks actually looked like.

## What you'll need

- Claude Code transcripts at `~/.claude/projects/` (always present).
- `gh` CLI authenticated (for PR events).
- `ANTHROPIC_API_KEY` in env (for task enrichment — skipped with `--no-enrich`).
- A projects registry at `~/.config/weekly-review/projects.yaml` (see `examples/projects.yaml`).
- (Optional) Expertise seed at `~/.config/weekly-review/expertise.yaml` (see `examples/expertise.yaml`).

## How to invoke

```bash
weekly-review                              # this week (last 14 days)
weekly-review --since 2026-04-05           # arbitrary range
weekly-review --no-enrich                  # skip LLM (faster, no est_human_hours)
weekly-review --inspect                    # per-task inspection view
weekly-review --recalibrate                # label anchors + refit bias correction
weekly-review --diff-runs <old> <new>      # compare two runs
weekly-review --refresh-estimates          # invalidate LLM response cache
weekly-review --write path/to/digest.md    # save digest to a file
```

## What it produces

A markdown digest with:

- **Leverage**: Σ est_human_hours, Σ your active hours, ratio, plus agent-hours and expertise breakdown, plus a cognitive wall-clock sanity check.
- **Coverage**: % of user-message events assigned to tasks. Target ≥ 95%.
- **Top tasks by est_human_hours** (what got done, with uncertainty + expertise tags).
- **Top tasks by your active hours** (where your time went).
- **Low-leverage tasks** (< 0.5× period average — review candidates).
- **Rework clusters** (top 5 loops by user-active + agent-active hours where shipped work was found broken — for continuous improvement). Plus a `leverage_user_excl_rework` line in the leverage block. Detection signals: revert commits/PRs, frustration markers ("still broken", "default to the old"), `fix/revert-*` branches.
- **Blocked on you**, **Context switches** (count only, no tax), **Resumed tasks** (flag only).
- **Open + low-confidence estimates** + last-calibrated nudge.

## How it works

1. **Extract**: `~/.claude/projects/*.jsonl` → user/agent/tool events. Git log + `gh pr list` → artifact events. All tagged by project (registered in `projects.yaml`).
2. **Segment**: deterministic rules (new-topic cues, issue refs, 14-day gap threshold) + project-proximity fallback within 7 days. Coverage target ≥ 95% without an LLM classifier.
3. **Apply overrides**: `~/.config/weekly-review/overrides.yaml` (merges, relabels, ignores).
4. **Compute metrics**: active hours (WPM-based), agent active hours, cognitive wall-clock (spans with ≤15-min gaps), context-switch count, per-task resumption count.
5. **Enrich** (one LLM call per task, Sonnet 4.6): title + est_human_hours + bryan_domain_expertise + dimensions + rationale. Uncertainty bucketed low/medium/high. Bias-corrected against anchors.
6. **Render**: markdown digest.

Helper modules in `src/weekly_review/`:

- `extractors/claude_transcript.py` — parses `~/.claude/projects/*.jsonl`; filters system-injected + channel messages.
- `extractors/git_log.py` — git log per registered repo.
- `extractors/github_pr.py` — `gh pr list` per registered repo.
- `segmentation.py` — Layer 1 rules + proximity fallback.
- `effort.py` — presence hours (the reported hands-on number), per-message active hours (a floor), cognitive wall-clock, switches, resumptions. See [docs/product/specs/2026-08-17-hands-on-measurement.md](../../../docs/product/specs/2026-08-17-hands-on-measurement.md) before quoting a hands-on figure.
- `enrichment.py` — per-task LLM pass.
- `calibration.py` — anchor IO + geometric-mean bias correction.
- `digest.py` — markdown digest renderer.

Each module is independently testable. 246 unit tests under `tests/`.

## Configuration

Per-user config at `~/.config/weekly-review/`:

```
projects.yaml     # cwd-pattern → project-slug mapping, with exclude: true for automated workloads
expertise.yaml    # self-declared expertise seed (expert / competent / learning)
overrides.yaml    # merge / relabel / ignore corrections (output of --inspect review)
anchors.yaml      # labeled human-effort examples — written by --recalibrate
bias.yaml         # fitted bias multiplier — written by --recalibrate
```

See `examples/` in this repo for templates.

## Transcript archive

`~/dev/weekly-review-transcripts/` is the single parent dir for every transcript source:

```
mac_mini/           # hourly mirror of this Mac's ~/.claude/projects/ (LaunchAgent)
mac_mini_history/   # overwrite-protection store (see archive guarantees below)
laptop/             # one-off rsync of the MacBook's ~/.claude/projects/
conductor/*.db      # date-stamped Conductor SQLite backups
```

`weekly-review` auto-discovers each subdir via `--transcripts-root`
(defaults to that path). The archive LaunchAgent at
`com.weekly-review.archive` runs hourly and uses
`rsync -a --update --backup --backup-dir=…` — so deletes are ignored
(files persist), and any overwrite preserves the old version in
`mac_mini_history/<timestamp>/`.

Derived app state (runs, LLM cache) stays at `~/.local/share/weekly-review/` — separate from transcripts.

**Check archive freshness against the requested window before trusting any run.** The LaunchAgent has died twice to macOS revoking its disk access after an OS update, silently — the pipeline reads the archive by design, so a dead job reports an empty week rather than an error. Treat "0 files in range" as a failure, not a quiet week. A manual `~/.local/bin/archive-transcripts.sh` from a terminal unblocks it (the shell has the disk access launchd lost).

## Publishing into someone else's doc

The digest above is the long form — it's for whoever asked "how did the week go" and wants the working. **A section written into a shared planning doc is not the digest.** The user's standing instruction (2026-08-17): lean, high level, no explanatory text, no how-we-got-there.

- **Ship the conclusion and a link, not the reasoning.** Methodology, corrections, tolerance tables and provenance go in `docs/product/specs/2026-08-17-hands-on-measurement.md`, which is bound as a live-feedback doc. Link it once.
- **Budget the section before writing it.** If it doesn't fit on a phone screen, it's the wrong artifact — cut to what he'd act on.
- **Every number carries a source or doesn't ship.** Run id, window, event count. A number without provenance reads as invented, and he will say so.
- **Never state a hands-on figure without the model behind it.** Presence @10min is the default; say which, since the same week reads 9.9h–37.4h across tolerances.
- **A zero means "no trace", never "didn't happen."** Goals that live in email, meetings or conversation are structurally invisible here.
- **Use the block-scoped LF tools** (`find_and_replace`, `insert_blocks_at_anchor`), never `set_doc_content`, on a doc someone is actively editing — a full-doc write has destroyed live edits.

## Anti-patterns

- **Don't treat the leverage ratio as a scoreboard.** It's a personal signal, not a KPI. SPACE and quantified-self literature are clear: single-number productivity dashboards get gamed even by an audience of one.
- **Don't add a "context-switch tax" or other funny adjustments.** The metrics stay raw; counter-signals are surfaced separately.
- **Don't run with a stale calibration.** The digest nudges you if it's been > 14 days since the last `--recalibrate`.
- **Don't rule on whether it was the right week.** No on-goal / off-goal split, no "unplanned work" bucket, no off-track flag — in the generated digest or in a hand-written one. Report where the hours went, in full; which of it was worth doing is the user's call, and they have said so. The mid-week off-track ping is the lead agent's planning loop, not a measurement output. `tests/test_no_goal_verdict.py` enforces this on the renderer, the worksheets, the BI export and the audits; a digest you write by hand is the path it cannot see, which is why it is written here too.
