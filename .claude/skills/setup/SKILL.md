---
name: setup
description: First-time setup for a fresh clone of weekly-review. Checks dependencies, installs the Python package, seeds your per-user config (identity, projects, expertise), starts the transcript archive, and does a first run on last week. Run once after cloning.
user-invocable: true
---

# Setup

Bring a fresh clone to the point where `uv run weekly-review` measures the user's last week. Go in order and confirm with the user before each step that writes outside the repo. Never overwrite a file that already exists in `~/.config/weekly-review/`.

## 1. Confirm scope

Ask once:

> "I'll set up weekly-review. Plan: check dependencies (Python 3.12+, `uv`, `gh`), install the package, create your config in `~/.config/weekly-review/` (who you are, which projects to measure), start a local archive of your Claude Code transcripts, and do a first run on last week. Everything stays on this machine. Anything to skip?"

## 2. Check dependencies

Run these checks in parallel and report what's missing:

```sh
python3 --version        # need 3.12+
uv --version
gh auth status           # PR data comes from the GitHub CLI
git --version
```

Offer install commands for anything missing (`brew install uv gh` on macOS). Stop if `gh` isn't signed in; ask the user to run `! gh auth login` themselves.

## 3. Install and test

```sh
uv sync
uv run pytest -q
```

All tests should pass. If any fail, stop and show the output.

## 4. Seed config

```sh
mkdir -p ~/.config/weekly-review
```

Copy each example only if the target is absent:

| Source | Target | What to fill in |
|---|---|---|
| `examples/identity.yaml` | `identity.yaml` | GitHub login(s), and every display name the user appears under in a shared workspace |
| `examples/projects.yaml` | `projects.yaml` | One stanza per project |
| `examples/expertise.yaml` | `expertise.yaml` | Self-rated expertise per domain |

**Identity.** Fill `github_users` from `gh api user --jq .login` and confirm it with the user. Ask for every name they appear under. Every hours figure is "time this person spent", so a missing handle silently drops their own activity.

**Projects.** List the user's working directories (for example the git repos under `~/dev`) and propose one stanza per project they want measured. Each stanza needs `cwd_patterns` covering the repo and **both** worktree layouts:

```yaml
- slug: my-app
  cwd_patterns:
  - /Users/*/dev/my-app
  - /Users/*/dev/my-app/**
  - /Users/*/dev/my-app--claude-worktrees-*
  - /Users/*/dev/my-app--claude-worktrees-*/**
```

Patterns are matched in order and the first match wins, so put `exclude: true` stanzas (automated harnesses, benchmarks) before the project they live in. If a repo commits generated data, add `diffstat_exclude` globs so it isn't priced as written code. A working directory with no stanza is scattered onto nearby projects instead of reported, so cover every directory the user works in.

**API key.** Enriched runs call the Anthropic API. Ask the user to add the key themselves, not paste it into chat:

```sh
! printf 'ANTHROPIC_API_KEY=%s\n' "<key>" > ~/.config/weekly-review/env && chmod 600 ~/.config/weekly-review/env
```

## 5. Start the transcript archive

Claude Code deletes transcripts after about 16 weeks, and this tool reads from an archive that keeps them. Every run refreshes the archive first if the script is installed:

```sh
mkdir -p ~/.local/bin
cp scripts/archive-transcripts.sh ~/.local/bin/
chmod +x ~/.local/bin/archive-transcripts.sh
~/.local/bin/archive-transcripts.sh
```

The archive lands in `~/dev/weekly-review-transcripts/mac_mini/`. Set `WEEKLY_REVIEW_ARCHIVE` to change it, and pass the matching `--transcripts-root` / `--source-machine` to the CLI.

Optional: schedule it so transcripts are copied even in weeks with no run. A cron entry works. On macOS, a LaunchAgent running `/bin/bash` can be denied access to a home directory on a second volume. If the schedule seems to do nothing, read the job's error log before anything else.

## 6. First run

Measure last week, Monday through Sunday. `--until` is inclusive, so pass the Sunday:

```sh
uv run weekly-review --since <last Monday> --until <last Sunday> --no-enrich
```

This run has no API calls. Check three lines in its output with the user:
- **Window:** 7 days.
- **Archive:** a non-zero number of files in the window.
- **Unrouted share:** under 5%. A higher share means a directory is missing from `projects.yaml`.

Then run the same week enriched (one API call per unit of work):

```sh
uv run weekly-review --since <last Monday> --until <last Sunday> --write ~/weekly-review-<date>.md
```

Show the user the digest and ask whether the week reads the way they remember it. If a number looks wrong, the definitions are in `METHOD.md`.

## 7. Optional sources

- **claude-workspaces board:** read automatically when its data directory exists. Turn off with `--no-live-feedback --no-board-log`.
- **Slack:** copy `examples/slack_channels.yaml.example` and map channel ids to project slugs.
- **Other machines:** `--extra-transcripts DIR:MACHINE` adds another machine's archived transcripts.

## Done

Tell the user what was configured, where the config lives, and the command for next week's run.
