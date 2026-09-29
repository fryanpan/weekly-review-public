# Data sources by era

The user's working style and the resulting transcript geography have changed every
1-2 months. Any analysis longer than 2 weeks needs to ingest from multiple
sources with different shapes. This doc is the canonical map.

## Timeline of how the user worked

| Period | Primary tool | Where transcripts live | cwd shape | Ingestion status |
|---|---|---|---|---|
| **~Jan 2026** | Claude Code CLI on personal MacBook | `~/.claude/projects/` on MacBook → mirrored to `~/dev/weekly-review-transcripts/laptop/` | `/Users/<you>/dev/<project>/` | ✅ auto-discovered |
| **Feb 2026** | Conductor.build (also runs Claude Code under the hood) | `~/conductor/workspaces/<repo>/<branch>/` cwds in transcripts on MacBook + Conductor SQLite (`~/Library/Application Support/com.conductor.app/conductor.db`) | `/Users/<you>/conductor/workspaces/<repo>/<workspace>` | ✅ via `conductor_db` extractor (see `--conductor-db`) |
| **Mar–early Apr 2026** | Mac Mini direct + heavy product-engineer agent infrastructure | Mac Mini `~/.claude/projects/` + Cloudflare R2 bucket `product-engineer-transcripts` | Mac Mini: `/Users/<you>/dev/<project>`. R2: `/workspace/<project>` (Docker container) | ✅ Mac Mini auto-discovered. R2 backup at `~/dev/product_engineer_backup/r2_transcripts/objects/` symlinked into the discovery root. **Requires `/workspace/<slug>` patterns in projects.yaml** |
| **Mid Apr 2026 onward** | Remote Claude on Mac Mini, direct dev sessions | Mac Mini `~/.claude/projects/` | `/Users/<you>/dev/<project>` | ✅ auto-discovered |

## Where the data physically sits

```
~/dev/weekly-review-transcripts/                     # transcripts-root, auto-discovered
├── mac_mini/                                        # rsync of Mac Mini ~/.claude/projects/
│   └── -Users-<you>-dev-<project>/<uuid>.jsonl
├── mac_mini_history/                                # rsync --backup overflow store (skipped during scan)
├── laptop/                                          # MacBook history pre-Mar 2026
│   └── -Users-<you>-dev-<project>/<uuid>.jsonl
├── conductor/                                       # SQLite backups (NOT scanned as transcripts; see --conductor-db)
│   └── laptop-<date>.db
└── r2_product_engineer/                             # symlink → product_engineer_backup/r2_transcripts/objects
    └── <agentUuid>-<sessionId>.jsonl

~/dev/product_engineer_backup/                       # R2 mirror, ~140 MB / 1488 transcripts
├── r2_transcripts/objects/                          # the .jsonl files
└── db/                                              # Cloudflare D1 / KV exports for context
```

## Transcript shape by era

All three sources produce JSONL with the same Claude Code SDK schema (timestamp,
sessionId, type, message.role, message.content, cwd, tool_use blocks). The
extractor in `src/weekly_review/extractors/claude_transcript.py` handles all
three uniformly. Per-source quirks:

### Claude Code CLI (laptop + Mac Mini)
- `cwd` is the actual filesystem path: `/Users/<you>/dev/<project>` (or `/Users/*/dev/<project>--claude-worktrees-<x>`).
- The first `user` message is the user typing.
- Subagent spawned tasks: filed under `.../subagents/<uuid>.jsonl` — those `user` records are synthetic parent-agent prompts, NOT the user. The extractor emits them as `actor=agent kind=subagent_input` so they don't inflate active-time metrics.
- `system-reminder`, `<channel source=...>`, `<task-notification>`, scheduled-loop triggers, `<tool_result>` echoes are filtered out (see `SYSTEM_MESSAGE_PATTERNS`).

