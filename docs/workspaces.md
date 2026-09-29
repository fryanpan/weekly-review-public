# Where docs go in this project

Short version: measurement output goes in `docs/research/`, the method it was
produced by goes in `docs/product/specs/`, and anything containing raw
transcript text goes in `docs/data/`, which is gitignored and **not mounted**.

| Folder | Holds | Committed? | Mounted? |
| --- | --- | --- | --- |
| `docs/research/` | findings and one-off measurements, dated: a restated series, an audit, a delivered-hours figure | yes | yes |
| `docs/product/` | vision, specs, plans, decisions — the method, and why it is the method | yes | yes |
| `docs/process/` | `learnings.md`, retrospectives | yes | yes |
| `docs/architecture/` | where the data lives and what shape it is in | yes | yes |
| `docs/data/` | weekly digests, estimate verdicts, label worksheets — **raw transcript excerpts and the user's own messages** | **no, gitignored** | **no** |

## Why `docs/data/` is not mounted

A mount is a filesystem walk, not `git ls-files` — gitignored files are served
exactly like tracked ones. `docs/data/` is 1.5 MB of digests and verdict
worksheets quoting the user's own messages verbatim, and `.gitignore` is the only
thing currently holding it back. Mounting `docs/` at the root would publish to
the board precisely what the repo was configured not to carry.

So mounts are per-folder here and always will be. **If you add a folder, mount
that folder — never `docs/`, and never the repo root.**

## Naming

Dated deliverables are `YYYY-MM-DD-what-it-is.md`. The date is when it was
*measured*, not when it was last edited, because these are measurements and a
measurement belongs to its window.

## Before you write a doc

Any published effort number needs the canonical metric block
(`docs/product/specs/2026-06-10-canonical-metric-block.md`) and a provenance
footer naming the run that produced it. A figure without the run that made it
cannot be checked, and this project has retracted numbers for exactly that.

## A note on privacy

This project is `workspace` privacy, so mounted files are readable by everyone
on the board and over the tailnet. That is deliberate — it is the user's own
measurement of his own work, and they read it on their phone. It is also why
third-party material does not belong in the mounted folders: meeting turn text
and the meeting ledger's `speakers` records carry other people's names and
personal business, and neither is stored in this repo at all.
