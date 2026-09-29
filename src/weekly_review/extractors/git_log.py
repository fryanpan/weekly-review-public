"""Extract `commit` events from git log for a registered repo."""
from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path
from typing import Iterator, Sequence

from ..events import Event

# Sentinel prefix so commit header lines can't be confused with --numstat rows
# (a filename could contain anything, including a '|').
_MARK = "\x01commit\x01"


def _excluded(path: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pat) for pat in patterns)


def _parse_numstat(
    lines: list[str], exclude: Sequence[str] = (),
) -> tuple[int, int, int, int, int]:
    """(files_changed, insertions, deletions, excluded_files, excluded_insertions)
    from `git log --numstat` rows.

    Rows are `added<TAB>deleted<TAB>path`. Binary files report '-' for both;
    they count as a changed file but contribute no lines. A path matching any
    `exclude` glob is left out of the first three numbers and counted in the
    last two, so a commit of checked-in data reads as the small authored change
    it was, with the excluded bulk still visible.
    """
    files = added = deleted = ex_files = ex_added = 0
    for row in lines:
        parts = row.split("\t", 2)
        if len(parts) < 3:
            continue
        a, d, path = parts
        if _excluded(path, exclude):
            ex_files += 1
            if a.isdigit():
                ex_added += int(a)
            continue
        files += 1
        if a.isdigit():
            added += int(a)
        if d.isdigit():
            deleted += int(d)
    return files, added, deleted, ex_files, ex_added


def _repo_name(repo_path: str | Path) -> str:
    """The repo's own identity, independent of which project slug ingested it.

    `metadata.repo` has always carried the project SLUG, which breaks artifact
    dedup exactly where it matters most: one repo registered under two slugs
    (or a repo that SPLIT into a second one, as rare-disease-benchmark did out
    of health-tool) emits different repo strings for identical commits, and a
    squash commit can never be matched to the PR it closed. Prefer the origin
    remote's owner/name, since that is what the GitHub extractor records; fall
    back to the working-tree directory name.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_path), "remote", "get-url", "origin"],
            capture_output=True, text=True, check=False, timeout=10,
        )
        url = (out.stdout or "").strip()
        if out.returncode == 0 and url:
            # git@github.com:owner/name.git | https://github.com/owner/name(.git)
            tail = url.split(":")[-1] if url.startswith("git@") else url
            tail = tail.rstrip("/")
            if tail.endswith(".git"):
                tail = tail[: -len(".git")]
            name = tail.split("/")[-1]
            if name:
                return name.lower()
    except (OSError, subprocess.SubprocessError):
        pass
    # A worktree dir can be named for the branch, so walk up to the real root.
    try:
        top = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=False, timeout=10,
        )
        if top.returncode == 0 and top.stdout.strip():
            return Path(top.stdout.strip()).name.lower()
    except (OSError, subprocess.SubprocessError):
        pass
    return Path(str(repo_path)).name.lower()


def extract_git_log(
    repo_path: str | Path,
    project: str,
    since: str,
    source_machine: str,
    diffstat_exclude: Sequence[str] = (),
) -> Iterator[Event]:
    """Commit events, each carrying its real diffstat.

    `diffstat_exclude` (ProjectSpec.diffstat_exclude) names path globs whose
    lines are never authored work — checked-in data, benchmark output,
    generated files. They leave the diffstat and ride along as
    `excluded_files` / `excluded_insertions` so the prompt can say what was
    set aside.

    The diffstat is GROUND TRUTH for the estimator's EFFORT_BREAKDOWN countables
    (files touched, LoC added/removed). Without it the model had no evidence for
    those numbers and was inferring them from the conversation — which is exactly
    how estimates ended up reflecting what was ASKED rather than what got DONE.

    Merge commits are skipped for the diffstat (--no-merges would drop them
    entirely; instead they simply report a zero stat, since their content is
    already counted in the commits they merge).
    """
    fmt = f"{_MARK}%h|%aI|%an|%s"
    result = subprocess.run(
        [
            "git", "-C", str(repo_path), "log",
            f"--since={since}", f"--pretty=format:{fmt}",
            "--numstat", "--no-renames",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return

    seq = 0
    header: tuple[str, str, str, str] | None = None
    stat_rows: list[str] = []
    repo_name = _repo_name(repo_path)

    def _emit(h: tuple[str, str, str, str], rows: list[str]) -> Event:
        sha, iso, author, subject = h
        files, added, deleted, ex_files, ex_added = _parse_numstat(rows, diffstat_exclude)
        excluded = {"excluded_files": ex_files, "excluded_insertions": ex_added} if ex_files else {}
        return Event(
            ts=iso,
            actor="artifact",
            project=project,
            session_id=f"git:{project}",
            source_system="git",
            source_machine=source_machine,
            kind="commit",
            seq=seq,
            content_snippet=subject[:500],
            metadata={
                "short_sha": sha,
                "author": author,
                # Project slug serves as a coarse repo identifier for the
                # R2-conversation ↔ PR-task join (segmentation Rule 3). Kept
                # as-is so that join's behaviour doesn't shift.
                "repo": project,
                # The repo's OWN name, slug-independent — this is what artifact
                # dedup and workstream scoping key on.
                "repo_name": repo_name,
                "files_changed": files,
                "insertions": added,
                "deletions": deleted,
                **excluded,
            },
            author_id=author,
            author_kind="human",
        )

    for line in (result.stdout or "").splitlines():
        if line.startswith(_MARK):
            if header is not None:
                yield _emit(header, stat_rows)
                seq += 1
            parts = line[len(_MARK):].split("|", 3)
            header = tuple(parts) if len(parts) == 4 else None  # type: ignore[assignment]
            stat_rows = []
        elif line.strip() and header is not None:
            stat_rows.append(line)

    if header is not None:
        yield _emit(header, stat_rows)