### Conductor.build (Feb era + some early March)
- Stored in a SQLite DB at `~/Library/Application Support/com.conductor.app/conductor.db` (laptop). Backups land in `~/dev/weekly-review-transcripts/conductor/`.
- `cwd` reconstructed from `repo_name` + `workspace_dir`: `/Users/<you>/conductor/workspaces/<repo>/<workspace>`.
- Some root sessions never reached `~/.claude/projects/` (Conductor cleaned them up). The `extract_from_conductor_db` extractor recovers them from the SQLite tables.
- ⚠️ `--conductor-db` must be passed explicitly OR the file must live under `<transcripts-root>/conductor/*.db` for auto-discovery.

### Product-engineer agent (R2)
- The agent runs in Docker containers in Cloudflare's sandbox. cwd is `/workspace/<project>`, NOT `/Users/<you>/dev/<project>`.
- The first `user` message is the **synthesized agent prompt** (e.g. "You are a Product Engineer agent working on **notes-repo**. Your task: XX-209 Curate daily AI/ML digest…"). It's the user's intent (via Linear/Slack), packaged. Treat it as captured input but distinct in tone from a user-typed message.
- File naming: `<agentUuid>-<sessionId>.jsonl` or `<agentUuid>-agent-<sha>.jsonl` for restart-resumed sessions.
- The R2 backup at `~/dev/product_engineer_backup/r2_transcripts/objects/` is a flat dir of these .jsonl files. It's symlinked as `~/dev/weekly-review-transcripts/r2_product_engineer/` so auto-discovery picks it up.
- ⚠️ `projects.yaml` MUST include `/workspace/<slug>` and `/workspace/<slug>/**` patterns alongside the existing `/Users/*/dev/<slug>` ones, or these all route to "unknown".

## Implications for analysis

- **Don't assume "uncaptured" means "no transcript exists."** It can also mean the transcript exists but was never wired into ingestion. Always check for new sources before concluding a week is uncaptured.
- **Pre-Mar work is laptop-only.** Mac-mini-only ingestion misses it.
- **Mar–early Apr work is split** between Mac Mini direct (the user typing) and R2 product-engineer (agents executing on the user's behalf via Linear tickets / Slack). Both are "captured" in the sense that we have the transcript, but the user_message volume on R2 reflects agent prompts, not the user's typing time.
- **active_hours = words / WPM** is meaningful for Mac Mini direct sessions and Conductor sessions. For product-engineer R2 transcripts, the user_message volume is dominated by synthesized prompts, so the active-hours formula overstates the user's keyboard time. Treat R2 sessions' `active_hours` as a CAP (it's at most this) and prefer `agent_active_hours` for the actual labor signal there.

## Configuration cheatsheet

```bash
# Auto-discovers all subdirs of transcripts-root except SKIP_DIRS
weekly-review --since 2026-04-01 --until 2026-04-08

# Override transcripts-root if needed
weekly-review --transcripts-root /custom/path ...

# Add an extra one-off transcript dir (rare)
weekly-review --extra-transcripts /path/to/dir:laptop

# Conductor DBs auto-picked from <transcripts-root>/conductor/*.db
# Or explicit:
weekly-review --conductor-db /path/to/conductor.db:laptop:/Users/<you>
```

When R2 grows beyond what's already mirrored, run `scripts/sync-r2-transcripts.sh` (requires `PRODUCT_ENGINEER_WORKER_URL` and `PRODUCT_ENGINEER_API_KEY` in env or `~/.config/weekly-review/env`).

## Related

- [`learnings.md` § Transcript ingestion](../process/learnings.md) — bug-history of getting all three sources lined up.
- `examples/projects.yaml` — seeded with all known patterns including `/workspace/*`.
- `src/weekly_review/extractors/claude_transcript.py` — handles laptop + mac_mini + R2 transcripts uniformly.
- `src/weekly_review/extractors/conductor_db.py` — the Conductor.build SQLite path.
