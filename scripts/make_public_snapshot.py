#!/usr/bin/env python3
"""Build a one-commit public snapshot of this repo from its tracked files.

The private repo's history holds PR text, commit messages and deleted files
that were never scrubbed, so the public repo is a fresh repo with one commit,
built from `git archive HEAD` (committed content only: nothing gitignored,
untracked or uncommitted can enter it) minus the paths in EXCLUDE.

    scripts/make_public_snapshot.py /path/to/new/dir [--substitutions FILE]

Research notes are kept (the owner, 2026-09-18: scrub them, keep private and
public in parallel) and scrubbed on the way in from a substitution file that
lives OUTSIDE the repo, default `~/.config/weekly-review/public-substitutions.txt`.
The private originals are never edited: several are bound to a review board
and must not change on disk. The file is one rule per line, `from => to`;
plain `from` matches case-insensitively and the replacement takes the match's
case (lower, upper or capitalised); a `from` starting with `/` is a regex with
`\1`-style groups allowed in `to`; `#` lines are comments. Board-shaped ids
(`t-`, `d-`, `w-`, `r-` plus ten or more id characters) are always replaced
with `<prefix>-id`, no rule needed. Rules run over every file in the snapshot,
longest `from` first, so a name inside a longer name is not half-replaced.

The script never pushes and never creates a remote. It prints the snapshot's
`git status --porcelain -uall` (must be empty after the commit), the scrub
result over every file in it, and a removal note listing what was left out and
how many substitutions each file took (counts only, never the terms), so a
reviewer can read all three before anyone creates the public repo.

Exit codes: 0 = snapshot built and scrub clean, 1 = scrub found hits (the
snapshot is still written, for inspection), 2 = usage, missing substitution
file, or git error.
"""

from __future__ import annotations

import io
import re
import subprocess
import sys
import tarfile
from pathlib import Path

# Paths (prefix match) that stay private.
EXCLUDE = (
    "docs/data/",          # the user's own message text and run output
    ".claude/rules/",      # fleet-specific conventions (peer protocols, Notion)
    ".claude/worktrees/",
)
SUBSTITUTIONS_DEFAULT = Path("~/.config/weekly-review/public-substitutions.txt")
BOARD_ID = re.compile(r"\b([tdwr])-[A-Za-z0-9_-]{10,}\b")


def sh(args: list[str], cwd: Path | None = None) -> str:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout


def load_substitutions(path: Path) -> list[tuple[re.Pattern, str, bool]]:
    """Rules as (pattern, replacement, is_regex), longest literal first."""
    rules: list[tuple[str, str, bool]] = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if " => " not in line:
            raise ValueError(f"{path}:{n}: expected 'from => to'")
        src, dst = (s.strip() for s in line.split(" => ", 1))
        if not src:
            raise ValueError(f"{path}:{n}: empty 'from'")
        rules.append((src, dst, src.startswith("/")))
    rules.sort(key=lambda r: -len(r[0]))
    out = []
    for src, dst, is_re in rules:
        pat = re.compile(src[1:]) if is_re else re.compile(re.escape(src), re.IGNORECASE)
        out.append((pat, dst, is_re))
    return out


def _cased(match_text: str, dst: str) -> str:
    if match_text.islower():
        return dst.lower()
    if match_text.isupper():
        return dst.upper()
    if match_text[:1].isupper() and match_text[1:].islower():
        return dst[:1].upper() + dst[1:]
    return dst


def substitute(text: str, rules: list[tuple[re.Pattern, str, bool]]) -> tuple[str, int]:
    """Apply every rule, then the board-id rule. Returns (text, replacements)."""
    count = 0
    for pat, dst, is_re in rules:
        if is_re:
            text, n = pat.subn(dst, text)
        else:
            text, n = pat.subn(lambda m, d=dst: _cased(m.group(0), d), text)
        count += n
    text, n = BOARD_ID.subn(r"\1-id", text)
    return text, count + n


def build(src: Path, dest: Path, exclude: tuple[str, ...] = EXCLUDE,
          rules: list[tuple[re.Pattern, str, bool]] | None = None
          ) -> tuple[list[str], list[str], str, dict[str, int]]:
    """Copy `src`'s tracked files minus `exclude` into `dest`, apply `rules` to
    every text file, commit once, and write `<dest>-removed.md` beside it.
    Returns (kept, dropped, status, substituted) where status is `git status
    --porcelain -uall` in the snapshot after the commit and substituted maps
    each changed path to its replacement count."""
    head = sh(["git", "rev-parse", "--short", "HEAD"], cwd=src).strip()
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(f"{dest} exists and is not empty")
    dest.mkdir(parents=True, exist_ok=True)

    # Content comes from HEAD via `git archive`, not from the working tree, so
    # an uncommitted edit or a deleted tracked file cannot make the snapshot
    # differ from the commit its message names.
    tracked = [t for t in sh(["git", "ls-tree", "-r", "-z", "--name-only", "HEAD"], cwd=src).split("\0") if t]
    kept, dropped = [], []
    for rel in tracked:
        (dropped if rel.startswith(exclude) else kept).append(rel)
    archive = subprocess.run(["git", "archive", "--format=tar", "HEAD"], cwd=src,
                             check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
        members = [m for m in tar.getmembers() if m.isfile() and m.name in set(kept)]
        tar.extractall(dest, members=members, filter="data")

    # Rules run over the PATH as well as the contents: a codename in a file
    # name ships in the tree and in history, and a content scan never sees it
    # (Team Lead's review, 2026-09-18, found exactly that).
    substituted: dict[str, int] = {}
    renamed: dict[str, str] = {}
    for i, rel in enumerate(kept):
        p = dest / rel
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = None
        n = 0
        if text is not None:
            new, n = substitute(text, rules or [])
            if n:
                p.write_text(new, encoding="utf-8")
        new_rel, m = substitute(rel, rules or [])
        if m:
            (dest / new_rel).parent.mkdir(parents=True, exist_ok=True)
            p.rename(dest / new_rel)
            renamed[rel] = new_rel
            kept[i] = new_rel
        if n + m:
            substituted[new_rel] = n + m

    sh(["git", "init", "-q"], cwd=dest)
    sh(["git", "add", "-A"], cwd=dest)
    sh(["git", "-c", "user.name=weekly-review", "-c", "user.email=noreply@example.invalid",
        "commit", "-q", "-m", f"Public snapshot of weekly-review at {head}"], cwd=dest)
    status = sh(["git", "status", "--porcelain", "-uall"], cwd=dest)

    note = dest.parent / f"{dest.name}-removed.md"
    with note.open("w") as f:
        f.write(f"# Left out of the public snapshot (from {head})\n\n")
        f.write("Excluded path prefixes: " + ", ".join(f"`{e}`" for e in exclude) + "\n\n")
        for rel in dropped:
            f.write(f"- {rel}\n")
        f.write(f"\n## Substituted ({sum(substituted.values())} replacements in {len(substituted)} files)\n\n")
        for rel, n in substituted.items():
            f.write(f"- {rel}: {n}\n")
        if renamed:
            f.write(f"\n## Renamed ({len(renamed)} paths)\n\n")
            for old, new in renamed.items():
                f.write(f"- {new} (was a path the rules matched)\n")
    return kept, dropped, status, substituted


def main(argv: list[str]) -> int:
    args = list(argv[1:])
    subs_path = SUBSTITUTIONS_DEFAULT
    if "--substitutions" in args:
        i = args.index("--substitutions")
        if i + 1 >= len(args):
            print(__doc__)
            return 2
        subs_path = Path(args[i + 1])
        del args[i:i + 2]
    if len(args) != 1:
        print(__doc__)
        return 2
    dest = Path(args[0]).expanduser().resolve()
    subs_path = subs_path.expanduser()
    if not subs_path.is_file():
        print(f"refusing: no substitution file at {subs_path} (research notes would ship unscrubbed)")
        return 2
    try:
        rules = load_substitutions(subs_path)
    except ValueError as e:
        print(f"refusing: {e}")
        return 2
    src = Path(sh(["git", "rev-parse", "--show-toplevel"]).strip())
    try:
        kept, dropped, status, substituted = build(src, dest, rules=rules)
    except FileExistsError as e:
        print(f"refusing: {e}")
        return 2
    print(f"snapshot: {dest}  ({len(kept)} files kept, {len(dropped)} dropped, "
          f"{sum(substituted.values())} substitutions in {len(substituted)} files from {len(rules)} rules)")
    print("git status --porcelain -uall after commit:", "(empty)" if not status.strip() else "\n" + status)
    print(f"removal note: {dest.parent / (dest.name + '-removed.md')}")

    scrub = src / "scripts" / "scrub-check.py"
    r = subprocess.run([sys.executable, str(scrub), *(str(dest / rel) for rel in kept)],
                       capture_output=True, text=True)
    tail = (r.stdout + r.stderr).strip().splitlines()
    print("scrub-check over the snapshot:", "clean" if r.returncode == 0 else f"exit {r.returncode}")
    for line in tail[-3:]:
        print("  " + line)
    return 0 if r.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
